import Foundation

// MARK: - История и инциденты

/// Точка истории опросов (серия для полоски uptime).
struct SeriesPoint: Codable, Sendable, Equatable {
    /// Epoch-секунды (как `t` в API Python-сервера).
    var t: Int
    var status: Status
    var latencyMs: Int?
}

enum IncidentStatus: String, Codable, Sendable {
    case active
    case resolved
}

/// Инцидент недоступности — поля как в server.py/API.
struct Incident: Codable, Sendable, Identifiable, Equatable {
    let id: String
    let serviceId: String
    let serviceName: String
    var title: String
    var reason: String
    var status: IncidentStatus
    let startedAt: Date
    var endedAt: Date?
    let failedChecks: Int
    let totalChecks: Int
    var serviceStatus: Status

    var isActive: Bool { status == .active }

    /// Длительность в секундах (для активного — до `now`).
    func duration(now: Date) -> Int {
        let end = endedAt ?? now
        return max(0, Int(end.timeIntervalSince(startedAt)))
    }
}

/// Событие журнала: переход статуса сервиса (ok → blocked и т.п.).
struct MonitorEvent: Codable, Sendable, Identifiable, Equatable {
    let id: String
    let serviceId: String
    let serviceName: String
    let status: Status
    let title: String
    let detail: String
    let at: Date
}

/// Счётчики серии подряд идущих успехов/сбоев для открытия/закрытия инцидента.
struct Streak: Codable, Sendable, Equatable {
    var fail: Int = 0
    var ok: Int = 0
    var firstFail: Date?
}

/// Последний результат сервиса (аналог `last_results` в server.py).
struct ServiceResultData: Sendable, Equatable {
    var status: Status
    var label: String
    var reason: String
    var hint: String?
    var latencyMs: Int?
    var checksOk: Int
    var checksTotal: Int
    var checks: [CheckResult]
}

// MARK: - Снимок состояния для UI

struct InternetSummary: Sendable, Equatable {
    var status: Status
    var label: String
    var latencyMs: Int?
}

struct SnapshotSummary: Sendable, Equatable {
    var overall: Status
    var overallLabel: String
    var servicesTotal: Int
    var servicesOk: Int
    var servicesAffected: Int
    var activeIncidents: Int
    var avgLatencyMs: Int?
    var checksTotal: Int
    var checksOk: Int
}

struct ServiceSnapshot: Sendable, Identifiable, Equatable {
    var id: String
    var name: String
    var subtitle: String
    var icon: String
    var accent: String
    var status: Status
    var statusLabel: String
    var statusReason: String
    var hint: String?
    var latencyMs: Int?
    var uptime1h: Double
    var uptime24h: Double
    var checksOk: Int
    var checksTotal: Int
    var checks: [CheckResult]
    var series: [SeriesPoint]
    var activeIncident: Incident?
    var lastIncident: Incident?
}

struct Snapshot: Sendable, Equatable {
    var generatedAt: Date
    var version: Int
    var pollInterval: Int
    var nextCheckIn: TimeInterval
    var checking: Bool
    var internet: InternetSummary
    var summary: SnapshotSummary
    var services: [ServiceSnapshot]
    var recentIncidents: [Incident]
}

/// Итог одного раунда опроса: что было, что стало (для уведомлений).
struct RoundOutcome: Sendable {
    var previousStatuses: [String: Status]
    var infos: [String: ServiceInfo]
    var statuses: [String: Status]
}

/// Состояние монитора, сохраняемое между запусками.
struct StatePersistence: Codable, Sendable {
    var series: [String: [SeriesPoint]]
    var incidents: [Incident]
    var events: [MonitorEvent]
    var streaks: [String: Streak]
    var version: Int
    var rounds: Int
    var lastRoundEpoch: Date?
}

// MARK: - MonitorState

/// Состояние монитора: серии, инциденты, streaks и снимок для UI.
///
/// Чистый Swift (без UI и сети): часы и генератор id внедряются, поэтому
/// инциденты, uptime и классификация проверяются unit-тестами с управляемым
/// временем. Логика инцидентов — порт `Monitor._apply_round/_update_incidents`
/// из server.py.
final class MonitorState {
    private(set) var config: AppConfig
    private(set) var series: [String: [SeriesPoint]] = [:]
    private(set) var incidents: [Incident] = []
    private(set) var events: [MonitorEvent] = []
    private(set) var streaks: [String: Streak] = [:]
    private(set) var lastResults: [String: ServiceResultData] = [:]
    private(set) var version = 0
    private(set) var rounds = 0
    private(set) var lastRoundEpoch: Date?
    var nextRoundEpoch: Date?

    private let clock: () -> Date
    private let idGenerator: () -> String

    /// Максимум событий журнала (инциденты ограничены config.incidents.max_stored).
    static let maxEvents = 300

    init(config: AppConfig,
         clock: @escaping () -> Date = { Date() },
         idGenerator: @escaping () -> String = MonitorState.makeId) {
        self.config = config
        self.clock = clock
        self.idGenerator = idGenerator
        bootstrap()
    }

    // MARK: Настройка

    /// Применяет новый конфиг (после правок в «Настройках»).
    func updateConfig(_ newConfig: AppConfig) {
        config = newConfig
        bootstrap()
        pruneSeries(now: clock())
        pruneIncidents()
        pruneEvents()
    }

    private func bootstrap() {
        for service in config.services {
            if series[service.id] == nil { series[service.id] = [] }
            if streaks[service.id] == nil { streaks[service.id] = Streak() }
        }
    }

    // MARK: Раунд опроса

    func statusSnapshot() -> [String: Status] {
        lastResults.mapValues(\.status)
    }

    /// Применяет результаты раунда: классификация, серии, инциденты, события.
    @discardableResult
    func applyRound(byService: [String: [CheckResult]]) -> RoundOutcome {
        let now = clock()
        let previous = statusSnapshot()
        var infos: [String: ServiceInfo] = [:]
        var checksByService: [String: [CheckResult]] = [:]

        let internetStatus: Status
        if let baseline = config.baselineService, let checks = byService[baseline.id] {
            let info = Classifier.classify(config: config, service: baseline,
                                           checks: checks, internetStatus: .ok)
            infos[baseline.id] = info
            checksByService[baseline.id] = checks
            internetStatus = info.status
        } else {
            internetStatus = .unknown
        }

        for service in config.services where service.id != AppConfig.baselineServiceId {
            let checks = byService[service.id] ?? []
            infos[service.id] = Classifier.classify(config: config, service: service,
                                                    checks: checks, internetStatus: internetStatus)
            checksByService[service.id] = checks
        }

        for service in config.services {
            guard let info = infos[service.id] else { continue }
            let checks = checksByService[service.id] ?? []
            let previousStatus = previous[service.id]

            lastResults[service.id] = ServiceResultData(
                status: info.status,
                label: info.label,
                reason: info.reason,
                hint: info.hint,
                latencyMs: info.latencyMs,
                checksOk: info.checksOk,
                checksTotal: info.checksTotal,
                checks: checks
            )
            appendSeriesPoint(serviceId: service.id, status: info.status,
                              latencyMs: info.latencyMs, now: now)

            if let previousStatus, previousStatus != info.status {
                appendEvent(service: service, from: previousStatus, to: info.status,
                            reason: info.reason, now: now)
            }
            updateIncident(service: service, info: info, checks: checks, now: now)
        }

        pruneSeries(now: now)
        pruneIncidents()
        pruneEvents()
        version += 1
        rounds += 1
        lastRoundEpoch = now

        return RoundOutcome(previousStatuses: previous, infos: infos,
                            statuses: statusSnapshot())
    }

    // MARK: Серия

    private func appendSeriesPoint(serviceId: String, status: Status,
                                   latencyMs: Int?, now: Date) {
        var points = series[serviceId] ?? []
        points.append(SeriesPoint(t: Int(now.timeIntervalSince1970),
                                  status: status, latencyMs: latencyMs))
        if points.count > config.history.maxPoints {
            points.removeFirst(points.count - config.history.maxPoints)
        }
        let cutoff = Int(now.addingTimeInterval(
            -TimeInterval(config.history.maxAgeHours * 3600)).timeIntervalSince1970)
        points.removeAll { $0.t < cutoff }
        series[serviceId] = points
    }

    private func pruneSeries(now: Date) {
        let cutoff = Int(now.addingTimeInterval(
            -TimeInterval(config.history.maxAgeHours * 3600)).timeIntervalSince1970)
        for service in config.services {
            var points = series[service.id] ?? []
            points.removeAll { $0.t < cutoff }
            if points.count > config.history.maxPoints {
                points.removeFirst(points.count - config.history.maxPoints)
            }
            series[service.id] = points
        }
    }

    /// Доля успешных точек серии с момента `since` (0...100, один знак).
    func uptime(serviceId: String, since: Date) -> Double {
        let threshold = Int(since.timeIntervalSince1970)
        let points = (series[serviceId] ?? []).filter { $0.t >= threshold }
        guard !points.isEmpty else { return 0 }
        let okCount = points.filter { $0.status == .ok }.count
        return (Double(okCount) / Double(points.count) * 1000).rounded() / 10
    }

    // MARK: События

    private func appendEvent(service: ServiceConfig, from: Status, to: Status,
                             reason: String, now: Date) {
        let event = MonitorEvent(
            id: idGenerator(),
            serviceId: service.id,
            serviceName: service.name,
            status: to,
            title: "\(service.name): \(config.label(for: from)) → \(config.label(for: to))",
            detail: reason,
            at: now
        )
        events.append(event)
    }

    private func pruneEvents() {
        if events.count > Self.maxEvents {
            events.removeFirst(events.count - Self.maxEvents)
        }
    }

    // MARK: Инциденты

    func activeIncident(serviceId: String) -> Incident? {
        incidents.reversed().first { $0.serviceId == serviceId && $0.status == .active }
    }

    func lastIncident(serviceId: String) -> Incident? {
        incidents.filter { $0.serviceId == serviceId }.max { $0.startedAt < $1.startedAt }
    }

    private func updateIncident(service: ServiceConfig, info: ServiceInfo,
                                checks: [CheckResult], now: Date) {
        guard info.status != .unknown else { return }
        var streak = streaks[service.id] ?? Streak()
        let active = activeIncident(serviceId: service.id)

        if info.status == .ok {
            streak.fail = 0
            streak.ok += 1
            if let active, streak.ok >= config.incidents.closeAfterSuccesses {
                resolveIncident(active, now: now)
            }
            streak.firstFail = nil
        } else {
            streak.fail += 1
            streak.ok = 0
            if streak.firstFail == nil { streak.firstFail = now }
            if active == nil && streak.fail >= config.incidents.openAfterFailures {
                openIncident(service: service, info: info, checks: checks,
                             startedAt: streak.firstFail ?? now)
            }
        }
        streaks[service.id] = streak
    }

    private func openIncident(service: ServiceConfig, info: ServiceInfo,
                              checks: [CheckResult], startedAt: Date) {
        let core = checks.filter(\.core).isEmpty ? checks : checks.filter(\.core)
        var title = service.incidentTitle
        if info.status == .degraded {
            title = "\(service.name): нестабильная работа"
        } else if info.status == .blocked {
            title = "\(service.name): похоже на блокировку"
        }
        let incident = Incident(
            id: idGenerator(),
            serviceId: service.id,
            serviceName: service.name,
            title: title,
            reason: info.reason,
            status: .active,
            startedAt: startedAt,
            endedAt: nil,
            failedChecks: core.filter { !$0.ok }.count,
            totalChecks: core.count,
            serviceStatus: info.status
        )
        incidents.append(incident)
    }

    private func resolveIncident(_ incident: Incident, now: Date) {
        guard let index = incidents.firstIndex(where: { $0.id == incident.id }) else { return }
        incidents[index].status = .resolved
        incidents[index].endedAt = now
    }

    private func pruneIncidents() {
        let limit = max(1, config.incidents.maxStored)
        if incidents.count > limit {
            incidents.sort { $0.startedAt < $1.startedAt }
            incidents.removeFirst(incidents.count - limit)
        }
    }

    /// Инциденты по убыванию времени начала.
    var incidentsNewestFirst: [Incident] {
        incidents.sorted { $0.startedAt > $1.startedAt }
    }

    var eventsNewestFirst: [MonitorEvent] {
        events.sorted { $0.at > $1.at }
    }

    /// Фильтрация журнала (поиск по сервису/заголовку/причине).
    func incidents(matching query: String, serviceId: String? = nil,
                   status: IncidentStatus? = nil, limit: Int = 500) -> [Incident] {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let filtered = incidents.filter { incident in
            if let serviceId, incident.serviceId != serviceId { return false }
            if let status, incident.status != status { return false }
            if !needle.isEmpty {
                let haystack = "\(incident.serviceName) \(incident.title) \(incident.reason)".lowercased()
                if !haystack.contains(needle) { return false }
            }
            return true
        }
        return Array(filtered.sorted { $0.startedAt > $1.startedAt }.prefix(max(1, limit)))
    }

    // MARK: Снимок для UI

    func snapshot(checking: Bool, nextCheckIn: TimeInterval) -> Snapshot {
        let now = clock()
        var services: [ServiceSnapshot] = []
        var latencies: [Int] = []

        for service in config.services {
            let info = lastResults[service.id]
            let status = info?.status ?? .unknown
            if let latency = info?.latencyMs { latencies.append(latency) }
            let points = series[service.id] ?? []
            let seriesLimit = max(1, config.history.seriesPoints)
            let points24h = now.addingTimeInterval(-TimeInterval(config.history.maxAgeHours * 3600))
            services.append(ServiceSnapshot(
                id: service.id,
                name: service.name,
                subtitle: service.subtitle,
                icon: service.icon,
                accent: service.accent,
                status: status,
                statusLabel: info?.label ?? config.label(for: status),
                statusReason: info?.reason ?? "Проверки ещё не выполнялись",
                hint: info?.hint,
                latencyMs: info?.latencyMs,
                uptime1h: uptime(serviceId: service.id, since: now.addingTimeInterval(-3600)),
                uptime24h: uptime(serviceId: service.id, since: points24h),
                checksOk: info?.checksOk ?? 0,
                checksTotal: info?.checksTotal ?? 0,
                checks: info?.checks ?? [],
                series: Array(points.suffix(seriesLimit)),
                activeIncident: activeIncident(serviceId: service.id),
                lastIncident: lastIncident(serviceId: service.id)
            ))
        }

        let internetLabels: [Status: String] = [
            .ok: "Интернет работает",
            .degraded: "Интернет нестабилен",
            .down: "Похоже, интернета нет",
            .unknown: "Проверка не выполнялась",
        ]

        let internet: InternetSummary
        if let baseline = services.first(where: { $0.id == AppConfig.baselineServiceId }) {
            let mapped: Status
            switch baseline.status {
            case .ok: mapped = .ok
            case .degraded: mapped = .degraded
            case .unknown: mapped = .unknown
            default: mapped = .down
            }
            internet = InternetSummary(
                status: mapped,
                label: internetLabels[mapped] ?? config.label(for: mapped),
                latencyMs: baseline.latencyMs
            )
        } else {
            internet = InternetSummary(status: .unknown, label: internetLabels[.unknown] ?? "—",
                                       latencyMs: nil)
        }

        let statuses = services.map(\.status)
        let affected = statuses.filter(\.isProblem).count
        let servicesOk = statuses.filter { $0 == .ok }.count
        let overall: Status
        if statuses.allSatisfy({ $0 == .unknown }) {
            overall = .unknown
        } else if internet.status == .down {
            overall = .down
        } else if affected > 0 {
            overall = .degraded
        } else {
            overall = .ok
        }
        let overallLabels: [Status: String] = [
            .ok: "Всё работает",
            .degraded: "Есть проблемы",
            .down: "Нет доступа в интернет",
            .blocked: "Есть блокировки",
            .unknown: "Нет данных",
        ]

        let checksTotal = services.reduce(0) { $0 + $1.checksTotal }
        let checksOk = services.reduce(0) { $0 + $1.checksOk }
        let summary = SnapshotSummary(
            overall: overall,
            overallLabel: overallLabels[overall] ?? config.label(for: overall),
            servicesTotal: services.count,
            servicesOk: servicesOk,
            servicesAffected: affected,
            activeIncidents: incidents.filter { $0.status == .active }.count,
            avgLatencyMs: latencies.isEmpty
                ? nil
                : Int((Double(latencies.reduce(0, +)) / Double(latencies.count)).rounded()),
            checksTotal: checksTotal,
            checksOk: checksOk
        )

        return Snapshot(
            generatedAt: now,
            version: version,
            pollInterval: config.polling.intervalSec,
            nextCheckIn: nextCheckIn,
            checking: checking,
            internet: internet,
            summary: summary,
            services: services,
            recentIncidents: Array(incidentsNewestFirst.prefix(5))
        )
    }

    // MARK: Персистентность

    var persistence: StatePersistence {
        StatePersistence(series: series, incidents: incidents, events: events,
                         streaks: streaks, version: version, rounds: rounds,
                         lastRoundEpoch: lastRoundEpoch)
    }

    func restore(_ state: StatePersistence) {
        series = state.series
        incidents = state.incidents
        events = state.events
        streaks = state.streaks
        version = state.version
        rounds = state.rounds
        lastRoundEpoch = state.lastRoundEpoch
        bootstrap()
    }

    /// Полный сброс накопленных данных (кнопка «Очистить историю»).
    func reset() {
        series.removeAll()
        incidents.removeAll()
        events.removeAll()
        streaks.removeAll()
        lastResults.removeAll()
        version = 0
        rounds = 0
        lastRoundEpoch = nil
        nextRoundEpoch = nil
        bootstrap()
    }

    static func makeId() -> String {
        String(UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(12)).lowercased()
    }
}
