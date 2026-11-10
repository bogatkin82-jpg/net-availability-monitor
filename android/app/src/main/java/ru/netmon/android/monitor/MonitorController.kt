package ru.netmon.android.monitor

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.withContext
import ru.netmon.android.SettingsRepository
import ru.netmon.core.AppConfig
import ru.netmon.core.CheckExecutor
import ru.netmon.core.CheckOptions
import ru.netmon.core.IncidentReport
import ru.netmon.core.MonitorState
import ru.netmon.core.Snapshot
import ru.netmon.core.StagedChecker
import ru.netmon.core.UserSettings

/**
 * Мост между UI/сервисом и движком: держит [MonitorState], выполняет раунды
 * проверок параллельно по всем включённым сервисам, применяет настройки
 * пользователя поверх config.json.
 */
class MonitorController(
    private val context: Context,
    private val config: AppConfig,
    private val settingsRepository: SettingsRepository,
    private val checker: CheckExecutor = StagedChecker(),
) {
    private val state = MonitorState(config)
    private val roundMutex = Mutex()

    @Suppress("unused")
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val _settings = MutableStateFlow(settingsRepository.load())
    private val _snapshot = MutableStateFlow(state.snapshot())

    val settings: StateFlow<UserSettings> = _settings.asStateFlow()
    val snapshot: StateFlow<Snapshot> = _snapshot.asStateFlow()

    private var lastRoundFinishedMs: Long = 0L

    init {
        state.loadJson(settingsRepository.loadState())
        state.updateConfig(_settings.value.effectiveConfig(config))
        _snapshot.value = state.snapshot()
    }

    // --- настройки --------------------------------------------------------
    fun applySettings(settings: UserSettings) {
        settingsRepository.save(settings)
        _settings.value = settings
        state.updateConfig(settings.effectiveConfig(config))
        state.markNextRound(System.currentTimeMillis() + settings.intervalSec * 1000L)
        _snapshot.value = state.snapshot()
    }

    fun resetHistory() {
        state.resetHistory()
        settingsRepository.clearState()
        _snapshot.value = state.snapshot()
    }

    // --- планирование -----------------------------------------------------
    fun nextCheckInSeconds(): Double {
        val next = state.nextRoundEpoch ?: return 0.0
        return maxOf(0.0, (next - System.currentTimeMillis()) / 1000.0)
    }

    fun isRoundDue(): Boolean =
        System.currentTimeMillis() - lastRoundFinishedMs >= _settings.value.intervalSec * 1000L

    suspend fun refreshIfDue(): Snapshot? = if (isRoundDue()) refresh() else null

    /**
     * Один раунд проверок. Если раунд уже идёт — возвращает null
     * (аналог неблокирующего round_lock в server.py).
     */
    suspend fun refresh(): Snapshot? {
        if (!roundMutex.tryLock()) return null
        try {
            val settings = _settings.value
            val effectiveConfig = settings.effectiveConfig(config)
            val options = settings.checkOptions(config)
            val started = System.currentTimeMillis()
            state.markNextRound(started + settings.intervalSec * 1000L)
            state.setChecking(true)
            _snapshot.value = state.snapshot()

            val byService = withContext(Dispatchers.IO) {
                effectiveConfig.services.associate { service ->
                    service.id to coroutineScope {
                        service.checks.map { spec ->
                            async { checker.run(spec, options) }
                        }.awaitAll()
                    }
                }
            }

            state.applyRound(byService)
            lastRoundFinishedMs = System.currentTimeMillis()
            state.setChecking(false)
            state.markNextRound(lastRoundFinishedMs + settings.intervalSec * 1000L)
            settingsRepository.saveState(state.toJson())
            _snapshot.value = state.snapshot()
            return _snapshot.value
        } finally {
            state.setChecking(false)
            roundMutex.unlock()
        }
    }

    fun incidentsReport(
        query: String = "",
        serviceId: String = "all",
        status: String = "all",
        hours: Int = 0,
    ): IncidentReport = state.incidentsReport(query, serviceId, status, hours)

    /** Опции проверок для UI-подсказок о текущих таймаутах. */
    fun currentCheckOptions(): CheckOptions = _settings.value.checkOptions(config)
}
