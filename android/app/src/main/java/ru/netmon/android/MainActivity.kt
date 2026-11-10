package ru.netmon.android

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import ru.netmon.android.ui.NetMonRoot
import ru.netmon.android.service.MonitorService
import ru.netmon.android.ui.NetMonTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val container = (application as NetMonApp).container
        // Постоянная иконка статуса в строке состояния: поднимаем
        // foreground-service при запуске, если он включён в настройках.
        if (container.controller.settings.value.foregroundService && !MonitorService.isRunning) {
            try {
                MonitorService.start(this)
            } catch (exc: RuntimeException) {
                android.util.Log.w("NetMon", "не удалось запустить foreground-service", exc)
            }
        }
        setContent {
            NetMonTheme {
                NetMonRoot(container)
            }
        }
    }
}
