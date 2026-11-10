package ru.netmon.android.service

import android.app.Notification
import android.graphics.Color
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationManagerCompat
import ru.netmon.android.MainActivity
import ru.netmon.android.R
import ru.netmon.core.AppConfig
import ru.netmon.core.Snapshot
import ru.netmon.core.Status

/**
 * Уведомления: постоянное уведомление foreground-сервиса («Мониторинг сети»)
 * и оповещения при переходе сервиса в blocked/down (notify_on из config.json).
 */
class Notifications(private val context: Context, private val config: AppConfig) {

    private val manager: NotificationManager =
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    /** Канал статуса — тихий, низкий приоритет. */
    fun ensureStatusChannel() {
        val channel = NotificationChannel(
            CHANNEL_STATUS,
            context.getString(R.string.notification_channel_status),
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = context.getString(R.string.notification_channel_status_desc)
            setShowBadge(false)
            enableVibration(false)
            setSound(null, null)
        }
        manager.createNotificationChannel(channel)
    }

    /**
     * Канал алертов: настройки звука/вибрации нельзя менять у существующего
     * канала, поэтому id зависит от них (создаём только нужный вариант).
     */
    fun alertChannelId(sound: Boolean, vibrate: Boolean): String =
        "net_monitor_alerts_${if (sound) "s" else "q"}_${if (vibrate) "v" else "n"}"

    fun ensureAlertChannel(sound: Boolean, vibrate: Boolean) {
        val channelId = alertChannelId(sound, vibrate)
        if (manager.getNotificationChannel(channelId) != null) return
        val channel = NotificationChannel(
            channelId,
            context.getString(R.string.notification_channel_alerts),
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = context.getString(R.string.notification_channel_alerts_desc)
            enableVibration(vibrate)
            if (!vibrate) {
                vibrationPattern = longArrayOf(0)
                enableVibration(false)
            }
            if (sound) {
                setSound(
                    android.media.RingtoneManager.getDefaultUri(android.media.RingtoneManager.TYPE_NOTIFICATION),
                    null,
                )
            } else {
                setSound(null, null)
            }
        }
        manager.createNotificationChannel(channel)
    }

    private fun contentIntent(): PendingIntent {
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        return PendingIntent.getActivity(
            context,
            0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun serviceIntent(action: String, requestCode: Int): PendingIntent {
        val intent = Intent(context, MonitorService::class.java).setAction(action)
        return PendingIntent.getService(
            context,
            requestCode,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    /** Иконка статуса для строки состояния (система делает её монохромной). */
    private fun statusIcon(status: Status): Int = when (status) {
        Status.OK -> R.drawable.ic_stat_ok
        Status.DEGRADED -> R.drawable.ic_stat_degraded
        Status.BLOCKED -> R.drawable.ic_stat_blocked
        Status.DOWN -> R.drawable.ic_stat_down
        Status.UNKNOWN -> R.drawable.ic_stat_unknown
    }

    /** Акцентный цвет уведомления в шторке (в строке состояния цвет недоступен). */
    private fun statusColor(status: Status): Int {
        val hex = config.colorFor(status.id)
        return try {
            Color.parseColor(hex)
        } catch (exc: IllegalArgumentException) {
            Color.GRAY
        }
    }

    /** Постоянное уведомление со сводкой статусов и иконкой-индикатором. */
    fun statusNotification(snapshot: Snapshot): Notification {
        val lines = snapshot.services.joinToString(" · ") { service ->
            "${service.name}: ${service.statusLabel}"
        }
        val overall = snapshot.summary.overall
        val title = "Мониторинг сети · ${snapshot.summary.overallLabel}"
        val builder = Notification.Builder(context, CHANNEL_STATUS)
            .setSmallIcon(statusIcon(overall))
            .setColor(statusColor(overall))
            .setContentTitle(title)
            .setContentText(lines.ifEmpty { "Проверки ещё не выполнялись" })
            .setStyle(Notification.BigTextStyle().bigText(lines))
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(true)
            .setCategory(Notification.CATEGORY_STATUS)
            .setVisibility(Notification.VISIBILITY_PUBLIC)
            .setContentIntent(contentIntent())
            .addAction(
                Notification.Action.Builder(
                    null,
                    "Проверить",
                    serviceIntent(MonitorService.ACTION_CHECK_NOW, REQ_CHECK),
                ).build(),
            )
            .addAction(
                Notification.Action.Builder(
                    null,
                    "Стоп",
                    serviceIntent(MonitorService.ACTION_STOP, REQ_STOP),
                ).build(),
            )
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.S) {
            builder.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE)
        }
        return builder.build()
    }

    /** Оповещение о переходе сервиса в blocked/down. */
    fun alertNotification(
        serviceName: String,
        statusLabel: String,
        reason: String,
        sound: Boolean,
        vibrate: Boolean,
    ): Notification {
        return Notification.Builder(context, alertChannelId(sound, vibrate))
            .setSmallIcon(R.drawable.ic_stat_network)
            .setContentTitle("$serviceName: $statusLabel")
            .setContentText(reason)
            .setStyle(Notification.BigTextStyle().bigText(reason))
            .setAutoCancel(true)
            .setContentIntent(contentIntent())
            .build()
    }

    fun notifyStatus(snapshot: Snapshot) {
        ensureStatusChannel()
        NotificationManagerCompat.from(context).notify(STATUS_NOTIFICATION_ID, statusNotification(snapshot))
    }

    fun notifyAlert(
        serviceId: String,
        serviceName: String,
        statusLabel: String,
        reason: String,
        sound: Boolean,
        vibrate: Boolean,
    ) {
        ensureAlertChannel(sound, vibrate)
        val id = ALERT_NOTIFICATION_BASE + (serviceId.hashCode() and 0x7F)
        NotificationManagerCompat.from(context).notify(
            id,
            alertNotification(serviceName, statusLabel, reason, sound, vibrate),
        )
    }

    fun cancelAlert(serviceId: String) {
        NotificationManagerCompat.from(context)
            .cancel(ALERT_NOTIFICATION_BASE + (serviceId.hashCode() and 0x7F))
    }

    fun cancelAll() {
        NotificationManagerCompat.from(context).cancelAll()
    }

    companion object {
        const val CHANNEL_STATUS: String = "net_monitor_status"
        const val STATUS_NOTIFICATION_ID: Int = 1001
        const val ALERT_NOTIFICATION_BASE: Int = 2000
        private const val REQ_CHECK = 11
        private const val REQ_STOP = 12

        /** Статусы, при которых показываем системный алерт (по умолчанию из config). */
        val ALERT_STATUSES: Set<String> = setOf(Status.BLOCKED.id, Status.DOWN.id)
    }
}
