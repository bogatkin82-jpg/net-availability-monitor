package ru.netmon.android.service

import android.content.Context

/** Тумблер «Скрывать значок в строке состояния». */
object StatusIconPrefs {
    private const val PREFS = "netmon_widget"
    private const val KEY_HIDDEN = "hide_status_icon"

    fun isHidden(context: Context): Boolean =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getBoolean(KEY_HIDDEN, false)

    fun set(context: Context, hidden: Boolean) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putBoolean(KEY_HIDDEN, hidden).apply()
    }
}
