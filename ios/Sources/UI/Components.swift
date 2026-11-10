import SwiftUI

// MARK: - Мелкие элементы

/// Плашка статуса: цветной текст на полупрозрачном фоне.
struct StatusBadge: View {
    let label: String
    let color: Color
    var compact: Bool = false

    var body: some View {
        Text(label)
            .font(.caption.weight(.semibold))
            .lineLimit(1)
            .minimumScaleFactor(0.8)
            .padding(.horizontal, compact ? 8 : 10)
            .padding(.vertical, compact ? 3 : 5)
            .background(color.opacity(0.16), in: Capsule())
            .foregroundStyle(color)
    }
}

/// Иконка сервиса в скруглённом квадрате с акцентом из config.json.
struct ServiceIconView: View {
    let symbol: String
    let accent: Color
    var size: CGFloat = 44

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: size * 0.3, style: .continuous)
                .fill(accent.opacity(0.16))
            Image(systemName: symbol)
                .font(.system(size: size * 0.42, weight: .semibold))
                .foregroundStyle(accent)
        }
        .frame(width: size, height: size)
    }
}

/// Мини-тайл с подписью и значением.
struct MetricTile: View {
    let title: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.caption2)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.subheadline.weight(.semibold).monospacedDigit())
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(8)
        .background(Color.primary.opacity(0.05),
                    in: RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}

/// Полоска последних опросов: цвет точки = статус сервиса в тот момент.
struct SeriesStrip: View {
    let points: [SeriesPoint]
    let config: AppConfig
    var maxPoints: Int = 60

    var body: some View {
        let visible = Array(points.suffix(maxPoints))
        HStack(spacing: 1) {
            if visible.isEmpty {
                RoundedRectangle(cornerRadius: 2, style: .continuous)
                    .fill(Color.primary.opacity(0.08))
                    .frame(height: 16)
            } else {
                ForEach(Array(visible.enumerated()), id: \.offset) { _, point in
                    RoundedRectangle(cornerRadius: 1.5, style: .continuous)
                        .fill(Theme.statusColor(point.status, config: config)
                            .opacity(point.status == .unknown ? 0.3 : 0.85))
                        .frame(height: 16)
                }
            }
        }
        .frame(maxWidth: .infinity)
        .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
    }
}

// MARK: - Одна проверка

struct CheckRow: View {
    let check: CheckResult
    let config: AppConfig

    private var tint: Color {
        check.ok
            ? Theme.statusColor(.ok, config: config)
            : Theme.statusColor(.down, config: config)
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: check.ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(tint)
                .padding(.top, 2)

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(check.label)
                        .font(.callout)
                        .lineLimit(1)
                        .truncationMode(.tail)
                    if check.core {
                        Text("core")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(Color.primary.opacity(0.07), in: Capsule())
                    }
                    Spacer(minLength: 4)
                    Text("\(check.latencyMs) мс")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }

                HStack(spacing: 8) {
                    Text(check.target)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    if let ip = check.ip, !ip.isEmpty {
                        Text(ip)
                            .font(.caption2.monospaced())
                            .foregroundStyle(.tertiary)
                            .lineLimit(1)
                    }
                }

                stageRow

                if !check.ok {
                    Text(errorLine)
                        .font(.caption)
                        .foregroundStyle(tint)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private var errorLine: String {
        let label = check.errorLabel ?? "Ошибка проверки"
        if let text = check.errorText, !text.isEmpty {
            return "\(label): \(text)"
        }
        return label
    }

    private var stageRow: some View {
        HStack(spacing: 5) {
            if let value = check.timings.dnsMs { stageChip("DNS", value) }
            if let value = check.timings.tcpMs { stageChip("TCP", value) }
            if let value = check.timings.tlsMs { stageChip("TLS", value) }
            if let value = check.timings.httpMs { stageChip("HTTP", value) }
            if let total = check.timings.totalMs {
                Text("итого \(total) мс")
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.tertiary)
            }
        }
    }

    private func stageChip(_ title: String, _ ms: Int) -> some View {
        Text("\(title) \(ms)")
            .font(.caption2.monospacedDigit())
            .foregroundStyle(.secondary)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(Color.primary.opacity(0.06), in: Capsule())
    }
}

// MARK: - Карточка сервиса

struct ServiceCard: View {
    let service: ServiceSnapshot
    let config: AppConfig
    @Binding var isExpanded: Bool

    private var color: Color {
        Theme.statusColor(service.status, config: config)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Button {
                withAnimation(.easeInOut(duration: 0.18)) {
                    isExpanded.toggle()
                }
            } label: {
                header
            }
            .buttonStyle(.plain)

            SeriesStrip(points: service.series, config: config)

            HStack(spacing: 8) {
                MetricTile(title: "uptime 1ч", value: Format.percent(service.uptime1h))
                MetricTile(title: "uptime 24ч", value: Format.percent(service.uptime24h))
                MetricTile(title: "проверки", value: Format.checks(service.checksOk, service.checksTotal))
            }

            VStack(alignment: .leading, spacing: 5) {
                Text(service.statusReason)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let hint = service.hint, !hint.isEmpty {
                    Label(hint, systemImage: "lightbulb")
                        .font(.caption)
                        .foregroundStyle(color)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            if isExpanded {
                checksList
            }
        }
        .padding(16)
        .background(Theme.cardBackground)
    }

    private var header: some View {
        HStack(spacing: 12) {
            ServiceIconView(
                symbol: ServiceIcon.symbol(for: service.id, icon: service.icon),
                accent: Color(hex: service.accent)
            )
            VStack(alignment: .leading, spacing: 3) {
                Text(service.name)
                    .font(.headline)
                    .foregroundStyle(.primary)
                Text(service.subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            Spacer(minLength: 6)
            VStack(alignment: .trailing, spacing: 4) {
                StatusBadge(label: service.statusLabel, color: color, compact: true)
                Text(Format.latency(service.latencyMs))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
        }
        .contentShape(Rectangle())
    }

    private var checksList: some View {
        VStack(alignment: .leading, spacing: 12) {
            Divider().opacity(0.5)
            if service.checks.isEmpty {
                Text("Проверки ещё не выполнялись")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(Array(service.checks.enumerated()), id: \.element.id) { index, check in
                    CheckRow(check: check, config: config)
                    if index < service.checks.count - 1 {
                        Divider().opacity(0.3)
                    }
                }
            }
        }
    }
}

// MARK: - Сводная плашка

struct SummaryCard: View {
    let snapshot: Snapshot
    let config: AppConfig
    let isChecking: Bool
    let onRefresh: () -> Void

    private var color: Color {
        Theme.statusColor(snapshot.summary.overall, config: config)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: ServiceIcon.statusSymbol(snapshot.summary.overall))
                    .font(.system(size: 34, weight: .semibold))
                    .foregroundStyle(color)
                VStack(alignment: .leading, spacing: 4) {
                    Text(snapshot.summary.overallLabel)
                        .font(.title2.weight(.bold))
                    Text(subtitle)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }

            HStack(spacing: 8) {
                MetricTile(title: "сервисов в норме",
                           value: "\(snapshot.summary.servicesOk)/\(snapshot.summary.servicesTotal)")
                MetricTile(title: "средний отклик",
                           value: Format.latency(snapshot.summary.avgLatencyMs))
                MetricTile(title: "проверок",
                           value: Format.checks(snapshot.summary.checksOk, snapshot.summary.checksTotal))
            }

            HStack(spacing: 10) {
                Button(action: onRefresh) {
                    HStack(spacing: 8) {
                        if isChecking {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: "arrow.clockwise")
                        }
                        Text(isChecking ? "Проверяю…" : "Проверить сейчас")
                            .font(.callout.weight(.semibold))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                }
                .buttonStyle(.borderedProminent)
                .tint(color)
                .disabled(isChecking)

                if snapshot.summary.activeIncidents > 0 {
                    StatusBadge(label: "\(snapshot.summary.activeIncidents) инцидент(а)",
                                color: Theme.statusColor(.degraded, config: config))
                }
            }
        }
        .padding(18)
        .background(Theme.cardBackground)
    }

    private var subtitle: String {
        let internet = snapshot.internet.label
        let problems = snapshot.summary.servicesAffected
        if problems == 0 {
            return "\(internet) · все проверки проходят"
        }
        return "\(internet) · проблемных сервисов: \(problems)"
    }
}

// MARK: - Подвал с временем

struct FooterCard: View {
    let snapshot: Snapshot
    let nextCheckDate: Date?
    let source: ConfigLoader.Source

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            VStack(alignment: .leading, spacing: 6) {
                Label("Обновлено \(Format.relative(snapshot.generatedAt, now: context.date))",
                      systemImage: "clock")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let nextCheckDate {
                    Text("Следующий опрос \(Format.countdown(nextCheckDate.timeIntervalSince(context.date))) · интервал \(snapshot.pollInterval) с")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Text("Автоопрос приостановлен")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Text(source == .bundle
                     ? "config.json загружен из бандла приложения"
                     : "config.json не найден — используются встроенные значения")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
            .background(Color.primary.opacity(0.04),
                        in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        }
    }
}
