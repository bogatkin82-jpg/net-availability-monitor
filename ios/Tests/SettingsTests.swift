import Foundation
import XCTest
@testable import NetMon

/// Пользовательские настройки: дефолты из config.json, ограничения,
/// эффективный конфиг и сохранение в UserDefaults.
final class SettingsTests: XCTestCase {
    private let config = AppConfig.defaults
    private let suiteName = "ru.netmon.ios.tests.settings"

    override func tearDown() {
        UserDefaults(suiteName: suiteName)?.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    func testDefaultsComeFromConfig() {
        let settings = UserSettings.defaults(from: config)
        XCTAssertEqual(settings.intervalSec, 20)
        XCTAssertEqual(settings.checkTimeoutSec, 8)
        XCTAssertEqual(settings.dnsTimeoutSec, 4)
        XCTAssertEqual(settings.openAfterFailures, 2)
        XCTAssertEqual(settings.closeAfterSuccesses, 1)
        XCTAssertEqual(settings.maxStored, 500)
        XCTAssertEqual(settings.notificationsEnabled, true)
        XCTAssertEqual(settings.notifyOn, ["blocked", "down"])
        XCTAssertEqual(UserSettings.minIntervalSec, 5)
    }

    func testClamping() {
        var raw = UserSettings(intervalSec: 1,
                               checkTimeoutSec: 0,
                               dnsTimeoutSec: 0,
                               openAfterFailures: 0,
                               closeAfterSuccesses: 0,
                               maxStored: 1_000_000,
                               notificationsEnabled: true,
                               notifyOn: ["down", "bogus"])
        let clamped = raw.clamped()
        XCTAssertEqual(clamped.intervalSec, UserSettings.minIntervalSec)
        XCTAssertEqual(clamped.checkTimeoutSec, 1)
        XCTAssertEqual(clamped.dnsTimeoutSec, 1)
        XCTAssertEqual(clamped.openAfterFailures, 1)
        XCTAssertEqual(clamped.closeAfterSuccesses, 1)
        XCTAssertEqual(clamped.maxStored, 5000)
        XCTAssertEqual(clamped.notifyOn, ["down"])

        raw.intervalSec = 10_000
        XCTAssertEqual(raw.clamped().intervalSec, UserSettings.maxIntervalSec)
    }

    func testEffectiveConfigAppliesUserValues() {
        let settings = UserSettings(intervalSec: 45,
                                    checkTimeoutSec: 10,
                                    dnsTimeoutSec: 6,
                                    openAfterFailures: 3,
                                    closeAfterSuccesses: 2,
                                    maxStored: 100,
                                    notificationsEnabled: false,
                                    notifyOn: ["down", "blocked"])
        let effective = settings.effectiveConfig(base: config)
        XCTAssertEqual(effective.polling.intervalSec, 45)
        XCTAssertEqual(effective.polling.checkTimeoutSec, 10)
        XCTAssertEqual(effective.polling.dnsTimeoutSec, 6)
        XCTAssertEqual(effective.incidents.openAfterFailures, 3)
        XCTAssertEqual(effective.incidents.closeAfterSuccesses, 2)
        XCTAssertEqual(effective.incidents.maxStored, 100)
        XCTAssertEqual(effective.notifications.enabled, false)
        // notify_on нормализуется в порядке статусов config.json.
        XCTAssertEqual(effective.notifications.notifyOn, ["blocked", "down"])
        // База не мутируется (value type).
        XCTAssertEqual(config.polling.intervalSec, 20)
        // Сервисы и подписи остаются из config.json.
        XCTAssertEqual(effective.services.count, 3)
        XCTAssertEqual(effective.label(for: .blocked), "Похоже на блокировку")
    }

    func testCheckOptionsOverrideOnlyWhenChanged() {
        let defaults = UserSettings.defaults(from: config)
        XCTAssertNil(defaults.checkOptions(config: config).checkTimeoutSec,
                     "если таймаут не меняли, движок берёт per-check timeout")
        XCTAssertEqual(defaults.checkOptions(config: config).dnsTimeoutSec, 4)

        var changed = defaults
        changed.checkTimeoutSec = 12
        changed.dnsTimeoutSec = 3
        let options = changed.checkOptions(config: config)
        XCTAssertEqual(options.checkTimeoutSec, 12)
        XCTAssertEqual(options.dnsTimeoutSec, 3)
    }

    func testNotifyOnStatusesAreOrdered() {
        var settings = UserSettings.defaults(from: config)
        settings.notifyOn = ["down", "blocked"]
        XCTAssertEqual(settings.notifyOnStatuses, [.blocked, .down])
    }

    func testUserDefaultsPersistence() {
        guard let store = UserDefaults(suiteName: suiteName) else {
            return XCTFail("не удалось создать UserDefaults(suiteName:)")
        }
        store.removePersistentDomain(forName: suiteName)

        let settings = UserSettings(intervalSec: 30,
                                    checkTimeoutSec: 9,
                                    dnsTimeoutSec: 5,
                                    openAfterFailures: 4,
                                    closeAfterSuccesses: 2,
                                    maxStored: 200,
                                    notificationsEnabled: false,
                                    notifyOn: ["blocked"])
        SettingsStore.save(settings, store: store)
        XCTAssertEqual(SettingsStore.load(fallback: UserSettings.defaults(from: config), store: store),
                       settings)

        SettingsStore.clear(store: store)
        XCTAssertEqual(SettingsStore.load(fallback: UserSettings.defaults(from: config), store: store),
                       UserSettings.defaults(from: config))
    }

    func testBrokenPersistenceFallsBackToDefaults() {
        guard let store = UserDefaults(suiteName: suiteName) else {
            return XCTFail("не удалось создать UserDefaults(suiteName:)")
        }
        store.removePersistentDomain(forName: suiteName)
        store.set(Data("не json".utf8), forKey: SettingsStore.key)
        XCTAssertEqual(SettingsStore.load(fallback: UserSettings.defaults(from: config), store: store),
                       UserSettings.defaults(from: config))
    }
}
