import Foundation

/// Форматирование чисел/дат для русского интерфейса.
enum Format {
    static let locale = Locale(identifier: "ru_RU")

    static func latency(_ value: Int?) -> String {
        guard let value else { return "—" }
        return "\(value) мс"
    }

    static func percent(_ value: Double) -> String {
        String(format: "%.0f%%", value)
    }

    static func uptime(_ value: Double) -> String {
        String(format: "%.1f%%", value)
    }

    static func time(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.dateFormat = "HH:mm:ss"
        return formatter.string(from: date)
    }

    static func dateTime(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.dateFormat = "d MMM, HH:mm"
        return formatter.string(from: date)
    }

    static func relative(_ date: Date, now: Date = Date()) -> String {
        let seconds = Int(now.timeIntervalSince(date))
        if seconds < 5 { return "только что" }
        if seconds < 60 { return "\(seconds) с назад" }
        let minutes = seconds / 60
        if minutes < 60 { return "\(minutes) мин назад" }
        let hours = minutes / 60
        if hours < 24 { return "\(hours) ч назад" }
        let days = hours / 24
        return "\(days) дн назад"
    }

    static func duration(_ seconds: Int) -> String {
        let total = max(0, seconds)
        if total < 60 { return "\(total) с" }
        let minutes = total / 60
        let restSeconds = total % 60
        if minutes < 60 {
            return restSeconds == 0 ? "\(minutes) мин" : "\(minutes) мин \(restSeconds) с"
        }
        let hours = minutes / 60
        let restMinutes = minutes % 60
        if hours < 24 {
            return restMinutes == 0 ? "\(hours) ч" : "\(hours) ч \(restMinutes) мин"
        }
        let days = hours / 24
        return "\(days) дн \(hours % 24) ч"
    }

    static func countdown(_ interval: TimeInterval) -> String {
        let seconds = max(0, Int(interval.rounded()))
        if seconds == 0 { return "сейчас" }
        if seconds < 60 { return "через \(seconds) с" }
        return "через \(seconds / 60) мин"
    }

    static func checks(_ ok: Int, _ total: Int) -> String {
        "\(ok)/\(total)"
    }
}
