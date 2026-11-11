package ru.netmon.android.service

import android.app.Notification
import android.graphics.Color
import android.media.AudioAttributes
import android.net.Uri
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

    /**
     * Канал статуса для строки состояния. Настройка Android
     * «Скрывать тихие уведомления в строке состояния» (hideSilentStatusBar)
     * убирает иконки полностью беззвучных уведомлений, поэтому канал формально
     * не «тихий»: ему назначен ресурс с 0.1 с тишины. Реального звука нет,
     * вибрация выключена, обновления идут с ONLY_ALERT_ONCE.
     * id v3 — важность/звук существующего канала изменить нельзя.
     */
    fun ensureStatusChannel() {
        val channel = NotificationChannel(
            CHANNEL_STATUS,
            context.getString(R.string.notification_channel_status),
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply {
            description = context.getString(R.string.notification_channel_status_desc)
            setShowBadge(false)
            enableVibration(false)
            setSound(
                silentSoundUri(),
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_NOTIFICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build(),
            )
            lockscreenVisibility = Notification.VISIBILITY_PUBLIC
        }
        manager.createNotificationChannel(channel)

        val hidden = NotificationChannel(
            CHANNEL_STATUS_HIDDEN,
            context.getString(R.string.notification_channel_status_hidden),
            NotificationManager.IMPORTANCE_MIN,
        ).apply {
            description = context.getString(R.string.notification_channel_status_hidden_desc)
            setShowBadge(false)
            enableVibration(false)
            setSound(null, null)
            lockscreenVisibility = Notification.VISIBILITY_PUBLIC
        }
        manager.createNotificationChannel(hidden)
    }

    private fun statusChannelId(): String =
        if (StatusIconPrefs.isHidden(context)) CHANNEL_STATUS_HIDDEN else CHANNEL_STATUS

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

    /** Худший статус для индикатора — как цветная точка в menu bar macOS. */
    private fun indicatorStatus(snapshot: Snapshot): Status {
        val statuses = snapshot.services.map { it.status } + snapshot.internet.status
        return when {
            statuses.any { it == Status.DOWN } -> Status.DOWN
            statuses.any { it == Status.BLOCKED } -> Status.BLOCKED
            statuses.any { it == Status.DEGRADED } -> Status.DEGRADED
            statuses.any { it == Status.UNKNOWN } -> Status.UNKNOWN
            else -> Status.OK
        }
    }

    private fun silentSoundUri(): Uri =
        Uri.parse("android.resource://${context.packageName}/${R.raw.silent}")

    /** Постоянное уведомление со сводкой статусов и иконкой-индикатором. */
    fun statusNotification(snapshot: Snapshot): Notification {
        val lines = snapshot.services.joinToString(" · ") { service ->
            "${service.name}: ${service.statusLabel}"
        }
        val overall = snapshot.summary.overall
        val indicator = indicatorStatus(snapshot)
        val title = "Мониторинг сети · ${snapshot.summary.overallLabel}"
        val hidden = StatusIconPrefs.isHidden(context)
        val builder = Notification.Builder(context, statusChannelId())
            .setSmallIcon(statusIcon(indicator))
            .setColor(statusColor(indicator))
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
        if (!hidden) {
            builder.setSound(silentSoundUri())
        }
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.S) {
            builder.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE)
        }
        return builder.build()
    }

    fun notifyStatus(snapshot: Snapshot) {
        ensureStatusChannel()
        NotificationManagerCompat.from(context)
            .notify(STATUS_NOTIFICATION_ID, statusNotification(snapshot))
    }

    fun cancelAll() {
        NotificationManagerCompat.from(context).cancelAll()
    }

    companion object {
        const val CHANNEL_STATUS: String = "net_monitor_status_v3"
        const val CHANNEL_STATUS_HIDDEN: String = "net_monitor_status_hidden_v1"
        const val STATUS_NOTIFICATION_ID: Int = 1001
        private const val REQ_CHECK = 11
        private const val REQ_STOP = 12

    }
}
