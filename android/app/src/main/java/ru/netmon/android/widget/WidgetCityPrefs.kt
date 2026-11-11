package ru.netmon.android.widget

import android.content.Context

/** Город для виджета-погоды (ручной выбор; пусто — определять по IP). */
object WidgetCityPrefs {
    private const val PREFS = "netmon_widget"
    private const val KEY_CITY = "weather_city"

    fun get(context: Context): String =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY_CITY, "")?.trim().orEmpty()

    fun set(context: Context, city: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_CITY, city.trim()).apply()
    }
}
