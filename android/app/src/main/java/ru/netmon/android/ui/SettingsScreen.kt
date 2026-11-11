package ru.netmon.android.ui

import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.os.Build
import android.widget.Toast

import android.content.Intent
import android.provider.Settings

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.MaterialTheme
import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import ru.netmon.core.AppConfig
import ru.netmon.core.Status
import androidx.core.content.ContextCompat
import ru.netmon.android.widget.StatusClockWidget
import ru.netmon.android.widget.WeatherRepository
import ru.netmon.android.widget.WidgetCityPrefs
import ru.netmon.android.service.StatusIconPrefs
import ru.netmon.core.UserSettings

/** Экран «Настройки»: значения по умолчанию — из config.json, правки — в SharedPreferences. */
@Composable
fun SettingsScreen(
    config: AppConfig,
    settings: UserSettings,
    serviceRunning: Boolean,
    onSave: (UserSettings) -> Unit,
    onStartService: () -> Unit,
    onStopService: () -> Unit,
    onResetHistory: () -> Unit,
) {
    val context = LocalContext.current

    var draft by remember(settings) { mutableStateOf(settings) }
    var showResetDialog by remember { mutableStateOf(false) }
    var saved by remember { mutableStateOf(false) }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 14.dp),
    ) {
        Text(
            text = "Настройки",
            color = TextPrimary,
            style = MaterialTheme.typography.titleLarge,
        )
        Spacer(Modifier.height(2.dp))
        Text(
            text = "Значения по умолчанию совпадают с config.json; ваши правки сохраняются " +
                "в приложении и перекрывают его.",
            color = TextMuted,
            style = MaterialTheme.typography.bodySmall,
        )

        Spacer(Modifier.height(12.dp))
        SectionCard {
            Text("Опрос", color = TextPrimary, style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(4.dp))
            Text(
                text = "в config.json: интервал ${config.polling.intervalSec} с, " +
                    "таймаут ${config.polling.checkTimeoutSec} с, DNS ${config.polling.dnsTimeoutSec} с",
                color = TextMuted,
                style = MaterialTheme.typography.labelSmall,
            )
            Spacer(Modifier.height(6.dp))
            StepperRow(
                label = "Интервал опроса",
                value = draft.intervalSec,
                unit = "с",
                range = UserSettings.MIN_INTERVAL_SEC..300,
                step = 5,
            ) { draft = draft.copy(intervalSec = it) }
            StepperRow(
                label = "Таймаут проверки",
                value = draft.checkTimeoutSec,
                unit = "с",
                range = 2..30,
                step = 1,
            ) { draft = draft.copy(checkTimeoutSec = it) }
            StepperRow(
                label = "DNS-таймаут",
                value = draft.dnsTimeoutSec,
                unit = "с",
                range = 1..10,
                step = 1,
            ) { draft = draft.copy(dnsTimeoutSec = it) }
        }

        Spacer(Modifier.height(10.dp))
        SectionCard {
            Text("Инциденты", color = TextPrimary, style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(4.dp))
            Text(
                text = "в config.json: открыть после ${config.incidents.openAfterFailures}, " +
                    "закрыть после ${config.incidents.closeAfterSuccesses}, хранить ${config.incidents.maxStored}",
                color = TextMuted,
                style = MaterialTheme.typography.labelSmall,
            )
            Spacer(Modifier.height(6.dp))
            StepperRow(
                label = "Открывать после",
                value = draft.openAfterFailures,
                unit = "неудач",
                range = 1..10,
                step = 1,
            ) { draft = draft.copy(openAfterFailures = it) }
            StepperRow(
                label = "Закрывать после",
                value = draft.closeAfterSuccesses,
                unit = "успехов",
                range = 1..5,
                step = 1,
            ) { draft = draft.copy(closeAfterSuccesses = it) }
            StepperRow(
                label = "Хранить инцидентов",
                value = draft.maxStored,
                unit = "шт",
                range = 50..5000,
                step = 50,
            ) { draft = draft.copy(maxStored = it) }
        }

        Spacer(Modifier.height(10.dp))
        SectionCard {
            Text("Сервисы", color = TextPrimary, style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(4.dp))
            config.services.forEach { service ->
                SwitchRow(
                    label = service.name,
                    caption = service.subtitle,
                    checked = service.id in draft.enabledServiceIds,
                ) { enabled ->
                    draft = draft.copy(
                        enabledServiceIds = if (enabled) {
                            draft.enabledServiceIds + service.id
                        } else {
                            draft.enabledServiceIds - service.id
                        },
                    )
                }
            }
        }

        Spacer(Modifier.height(10.dp))
        SectionCard {
            Text("Фоновый мониторинг", color = TextPrimary, style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(4.dp))
            Text(
                text = "Постоянная иконка статуса в строке состояния (рядом с часами) и проверки " +
                    "по расписанию в foreground-сервисе. Цвет виден в шторке; сама иконка " +
                    "монохромная и меняет форму: ● всё ок, ▲ нестабильно, ⊘ блокировка, ✕ нет связи, ○ нет данных. Цвет в строке состояния задаёт тема Android.",
                color = TextMuted,
                style = MaterialTheme.typography.labelSmall,
            )
            TextButton(
                onClick = {
                    val intent = Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                        .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
                    context.startActivity(intent)
                },
            ) { Text("Настройки уведомлений приложения") }
            OutlinedButton(
                onClick = {
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                        val manager = AppWidgetManager.getInstance(context)
                        val provider = ComponentName(context, StatusClockWidget::class.java)
                        if (manager.isRequestPinAppWidgetSupported) {
                            manager.requestPinAppWidget(provider, null, null)
                        } else {
                            Toast.makeText(
                                context,
                                "Добавьте виджет вручную: долгий тап по рабочему столу → Виджеты",
                                Toast.LENGTH_LONG,
                            ).show()
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Добавить виджет-часы на главный экран") }
            var cityDraft by remember { mutableStateOf(WidgetCityPrefs.get(context)) }
            var geoRefresh by remember { mutableStateOf(WidgetCityPrefs.geoRefreshMinutes(context)) }
            Text(
                text = "Обновление города по GPS",
                color = TextPrimary,
                style = MaterialTheme.typography.bodyMedium,
            )
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                listOf(120 to "2 часа", 240 to "4 часа", 480 to "8 часов", -1 to "вручную")
                    .forEach { (minutes, label) ->
                        FilterChip(
                            selected = geoRefresh == minutes,
                            onClick = {
                                geoRefresh = minutes
                                WidgetCityPrefs.setGeoRefreshMinutes(context, minutes)
                                WeatherRepository.refreshIfNeeded(context, force = true)
                            },
                            label = { Text(label) },
                        )
                    }
            }
            Text(
                text = "«вручную» — город берётся из поля ниже, GPS-обновление не выполняется",
                color = TextMuted,
                style = MaterialTheme.typography.labelSmall,
            )
            OutlinedTextField(
                value = cityDraft,
                onValueChange = { cityDraft = it },
                label = { Text("Город для часов (вручную)") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            TextButton(
                onClick = {
                    WidgetCityPrefs.set(context, cityDraft)
                    WeatherRepository.refreshIfNeeded(context, force = true)
                    Toast.makeText(context, "Город сохранён", Toast.LENGTH_SHORT).show()
                },
            ) { Text("Сохранить город") }

            val locationLauncher = rememberLauncherForActivityResult(
                ActivityResultContracts.RequestMultiplePermissions(),
            ) { granted ->
                if (granted.values.any { it }) {
                    if (WidgetCityPrefs.geoRefreshMinutes(context) < 0) {
                        WidgetCityPrefs.setGeoRefreshMinutes(context, 30)
                        geoRefresh = 30
                    }
                    WeatherRepository.refreshIfNeeded(context, force = true)
                    Toast.makeText(context, "Город будет по геопозиции", Toast.LENGTH_SHORT).show()
                } else {
                    Toast.makeText(context, "Геопозиция не разрешена", Toast.LENGTH_SHORT).show()
                }
            }
            val locationGranted = ContextCompat.checkSelfPermission(
                context, Manifest.permission.ACCESS_COARSE_LOCATION,
            ) == android.content.pm.PackageManager.PERMISSION_GRANTED
            Button(
                onClick = {
                    locationLauncher.launch(
                        arrayOf(
                            Manifest.permission.ACCESS_COARSE_LOCATION,
                            Manifest.permission.ACCESS_FINE_LOCATION,
                        ),
                    )
                },
            ) {
                Text(
                    if (locationGranted) "Обновить город по геопозиции"
                    else "Разрешить геопозицию для погоды",
                )
            }

            SwitchRow(
                label = "Индикатор статуса в строке состояния",
                caption = "запускать при сохранении настроек и при открытии приложения",
                checked = draft.foregroundService,
            ) { draft = draft.copy(foregroundService = it) }
            var hideStatusIcon by remember { mutableStateOf(StatusIconPrefs.isHidden(context)) }
            SwitchRow(
                label = "Скрывать значок в строке состояния",
                caption = "тихий канал: на HONOR значок обычно скрывается, сервис продолжает работать",
                checked = hideStatusIcon,
            ) { enabled ->
                hideStatusIcon = enabled
                StatusIconPrefs.set(context, enabled)
            }
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = onStartService,
                    enabled = !serviceRunning,
                    colors = ButtonDefaults.buttonColors(containerColor = AccentGreen, contentColor = Color(0xFF04140E)),
                    modifier = Modifier.weight(1f),
                ) { Text("Запустить") }
                OutlinedButton(
                    onClick = onStopService,
                    enabled = serviceRunning,
                    modifier = Modifier.weight(1f),
                ) { Text("Остановить") }
            }
            Spacer(Modifier.height(4.dp))
            Text(
                text = if (serviceRunning) "Сервис работает" else "Сервис остановлен",
                color = if (serviceRunning) AccentGreen else TextMuted,
                style = MaterialTheme.typography.labelSmall,
            )
        }

        Spacer(Modifier.height(14.dp))
        Button(
            onClick = {
                onSave(draft)
                saved = true
            },
            modifier = Modifier.fillMaxWidth(),
            colors = ButtonDefaults.buttonColors(
                containerColor = AccentBlue,
                contentColor = Color.White,
            ),
        ) {
            Text("Сохранить настройки", fontWeight = FontWeight.SemiBold)
        }
        if (saved) {
            Spacer(Modifier.height(6.dp))
            Text(
                text = "Сохранено и применено к движку проверок.",
                color = AccentGreen,
                style = MaterialTheme.typography.bodySmall,
            )
        }

        Spacer(Modifier.height(8.dp))
        OutlinedButton(
            onClick = { showResetDialog = true },
            modifier = Modifier.fillMaxWidth(),
        ) { Text("Сбросить историю и журнал") }

        Spacer(Modifier.height(14.dp))
        Text(
            text = "Источник сервисов, эндпоинтов, таймаутов, порогов, статусов, цветов и подсказок — " +
                "config.json из корня репозитория (копируется в assets при сборке).",
            color = TextMuted,
            style = MaterialTheme.typography.labelSmall,
        )
        Spacer(Modifier.height(16.dp))
    }

    if (showResetDialog) {
        AlertDialog(
            onDismissRequest = { showResetDialog = false },
            title = { Text("Сбросить историю?") },
            text = { Text("Будут удалены серии uptime и журнал инцидентов. Настройки останутся.") },
            confirmButton = {
                TextButton(onClick = {
                    onResetHistory()
                    showResetDialog = false
                    saved = false
                }) { Text("Сбросить", color = Color(0xFFFF3B30)) }
            },
            dismissButton = {
                TextButton(onClick = { showResetDialog = false }) { Text("Отмена") }
            },
            containerColor = Card,
            titleContentColor = TextPrimary,
            textContentColor = TextSecondary,
        )
    }
}

@Composable
private fun StepperRow(
    label: String,
    value: Int,
    unit: String,
    range: IntRange,
    step: Int,
    onChange: (Int) -> Unit,
) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(modifier = Modifier.weight(1f)) {
            Text(text = label, color = TextSecondary, style = MaterialTheme.typography.bodyMedium)
        }
        TextButton(
            onClick = { onChange((value - step).coerceIn(range.first, range.last)) },
            enabled = value > range.first,
        ) { Text("−", color = AccentBlue, style = MaterialTheme.typography.titleMedium) }
        Text(
            text = "$value $unit",
            color = TextPrimary,
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.width(86.dp),
            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
        )
        TextButton(
            onClick = { onChange((value + step).coerceIn(range.first, range.last)) },
            enabled = value < range.last,
        ) { Text("+", color = AccentBlue, style = MaterialTheme.typography.titleMedium) }
    }
}

@Composable
private fun SwitchRow(
    label: String,
    caption: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(text = label, color = TextPrimary, style = MaterialTheme.typography.bodyMedium)
            if (caption.isNotEmpty()) {
                Text(text = caption, color = TextMuted, style = MaterialTheme.typography.labelSmall)
            }
        }
        Spacer(Modifier.width(8.dp))
        Switch(
            checked = checked,
            onCheckedChange = onCheckedChange,
            colors = SwitchDefaults.colors(
                checkedThumbColor = Color.White,
                checkedTrackColor = AccentBlue,
                uncheckedThumbColor = TextMuted,
                uncheckedTrackColor = CardSoft,
                uncheckedBorderColor = Border,
            ),
        )
    }
}
