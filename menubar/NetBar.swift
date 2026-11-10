// NetBar — индикатор доступности YouTube/Telegram в строке меню macOS.
//
// Режимы (переменная окружения NETBAR_MODE):
//   dot     — одна цветная точка в строке меню (по умолчанию);
//   text    — компактный текст YT✓ TG✕;
//   overlay — плавающая точка поверх всех окон в правом верхнем углу (у часов),
//             всегда видна, даже если строка меню переполнена и пункты спрятаны.
//
// Во всех режимах: опрос http://127.0.0.1:8099/api/state каждые 10 секунд,
// меню с деталями, кнопкой проверки, открытием дашборда и запуском сервера.
//
// Сборка: ./build.sh
// Самопроверка без GUI: ./NetBar.app/Contents/MacOS/NetBar --dump

import Cocoa
import Foundation

let logPath = "/tmp/netmon-menubar.log"

func colorFromHex(_ value: String) -> NSColor? {
    var text = value.trimmingCharacters(in: .whitespacesAndNewlines)
    if text.hasPrefix("#") { text.removeFirst() }
    guard text.count == 6, let raw = UInt32(text, radix: 16) else { return nil }
    return NSColor(srgbRed: CGFloat((raw >> 16) & 0xFF) / 255.0,
                   green: CGFloat((raw >> 8) & 0xFF) / 255.0,
                   blue: CGFloat(raw & 0xFF) / 255.0,
                   alpha: 1.0)
}

struct AppSettings {
    var mode = "dot"
    var overlayWindow: CGFloat = 24
    var overlayDot: CGFloat = 9.6
    var statusItem: CGFloat = 22
    var port = 8099
    var labels: [String: String] = [:]
    var colors: [String: NSColor] = [:]

    static func load() -> AppSettings {
        var settings = AppSettings()
        let defaultLabels = ["ok": "Доступен", "degraded": "Нестабильно",
                             "blocked": "Похоже на блокировку", "down": "Недоступен",
                             "unknown": "Нет данных"]
        let defaultColors: [String: NSColor] = [
            "ok": .systemGreen, "degraded": .systemOrange,
            "blocked": .systemRed, "down": .systemRed, "unknown": .systemGray,
        ]
        settings.labels = defaultLabels
        settings.colors = defaultColors
        var mode = ProcessInfo.processInfo.environment["NETBAR_MODE"] ?? ""

        var configPath = ProcessInfo.processInfo.environment["NETMON_CONFIG"]
        if configPath == nil, let dir = ProcessInfo.processInfo.environment["NETMON_DIR"] {
            configPath = dir + "/config.json"
        }
        if configPath == nil, let raw = try? String(contentsOf:
            FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".netmon-dir"),
            encoding: .utf8) {
            configPath = raw.trimmingCharacters(in: .whitespacesAndNewlines) + "/config.json"
        }
        guard let path = configPath,
              let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            if !["overlay", "dot", "text"].contains(mode) { mode = "dot" }
            settings.mode = mode
            return settings
        }
        if let server = root["server"] as? [String: Any],
           let port = server["port"] as? Int, port > 0, port < 65536 {
            settings.port = port
        }
        if let labels = root["status_labels"] as? [String: String] {
            settings.labels = defaultLabels.merging(labels) { _, new in new }
        }
        if let colors = root["status_colors"] as? [String: String] {
            for (status, hex) in colors {
                if let color = colorFromHex(hex) { settings.colors[status] = color }
            }
        }
        if let display = root["display"] as? [String: Any],
           let macos = display["macos_menubar"] as? [String: Any] {
            if mode.isEmpty, let value = macos["mode"] as? String { mode = value }
            if let value = macos["overlay_window_pt"] as? Double { settings.overlayWindow = CGFloat(value) }
            if let value = macos["overlay_dot_pt"] as? Double { settings.overlayDot = CGFloat(value) }
            if let value = macos["status_item_pt"] as? Double { settings.statusItem = CGFloat(value) }
        }
        if !["overlay", "dot", "text"].contains(mode) { mode = "dot" }
        settings.mode = mode
        return settings
    }
}

let APP_SETTINGS = AppSettings.load()
let serverBaseURL = URL(string: "http://127.0.0.1:\(APP_SETTINGS.port)")!
let monitorStateURL = serverBaseURL.appendingPathComponent("api/state")
let monitorCheckURL = serverBaseURL.appendingPathComponent("api/check")
let dashboardURL = serverBaseURL

// MARK: - Утилиты

func logLine(_ message: String) {
    let stamp = ISO8601DateFormatter().string(from: Date())
    guard let data = "\(stamp) \(message)\n".data(using: .utf8) else { return }
    let url = URL(fileURLWithPath: logPath)
    if let handle = try? FileHandle(forWritingTo: url) {
        handle.seekToEndOfFile()
        handle.write(data)
        try? handle.close()
    } else {
        try? data.write(to: url)
    }
}

func uiFont(_ size: CGFloat, _ weight: NSFont.Weight) -> NSFont {
    NSFont.systemFont(ofSize: size, weight: weight)
}

func statusColor(_ status: String) -> NSColor {
    if let color = APP_SETTINGS.colors[status] { return color }
    switch status {
    case "ok": return .systemGreen
    case "degraded": return .systemOrange
    case "blocked", "down": return .systemRed
    default: return .systemGray
    }
}

func statusGlyph(_ status: String) -> String {
    switch status {
    case "ok": return "✓"
    case "degraded": return "!"
    case "blocked", "down": return "✕"
    default: return "?"
    }
}

func statusName(_ status: String) -> String {
    if let label = APP_SETTINGS.labels[status] { return label.lowercased() }
    switch status {
    case "ok": return "доступен"
    case "degraded": return "нестабильно"
    case "blocked": return "похоже на блокировку"
    case "down": return "недоступен"
    default: return "нет данных"
    }
}

func dotImage(_ color: NSColor) -> NSImage {
    let size = NSSize(width: 14, height: 14)
    let image = NSImage(size: size)
    image.lockFocus()
    color.setFill()
    NSBezierPath(ovalIn: NSRect(x: 3, y: 3, width: 8, height: 8)).fill()
    image.unlockFocus()
    image.isTemplate = false
    return image
}

func findProjectDirectory() -> URL? {
    if let env = ProcessInfo.processInfo.environment["NETMON_DIR"], !env.isEmpty {
        let url = URL(fileURLWithPath: env)
        if FileManager.default.fileExists(atPath: url.appendingPathComponent("server.py").path) {
            return url
        }
    }
    var current = URL(fileURLWithPath: CommandLine.arguments[0])
        .resolvingSymlinksInPath()
        .deletingLastPathComponent()
    for _ in 0..<7 {
        if FileManager.default.fileExists(atPath: current.appendingPathComponent("server.py").path) {
            return current
        }
        let parent = current.deletingLastPathComponent()
        if parent.path == current.path { break }
        current = parent
    }
    // Путь к проекту, записанный установщиком: нужен, когда NetBar.app лежит
    // отдельно (например, в ~/Applications) и запускается двойным кликом.
    let marker = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".netmon-dir")
    if let raw = try? String(contentsOf: marker, encoding: .utf8) {
        let candidate = URL(fileURLWithPath: raw.trimmingCharacters(in: .whitespacesAndNewlines))
        if FileManager.default.fileExists(atPath: candidate.appendingPathComponent("server.py").path) {
            return candidate
        }
    }
    return nil
}

// MARK: - Самопроверка (--dump)

func fetchStateSynchronously() -> [String: Any]? {
    var request = URLRequest(url: monitorStateURL,
                             cachePolicy: .reloadIgnoringLocalCacheData,
                             timeoutInterval: 5)
    request.httpMethod = "GET"
    let semaphore = DispatchSemaphore(value: 0)
    var payload: Data?
    URLSession.shared.dataTask(with: request) { data, _, _ in
        payload = data
        semaphore.signal()
    }.resume()
    _ = semaphore.wait(timeout: .now() + 8)
    guard let data = payload,
          let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        return nil
    }
    return object
}

func dumpStatus() -> Int32 {
    guard let state = fetchStateSynchronously() else {
        print("MONITOR UNAVAILABLE: \(monitorStateURL.absoluteString) недоступен")
        return 2
    }
    let summary = state["summary"] as? [String: Any] ?? [:]
    let internet = state["internet"] as? [String: Any] ?? [:]
    print("overall: \(summary["overall_label"] as? String ?? "?")")
    print("internet: \(internet["status"] as? String ?? "?") (\(internet["label"] as? String ?? "?"))")
    print("checks: \(summary["checks_ok"] ?? "?")/\(summary["checks_total"] ?? "?") · incidents: \(summary["active_incidents"] ?? "?")")
    if let services = state["services"] as? [[String: Any]] {
        for service in services {
            let id = service["id"] as? String ?? "?"
            let status = service["status"] as? String ?? "unknown"
            let name = service["name"] as? String ?? id
            let reason = service["status_reason"] as? String ?? ""
            print("\(name) [\(id)]: \(status) — \(statusName(status)); \(reason)")
        }
        var title = ""
        for id in ["youtube", "telegram"] {
            if let svc = services.first(where: { ($0["id"] as? String) == id }),
               let status = svc["status"] as? String {
                title += (id == "youtube" ? "YT" : "TG") + statusGlyph(status) + " "
            }
        }
        print("menu title: \(title.trimmingCharacters(in: .whitespaces))")
    }
    return 0
}

if CommandLine.arguments.contains("--dump") {
    exit(dumpStatus())
}

// MARK: - Плавающая точка (overlay)

final class DotView: NSView {
    var dotColor: NSColor = .systemGray {
        didSet { needsDisplay = true }
    }
    var dotDiameter: CGFloat = 9.6
    var onActivate: (() -> Void)?
    var onContextMenu: (() -> Void)?
    var onDragEnd: (() -> Void)?

    private var dragStartMouse = NSPoint.zero
    private var dragStartOrigin = NSPoint.zero
    private var didDrag = false

    override var isOpaque: Bool { false }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.clear.setFill()
        dirtyRect.fill()
        let rect = bounds.insetBy(dx: (bounds.width - dotDiameter) / 2,
                                  dy: (bounds.height - dotDiameter) / 2)
        let path = NSBezierPath(ovalIn: rect)
        dotColor.setFill()
        path.fill()
        NSColor.black.withAlphaComponent(0.30).setStroke()
        path.lineWidth = 1
        path.stroke()
    }

    override func mouseDown(with event: NSEvent) {
        didDrag = false
        dragStartMouse = NSEvent.mouseLocation
        dragStartOrigin = window?.frame.origin ?? .zero
    }

    override func mouseDragged(with event: NSEvent) {
        guard let window = window else { return }
        let current = NSEvent.mouseLocation
        let dx = current.x - dragStartMouse.x
        let dy = current.y - dragStartMouse.y
        if abs(dx) + abs(dy) > 3 { didDrag = true }
        window.setFrameOrigin(NSPoint(x: dragStartOrigin.x + dx, y: dragStartOrigin.y + dy))
    }

    override func mouseUp(with event: NSEvent) {
        if didDrag {
            onDragEnd?()
        } else {
            onActivate?()
        }
    }

    override func rightMouseUp(with event: NSEvent) {
        onContextMenu?()
    }
}

// MARK: - Приложение

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem?
    private var overlayWindow: NSWindow?
    private var overlayView: DotView?
    private var timer: Timer?
    private var lastState: [String: Any]?
    private var lastUpdated: Date?
    private var serverAvailable = false
    private var checking = false
    private var lastTitleSignature = ""
    private var displayMode: String { APP_SETTINGS.mode }
    private let menu = NSMenu()
    private let projectDirectory = findProjectDirectory()

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        menu.delegate = self
        if displayMode == "overlay" {
            createOverlay()
        } else {
            createStatusItem()
        }
        rebuildMenu()
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 10.0, repeats: true) { [weak self] _ in
            self?.refresh()
        }
        timer?.tolerance = 2.0
        NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil, queue: .main) { [weak self] _ in
            self?.positionOverlay()
        }
        logLine("NetBar запущен (режим \(displayMode), server dir: \(projectDirectory?.path ?? "не найден"))")
        if displayMode == "overlay" {
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in
                guard let self = self, let window = self.overlayWindow else { return }
                logLine("overlay: frame=\(NSStringFromRect(window.frame)) level=\(window.level.rawValue) visible=\(window.isVisible)")
            }
        } else if let button = statusItem?.button {
            let frameText = button.window.map { NSStringFromRect($0.frame) } ?? "нет окна"
            logLine("status item: visible=\(statusItem?.isVisible ?? false) button=\(button != nil) image=\(String(describing: button.image?.size)) frame=\(frameText) barHeight=\(NSStatusBar.system.thickness)")
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate()
    }

    // MARK: создание индикаторов

    private func createStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if displayMode != "text" {
            statusItem?.length = APP_SETTINGS.statusItem
        }
        statusItem?.menu = menu
    }

    private func createOverlay() {
        let size: CGFloat = APP_SETTINGS.overlayWindow
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: size, height: size),
                              styleMask: [.borderless],
                              backing: .buffered,
                              defer: false)
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = false
        window.level = NSWindow.Level(rawValue: NSWindow.Level.statusBar.rawValue + 1)
        window.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle, .fullScreenAuxiliary]
        window.ignoresMouseEvents = false

        let view = DotView(frame: NSRect(x: 0, y: 0, width: size, height: size))
        view.dotDiameter = APP_SETTINGS.overlayDot
        view.onActivate = { [weak self] in self?.showOverlayMenu() }
        view.onContextMenu = { [weak self] in self?.openDashboard() }
        view.onDragEnd = { [weak self] in self?.saveOverlayPosition() }
        window.contentView = view

        overlayWindow = window
        overlayView = view
        positionOverlay()
        window.orderFrontRegardless()
    }

    // MARK: позиция плавающей точки

    private func positionOverlay() {
        guard let window = overlayWindow,
              let screen = NSScreen.main ?? NSScreen.screens.first else { return }
        let size = window.frame.size
        let screenFrame = screen.frame
        let menuBarHeight = max(24, screen.frame.maxY - screen.visibleFrame.maxY)
        // По умолчанию — сразу под строкой меню в правом верхнем углу, рядом с часами.
        var origin = NSPoint(x: screenFrame.maxX - size.width - 14,
                             y: screenFrame.maxY - menuBarHeight - size.height - 8)
        if let saved = UserDefaults.standard.array(forKey: "overlayOrigin") as? [Double],
           saved.count == 2 {
            let candidate = NSPoint(x: saved[0], y: saved[1])
            let rect = NSRect(x: candidate.x, y: candidate.y, width: size.width, height: size.height)
            if screenFrame.contains(rect) {
                origin = candidate
            }
        }
        window.setFrameOrigin(origin)
    }

    private func saveOverlayPosition() {
        guard let window = overlayWindow,
              let screen = NSScreen.main ?? NSScreen.screens.first else { return }
        var origin = window.frame.origin
        let size = window.frame.size
        let screenFrame = screen.frame
        // не даём утащить точку за пределы экрана
        origin.x = min(max(origin.x, screenFrame.minX), screenFrame.maxX - size.width)
        origin.y = min(max(origin.y, screenFrame.minY), screenFrame.maxY - size.height)
        window.setFrameOrigin(origin)
        UserDefaults.standard.set([origin.x, origin.y], forKey: "overlayOrigin")
        logLine("позиция плавающей точки сохранена: \(NSStringFromPoint(origin))")
    }

    @objc private func resetOverlayPosition() {
        UserDefaults.standard.removeObject(forKey: "overlayOrigin")
        positionOverlay()
        logLine("позиция плавающей точки сброшена")
    }

    private func showOverlayMenu() {
        rebuildMenu()
        guard let view = overlayView else { return }
        menu.popUp(positioning: nil, at: NSPoint(x: 0, y: view.bounds.height + 4), in: view)
    }

    // MARK: сеть

    private func refresh() {
        var request = URLRequest(url: monitorStateURL,
                                 cachePolicy: .reloadIgnoringLocalCacheData,
                                 timeoutInterval: 4)
        request.httpMethod = "GET"
        URLSession.shared.dataTask(with: request) { [weak self] data, _, _ in
            DispatchQueue.main.async {
                guard let self = self else { return }
                if let data = data,
                   let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    self.lastState = object
                    self.serverAvailable = true
                    self.lastUpdated = Date()
                } else {
                    self.serverAvailable = false
                }
                self.updateAppearance()
                if !self.checking { self.rebuildMenu() }
            }
        }.resume()
    }

    private func forceCheck() {
        guard !checking else { return }
        checking = true
        rebuildMenu()
        var request = URLRequest(url: monitorCheckURL, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 30)
        request.httpMethod = "POST"
        request.httpBody = Data()
        URLSession.shared.dataTask(with: request) { [weak self] _, _, _ in
            DispatchQueue.main.async {
                self?.checking = false
                self?.refresh()
            }
        }.resume()
    }

    // MARK: отображение

    private func serviceState(_ id: String) -> [String: Any]? {
        guard let services = lastState?["services"] as? [[String: Any]] else { return nil }
        return services.first { ($0["id"] as? String) == id }
    }

    private func aggregateStatus(_ statuses: [String]) -> String {
        let values = Set(statuses)
        if values.contains("blocked") || values.contains("down") { return "down" }
        if values.contains("degraded") { return "degraded" }
        if values.contains("unknown") { return "unknown" }
        return "ok"
    }

    private func segment(_ label: String, status: String) -> NSAttributedString {
        let attributes: [NSAttributedString.Key: Any] = [
            .foregroundColor: statusColor(status),
            .font: uiFont(12, .semibold),
        ]
        let text = NSMutableAttributedString(string: label, attributes: attributes)
        text.append(NSAttributedString(string: statusGlyph(status), attributes: attributes))
        return text
    }

    private func tooltipText(youtube: String, telegram: String, internet: String) -> String {
        var tooltip = "YouTube: \(statusName(youtube))\nTelegram: \(statusName(telegram))\nИнтернет: \(statusName(internet))"
        if let updated = lastUpdated {
            let formatter = DateFormatter()
            formatter.dateFormat = "HH:mm:ss"
            tooltip += "\nОбновлено \(formatter.string(from: updated))"
        }
        return tooltip
    }

    private func updateAppearance() {
        if displayMode == "overlay" {
            updateOverlay()
        } else {
            updateStatusItem()
        }
    }

    private func updateOverlay() {
        guard let view = overlayView else { return }
        guard serverAvailable, let state = lastState else {
            view.dotColor = .systemGray
            view.toolTip = "Монитор недоступен. Клик — меню, чтобы запустить сервер."
            if lastTitleSignature != "server-down" {
                lastTitleSignature = "server-down"
                logLine("сервер недоступен")
            }
            return
        }
        let youtube = (serviceState("youtube")?["status"] as? String) ?? "unknown"
        let telegram = (serviceState("telegram")?["status"] as? String) ?? "unknown"
        let internet = ((state["internet"] as? [String: Any])?["status"] as? String) ?? "unknown"
        let aggregate = aggregateStatus([youtube, telegram, internet])
        view.dotColor = statusColor(aggregate)
        view.toolTip = tooltipText(youtube: youtube, telegram: telegram, internet: internet)
        let signature = "\(internet)|\(youtube)|\(telegram)"
        if signature != lastTitleSignature {
            lastTitleSignature = signature
            logLine("статус: NET=\(internet) YT=\(youtube) TG=\(telegram) · точка=\(aggregate)")
        }
    }

    private func updateStatusItem() {
        guard let button = statusItem?.button else { return }
        let dotMode = displayMode != "text"
        button.imagePosition = .imageOnly

        if !serverAvailable || lastState == nil {
            if dotMode {
                button.attributedTitle = NSAttributedString(string: "")
                button.image = dotImage(.systemGray)
                button.imageScaling = .scaleProportionallyDown
            } else {
                button.image = nil
                button.attributedTitle = NSAttributedString(
                    string: "NET ?",
                    attributes: [.foregroundColor: NSColor.secondaryLabelColor,
                                 .font: uiFont(12, .semibold)])
            }
            button.toolTip = "Монитор недоступен. Откройте меню, чтобы запустить сервер."
            if lastTitleSignature != "server-down" {
                lastTitleSignature = "server-down"
                logLine("сервер недоступен")
            }
            return
        }

        let youtube = (serviceState("youtube")?["status"] as? String) ?? "unknown"
        let telegram = (serviceState("telegram")?["status"] as? String) ?? "unknown"
        let internet = ((lastState?["internet"] as? [String: Any])?["status"] as? String) ?? "unknown"
        let aggregate = aggregateStatus([youtube, telegram, internet])

        if dotMode {
            button.attributedTitle = NSAttributedString(string: "")
            button.image = dotImage(statusColor(aggregate))
            button.imageScaling = .scaleProportionallyDown
            button.setAccessibilityLabel(
                "Мониторинг сети: YouTube \(statusName(youtube)), Telegram \(statusName(telegram)), интернет \(statusName(internet))")
        } else {
            button.image = nil
            let title = NSMutableAttributedString()
            if internet != "ok" {
                title.append(segment("NET ", status: internet))
            }
            title.append(segment("YT", status: youtube))
            title.append(NSAttributedString(string: " ", attributes: [.font: uiFont(12, .regular)]))
            title.append(segment("TG", status: telegram))
            button.attributedTitle = title
        }

        let signature = "\(internet)|\(youtube)|\(telegram)"
        if signature != lastTitleSignature {
            lastTitleSignature = signature
            logLine("статус: NET=\(internet) YT=\(youtube) TG=\(telegram) · точка=\(aggregate)")
        }
        button.toolTip = tooltipText(youtube: youtube, telegram: telegram, internet: internet)
    }

    // MARK: меню

    private func addDisabled(_ title: String, small: Bool = false, color: NSColor = .secondaryLabelColor) {
        let item = NSMenuItem(title: "", action: nil, keyEquivalent: "")
        item.attributedTitle = NSAttributedString(
            string: title,
            attributes: [.foregroundColor: color,
                         .font: small ? uiFont(11, .regular) : uiFont(12, .medium)])
        item.isEnabled = false
        menu.addItem(item)
    }

    private func addAction(_ title: String, _ selector: Selector) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: selector, keyEquivalent: "")
        item.target = self
        item.isEnabled = true
        menu.addItem(item)
        return item
    }

    private func addService(_ service: [String: Any]) {
        let id = service["id"] as? String ?? "?"
        let name = service["name"] as? String ?? id
        let status = service["status"] as? String ?? "unknown"
        let latency = service["latency_ms"] as? Int
        let label = service["status_label"] as? String ?? statusName(status)

        var title = id == "internet" ? "\(name): \(label)" : "\(name) — \(label)"
        if let latency = latency, id != "internet" {
            title += " · \(latency) мс"
        }
        let item = NSMenuItem(title: title, action: #selector(openDashboard), keyEquivalent: "")
        item.target = self
        item.image = NSImage(size: NSSize(width: 12, height: 12))
        item.image = dotImage(statusColor(status))
        item.attributedTitle = NSAttributedString(
            string: title,
            attributes: [.foregroundColor: statusColor(status),
                         .font: uiFont(12, .medium)])
        menu.addItem(item)

        if let reason = service["status_reason"] as? String, !reason.isEmpty {
            let truncated = reason.count > 82 ? String(reason.prefix(82)) + "…" : reason
            addDisabled("    " + truncated, small: true)
        }
        if let incident = service["active_incident"] as? [String: Any],
           let duration = incident["duration_sec"] as? Int, duration > 0 {
            let minutes = duration / 60
            let seconds = duration % 60
            let human = minutes > 0 ? "\(minutes) мин \(seconds) с" : "\(seconds) с"
            addDisabled("    сбой идёт: \(human)", small: true)
        }
    }

    private func rebuildMenu() {
        menu.removeAllItems()

        if !serverAvailable || lastState == nil {
            addDisabled("Монитор не отвечает")
            addDisabled(serverBaseURL.absoluteString, small: true)
            menu.addItem(NSMenuItem.separator())
            _ = addAction("Запустить сервер мониторинга", #selector(startServer))
        } else {
            let summary = lastState?["summary"] as? [String: Any] ?? [:]
            let updated = DateFormatter.localizedString(from: lastUpdated ?? Date(),
                                                        dateStyle: .none, timeStyle: .medium)
            addDisabled("Мониторинг доступности · \(updated)")
            let checks = "\(summary["checks_ok"] ?? "?")/\(summary["checks_total"] ?? "?") проверок"
            let incidents = summary["active_incidents"] as? Int ?? 0
            addDisabled("\(checks) · инцидентов: \(incidents)", small: true)
            menu.addItem(NSMenuItem.separator())
            for id in ["youtube", "telegram", "internet"] {
                if let service = serviceState(id) { addService(service) }
            }
            menu.addItem(NSMenuItem.separator())
            let checkItem = addAction(checking ? "Проверка идёт…" : "Проверить сейчас", #selector(checkNow))
            checkItem.isEnabled = !checking
            _ = addAction("Открыть дашборд", #selector(openDashboard))
        }

        if displayMode == "overlay" {
            menu.addItem(NSMenuItem.separator())
            _ = addAction("Сбросить позицию точки", #selector(resetOverlayPosition))
        }
        menu.addItem(NSMenuItem.separator())
        _ = addAction("Выход", #selector(quitApp))
    }

    // MARK: действия

    @objc private func checkNow() {
        forceCheck()
    }

    @objc private func openDashboard() {
        NSWorkspace.shared.open(dashboardURL)
    }

    @objc private func startServer() {
        guard let directory = projectDirectory else {
            logLine("не найден каталог с server.py — задайте NETMON_DIR")
            return
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["python3", "-B", "server.py", "--port", String(APP_SETTINGS.port)]
        process.currentDirectoryURL = directory
        let logURL = URL(fileURLWithPath: "/tmp/netmon-8099.log")
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        if let handle = try? FileHandle(forWritingTo: logURL) {
            handle.seekToEndOfFile()
            process.standardOutput = handle
            process.standardError = handle
        }
        do {
            try process.run()
            logLine("сервер запущен, pid \(process.processIdentifier)")
            DispatchQueue.main.asyncAfter(deadline: .now() + 3.0) { [weak self] in
                self?.refresh()
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 12.0) { [weak self] in
                self?.refresh()
            }
        } catch {
            logLine("не удалось запустить сервер: \(error)")
        }
    }

    @objc private func quitApp() {
        NSApp.terminate(nil)
    }
}

extension AppDelegate: NSMenuDelegate {
    func menuWillOpen(_ menu: NSMenu) {
        rebuildMenu()
    }
}

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.run()
