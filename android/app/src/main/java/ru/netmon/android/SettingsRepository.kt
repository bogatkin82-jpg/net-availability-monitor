package ru.netmon.android

import android.content.Context
import ru.netmon.core.AppConfig
import ru.netmon.core.UserSettings

/**
 * Пользовательские настройки в SharedPreferences.
 *
 * Дефолты — всегда из config.json: если ключа в prefs нет, возвращается
 * значение из [AppConfig]. Сохраняются только явно изменённые поля.
 */
class SettingsRepository(context: Context, private val config: AppConfig) {

    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun load(): UserSettings {
        val defaults = UserSettings.defaults(config)
        val allServices = config.services.map { it.id }.toSet()
        val enabled = prefs.getStringSet(KEY_ENABLED_SERVICES, null)
            ?.filter { it in allServices }
            ?.toSet()
            ?: defaults.enabledServiceIds
        val notifyOn = prefs.getStringSet(KEY_NOTIFY_ON, null)
            ?.filter { it in UserSettings.NOTIFY_STATUSES }
            ?.toSet()
            ?: defaults.notifyOn
        return defaults.copy(
            intervalSec = prefs.getInt(KEY_INTERVAL, defaults.intervalSec),
            checkTimeoutSec = prefs.getInt(KEY_CHECK_TIMEOUT, defaults.checkTimeoutSec),
            dnsTimeoutSec = prefs.getInt(KEY_DNS_TIMEOUT, defaults.dnsTimeoutSec),
            openAfterFailures = prefs.getInt(KEY_OPEN_AFTER, defaults.openAfterFailures),
            closeAfterSuccesses = prefs.getInt(KEY_CLOSE_AFTER, defaults.closeAfterSuccesses),
            maxStored = prefs.getInt(KEY_MAX_STORED, defaults.maxStored),
            enabledServiceIds = enabled,
            notificationsEnabled = prefs.getBoolean(KEY_NOTIFICATIONS, defaults.notificationsEnabled),
            notifyOn = notifyOn,
            foregroundService = prefs.getBoolean(KEY_FOREGROUND, defaults.foregroundService),
            sound = prefs.getBoolean(KEY_SOUND, defaults.sound),
            vibrate = prefs.getBoolean(KEY_VIBRATE, defaults.vibrate),
        )
    }

    fun save(settings: UserSettings) {
        prefs.edit()
            .putInt(KEY_INTERVAL, settings.intervalSec)
            .putInt(KEY_CHECK_TIMEOUT, settings.checkTimeoutSec)
            .putInt(KEY_DNS_TIMEOUT, settings.dnsTimeoutSec)
            .putInt(KEY_OPEN_AFTER, settings.openAfterFailures)
            .putInt(KEY_CLOSE_AFTER, settings.closeAfterSuccesses)
            .putInt(KEY_MAX_STORED, settings.maxStored)
            .putStringSet(KEY_ENABLED_SERVICES, settings.enabledServiceIds)
            .putBoolean(KEY_NOTIFICATIONS, settings.notificationsEnabled)
            .putStringSet(KEY_NOTIFY_ON, settings.notifyOn)
            .putBoolean(KEY_FOREGROUND, settings.foregroundService)
            .putBoolean(KEY_SOUND, settings.sound)
            .putBoolean(KEY_VIBRATE, settings.vibrate)
            .apply()
    }

    /** Состояние монитора (серии, инциденты) — восстанавливается после рестарта. */
    fun saveState(json: String) {
        prefs.edit().putString(KEY_STATE, json).apply()
    }

    fun loadState(): String? = prefs.getString(KEY_STATE, null)

    fun clearState() {
        prefs.edit().remove(KEY_STATE).apply()
    }

    private companion object {
        const val PREFS_NAME = "netmon_settings"
        const val KEY_INTERVAL = "interval_sec"
        const val KEY_CHECK_TIMEOUT = "check_timeout_sec"
        const val KEY_DNS_TIMEOUT = "dns_timeout_sec"
        const val KEY_OPEN_AFTER = "open_after_failures"
        const val KEY_CLOSE_AFTER = "close_after_successes"
        const val KEY_MAX_STORED = "max_stored"
        const val KEY_ENABLED_SERVICES = "enabled_services"
        const val KEY_NOTIFICATIONS = "notifications_enabled"
        const val KEY_NOTIFY_ON = "notify_on"
        const val KEY_FOREGROUND = "foreground_service"
        const val KEY_SOUND = "sound"
        const val KEY_VIBRATE = "vibrate"
        const val KEY_STATE = "monitor_state"
    }
}
