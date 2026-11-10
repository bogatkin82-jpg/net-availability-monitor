package ru.netmon.android

import android.app.Application
import android.content.Context
import ru.netmon.android.monitor.MonitorController
import ru.netmon.android.service.Notifications
import ru.netmon.core.AppConfig

/** Контейнер зависимостей приложения (без DI-библиотек). */
class AppContainer(context: Context) {
    private val applicationContext: Context = context.applicationContext

    val config: AppConfig = ConfigRepository(applicationContext).load()
    private val settingsRepository = SettingsRepository(applicationContext, config)
    val notifications: Notifications = Notifications(applicationContext, config)
    val controller: MonitorController = MonitorController(
        context = applicationContext,
        config = config,
        settingsRepository = settingsRepository,
    )
}

class NetMonApp : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
    }
}
