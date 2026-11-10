import Foundation
import XCTest
@testable import NetMon

/// Инциденты, события, серии, uptime и снимок состояния — порт server.py.
final class IncidentsTests: XCTestCase {
    private var config = AppConfig.defaults
    private var clock = TestClock()
    private var state: MonitorState!

    override func setUp() {
        super.setUp()
        config = TestFixtures.config(openAfter: 2, closeAfter: 1, maxStored: 500)
        clock = TestClock()
        state = MonitorState(config: config,
                             clock: { [clock] in clock.now },
                             idGenerator: { "test-id" })
    }

    private func failing(_ serviceId: String,
                         error: String = ErrorCodes.connectTimeout) -> [CheckResult] {
        TestFixtures.results(for: TestFixtures.service(config, serviceId),
                             coreOk: false, nonCoreOk: true, error: error)
    }

    private func healthy(_ serviceId: String) -> [CheckResult] {
        TestFixtures.results(for: TestFixtures.service(config, serviceId), coreOk: true)
    }

    func testIncidentOpensAfterConfiguredFailures() {
        let round1 = TestFixtures.replacing(TestFixtures.allOk(config), "youtube", failing("youtube"))
        state.applyRound(byService: round1)
        XCTAssertEqual(state.incidents.count, 0, "после одного сбоя инцидент ещё не должен открываться")
        XCTAssertEqual(state.lastResults["youtube"]?.status, .blocked)

        clock.advance(21)
        state.applyRound(byService: round1)
        XCTAssertEqual(state.incidents.count, 1)
        let incident = state.incidents[0]
        XCTAssertEqual(incident.serviceId, "youtube")
        XCTAssertEqual(incident.serviceName, "YouTube")
        XCTAssertEqual(incident.status, .active)
        XCTAssertEqual(incident.serviceStatus, .blocked)
        XCTAssertEqual(incident.title, "YouTube: похоже на блокировку")
        XCTAssertEqual(incident.failedChecks, 2)
        XCTAssertEqual(incident.totalChecks, 2)
        XCTAssertTrue(incident.reason.contains("фильтрацию"), "причина: \(incident.reason)")
        // Инцидент датируется первым сбоем серии (как first_fail в server.py).
        XCTAssertEqual(incident.startedAt, Date(timeIntervalSince1970: 1_700_000_000))
        XCTAssertNil(incident.endedAt)
        XCTAssertEqual(incident.duration(now: clock.now), 21)
        XCTAssertEqual(state.activeIncident(serviceId: "youtube")?.id, incident.id)
    }

    func testIncidentResolvesAfterSuccess() {
        let bad = TestFixtures.replacing(TestFixtures.allOk(config), "youtube", failing("youtube"))
        state.applyRound(byService: bad)
        clock.advance(21)
        state.applyRound(byService: bad)
        XCTAssertEqual(state.incidents.count, 1)

        clock.advance(21)
        state.applyRound(byService: TestFixtures.allOk(config))
        XCTAssertEqual(state.incidents[0].status, .resolved)
        XCTAssertEqual(state.incidents[0].endedAt, clock.now)
        XCTAssertEqual(state.incidents[0].duration(now: clock.now), 42)
        XCTAssertNil(state.activeIncident(serviceId: "youtube"))
    }

    func testCloseAfterSuccessesIsRespected() {
        config = TestFixtures.config(openAfter: 1, closeAfter: 2, maxStored: 500)
        state = MonitorState(config: config, clock: { [clock] in clock.now },
                             idGenerator: { UUID().uuidString })
        let bad = TestFixtures.replacing(TestFixtures.allOk(config), "youtube", failing("youtube"))
        state.applyRound(byService: bad)
        XCTAssertEqual(state.incidents.count, 1)

        clock.advance(21)
        state.applyRound(byService: TestFixtures.allOk(config))
        XCTAssertEqual(state.incidents[0].status, .active, "одного успеха мало при close_after_successes = 2")

        clock.advance(21)
        state.applyRound(byService: TestFixtures.allOk(config))
        XCTAssertEqual(state.incidents[0].status, .resolved)
    }

    func testUnknownStatusDoesNotOpenIncident() {
        var config = TestFixtures.config(openAfter: 1, closeAfter: 1, maxStored: 500)
        config.incidents.openAfterFailures = 1
        let state = MonitorState(config: config, clock: { [clock] in clock.now },
                                 idGenerator: { UUID().uuidString })
        var results = TestFixtures.allOk(config)
        results["youtube"] = []
        for _ in 0..<3 {
            state.applyRound(byService: results)
            clock.advance(21)
        }
        XCTAssertEqual(state.lastResults["youtube"]?.status, Status.unknown)
        XCTAssertEqual(state.incidents.count, 0)
    }

    func testStatusChangeIsRecordedAsEvent() {
        let bad = TestFixtures.replacing(TestFixtures.allOk(config), "youtube", failing("youtube"))
        state.applyRound(byService: bad)
        XCTAssertEqual(state.events.count, 0, "первый раунд не считается сменой статуса")

        clock.advance(21)
        state.applyRound(byService: bad)
        XCTAssertEqual(state.events.count, 0, "статус не изменился — события нет")

        clock.advance(21)
        state.applyRound(byService: TestFixtures.allOk(config))
        XCTAssertEqual(state.events.count, 1)
        let event = state.events[0]
        XCTAssertEqual(event.serviceId, "youtube")
        XCTAssertEqual(event.status, .ok)
        XCTAssertTrue(event.title.contains("Похоже на блокировку"), "заголовок: \(event.title)")
        XCTAssertTrue(event.title.contains("Доступен"), "заголовок: \(event.title)")
        XCTAssertEqual(event.at, clock.now)
    }

    func testIncidentsAreTrimmedToMaxStored() {
        config = TestFixtures.config(openAfter: 1, closeAfter: 1, maxStored: 1)
        state = MonitorState(config: config, clock: { [clock] in clock.now },
                             idGenerator: { UUID().uuidString })
        var results = TestFixtures.allOk(config)
        results["youtube"] = failing("youtube")
        results["telegram"] = failing("telegram")
        state.applyRound(byService: results)
        XCTAssertEqual(state.incidents.count, 1, "max_stored = 1 должен обрезать журнал")
    }

    func testUptimeCalculatedFromSeries() {
        state.applyRound(byService: TestFixtures.allOk(config))
        clock.advance(21)
        state.applyRound(byService: TestFixtures.replacing(TestFixtures.allOk(config),
                                                         "youtube", failing("youtube")))
        let since = clock.now.addingTimeInterval(-3600)
        XCTAssertEqual(state.uptime(serviceId: "youtube", since: since), 50.0, accuracy: 0.05)
        XCTAssertEqual(state.uptime(serviceId: "telegram", since: since), 100.0, accuracy: 0.05)
        XCTAssertEqual(state.uptime(serviceId: "youtube",
                                    since: clock.now.addingTimeInterval(60)), 0.0)
    }

    func testSeriesRespectsHistoryLimits() {
        config.history = HistoryConfig(maxAgeHours: 24, maxPoints: 3, seriesPoints: 2)
        state = MonitorState(config: config, clock: { [clock] in clock.now },
                             idGenerator: { UUID().uuidString })
        for _ in 0..<5 {
            state.applyRound(byService: TestFixtures.allOk(config))
            clock.advance(21)
        }
        XCTAssertLessThanOrEqual(state.series["youtube"]?.count ?? 0, 3)
        let snapshot = state.snapshot(checking: false, nextCheckIn: 0)
        XCTAssertLessThanOrEqual(snapshot.services.first { $0.id == "youtube" }?.series.count ?? 0, 2)
    }

    func testSnapshotOverallStatus() {
        var snapshot = state.snapshot(checking: false, nextCheckIn: 0)
        XCTAssertEqual(snapshot.summary.overall, .unknown)
        XCTAssertEqual(snapshot.summary.overallLabel, "Нет данных")
        XCTAssertEqual(snapshot.internet.status, .unknown)

        state.applyRound(byService: TestFixtures.allOk(config))
        snapshot = state.snapshot(checking: false, nextCheckIn: 0)
        XCTAssertEqual(snapshot.summary.overall, .ok)
        XCTAssertEqual(snapshot.summary.overallLabel, "Всё работает")
        XCTAssertEqual(snapshot.internet.label, "Интернет работает")
        XCTAssertEqual(snapshot.summary.servicesOk, 3)
        XCTAssertEqual(snapshot.summary.servicesAffected, 0)

        state.applyRound(byService: TestFixtures.replacing(TestFixtures.allOk(config),
                                                         "youtube", failing("youtube")))
        snapshot = state.snapshot(checking: false, nextCheckIn: 0)
        XCTAssertEqual(snapshot.summary.overall, .degraded)
        XCTAssertEqual(snapshot.summary.overallLabel, "Есть проблемы")
        XCTAssertEqual(snapshot.summary.servicesAffected, 1)

        var internetDown = TestFixtures.allOk(config)
        internetDown["internet"] = failing("internet")
        state.applyRound(byService: internetDown)
        snapshot = state.snapshot(checking: false, nextCheckIn: 0)
        XCTAssertEqual(snapshot.summary.overall, .down)
        XCTAssertEqual(snapshot.summary.overallLabel, "Нет доступа в интернет")
        XCTAssertEqual(snapshot.internet.status, .down)
    }

    func testPersistenceRoundTrip() throws {
        let bad = TestFixtures.replacing(TestFixtures.allOk(config), "youtube", failing("youtube"))
        state.applyRound(byService: bad)
        clock.advance(21)
        state.applyRound(byService: bad)

        let data = try JSONEncoder().encode(state.persistence)
        let restored = try JSONDecoder().decode(StatePersistence.self, from: data)
        let second = MonitorState(config: config, clock: { [clock] in clock.now },
                                  idGenerator: { UUID().uuidString })
        second.restore(restored)

        XCTAssertEqual(second.incidents.count, state.incidents.count)
        XCTAssertEqual(second.events.count, state.events.count)
        XCTAssertEqual(second.rounds, state.rounds)
        XCTAssertEqual(second.version, state.version)
        XCTAssertEqual(second.series["youtube"]?.count, state.series["youtube"]?.count)
        XCTAssertEqual(second.snapshot(checking: false, nextCheckIn: 0).summary.overall,
                       state.snapshot(checking: false, nextCheckIn: 0).summary.overall)
    }

    func testResetClearsEverything() {
        let bad = TestFixtures.replacing(TestFixtures.allOk(config), "youtube", failing("youtube"))
        state.applyRound(byService: bad)
        clock.advance(21)
        state.applyRound(byService: bad)
        XCTAssertFalse(state.incidents.isEmpty)

        state.reset()
        XCTAssertTrue(state.incidents.isEmpty)
        XCTAssertTrue(state.events.isEmpty)
        XCTAssertTrue(state.series.values.allSatisfy(\.isEmpty))
        XCTAssertEqual(state.version, 0)
        XCTAssertEqual(state.rounds, 0)
        XCTAssertEqual(state.snapshot(checking: false, nextCheckIn: 0).summary.overall, .unknown)
    }
}
