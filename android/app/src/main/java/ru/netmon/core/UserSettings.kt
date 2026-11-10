package ru.netmon.core

/**
 * Пользовательские настройки поверх config.json.
 *
 * Дефолты всегда берутся из config.json (через [defaults]); меняются только те
 * поля, которые пользователь реально трогал в экране «Настройки». Итоговый
 * конфиг для движка собирает [effectiveConfig].
 */
data class UserSettings(
    val intervalSec: Int,
    val checkTimeoutSec: Int,
    val dnsTimeoutSec: Int,
    val openAfterFailures: Int,
    val closeAfterSuccesses: Int,
    val maxStored: Int,
    val enabledServiceIds: Set<String>,
    val notificationsEnabled: Boolean,
    val notifyOn: Set<String>,
    val foregroundService: Boolean,
    val sound: Boolean,
    val vibrate: Boolean,
) {
    /** Полный конфиг с наложенными настройками пользователя. */
    fun effectiveConfig(base: AppConfig): AppConfig {
        val services = base.services.filter { it.id in enabledServiceIds }.ifEmpty { base.services }
        // Если пользователь выключил встроенный уведомления-канал -- foreground
        // всё равно может работать, но системные алерты не шлём.
        val androidNotifications = base.notifications.android.copy(
            enabled = notificationsEnabled,
            notifyOn = notifyOn.toList().sortedBy { status -> Status.ALL.indexOf(Status.fromId(status)) },
            foregroundService = foregroundService,
            sound = sound,
            vibrate = vibrate,
        )
        return base.copy(
            polling = base.polling.copy(
                intervalSec = intervalSec.coerceAtLeast(MIN_INTERVAL_SEC),
                checkTimeoutSec = checkTimeoutSec.coerceAtLeast(1),
                dnsTimeoutSec = dnsTimeoutSec.coerceAtLeast(1),
            ),
            incidents = base.incidents.copy(
                openAfterFailures = openAfterFailures.coerceAtLeast(1),
                closeAfterSuccesses = closeAfterSuccesses.coerceAtLeast(1),
                maxStored = maxStored.coerceAtLeast(1),
            ),
            services = services,
            notifications = NotificationsConfig(android = androidNotifications),
        )
    }

    /**
     * Опции прогона. Если пользователь не менял таймаут проверки, передаём null —
     * тогда движок использует per-check `timeout` из config.json, как server.py.
     */
    fun checkOptions(config: AppConfig): CheckOptions = CheckOptions(
        checkTimeoutSec = checkTimeoutSec.coerceAtLeast(1)
            .takeIf { it != config.polling.checkTimeoutSec },
        dnsTimeoutSec = dnsTimeoutSec.coerceAtLeast(1),
    )

    companion object {
        /** Минимальный интервал фонового опроса — требование ТЗ (не чаще 15 с). */
        const val MIN_INTERVAL_SEC: Int = 15

        /** Допустимые notify_on — те же статусы, что в config.json. */
        val NOTIFY_STATUSES: List<String> = Status.ALL.map { it.id }

        fun defaults(config: AppConfig): UserSettings = UserSettings(
            intervalSec = config.polling.intervalSec,
            checkTimeoutSec = config.polling.checkTimeoutSec,
            dnsTimeoutSec = config.polling.dnsTimeoutSec,
            openAfterFailures = config.incidents.openAfterFailures,
            closeAfterSuccesses = config.incidents.closeAfterSuccesses,
            maxStored = config.incidents.maxStored,
            enabledServiceIds = config.services.map { it.id }.toSet(),
            notificationsEnabled = config.notifications.android.enabled,
            notifyOn = config.notifications.android.notifyOn.toSet(),
            foregroundService = config.notifications.android.foregroundService,
            sound = config.notifications.android.sound,
            vibrate = config.notifications.android.vibrate,
        )
    }
}
