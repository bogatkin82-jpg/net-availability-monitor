package ru.netmon.android.ui

import androidx.compose.ui.graphics.Color
import ru.netmon.core.AppConfig
import ru.netmon.core.Status
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** #rrggbb / #rgb -> Color (как hexToRgba на дашборде). */
fun parseHexColor(hex: String?, fallback: Color): Color {
    val value = hex?.trim() ?: return fallback
    if (!value.startsWith("#")) return fallback
    val body = value.substring(1)
    val normalized = when (body.length) {
        3 -> buildString { body.forEach { append(it).append(it) } }
        6 -> body
        else -> return fallback
    }
    val int = normalized.toLongOrNull(16) ?: return fallback
    return Color(0xFF000000L or int)
}

/** Цвет статуса строго из config.json (status_colors). */
fun statusColor(config: AppConfig, status: Status): Color =
    parseHexColor(config.colorFor(status.id), TextMuted)

fun formatLatency(latencyMs: Int?): String =
    if (latencyMs == null || latencyMs <= 0) "—" else "$latencyMs мс"

fun formatUptime(value: Double): String = "%.1f%%".format(value)

fun formatDuration(seconds: Long): String {
    if (seconds <= 0) return "0 с"
    val hours = seconds / 3600
    val minutes = (seconds % 3600) / 60
    val secs = seconds % 60
    return when {
        hours > 0 -> "${hours} ч ${minutes} мин"
        minutes > 0 -> "${minutes} мин ${secs} с"
        else -> "${secs} с"
    }
}

private val clockFormatter: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm:ss")

fun formatClock(epochSec: Long): String =
    clockFormatter.format(Instant.ofEpochSecond(epochSec).atZone(ZoneId.systemDefault()))

/** ISO-строка от сервера/движка -> HH:mm:ss. */
fun formatIsoClock(iso: String?): String {
    if (iso.isNullOrBlank()) return "—"
    return try {
        clockFormatter.format(Instant.parse(iso).atZone(ZoneId.systemDefault()))
    } catch (_: Exception) {
        iso.take(19).replace('T', ' ')
    }
}

fun formatNextCheck(seconds: Double): String =
    if (seconds <= 0.0) "сейчас" else "${seconds.toInt()} с"
