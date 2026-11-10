package ru.netmon.android.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import ru.netmon.core.AppConfig
import ru.netmon.core.CheckResult
import ru.netmon.core.ServiceSnapshot
import ru.netmon.core.StageTimings
import ru.netmon.core.Status

/** Цветная плашка статуса — цвет берётся из config.json (status_colors). */
@Composable
fun StatusBadge(label: String, color: Color, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier
            .background(color.copy(alpha = 0.16f), RoundedCornerShape(8.dp))
            .padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .size(7.dp)
                .background(color, CircleShape),
        )
        Spacer(Modifier.width(6.dp))
        Text(
            text = label,
            color = color,
            style = androidx.compose.material3.MaterialTheme.typography.bodySmall,
            fontWeight = FontWeight.SemiBold,
        )
    }
}

@Composable
fun KpiTile(label: String, value: String, modifier: Modifier = Modifier, accent: Color = TextPrimary) {
    Column(
        modifier = modifier
            .background(CardSoft, RoundedCornerShape(12.dp))
            .padding(horizontal = 10.dp, vertical = 8.dp),
    ) {
        Text(text = label, color = TextMuted, style = androidx.compose.material3.MaterialTheme.typography.labelSmall)
        Spacer(Modifier.height(2.dp))
        Text(text = value, color = accent, style = androidx.compose.material3.MaterialTheme.typography.titleMedium)
    }
}

@Composable
fun SectionCard(
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    Card(
        modifier = modifier.fillMaxWidth(),
        shape = CardShape,
        colors = CardDefaults.cardColors(containerColor = Card),
        border = BorderStroke(1.dp, Border),
    ) {
        Column(modifier = Modifier.padding(14.dp)) { content() }
    }
}

/** Этап проверки: DNS/TCP/TLS/HTTP и его время. */
@Composable
fun StageChip(name: String, ms: Int?) {
    val color = if (ms == null) TextMuted else TextSecondary
    Row(
        modifier = Modifier
            .background(BgDeep, RoundedCornerShape(6.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(text = name, color = TextMuted, style = androidx.compose.material3.MaterialTheme.typography.labelSmall)
        Spacer(Modifier.width(4.dp))
        Text(
            text = if (ms == null) "—" else "$ms",
            color = color,
            style = androidx.compose.material3.MaterialTheme.typography.labelSmall,
        )
    }
}

@Composable
fun StageRow(timings: StageTimings) {
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        StageChip("DNS", timings.dnsMs)
        StageChip("TCP", timings.tcpMs)
        StageChip("TLS", timings.tlsMs)
        StageChip("HTTP", timings.httpMs)
    }
}

/** Одна проверка сервиса: статус, время этапов, ошибка. */
@Composable
fun CheckRow(check: CheckResult) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 5.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                modifier = Modifier
                    .size(7.dp)
                    .background(if (check.ok) AccentGreen else Color(0xFFFF3B30), CircleShape),
            )
            Spacer(Modifier.width(8.dp))
            Text(
                text = check.label,
                color = if (check.ok) TextSecondary else TextPrimary,
                style = androidx.compose.material3.MaterialTheme.typography.bodySmall,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            Spacer(Modifier.width(8.dp))
            Text(
                text = formatLatency(check.latencyMs),
                color = TextMuted,
                style = androidx.compose.material3.MaterialTheme.typography.labelSmall,
            )
        }
        Spacer(Modifier.height(4.dp))
        Row(
            modifier = Modifier.padding(start = 15.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            StageRow(check.timings)
        }
        val errorText = check.errorText
        if (!check.ok && errorText != null) {
            Text(
                text = "${check.errorLabel ?: "Ошибка"}: $errorText",
                color = Color(0xFFFFB3BB),
                style = androidx.compose.material3.MaterialTheme.typography.labelSmall,
                modifier = Modifier.padding(start = 15.dp, top = 2.dp),
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

/** Карточка сервиса: статус, причина, подсказка, отклик, uptime и список проверок. */
@Composable
fun ServiceCard(
    config: AppConfig,
    service: ServiceSnapshot,
    expanded: Boolean,
    onToggleExpanded: () -> Unit,
) {
    val color = statusColor(config, service.status)
    val accent = parseHexColor(service.accent, AccentBlue)
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onToggleExpanded() },
        shape = CardShape,
        colors = CardDefaults.cardColors(containerColor = Card),
        border = BorderStroke(1.dp, Border.copy(alpha = 0.9f)),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(10.dp)
                        .background(accent, CircleShape),
                )
                Spacer(Modifier.width(8.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = service.name,
                        color = TextPrimary,
                        style = androidx.compose.material3.MaterialTheme.typography.titleMedium,
                    )
                    Text(
                        text = service.subtitle,
                        color = TextMuted,
                        style = androidx.compose.material3.MaterialTheme.typography.bodySmall,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                StatusBadge(label = service.statusLabel, color = color)
            }

            Spacer(Modifier.height(10.dp))
            Text(
                text = service.statusReason,
                color = TextSecondary,
                style = androidx.compose.material3.MaterialTheme.typography.bodyMedium,
            )
            val hint = service.hint
            if (!hint.isNullOrBlank()) {
                Spacer(Modifier.height(6.dp))
                Text(
                    text = hint,
                    color = if (service.status == Status.OK) TextMuted else color,
                    style = androidx.compose.material3.MaterialTheme.typography.bodySmall,
                )
            }

            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                KpiTile(
                    label = "Отклик",
                    value = formatLatency(service.latencyMs),
                    modifier = Modifier.weight(1f),
                )
                KpiTile(
                    label = "Uptime 1ч",
                    value = formatUptime(service.uptime1h),
                    modifier = Modifier.weight(1f),
                )
                KpiTile(
                    label = "Uptime 24ч",
                    value = formatUptime(service.uptime24h),
                    modifier = Modifier.weight(1f),
                )
            }

            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = "Проверки ${service.checksOk}/${service.checksTotal}",
                    color = TextMuted,
                    style = androidx.compose.material3.MaterialTheme.typography.labelSmall,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    text = if (expanded) "свернуть" else "показать",
                    color = AccentBlue,
                    style = androidx.compose.material3.MaterialTheme.typography.labelSmall,
                )
            }

            if (expanded) {
                Spacer(Modifier.height(6.dp))
                service.checks.forEach { check ->
                    CheckRow(check)
                }
                if (service.checks.isEmpty()) {
                    Text(
                        text = "Проверки ещё не выполнялись",
                        color = TextMuted,
                        style = androidx.compose.material3.MaterialTheme.typography.bodySmall,
                    )
                }
            }
        }
    }
}

/** Небольшая строка «метка: значение» для настроек/журнала. */
@Composable
fun InfoRow(label: String, value: String, modifier: Modifier = Modifier) {
    Row(modifier = modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(
            text = label,
            color = TextMuted,
            style = androidx.compose.material3.MaterialTheme.typography.bodySmall,
            modifier = Modifier.weight(1f),
        )
        Text(
            text = value,
            color = TextPrimary,
            style = androidx.compose.material3.MaterialTheme.typography.bodySmall,
        )
    }
}
