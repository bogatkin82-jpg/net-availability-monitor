import Foundation
import XCTest
@testable import NetMon

/// Общие фикстуры для unit-тестов (без сетевых вызовов).
enum TestFixtures {
    /// Конфиг как config.json, но с управляемыми порогами инцидентов.
    static func config(openAfter: Int = 2,
                       closeAfter: Int = 1,
                       maxStored: Int = 500) -> AppConfig {
        var config = AppConfig.defaults
        config.incidents = IncidentsConfig(
            openAfterFailures: openAfter,
            closeAfterSuccesses: closeAfter,
            maxStored: maxStored
        )
        return config
    }

    static func service(_ config: AppConfig, _ id: String) -> ServiceConfig {
        guard let service = config.service(id: id) else {
            fatalError("в конфиге нет сервиса \(id)")
        }
        return service
    }

    static func check(_ id: String,
                      kind: CheckKind = .https,
                      core: Bool = false,
                      ok: Bool,
                      error: String? = nil,
                      latency: Int = 100,
                      stage: String = "http",
                      label: String? = nil,
                      target: String = "example.com") -> CheckResult {
        CheckResult(
            id: id,
            label: label ?? id,
            target: target,
            kind: kind,
            core: core,
            ok: ok,
            latencyMs: latency,
            stage: stage,
            error: error,
            errorText: error == nil ? nil : "тест: \(error ?? "")",
            httpStatus: ok ? 204 : nil,
            ip: "1.2.3.4",
            timings: StageTimings(dnsMs: 5, tcpMs: 10, tlsMs: 20, httpMs: latency),
            measuredAt: Date(timeIntervalSince1970: 1_700_000_000)
        )
    }

    /// Результаты для всех проверок сервиса: core-проверки успешны или нет.
    static func results(for service: ServiceConfig,
                        coreOk: Bool,
                        nonCoreOk: Bool = true,
                        error: String = ErrorCodes.connectTimeout) -> [CheckResult] {
        service.checks.map { spec in
            let ok = spec.core ? coreOk : nonCoreOk
            return check(spec.id,
                         kind: spec.kind,
                         core: spec.core,
                         ok: ok,
                         error: ok ? nil : error,
                         latency: ok ? 100 : 500,
                         label: spec.label,
                         target: spec.target)
        }
    }

    /// Все сервисы здоровы (для раундов MonitorState).
    static func allOk(_ config: AppConfig) -> [String: [CheckResult]] {
        var results: [String: [CheckResult]] = [:]
        for service in config.services {
            results[service.id] = self.results(for: service, coreOk: true)
        }
        return results
    }

    /// Заменяет результаты одного сервиса.
    static func replacing(_ base: [String: [CheckResult]],
                          _ serviceId: String,
                          _ checks: [CheckResult]) -> [String: [CheckResult]] {
        var results = base
        results[serviceId] = checks
        return results
    }
}

/// Управляемые часы для детерминированных тестов инцидентов/uptime.
final class TestClock {
    var now = Date(timeIntervalSince1970: 1_700_000_000)

    func advance(_ seconds: TimeInterval) {
        now = now.addingTimeInterval(seconds)
    }
}
