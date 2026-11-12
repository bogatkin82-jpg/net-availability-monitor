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
                R.drawable.ic_snow_frame_7,
                R.drawable.ic_snow_frame_8,
                R.drawable.ic_snow_frame_9,
                R.drawable.ic_snow_frame_10,
                R.drawable.ic_snow_frame_11,
                R.drawable.ic_snow_frame_12,
                R.drawable.ic_snow_frame_13,
                R.drawable.ic_snow_frame_14,
                R.drawable.ic_snow_frame_15,
                R.drawable.ic_snow_frame_16,
                R.drawable.ic_snow_frame_17,
                R.drawable.ic_snow_frame_18,
                R.drawable.ic_snow_frame_19,
                R.drawable.ic_snow_frame_20,
                R.drawable.ic_snow_frame_21,
                R.drawable.ic_snow_frame_22,
                R.drawable.ic_snow_frame_23,
                R.drawable.ic_snow_frame_24,
            ),
            375L,
        ),
        RAIN(
            intArrayOf(
                R.drawable.ic_rain_frame_1,
                R.drawable.ic_rain_frame_2,
                R.drawable.ic_rain_frame_3,
                R.drawable.ic_rain_frame_4,
                R.drawable.ic_rain_frame_5,
                R.drawable.ic_rain_frame_6,
                R.drawable.ic_rain_frame_7,
                R.drawable.ic_rain_frame_8,
                R.drawable.ic_rain_frame_9,
                R.drawable.ic_rain_frame_10,
                R.drawable.ic_rain_frame_11,
                R.drawable.ic_rain_frame_12,
                R.drawable.ic_rain_frame_13,
                R.drawable.ic_rain_frame_14,
                R.drawable.ic_rain_frame_15,
                R.drawable.ic_rain_frame_16,
            ),
            225L,
        ),
        DRIZZLE(
            intArrayOf(
                R.drawable.ic_drizzle_frame_1,
                R.drawable.ic_drizzle_frame_2,
                R.drawable.ic_drizzle_frame_3,
                R.drawable.ic_drizzle_frame_4,
                R.drawable.ic_drizzle_frame_5,
                R.drawable.ic_drizzle_frame_6,
                R.drawable.ic_drizzle_frame_7,
                R.drawable.ic_drizzle_frame_8,
                R.drawable.ic_drizzle_frame_9,
                R.drawable.ic_drizzle_frame_10,
                R.drawable.ic_drizzle_frame_11,
                R.drawable.ic_drizzle_frame_12,
                R.drawable.ic_drizzle_frame_13,
                R.drawable.ic_drizzle_frame_14,
                R.drawable.ic_drizzle_frame_15,
                R.drawable.ic_drizzle_frame_16,
            ),
            262L,
        ),
        STORM(
            intArrayOf(
                R.drawable.ic_storm_frame_1,
                R.drawable.ic_storm_frame_2,
                R.drawable.ic_storm_frame_3,
                R.drawable.ic_storm_frame_4,
                R.drawable.ic_storm_frame_5,
                R.drawable.ic_storm_frame_6,
                R.drawable.ic_storm_frame_7,
                R.drawable.ic_storm_frame_8,
                R.drawable.ic_storm_frame_9,
                R.drawable.ic_storm_frame_10,
                R.drawable.ic_storm_frame_11,
                R.drawable.ic_storm_frame_12,
                R.drawable.ic_storm_frame_13,
                R.drawable.ic_storm_frame_14,
                R.drawable.ic_storm_frame_15,
                R.drawable.ic_storm_frame_16,
            ),
            195L,
        ),
        FOG(
            intArrayOf(
                R.drawable.ic_fog_frame_1,
                R.drawable.ic_fog_frame_2,
                R.drawable.ic_fog_frame_3,
                R.drawable.ic_fog_frame_4,
                R.drawable.ic_fog_frame_5,
                R.drawable.ic_fog_frame_6,
                R.drawable.ic_fog_frame_7,
                R.drawable.ic_fog_frame_8,
                R.drawable.ic_fog_frame_9,
                R.drawable.ic_fog_frame_10,
                R.drawable.ic_fog_frame_11,
                R.drawable.ic_fog_frame_12,
            ),
            487L,
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
