package ru.netmon.core

import java.io.File
import java.io.InputStream

/**
 * Модель настроек приложения.
 *
 * ВАЖНО (паритет с десктопом): единственный источник истины — `config.json`
 * в корне репозитория. При сборке Gradle копирует его в
 * `app/src/main/assets/config.json`, приложение читает именно этот файл.
 * Значения в [AppConfig.DEFAULTS] — только аварийный фолбэк, если файла нет,
 * и они обязаны совпадать с `config.json` (это проверяет unit-тест
 * ConfigParityTest).
 */
data class ServerConfig(
    val host: String,
    val port: Int,
)

data class PollingConfig(
    val intervalSec: Int,
    val checkTimeoutSec: Int,
    val dnsTimeoutSec: Int,
)

data class IncidentsConfig(
    val openAfterFailures: Int,
    val closeAfterSuccesses: Int,
    val maxStored: Int,
)

data class HistoryConfig(
    val maxAgeHours: Int,
    val maxPoints: Int,
    val seriesPoints: Int,
)

data class AndroidNotificationsConfig(
    val enabled: Boolean,
    val notifyOn: List<String>,
    val foregroundService: Boolean,
    val sound: Boolean,
    val vibrate: Boolean,
)

data class NotificationsConfig(
    val android: AndroidNotificationsConfig,
)

/** Один сервис из services[] (карточка на дашборде и набор проверок). */
data class ServiceConfig(
    val id: String,
    val name: String,
    val subtitle: String,
    val icon: String,
    val accent: String,
    val incidentTitle: String,
    val hints: Map<String, String>,
    val checks: List<CheckSpec>,
)

data class AppConfig(
    val version: Int,
    val server: ServerConfig,
    val polling: PollingConfig,
    val incidents: IncidentsConfig,
    val history: HistoryConfig,
    val statusLabels: Map<String, String>,
    val statusColors: Map<String, String>,
    val services: List<ServiceConfig>,
    val notifications: NotificationsConfig,
) {
    fun serviceById(id: String): ServiceConfig? = services.firstOrNull { it.id == id }

    /** Подпись статуса из config.json (фолбэк — как на дашборде). */
    fun labelFor(status: String): String =
        statusLabels[status] ?: statusLabels[Status.UNKNOWN.id] ?: "Нет данных"

    /** Цвет статуса из config.json (фолбэк — серый «нет данных»). */
    fun colorFor(status: String): String =
        statusColors[status] ?: statusColors[Status.UNKNOWN.id] ?: "#8e8e93"

    companion object {
        /** Идентификатор «контрольного канала» — как в server.py/static/app.js. */
        const val BASELINE_SERVICE_ID: String = "internet"

        /**
         * Фолбэк на случай отсутствия/повреждения assets/config.json.
         * Полностью повторяет корневой config.json репозитория.
         */
        val DEFAULTS: AppConfig = AppConfig(
            version = 1,
            server = ServerConfig(host = "127.0.0.1", port = 8099),
            polling = PollingConfig(intervalSec = 20, checkTimeoutSec = 8, dnsTimeoutSec = 4),
            incidents = IncidentsConfig(openAfterFailures = 2, closeAfterSuccesses = 1, maxStored = 500),
            history = HistoryConfig(maxAgeHours = 24, maxPoints = 8640, seriesPoints = 180),
            statusLabels = linkedMapOf(
                "ok" to "Доступен",
                "degraded" to "Нестабильно",
                "blocked" to "Похоже на блокировку",
                "down" to "Недоступен",
                "unknown" to "Нет данных",
            ),
            statusColors = linkedMapOf(
                "ok" to "#34c759",
                "degraded" to "#ff9500",
                "blocked" to "#ff3b30",
                "down" to "#ff3b30",
                "unknown" to "#8e8e93",
            ),
            services = listOf(
                ServiceConfig(
                    id = "youtube",
                    name = "YouTube",
                    subtitle = "видеохостинг · youtube.com",
                    icon = "youtube",
                    accent = "#ff3040",
                    incidentTitle = "YouTube недоступен",
                    hints = linkedMapOf(
                        "degraded" to "Часть проверок не проходит — сравните с доступом через VPN",
                        "blocked" to "Похоже на блокировку: проверьте доступ через VPN или мобильный интернет",
                        "down" to "Сервис не отвечает — проверьте соединение и повторите позже",
                    ),
                    checks = listOf(
                        CheckSpec(
                            id = "yt_https",
                            label = "HTTPS /generate_204",
                            kind = CheckKind.HTTPS,
                            host = "www.youtube.com",
                            port = 443,
                            path = "/generate_204",
                            expect = listOf(204),
                            core = true,
                            timeoutSec = 8,
                        ),
                        CheckSpec(
                            id = "yt_img",
                            label = "HTTPS i.ytimg.com /generate_204",
                            kind = CheckKind.HTTPS,
                            host = "i.ytimg.com",
                            port = 443,
                            path = "/generate_204",
                            expect = listOf(204),
                            core = true,
                            timeoutSec = 8,
                        ),
                        CheckSpec(
                            id = "yt_www",
                            label = "HTTPS главная страница",
                            kind = CheckKind.HTTPS,
                            host = "www.youtube.com",
                            port = 443,
                            path = "/",
                            expect = listOf(200, 301, 302, 303),
                            core = false,
                            timeoutSec = 8,
                        ),
                        CheckSpec(
                            id = "yt_tcp",
                            label = "TCP www.youtube.com:443",
                            kind = CheckKind.TCP,
                            host = "www.youtube.com",
                            port = 443,
                            path = "",
                            expect = emptyList(),
                            core = false,
                            timeoutSec = 8,
                        ),
                        CheckSpec(
                            id = "yt_dns",
                            label = "DNS www.youtube.com",
                            kind = CheckKind.DNS,
                            host = "www.youtube.com",
                            port = 443,
                            path = "",
                            expect = emptyList(),
                            core = false,
                            timeoutSec = 8,
                        ),
                    ),
                ),
                ServiceConfig(
                    id = "telegram",
                    name = "Telegram",
                    subtitle = "мессенджер · telegram.org",
                    icon = "telegram",
                    accent = "#2ea6e0",
                    incidentTitle = "Telegram недоступен",
                    hints = linkedMapOf(
                        "degraded" to "Часть проверок не проходит — возможна фильтрация MTProto",
                        "blocked" to "TCP/TLS к Telegram не проходит: вероятна блокировка — включите VPN",
                        "down" to "Сервис не отвечает — проверьте соединение и повторите позже",
                    ),
                    checks = listOf(
                        CheckSpec(
                            id = "tg_web",
                            label = "HTTPS web.telegram.org",
                            kind = CheckKind.HTTPS,
                            host = "web.telegram.org",
                            port = 443,
                            path = "/",
                            expect = listOf(200, 301, 302),
                            core = true,
                            timeoutSec = 8,
                        ),
                        CheckSpec(
                            id = "tg_api",
                            label = "HTTPS api.telegram.org",
                            kind = CheckKind.HTTPS,
                            host = "api.telegram.org",
                            port = 443,
                            path = "/",
                            expect = listOf(200, 301, 302, 400, 404),
                            core = true,
                            timeoutSec = 8,
                        ),
                        CheckSpec(
                            id = "tg_dc2",
                            label = "TCP DC2 149.154.167.51:443",
                            kind = CheckKind.TCP,
                            host = "149.154.167.51",
                            port = 443,
                            path = "",
                            expect = emptyList(),
                            core = true,
                            timeoutSec = 8,
                        ),
                        CheckSpec(
                            id = "tg_dc1",
                            label = "TCP DC1 149.154.175.50:443",
                            kind = CheckKind.TCP,
                            host = "149.154.175.50",
                            port = 443,
                            path = "",
                            expect = emptyList(),
                            core = false,
                            timeoutSec = 8,
                        ),
                        CheckSpec(
                            id = "tg_dc4",
                            label = "TCP DC4 149.154.167.91:443",
                            kind = CheckKind.TCP,
                            host = "149.154.167.91",
                            port = 443,
                            path = "",
                            expect = emptyList(),
                            core = false,
                            timeoutSec = 8,
                        ),
                        CheckSpec(
                            id = "tg_dns_api",
                            label = "DNS api.telegram.org",
                            kind = CheckKind.DNS,
                            host = "api.telegram.org",
                            port = 443,
                            path = "",
                            expect = emptyList(),
                            core = false,
                            timeoutSec = 8,
                        ),
                        CheckSpec(
                            id = "tg_dns_web",
                            label = "DNS web.telegram.org",
                            kind = CheckKind.DNS,
                            host = "web.telegram.org",
                            port = 443,
                            path = "",
                            expect = emptyList(),
                            core = false,
                            timeoutSec = 8,
                        ),
                    ),
                ),
                ServiceConfig(
                    id = "internet",
                    name = "Интернет",
                    subtitle = "контрольный канал · gstatic / cloudflare",
                    icon = "globe",
                    accent = "#38d39f",
                    incidentTitle = "Интернет недоступен",
                    hints = linkedMapOf(
                        "degraded" to "Канал нестабилен — проверьте Wi-Fi/роутер",
                        "down" to "Проверьте Wi-Fi/кабель и роутер: базовые проверки не проходят",
                    ),
                    checks = listOf(
                        CheckSpec(
                            id = "inet_gstatic",
                            label = "HTTPS connectivitycheck.gstatic.com",
                            kind = CheckKind.HTTPS,
                            host = "connectivitycheck.gstatic.com",
                            port = 443,
                            path = "/generate_204",
                            expect = listOf(204),
                            core = true,
                            timeoutSec = 8,
                        ),
                        CheckSpec(
                            id = "inet_cloudflare",
                            label = "HTTPS cloudflare.com/cdn-cgi/trace",
                            kind = CheckKind.HTTPS,
                            host = "cloudflare.com",
                            port = 443,
                            path = "/cdn-cgi/trace",
                            expect = listOf(200),
                            core = true,
                            timeoutSec = 8,
                        ),
                        CheckSpec(
                            id = "inet_tcp",
                            label = "TCP 1.1.1.1:443",
                            kind = CheckKind.TCP,
                            host = "1.1.1.1",
                            port = 443,
                            path = "",
                            expect = emptyList(),
                            core = true,
                            timeoutSec = 8,
                        ),
                        CheckSpec(
                            id = "inet_dns",
                            label = "DNS cloudflare.com",
                            kind = CheckKind.DNS,
                            host = "cloudflare.com",
                            port = 443,
                            path = "",
                            expect = emptyList(),
                            core = false,
                            timeoutSec = 8,
                        ),
                    ),
                ),
            ),
            notifications = NotificationsConfig(
                android = AndroidNotificationsConfig(
                    enabled = true,
                    notifyOn = listOf("blocked", "down"),
                    foregroundService = true,
                    sound = false,
                    vibrate = false,
                ),
            ),
        )
    }
}

/** Разбор config.json в модель. */
object ConfigParser {

    fun parse(text: String): AppConfig {
        val root = Json.parseObject(text)
        val polling = root.objOrNull("polling")
        val incidents = root.objOrNull("incidents")
        val history = root.objOrNull("history")
        val server = root.objOrNull("server")
        val notifications = root.objOrNull("notifications")
        val android = notifications?.objOrNull("android")

        val services = root.listOrEmpty("services").map { raw ->
            parseService(
                raw.jsonObject()
                    ?: throw JsonParseException("Элемент services[] должен быть объектом"),
            )
        }
        if (services.isEmpty()) throw JsonParseException("config.json: пустой список services")

        val labels = root.stringMap("status_labels").ifEmpty { AppConfig.DEFAULTS.statusLabels }
        val colors = root.stringMap("status_colors").ifEmpty { AppConfig.DEFAULTS.statusColors }

        return AppConfig(
            version = root.intOr("version", AppConfig.DEFAULTS.version),
            server = ServerConfig(
                host = server?.stringOrNull("host") ?: AppConfig.DEFAULTS.server.host,
                port = server?.intOr("port", AppConfig.DEFAULTS.server.port) ?: AppConfig.DEFAULTS.server.port,
            ),
            polling = PollingConfig(
                intervalSec = polling?.intOr("interval_sec", AppConfig.DEFAULTS.polling.intervalSec)
                    ?: AppConfig.DEFAULTS.polling.intervalSec,
                checkTimeoutSec = polling?.intOr("check_timeout_sec", AppConfig.DEFAULTS.polling.checkTimeoutSec)
                    ?: AppConfig.DEFAULTS.polling.checkTimeoutSec,
                dnsTimeoutSec = polling?.intOr("dns_timeout_sec", AppConfig.DEFAULTS.polling.dnsTimeoutSec)
                    ?: AppConfig.DEFAULTS.polling.dnsTimeoutSec,
            ),
            incidents = IncidentsConfig(
                openAfterFailures = incidents?.intOr(
                    "open_after_failures",
                    AppConfig.DEFAULTS.incidents.openAfterFailures,
                ) ?: AppConfig.DEFAULTS.incidents.openAfterFailures,
                closeAfterSuccesses = incidents?.intOr(
                    "close_after_successes",
                    AppConfig.DEFAULTS.incidents.closeAfterSuccesses,
                ) ?: AppConfig.DEFAULTS.incidents.closeAfterSuccesses,
                maxStored = incidents?.intOr("max_stored", AppConfig.DEFAULTS.incidents.maxStored)
                    ?: AppConfig.DEFAULTS.incidents.maxStored,
            ),
            history = HistoryConfig(
                maxAgeHours = history?.intOr("max_age_hours", AppConfig.DEFAULTS.history.maxAgeHours)
                    ?: AppConfig.DEFAULTS.history.maxAgeHours,
                maxPoints = history?.intOr("max_points", AppConfig.DEFAULTS.history.maxPoints)
                    ?: AppConfig.DEFAULTS.history.maxPoints,
                seriesPoints = history?.intOr("series_points", AppConfig.DEFAULTS.history.seriesPoints)
                    ?: AppConfig.DEFAULTS.history.seriesPoints,
            ),
            statusLabels = labels,
            statusColors = colors,
            services = services,
            notifications = NotificationsConfig(
                android = AndroidNotificationsConfig(
                    enabled = android?.boolOr(
                        "enabled",
                        AppConfig.DEFAULTS.notifications.android.enabled,
                    ) ?: AppConfig.DEFAULTS.notifications.android.enabled,
                    notifyOn = android?.listOrEmpty("notify_on")?.mapNotNull { it.jsonString() }
                        ?.ifEmpty { null }
                        ?: AppConfig.DEFAULTS.notifications.android.notifyOn,
                    foregroundService = android?.boolOr(
                        "foreground_service",
                        AppConfig.DEFAULTS.notifications.android.foregroundService,
                    ) ?: AppConfig.DEFAULTS.notifications.android.foregroundService,
                    sound = android?.boolOr("sound", AppConfig.DEFAULTS.notifications.android.sound)
                        ?: AppConfig.DEFAULTS.notifications.android.sound,
                    vibrate = android?.boolOr("vibrate", AppConfig.DEFAULTS.notifications.android.vibrate)
                        ?: AppConfig.DEFAULTS.notifications.android.vibrate,
                ),
            ),
        )
    }

    private fun parseService(raw: Map<String, Any?>): ServiceConfig {
        val id = raw.stringOrNull("id") ?: throw JsonParseException("services[]: нет поля id")
        val checks = raw.listOrEmpty("checks").map { item ->
            parseCheck(item.jsonObject() ?: throw JsonParseException("checks[] сервиса $id должен быть объектом"))
        }
        if (checks.isEmpty()) throw JsonParseException("services[$id]: пустой список checks")
        return ServiceConfig(
            id = id,
            name = raw.stringOrNull("name") ?: id,
            subtitle = raw.stringOrNull("subtitle") ?: "",
            icon = raw.stringOrNull("icon") ?: "globe",
            accent = raw.stringOrNull("accent") ?: "#8798ad",
            incidentTitle = raw.stringOrNull("incident_title") ?: "${raw.stringOrNull("name") ?: id} недоступен",
            hints = raw.stringMap("hints"),
            checks = checks,
        )
    }

    private fun parseCheck(raw: Map<String, Any?>): CheckSpec {
        val id = raw.stringOrNull("id") ?: throw JsonParseException("checks[]: нет поля id")
        val kindId = raw.stringOrNull("kind") ?: throw JsonParseException("checks[$id]: нет поля kind")
        val kind = CheckKind.fromId(kindId)
            ?: throw JsonParseException("checks[$id]: неизвестный kind '$kindId'")
        val host = raw.stringOrNull("host") ?: throw JsonParseException("checks[$id]: нет поля host")
        return CheckSpec(
            id = id,
            label = raw.stringOrNull("label") ?: host,
            kind = kind,
            host = host,
            port = raw.intOr("port", 443),
            path = raw.stringOrNull("path") ?: "",
            expect = raw.intList("expect"),
            core = raw.boolOr("core", false),
            timeoutSec = raw.intOr("timeout", AppConfig.DEFAULTS.polling.checkTimeoutSec),
        )
    }
}

/** Загрузка config.json из файла/потока (assets на Android, файл в JVM-тестах). */
object ConfigLoader {
    const val ASSET_NAME: String = "config.json"

    fun load(stream: InputStream): AppConfig = stream.use { input ->
        ConfigParser.parse(input.readBytes().toString(Charsets.UTF_8))
    }

    fun loadFile(file: File): AppConfig = load(file.inputStream())

    /** Текст конфига или null, если файла нет. */
    fun loadText(file: File): String? =
        if (file.isFile) file.readText(Charsets.UTF_8) else null
}
