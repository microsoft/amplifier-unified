import Foundation

public enum CompanionError: Error, LocalizedError {
    case invalidEndpoint, incompatibleHost, rejected(Int), uncertain, changedHost
    public var errorDescription: String? {
        switch self {
        case .invalidEndpoint: return "Use an HTTPS host address, or HTTP on 127.0.0.1 or localhost. Omit paths, passwords, and query parameters."
        case .incompatibleHost: return "This host does not support Amplifier Tempo yet. Update Unified on that host."
        case .rejected(let code): return "The host refused this request (HTTP \(code)). Check your connection and token."
        case .uncertain: return "Delivery is unconfirmed. Your reply is saved. Check the chat before choosing Retry."
        case .changedHost: return "This reply belongs to a different host. Reconnect to its original host."
        }
    }
}

public struct Endpoint: Equatable {
    public let url: URL
    public init(_ value: String) throws {
        guard var parts = URLComponents(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              let host = parts.host, !host.isEmpty, parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil, ["", "/"].contains(parts.path),
              parts.scheme == "https" || (parts.scheme == "http" && ["localhost", "127.0.0.1", "[::1]", "::1"].contains(host)),
              parts.port == nil || (1...65535).contains(parts.port!) else { throw CompanionError.invalidEndpoint }
        parts.path = ""; parts.host = host.lowercased()
        guard let url = parts.url else { throw CompanionError.invalidEndpoint }
        self.url = url
    }
    public func route(_ parts: String...) -> URL {
        parts.reduce(url) { $0.appendingPathComponent($1) }
    }
    public func chatURL(_ id: String) -> URL {
        var parts = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        parts.queryItems = [URLQueryItem(name: "chat", value: id)]
        return parts.url!
    }
}

public struct ChatSummary: Decodable, Identifiable {
    public let id: String
    public let title: String
    public let status: String
    public let pinned: Bool
}
public struct AttentionItem: Codable, Identifiable {
    public let id: String
    public let fingerprint: String
    public let sessionId: String
    public let title: String
    public let kind: String
    public let summary: String
    public let pinned: Bool
    public var key: String { id + ":" + fingerprint }
    public var allowsReply: Bool { kind == "completion" }
}
public struct CompanionSnapshot: Decodable {
    public let protocolVersion: Int
    public let hostIdentity: String
    public let hostInstanceId: String
    public let workingCount: Int
    public let attentionCount: Int
    public let sessions: [ChatSummary]
    public let items: [AttentionItem]
    public let sessionsTruncated: Bool
    public let itemsTruncated: Bool
}

public enum Motion: Int, CaseIterable {
    case idle, movingRight, movingLeft, greeting, jump, reaction, waiting, working, review
    public var count: Int { [6, 8, 8, 4, 5, 8, 6, 6, 6][rawValue] }
    public static func status(connected: Bool, working: Int, attention: Int) -> Motion {
        if !connected { return .waiting }
        if attention > 0 { return .review }
        return working > 0 ? .working : .idle
    }
}
