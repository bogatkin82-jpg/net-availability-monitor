package ru.netmon.core

import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.security.cert.CertPathValidatorException
import java.security.cert.CertificateException
import javax.net.ssl.SSLException
import javax.net.ssl.SSLHandshakeException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Проверки постадийного движка без сети: маппинг ошибок DNS/TCP/TLS/HTTP,
 * разбор HTTP-статуса, IP-литералы, цели проверок.
 */
class StagedCheckerTest {

    @Test
    fun `ip literals are detected`() {
        assertTrue(StagedChecker.isIpLiteral("149.154.167.51"))
        assertTrue(StagedChecker.isIpLiteral("1.1.1.1"))
        assertTrue(StagedChecker.isIpLiteral("::1"))
        assertFalse(StagedChecker.isIpLiteral("www.youtube.com"))
        assertFalse(StagedChecker.isIpLiteral("999.1.1.1"))
        assertFalse(StagedChecker.isIpLiteral("1.1.1"))
    }

    @Test
    fun `http status is parsed from first line`() {
        val response = "HTTP/1.1 204 No Content\r\nServer: x\r\n\r\n".toByteArray()
        assertEquals(204, StagedChecker.parseHttpStatus(response))
        assertEquals(404, StagedChecker.parseHttpStatus("HTTP/1.0 404 Not Found\r\n".toByteArray()))
        assertNull(StagedChecker.parseHttpStatus("garbage".toByteArray()))
        assertNull(StagedChecker.parseHttpStatus(ByteArray(0)))
    }

    @Test
    fun `http request matches server implementation`() {
        val request = String(
            StagedChecker.buildHttpGetRequest("www.youtube.com", "/generate_204", "TestAgent/1.0"),
            Charsets.UTF_8,
        )
        assertTrue(request.startsWith("GET /generate_204 HTTP/1.1\r\n"))
        assertTrue(request.contains("Host: www.youtube.com\r\n"))
        assertTrue(request.contains("User-Agent: TestAgent/1.0\r\n"))
        assertTrue(request.contains("Accept: */*\r\n"))
        assertTrue(request.contains("Connection: close\r\n"))
        assertTrue(request.endsWith("\r\n\r\n"))
    }

    @Test
    fun `connect failures map to server error codes`() {
        val timedOut = StagedChecker.mapConnectFailure(SocketTimeoutException("timeout"), 8.0)
        assertEquals(ErrorCodes.CONNECT_TIMEOUT, timedOut.code)
        assertEquals(CheckStage.TCP, timedOut.stage)

        val refused = StagedChecker.mapConnectFailure(ConnectException("Connection refused"), 8.0)
        assertEquals(ErrorCodes.CONNECT_REFUSED, refused.code)

        val unreachable = StagedChecker.mapConnectFailure(ConnectException("Network is unreachable"), 8.0)
        assertEquals(ErrorCodes.CONNECT_UNREACHABLE, unreachable.code)

        val noRoute = StagedChecker.mapConnectFailure(NoRouteToHostException("no route"), 8.0)
        assertEquals(ErrorCodes.CONNECT_UNREACHABLE, noRoute.code)
    }

    @Test
    fun `tls failures map to server error codes`() {
        val certError = SSLHandshakeException("chain").apply {
            initCause(CertPathValidatorException("bad chain"))
        }
        assertEquals(ErrorCodes.TLS_CERT_ERROR, StagedChecker.mapTlsFailure(certError, 8.0).code)

        val certificate = SSLHandshakeException("cert").apply {
            initCause(CertificateException("expired"))
        }
        assertEquals(ErrorCodes.TLS_CERT_ERROR, StagedChecker.mapTlsFailure(certificate, 8.0).code)

        assertEquals(
            ErrorCodes.TLS_HANDSHAKE_TIMEOUT,
            StagedChecker.mapTlsFailure(SocketTimeoutException("handshake timeout"), 8.0).code,
        )
        assertEquals(
            ErrorCodes.TLS_RESET,
            StagedChecker.mapTlsFailure(SSLException("Connection reset by peer"), 8.0).code,
        )
        assertEquals(
            ErrorCodes.TLS_RESET,
            StagedChecker.mapTlsFailure(SSLException("unexpected EOF"), 8.0).code,
        )
        assertEquals(
            ErrorCodes.TLS_ERROR,
            StagedChecker.mapTlsFailure(SSLException("general failure"), 8.0).code,
        )
    }

    @Test
    fun `http failures map to server error codes`() {
        assertEquals(
            ErrorCodes.READ_TIMEOUT,
            StagedChecker.mapHttpFailure(SocketTimeoutException("recv timeout"), 8.0).code,
        )
        assertEquals(
            ErrorCodes.HTTP_ERROR,
            StagedChecker.mapHttpFailure(SSLException("broken pipe"), 8.0).code,
        )
        assertEquals(
            ErrorCodes.HTTP_ERROR,
            StagedChecker.mapHttpFailure(java.io.IOException("oops"), 8.0).code,
        )
    }

    @Test
    fun `check target formatting matches server`() {
        fun spec(id: String, kind: CheckKind, host: String, port: Int, path: String) = CheckSpec(
            id = id,
            label = id,
            kind = kind,
            host = host,
            port = port,
            path = path,
            expect = emptyList(),
            core = false,
            timeoutSec = 8,
        )
        assertEquals("www.youtube.com", spec("a", CheckKind.DNS, "www.youtube.com", 443, "").target())
        assertEquals("www.youtube.com:443", spec("b", CheckKind.TCP, "www.youtube.com", 443, "").target())
        assertEquals(
            "www.youtube.com/generate_204",
            spec("c", CheckKind.HTTPS, "www.youtube.com", 443, "/generate_204").target(),
        )
        assertEquals(
            "example.com:8443/",
            spec("d", CheckKind.HTTPS, "example.com", 8443, "").target(),
        )
    }

    @Test
    fun `error labels match server dictionary`() {
        val expected = mapOf(
            "dns_error" to "DNS-запись не разрешается",
            "dns_timeout" to "DNS-запрос не завершился",
            "connect_timeout" to "TCP-соединение не устанавливается (таймаут)",
            "connect_refused" to "TCP-соединение отклонено",
            "connect_unreachable" to "Сеть недоступна",
            "tls_handshake_timeout" to "TLS-рукопожатие не завершилось (таймаут)",
            "tls_reset" to "TLS-соединение сброшено",
            "tls_cert_error" to "Ошибка TLS-сертификата",
            "tls_error" to "Ошибка TLS",
            "read_timeout" to "Ответ не получен (таймаут)",
            "http_status_unexpected" to "Неожиданный HTTP-статус",
            "http_error" to "Ошибка HTTP-обмена",
            "unknown_error" to "Неизвестная ошибка",
        )
        assertEquals(expected, ErrorCodes.LABELS)
        assertEquals(
            setOf(
                "connect_timeout",
                "connect_unreachable",
                "tls_handshake_timeout",
                "tls_reset",
                "tls_error",
                "tls_cert_error",
                "read_timeout",
            ),
            ErrorCodes.BLOCK_ERRORS,
        )
        assertEquals(setOf("dns_error", "dns_timeout"), ErrorCodes.DNS_ERRORS)
    }

    @Test
    fun `check options fall back to spec timeout and dns budget`() {
        val spec = CheckSpec(
            id = "x",
            label = "x",
            kind = CheckKind.HTTPS,
            host = "example.com",
            port = 443,
            path = "/",
            expect = listOf(200),
            core = true,
            timeoutSec = 8,
        )
        val defaults = CheckOptions()
        assertEquals(8.0, defaults.timeoutFor(spec), 0.001)
        assertEquals(4.0, defaults.dnsBudgetFor(spec), 0.001)

        val custom = CheckOptions(checkTimeoutSec = 3, dnsTimeoutSec = 10)
        assertEquals(3.0, custom.timeoutFor(spec), 0.001)
        assertEquals(3.0, custom.dnsBudgetFor(spec), 0.001)
    }
}
