import Foundation

/// Результат классификации сервиса (одна карточка на экране «Статус»).
struct ServiceInfo: Sendable, Equatable {
    var status: Status
    var label: String
    var reason: String
    var hint: String?
    var latencyMs: Int?
    var checksOk: Int
    var checksTotal: Int
}

/// Классификация состояния сервиса — построчный порт `Monitor._classify()`
/// из server.py (и Android-версии Classifier.kt).
///
/// Правила:
///  * все core-проверки ok → ok; если максимальная задержка core ≥ 4000 мс → degraded;
///  * часть core ok → degraded (для контрольного канала «интернет» — тоже degraded);
///  * все core fail, но интернет down → down;
///  * все core fail, ошибки только DNS → blocked;
///  * все core fail, ошибки из BLOCK_ERRORS → blocked;
///  * иначе → down;
///  * нет проверок → unknown.
enum Classifier {
    /// Порог «медленного» ответа для core-проверок (как SLOW_LATENCY_MS в server.py).
    static let slowCoreThresholdMs = 4000

    private static let baselineLabels: [Status: String] = [
        .ok: "Интернет работает",
        .degraded: "Интернет нестабилен",
        .down: "Нет интернета",
    ]

    static func classify(config: AppConfig,
                         service: ServiceConfig,
                         checks: [CheckResult],
                         internetStatus: Status) -> ServiceInfo {
        let baseline = service.id == AppConfig.baselineServiceId
        let core = checks.filter(\.core).isEmpty ? checks : checks.filter(\.core)
        let total = core.count
        let ok = core.filter(\.ok)
        let bad = core.filter { !$0.ok }

        // В карточке показываем все проверки, а не только core — как на дашборде.
        let allTotal = checks.count
        let allOk = checks.filter(\.ok).count

        // «Отклик» сервиса — только по успешным core-проверкам.
        let coreLatencies = ok.map(\.latencyMs)
        let latency: Int? = coreLatencies.isEmpty
            ? nil
            : Int((Double(coreLatencies.reduce(0, +)) / Double(coreLatencies.count)).rounded())
        let maxLatency = coreLatencies.max()

        func label(for status: Status) -> String {
            if baseline, let baselineLabel = baselineLabels[status] {
                return baselineLabel
            }
            return config.label(for: status)
        }

        func result(_ status: Status,
                    _ reason: String,
                    hint: String?,
                    latency latencyValue: Int? = latency,
                    okCount: Int? = nil,
                    totalCount: Int? = nil) -> ServiceInfo {
            ServiceInfo(
                status: status,
                label: label(for: status),
                reason: reason,
                hint: hint,
                latencyMs: latencyValue,
                checksOk: okCount ?? allOk,
                checksTotal: totalCount ?? allTotal
            )
        }

        if total == 0 {
            return result(.unknown, "Проверки ещё не выполнялись", hint: nil,
                          latency: nil, okCount: 0, totalCount: 0)
        }

        if ok.count == total {
            if let maxLatency, maxLatency >= slowCoreThresholdMs {
                return result(.degraded, "Сервис отвечает медленно: \(maxLatency) мс",
                              hint: service.hints["degraded"])
            }
            let reason = baseline ? "Базовые проверки проходят" : "Все проверки проходят"
            return result(.ok, reason, hint: nil)
        }

        guard let first = bad.first else {
            return result(.unknown, "Проверки ещё не выполнялись", hint: nil, latency: nil)
        }

        if baseline {
            if !ok.isEmpty {
                return result(.degraded,
                              "Не проходят \(bad.count) из \(total) проверок: \(first.reasonText)",
                              hint: service.hints["degraded"])
            }
            return result(.down,
                          "Базовые проверки не проходят: \(first.reasonText)",
                          hint: service.hints["down"])
        }

        if !ok.isEmpty {
            return result(.degraded,
                          "Не проходят \(bad.count) из \(total) проверок: \(first.reasonText)",
                          hint: service.hints["degraded"])
        }

        if internetStatus == .down {
            return result(.down,
                          "Нет доступа в интернет — проверка сервиса невозможна",
                          hint: service.hints["down"])
        }

        let errors = Set(bad.compactMap(\.error).filter { !$0.isEmpty })
        if !errors.isEmpty && errors.isSubset(of: ErrorCodes.dnsErrors) {
            return result(.blocked,
                          "DNS не разрешает адрес сервиса при работающем интернете: \(first.target)",
                          hint: service.hints["blocked"])
        }
        if !errors.isDisjoint(with: ErrorCodes.blockErrors) {
            return result(.blocked,
                          "\(first.reasonText). Похоже на фильтрацию трафика",
                          hint: service.hints["blocked"])
        }
        return result(.down, first.reasonText, hint: service.hints["down"])
    }
}
