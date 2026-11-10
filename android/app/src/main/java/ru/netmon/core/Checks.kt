package ru.netmon.core

import java.util.concurrent.TimeUnit

/** Вид проверки — как поле `kind` в config.json. */
enum class CheckKind(val id: String) {
    HTTPS("https"),
    TLS("tls"),
    TCP("tcp"),
    DNS("dns");

    companion object {
        fun fromId(id: String?): CheckKind? = entries.firstOrNull { it.id == id }
    }
}

/** Одна проверка из config.json (services[].checks[]). */
data class CheckSpec(
    val id: String,
    val label: String,
    val kind: CheckKind,
    val host: String,
    val port: Int,
    val path: String,
    val expect: List<Int>,
    val core: Boolean,
    val timeoutSec: Int,
) {
    /** Как `check_target()` в server.py: то, что показываем в причине сбоя. */
    fun target(): String = when (kind) {
        CheckKind.DNS -> host
        CheckKind.TCP -> "$host:$port"
        else -> {
            val hostPort = if (port == 443) host else "$host:$port"
            hostPort + (path.ifEmpty { "/" })
        }
    }
}

/** Статус сервиса — ровно те же идентификаторы, что и в config.json/дашборде. */
enum class Status(val id: String) {
    OK("ok"),
    DEGRADED("degraded"),
    BLOCKED("blocked"),
    DOWN("down"),
    UNKNOWN("unknown");

    companion object {
        val ALL: List<Status> = entries.toList()

        fun fromId(id: String?): Status =
            entries.firstOrNull { it.id == id } ?: UNKNOWN
    }
}

/** Тайминги этапов одной проверки, мс. null — этап не выполнялся. */
data class StageTimings(
    val dnsMs: Int? = null,
    val tcpMs: Int? = null,
    val tlsMs: Int? = null,
    val httpMs: Int? = null,
)

/**
 * Результат одной проверки. Структура полей совпадает с server.py `_mk_result`,
 * чтобы UI и журнал выглядели одинаково на всех платформах.
 */
data class CheckResult(
    val id: String,
    val label: String,
    val target: String,
    val kind: CheckKind,
    val core: Boolean,
    val ok: Boolean,
    val latencyMs: Int,
    val stage: String,
    val error: String? = null,
    val errorText: String? = null,
    val httpStatus: Int? = null,
    val ip: String? = null,
    val timings: StageTimings = StageTimings(),
    val measuredAt: String = "",
) {
    val errorLabel: String?
        get() = if (error.isNullOrEmpty()) null else ErrorCodes.label(error)

    /** Текст причины сбоя: «<подпись ошибки> — <цель>». */
    fun reasonText(): String = "${errorLabel ?: "Ошибка проверки"} — $target"
}

/** Коды ошибок и подписи — паритет с server.py (ERROR_LABELS / BLOCK_ERRORS). */
object ErrorCodes {
    const val DNS_ERROR = "dns_error"
    const val DNS_TIMEOUT = "dns_timeout"
    const val CONNECT_TIMEOUT = "connect_timeout"
    const val CONNECT_REFUSED = "connect_refused"
    const val CONNECT_UNREACHABLE = "connect_unreachable"
    const val TLS_HANDSHAKE_TIMEOUT = "tls_handshake_timeout"
    const val TLS_RESET = "tls_reset"
    const val TLS_CERT_ERROR = "tls_cert_error"
    const val TLS_ERROR = "tls_error"
    const val READ_TIMEOUT = "read_timeout"
    const val HTTP_STATUS_UNEXPECTED = "http_status_unexpected"
    const val HTTP_ERROR = "http_error"
    const val UNKNOWN_ERROR = "unknown_error"

    val LABELS: Map<String, String> = linkedMapOf(
        DNS_ERROR to "DNS-запись не разрешается",
        DNS_TIMEOUT to "DNS-запрос не завершился",
        CONNECT_TIMEOUT to "TCP-соединение не устанавливается (таймаут)",
        CONNECT_REFUSED to "TCP-соединение отклонено",
        CONNECT_UNREACHABLE to "Сеть недоступна",
        TLS_HANDSHAKE_TIMEOUT to "TLS-рукопожатие не завершилось (таймаут)",
        TLS_RESET to "TLS-соединение сброшено",
        TLS_CERT_ERROR to "Ошибка TLS-сертификата",
        TLS_ERROR to "Ошибка TLS",
        READ_TIMEOUT to "Ответ не получен (таймаут)",
        HTTP_STATUS_UNEXPECTED to "Неожиданный HTTP-статус",
        HTTP_ERROR to "Ошибка HTTP-обмена",
        UNKNOWN_ERROR to "Неизвестная ошибка",
    )

    /** Ошибки, которые при живом интернете типичны для фильтрации трафика. */
    val BLOCK_ERRORS: Set<String> = linkedSetOf(
        CONNECT_TIMEOUT,
        CONNECT_UNREACHABLE,
        TLS_HANDSHAKE_TIMEOUT,
        TLS_RESET,
        TLS_ERROR,
        TLS_CERT_ERROR,
        READ_TIMEOUT,
    )

    /** Только DNS-ошибки: при работающем интернете это тоже похоже на блокировку. */
    val DNS_ERRORS: Set<String> = setOf(DNS_ERROR, DNS_TIMEOUT)

    fun label(code: String): String = LABELS[code] ?: "Ошибка проверки"
}

/** Этапы проверки (для UI/журнала). */
object CheckStage {
    const val OK = "ok"
    const val DNS = "dns"
    const val TCP = "tcp"
    const val TLS = "tls"
    const val HTTP = "http"
    const val UNKNOWN = "unknown"
}

/** Внутренняя ошибка проверки с кодом/этапом — аналог CheckError в server.py. */
class CheckFailure(
    val code: String,
    val stage: String,
    val text: String,
) : Exception(text)

/** Параметры прогона, перекрывающие значения из config.json (настройки пользователя). */
data class CheckOptions(
    val checkTimeoutSec: Int? = null,
    val dnsTimeoutSec: Int? = null,
) {
    fun timeoutFor(spec: CheckSpec): Double =
        (checkTimeoutSec ?: spec.timeoutSec).toDouble().coerceAtLeast(1.0)

    fun dnsBudgetFor(spec: CheckSpec): Double =
        (dnsTimeoutSec ?: 4).toDouble().coerceAtLeast(1.0)
            .coerceAtMost(timeoutFor(spec))
}

/** Абстракция исполнителя проверок: реальная сеть или фейк в тестах. */
interface CheckExecutor {
    fun run(spec: CheckSpec, options: CheckOptions = CheckOptions()): CheckResult
}

internal fun timeoutToMillis(seconds: Double): Int =
    TimeUnit.SECONDS.toMillis(seconds.toLong().coerceAtLeast(0L))
        .coerceAtLeast(1L)
        .coerceAtMost(Int.MAX_VALUE.toLong())
        .toInt()
