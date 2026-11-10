import SwiftUI

/// Цвета, иконки и общие визуальные константы.
///
/// Палитра статусов берётся из `config.json` (`status_colors`), акценты
/// сервисов — из `services[].accent`, поэтому приложение выглядит так же,
/// как дашборд Python-сервера и Android-клиент.
enum Theme {
    static let accent = Color(hex: "#38d39f")
    static let cardCornerRadius: CGFloat = 20

    /// Фон экрана: очень лёгкий вертикальный градиент (работает в тёмной теме).
    static var screenBackground: some View {
        LinearGradient(
            colors: [Color.primary.opacity(0.035), Color.primary.opacity(0.0)],
            startPoint: .top,
            endPoint: .bottom
        )
        .ignoresSafeArea()
    }

    static var cardBackground: some View {
        RoundedRectangle(cornerRadius: cardCornerRadius, style: .continuous)
            .fill(Color.primary.opacity(0.055))
            .overlay(
                RoundedRectangle(cornerRadius: cardCornerRadius, style: .continuous)
                    .strokeBorder(Color.primary.opacity(0.08), lineWidth: 1)
            )
    }

    static func color(_ hex: String) -> Color {
        Color(hex: hex)
    }

    /// Цвет статуса из config.json.
    static func statusColor(_ status: Status, config: AppConfig) -> Color {
        Color(hex: config.color(for: status))
    }
}

/// SF Symbols для сервисов: в config.json поле `icon` — строковый идентификатор.
enum ServiceIcon {
    static func symbol(for serviceId: String, icon: String) -> String {
        switch serviceId {
        case "youtube":
            return "play.rectangle.fill"
        case "telegram":
            return "paperplane.fill"
        case "internet":
            return "globe"
        default:
            switch icon {
            case "youtube":
                return "play.rectangle.fill"
            case "telegram":
                return "paperplane.fill"
            case "globe", "internet":
                return "globe"
            default:
                return "questionmark.circle.fill"
            }
        }
    }

    static func statusSymbol(_ status: Status) -> String {
        switch status {
        case .ok:
            return "checkmark.circle.fill"
        case .degraded:
            return "exclamationmark.triangle.fill"
        case .blocked:
            return "hand.raised.fill"
        case .down:
            return "xmark.octagon.fill"
        case .unknown:
            return "questionmark.circle.fill"
        }
    }
}

extension Color {
    /// `#rrggbb` из config.json → Color. Невалидное значение → fallback.
    init(hex: String, fallback: Color = .gray) {
        var text = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.hasPrefix("#") {
            text.removeFirst()
        }
        guard text.count == 6, let value = UInt32(text, radix: 16) else {
            self = fallback
            return
        }
        let red = Double((value >> 16) & 0xFF) / 255.0
        let green = Double((value >> 8) & 0xFF) / 255.0
        let blue = Double(value & 0xFF) / 255.0
        self.init(red: red, green: green, blue: blue)
    }
}
