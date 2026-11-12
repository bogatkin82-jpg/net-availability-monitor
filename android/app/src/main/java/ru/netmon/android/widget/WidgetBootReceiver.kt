package ru.netmon.android.widget

import android.appwidget.AppWidgetManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.ComponentName

/** После перезагрузки возвращает минутный тик виджета. */
class WidgetBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val manager = AppWidgetManager.getInstance(context)
        val ids = manager.getAppWidgetIds(ComponentName(context, StatusClockWidget::class.java))
        if (ids.isNotEmpty()) WidgetTickScheduler.schedule(context)
    }
}
