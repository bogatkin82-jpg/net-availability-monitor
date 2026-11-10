package ru.netmon.core

import java.io.IOException
import java.net.ConnectException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.NoRouteToHostException
import java.net.Socket
import java.net.SocketTimeoutException
import java.security.cert.CertPathValidatorException
import java.security.cert.CertificateException
import java.util.concurrent.Callable
import java.util.concurrent.ExecutionException
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import javax.net.ssl.SNIHostName
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLException
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory
import kotlin.math.roundToInt

/** Резолвер DNS: абстракция нужна для unit-тестов без сети. */
fun interface DnsResolver {
    @Throws(Exception::class)
    fun resolve(host: String): List<String>
}

/** Системный резолвер (InetAddress) — как socket.getaddrinfo в server.py. */
object SystemDnsResolver : DnsResolver {
    override fun resolve(host: String): List<String> =
        InetAddress.getAllByName(host).mapNotNull { it.hostAddress }

    /** IPv4 в приоритете, как в server.py (ipv4 or seen). */
    fun ordered(addresses: List<String>): List<String> {
        val unique = addresses.distinct()
        val ipv4 = unique.filter { !it.contains(':') }
        return ipv4.ifEmpty { unique }
    }
}

/**
 * Постадийная проверка доступности — порт `run_check()` из server.py:
 * DNS -> TCP -> TLS -> HTTP с замером времени каждого этапа и теми же кодами ошибок.
 *
 * Класс не зависит от Android API (только java.net/java.security), поэтому
 * полностью тестируется на JVM.
 */
class StagedChecker(
    private val dnsResolver: DnsResolver = SystemDnsResolver,
    private val userAgent: String = DEFAULT_USER_AGENT,
    private val isoClock: () -> String = { IsoTime.now() },
) : CheckExecutor {

    override fun run(spec: CheckSpec, options: CheckOptions): CheckResult {
        val timeoutSec = options.timeoutFor(spec)
        val startedNanos = System.nanoTime()
        val deadlineNanos = startedNanos + (timeoutSec * 1_000_000_000.0).toLong()

        var ip: String? = null
        var httpStatus: Int? = null
        var plainSocket: Socket? = null
        var tlsSocket: SSLSocket? = null
        var timings = StageTimings()

        fun elapsedMs(): Int = ((System.nanoTime() - startedNanos) / 1_000_000.0).roundToInt()

        fun remainingSec(floor: Double = 0.5): Double =
            maxOf(floor, (deadlineNanos - System.nanoTime()) / 1_000_000_000.0)

        fun build(ok: Boolean, stage: String, error: String? = null, errorText: String? = null) =
            CheckResult(
                id = spec.id,
                label = spec.label,
                target = spec.target(),
                kind = spec.kind,
                core = spec.core,
                ok = ok,
                latencyMs = elapsedMs(),
                stage = stage,
                error = error,
                errorText = errorText,
                httpStatus = httpStatus,
                ip = ip,
                timings = timings,
                measuredAt = isoClock(),
            )

        try {
            // --- DNS --------------------------------------------------------
            val dnsStarted = System.nanoTime()
            val dnsTimeout = minOf(options.dnsBudgetFor(spec), remainingSec())
            val addresses = resolveWithTimeout(spec.host, dnsTimeout)
            timings = timings.copy(dnsMs = elapsedSince(dnsStarted))
            ip = addresses.firstOrNull()
                ?: throw CheckFailure(ErrorCodes.DNS_ERROR, CheckStage.DNS, "DNS вернул пустой список адресов")
            if (spec.kind == CheckKind.DNS) {
                return build(true, CheckStage.OK)
            }

            // --- TCP --------------------------------------------------------
            val tcpStarted = System.nanoTime()
            val socket = Socket()
            plainSocket = socket
            try {
                socket.connect(
                    InetSocketAddress(ip, spec.port),
                    timeoutToMillis(remainingSec()),
                )
            } catch (error: Throwable) {
                if (error is CheckFailure) throw error
                throw mapConnectFailure(error, timeoutSec)
            }
            timings = timings.copy(tcpMs = elapsedSince(tcpStarted))
            if (spec.kind == CheckKind.TCP) {
                return build(true, CheckStage.OK)
            }

            // --- TLS --------------------------------------------------------
            val tlsStarted = System.nanoTime()
            val secure: SSLSocket
            try {
                socket.soTimeout = timeoutToMillis(remainingSec())
                secure = createTlsSocket(socket, spec.host)
                tlsSocket = secure
                secure.startHandshake()
            } catch (error: Throwable) {
                if (error is CheckFailure) throw error
                throw mapTlsFailure(error, timeoutSec)
            }
            timings = timings.copy(tlsMs = elapsedSince(tlsStarted))
            if (spec.kind == CheckKind.TLS) {
                return build(true, CheckStage.OK)
            }

            // --- HTTP -------------------------------------------------------
            val httpStarted = System.nanoTime()
            val path = spec.path.ifEmpty { "/" }
            val request = buildHttpGetRequest(spec.host, path, userAgent)
            val responseBytes = try {
                secure.soTimeout = timeoutToMillis(remainingSec())
                secure.getOutputStream().apply {
                    write(request)
                    flush()
                }
                val buffer = ByteArray(1024)
                val read = secure.getInputStream().read(buffer)
                if (read <= 0) throw CheckFailure(ErrorCodes.HTTP_ERROR, CheckStage.HTTP, "Пустой ответ сервера")
                buffer.copyOf(read)
            } catch (error: Throwable) {
                if (error is CheckFailure) throw error
                throw mapHttpFailure(error, timeoutSec)
            }
            timings = timings.copy(httpMs = elapsedSince(httpStarted))
            httpStatus = parseHttpStatus(responseBytes)
                ?: throw CheckFailure(
                    ErrorCodes.HTTP_ERROR,
                    CheckStage.HTTP,
                    "Не удалось разобрать HTTP-статус",
                )
            val expect = spec.expect.ifEmpty { listOf(200) }
            if (httpStatus !in expect) {
                throw CheckFailure(
                    ErrorCodes.HTTP_STATUS_UNEXPECTED,
                    CheckStage.HTTP,
                    "HTTP $httpStatus (expected $expect)",
                )
            }
            return build(true, CheckStage.OK)

        } catch (failure: CheckFailure) {
            return build(false, failure.stage, failure.code, failure.text)
        } catch (error: Throwable) {
            return build(
                false,
                CheckStage.UNKNOWN,
                ErrorCodes.UNKNOWN_ERROR,
                "${error::class.simpleName}: ${error.message}",
            )
        } finally {
            closeQuietly(tlsSocket)
            closeQuietly(plainSocket)
        }
    }

    // --- DNS с таймаутом (getaddrinfo нельзя прервать, поэтому daemon-пул) ----
    private fun resolveWithTimeout(host: String, timeoutSec: Double): List<String> {
        if (isIpLiteral(host)) return listOf(host)
        val task = dnsPool.submit(Callable { dnsResolver.resolve(host) })
        val addresses = try {
            task.get(timeoutToMillis(timeoutSec).toLong(), TimeUnit.MILLISECONDS)
        } catch (timeout: TimeoutException) {
            task.cancel(true)
            throw CheckFailure(ErrorCodes.DNS_TIMEOUT, CheckStage.DNS, "getaddrinfo timeout")
        } catch (error: ExecutionException) {
            val cause = error.cause ?: error
            throw CheckFailure(ErrorCodes.DNS_ERROR, CheckStage.DNS, cause.message ?: cause.toString())
        } catch (interrupted: InterruptedException) {
            Thread.currentThread().interrupt()
            throw CheckFailure(ErrorCodes.DNS_ERROR, CheckStage.DNS, "DNS-запрос прерван")
        }
        return SystemDnsResolver.ordered(addresses)
    }

    private fun createTlsSocket(plain: Socket, host: String): SSLSocket {
        val factory: SSLSocketFactory = sslContext.socketFactory
        val socket = factory.createSocket(plain, host, plain.port, true) as SSLSocket
        val parameters = socket.sslParameters
        // Проверка hostname сертификата (аналог check_hostname=True в server.py).
        parameters.endpointIdentificationAlgorithm = "HTTPS"
        // SNI — как server_hostname в ssl.wrap_socket.
        parameters.serverNames = if (isIpLiteral(host)) null else listOf(SNIHostName(host))
        socket.sslParameters = parameters
        return socket
    }

    private val sslContext: SSLContext by lazy {
        SSLContext.getInstance("TLS").apply { init(null, null, null) }
    }

    private fun elapsedSince(startNanos: Long): Int =
        ((System.nanoTime() - startNanos) / 1_000_000.0).roundToInt()

    companion object {
        const val DEFAULT_USER_AGENT: String =
            "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 " +
                "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"

        private val dnsPool: ExecutorService =
            Executors.newCachedThreadPool { runnable ->
                Thread(runnable, "dns-helper").apply { isDaemon = true }
            }

        private fun closeQuietly(socket: Socket?) {
            try {
                socket?.close()
            } catch (_: IOException) {
                // закрываем молча — как в server.py
            }
        }

        /** Аналог check_target()/is_ip_literal(). */
        fun isIpLiteral(host: String): Boolean {
            if (host.contains(':')) return true
            val parts = host.split('.')
            if (parts.size != 4) return false
            return parts.all { part ->
                part.isNotEmpty() && part.length <= 3 && part.all { it.isDigit() } &&
                    part.toInt() in 0..255
            }
        }

        /** Сырой HTTP/1.1 GET — как в server.py (sendall + recv). */
        fun buildHttpGetRequest(host: String, path: String, userAgent: String): ByteArray =
            (
                "GET $path HTTP/1.1\r\n" +
                    "Host: $host\r\n" +
                    "User-Agent: $userAgent\r\n" +
                    "Accept: */*\r\n" +
                    "Accept-Language: ru-RU,ru;q=0.9,en;q=0.8\r\n" +
                    "Connection: close\r\n\r\n"
                ).toByteArray(Charsets.UTF_8)

        /** Разбор кода ответа из первой строки. */
        fun parseHttpStatus(data: ByteArray): Int? {
            val firstLine = String(data, Charsets.ISO_8859_1).substringBefore("\r\n")
            val parts = firstLine.split(' ')
            return parts.getOrNull(1)?.toIntOrNull()
        }

        /** Маппинг ошибок TCP — паритет с server.py. */
        fun mapConnectFailure(error: Throwable, timeoutSec: Double): CheckFailure {
            val cause = unwrap(error)
            val message = (cause.message ?: "").lowercase()
            return when {
                cause is SocketTimeoutException -> CheckFailure(
                    ErrorCodes.CONNECT_TIMEOUT,
                    CheckStage.TCP,
                    "connect() timeout %.0fs".format(timeoutSec),
                )
                cause is ConnectException && message.contains("refused") ->
                    CheckFailure(ErrorCodes.CONNECT_REFUSED, CheckStage.TCP, "ECONNREFUSED")
                cause is ConnectException -> CheckFailure(
                    ErrorCodes.CONNECT_UNREACHABLE,
                    CheckStage.TCP,
                    "ConnectException: ${cause.message}",
                )
                cause is NoRouteToHostException -> CheckFailure(
                    ErrorCodes.CONNECT_UNREACHABLE,
                    CheckStage.TCP,
                    "NoRouteToHostException: ${cause.message}",
                )
                else -> CheckFailure(
                    ErrorCodes.CONNECT_UNREACHABLE,
                    CheckStage.TCP,
                    "${cause::class.simpleName}: ${cause.message}",
                )
            }
        }

        /** Маппинг ошибок TLS-рукопожатия — паритет с server.py. */
        fun mapTlsFailure(error: Throwable, timeoutSec: Double): CheckFailure {
            var cause: Throwable? = error
            while (cause != null) {
                if (cause is CertPathValidatorException || cause is CertificateException) {
                    return CheckFailure(
                        ErrorCodes.TLS_CERT_ERROR,
                        CheckStage.TLS,
                        cause.message ?: cause.toString(),
                    )
                }
                cause = cause.cause
            }
            val unwrapped = unwrap(error)
            val text = (unwrapped.message ?: "").lowercase()
            return when {
                unwrapped is SocketTimeoutException || "timed out" in text || "timeout" in text ->
                    CheckFailure(
                        ErrorCodes.TLS_HANDSHAKE_TIMEOUT,
                        CheckStage.TLS,
                        "SSL handshake timeout %.0fs".format(timeoutSec),
                    )
                "reset" in text || "eof" in text || "closed" in text || "broken pipe" in text ->
                    CheckFailure(ErrorCodes.TLS_RESET, CheckStage.TLS, unwrapped.toString())
                else -> CheckFailure(ErrorCodes.TLS_ERROR, CheckStage.TLS, unwrapped.toString())
            }
        }

        /** Маппинг ошибок HTTP-обмена — паритет с server.py. */
        fun mapHttpFailure(error: Throwable, timeoutSec: Double): CheckFailure {
            val unwrapped = unwrap(error)
            return when {
                unwrapped is SocketTimeoutException -> CheckFailure(
                    ErrorCodes.READ_TIMEOUT,
                    CheckStage.HTTP,
                    "recv() timeout %.0fs".format(timeoutSec),
                )
                unwrapped is SSLException -> CheckFailure(
                    ErrorCodes.HTTP_ERROR,
                    CheckStage.HTTP,
                    unwrapped.toString(),
                )
                else -> CheckFailure(
                    ErrorCodes.HTTP_ERROR,
                    CheckStage.HTTP,
                    "${unwrapped::class.simpleName}: ${unwrapped.message}",
                )
            }
        }

        private fun unwrap(error: Throwable): Throwable {
            var current: Throwable = error
            // Разворачиваем только «обёртки» javax.net.ssl, чтобы не потерять суть.
            while (true) {
                val cause = current.cause ?: return current
                if (current is SSLException || current is IOException) {
                    current = cause
                } else {
                    return current
                }
            }
        }
    }
}
