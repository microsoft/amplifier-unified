import Foundation

public struct PendingReply: Codable, Identifiable {
    public let id: String
    public let notificationID: String
    public let endpoint: String
    public let hostIdentity: String
    public let sessionID: String
    public let text: String
    public var status: String
    public let createdAt: Date
}

public struct Journal: Codable {
    public var clientID = "mac-" + UUID().uuidString
    public var seen: [String: [String]] = [:]
    public var replies: [PendingReply] = []
    public init() {}

    /// First contact establishes a baseline, not a burst of historical alerts.
    /// Host process restarts do not reset identity. Device preferences do not
    /// acknowledge the host's shared unread state.
    public mutating func observe(_ snapshot: CompanionSnapshot, endpoint: String,
                                 enabled: Bool, pinnedOnly: Bool) -> [AttentionItem] {
        let scope = endpoint + "|" + snapshot.hostIdentity
        let previous = seen[scope].map(Set.init)
        let fresh = snapshot.items.filter { !(previous?.contains($0.key) ?? true) }
        let current = snapshot.items.map(\.key)
        seen[scope] = Array((current + (seen[scope] ?? []).filter { !current.contains($0) }).prefix(2048))
        return enabled ? fresh.filter { !pinnedOnly || $0.pinned } : []
    }

    public mutating func enqueue(notificationID: String, endpoint: String, hostIdentity: String,
                                 sessionID: String, text: String) -> (PendingReply, Bool) {
        if let existing = replies.first(where: { $0.notificationID == notificationID && $0.text == text && $0.endpoint == endpoint }) {
            return (existing, false)
        }
        let reply = PendingReply(id: UUID().uuidString, notificationID: notificationID,
            endpoint: endpoint, hostIdentity: hostIdentity, sessionID: sessionID,
            text: text, status: "saved", createdAt: Date())
        replies.append(reply)
        return (reply, true)
    }
    public mutating func mark(_ id: String, _ status: String) {
        if let i = replies.firstIndex(where: { $0.id == id }) { replies[i].status = status }
    }
    public func save(to file: URL) throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(),
            withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        try JSONEncoder().encode(self).write(to: file, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
    }
    public static func load(from file: URL) throws -> Journal {
        guard FileManager.default.fileExists(atPath: file.path) else { return Journal() }
        return try JSONDecoder().decode(Journal.self, from: Data(contentsOf: file))
    }
}
