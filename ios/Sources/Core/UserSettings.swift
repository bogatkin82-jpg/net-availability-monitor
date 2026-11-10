import Foundation

/// Опции одного раунда проверок.
struct CheckOptions: Sendable, Equatable {
    /// Переопределение `check_timeout_sec`; `nil` — брать per-check `timeout`
    /// из config.json (как server.py).
    var checkTimeoutSec: Int?
    /// `dns_timeout_sec` из настроек.
    var dnsTimeoutSec: Int
}

/// Пользовательские настройки поверх config.json.
///
/// Дефолты всегда берутся из config.json; меняются только те поля, которые
/// пользователь реально трогал на экране «Настройки». Итоговый конфиг для
/// движка собирает `effectiveConfig(base:)`. Правки сохраняются в UserDefaults.
struct UserSettings: Codable, Sendable, Equatable {
    var intervalSec: Int
    var checkTimeoutSec: Int
    var dnsTimeoutSec: Int
    var openAfterFailures: Int
    var closeAfterSuccesses: Int
    var maxStored: Int
    var notificationsEnabled: Bool
    var notifyOn: [String]

    /// Минимальный интервал опроса — требование ТЗ (не чаще 5 с).
    static let minIntervalSec = 5
    static let maxIntervalSec = 300

    static func defaults(from config: AppConfig) -> UserSettings {
        UserSettings(
            intervalSec: config.polling.intervalSec,
            checkTimeoutSec: config.polling.checkTimeoutSec,
            dnsTimeoutSec: config.polling.dnsTimeoutSec,
            openAfterFailures: config.incidents.openAfterFailures,
            closeAfterSuccesses: config.incidents.closeAfterSuccesses,
            maxStored: config.incidents.maxStored,
            notificationsEnabled: config.notifications.enabled,
            notifyOn: config.notifications.notifyOn
        )
    }

    /// Полный конфиг с наложенными настройками пользователя.
    func effectiveConfig(base: AppConfig) -> AppConfig {
        let clamped = self.clamped()
        var config = base
        config.polling = PollingConfig(
            intervalSec: clamped.intervalSec,
            checkTimeoutSec: clamped.checkTimeoutSec,
            dnsTimeoutSec: clamped.dnsTimeoutSec
        )
        config.incidents = IncidentsConfig(
            openAfterFailures: clamped.openAfterFailures,
            closeAfterSuccesses: clamped.closeAfterSuccesses,
            maxStored: clamped.maxStored
        )
        config.notifications = NotificationSettings(
            enabled: clamped.notificationsEnabled,
            notifyOn: Status.allCases.map(\.rawValue).filter { clamped.notifyOn.contains($0) }
        )
        return config
    }

    /// Опции раунда. Если таймаут не менялся — `nil`, и движок использует
    /// per-check `timeout` из config.json.
    func checkOptions(config: AppConfig) -> CheckOptions {
        let clamped = self.clamped()
        let override = clamped.checkTimeoutSec != config.polling.checkTimeoutSec
            ? clamped.checkTimeoutSec
            : nil
        return CheckOptions(checkTimeoutSec: override, dnsTimeoutSec: clamped.dnsTimeoutSec)
    }

    /// `notify_on` в виде статусов (в порядке config.json).
    var notifyOnStatuses: [Status] {
        Status.allCases.filter { notifyOn.contains($0.rawValue) }
    }

    /// Значения в допустимых пределах (то же делает парсер config.json).
    func clamped() -> UserSettings {
        var value = self
        value.intervalSec = min(max(value.intervalSec, Self.minIntervalSec), Self.maxIntervalSec)
        value.checkTimeoutSec = min(max(value.checkTimeoutSec, 1), 60)
        value.dnsTimeoutSec = min(max(value.dnsTimeoutSec, 1), 30)
        value.openAfterFailures = min(max(value.openAfterFailures, 1), 20)
        value.closeAfterSuccesses = min(max(value.closeAfterSuccesses, 1), 20)
        value.maxStored = min(max(value.maxStored, 10), 5000)
        value.notifyOn = value.notifyOn.filter { Status(rawValue: $0) != nil }
        return value
    }
}

/// Сохранение настроек в UserDefaults (JSON).
enum SettingsStore {
    static let key = "netmon.settings.v1"

    static func load(fallback: UserSettings, store: UserDefaults = .standard) -> UserSettings {
        guard let data = store.data(forKey: key),
              let settings = try? JSONDecoder().decode(UserSettings.self, from: data) else {
            return fallback
        }
        return settings.clamped()
    }

    static func save(_ settings: UserSettings, store: UserDefaults = .standard) {
        guard let data = try? JSONEncoder().encode(settings) else { return }
        store.set(data, forKey: key)
    }

    static func clear(store: UserDefaults = .standard) {
        store.removeObject(forKey: key)
    }
}
