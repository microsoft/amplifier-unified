import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

private final class NoRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

public final class HostClient {
    public let endpoint: Endpoint
    private let token: String
    private let clientID: String
    private let session: URLSession
    public init(endpoint: Endpoint, token: String, clientID: String,
                configuration: URLSessionConfiguration = .ephemeral) {
        self.endpoint = endpoint; self.token = token; self.clientID = clientID
        let config = configuration
        config.timeoutIntervalForRequest = 20
        config.timeoutIntervalForResource = 30
        config.httpCookieStorage = nil
        self.session = URLSession(configuration: config, delegate: NoRedirects(), delegateQueue: nil)
    }
    deinit { session.invalidateAndCancel() }
    private func request(_ url: URL, body: [String: Any]? = nil) async throws -> Data {
        var request = URLRequest(url: url)
        request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        request.setValue(clientID, forHTTPHeaderField: "X-Amplifier-Client")
        request.setValue("delta-v1", forHTTPHeaderField: "X-Amplifier-State-Transport")
        if let body {
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw CompanionError.uncertain }
        guard (200..<300).contains(http.statusCode) else { throw CompanionError.rejected(http.statusCode) }
        return data
    }
    public func attach() async throws {
        let data = try await request(endpoint.route("api", "clients", "attach"),
            body: ["clientId": clientID, "kind": "native", "protocolVersion": 1])
        let value = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard value?["protocolVersion"] as? Int == 1 else { throw CompanionError.incompatibleHost }
    }
    public func snapshot() async throws -> CompanionSnapshot {
        do {
            let data = try await request(endpoint.route("api", "companion"))
            let value = try JSONDecoder().decode(CompanionSnapshot.self, from: data)
            guard value.protocolVersion == 1 else { throw CompanionError.incompatibleHost }
            return value
        } catch CompanionError.rejected(404) { throw CompanionError.incompatibleHost }
    }
    public func send(_ reply: PendingReply) async throws {
        guard reply.endpoint == endpoint.url.absoluteString else { throw CompanionError.changedHost }
        // Verify durable host identity even if the address was reassigned.
        guard try await snapshot().hostIdentity == reply.hostIdentity else { throw CompanionError.changedHost }
        let data = try await request(endpoint.route("api", "sessions", reply.sessionID, "commands"), body: [
            "id": reply.id, "action": "conversation.send", "args": [
                "text": reply.text, "via": "chat", "preserveDraft": true, "attachmentIds": []] as [String: Any]])
        let value = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard value?["accepted"] as? Bool == true else { throw CompanionError.uncertain }
    }
}
