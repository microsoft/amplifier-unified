#if os(macOS)
import AppKit

@MainActor final class SettingsWindow: NSWindowController {
    private let address = NSTextField(string: "")
    private let token = NSSecureTextField(string: "")
    private let shape = NSPopUpButton()
    private let notifications = NSButton(checkboxWithTitle: "Show desktop notifications", target: nil, action: nil)
    private let all = NSButton(checkboxWithTitle: "Include unpinned chats", target: nil, action: nil)
    private let errorLabel = NSTextField(wrappingLabelWithString: "")
    private let save: (String, String, String, Bool, Bool) throws -> Void

    init(defaults: UserDefaults, recoveryPath: String?,
         save: @escaping (String, String, String, Bool, Bool) throws -> Void) {
        self.save = save
        let window = EditingWindow(contentRect: NSRect(x: 0, y: 0, width: 460, height: 510),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Amplifier Tempo"; window.center()
        super.init(window: window)
        let stack = NSStackView(); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 12
        stack.translatesAutoresizingMaskIntoConstraints = false
        window.contentView!.addSubview(stack)
        NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor, constant: -24),
            stack.topAnchor.constraint(equalTo: window.contentView!.topAnchor, constant: 24)])
        func add(_ view: NSView) { stack.addArrangedSubview(view); view.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true }
        add(NSTextField(wrappingLabelWithString: "Connect to the Unified host where your chats run."))
        add(NSTextField(labelWithString: "Host address"))
        address.stringValue = defaults.string(forKey: "endpoint") ?? "http://127.0.0.1:8941"
        address.setAccessibilityLabel("Unified host address"); add(address)
        add(NSTextField(labelWithString: "Host control token · stored in macOS Keychain"))
        token.placeholderString = "Leave blank to keep the saved token"
        token.setAccessibilityLabel("Host control token"); add(token)
        shape.addItems(withTitles: ["Arc · cyan + amber", "Relay · blue", "Crest · purple + blue"])
        shape.selectItem(at: ["arc", "relay", "crest"].firstIndex(of: defaults.string(forKey: "shape") ?? "arc") ?? 0)
        shape.setAccessibilityLabel("Companion design"); add(shape)
        notifications.state = defaults.bool(forKey: "notifications") ? .on : .off; add(notifications)
        all.state = defaults.bool(forKey: "notifyAll") ? .on : .off; add(all)
        add(NSTextField(wrappingLabelWithString: "Notifications start with new activity after you connect. Replies go to the chat that produced the notification. Questions and approvals open in Amplifier."))
        let button = NSButton(title: "Save and connect", target: self, action: #selector(commit))
        button.keyEquivalent = "\r"; stack.addArrangedSubview(button)
        errorLabel.textColor = .systemRed; add(errorLabel)
        if let recoveryPath { errorLabel.stringValue = "Saved replies need recovery: \(recoveryPath). No replies will be sent." }
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    @objc private func commit() {
        do {
            try save(address.stringValue, token.stringValue.trimmingCharacters(in: .whitespacesAndNewlines),
                ["arc", "relay", "crest"][shape.indexOfSelectedItem], notifications.state == .on, all.state == .on)
            token.stringValue = ""; errorLabel.stringValue = ""; close()
        } catch { errorLabel.stringValue = error.localizedDescription }
    }
}
#endif
