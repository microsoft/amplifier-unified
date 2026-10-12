import XCTest
@testable import CompanionCore
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

final class CompanionCoreTests: XCTestCase {
    func snapshot(_ fingerprint: String = "one", host: String = "host", pinned: Bool = true) throws -> CompanionSnapshot {
        let value: [String: Any] = ["protocolVersion": 1, "hostIdentity": host, "hostInstanceId": UUID().uuidString,
            "workingCount": 1, "attentionCount": 1, "sessions": [], "sessionsTruncated": false, "itemsTruncated": false,
            "items": [["id": "completion:chat-A", "fingerprint": fingerprint, "sessionId": "chat-A", "title": "A",
                       "kind": "completion", "summary": "Response ready", "pinned": pinned]]]
        return try JSONDecoder().decode(CompanionSnapshot.self, from: JSONSerialization.data(withJSONObject: value))
    }
    func testEndpointValidationAndEncodedNavigation() throws {
        for address in ["http://example.com", "https://user:pass@host", "https://host/?token=x", "https://host/path", "file:///tmp/a", "https://host/#x"] {
            XCTAssertThrowsError(try Endpoint(address), address)
        }
        let host = try Endpoint("http://127.0.0.1:8941/")
        XCTAssertEqual(host.url.absoluteString, "http://127.0.0.1:8941")
        let url = host.chatURL("chat A&other=value")
        XCTAssertEqual(URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.count, 1)
        XCTAssertEqual(URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first?.value, "chat A&other=value")
    }
    func testBaselineAndRestartNeverReplayOldNotifications() throws {
        var ledger = Journal()
        XCTAssertTrue(ledger.observe(try snapshot(), endpoint: "host", enabled: true, pinnedOnly: true).isEmpty)
        XCTAssertTrue(ledger.observe(try snapshot(), endpoint: "host", enabled: true, pinnedOnly: true).isEmpty)
        XCTAssertEqual(ledger.observe(try snapshot("two"), endpoint: "host", enabled: true, pinnedOnly: true).count, 1)
        XCTAssertTrue(ledger.observe(try snapshot("two", host: "replacement"), endpoint: "host", enabled: true, pinnedOnly: true).isEmpty)
    }
    func testDisabledAndUnpinnedActivityDoesNotFloodWhenEnabled() throws {
        var ledger = Journal()
        _ = ledger.observe(try snapshot(), endpoint: "host", enabled: false, pinnedOnly: true)
        XCTAssertTrue(ledger.observe(try snapshot("two", pinned: false), endpoint: "host", enabled: true, pinnedOnly: true).isEmpty)
        XCTAssertTrue(ledger.observe(try snapshot("two", pinned: false), endpoint: "host", enabled: true, pinnedOnly: false).isEmpty)
    }
    func testReplyTargetAndIdentitySurviveRestart() throws {
        var ledger = Journal()
        let (first, fresh) = ledger.enqueue(notificationID: "notice", endpoint: "https://host", hostIdentity: "host-id", sessionID: "chat-A", text: "Continue")
        XCTAssertTrue(fresh)
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("journal.json")
        ledger.mark(first.id, "unconfirmed"); try ledger.save(to: file)
        var restored = try Journal.load(from: file)
        let (duplicate, isNew) = restored.enqueue(notificationID: "notice", endpoint: "https://host", hostIdentity: "host-id", sessionID: "chat-A", text: "Continue")
        XCTAssertFalse(isNew); XCTAssertEqual(first.id, duplicate.id)
        XCTAssertEqual(duplicate.sessionID, "chat-A"); XCTAssertEqual(duplicate.status, "unconfirmed")
        XCTAssertEqual(restored.clientID, ledger.clientID)
        try Data("broken".utf8).write(to: file)
        XCTAssertThrowsError(try Journal.load(from: file))
    }
    func testStateMapping() {
        XCTAssertEqual(Motion.status(connected: false, working: 3, attention: 1), .waiting)
        XCTAssertEqual(Motion.status(connected: true, working: 3, attention: 1), .review)
        XCTAssertEqual(Motion.status(connected: true, working: 3, attention: 0), .working)
        XCTAssertEqual(Motion.status(connected: true, working: 0, attention: 0), .idle)
    }
    func testReplyUsesCapturedTargetAndExactIDForRetry() async throws {
        var ledger = Journal()
        let (reply, _) = ledger.enqueue(notificationID: "notice", endpoint: "https://host", hostIdentity: "host", sessionID: "chat-A", text: "Continue")
        let data = try JSONSerialization.data(withJSONObject: ["protocolVersion": 1, "hostIdentity": "host", "hostInstanceId": "instance",
            "workingCount": 0, "attentionCount": 0, "sessions": [], "items": [], "sessionsTruncated": false, "itemsTruncated": false])
        var commands: [[String: Any]] = []
        StubURLProtocol.handler = { request in
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer private-token")
            XCTAssertEqual(request.value(forHTTPHeaderField: "X-Amplifier-Client"), ledger.clientID)
            if request.url!.path == "/api/companion" { return (200, data) }
            XCTAssertEqual(request.url!.path, "/api/sessions/chat-A/commands")
            let body: Data
            if let bytes = request.httpBody { body = bytes }
            else {
                let stream = request.httpBodyStream!; stream.open(); defer { stream.close() }
                var result = Data(), buffer = [UInt8](repeating: 0, count: 4096)
                while stream.hasBytesAvailable { let count = stream.read(&buffer, maxLength: buffer.count); if count <= 0 { break }; result.append(buffer, count: count) }
                body = result
            }
            commands.append(try JSONSerialization.jsonObject(with: body) as! [String: Any])
            return (200, Data("{\"accepted\":true}".utf8))
        }
        defer { StubURLProtocol.handler = nil }
        let config = URLSessionConfiguration.ephemeral; config.protocolClasses = [StubURLProtocol.self]
        let client = HostClient(endpoint: try Endpoint("https://host"), token: "private-token", clientID: ledger.clientID, configuration: config)
        try await client.send(reply); try await client.send(reply)
        XCTAssertEqual(commands.count, 2)
        XCTAssertEqual(commands[0]["id"] as? String, reply.id)
        XCTAssertEqual(commands[1]["id"] as? String, reply.id)
        XCTAssertEqual((commands[0]["args"] as? [String: Any])?["preserveDraft"] as? Bool, true)
    }
}

private final class StubURLProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (Int, Data))?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            let (status, data) = try Self.handler!(request)
            let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data); client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}
