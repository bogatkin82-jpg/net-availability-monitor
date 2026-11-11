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
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Виджет в стиле HONOR «погода+часы»: крупные тонкие часы, дата, город и погода.
 * Цвет времени — худший статус сети (зелёный/жёлтый/красный), остальное белое.
 * Обновляется foreground-сервисом; погода — Open-Meteo, город по IP.
 */
class StatusClockWidget : AppWidgetProvider() {

    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
    ) {
        WeatherRepository.refreshIfNeeded(context)
        renderAll(context, latestSnapshot)
    }

    companion object {
        @Volatile
        private var latestSnapshot: Snapshot? = null

        private val timeFormat = SimpleDateFormat("HH:mm", Locale.getDefault())
        private val dateFormat = SimpleDateFormat("d MMMM EEE", Locale("ru"))

        /** Вызывается сервисом после каждого раунда проверок. */
        fun updateAll(context: Context, snapshot: Snapshot) {
            latestSnapshot = snapshot
            WeatherRepository.refreshIfNeeded(context)
            renderAll(context, snapshot)
        }

        /** Перерисовать виджеты после обновления погоды. */
        fun refreshWidgets(context: Context) {
            renderAll(context, latestSnapshot)
        }

        private fun renderAll(context: Context, snapshot: Snapshot?) {
            val manager = AppWidgetManager.getInstance(context)
            val ids = manager.getAppWidgetIds(
                ComponentName(context, StatusClockWidget::class.java),
            )
            if (ids.isEmpty()) return
            val views = buildViews(context, snapshot)
            ids.forEach { id -> manager.updateAppWidget(id, views) }
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
                Color.WHITE
            }
        }

        private fun buildViews(context: Context, snapshot: Snapshot?): RemoteViews {
            val honorLayout = context.resources.getIdentifier(
                "widget_clock_honor", "layout", context.packageName,
            )
            val layoutId = if (honorLayout != 0) honorLayout else R.layout.widget_clock
            val views = RemoteViews(context.packageName, layoutId)
            val status = snapshot?.let { worstStatus(it) } ?: Status.UNKNOWN
            val color = statusColor(context, status)
            val now = Date()
            views.setTextViewText(R.id.widget_time, timeFormat.format(now))
            views.setTextColor(R.id.widget_time, color)
            val dateParts = dateFormat.format(now).split(" ")
            val weekday = dateParts.last().replaceFirstChar { it.uppercase() }
            views.setTextViewText(
                R.id.widget_date,
                (dateParts.dropLast(1) + weekday).joinToString(" "),
            )

            val weather = WeatherRepository.cached
            if (weather != null) {
                views.setTextViewText(R.id.widget_city, weather.city)
                views.setTextViewText(R.id.widget_temp, "${weather.temperatureC}°")
                views.setTextViewText(R.id.widget_condition, conditionLabel(weather.weatherCode))
                views.setImageViewResource(
                    R.id.widget_weather_icon,
                    weatherIcon(context, weather.weatherCode, weather.isDay),
                )
            } else {
                views.setTextViewText(R.id.widget_city, "Погода")
                views.setTextViewText(R.id.widget_temp, "--°")
                views.setTextViewText(R.id.widget_condition, "нет данных")
                views.setImageViewResource(R.id.widget_weather_icon, R.drawable.ic_weather_cloud)
            }

            val intent = Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            }
            val pending = PendingIntent.getActivity(
                context, 0, intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            views.setOnClickPendingIntent(R.id.widget_root, pending)

            // Клик по погоде открывает штатное приложение HONOR Погода.
            val weatherIntent = Intent().apply {
                component = ComponentName(
                    "com.hihonor.android.totemweather",
                    "com.hihonor.android.totemweather.WeatherHome",
                )
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED
            }
            val weatherPending = PendingIntent.getActivity(
                context, 1, weatherIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            listOf(
                R.id.widget_weather, R.id.widget_city, R.id.widget_temp,
                R.id.widget_condition, R.id.widget_weather_icon,
            ).forEach { id -> views.setOnClickPendingIntent(id, weatherPending) }
            return views
        }

        private fun weatherIcon(context: Context, code: Int, isDay: Boolean): Int {
            val honorName = when (code) {
                0 -> if (isDay) "honor_weather_sun" else "honor_weather_star"
                1, 2 -> "honor_weather_cloud"
                3 -> "honor_weather_cloud_dark"
                45, 48 -> "honor_weather_fog"
                51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82 ->
                    "honor_weather_rain"
                71, 73, 75, 77, 85, 86 -> "honor_weather_snow"
                95, 96, 99 -> "honor_weather_rain"
                else -> "honor_weather_cloud"
            }
            val honorId = context.resources.getIdentifier(
                honorName, "drawable", context.packageName,
            )
            if (honorId != 0) return honorId
            return when (code) {
                0 -> if (isDay) R.drawable.ic_weather_sun else R.drawable.ic_weather_moon
                1, 2, 3 -> R.drawable.ic_weather_cloud
                45, 48 -> R.drawable.ic_weather_fog
                51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82 ->
                    R.drawable.ic_weather_rain
                71, 73, 75, 77, 85, 86 -> R.drawable.ic_weather_snow
                95, 96, 99 -> R.drawable.ic_weather_storm
                else -> R.drawable.ic_weather_cloud
            }
        }

        private fun conditionLabel(code: Int): String = when (code) {
            0 -> "Ясно"
            1 -> "Малооблачно"
            2 -> "Переменная облачность"
            3 -> "Облачно"
            45, 48 -> "Туман"
            51, 53, 55, 56, 57 -> "Морось"
            61, 63, 65, 66, 67 -> "Дождь"
            71, 73, 75, 77 -> "Снег"
            80, 81, 82 -> "Ливень"
            85, 86 -> "Снегопад"
            95, 96, 99 -> "Гроза"
            else -> "Облачно"
        }
    }
}
