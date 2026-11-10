import Foundation
import XCTest
@testable import NetMon

/// Классификатор статусов — паритет с Monitor._classify() из server.py.
final class ClassifierTests: XCTestCase {
    private let config = AppConfig.defaults

    private func classify(_ checks: [CheckResult],
                          serviceId: String = "youtube",
                          internet: Status = .ok) -> ServiceInfo {
        Classifier.classify(
            config: config,
            service: TestFixtures.service(config, serviceId),
            checks: checks,
            internetStatus: internet
        )
    }

    private func makeChecks(_ serviceId: String,
                            coreOk: Bool,
                            nonCoreOk: Bool = true,
                            error: String = ErrorCodes.connectTimeout,
                            coreLatency: Int = 100) -> [CheckResult] {
        let service = TestFixtures.service(config, serviceId)
        return service.checks.map { spec in
            if spec.core {
                return TestFixtures.check(spec.id, kind: spec.kind, core: true, ok: coreOk,
                                          error: coreOk ? nil : error,
                                          latency: coreOk ? coreLatency : 500,
                                          label: spec.label, target: spec.target)
            }
            return TestFixtures.check(spec.id, kind: spec.kind, core: false, ok: nonCoreOk,
                                      error: nonCoreOk ? nil : error,
                                      latency: 50, label: spec.label, target: spec.target)
        }
    }

    func testAllCoreOkGivesOk() {
        let info = classify(makeChecks("youtube", coreOk: true))
        XCTAssertEqual(info.status, .ok)
        XCTAssertEqual(info.label, "Доступен")
        XCTAssertEqual(info.reason, "Все проверки проходят")
        XCTAssertNil(info.hint)
        XCTAssertEqual(info.checksOk, 5)
        XCTAssertEqual(info.checksTotal, 5)
        // «Отклик» считается только по успешным core-проверкам.
        XCTAssertEqual(info.latencyMs, 100)
    }

    func testSlowCoreLatencyDegrades() {
        let info = classify(makeChecks("youtube", coreOk: true, coreLatency: 4_500))
        XCTAssertEqual(info.status, .degraded)
        XCTAssertEqual(info.label, "Нестабильно")
        XCTAssertTrue(info.reason.contains("4500"), "причина: \(info.reason)")
        XCTAssertEqual(info.hint, TestFixtures.service(config, "youtube").hints["degraded"])
    }

    func testAllCoreFailWithHealthyNonCoreIsBlocked() {
        let info = classify(makeChecks("youtube", coreOk: false, nonCoreOk: true))
        XCTAssertEqual(info.status, .blocked) // все core упали, ошибка из BLOCK_ERRORS
        XCTAssertEqual(info.checksOk, 3)
        XCTAssertEqual(info.checksTotal, 5)
    }

    func testOneOfTwoCoreFailsIsDegraded() {
        let service = TestFixtures.service(config, "youtube")
        let coreSpecs = service.checks.filter(\.core)
        XCTAssertEqual(coreSpecs.count, 2)
        var checks = service.checks.map { spec in
            TestFixtures.check(spec.id, kind: spec.kind, core: spec.core, ok: true,
                               latency: 100, label: spec.label, target: spec.target)
        }
        if let index = checks.firstIndex(where: { $0.core }) {
            checks[index] = TestFixtures.check(checks[index].id, kind: checks[index].kind,
                                               core: true, ok: false,
                                               error: ErrorCodes.connectTimeout, latency: 800,
                                               label: checks[index].label, target: checks[index].target)
        }
        let info = classify(checks)
        XCTAssertEqual(info.status, .degraded)
        XCTAssertTrue(info.reason.contains("1 из 2"), "причина: \(info.reason)")
    }

    func testAllCoreFailWithInternetDownIsDown() {
        let info = classify(makeChecks("youtube", coreOk: false), internet: .down)
        XCTAssertEqual(info.status, .down)
        XCTAssertEqual(info.label, "Недоступен")
        XCTAssertTrue(info.reason.contains("Нет доступа в интернет"), "причина: \(info.reason)")
    }

    func testDnsOnlyFailureIsBlocked() {
        let info = classify(makeChecks("youtube", coreOk: false, error: ErrorCodes.dnsError))
        XCTAssertEqual(info.status, .blocked)
        XCTAssertEqual(info.label, "Похоже на блокировку")
        XCTAssertTrue(info.reason.contains("DNS"), "причина: \(info.reason)")
    }

    func testTlsResetIsBlocked() {
        let info = classify(makeChecks("telegram", coreOk: false, error: ErrorCodes.tlsReset))
        XCTAssertEqual(info.status, .blocked)
        XCTAssertTrue(info.reason.contains("фильтрацию"), "причина: \(info.reason)")
        XCTAssertEqual(info.hint, TestFixtures.service(config, "telegram").hints["blocked"])
    }

    func testNonBlockErrorIsDown() {
        let info = classify(makeChecks("youtube", coreOk: false,
                                       error: ErrorCodes.httpStatusUnexpected))
        XCTAssertEqual(info.status, .down)
        XCTAssertEqual(info.hint, TestFixtures.service(config, "youtube").hints["down"])
    }

    func testBaselineDown() {
        let info = classify(makeChecks("internet", coreOk: false), serviceId: "internet")
        XCTAssertEqual(info.status, .down)
        XCTAssertEqual(info.label, "Нет интернета")
        XCTAssertTrue(info.reason.contains("Базовые проверки не проходят"), "причина: \(info.reason)")
    }

    func testBaselinePartiallyOkIsDegraded() {
        let service = TestFixtures.service(config, "internet")
        var checks = service.checks.map { spec in
            TestFixtures.check(spec.id, kind: spec.kind, core: spec.core, ok: true,
                               latency: 100, label: spec.label, target: spec.target)
        }
        if let index = checks.firstIndex(where: { $0.core }) {
            checks[index] = TestFixtures.check(checks[index].id, kind: checks[index].kind,
                                               core: true, ok: false,
                                               error: ErrorCodes.connectTimeout, latency: 900,
                                               label: checks[index].label,
                                               target: checks[index].target)
        }
        let info = classify(checks, serviceId: "internet")
        XCTAssertEqual(info.status, .degraded)
        XCTAssertEqual(info.label, "Интернет нестабилен")
    }

    func testBaselineAllOk() {
        let info = classify(makeChecks("internet", coreOk: true), serviceId: "internet")
        XCTAssertEqual(info.status, .ok)
        XCTAssertEqual(info.label, "Интернет работает")
        XCTAssertEqual(info.reason, "Базовые проверки проходят")
    }

    func testNoChecksIsUnknown() {
        let info = classify([], serviceId: "youtube")
        XCTAssertEqual(info.status, .unknown)
        XCTAssertEqual(info.label, "Нет данных")
        XCTAssertEqual(info.reason, "Проверки ещё не выполнялись")
        XCTAssertEqual(info.checksTotal, 0)
        XCTAssertEqual(info.checksOk, 0)
        XCTAssertNil(info.latencyMs)
    }
}
