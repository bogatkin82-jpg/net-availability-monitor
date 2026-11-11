package ru.netmon.android.widget

import android.content.Context
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URLEncoder
import java.net.URL
import java.util.concurrent.Executors
import kotlin.math.roundToInt

/** Погода для виджета: город/координаты по IP, текущие данные Open-Meteo. */
data class WeatherInfo(
    val city: String,
    val temperatureC: Int,
    val weatherCode: Int,
    val isDay: Boolean,
)

object WeatherRepository {

    @Volatile
    var cached: WeatherInfo? = null
        private set

    @Volatile
    private var fetchedAtMs: Long = 0L

    private val executor = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "netmon-weather").apply { isDaemon = true }
    }

    fun refreshIfNeeded(context: Context, force: Boolean = false) {
        val now = System.currentTimeMillis()
        if (!force && cached != null && now - fetchedAtMs < CACHE_TTL_MS) return
        val appContext = context.applicationContext
        executor.execute {
            try {
                val geo = fetchGeo(appContext)
                val weather = fetchWeather(geo.first, geo.second)
                cached = WeatherInfo(
                    city = geo.third,
                    temperatureC = weather.first,
                    weatherCode = weather.second,
                    isDay = weather.third,
                )
                fetchedAtMs = System.currentTimeMillis()
                StatusClockWidget.refreshWidgets(appContext)
            } catch (exc: Exception) {
                // Сеть/гео-сервис недоступны — оставляем последнюю погоду.
                StatusClockWidget.refreshWidgets(appContext)
            }
        }
    }

    /** @return latitude, longitude, city */
    private fun fetchGeo(context: Context): Triple<Double, Double, String> {
        val manual = WidgetCityPrefs.get(context)
        if (manual.isNotBlank()) {
            val point = geocode(manual)
            if (point != null) return point
        }
        val primary = runCatching { fetchJson("https://ipapi.co/json/") }.getOrNull()
        if (primary != null && primary.optBoolean("error", false).not()) {
            val lat = primary.optDouble("latitude", Double.NaN)
            val lon = primary.optDouble("longitude", Double.NaN)
            if (!lat.isNaN() && !lon.isNaN()) {
                return Triple(lat, lon, primary.optString("city", "").ifBlank { "—" })
            }
        }
        val fallback = fetchJson("https://ipwho.is/")
        val lat = fallback.optDouble("latitude", Double.NaN)
        val lon = fallback.optDouble("longitude", Double.NaN)
        require(!lat.isNaN() && !lon.isNaN()) { "no coordinates" }
        return Triple(lat, lon, fallback.optString("city", "").ifBlank { "—" })
    }

    /** Геокодинг ручного города через Open-Meteo. */
    private fun geocode(city: String): Triple<Double, Double, String>? {
        val query = URLEncoder.encode(city, "UTF-8")
        val url = "https://geocoding-api.open-meteo.com/v1/search" +
            "?name=$query&count=1&language=ru&format=json"
        val results = runCatching { fetchJson(url).optJSONArray("results") }.getOrNull()
        if (results == null || results.length() == 0) return null
        val first = results.getJSONObject(0)
        val lat = first.optDouble("latitude", Double.NaN)
        val lon = first.optDouble("longitude", Double.NaN)
        if (lat.isNaN() || lon.isNaN()) return null
        val name = first.optString("name", city).ifBlank { city }
        return Triple(lat, lon, name)
    }

    /** @return temperature C, weather code, isDay */
    private fun fetchWeather(lat: Double, lon: Double): Triple<Int, Int, Boolean> {
        val url = "https://api.open-meteo.com/v1/forecast" +
            "?latitude=$lat&longitude=$lon" +
            "&current=temperature_2m,weather_code,is_day&timezone=auto"
        val current = fetchJson(url).getJSONObject("current")
        return Triple(
            current.getDouble("temperature_2m").roundToInt(),
            current.optInt("weather_code", 0),
            current.optInt("is_day", 1) == 1,
        )
    }

    private fun fetchJson(url: String): JSONObject {
        val connection = (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = 5_000
            readTimeout = 5_000
            requestMethod = "GET"
            setRequestProperty("User-Agent", "netmon-android-widget/1.0")
        }
        try {
            val text = connection.inputStream.bufferedReader().use { it.readText() }
            return JSONObject(text)
        } finally {
            connection.disconnect()
        }
    }

    private const val CACHE_TTL_MS = 30 * 60 * 1000L
}
