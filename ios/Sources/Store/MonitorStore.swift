import Foundation
import SwiftUI
import UserNotifications

/// Главный store приложения: таймер опроса, ручная проверка, история,
/// инциденты, уведомления и сохранение состояния в UserDefaults.
///
/// Сетевые проверки выполняются вне главного потока (`StagedChecker` —
/// nonisolated async), UI обновляется на `@MainActor`.
@MainActor
final class MonitorStore: ObservableObject {
    static let stateKey = "netmon.state.v1"

    // MARK: Публикуемое состояние

    @Published private(set) var config: AppConfig
    @Published private(set) var configSource: ConfigLoader.Source
    @Published private(set) var snapshot: Snapshot
    @Published private(set) var incidents: [Incident] = []
    @Published private(set) var events: [MonitorEvent] = []
    @Published private(set) var isChecking = false
    @Published private(set) var notificationPermission: UNAuthorizationStatus = .notDetermined

    /// Настройки пользователя. Любое изменение сразу сохраняется и
    /// перезапускает таймер опроса.
    @Published var settings: UserSettings {
        didSet {
            SettingsStore.save(settings, store: defaults)
            if settings.notificationsEnabled && !oldValue.notificationsEnabled {
                Task { await requestNotificationPermission() }
            }
            // Таймер перезапускаем только при смене интервала опроса.
            if settings.clamped().intervalSec != oldValue.clamped().intervalSec {
                restartTimer()
            }
            refreshPublished()
        }
    }

    // MARK: Приватное

    private let defaults: UserDefaults
    private let monitor: MonitorState
    private let checker = StagedChecker()
    private let notifications = NotificationManager.shared
    private var timerTask: Task<Void, Never>?
    private var hasStarted = false

    // MARK: Инициализация

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults

        let loaded = ConfigLoader.load()
        let initialSettings = SettingsStore.load(
            fallback: UserSettings.defaults(from: loaded.config),
            store: defaults
        )
        let state = MonitorState(config: initialSettings.effectiveConfig(base: loaded.config))
        if let data = defaults.data(forKey: Self.stateKey),
           let persisted = try? JSONDecoder().decode(StatePersistence.self, from: data) {
            state.restore(persisted)
        }

        self.config = loaded.config
        self.configSource = loaded.source
        self.settings = initialSettings
        self.monitor = state
        self.snapshot = state.snapshot(checking: false, nextCheckIn: 0)
        self.incidents = state.incidentsNewestFirst
        self.events = state.eventsNewestFirst
    }

    // MARK: Производные значения

    /// Конфиг с наложенными настройками пользователя.
    var effectiveConfig: AppConfig {
        settings.effectiveConfig(base: config)
    }

    /// Момент следующего автоматического опроса (nil — таймер остановлен).
    var nextCheckDate: Date? {
        monitor.nextRoundEpoch
    }

    var hasData: Bool {
        monitor.rounds > 0
    }

    // MARK: Жизненный цикл

    /// Запускает таймер и, если данных ещё нет, выполняет первый опрос.
    func start() async {
        guard !hasStarted else { return }
        hasStarted = true
        await refreshNotificationPermission()
        if effectiveConfig.notifications.enabled,
           notificationPermission == .notDetermined {
            await requestNotificationPermission()
        }
        scheduleTimer()
        if monitor.rounds == 0 {
            await checkNow()
        }
    }

    func handleScenePhase(_ phase: ScenePhase) {
        switch phase {
        case .active:
            // Возврат в foreground: опрос возобновляется (фона на iOS нет).
            scheduleTimer()
            if let last = monitor.lastRoundEpoch,
               Date().timeIntervalSince(last) > 5 {
                Task { await checkNow() }
            }
        case .inactive:
            break
        case .background:
            timerTask?.cancel()
            timerTask = nil
            monitor.nextRoundEpoch = nil
            refreshPublished()
        @unknown default:
            break
        }
    }

    // MARK: Проверки

    /// Ручная проверка (кнопка «Проверить сейчас» / pull-to-refresh).
    func checkNow() async {
        guard !isChecking else { return }
        isChecking = true
        refreshPublished()

        let activeConfig = effectiveConfig
        monitor.updateConfig(activeConfig)
        let options = settings.checkOptions(config: config)

        let results = await checker.runRound(services: activeConfig.services, options: options)
        let outcome = monitor.applyRound(byService: results)
        saveState()

        isChecking = false
        refreshPublished()
        await sendNotifications(outcome: outcome, config: activeConfig)
    }

    // MARK: Таймер

    private func restartTimer() {
        guard hasStarted else { return }
        scheduleTimer()
    }

    private func scheduleTimer() {
        timerTask?.cancel()
        let interval = TimeInterval(settings.clamped().intervalSec)
        monitor.nextRoundEpoch = Date().addingTimeInterval(interval)
        refreshPublished()

        timerTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                let current = TimeInterval(self.settings.clamped().intervalSec)
                self.monitor.nextRoundEpoch = Date().addingTimeInterval(current)
                self.refreshPublished()
                do {
                    try await Task.sleep(nanoseconds: UInt64(current * 1_000_000_000))
                } catch {
                    return
                }
                if Task.isCancelled { return }
                await self.checkNow()
            }
        }
    }

    // MARK: Уведомления

    func requestNotificationPermission() async {
        _ = await NotificationManager.shared.requestAuthorization()
        await refreshNotificationPermission()
    }

    private func refreshNotificationPermission() async {
        await notifications.refreshAuthorization()
        notificationPermission = notifications.authorizationStatus
    }

    /// Уведомления только при переходе статуса в blocked/down (notify_on),
    /// если уведомления включены в настройках.
    private func sendNotifications(outcome: RoundOutcome, config: AppConfig) async {
        guard config.notifications.enabled else { return }
        let allowed = Set(config.notifications.notifyOn)
        guard !allowed.isEmpty else { return }

        for service in config.services {
            guard let current = outcome.statuses[service.id],
                  let previous = outcome.previousStatuses[service.id],
                  previous != current,
                  allowed.contains(current.rawValue),
                  current.isProblem,
                  let info = outcome.infos[service.id] else { continue }
            let body = info.reason + (info.hint.map { "\n" + $0 } ?? "")
            await notifications.post(title: "\(service.name): \(info.label)", body: body)
        }
    }

    // MARK: Настройки / данные

    /// Сброс настроек к значениям config.json.
    func resetSettings() {
        settings = UserSettings.defaults(from: config)
    }

    /// Удаление истории, серий и инцидентов.
    func clearHistory() {
        monitor.reset()
        defaults.removeObject(forKey: Self.stateKey)
        refreshPublished()
    }

    // MARK: Служебное

    private func saveState() {
        guard let data = try? JSONEncoder().encode(monitor.persistence) else { return }
        defaults.set(data, forKey: Self.stateKey)
    }

    private func refreshPublished() {
        snapshot = monitor.snapshot(checking: isChecking, nextCheckIn: nextCheckIn)
        incidents = monitor.incidentsNewestFirst
        events = monitor.eventsNewestFirst
    }

    private var nextCheckIn: TimeInterval {
        guard let next = monitor.nextRoundEpoch, !isChecking else { return 0 }
        return max(0, next.timeIntervalSinceNow)
    }
}
