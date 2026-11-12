package ru.netmon.android.widget

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import ru.netmon.android.NetMonApp

/** Обновляет время на виджете каждую минуту, даже если сервис остановлен. */
class WidgetTickReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val app = context.applicationContext as? NetMonApp ?: return
        StatusClockWidget.updateAll(context, app.container.controller.snapshot.value)
    }
}
