package ru.netmon.android.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import ru.netmon.core.AppConfig
import ru.netmon.core.Snapshot
import ru.netmon.core.Status

/** Экран «Статус»: сводка + карточки сервисов с проверками. */
@Composable
fun StatusScreen(
    config: AppConfig,
    snapshot: Snapshot,
    nextCheckIn: Double,
    onCheckNow: () -> Unit,
) {
    var collapsed by remember { mutableStateOf(emptySet<String>()) }
    val overallColor = statusColor(config, snapshot.summary.overall)

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 14.dp),
    ) {
        Text(
            text = "Мониторинг сети",
            color = TextPrimary,
            style = MaterialTheme.typography.titleLarge,
        )
        Spacer(Modifier.height(2.dp))
        Text(
            text = "Проверки каждые ${snapshot.pollInterval} с · экран обновляется каждые 3 с",
            color = TextMuted,
            style = MaterialTheme.typography.bodySmall,
        )

        Spacer(Modifier.height(12.dp))
        SectionCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                StatusBadge(label = snapshot.summary.overallLabel, color = overallColor)
                Spacer(Modifier.width(8.dp))
                Spacer(Modifier.weight(1f))
                Text(
                    text = if (snapshot.checking) "идёт проверка…" else "через ${formatNextCheck(nextCheckIn)}",
                    color = TextMuted,
                    style = MaterialTheme.typography.labelSmall,
                )
            }
            Spacer(Modifier.height(10.dp))
            Text(
                text = "Интернет: ${snapshot.internet.label}",
                color = TextSecondary,
                style = MaterialTheme.typography.bodyMedium,
            )
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                KpiTile(
                    label = "Сервисы",
                    value = "${snapshot.summary.servicesOk}/${snapshot.summary.servicesTotal}",
                    modifier = Modifier.weight(1f),
                    accent = overallColor,
                )
                KpiTile(
                    label = "С проблемами",
                    value = "${snapshot.summary.servicesAffected}",
                    modifier = Modifier.weight(1f),
                )
                KpiTile(
                    label = "Инциденты",
                    value = "${snapshot.summary.activeIncidents}",
                    modifier = Modifier.weight(1f),
                )
            }
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                KpiTile(
                    label = "Ср. отклик",
                    value = formatLatency(snapshot.summary.avgLatencyMs),
                    modifier = Modifier.weight(1f),
                )
                KpiTile(
                    label = "Проверки",
                    value = "${snapshot.summary.checksOk}/${snapshot.summary.checksTotal}",
                    modifier = Modifier.weight(1f),
                )
                KpiTile(
                    label = "Обновлено",
                    value = formatIsoClock(snapshot.generatedAt),
                    modifier = Modifier.weight(1f),
                )
            }
            Spacer(Modifier.height(12.dp))
            Button(
                onClick = onCheckNow,
                enabled = !snapshot.checking,
                modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.buttonColors(
                    containerColor = AccentBlue,
                    contentColor = androidx.compose.ui.graphics.Color.White,
                ),
            ) {
                if (snapshot.checking) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(16.dp),
                        strokeWidth = 2.dp,
                        color = androidx.compose.ui.graphics.Color.White,
                    )
                    Spacer(Modifier.width(8.dp))
                }
                Text(
                    text = if (snapshot.checking) "Проверяю…" else "Проверить сейчас",
                    fontWeight = FontWeight.SemiBold,
                )
            }
        }

        Spacer(Modifier.height(12.dp))
        snapshot.services.forEach { service ->
            val expanded = service.id !in collapsed
            ServiceCard(
                config = config,
                service = service,
                expanded = expanded,
                onToggleExpanded = {
                    collapsed = if (expanded) collapsed + service.id else collapsed - service.id
                },
            )
            val incident = service.activeIncident
            if (incident != null) {
                Spacer(Modifier.height(6.dp))
                Text(
                    text = "Инцидент активен с ${formatIsoClock(incident.startedAt)} · " +
                        formatDuration(incident.durationSec),
                    color = statusColor(config, Status.fromId(incident.serviceStatus)),
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(start = 4.dp),
                )
            }
            Spacer(Modifier.height(12.dp))
        }

        if (snapshot.services.isEmpty()) {
            SectionCard {
                Text(
                    text = "Все сервисы выключены в настройках.",
                    color = TextSecondary,
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }

        Spacer(Modifier.height(6.dp))
        Text(
            text = "Сервисы, эндпоинты, таймауты, пороги и цвета взяты из config.json",
            color = TextMuted,
            style = MaterialTheme.typography.labelSmall,
        )
        Spacer(Modifier.height(16.dp))
    }
}
