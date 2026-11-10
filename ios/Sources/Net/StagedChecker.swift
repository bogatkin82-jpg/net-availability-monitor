import Foundation
import Network

/// Движок проверок: DNS → TCP → TLS → HTTP с замерами этапов.
///
/// Аналог `run_check()` из server.py:
///  * DNS — `getaddrinfo` (таймаут через отдельный таймер);
///  * TCP/TLS — `NWConnection` (state `.ready` = соединение/рукопожатие готово);
///  * HTTPS — `URLSession` с таймаутом и проверкой ожидаемого HTTP-статуса.
///
/// Проверки в рамках раунда выполняются параллельно (до 16 задач, как
/// ThreadPoolExecutor в server.py). Класс неизменяемый, поэтому безопасен
/// для конкурентных вызовов.
final class StagedChecker: @unchecked Sendable {
    /// User-Agent как у мобильного Safari (server.py использует десктопный).
    static let userAgent = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        + "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"

    private let session: URLSession

    init() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 30
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.urlCache = nil
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.httpCookieAcceptPolicy = .never
        configuration.waitsForConnectivity = false
        session = URLSession(configuration: configuration)
    }

    deinit {
        session.invalidateAndCancel()
    }

    // MARK: - Раунд

    /// Прогоняет все проверки всех сервисов и возвращает результаты в порядке
    /// config.json (ключ — id сервиса).
    func runRound(services: [ServiceConfig], options: CheckOptions) async -> [String: [CheckResult]] {
        var collected: [String: [CheckResult]] = [:]
        await withTaskGroup(of: (serviceId: String, result: CheckResult).self) { group in
            for service in services {
                for spec in service.checks {
                    group.addTask {
                        (serviceId: service.id, result: await self.check(spec, options: options))
                    }
                }
            }
            for await item in group {
                collected[item.serviceId, default: []].append(item.result)
            }
        }

        var ordered: [String: [CheckResult]] = [:]
        for service in services {
            let byId = Dictionary(uniqueKeysWithValues: (collected[service.id] ?? []).map { ($0.id, $0) })
            ordered[service.id] = service.checks.compactMap { byId[$0.id] }
        }
        return ordered
    }

    // MARK: - Одна проверка

    func check(_ spec: CheckSpec, options: CheckOptions) async -> CheckResult {
        let started = Date()
        let timeout = max(1.0, Double(options.checkTimeoutSec ?? spec.timeoutSec))
        let deadline = started.addingTimeInterval(timeout)
        var timings = StageTimings()
        var ip: String?
        var httpStatus: Int?

        func elapsedMs() -> Int {
            Int((Date().timeIntervalSince(started) * 1000).rounded())
        }

        func remaining() -> TimeInterval {
            max(0.5, deadline.timeIntervalSinceNow)
        }

        func result(ok: Bool, stage: String, error: String? = nil, text: String? = nil) -> CheckResult {
            CheckResult(
                id: spec.id,
                label: spec.label,
                target: spec.target,
                kind: spec.kind,
                core: spec.core,
                ok: ok,
                latencyMs: elapsedMs(),
                stage: stage,
                error: error,
                errorText: text,
                httpStatus: httpStatus,
                ip: ip,
                timings: timings,
                measuredAt: started
            )
        }

        // --- DNS ---------------------------------------------------------
        let dnsStarted = Date()
        let dns = await Self.resolve(host: spec.host,
                                     timeout: min(Double(options.dnsTimeoutSec), remaining()))
        timings.dnsMs = Int((Date().timeIntervalSince(dnsStarted) * 1000).rounded())
        switch dns {
        case .success(let address):
            ip = address
        case .failure(let code, let text):
            return result(ok: false, stage: "dns", error: code, text: text)
        }
        if spec.kind == .dns {
            return result(ok: true, stage: "dns")
        }

        // --- TCP ---------------------------------------------------------
        let tcpStarted = Date()
        let tcp = await Self.connect(host: spec.host, port: spec.port,
                                     useTLS: false, timeout: remaining())
        timings.tcpMs = Int((Date().timeIntervalSince(tcpStarted) * 1000).rounded())
        if case let .failure(code, text) = tcp {
            return result(ok: false, stage: "tcp", error: code, text: text)
        }
        if spec.kind == .tcp {
            return result(ok: true, stage: "tcp")
        }

        // --- TLS ---------------------------------------------------------
        if spec.kind == .tls {
            let tlsStarted = Date()
            let tls = await Self.connect(host: spec.host, port: spec.port,
                                         useTLS: true, timeout: remaining())
            timings.tlsMs = Int((Date().timeIntervalSince(tlsStarted) * 1000).rounded())
            if case let .failure(code, text) = tls {
                return result(ok: false, stage: "tls", error: code, text: text)
            }
            return result(ok: true, stage: "tls")
        }

        // --- HTTPS -------------------------------------------------------
        let httpStarted = Date()
        let http = await httpGet(host: spec.host, port: spec.port, path: spec.path,
                                 timeout: remaining())
        timings.httpMs = Int((Date().timeIntervalSince(httpStarted) * 1000).rounded())
        switch http {
        case .failure(let code, let text):
            return result(ok: false, stage: "http", error: code, text: text)
        case .response(let status):
            httpStatus = status
            if spec.expect.isEmpty || spec.expect.contains(status) {
                return result(ok: true, stage: "http")
            }
            let expected = spec.expect.map(String.init).joined(separator: "/")
            return result(ok: false, stage: "http",
                          error: ErrorCodes.httpStatusUnexpected,
                          text: "HTTP \(status) (ожидалось \(expected))")
        }
    }

    // MARK: - DNS

    private enum DnsOutcome: Sendable {
        case success(String)
        case failure(code: String, text: String)
    }

    /// Асинхронный `getaddrinfo` с таймаутом (сам вызов блокирующий, поэтому
    /// уходит на global-очередь).
    private static func resolve(host: String, timeout: TimeInterval) async -> DnsOutcome {
        await withCheckedContinuation { (continuation: CheckedContinuation<DnsOutcome, Never>) in
            let gate = ResumeGate()
            let finish: @Sendable (DnsOutcome) -> Void = { outcome in
                gate.once { continuation.resume(returning: outcome) }
            }
            let queue = DispatchQueue.global(qos: .utility)
            queue.async {
                finish(lookup(host))
            }
            queue.asyncAfter(deadline: .now() + max(0.5, timeout)) {
                finish(.failure(code: ErrorCodes.dnsTimeout, text: "DNS-запрос не завершился"))
            }
        }
    }

    private static func lookup(_ host: String) -> DnsOutcome {
        var hints = addrinfo()
        hints.ai_family = AF_UNSPEC
        hints.ai_socktype = SOCK_STREAM
        var result: UnsafeMutablePointer<addrinfo>?
        let status = getaddrinfo(host, nil, &hints, &result)
        defer {
            if let result {
                freeaddrinfo(result)
            }
        }
        guard status == 0 else {
            let text = String(cString: gai_strerror(status))
            if status == EAI_AGAIN {
                return .failure(code: ErrorCodes.dnsTimeout, text: text)
            }
            return .failure(code: ErrorCodes.dnsError, text: text)
        }
        var node = result
        while let current = node {
            if let address = numericHost(current.pointee.ai_addr) {
                return .success(address)
            }
            node = current.pointee.ai_next
        }
        return .failure(code: ErrorCodes.dnsError, text: "Пустой ответ DNS")
    }

    private static func numericHost(_ address: UnsafeMutablePointer<sockaddr>?) -> String? {
        guard let address else { return nil }
        var buffer = [CChar](repeating: 0, count: Int(NI_MAXHOST))
        let length = socklen_t(address.pointee.sa_len)
        let rc = getnameinfo(address, length, &buffer, socklen_t(buffer.count), nil, 0, NI_NUMERICHOST)
        guard rc == 0 else { return nil }
        return String(cString: buffer)
    }

    // MARK: - TCP / TLS (Network.framework)

    private enum ConnectOutcome: Sendable {
        case ready
        case failure(code: String, text: String)
    }

    private static func connect(host: String, port: Int, useTLS: Bool,
                                timeout: TimeInterval) async -> ConnectOutcome {
        let clampedPort = UInt16(clamping: min(max(port, 1), 65535))
        guard let nwPort = NWEndpoint.Port(rawValue: clampedPort) else {
            return .failure(code: ErrorCodes.connectUnreachable, text: "Некорректный порт \(port)")
        }
        let parameters: NWParameters
        if useTLS {
            parameters = NWParameters(tls: NWProtocolTLS.Options(), tcp: NWProtocolTCP.Options())
        } else {
            parameters = NWParameters(tls: nil, tcp: NWProtocolTCP.Options())
        }

        let connection = NWConnection(host: NWEndpoint.Host(host), port: nwPort, using: parameters)
        let handle = ConnectionHandle(connection)
        let gate = ResumeGate()
        let stateQueue = DispatchQueue(label: "ru.netmon.nw.state")
        let timerQueue = DispatchQueue(label: "ru.netmon.nw.timer")

        return await withCheckedContinuation { (continuation: CheckedContinuation<ConnectOutcome, Never>) in
            let finish: @Sendable (ConnectOutcome) -> Void = { outcome in
                gate.once {
                    handle.cancel()
                    continuation.resume(returning: outcome)
                }
            }
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    finish(.ready)
                case .failed(let error):
                    let mapped = mapNetworkError(error, useTLS: useTLS)
                    finish(.failure(code: mapped.code, text: mapped.text))
                case .cancelled:
                    finish(.failure(code: ErrorCodes.connectUnreachable, text: "Соединение отменено"))
                default:
                    break
                }
            }
            connection.start(queue: stateQueue)
            timerQueue.asyncAfter(deadline: .now() + max(0.5, timeout)) {
                finish(.failure(
                    code: useTLS ? ErrorCodes.tlsHandshakeTimeout : ErrorCodes.connectTimeout,
                    text: useTLS
                        ? "TLS-рукопожатие не завершилось (таймаут)"
                        : "TCP-соединение не устанавливается (таймаут)"
                ))
            }
        }
    }

    private static func mapNetworkError(_ error: NWError,
                                        useTLS: Bool) -> (code: String, text: String) {
        switch error {
        case .dns(let code):
            return (ErrorCodes.dnsError, "DNS: \(code)")
        case .tls(let status):
            return (ErrorCodes.tlsError, "TLS: \(status)")
        case .posix(let code):
            switch code {
            case .ECONNREFUSED:
                return (ErrorCodes.connectRefused, "ECONNREFUSED")
            case .ETIMEDOUT:
                return useTLS
                    ? (ErrorCodes.tlsHandshakeTimeout, "TLS-рукопожатие не завершилось (таймаут)")
                    : (ErrorCodes.connectTimeout, "TCP-соединение не устанавливается (таймаут)")
            case .ECONNRESET, .EPIPE:
                return useTLS
                    ? (ErrorCodes.tlsReset, "TLS-соединение сброшено")
                    : (ErrorCodes.connectUnreachable, "Соединение сброшено")
            case .ENETUNREACH, .EHOSTUNREACH, .ENETDOWN:
                return (ErrorCodes.connectUnreachable, "Сеть недоступна")
            default:
                return (ErrorCodes.connectUnreachable, "POSIX \(code.rawValue)")
            }
        default:
            // Будущие/неизвестные случаи NWError не должны ронять мониторинг.
            return (ErrorCodes.unknownError, "\(error)")
        }
    }

    // MARK: - HTTPS (URLSession)

    private enum HttpOutcome: Sendable {
        case response(Int)
        case failure(code: String, text: String)
    }

    private func httpGet(host: String, port: Int, path: String,
                         timeout: TimeInterval) async -> HttpOutcome {
        var components = URLComponents()
        components.scheme = "https"
        components.host = host
        components.port = port == 443 ? nil : port
        components.path = path.isEmpty ? "/" : path
        guard let url = components.url else {
            return .failure(code: ErrorCodes.httpError, text: "Некорректный URL")
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = max(0.5, timeout)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue(Self.userAgent, forHTTPHeaderField: "User-Agent")
        request.setValue("*/*", forHTTPHeaderField: "Accept")
        request.setValue("ru-RU,ru;q=0.9,en;q=0.8", forHTTPHeaderField: "Accept-Language")

        do {
            let (_, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                return .failure(code: ErrorCodes.httpError, text: "Не удалось разобрать HTTP-ответ")
            }
            return .response(http.statusCode)
        } catch {
            return Self.mapHttpError(error)
        }
    }

    private static func mapHttpError(_ error: Error) -> HttpOutcome {
        guard let urlError = error as? URLError else {
            return .failure(code: ErrorCodes.httpError, text: error.localizedDescription)
        }
        switch urlError.code {
        case .timedOut:
            return .failure(code: ErrorCodes.readTimeout, text: "Ответ не получен (таймаут)")
        case .cannotFindHost, .dnsLookupFailed:
            return .failure(code: ErrorCodes.dnsError, text: "DNS-запись не разрешается")
        case .cannotConnectToHost:
            return .failure(code: ErrorCodes.connectUnreachable, text: "Сеть недоступна")
        case .networkConnectionLost:
            return .failure(code: ErrorCodes.tlsReset, text: "TLS-соединение сброшено")
        case .secureConnectionFailed:
            return .failure(code: ErrorCodes.tlsError, text: "Ошибка TLS")
        case .serverCertificateUntrusted, .serverCertificateHasBadDate,
             .serverCertificateNotYetValid, .serverCertificateHasUnknownRoot,
             .clientCertificateRejected, .clientCertificateRequired:
            return .failure(code: ErrorCodes.tlsCertError, text: "Ошибка TLS-сертификата")
        case .appTransportSecurityRequiresSecureConnection:
            return .failure(code: ErrorCodes.tlsError, text: "Ошибка TLS")
        case .notConnectedToInternet:
            return .failure(code: ErrorCodes.connectUnreachable, text: "Сеть недоступна")
        case .cancelled:
            return .failure(code: ErrorCodes.unknownError, text: "Проверка отменена")
        default:
            return .failure(code: ErrorCodes.httpError, text: urlError.localizedDescription)
        }
    }
}

// MARK: - Вспомогательные типы

/// Гарантирует однократное возобновление continuation (таймер + callback).
private final class ResumeGate: @unchecked Sendable {
    private var resumed = false
    private let lock = NSLock()

    func once(_ body: () -> Void) {
        lock.lock()
        if resumed {
            lock.unlock()
            return
        }
        resumed = true
        lock.unlock()
        body()
    }
}

/// Безопасно отменяет NWConnection из @Sendable-замыканий.
private final class ConnectionHandle: @unchecked Sendable {
    private let lock = NSLock()
    private var connection: NWConnection?

    init(_ connection: NWConnection) {
        self.connection = connection
    }

    func cancel() {
        lock.lock()
        let current = connection
        connection = nil
        lock.unlock()
        current?.cancel()
    }
}
