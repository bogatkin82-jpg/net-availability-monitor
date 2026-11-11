package ru.netmon.android.widget

import android.content.Context

/** Город для виджета-погоды (ручной выбор; пусто — определять по IP). */
object WidgetCityPrefs {
    private const val PREFS = "netmon_widget"
    private const val KEY_CITY = "weather_city"
    private const val KEY_GEO_REFRESH = "weather_geo_refresh_min"
    private const val KEY_GEO_LAT = "weather_geo_lat"
    private const val KEY_GEO_LON = "weather_geo_lon"
    private const val KEY_GEO_CITY = "weather_geo_city"
    private const val KEY_GEO_AT = "weather_geo_at"

    fun get(context: Context): String =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY_CITY, "")?.trim().orEmpty()

    fun set(context: Context, city: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_CITY, city.trim()).apply()
    }

    /**
     * Как часто обновлять город по GPS, в минутах.
     * -1 — не обновлять автоматически (только ручной город/кэш).
     */
    fun geoRefreshMinutes(context: Context): Int {
        val stored = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getInt(KEY_GEO_REFRESH, 120)
        return if (stored in setOf(120, 240, 480, -1)) stored else 120
    }

    fun setGeoRefreshMinutes(context: Context, minutes: Int) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putInt(KEY_GEO_REFRESH, minutes).apply()
    }

    data class CachedGeo(
        val latitude: Double,
        val longitude: Double,
        val city: String,
        val fetchedAtMs: Long,
    )

    fun cachedGeo(context: Context): CachedGeo? {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val city = prefs.getString(KEY_GEO_CITY, "")?.trim().orEmpty()
        if (city.isBlank()) return null
        val lat = prefs.getString(KEY_GEO_LAT, null)?.toDoubleOrNull() ?: return null
        val lon = prefs.getString(KEY_GEO_LON, null)?.toDoubleOrNull() ?: return null
        return CachedGeo(lat, lon, city, prefs.getLong(KEY_GEO_AT, 0L))
    }

    fun saveCachedGeo(context: Context, latitude: Double, longitude: Double, city: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_GEO_LAT, latitude.toString())
            .putString(KEY_GEO_LON, longitude.toString())
            .putString(KEY_GEO_CITY, city.trim())
            .putLong(KEY_GEO_AT, System.currentTimeMillis())
            .apply()
    }
}
