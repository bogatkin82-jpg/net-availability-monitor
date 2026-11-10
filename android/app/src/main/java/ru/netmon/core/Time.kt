package ru.netmon.core

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit

/** Время в форматах, совместимых с server.py (ISO-8601 с секундной точностью). */
object IsoTime {
    private val formatter: DateTimeFormatter = DateTimeFormatter.ISO_OFFSET_DATE_TIME

    fun ofEpochMillis(epochMs: Long): String = Instant.ofEpochMilli(epochMs)
        .atZone(ZoneId.systemDefault())
        .truncatedTo(ChronoUnit.SECONDS)
        .format(formatter)

    fun now(): String = ofEpochMillis(System.currentTimeMillis())
}
