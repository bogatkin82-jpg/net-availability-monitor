package ru.netmon.android.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import ru.netmon.android.AppContainer
import ru.netmon.android.service.MonitorService

import ru.netmon.core.UserSettings

private enum class Tab(val title: String, val glyph: String) {
    STATUS("Статус", "◉"),
    JOURNAL("Журнал", "☰"),
    SETTINGS("Настройки", "⚙"),
}

/**
 * Корневой экран: нижняя навигация «Статус / Журнал / Настройки»,
 * запрос POST_NOTIFICATIONS на Android 13+ и автообновление.
 */
@Composable
fun NetMonRoot(container: AppContainer) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val scope = rememberCoroutineScope()

    val snapshot by container.controller.snapshot.collectAsStateWithLifecycle()
    val settings by container.controller.settings.collectAsStateWithLifecycle()

    var tab by rememberSaveable { mutableIntStateOf(0) }
    var serviceRunning by remember { mutableStateOf(MonitorService.isRunning) }
    var tickMs by remember { mutableLongStateOf(System.currentTimeMillis()) }

    // Разрешение на уведомления (Android 13+).
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { }
    LaunchedEffect(Unit) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.POST_NOTIFICATIONS,
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    // Автообновление в активном состоянии: раунд проверок — по интервалу из настроек,
    // счётчик «через N с» — раз в секунду. В фоне жизненный цикл останавливает цикл.
    LaunchedEffect(lifecycleOwner, settings.intervalSec) {
        lifecycleOwner.repeatOnLifecycle(Lifecycle.State.RESUMED) {
            launch {
                while (isActive) {
                    container.controller.refreshIfDue()
                    delay(UI_REFRESH_MS)
                }
            }
            launch {
                while (isActive) {
                    tickMs = System.currentTimeMillis()
                    delay(1000L)
                }
            }
        }
    }

    // Тик в ключе remember, чтобы пересчитать обратный отсчёт каждую секунду.
    val nextCheckIn = remember(tickMs, snapshot) { container.controller.nextCheckInSeconds() }

    Scaffold(
        containerColor = Bg,
        bottomBar = {
            NavigationBar(containerColor = Card, contentColor = TextSecondary) {
                Tab.entries.forEach { item ->
                    NavigationBarItem(
                        selected = tab == item.ordinal,
                        onClick = { tab = item.ordinal },
                        icon = { Text(text = item.glyph, style = MaterialTheme.typography.titleMedium) },
                        label = { Text(text = item.title, style = MaterialTheme.typography.bodySmall) },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = AccentBlue,
                            selectedTextColor = AccentBlue,
                            unselectedIconColor = TextMuted,
                            unselectedTextColor = TextMuted,
                            indicatorColor = AccentBlue.copy(alpha = 0.14f),
                        ),
                    )
                }
            }
        },
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            when (Tab.entries[tab]) {
                Tab.STATUS -> StatusScreen(
                    config = container.config,
                    snapshot = snapshot,
                    nextCheckIn = nextCheckIn,
                    onCheckNow = {
                        scope.launch { container.controller.refresh() }
                    },
                )

                Tab.JOURNAL -> JournalScreen(
                    config = container.config,
                    controller = container.controller,
                    snapshotVersion = snapshot.version,
                )

                Tab.SETTINGS -> SettingsScreen(
                    config = container.config,
                    settings = settings,
                    serviceRunning = serviceRunning,
                    onSave = { updated: UserSettings ->
                        container.controller.applySettings(updated)
                        if (updated.foregroundService) {
                            MonitorService.start(context)
                            serviceRunning = true
                        } else {
                            MonitorService.stop(context)
                            serviceRunning = false
                        }
                        if (serviceRunning) MonitorService.refreshNotification(context)
                    },
                    onStartService = {
                        MonitorService.start(context)
                        serviceRunning = true
                    },
                    onStopService = {
                        MonitorService.stop(context)
                        serviceRunning = false
                    },
                    onResetHistory = { container.controller.resetHistory() },
                )
            }
        }
    }
}

/** Период «UI-тика»: обновление экрана и попытка запуска раунда. */
private const val UI_REFRESH_MS = 3_000L
