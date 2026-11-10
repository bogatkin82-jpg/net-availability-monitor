import Foundation
import UserNotifications

/// Локальные уведомления UNUserNotificationCenter.
///
/// iOS не даёт приложению работать в фоне как Android foreground service,
/// поэтому уведомления приходят только когда приложение активно (или сразу
/// после возврата в foreground) — см. docs/ios.md.
@MainActor
final class NotificationManager {
    static let shared = NotificationManager()

    private let center = UNUserNotificationCenter.current()
    private(set) var authorizationStatus: UNAuthorizationStatus = .notDetermined

    private init() {}

    func refreshAuthorization() async {
        let settings = await center.notificationSettings()
        authorizationStatus = settings.authorizationStatus
    }

    /// Запрос разрешения (системный диалог показывается один раз).
    @discardableResult
    func requestAuthorization() async -> Bool {
        do {
            let granted = try await center.requestAuthorization(options: [.alert, .sound, .badge])
            await refreshAuthorization()
            return granted
        } catch {
            await refreshAuthorization()
            return false
        }
    }

    /// Немедленная локальная доставка (trigger: nil).
    func post(title: String, body: String, sound: Bool = false) async {
        switch authorizationStatus {
        case .authorized, .provisional, .ephemeral:
            break
        default:
            return
        }
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        if sound {
            content.sound = .default
        }
        let request = UNNotificationRequest(identifier: UUID().uuidString,
                                            content: content,
                                            trigger: nil)
        do {
            try await center.add(request)
        } catch {
            // Уведомления не критичны для мониторинга — молча пропускаем.
        }
    }
}
