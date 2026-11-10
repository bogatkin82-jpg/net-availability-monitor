import Foundation

/// Загрузка и разбор `Resources/config.json`.
///
/// Важно (паритет с десктопом и Android): единственный источник истины —
/// корневой `config.json` репозитория; при сборке он копируется в
/// `ios/Resources/config.json` и попадает в бандл приложения. Значения в
/// `AppConfig.defaults` — аварийный фолбэк (если файла нет), они обязаны
/// совпадать с config.json (проверяет `ConfigParityTests`).
enum ConfigLoader {
    /// Имя ресурса в бандле (Resources/config.json).
    static let resourceName = "config"

    enum Source: String, Sendable {
        case bundle
        case fallback
    }

    struct Loaded: Sendable {
        let config: AppConfig
        let source: Source
    }

    enum ConfigError: Error {
        case notAnObject
    }

    /// Читает config.json из бандла; при любой проблеме возвращает дефолты.
    static func load(bundle: Bundle = .main) -> Loaded {
        guard let url = bundle.url(forResource: resourceName, withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let config = try? decode(data) else {
            return Loaded(config: .defaults, source: .fallback)
        }
        return Loaded(config: config, source: .bundle)
    }

    /// Разбирает JSON-данные конфига (бросает при невалидном JSON).
    static func decode(_ data: Data) throws -> AppConfig {
        let object = try JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
        guard let root = object as? [String: Any] else {
            throw ConfigError.notAnObject
        }
        return makeConfig(from: root)
    }

    /// Собирает AppConfig из словаря JSON, подставляя дефолты для отсутствующих полей.
    static func makeConfig(from root: [String: Any]) -> AppConfig {
        let base = AppConfig.defaults

        var config = AppConfig(
            version: intValue(root["version"]) ?? base.version,
            server: base.server,
            polling: base.polling,
            incidents: base.incidents,
            history: base.history,
            network: base.network,
            statusLabels: base.statusLabels,
            statusColors: base.statusColors,
            services: base.services,
            notifications: base.notifications
        )

        if let raw = root.object("server") {
            config.server = ServerConfig(
                host: raw.string("host") ?? base.server.host,
                port: clamp(raw.int("port") ?? base.server.port, 1, 65535)
            )
        }

        if let raw = root.object("polling") {
            config.polling = PollingConfig(
                intervalSec: max(5, raw.int("interval_sec") ?? base.polling.intervalSec),
                checkTimeoutSec: max(1, raw.int("check_timeout_sec") ?? base.polling.checkTimeoutSec),
                dnsTimeoutSec: max(1, raw.int("dns_timeout_sec") ?? base.polling.dnsTimeoutSec)
            )
        }

        if let raw = root.object("incidents") {
            config.incidents = IncidentsConfig(
                openAfterFailures: max(1, raw.int("open_after_failures") ?? base.incidents.openAfterFailures),
                closeAfterSuccesses: max(1, raw.int("close_after_successes") ?? base.incidents.closeAfterSuccesses),
                maxStored: max(1, raw.int("max_stored") ?? base.incidents.maxStored)
            )
        }

        if let raw = root.object("history") {
            config.history = HistoryConfig(
                maxAgeHours: max(1, raw.int("max_age_hours") ?? base.history.maxAgeHours),
                maxPoints: max(1, raw.int("max_points") ?? base.history.maxPoints),
                seriesPoints: max(1, raw.int("series_points") ?? base.history.seriesPoints)
            )
        }

        if let raw = root.object("network") {
            config.network = NetworkConfig(proxy: raw.string("proxy") ?? base.network.proxy)
        }

        if let raw = root.object("status_labels") {
            config.statusLabels = mergeStatusMap(raw, fallback: base.statusLabels)
        }

        if let raw = root.object("status_colors") {
            config.statusColors = mergeStatusMap(raw, fallback: base.statusColors)
        }

        if let services = parseServices(root.array("services")), !services.isEmpty {
            config.services = services
        }

        config.notifications = parseNotifications(root.object("notifications"), base: base.notifications)

        return config
    }

    // MARK: - Разбор секций

    private static func parseServices(_ raw: [Any]?) -> [ServiceConfig]? {
        guard let raw else { return nil }
        var services: [ServiceConfig] = []
        for item in raw {
            guard let dict = item as? [String: Any],
                  let id = dict.string("id"), !id.isEmpty else { continue }
            let name = dict.string("name") ?? id
            services.append(ServiceConfig(
                id: id,
                name: name,
                subtitle: dict.string("subtitle") ?? "",
                icon: dict.string("icon") ?? id,
                accent: dict.string("accent") ?? "#8e8e93",
                incidentTitle: dict.string("incident_title") ?? "\(name) недоступен",
                hints: stringMap(dict.object("hints")),
                checks: parseChecks(dict.array("checks"))
            ))
        }
        return services
    }

    private static func parseChecks(_ raw: [Any]?) -> [CheckSpec] {
        guard let raw else { return [] }
        var checks: [CheckSpec] = []
        for item in raw {
            guard let dict = item as? [String: Any],
                  let id = dict.string("id"), !id.isEmpty,
                  let kindRaw = dict.string("kind"), let kind = CheckKind(rawValue: kindRaw),
                  let host = dict.string("host"), !host.isEmpty else { continue }
            let expect: [Int] = (dict.array("expect") ?? []).compactMap { intValue($0) }
            checks.append(CheckSpec(
                id: id,
                label: dict.string("label") ?? id,
                kind: kind,
                host: host,
                port: clamp(dict.int("port") ?? 443, 1, 65535),
                path: dict.string("path") ?? "",
                expect: expect,
                core: dict.bool("core") ?? false,
                timeoutSec: max(1, dict.int("timeout") ?? 8)
            ))
        }
        return checks
    }

    /// `notifications.ios` (если появится) либо `notifications.android` как
    /// общий мобильный блок; иначе — дефолты. Поля: enabled, notify_on.
    private static func parseNotifications(_ raw: [String: Any]?,
                                          base: NotificationSettings) -> NotificationSettings {
        guard let raw else { return base }
        guard let block = raw.object("ios") ?? raw.object("android") else { return base }
        var settings = base
        if let enabled = block.bool("enabled") {
            settings.enabled = enabled
        }
        if let list = block.array("notify_on") {
            let values = list.compactMap { $0 as? String }.filter { Status(rawValue: $0) != nil }
            if !values.isEmpty {
                settings.notifyOn = values
            }
        }
        return settings
    }

    private static func mergeStatusMap(_ raw: [String: Any],
                                       fallback: [String: String]) -> [String: String] {
        var merged = fallback
        for (key, value) in raw {
            if let text = value as? String, !text.isEmpty {
                merged[key] = text
            }
        }
        // Гарантируем все пять статусов — как в config.json.
        for status in Status.allCases where merged[status.rawValue] == nil {
            merged[status.rawValue] = fallback[status.rawValue]
        }
        return merged
    }

    private static func stringMap(_ raw: [String: Any]?) -> [String: String] {
        guard let raw else { return [:] }
        var result: [String: String] = [:]
        for (key, value) in raw {
            if let text = value as? String {
                result[key] = text
            }
        }
        return result
    }

    // MARK: - Приведение типов

    private static func intValue(_ value: Any?) -> Int? {
        if let number = value as? NSNumber { return number.intValue }
        if let text = value as? String { return Int(text) }
        return nil
    }

    private static func clamp(_ value: Int, _ lower: Int, _ upper: Int) -> Int {
        min(max(value, lower), upper)
    }
}

// MARK: - Удобное чтение JSON-словаря

private extension Dictionary where Key == String, Value == Any {
    func object(_ key: String) -> [String: Any]? {
        self[key] as? [String: Any]
    }

    func array(_ key: String) -> [Any]? {
        self[key] as? [Any]
    }

    func string(_ key: String) -> String? {
        self[key] as? String
    }

    func int(_ key: String) -> Int? {
        if let number = self[key] as? NSNumber { return number.intValue }
        if let text = self[key] as? String { return Int(text) }
        return nil
    }

    func bool(_ key: String) -> Bool? {
        if let number = self[key] as? NSNumber { return number.boolValue }
        if let text = self[key] as? String {
            return (text as NSString).boolValue
        }
        return nil
    }
}
