package ru.netmon.android.widget

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Geocoder
import android.location.Location
import android.location.LocationManager
import android.os.Build
import androidx.core.content.ContextCompat
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URLEncoder
import java.net.URL
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
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
        // Как в оригинале: сначала координаты телефона.
        locationGeo(context)?.let { return it }
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

    /** Последняя известная геопозиция + обратное геокодирование в город. */
    private fun locationGeo(context: Context): Triple<Double, Double, String>? {
        val fine = ContextCompat.checkSelfPermission(
            context, Manifest.permission.ACCESS_FINE_LOCATION,
        ) == PackageManager.PERMISSION_GRANTED
        val coarse = ContextCompat.checkSelfPermission(
            context, Manifest.permission.ACCESS_COARSE_LOCATION,
        ) == PackageManager.PERMISSION_GRANTED
        if (!fine && !coarse) return null
        val manager = context.getSystemService(Context.LOCATION_SERVICE) as? LocationManager ?: return null
        val location = currentLocation(context, manager) ?: return null
        val reverse = reverseGeocode(context, location.latitude, location.longitude)
        val manual = WidgetCityPrefs.get(context)
        // OSM в сельской местности часто отдаёт «городской округ» вместо посёлка —
        // сохраняем координаты GPS, но показываем сохранённое имя, если оно есть.
        val city = if (
            manual.isNotBlank() && (
                reverse.contains("округ", ignoreCase = true) ||
                    reverse.contains("район", ignoreCase = true) ||
                    reverse.contains("область", ignoreCase = true)
                )
        ) manual else reverse
        return Triple(location.latitude, location.longitude, city)
    }

    /** Свежая геопозиция; при неудаче — последняя известная. */
    private fun currentLocation(context: Context, manager: LocationManager): Location? {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            for (provider in listOf(
                LocationManager.NETWORK_PROVIDER,
                LocationManager.GPS_PROVIDER,
            )) {
                try {
                    if (!manager.isProviderEnabled(provider)) continue
                    val latch = CountDownLatch(1)
                    var result: Location? = null
                    manager.getCurrentLocation(
                        provider,
                        null,
                        context.mainExecutor,
                    ) { location ->
                        result = location
                        latch.countDown()
                    }
                    if (latch.await(4, TimeUnit.SECONDS) && result != null) {
                        return result
                    }
                } catch (exc: SecurityException) {
                    // нет разрешения на провайдер
                } catch (exc: IllegalArgumentException) {
                    // провайдер выключен
                }
            }
        }
        var best: Location? = null
        for (provider in listOf(
            LocationManager.GPS_PROVIDER,
            LocationManager.NETWORK_PROVIDER,
            LocationManager.PASSIVE_PROVIDER,
        )) {
            try {
                val location = manager.getLastKnownLocation(provider) ?: continue
                if (best == null || location.time > best!!.time) best = location
            } catch (exc: SecurityException) {
                // нет разрешения
            } catch (exc: IllegalArgumentException) {
                // провайдер выключен
            }
        }
        return best
    }

    private fun reverseGeocode(context: Context, lat: Double, lon: Double): String {
        // Сначала населённый пункт (city/locality), а не административный район.
        runCatching {
            val json = fetchJson(
                "https://api.bigdatacloud.net/data/reverse-geocode-client" +
                    "?latitude=$lat&longitude=$lon&localityLanguage=ru",
            )
            json.optString("city").ifBlank { json.optString("locality") }
        }.getOrNull()?.takeIf { it.isNotBlank() }?.let { return it }
        try {
            val address = Geocoder(context, Locale("ru"))
                .getFromLocation(lat, lon, 1)
                ?.firstOrNull()
            val city = address?.locality ?: address?.subLocality
            if (!city.isNullOrBlank()) return city
        } catch (exc: Exception) {
            // Geocoder недоступен
        }
        runCatching {
            val json = fetchJson(
                "https://nominatim.openstreetmap.org/reverse?format=jsonv2" +
                    "&lat=$lat&lon=$lon&accept-language=ru",
            )
            val address = json.optJSONObject("address")
            if (address != null) {
                val city = address.optString("city").ifBlank {
                    address.optString("town").ifBlank {
                        address.optString("village").ifBlank {
                            address.optString("suburb").ifBlank {
                                address.optString("municipality")
                            }
                        }
                    }
                }
                if (city.isNotBlank()) return city
            }
        }
        return "Рядом с вами"
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
