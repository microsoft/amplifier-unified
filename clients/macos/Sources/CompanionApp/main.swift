#if os(macOS)
import AppKit
import CompanionCore
import CryptoKit
import Darwin
import Security
import UserNotifications

enum Keychain {
    static func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: "com.microsoft.amplifier.companion",
         kSecAttrAccount as String: account]
    }
    static func read(_ account: String) throws -> String? {
        var q = query(account); q[kSecReturnData as String] = true
        var result: CFTypeRef?
        let status = SecItemCopyMatching(q as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data,
              let token = String(data: data, encoding: .utf8) else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(status)) }
        return token
    }
    static func save(_ token: String, for account: String) throws {
        let q = query(account), data = Data(token.utf8)
        var status = SecItemUpdate(q as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if status == errSecItemNotFound {
            var add = q; add[kSecValueData as String] = data
            status = SecItemAdd(add as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(status)) }
    }
}

@MainActor final class CompanionController: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    // Keep the preview identity and storage keys so the rename preserves saved
    // connections, notification permission, position, and pending reply IDs.
    private let defaults = UserDefaults.standard
    private let journalURL = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("Amplifier Companion/journal.json")
    private var journal = Journal()
    private var storageReady = false
    private var journalLock: Int32 = -1
    private var host: HostClient?
    private var poll: Task<Void, Never>?
    private var sends = Set<String>()
    private var latest: CompanionSnapshot?
    private var status = "Not connected"
    private var connected = false
    private var item: NSStatusItem!
    private var panel: NSPanel!
    private var sprite: SpriteView!
    private var settings: SettingsWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        do {
            try FileManager.default.createDirectory(at: journalURL.deletingLastPathComponent(),
                withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            journalLock = Darwin.open(journalURL.deletingLastPathComponent().appendingPathComponent("client.lock").path,
                                     O_CREAT | O_RDWR | O_NOFOLLOW, S_IRUSR | S_IWUSR)
            guard journalLock >= 0, flock(journalLock, LOCK_EX | LOCK_NB) == 0 else {
                NSApp.terminate(nil); return
            }
            journal = try Journal.load(from: journalURL); storageReady = true
        }
        catch { status = "Saved replies could not be read. Open Settings for details." }
        UNUserNotificationCenter.current().delegate = self
        let reply = UNTextInputNotificationAction(identifier: "reply", title: "Reply", options: [],
            textInputButtonTitle: "Send", textInputPlaceholder: "Reply to this chat")
        let open = UNNotificationAction(identifier: "open", title: "Open chat", options: [.foreground])
        UNUserNotificationCenter.current().setNotificationCategories([
            UNNotificationCategory(identifier: "response", actions: [reply, open], intentIdentifiers: []),
            UNNotificationCategory(identifier: "attention", actions: [open], intentIdentifiers: [])])
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.image = NSImage(systemSymbolName: "waveform", accessibilityDescription: "Amplifier Tempo")
        panel = NSPanel(contentRect: NSRect(x: 80, y: 100, width: 128, height: 208 * 2 / 3 + 22),
            styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.isFloatingPanel = true; panel.level = .floating
        panel.isOpaque = false; panel.backgroundColor = .clear; panel.hasShadow = false
        panel.hidesOnDeactivate = false; panel.isMovableByWindowBackground = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.setFrameAutosaveName("CompanionPosition")
        panel.setContentSize(NSSize(width: 128, height: 208 * 2 / 3 + 22))
        sprite = SpriteView(frame: panel.contentView!.bounds)
        sprite.open = { [weak self] in self?.openHost() }
        panel.contentView = sprite
        applyAppearance(); rebuildMenu()
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(wake),
            name: NSWorkspace.didWakeNotification, object: nil)
        if defaults.string(forKey: "endpoint") == nil { showSettings() }
        else { connect() }
    }
    func applicationWillTerminate(_ notification: Notification) {
        poll?.cancel()
        if journalLock >= 0 { Darwin.close(journalLock) }
    }
    @objc private func wake() { connect() }
    private func persist() throws {
        guard storageReady else { throw NSError(domain: "Companion", code: 1,
            userInfo: [NSLocalizedDescriptionKey: "Saved replies could not be loaded. Keep the journal file for recovery; no reply was sent."] ) }
        try journal.save(to: journalURL)
    }
    private func connect() {
        poll?.cancel(); connected = false; latest = nil; host = nil
        do {
            let endpoint = try Endpoint(defaults.string(forKey: "endpoint") ?? "http://127.0.0.1:8941")
            guard let token = try Keychain.read(endpoint.url.absoluteString), !token.isEmpty else {
                status = "Connect to your Unified host"; rebuildMenu(); return
            }
            try persist()
            let client = HostClient(endpoint: endpoint, token: token, clientID: journal.clientID)
            host = client; status = "Connecting…"; rebuildMenu()
            poll = Task { [weak self] in
                var delay: UInt64 = 2
                while !Task.isCancelled {
                    do {
                        try await client.attach()
                        while !Task.isCancelled {
                            let value = try await client.snapshot()
                            guard !Task.isCancelled, let self else { return }
                            self.receive(value, client: client); delay = 2
                            try await Task.sleep(nanoseconds: 5_000_000_000)
                        }
                    } catch {
                        guard !Task.isCancelled, let self else { return }
                        self.connected = false; self.status = error.localizedDescription
                        self.rebuildMenu()
                        try? await Task.sleep(nanoseconds: delay * 1_000_000_000)
                        delay = min(delay * 2, 60)
                    }
                }
            }
        } catch { status = error.localizedDescription; rebuildMenu() }
    }
    private func receive(_ value: CompanionSnapshot, client: HostClient) {
        latest = value; connected = true
        status = value.workingCount > 0 ? "\(value.workingCount) working" : "Ready"
        var next = journal
        let alerts = next.observe(value, endpoint: client.endpoint.url.absoluteString,
            enabled: defaults.bool(forKey: "notifications"), pinnedOnly: !defaults.bool(forKey: "notifyAll"))
        do {
            // Persist the seen ledger before any OS side effect.
            if next.seen != journal.seen { try next.save(to: journalURL) }
            journal = next
            for alert in alerts { deliver(alert, snapshot: value, endpoint: client.endpoint) }
        } catch { status = "Could not save notification state; notifications paused." }
        rebuildMenu()
    }
    private func deliver(_ alert: AttentionItem, snapshot: CompanionSnapshot, endpoint: Endpoint) {
        let content = UNMutableNotificationContent()
        content.title = "Amplifier"; content.body = alert.summary
        content.categoryIdentifier = alert.allowsReply ? "response" : "attention"
        content.threadIdentifier = snapshot.hostIdentity + ":" + alert.sessionId
        content.userInfo = ["endpoint": endpoint.url.absoluteString, "host": snapshot.hostIdentity,
                            "session": alert.sessionId, "replyAllowed": alert.allowsReply]
        let key = endpoint.url.absoluteString + snapshot.hostIdentity + alert.key
        let id = SHA256.hash(data: Data(key.utf8)).map { String(format: "%02x", $0) }.joined()
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: id, content: content, trigger: nil)) { [weak self] error in
            if error != nil { Task { @MainActor in self?.status = "Notifications unavailable. Check macOS notification settings."; self?.rebuildMenu() } }
        }
    }
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse, withCompletionHandler completionHandler: @escaping () -> Void) {
        Task { @MainActor in
            defer { completionHandler() }
            let info = response.notification.request.content.userInfo
            guard let endpoint = info["endpoint"] as? String, let hostID = info["host"] as? String,
                  let sessionID = info["session"] as? String else { return }
            if let text = response as? UNTextInputNotificationResponse, info["replyAllowed"] as? Bool == true,
               !text.userText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                do {
                    try persist()
                    var next = journal
                    let (entry, isNew) = next.enqueue(notificationID: response.notification.request.identifier,
                        endpoint: endpoint, hostIdentity: hostID, sessionID: sessionID, text: text.userText)
                    try next.save(to: journalURL) // Commit before publishing the in-memory outbox.
                    journal = next
                    if isNew { send(entry) }
                } catch { status = error.localizedDescription; rebuildMenu() }
            } else if response.actionIdentifier != UNNotificationDismissActionIdentifier {
                if let url = try? Endpoint(endpoint).chatURL(sessionID) { NSWorkspace.shared.open(url) }
            }
        }
    }
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
        willPresent notification: UNNotification, withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .list])
    }
    private func send(_ entry: PendingReply) {
        guard let host, !sends.contains(entry.id) else { status = "Reply saved. Connect to its host, then use Retry."; rebuildMenu(); return }
        do { try persist() } catch { status = error.localizedDescription; rebuildMenu(); return }
        sends.insert(entry.id); status = "Sending reply…"; rebuildMenu()
        Task {
            do {
                try await host.send(entry)
                journal.mark(entry.id, "accepted"); try persist()
                status = "Reply accepted by the host"
            } catch {
                journal.mark(entry.id, "unconfirmed")
                do { try persist(); status = error.localizedDescription }
                catch { status = "Reply delivery is unconfirmed; its recovery record could not be updated." }
            }
            sends.remove(entry.id); rebuildMenu()
        }
    }
    private func rebuildMenu() {
        guard item != nil else { return }
        let menu = NSMenu()
        let statusRow = menu.addItem(withTitle: status, action: nil, keyEquivalent: "")
        statusRow.isEnabled = false
        menu.addItem(withTitle: "Open Amplifier", action: #selector(openHost), keyEquivalent: "o").target = self
        if let latest {
            let scope = latest.items.filter { defaults.bool(forKey: "notifyAll") || $0.pinned }
            item.button?.title = latest.workingCount > 0 ? " \(latest.workingCount)" : ""
            sprite.motion = Motion.status(connected: connected, working: latest.workingCount, attention: scope.filter { $0.kind != "completion" }.count)
            let chats = latest.sessions.prefix(10)
            if !chats.isEmpty { menu.addItem(.separator()) }
            for chat in chats {
                let row = menu.addItem(withTitle: String(chat.title.prefix(55)) + " · " + chat.status,
                                       action: #selector(openChat(_:)), keyEquivalent: "")
                row.representedObject = chat.id; row.target = self
            }
            if latest.sessionsTruncated || latest.itemsTruncated {
                menu.addItem(withTitle: "More activity is available in Amplifier", action: #selector(openHost), keyEquivalent: "").target = self
            }
        } else { item.button?.title = ""; sprite.motion = .waiting }
        let pending = journal.replies.filter { $0.status != "accepted" }
        if !pending.isEmpty {
            menu.addItem(.separator())
            menu.addItem(withTitle: "Saved replies — check chat before retrying", action: nil, keyEquivalent: "").isEnabled = false
            for entry in pending.prefix(10) {
                let row = menu.addItem(withTitle: String(entry.text.prefix(40)), action: nil, keyEquivalent: "")
                let actions = NSMenu()
                let view = actions.addItem(withTitle: "Open original chat", action: #selector(openReplyChat(_:)), keyEquivalent: "")
                view.representedObject = entry.id; view.target = self
                let retry = actions.addItem(withTitle: "Retry same reply", action: #selector(retry(_:)), keyEquivalent: "")
                retry.representedObject = entry.id; retry.target = self; retry.isEnabled = !sends.contains(entry.id)
                row.submenu = actions
            }
        }
        menu.addItem(.separator())
        menu.addItem(withTitle: panel.isVisible ? "Hide Tempo" : "Show Tempo", action: #selector(toggleCompanion), keyEquivalent: "").target = self
        menu.addItem(withTitle: "Settings…", action: #selector(showSettings), keyEquivalent: ",").target = self
        menu.addItem(withTitle: "Reconnect", action: #selector(wake), keyEquivalent: "").target = self
        menu.addItem(withTitle: "Quit Tempo", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.menu = menu; sprite.setAccessibilityLabel("Amplifier: " + status)
        sprite.caption = connected ? ((latest?.workingCount ?? 0) > 0 ? "Working" : "Ready") : "Offline"
    }
    @objc private func retry(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String,
              let entry = journal.replies.first(where: { $0.id == id }) else { return }
        send(entry)
    }
    @objc private func openReplyChat(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String,
              let entry = journal.replies.first(where: { $0.id == id }),
              let url = try? Endpoint(entry.endpoint).chatURL(entry.sessionID) else { return }
        NSWorkspace.shared.open(url)
    }
    @objc private func openChat(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String, let url = host?.endpoint.chatURL(id) else { return }
        NSWorkspace.shared.open(url)
    }
    @objc private func openHost() {
        if let endpoint = try? Endpoint(defaults.string(forKey: "endpoint") ?? "http://127.0.0.1:8941") { NSWorkspace.shared.open(endpoint.url) }
    }
    @objc private func toggleCompanion() {
        defaults.set(panel.isVisible, forKey: "hideCompanion"); applyAppearance(); rebuildMenu()
    }
    private func applyAppearance() {
        sprite.load(defaults.string(forKey: "shape") ?? "arc")
        let visible = !defaults.bool(forKey: "hideCompanion")
        if visible { panel.orderFrontRegardless() } else { panel.orderOut(nil) }
        sprite.animate(visible)
    }
    @objc private func showSettings() {
        if settings == nil {
            settings = SettingsWindow(defaults: defaults, recoveryPath: storageReady ? nil : journalURL.path) { [weak self] address, token, shape, notifications, all in
                guard let self else { return }
                let endpoint = try Endpoint(address)
                if !token.isEmpty { try Keychain.save(token, for: endpoint.url.absoluteString) }
                guard try Keychain.read(endpoint.url.absoluteString)?.isEmpty == false else {
                    throw NSError(domain: "Companion", code: 2, userInfo: [NSLocalizedDescriptionKey: "Enter this host’s control token."])
                }
                self.defaults.set(endpoint.url.absoluteString, forKey: "endpoint")
                self.defaults.set(shape, forKey: "shape"); self.defaults.set(all, forKey: "notifyAll")
                self.defaults.set(notifications, forKey: "notifications")
                if notifications {
                    UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { allowed, _ in
                        Task { @MainActor in
                            if !allowed { self.status = "Allow notifications in macOS System Settings."; self.rebuildMenu() }
                        }
                    }
                }
                self.applyAppearance(); self.connect()
            }
        }
        settings?.showWindow(nil); NSApp.activate(ignoringOtherApps: true)
    }
}

MainActor.assumeIsolated {
    let app = NSApplication.shared
    let controller = CompanionController()
    app.delegate = controller
    withExtendedLifetime(controller) { app.run() }
}
#else
print("Amplifier Tempo requires macOS 13 or later.")
#endif
