# Authenticated connector account identity

Unified supports the opt-in MCP extension `io.amplifier/account-identity`, version
1. This is an adapter contract, not a claim that ordinary MCP servers implement
it or that any real account provider has passed interoperability acceptance.

The server advertises this standard SDK extension setting:

```json
{"version": 1, "resourceUri": "amplifier-account://current"}
```

The resource is read using the already authenticated MCP connection. It must
return exactly one `application/json` text resource with the exact advertised URI:

```json
{"schemaVersion": 1, "issuer": "https://issuer.example", "subject": "stable-principal-id", "displayName": "Optional account name"}
```

The issuer must exactly equal the authorization issuer validated by the MCP OAuth
SDK. The server owns deriving the subject from its authenticated principal; it
must never echo an untrusted client label or decode an unverified token for this
purpose. Assertions are at most 8192 UTF-8 bytes; issuer/subject/displayName are
at most 2000/500/200 bytes and contain no control characters. Other fields are
ignored. Names are rendered as escaped text. Identity is the exact issuer+subject,
never the mutable display name.

This host currently accepts the capability only on Streamable HTTP with the
existing OAuth SDK context and grant. It requests no additional scopes. The
assertion is **server-attested through authenticated transport**, not independent
verification of a legal person, organization membership, or account ownership.
Public provenance records endpoint, resource URI, SDK issuer source, observation
time and revision. The account status `verified` has only that stated meaning.

The initial supported assertion becomes the durable binding. Every reconnect and
each serially queued tool/resource call rechecks it. A differing principal blocks
the call, preserves the expected identity, exposes the candidate, and sets
`account.status=changed`. `smartTools.accountAccept` requires the exact current
revision, issuer and subject; UI and agent use this shared action. Its durable
receipt records previous/candidate identity, actor source and time. Accepting does
not connect or send a tool call. Explicit reconnect must attest the accepted
identity before tools become ready. Reusing a command ID cannot accept twice.
The public shared action dispatcher supplies origin; an account argument cannot
forge it.

If the SDK refreshes credentials after attestation or in response to a tool-call
challenge, the outgoing transport guard blocks dispatch under an authorization
value that was not used for that attestation. Values are retained only in the
private live transport object and never enter public state/receipts. The caller
must reconnect and verify; there is no automatic replay. This does not make two
server requests atomic: the connector remains responsible for authenticating each
request and maintaining stable subject semantics. A remote outcome lost after
submission is still unknown, never proof of rollback.

Missing or malformed evidence is `unknown` before a binding exists; an invalid
advertised assertion fails connection. A previously bound identity becomes
`unconfirmed` on restart, missing capability, failed assertion or authorization
change, and cannot silently degrade to an unbound connection. Forgetting local
credentials keeps the accepted identity binding. Removing the explicit connection
registration removes that association. Passive state inspection never contacts
the connector or starts sign-in.

OIDC UserInfo is not inferred from an issuer URL. The installed MCP SDK does not
retain an advertised UserInfo endpoint or expose validated ID-token subject
binding, so this delivery cannot implement verified OIDC UserInfo from that SDK
state. No opaque access token claims, guessed endpoints, new scopes, or fabricated
account labels are used. A future provider-owned OIDC adapter must expose its
validated issuer/subject and already advertised endpoint/grant explicitly, following
[OIDC UserInfo subject validation](https://openid.net/specs/openid-connect-core-1_0.html#UserInfo).

Acceptance uses actual local official SDK OAuth/MCP servers with two synthetic
principals and the production app/UI. It covers grant reuse and refresh, restart,
mismatch denial before tool execution, exact user/agent accept receipts, reverify,
invalid issuer/fields, credential-change guard, escaped rendering, and preservation
of the selected task and unsent draft. No real account interoperability or remote
provider consent is claimed.
