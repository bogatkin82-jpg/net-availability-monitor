package ru.netmon.android.service

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import ru.netmon.android.NetMonApp
import ru.netmon.android.monitor.MonitorController
import ru.netmon.core.Snapshot
import ru.netmon.core.UserSettings

/**
 * Foreground-сервис мониторинга (Android 8+):
 *  * постоянное уведомление «Мониторинг сети» с текущим статусом;
 *  * периодические проверки с интервалом из настроек (минимум 15 с);
 *  * системное уведомление при переходе сервиса в blocked/down (notify_on).
 *
 * Никакого WorkManager: для коротких интервалов используем корутину + delay,
 * как требует ТЗ.
 */
class MonitorService : Service() {

    private lateinit var controller: MonitorController
    private lateinit var notifications: Notifications
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var loopJob: Job? = null
    private var alertsSeeded = false
    private val lastAlertStatuses = mutableMapOf<String, String>()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        controller = (application as NetMonApp).container.controller
        notifications = Notifications(this)
        isRunning = true
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                loopJob?.cancel()
                ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_CHECK_NOW -> {
                ensureForeground()
                scope.launch { runRoundAndPublish() }
                startLoop()
                return START_STICKY
            }
        }
        ensureForeground()
        startLoop()
        return START_STICKY
    }

    private fun ensureForeground() {
        val settings = controller.settings.value
        notifications.ensureStatusChannel()
        notifications.ensureAlertChannel(settings.sound, settings.vibrate)
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
        } else {
            0
        }
        ServiceCompat.startForeground(
            this,
            Notifications.STATUS_NOTIFICATION_ID,
            notifications.statusNotification(controller.snapshot.value),
            type,
        )
    }

    private fun startLoop() {
        if (loopJob?.isActive == true) return
        loopJob = scope.launch {
            while (isActive) {
                runRoundAndPublish()
                val interval = controller.settings.value.intervalSec
                    .coerceAtLeast(UserSettings.MIN_INTERVAL_SEC)
                delay(interval * 1000L)
            }
        }
    }

    private suspend fun runRoundAndPublish() {
        val snapshot = controller.refresh() ?: return
        notifications.notifyStatus(snapshot)
        maybeNotifyAlerts(snapshot)
    }

    /**
     * Уведомление о переходе статуса сервиса в один из notify_on.
     * Первый снимок после старта только запоминает статусы (без спама).
     */
    private fun maybeNotifyAlerts(snapshot: Snapshot) {
        val settings = controller.settings.value
        val seeded = alertsSeeded
        snapshot.services.forEach { service ->
            val current = service.status.id
            val previous = lastAlertStatuses[service.id]
            if (seeded && previous != null && previous != current && current in settings.notifyOn) {
                notifications.notifyAlert(
                    serviceId = service.id,
                    serviceName = service.name,
                    statusLabel = service.statusLabel,
                    reason = service.statusReason,
                    sound = settings.sound,
                    vibrate = settings.vibrate,
                )
            }
            lastAlertStatuses[service.id] = current
        }
        alertsSeeded = true
    }

    override fun onDestroy() {
        loopJob?.cancel()
        scope.cancel()
        isRunning = false
        super.onDestroy()
    }

    companion object {
        const val ACTION_START = "ru.netmon.android.action.START"
        const val ACTION_STOP = "ru.netmon.android.action.STOP"
        const val ACTION_CHECK_NOW = "ru.netmon.android.action.CHECK_NOW"

        @Volatile
        var isRunning: Boolean = false
            private set

        fun start(context: Context) {
            ContextCompat.startForegroundService(
                context,
                Intent(context, MonitorService::class.java).setAction(ACTION_START),
            )
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, MonitorService::class.java))
        }

        fun checkNow(context: Context) {
            ContextCompat.startForegroundService(
                context,
                Intent(context, MonitorService::class.java).setAction(ACTION_CHECK_NOW),
            )
        }
    }
}
