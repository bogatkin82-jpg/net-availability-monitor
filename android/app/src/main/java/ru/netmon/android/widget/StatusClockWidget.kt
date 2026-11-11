package ru.netmon.android.widget

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.widget.RemoteViews
import ru.netmon.android.MainActivity
import ru.netmon.android.NetMonApp
import ru.netmon.android.R
import ru.netmon.core.Snapshot
import ru.netmon.core.Status
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * Виджет «цветные часы»: время на главном экране окрашивается по худшему статусу
 * сервисов — зелёный/жёлтый/красный. Обновляется тем же foreground-сервисом,
 * который рисует индикатор в строке состояния.
 */
class StatusClockWidget : AppWidgetProvider() {

    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
    ) {
        val snapshot = latestSnapshot
        appWidgetIds.forEach { id ->
            appWidgetManager.updateAppWidget(id, buildViews(context, snapshot))
        }
    }

    companion object {
        @Volatile
        private var latestSnapshot: Snapshot? = null

        private val TIME_FORMAT = DateTimeFormatter.ofPattern("HH:mm")

        /** Вызывается сервисом после каждого раунда проверок. */
        fun updateAll(context: Context, snapshot: Snapshot) {
            latestSnapshot = snapshot
            val manager = AppWidgetManager.getInstance(context)
            val ids = manager.getAppWidgetIds(
                ComponentName(context, StatusClockWidget::class.java),
            )
            if (ids.isEmpty()) return
            ids.forEach { id -> manager.updateAppWidget(id, buildViews(context, snapshot)) }
        }

        private fun worstStatus(snapshot: Snapshot): Status {
            val statuses = snapshot.services.map { it.status } + snapshot.internet.status
            return when {
                statuses.any { it == Status.DOWN } -> Status.DOWN
                statuses.any { it == Status.BLOCKED } -> Status.BLOCKED
                statuses.any { it == Status.DEGRADED } -> Status.DEGRADED
                statuses.any { it == Status.UNKNOWN } -> Status.UNKNOWN
                else -> Status.OK
            }
        }

        private fun statusColor(context: Context, status: Status): Int {
            val config = (context.applicationContext as NetMonApp).container.config
            return try {
                Color.parseColor(config.colorFor(status.id))
            } catch (exc: IllegalArgumentException) {
                Color.GRAY
            }
        }

        private fun buildViews(context: Context, snapshot: Snapshot?): RemoteViews {
            val views = RemoteViews(context.packageName, R.layout.widget_clock)
            val status = snapshot?.let { worstStatus(it) } ?: Status.UNKNOWN
            val color = statusColor(context, status)
            val time = snapshot?.let {
                Instant.ofEpochMilli(it.epoch)
                    .atZone(ZoneId.systemDefault())
                    .format(TIME_FORMAT)
            } ?: "--:--"
            views.setTextViewText(R.id.widget_time, time)
            views.setTextColor(R.id.widget_time, color)
            views.setTextViewText(R.id.widget_dot, "●")
            views.setTextColor(R.id.widget_dot, color)
            views.setTextViewText(
                R.id.widget_status,
                snapshot?.summary?.overallLabel ?: "Нет данных",
            )
            views.setTextColor(R.id.widget_status, color)
            val detail = snapshot?.services?.joinToString(" · ") { "${it.name}: ${it.statusLabel}" }
                ?: "YouTube · Telegram · Интернет"
            views.setTextViewText(R.id.widget_detail, detail)
            val intent = Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            }
            val pending = PendingIntent.getActivity(
                context, 0, intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            views.setOnClickPendingIntent(R.id.widget_time, pending)
            return views
        }
    }
}
