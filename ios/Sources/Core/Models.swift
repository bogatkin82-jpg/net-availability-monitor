import Foundation

// MARK: - Статусы

/// Статус сервиса/проверки — ровно те же идентификаторы, что в `config.json`
/// (`ok / degraded / blocked / down / unknown`) и на дашборде Python-сервера.
enum Status: String, Codable, CaseIterable, Sendable {
    case ok
    case degraded
    case blocked
    case down
    case unknown

    var id: String { rawValue }

    /// Статус считается проблемным (для сводной плашки «Есть проблемы»).
    var isProblem: Bool {
        switch self {
        case .degraded, .blocked, .down:
            return true
        case .ok, .unknown:
            return false
        }
    }

    /// Порядок статусов в UI/`notify_on` — как в config.json.
    static var orderedIds: [String] { Status.allCases.map(\.rawValue) }
}

/// Вид проверки — поле `kind` в `services[].checks[]`.
enum CheckKind: String, Codable, CaseIterable, Sendable {
    case dns
    case tcp
    case tls
    case https

    /// Подпись стадии для UI.
    var label: String {
        switch self {
        case .dns: return "DNS"
        case .tcp: return "TCP"
        case .tls: return "TLS"
        case .https: return "HTTPS"
        }
    }
}

/// Тайминги этапов одной проверки в миллисекундах. `nil` — этап не выполнялся.
struct StageTimings: Codable, Sendable, Equatable {
    var dnsMs: Int?
    var tcpMs: Int?
    var tlsMs: Int?
    var httpMs: Int?

    init(dnsMs: Int? = nil, tcpMs: Int? = nil, tlsMs: Int? = nil, httpMs: Int? = nil) {
        self.dnsMs = dnsMs
        self.tcpMs = tcpMs
        self.tlsMs = tlsMs
        self.httpMs = httpMs
    }

    /// Сумма известных этапов (для строки «итого» в раскрытой проверке).
    var totalMs: Int? {
        let values = [dnsMs, tcpMs, tlsMs, httpMs].compactMap { $0 }
        guard !values.isEmpty else { return nil }
        return values.reduce(0, +)
    }
}

// MARK: - Описание проверки

/// Одна проверка из `services[].checks[]` config.json.
struct CheckSpec: Codable, Sendable, Identifiable, Equatable {
    let id: String
    let label: String
    let kind: CheckKind
    let host: String
    let port: Int
    let path: String
    let expect: [Int]
    let core: Bool
    let timeoutSec: Int

    /// Так же, как `check_target()` в server.py: то, что показываем в причине сбоя.
    var target: String {
        switch kind {
        case .dns:
            return host
        case .tcp, .tls:
            return "\(host):\(port)"
        case .https:
            let base = port == 443 ? host : "\(host):\(port)"
            return base + (path.isEmpty ? "/" : path)
        }
    }
}

// MARK: - Коды ошибок

/// Коды ошибок и их подписи — паритет с `ERROR_LABELS` / `BLOCK_ERRORS` в server.py.
enum ErrorCodes {
    static let dnsError = "dns_error"
    static let dnsTimeout = "dns_timeout"
    static let connectTimeout = "connect_timeout"
    static let connectRefused = "connect_refused"
    static let connectUnreachable = "connect_unreachable"
    static let tlsHandshakeTimeout = "tls_handshake_timeout"
    static let tlsReset = "tls_reset"
    static let tlsCertError = "tls_cert_error"
    static let tlsError = "tls_error"
    static let readTimeout = "read_timeout"
    static let httpStatusUnexpected = "http_status_unexpected"
    static let httpError = "http_error"
    static let unknownError = "unknown_error"

    /// Русские подписи — один в один с server.py.
    static let labels: [String: String] = [
        dnsError: "DNS-запись не разрешается",
        dnsTimeout: "DNS-запрос не завершился",
        connectTimeout: "TCP-соединение не устанавливается (таймаут)",
        connectRefused: "TCP-соединение отклонено",
        connectUnreachable: "Сеть недоступна",
        tlsHandshakeTimeout: "TLS-рукопожатие не завершилось (таймаут)",
        tlsReset: "TLS-соединение сброшено",
        tlsCertError: "Ошибка TLS-сертификата",
        tlsError: "Ошибка TLS",
        readTimeout: "Ответ не получен (таймаут)",
        httpStatusUnexpected: "Неожиданный HTTP-статус",
        httpError: "Ошибка HTTP-обмена",
        unknownError: "Неизвестная ошибка",
    ]

    /// Ошибки, характерные для фильтрации трафика при работающем интернете.
    static let blockErrors: Set<String> = [
        connectTimeout,
        connectUnreachable,
        tlsHandshakeTimeout,
        tlsReset,
        tlsError,
        tlsCertError,
        readTimeout,
    ]

    /// Ошибки DNS (проверка «только DNS не разрешается»).
    static let dnsErrors: Set<String> = [dnsError, dnsTimeout]

    static func label(_ code: String?) -> String? {
        guard let code, !code.isEmpty else { return nil }
        return labels[code] ?? code
    }

    /// Подпись этапа для раскрытой проверки: `dns` → DNS, `http` → HTTP.
    static func stageLabel(_ stage: String) -> String {
        switch stage {
        case "dns": return "DNS"
        case "tcp": return "TCP"
        case "tls": return "TLS"
        case "http": return "HTTP"
        default: return stage.uppercased()
        }
    }
}

// MARK: - Результат проверки

/// Результат одной проверки. Набор полей повторяет `_mk_result()` из server.py,
/// чтобы UI и журнал выглядели одинаково на всех платформах.
struct CheckResult: Codable, Sendable, Identifiable, Equatable {
    let id: String
    let label: String
    let target: String
    let kind: CheckKind
    let core: Bool
    let ok: Bool
    let latencyMs: Int
    let stage: String
    let error: String?
    let errorText: String?
    let httpStatus: Int?
    let ip: String?
    let timings: StageTimings
    let measuredAt: Date

    var errorLabel: String? { ErrorCodes.label(error) }

    /// Текст причины сбоя: «<подпись ошибки> — <цель>».
    var reasonText: String {
        "\(errorLabel ?? "Ошибка проверки") — \(target)"
    }

    var stageLabel: String { ErrorCodes.stageLabel(stage) }

    var latencyText: String {
        "\(latencyMs) мс"
    }
}

// MARK: - Конфигурация (config.json)

struct ServerConfig: Sendable, Equatable {
    var host: String
    var port: Int
}

struct PollingConfig: Sendable, Equatable {
    var intervalSec: Int
    var checkTimeoutSec: Int
    var dnsTimeoutSec: Int
}

struct IncidentsConfig: Sendable, Equatable {
    var openAfterFailures: Int
    var closeAfterSuccesses: Int
    var maxStored: Int
}

struct HistoryConfig: Sendable, Equatable {
    var maxAgeHours: Int
    var maxPoints: Int
    var seriesPoints: Int
}

/// `network` из config.json. На iOS прокси-режим не применяется: трафик и так
/// идёт через системный VPN/прокси, поэтому поле только читается и показывается
/// в «Настройках» как справочное.
struct NetworkConfig: Sendable, Equatable {
    var proxy: String
}

/// Настройки локальных уведомлений.
struct NotificationSettings: Sendable, Equatable {
    var enabled: Bool
    var notifyOn: [String]
}

/// Один сервис из `services[]`: карточка на экране «Статус» и набор проверок.
struct ServiceConfig: Sendable, Identifiable, Equatable {
    let id: String
    let name: String
    let subtitle: String
    let icon: String
    let accent: String
    let incidentTitle: String
    let hints: [String: String]
    let checks: [CheckSpec]
}

/// Полный конфиг приложения (корневой `config.json`).
struct AppConfig: Sendable, Equatable {
    var version: Int
    var server: ServerConfig
    var polling: PollingConfig
    var incidents: IncidentsConfig
    var history: HistoryConfig
    var network: NetworkConfig
    var statusLabels: [String: String]
    var statusColors: [String: String]
    var services: [ServiceConfig]
    var notifications: NotificationSettings

    /// Идентификатор «контрольного канала» — как в server.py/static/app.js.
    static let baselineServiceId = "internet"

    var baselineService: ServiceConfig? {
        services.first { $0.id == Self.baselineServiceId }
    }

    func service(id: String) -> ServiceConfig? {
        services.first { $0.id == id }
    }

    /// Подпись статуса из config.json (фолбэк — как на дашборде).
    func label(for status: Status) -> String {
        statusLabels[status.rawValue] ?? statusLabels[Status.unknown.rawValue] ?? "Нет данных"
    }

    /// Цвет статуса из config.json (фолбэк — серый «нет данных»).
    func color(for status: Status) -> String {
        statusColors[status.rawValue] ?? statusColors[Status.unknown.rawValue] ?? "#8e8e93"
    }

    /// Все проверки в порядке конфига: (serviceId, spec).
    var allChecks: [(serviceId: String, spec: CheckSpec)] {
        services.flatMap { service in service.checks.map { (service.id, $0) } }
    }
}

// MARK: - Значения по умолчанию (аварийный фолбэк)

extension AppConfig {
    /// Полностью повторяет корневой `config.json` репозитория.
    /// Используется, если `Resources/config.json` не найден или повреждён;
    /// совпадение с бандл-конфигом проверяет `ConfigParityTests`.
    static let defaults = AppConfig(
        version: 1,
        server: ServerConfig(host: "127.0.0.1", port: 8099),
        polling: PollingConfig(intervalSec: 20, checkTimeoutSec: 8, dnsTimeoutSec: 4),
        incidents: IncidentsConfig(openAfterFailures: 2, closeAfterSuccesses: 1, maxStored: 500),
        history: HistoryConfig(maxAgeHours: 24, maxPoints: 8640, seriesPoints: 180),
        network: NetworkConfig(proxy: "direct"),
        statusLabels: [
            "ok": "Доступен",
            "degraded": "Нестабильно",
            "blocked": "Похоже на блокировку",
            "down": "Недоступен",
            "unknown": "Нет данных",
        ],
        statusColors: [
            "ok": "#34c759",
            "degraded": "#ff9500",
            "blocked": "#ff3b30",
            "down": "#ff3b30",
            "unknown": "#8e8e93",
        ],
        services: [
            ServiceConfig(
                id: "youtube",
                name: "YouTube",
                subtitle: "видеохостинг · youtube.com",
                icon: "youtube",
                accent: "#ff3040",
                incidentTitle: "YouTube недоступен",
                hints: [
                    "degraded": "Часть проверок не проходит — сравните с доступом через VPN",
                    "blocked": "Похоже на блокировку: проверьте доступ через VPN или мобильный интернет",
                    "down": "Сервис не отвечает — проверьте соединение и повторите позже",
                ],
                checks: [
                    https("yt_https", "HTTPS /generate_204", "www.youtube.com",
                          "/generate_204", [204], core: true),
                    https("yt_img", "HTTPS i.ytimg.com /generate_204", "i.ytimg.com",
                          "/generate_204", [204], core: true),
                    https("yt_www", "HTTPS главная страница", "www.youtube.com",
                          "/", [200, 301, 302, 303]),
                    tcp("yt_tcp", "TCP www.youtube.com:443", "www.youtube.com"),
                    dns("yt_dns", "DNS www.youtube.com", "www.youtube.com"),
                ]
            ),
            ServiceConfig(
                id: "telegram",
                name: "Telegram",
                subtitle: "мессенджер · telegram.org",
                icon: "telegram",
                accent: "#2ea6e0",
                incidentTitle: "Telegram недоступен",
                hints: [
                    "degraded": "Часть проверок не проходит — возможна фильтрация MTProto",
                    "blocked": "TCP/TLS к Telegram не проходит: вероятна блокировка — включите VPN",
                    "down": "Сервис не отвечает — проверьте соединение и повторите позже",
                ],
                checks: [
                    https("tg_web", "HTTPS web.telegram.org", "web.telegram.org",
                          "/", [200, 301, 302], core: true),
                    https("tg_api", "HTTPS api.telegram.org", "api.telegram.org",
                          "/", [200, 301, 302, 400, 404], core: true),
                    tcp("tg_dc2", "TCP DC2 149.154.167.51:443", "149.154.167.51", core: true),
                    tcp("tg_dc1", "TCP DC1 149.154.175.50:443", "149.154.175.50"),
                    tcp("tg_dc4", "TCP DC4 149.154.167.91:443", "149.154.167.91"),
                    dns("tg_dns_api", "DNS api.telegram.org", "api.telegram.org"),
                    dns("tg_dns_web", "DNS web.telegram.org", "web.telegram.org"),
                ]
            ),
            ServiceConfig(
                id: "internet",
                name: "Интернет",
                subtitle: "контрольный канал · gstatic / cloudflare",
                icon: "globe",
                accent: "#38d39f",
                incidentTitle: "Интернет недоступен",
                hints: [
                    "degraded": "Канал нестабилен — проверьте Wi-Fi/роутер",
                    "down": "Проверьте Wi-Fi/кабель и роутер: базовые проверки не проходят",
                ],
                checks: [
                    https("inet_gstatic", "HTTPS connectivitycheck.gstatic.com",
                          "connectivitycheck.gstatic.com", "/generate_204", [204], core: true),
                    https("inet_cloudflare", "HTTPS cloudflare.com/cdn-cgi/trace",
                          "cloudflare.com", "/cdn-cgi/trace", [200], core: true),
                    tcp("inet_tcp", "TCP 1.1.1.1:443", "1.1.1.1", core: true),
                    dns("inet_dns", "DNS cloudflare.com", "cloudflare.com"),
                ]
            ),
        ],
        notifications: NotificationSettings(enabled: true, notifyOn: ["blocked", "down"])
    )

    // MARK: Хелперы для описания проверок

    private static func https(_ id: String, _ label: String, _ host: String, _ path: String,
                              _ expect: [Int], core: Bool = false, timeout: Int = 8,
                              port: Int = 443) -> CheckSpec {
        CheckSpec(id: id, label: label, kind: .https, host: host, port: port, path: path,
                  expect: expect, core: core, timeoutSec: timeout)
    }

    private static func tls(_ id: String, _ label: String, _ host: String, core: Bool = false,
                            timeout: Int = 8, port: Int = 443) -> CheckSpec {
        CheckSpec(id: id, label: label, kind: .tls, host: host, port: port, path: "",
                  expect: [], core: core, timeoutSec: timeout)
    }

    private static func tcp(_ id: String, _ label: String, _ host: String, port: Int = 443,
                            core: Bool = false, timeout: Int = 8) -> CheckSpec {
        CheckSpec(id: id, label: label, kind: .tcp, host: host, port: port, path: "",
                  expect: [], core: core, timeoutSec: timeout)
    }

    private static func dns(_ id: String, _ label: String, _ host: String, core: Bool = false,
                            timeout: Int = 8) -> CheckSpec {
        CheckSpec(id: id, label: label, kind: .dns, host: host, port: 443, path: "",
                  expect: [], core: core, timeoutSec: timeout)
    }
}
