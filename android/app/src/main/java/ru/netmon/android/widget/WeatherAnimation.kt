package ru.netmon.android.widget

import ru.netmon.android.R

/** Кадры анимации осадков и скорость их смены. */
object WeatherAnimation {

    enum class Kind(val frames: IntArray, val delayMs: Long) {
        SNOW(
            intArrayOf(
                R.drawable.ic_snow_frame_1,
                R.drawable.ic_snow_frame_2,
                R.drawable.ic_snow_frame_3,
                R.drawable.ic_snow_frame_4,
                R.drawable.ic_snow_frame_5,
                R.drawable.ic_snow_frame_6,
            ),
            500L,
        ),
        RAIN(
            intArrayOf(
                R.drawable.ic_rain_frame_1,
                R.drawable.ic_rain_frame_2,
                R.drawable.ic_rain_frame_3,
                R.drawable.ic_rain_frame_4,
            ),
            300L,
        ),
        DRIZZLE(
            intArrayOf(
                R.drawable.ic_drizzle_frame_1,
                R.drawable.ic_drizzle_frame_2,
                R.drawable.ic_drizzle_frame_3,
                R.drawable.ic_drizzle_frame_4,
            ),
            350L,
        ),
        STORM(
            intArrayOf(
                R.drawable.ic_storm_frame_1,
                R.drawable.ic_storm_frame_2,
                R.drawable.ic_storm_frame_3,
                R.drawable.ic_storm_frame_4,
            ),
            260L,
        ),
        FOG(
            intArrayOf(
                R.drawable.ic_fog_frame_1,
                R.drawable.ic_fog_frame_2,
                R.drawable.ic_fog_frame_3,
            ),
            650L,
        ),
    }

    fun kindFor(weatherCode: Int): Kind? = when (weatherCode) {
        45, 48 -> Kind.FOG
        51, 53, 55, 56, 57 -> Kind.DRIZZLE
        61, 63, 65, 66, 67, 80, 81, 82 -> Kind.RAIN
        71, 73, 75, 77, 85, 86 -> Kind.SNOW
        95, 96, 99 -> Kind.STORM
        else -> null
    }

    fun drawable(kind: Kind, frame: Int): Int =
        kind.frames[frame % kind.frames.size]
}
