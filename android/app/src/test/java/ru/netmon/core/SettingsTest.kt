package ru.netmon.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Пользовательские настройки: дефолты из config.json и наложение на движок. */
class SettingsTest {

    private val config = TestFixtures.repoConfig()

    @Test
    fun `defaults come from config`() {
        val defaults = UserSettings.defaults(config)
        assertEquals(20, defaults.intervalSec)
        assertEquals(8, defaults.checkTimeoutSec)
        assertEquals(4, defaults.dnsTimeoutSec)
        assertEquals(2, defaults.openAfterFailures)
        assertEquals(1, defaults.closeAfterSuccesses)
        assertEquals(500, defaults.maxStored)
        assertEquals(setOf("youtube", "telegram", "internet"), defaults.enabledServiceIds)
        assertTrue(defaults.notificationsEnabled)
        assertEquals(setOf("blocked", "down"), defaults.notifyOn)
        assertTrue(defaults.foregroundService)
        assertFalse(defaults.sound)
        assertFalse(defaults.vibrate)
    }

    @Test
    fun `effective config applies user overrides`() {
        val custom = UserSettings.defaults(config).copy(
            intervalSec = 45,
            checkTimeoutSec = 12,
            dnsTimeoutSec = 3,
            openAfterFailures = 4,
            closeAfterSuccesses = 3,
            maxStored = 100,
            notificationsEnabled = false,
            notifyOn = setOf("down"),
            sound = true,
            vibrate = true,
        )
        val effective = custom.effectiveConfig(config)
        assertEquals(45, effective.polling.intervalSec)
        assertEquals(12, effective.polling.checkTimeoutSec)
        assertEquals(3, effective.polling.dnsTimeoutSec)
        assertEquals(4, effective.incidents.openAfterFailures)
        assertEquals(3, effective.incidents.closeAfterSuccesses)
        assertEquals(100, effective.incidents.maxStored)
        assertFalse(effective.notifications.android.enabled)
        assertEquals(listOf("down"), effective.notifications.android.notifyOn)
        assertTrue(effective.notifications.android.sound)
        assertTrue(effective.notifications.android.vibrate)
        // Сервисы и их проверки не меняются пользовательскими настройками.
        assertEquals(config.services, effective.services)
    }

    @Test
    fun `interval and thresholds are clamped to sane minimums`() {
        val broken = UserSettings.defaults(config).copy(
            intervalSec = 1,
            checkTimeoutSec = 0,
            dnsTimeoutSec = 0,
            openAfterFailures = 0,
            closeAfterSuccesses = 0,
            maxStored = 0,
        )
        val effective = broken.effectiveConfig(config)
        assertEquals(UserSettings.MIN_INTERVAL_SEC, effective.polling.intervalSec)
        assertEquals(1, effective.polling.checkTimeoutSec)
        assertEquals(1, effective.polling.dnsTimeoutSec)
        assertEquals(1, effective.incidents.openAfterFailures)
        assertEquals(1, effective.incidents.closeAfterSuccesses)
        assertEquals(1, effective.incidents.maxStored)
    }

    @Test
    fun `disabled services are filtered from effective config`() {
        val custom = UserSettings.defaults(config).copy(enabledServiceIds = setOf("youtube"))
        val effective = custom.effectiveConfig(config)
        assertEquals(listOf("youtube"), effective.services.map { it.id })
        // Пустой набор не должен выключать всё приложение.
        val empty = UserSettings.defaults(config).copy(enabledServiceIds = emptySet())
        assertEquals(config.services, empty.effectiveConfig(config).services)
    }

    @Test
    fun `notify on is normalized and ordered by status list`() {
        val custom = UserSettings.defaults(config).copy(notifyOn = setOf("down", "blocked"))
        assertEquals(listOf("blocked", "down"), custom.effectiveConfig(config).notifications.android.notifyOn)
        assertEquals(UserSettings.NOTIFY_STATUSES, listOf("ok", "degraded", "blocked", "down", "unknown"))
    }

    @Test
    fun `check options use user timeouts`() {
        val custom = UserSettings.defaults(config).copy(checkTimeoutSec = 6, dnsTimeoutSec = 2)
        val options = custom.checkOptions(config)
        assertEquals(6, options.checkTimeoutSec)
        assertEquals(2, options.dnsTimeoutSec)
        val spec = config.serviceById("youtube")!!.checks.first()
        assertEquals(6.0, options.timeoutFor(spec), 0.001)
        assertEquals(2.0, options.dnsBudgetFor(spec), 0.001)

        // Дефолтный таймаут не перекрывает per-check timeout из config.json.
        val defaults = UserSettings.defaults(config).checkOptions(config)
        assertNull(defaults.checkTimeoutSec)
        assertEquals(8.0, defaults.timeoutFor(spec), 0.001)
    }
}
