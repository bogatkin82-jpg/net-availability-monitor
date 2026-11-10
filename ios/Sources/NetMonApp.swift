import SwiftUI

/// Точка входа приложения NetMonitor.
///
/// Тёмная/светлая тема — системная (адаптивные цвета), без принудительной схемы.
@main
struct NetMonApp: App {
    @StateObject private var store = MonitorStore()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(store)
        }
    }
}
