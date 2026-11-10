import SwiftUI
import UserNotifications

/// Экран «Настройки»: интервал, таймауты, пороги инцидентов, уведомления,
/// сброс к значениям config.json и очистка истории.
struct SettingsScreen: View {
    @EnvironmentObject private var store: MonitorStore
    @State private var showResetConfirm = false
    @State private var showClearConfirm = false

    var body: some View {
        NavigationStack {
            Form {
                pollingSection
                incidentsSection
                notificationsSection
                dataSection
                aboutSection
            }
            .navigationTitle("Настройки")
            .confirmationDialog(
                "Сбросить настройки к значениям config.json?",
                isPresented: $showResetConfirm,
                titleVisibility: .visible
            ) {
                Button("Сбросить", role: .destructive) {
                    store.resetSettings()
                }
                Button("Отмена", role: .cancel) {}
            } message: {
                Text("Интервал, таймауты, пороги инцидентов и уведомления вернутся к значениям из конфига.")
            }
            .confirmationDialog(
                "Очистить историю, серии и инциденты?",
                isPresented: $showClearConfirm,
                titleVisibility: .visible
            ) {
                Button("Очистить", role: .destructive) {
                    store.clearHistory()
                }
                Button("Отмена", role: .cancel) {}
            } message: {
                Text("Настройки останутся прежними, журнал и статистика будут удалены.")
            }
        }
    }

    // MARK: Секции

    private var pollingSection: some View {
        Section("Опрос") {
            Stepper(value: $store.settings.intervalSec,
                    in: UserSettings.minIntervalSec...UserSettings.maxIntervalSec,
                    step: 5) {
                settingRow("Интервал опроса", value: "\(store.settings.intervalSec) с")
            }
            Stepper(value: $store.settings.checkTimeoutSec, in: 1...60) {
                settingRow("Таймаут проверки", value: "\(store.settings.checkTimeoutSec) с")
            }
            Stepper(value: $store.settings.dnsTimeoutSec, in: 1...30) {
                settingRow("Таймаут DNS", value: "\(store.settings.dnsTimeoutSec) с")
            }
            Text("Интервал ограничен снизу \(UserSettings.minIntervalSec) с — как в ТЗ. Проверки идут параллельно и не блокируют интерфейс.")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    private var incidentsSection: some View {
        Section("Инциденты") {
            Stepper(value: $store.settings.openAfterFailures, in: 1...20) {
                settingRow("Открывать после", value: "\(store.settings.openAfterFailures) сбоев подряд")
            }
            Stepper(value: $store.settings.closeAfterSuccesses, in: 1...20) {
                settingRow("Закрывать после", value: "\(store.settings.closeAfterSuccesses) успехов подряд")
            }
            Stepper(value: $store.settings.maxStored, in: 10...5000, step: 10) {
                settingRow("Хранить инцидентов", value: "\(store.settings.maxStored)")
            }
        }
    }

    private var notificationsSection: some View {
        Section("Уведомления") {
            Toggle("Уведомлять о проблемах", isOn: $store.settings.notificationsEnabled)

            if store.settings.notificationsEnabled {
                ForEach([Status.blocked, Status.degraded, Status.down], id: \.rawValue) { status in
                    Toggle("Статус «\(store.config.label(for: status))»",
                           isOn: notifyBinding(status))
                }
            }

            HStack {
                Text("Разрешение iOS")
                Spacer()
                Text(permissionLabel)
                    .foregroundStyle(.secondary)
            }

            if store.notificationPermission == .notDetermined {
                Button("Запросить разрешение") {
                    Task { await store.requestNotificationPermission() }
                }
            } else if store.notificationPermission == .denied {
                Text("Уведомления запрещены в системных настройках — включите их для NetMonitor вручную.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            Text("iOS не выполняет фоновый опрос: уведомления приходят, пока приложение активно (или сразу после возврата в foreground).")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    private var dataSection: some View {
        Section("Данные") {
            HStack {
                Text("Инцидентов в журнале")
                Spacer()
                Text("\(store.incidents.count)")
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }
            HStack {
                Text("Событий смены статуса")
                Spacer()
                Text("\(store.events.count)")
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }
            Button("Сбросить настройки к config.json") {
                showResetConfirm = true
            }
            Button("Очистить историю и инциденты", role: .destructive) {
                showClearConfirm = true
            }
        }
    }

    private var aboutSection: some View {
        Section("О приложении") {
            settingRow("Версия", value: appVersion)
            settingRow("Бандл", value: "ru.netmon.ios")
            settingRow("Сервисов в конфиге", value: "\(store.config.services.count)")
            settingRow("Проверок в конфиге", value: "\(store.config.services.reduce(0) { $0 + $1.checks.count })")
            settingRow("Источник конфига",
                       value: store.configSource == .bundle ? "config.json из бандла" : "встроенные значения")
            Text("network.proxy = \(store.config.network.proxy) на iOS не применяется: трафик идёт через системный VPN/прокси, отдельный прокси-туннель не нужен.")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: Вспомогательное

    private func settingRow(_ title: String, value: String) -> some View {
        HStack {
            Text(title)
            Spacer(minLength: 8)
            Text(value)
                .monospacedDigit()
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.trailing)
        }
    }

    private func notifyBinding(_ status: Status) -> Binding<Bool> {
        Binding(
            get: { store.settings.notifyOn.contains(status.rawValue) },
            set: { enabled in
                var list = store.settings.notifyOn.filter { $0 != status.rawValue }
                if enabled {
                    list.append(status.rawValue)
                }
                store.settings.notifyOn = list
            }
        )
    }

    private var permissionLabel: String {
        switch store.notificationPermission {
        case .authorized: return "разрешены"
        case .provisional: return "тихие"
        case .denied: return "запрещены"
        case .notDetermined: return "не запрошены"
        default: return "неизвестно"
        }
    }

    private var appVersion: String {
        let info = Bundle.main.infoDictionary
        let short = info?["CFBundleShortVersionString"] as? String ?? "1.0"
        let build = info?["CFBundleVersion"] as? String ?? "1"
        return "\(short) (\(build))"
    }
}
