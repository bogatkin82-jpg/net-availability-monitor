import Foundation
import XCTest
@testable import NetMon

/// Паритет с корневым config.json и разбор конфига с дефолтами.
final class ConfigTests: XCTestCase {
    func testBundledConfigMatchesDefaults() {
        let loaded = ConfigLoader.load()
        XCTAssertEqual(loaded.source, .bundle,
                       "config.json должен лежать в бандле приложения (ios/Resources/config.json)")
        XCTAssertEqual(loaded.config, AppConfig.defaults,
                       "встроенные дефолты обязаны совпадать с config.json")
    }

    func testConfigStructure() {
        let config = AppConfig.defaults
        XCTAssertEqual(config.version, 1)
        XCTAssertEqual(config.server.host, "127.0.0.1")
        XCTAssertEqual(config.server.port, 8099)
        XCTAssertEqual(config.polling.intervalSec, 20)
        XCTAssertEqual(config.polling.checkTimeoutSec, 8)
        XCTAssertEqual(config.polling.dnsTimeoutSec, 4)
        XCTAssertEqual(config.incidents.openAfterFailures, 2)
        XCTAssertEqual(config.incidents.closeAfterSuccesses, 1)
        XCTAssertEqual(config.incidents.maxStored, 500)
        XCTAssertEqual(config.history.maxAgeHours, 24)
        XCTAssertEqual(config.history.maxPoints, 8640)
        XCTAssertEqual(config.history.seriesPoints, 180)
        XCTAssertEqual(config.network.proxy, "direct")
        XCTAssertEqual(config.services.count, 3)
        XCTAssertEqual(config.services.reduce(0) { $0 + $1.checks.count }, 16)
        XCTAssertEqual(config.services.map(\.id), ["youtube", "telegram", "internet"])
        XCTAssertEqual(config.notifications.enabled, true)
        XCTAssertEqual(config.notifications.notifyOn, ["blocked", "down"])
    }

    func testRussianLabelsAndColors() {
        let config = AppConfig.defaults
        XCTAssertEqual(config.label(for: .ok), "Доступен")
        XCTAssertEqual(config.label(for: .degraded), "Нестабильно")
        XCTAssertEqual(config.label(for: .blocked), "Похоже на блокировку")
        XCTAssertEqual(config.label(for: .down), "Недоступен")
        XCTAssertEqual(config.label(for: .unknown), "Нет данных")
        XCTAssertEqual(config.color(for: .ok), "#34c759")
        XCTAssertEqual(config.color(for: .degraded), "#ff9500")
        XCTAssertEqual(config.color(for: .blocked), "#ff3b30")
        XCTAssertEqual(config.color(for: .down), "#ff3b30")
        XCTAssertEqual(config.color(for: .unknown), "#8e8e93")
    }

    func testChecksParsedAsInServer() {
        let config = AppConfig.defaults
        let youtube = TestFixtures.service(config, "youtube")
        XCTAssertEqual(youtube.name, "YouTube")
        XCTAssertEqual(youtube.icon, "youtube")
        XCTAssertEqual(youtube.checks.count, 5)

        let https = youtube.checks[0]
        XCTAssertEqual(https.id, "yt_https")
        XCTAssertEqual(https.kind, .https)
        XCTAssertEqual(https.host, "www.youtube.com")
        XCTAssertEqual(https.port, 443)
        XCTAssertEqual(https.path, "/generate_204")
        XCTAssertEqual(https.expect, [204])
        XCTAssertTrue(https.core)
        XCTAssertEqual(https.timeoutSec, 8)
        XCTAssertEqual(https.target, "www.youtube.com/generate_204")

        let telegram = TestFixtures.service(config, "telegram")
        let dc2 = telegram.checks.first { $0.id == "tg_dc2" }
        XCTAssertNotNil(dc2)
        XCTAssertEqual(dc2?.kind, CheckKind.tcp)
        XCTAssertEqual(dc2?.host, "149.154.167.51")
        XCTAssertEqual(dc2?.port, 443)
        XCTAssertTrue(dc2?.core == true)
        XCTAssertEqual(dc2?.target, "149.154.167.51:443")

        let internet = TestFixtures.service(config, "internet")
        XCTAssertEqual(internet.id, AppConfig.baselineServiceId)
        XCTAssertEqual(internet.checks.first { $0.id == "inet_tcp" }?.host, "1.1.1.1")
        XCTAssertEqual(internet.checks.first { $0.id == "inet_dns" }?.kind, CheckKind.dns)
    }

    func testEmptyJSONFallsBackToDefaults() {
        let config = ConfigLoader.makeConfig(from: [:])
        XCTAssertEqual(config, AppConfig.defaults)
    }

    func testCustomValuesParsedWithDefaultsForMissingSections() throws {
        let json = """
        {
          "version": 1,
          "polling": {"interval_sec": 30, "check_timeout_sec": 6, "dns_timeout_sec": 2},
          "incidents": {"open_after_failures": 3, "close_after_successes": 2, "max_stored": 1000},
          "network": {"proxy": "system"},
          "status_labels": {"ok": "Работает"},
          "notifications": {"ios": {"enabled": false, "notify_on": ["down"]}},
          "services": [
            {
              "id": "youtube",
              "name": "YouTube",
              "subtitle": "тест",
              "icon": "youtube",
              "accent": "#ff0000",
              "incident_title": "YouTube недоступен",
              "hints": {"down": "подсказка"},
              "checks": [
                {"id": "yt", "label": "HTTPS", "kind": "https", "host": "youtube.com",
                 "port": 443, "path": "/", "expect": [200, 301], "core": true, "timeout": 5},
                {"id": "yt_bad", "label": "Неизвестный kind", "kind": "icmp", "host": "x"}
              ]
            }
          ]
        }
        """
        let config = try ConfigLoader.decode(Data(json.utf8))
        XCTAssertEqual(config.polling.intervalSec, 30)
        XCTAssertEqual(config.polling.checkTimeoutSec, 6)
        XCTAssertEqual(config.polling.dnsTimeoutSec, 2)
        XCTAssertEqual(config.incidents.openAfterFailures, 3)
        XCTAssertEqual(config.incidents.closeAfterSuccesses, 2)
        XCTAssertEqual(config.incidents.maxStored, 1000)
        XCTAssertEqual(config.network.proxy, "system")
        XCTAssertEqual(config.label(for: .ok), "Работает")
        // Остальные подписи — из дефолтов, чтобы UI не остался без текста.
        XCTAssertEqual(config.label(for: .blocked), "Похоже на блокировку")
        XCTAssertEqual(config.color(for: .ok), "#34c759")
        XCTAssertEqual(config.notifications.enabled, false)
        XCTAssertEqual(config.notifications.notifyOn, ["down"])
        XCTAssertEqual(config.services.count, 1)
        XCTAssertEqual(config.services[0].checks.count, 1, "проверка с неизвестным kind должна игнорироваться")
        let check = config.services[0].checks[0]
        XCTAssertEqual(check.expect, [200, 301])
        XCTAssertEqual(check.timeoutSec, 5)
        XCTAssertEqual(config.history.maxAgeHours, 24, "отсутствующая секция берётся из дефолтов")
    }

    func testInvalidJSONThrows() {
        XCTAssertThrowsError(try ConfigLoader.decode(Data("[1,2,3]".utf8)))
        XCTAssertThrowsError(try ConfigLoader.decode(Data("{".utf8)))
    }

    func testStatusIdentifiers() {
        XCTAssertEqual(Status.allCases.map(\.rawValue),
                       ["ok", "degraded", "blocked", "down", "unknown"])
        XCTAssertEqual(Status(rawValue: "blocked"), .blocked)
        XCTAssertNil(Status(rawValue: "unknown_status"))
        XCTAssertTrue(Status.degraded.isProblem)
        XCTAssertTrue(Status.blocked.isProblem)
        XCTAssertTrue(Status.down.isProblem)
        XCTAssertFalse(Status.ok.isProblem)
        XCTAssertFalse(Status.unknown.isProblem)
    }
}
