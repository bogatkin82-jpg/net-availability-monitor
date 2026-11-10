package ru.netmon.android.ui

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import ru.netmon.android.monitor.MonitorController
import ru.netmon.core.AppConfig
import ru.netmon.core.Incident
import ru.netmon.core.IncidentStatus
import ru.netmon.core.Status

/** Экран «Журнал»: история инцидентов с поиском и фильтрами. */
@Composable
fun JournalScreen(
    config: AppConfig,
    controller: MonitorController,
    snapshotVersion: Int,
) {
    var query by rememberSaveable { mutableStateOf("") }
    var serviceFilter by rememberSaveable { mutableStateOf("all") }
    var statusFilter by rememberSaveable { mutableStateOf("all") }
    var hoursFilter by rememberSaveable { mutableIntStateOf(0) }

    val report = remember(query, serviceFilter, statusFilter, hoursFilter, snapshotVersion) {
        controller.incidentsReport(query, serviceFilter, statusFilter, hoursFilter)
    }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 14.dp),
    ) {
        Text(
            text = "Журнал сбоев",
            color = TextPrimary,
            style = MaterialTheme.typography.titleLarge,
        )
        Spacer(Modifier.height(2.dp))
        Text(
            text = "Инциденты открываются после ${config.incidents.openAfterFailures} неудач подряд " +
                "и закрываются после ${config.incidents.closeAfterSuccesses} успехов",
            color = TextMuted,
            style = MaterialTheme.typography.bodySmall,
        )

        Spacer(Modifier.height(10.dp))
        OutlinedTextField(
            value = query,
            onValueChange = { query = it },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            label = { Text("Поиск по сервису, заголовку, причине") },
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = AccentBlue,
                unfocusedBorderColor = Border,
                focusedLabelColor = AccentBlue,
                unfocusedLabelColor = TextMuted,
                cursorColor = AccentBlue,
            ),
        )

        Spacer(Modifier.height(8.dp))
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            FilterChip(
                selected = serviceFilter == "all",
                onClick = { serviceFilter = "all" },
                label = { Text("Все сервисы") },
                colors = journalChipColors(),
            )
            config.services.forEach { service ->
                FilterChip(
                    selected = serviceFilter == service.id,
                    onClick = { serviceFilter = service.id },
                    label = { Text(service.name) },
                    colors = journalChipColors(),
                )
            }
        }

        Spacer(Modifier.height(6.dp))
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            FilterChip(
                selected = statusFilter == "all",
                onClick = { statusFilter = "all" },
                label = { Text("Любой статус") },
                colors = journalChipColors(),
            )
            FilterChip(
                selected = statusFilter == "active",
                onClick = { statusFilter = "active" },
                label = { Text("Активные") },
                colors = journalChipColors(),
            )
            FilterChip(
                selected = statusFilter == "resolved",
                onClick = { statusFilter = "resolved" },
                label = { Text("Закрытые") },
                colors = journalChipColors(),
            )
        }

        Spacer(Modifier.height(6.dp))
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            listOf(0 to "Всё время", 1 to "1 час", 24 to "24 часа").forEach { (hours, label) ->
                FilterChip(
                    selected = hoursFilter == hours,
                    onClick = { hoursFilter = hours },
                    label = { Text(label) },
                    colors = journalChipColors(),
                )
            }
        }

        Spacer(Modifier.height(10.dp))
        Text(
            text = "Найдено: ${report.total}",
            color = TextMuted,
            style = MaterialTheme.typography.labelSmall,
        )
        Spacer(Modifier.height(6.dp))

        LazyColumn(
            modifier = Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(report.incidents, key = { it.id }) { incident ->
                IncidentCard(config = config, incident = incident)
            }
            if (report.incidents.isEmpty() && report.total == 0) {
                item {
                    SectionCard {
                        Text(
                            text = "Инцидентов нет — по фильтрам ничего не найдено.",
                            color = TextSecondary,
                            style = MaterialTheme.typography.bodyMedium,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun journalChipColors() = FilterChipDefaults.filterChipColors(
    containerColor = CardSoft,
    labelColor = TextSecondary,
    selectedContainerColor = AccentBlue.copy(alpha = 0.22f),
    selectedLabelColor = AccentBlue,
)

@Composable
private fun IncidentCard(config: AppConfig, incident: Incident) {
    val serviceStatus = Status.fromId(incident.serviceStatus)
    val accent = if (incident.status == IncidentStatus.ACTIVE) {
        statusColor(config, serviceStatus)
    } else {
        AccentGreen
    }
    SectionCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = incident.title,
                color = TextPrimary,
                style = MaterialTheme.typography.titleMedium,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            StatusBadge(
                label = if (incident.status == IncidentStatus.ACTIVE) "активен" else "закрыт",
                color = accent,
            )
        }
        Spacer(Modifier.height(6.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = incident.serviceName,
                color = TextMuted,
                style = MaterialTheme.typography.labelSmall,
            )
            Spacer(Modifier.weight(1f))
            Text(
                text = "${formatIsoClock(incident.startedAt)} · ${formatDuration(incident.durationSec)}",
                color = TextMuted,
                style = MaterialTheme.typography.labelSmall,
            )
        }
        Spacer(Modifier.height(6.dp))
        Text(
            text = incident.reason,
            color = TextSecondary,
            style = MaterialTheme.typography.bodySmall,
        )
        Spacer(Modifier.height(6.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = "Сбойных проверок: ${incident.failedChecks}/${incident.totalChecks}",
                color = TextMuted,
                style = MaterialTheme.typography.labelSmall,
            )
            if (incident.status == IncidentStatus.RESOLVED) {
                Spacer(Modifier.weight(1f))
                Text(
                    text = "закрыт ${formatIsoClock(incident.endedAt)}",
                    color = TextMuted,
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Normal,
                )
            }
        }
    }
}
