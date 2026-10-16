# Native device approval — v1

The host owns browser approval and revocable device credentials. Native clients
own private proof keys and OS credential storage. This implements the shared
client vision without requiring Tempo, changing existing client authentication,
or embedding a runtime in a native app.

1. The client creates a random 32-byte verifier, posts its base64url SHA-256
   challenge to `/api/devices/pair`, and retains the verifier in memory only.
2. The host returns a random request ID, a comparison code, a relative browser
   approval URL, a 5-second polling interval, and a 10-minute expiry. No host
   credential travels in a URL. The native app opens that URL at the exact host
   origin and displays the comparison code.
3. The signed-in owner compares the code and explicitly allows or denies the
   request. Approval uses a session-bound CSRF proof and trusted HTTPS (loopback
   HTTP is allowed for isolated development). Request names are untrusted labels.
4. The native app polls `/api/devices/pair/token` with its request ID and verifier.
   Only approved requests with the matching proof receive a device credential.
   A lost response can be retried until expiry without creating another device.
   Host restart expires unfinished pairing; the client starts again explicitly.
5. Credentials use the existing device store and revocation mechanism. Only
   hashes persist on the host; the client stores its credential in Keychain.
   `/devices` lists registrations and lets the signed-in owner revoke them.

The `companion-v1` scope permits native attachment, compact activity/catalog
reads, bounded message/work previews, managed-chat creation and sending chat
messages. It does not permit arbitrary actions, settings, files, terminal setup,
provider credentials, approvals, or device administration. It is not a
per-conversation ACL: reading and messaging chats on this host is the explicitly
approved authority. Legacy terminal credentials keep their existing authority.
Revocation rejects subsequent calls; already accepted work continues.

Anonymous pairing is bounded in memory and rate limited. Polling requires a
256-bit proof; browser URLs cannot redeem credentials. Denied, expired, revoked,
and invalid requests do not return a token. Responses are never cached; approval
pages have no external resources and suppress cross-origin referrers. Old hosts return a clear
unsupported result; existing manual-token connections continue to work unchanged.
