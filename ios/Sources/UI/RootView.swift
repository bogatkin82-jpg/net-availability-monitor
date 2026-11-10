import SwiftUI

/// Корневой экран: таб-бар «Статус» / «Журнал» / «Настройки».
struct RootView: View {
    @EnvironmentObject private var store: MonitorStore
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        TabView {
            StatusScreen()
                .tabItem {
                    Label("Статус", systemImage: "shield.lefthalf.filled")
                }
            JournalScreen()
                .tabItem {
                    Label("Журнал", systemImage: "text.book.closed")
                }
            SettingsScreen()
                .tabItem {
                    Label("Настройки", systemImage: "gearshape")
                }
        }
        .tint(Theme.accent)
        .task {
            await store.start()
        }
        .onChange(of: scenePhase) { _, phase in
            store.handleScenePhase(phase)
        }
    }
}
