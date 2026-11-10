import SwiftUI

/// Фильтр журнала.
enum JournalFilter: String, CaseIterable, Hashable {
    case all
    case incidents
    case events

    var title: String {
        switch self {
        case .all: return "Всё"
        case .incidents: return "Инциденты"
        case .events: return "События"
        }
    }
}

/// Запись журнала: инцидент или событие смены статуса.
enum JournalEntry: Identifiable {
    case incident(Incident)
    case event(MonitorEvent)

    var id: String {
        switch self {
        case .incident(let incident): return "incident-" + incident.id
        case .event(let event): return "event-" + event.id
        }
    }

    var date: Date {
        switch self {
        case .incident(let incident): return incident.startedAt
        case .event(let event): return event.at
        }
    }

    func matches(_ needle: String) -> Bool {
        switch self {
        case .incident(let incident):
            let haystack = "\(incident.serviceName) \(incident.title) \(incident.reason)"
            return haystack.lowercased().contains(needle)
        case .event(let event):
            let haystack = "\(event.serviceName) \(event.title) \(event.detail)"
            return haystack.lowercased().contains(needle)
        }
    }
}

/// Экран «Журнал»: инциденты и переходы статусов с поиском и фильтром.
struct JournalScreen: View {
    @EnvironmentObject private var store: MonitorStore
    @State private var query = ""
    @State private var filter: JournalFilter = .all

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                Picker("Показывать", selection: $filter) {
                    ForEach(JournalFilter.allCases, id: \.self) { item in
                        Text(item.title).tag(item)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)

                List {
                    ForEach(entries) { entry in
                        switch entry {
                        case .incident(let incident):
                            IncidentRow(incident: incident, config: store.config)
                        case .event(let event):
                            EventRow(event: event, config: store.config)
                        }
                    }
                }
                .listStyle(.plain)
                .overlay {
                    if entries.isEmpty {
                        ContentUnavailableView(
                            "Журнал пуст",
                            systemImage: "text.book.closed",
                            description: Text("Инциденты и переходы статусов появятся после первых опросов")
                        )
                    }
                }
            }
            .navigationTitle("Журнал")
            .searchable(text: $query, prompt: "Поиск по журналу")
        }
    }

    private var entries: [JournalEntry] {
        var items: [JournalEntry] = []
        if filter != .events {
            items.append(contentsOf: store.incidents.map(JournalEntry.incident))
        }
        if filter != .incidents {
            items.append(contentsOf: store.events.map(JournalEntry.event))
        }
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let filtered = needle.isEmpty ? items : items.filter { $0.matches(needle) }
        return filtered.sorted { $0.date > $1.date }
    }
}

// MARK: - Строки журнала

private struct IncidentRow: View {
    let incident: Incident
    let config: AppConfig

    private var color: Color {
        Theme.statusColor(incident.serviceStatus, config: config)
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: incident.isActive ? "exclamationmark.triangle.fill" : "checkmark.seal.fill")
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(incident.isActive ? color : Theme.statusColor(.ok, config: config))
                .padding(.top, 2)

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(incident.title)
                        .font(.callout.weight(.semibold))
                        .lineLimit(2)
                    Spacer(minLength: 4)
                    Text(incident.isActive ? "активен" : "закрыт")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(incident.isActive ? color : .secondary)
                }
                Text(incident.reason)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 6) {
                    Text(Format.dateTime(incident.startedAt))
                    Text("·")
                    Text(Format.duration(incident.duration(now: Date())))
                    Text("·")
                    Text("\(incident.failedChecks)/\(incident.totalChecks) проверок")
                }
                .font(.caption2)
                .foregroundStyle(.tertiary)
            }
        }
        .padding(.vertical, 4)
    }
}

private struct EventRow: View {
    let event: MonitorEvent
    let config: AppConfig

    private var color: Color {
        Theme.statusColor(event.status, config: config)
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "arrow.triangle.2.circlepath")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(color)
                .padding(.top, 2)

            VStack(alignment: .leading, spacing: 3) {
                Text(event.title)
                    .font(.callout)
                    .lineLimit(2)
                Text(event.detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                HStack(spacing: 6) {
                    Text(Format.dateTime(event.at))
                    Text("·")
                    Text(Format.time(event.at))
                }
                .font(.caption2)
                .foregroundStyle(.tertiary)
            }
        }
        .padding(.vertical, 4)
    }
}
