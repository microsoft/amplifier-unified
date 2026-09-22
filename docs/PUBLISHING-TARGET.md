# Separate private publishing service

The `amplifier_publishing.service` module hosts immutable static releases in a
separate, long-lived process. It reuses `Publisher` for review, deployment,
rollback, stop/remove, content verification and durable operation receipts.
The `amplifier_publishing.remote.SSHClient` library sends one request at a time
to that service over an existing SSH connection configuration.

The app integrates this target through the shared controls documented in
[Managed static publishing](PUBLISHING.md). Local Unix-socket, HTTP, app adapter
and controlled-transport acceptance is covered by the publishing tests. Host
installation, client reachability, private network, firewall, reverse proxy,
TLS and production acceptance remain separate deployment responsibilities.
A configured target is explicitly inspected and selected by its consuming
application. Registration or inspection does not install a service or deploy
a site. Keep local and remote records tied to their owning service identity.

## Explicit host configuration

An operator supplies all of the following before deployment:

- An existing host/account and trusted SSH host key, with the package already
  available in an explicitly named Python interpreter. The transport does not
  install packages, accept a new host key, create an account or provision hosts.
- A service owner, private store directory, owner-only admin socket directory,
  and a supervisor/process lifetime. A supervisor must preserve these stores.
- A numeric listener address: exactly `127.0.0.1` by default, or an explicitly
  chosen RFC1918 IPv4 interface in `10/8`, `172.16/12` or `192.168/16`.
- The intended network users and firewall/private-interface topology. An RFC1918
  address alone does not establish authorization, network isolation or TLS.

Admin operations are available only through an owner-only Unix socket (directory
0700, socket 0600); there is no TCP admin endpoint. The SSH account must have
access to that socket. This is an owner control interface, not a multi-user
authorization service. Task identifiers separate records for trusted callers;
an account with socket access controls the service.

The site listener uses HTTP, reports `authentication: "none"`, and reports its
actual policy as `loopback-only` or `private-network`. It never defaults to a
wildcard/public bind, and it does not claim password protection. Preview URLs
always remain on the service host's `127.0.0.1`. Each deployed site gets an
OS-selected port; updates/rollback retain that site's URL while its listener
is alive. No stable port, DNS name or external URL is invented.

An existing reverse proxy may be configured by the host owner as a separate
serving route, preserving the exact numeric `Host` authority expected by the
listener. This implementation neither mutates proxy configuration nor verifies
or advertises proxy URLs. Selecting such a route requires separate access/TLS
and external-URL acceptance; it is not completed by a successful direct probe.

For a concrete foreground service, replace the example paths with explicitly
allocated host paths and run the installed interpreter:

```sh
/absolute/env/bin/python -m amplifier_publishing.service serve \
  --root /absolute/private/publishing-store \
  --socket /absolute/private/publishing-admin/control.sock \
  --bind 127.0.0.1
```

The parent directories of configured paths are host configuration and are
canonicalized (including macOS `/var` aliases). Private leaf directories reject
symlinks and non-owner permissions. Imported artifact paths are independently
validated; they never name host filesystem inputs. Service import state lives
in the sibling `.publishing-store-service` directory outside the Publisher
store. Its `imports.sqlite3` also holds a service UUID, generated once on first
initialization and preserved across restarts. Keep both stores together when
planning private retention/backups. The returned `serviceId` identifies this
managed service state; it is not a password or an authorization credential.
A restored identity represents the same service and must have one authoritative
active owner, rather than being copied into independent live service instances.

SIGINT and SIGTERM close listeners and remove the service's own admin socket.
A crash/SIGKILL can leave a stale socket. Startup refuses any existing socket
path rather than unlinking another owner's endpoint. The operator must verify
that no owner is running before explicitly removing a stale socket. Restart
retains release/review/receipt history, marks former live URLs interrupted or
stopped, and never silently redeploys a site.

## RPC contract

The Unix socket accepts one newline-terminated JSON object and returns one JSON
envelope: `{"ok":true,"result":...}` or
`{"ok":false,"error":{"code":"...","message":"...","receipt":...}}`.
The optional receipt supplies a durable identity/outcome when available.
Unknown or extra fields and duplicate JSON keys are rejected. No arbitrary
Python method, build command, local source path or backup destination is an RPC.

`target` without other fields is the only unbound discovery request. It returns
the durable `serviceId`. Every other request requires `expectedServiceId` equal
to that inspected UUID, including reads and receipt lookups. A bound `target`
request may also include this guard. The service checks identity at request
admission, before reading session data, reserving a request ID, or performing
effects. A missing guard returns `service_identity_required`; a mismatched guard
returns `target_mismatch`. A separate preflight alone is insufficient because
the service at a socket may change between preflight and the actual request.

| Method | Required fields besides `method` and the identity guard |
| --- | --- |
| `target` | none; `expectedServiceId` is optional for discovery |
| `list`, `releases`, `receipts` | `sessionId` |
| `status` | `sessionId`, `siteId` |
| `receipt` | `sessionId`, `requestId` |
| `import` | `sessionId`, `requestId`, `siteId`, `manifest`, `manifestDigest`, `files` |
| `preview` | `sessionId`, `requestId`, `releaseId` |
| `review` | `sessionId`, `requestId`, `releaseId`, `note` |
| `deploy`, `rollback` | `sessionId`, `requestId`, `siteId`, `releaseId`, `expectedRevision` |
| `stop`, `remove` | `sessionId`, `requestId`, `siteId`, `expectedRevision` |

`receipt` returns `null` when that session has no recorded matching request; it
does not establish that a lost request never reached another target. `receipts`
provides operation outcomes/error codes for audit and diagnosis. HTTP access
logs, credentials, chat transcripts and query strings are not retained.

`Publisher.export_release(release_id, session_id)` supplies the exact
`siteId`, `sessionId`, `manifest`, `manifestDigest`, and base64 `files` fields
from an owned immutable release. The caller adds `method: "import"`, a new
`requestId`, and the expected service identity. No mutable build directory,
source path, review notes, previews, or runtime-store metadata are transferred.
Check the receiving target limits before sending the exported payload.

An import contains a path-sorted manifest of `{path,size,sha256}` entries and a
`files` object mapping those same paths to canonical base64 bytes. The manifest
digest is SHA256 of UTF-8 JSON with sorted object keys, no insignificant spaces,
and unescaped Unicode. Paths must be safe relative paths, unique and nonoverlapping.
`index.html` is required. Hidden paths, traversal, symlinks and special files
are excluded; the sole sentinel exception is an empty top-level `.nojekyll`
file, which is included in the manifest and never served over HTTP.

Limits are 16 MiB per JSON message, 2,000 files, 8 MiB per decoded file, and
10 MiB total decoded bytes. The encoded message limit also applies. Import
stages verified bytes in a private deterministic directory outside Publisher's
store, then asks Publisher to snapshot them. No compilation or uploaded program
executes. Successful and failed imports retain their canonical request digest
and durable receipt; transient staged bytes are removed after a known attempt.

Every newly admitted mutation has a durable RPC receipt in the service-private
`rpc_receipts` table before effects begin. Its `rpcPayloadDigest` is SHA256 of
the exact canonical request JSON, **including `expectedServiceId`**, using the
same UTF-8 canonicalization as the manifest. The receipt also carries `serviceId`.
This proof covers every argument, including review notes, expected revisions,
and imported bytes. A matching request ID, action, site, or release alone does
not prove that a receipt belongs to the requested operation.

The caller persists that same digest before transport and compares both proof
fields before adopting a remotely observed outcome. A lost conflict response
must not turn an older operation with the same ID and different arguments into
a successful new request. Exact retries return the stored outcome; changed
payloads fail with `request_conflict`. A service crash before the exact RPC
outcome was committed leaves the RPC receipt `unknown`. Recovery never borrows
a bare inner Publisher receipt to assert success or automatically replays it.

Historical receipts without RPC proof remain unproven on read. An explicit
legacy retry can gain proof only after the original Publisher/import retry
checks verify its exact arguments and return the historical outcome. A
mismatched retry does not relabel or overwrite that older receipt. Callers with
an unknown operation and missing or mismatched proof retain uncertainty; merely
reading matching request IDs is not sufficient reconciliation.

The older import layer retains its payload digest excluding the admission-only
`expectedServiceId` for exact-retry compatibility with prior versions; the new
RPC proof above includes identity. Pending inner imports may recover a known
build success only when action, session, site, and a durably recorded expected
manifest all match. Older pending imports without that evidence remain
`unknown`. Use a new request ID only for a new deliberate operation after
reconciliation.

Send a read-only request from the host's stdin with:

```sh
printf '%s\n' '{"method":"target"}' | \
  /absolute/env/bin/python -m amplifier_publishing.service request \
  --socket /absolute/private/publishing-admin/control.sock
```

## SSH transport and URL verification

The caller supplies the target explicitly:

```python
from amplifier_publishing.remote import SSHClient

target = SSHClient(
    hostname="existing-private-host",
    username="existing-operator",
    python="/absolute/env/bin/python",
    socket_path="/absolute/private/publishing-admin/control.sock",
    expected_bind="10.0.0.25",  # must be the deliberately configured interface
    expected_service_id=inspected_service_id,  # UUID returned by prior discovery
    timeout=30,
)
capabilities = target.verify_target()
sites = target.request({"method": "list", "sessionId": "task-identifier"})
```

The bind in this example is illustrative, not a discovered host configuration.
SSH runs without a local shell; every remote argument is separately shell-quoted.
Requests travel on stdin, never in command arguments. Host-key checking is strict
and SSH is noninteractive. A mutation first checks the target's protocol, bind,
access policy, and persistent service identity. The client also sends the pinned
identity on the actual operation, so a replacement service cannot receive it
after a successful preflight. All reads carry the same guard. If
`expected_service_id` is omitted, the first successful `verify_target()` pins
the discovered UUID for that client instance; subsequent discovery cannot
silently retarget it. Applications should require the inspected identity when
persisting and selecting target profiles.

The client bounds stdout at 16 MiB and discards bounded stderr
(64 KiB); it kills the local SSH process on output overflow or timeout. This
cannot establish whether a remote operation completed.

A lost, malformed or oversized response returns `unknown_outcome`, retaining
the request ID, pinned service ID, and deterministic receipt ID where provided. The transport makes
no automatic retry and labels its synthetic reconciliation reference
`remoteReceiptVerified: false`. Read the server's `receipt`, `status` and
`releases` before any further mutation. The original request ID remains the
reference even if a later read shows that the operation succeeded.

Successful request responses validate the syntax and configured authority of
site URLs. They do **not** establish reachability. After explicit deployment,
`target.verify_site(session_id="...", site_id="...")` separately performs a
bounded direct HTTP GET, follows no redirects/proxies, verifies `index.html`
size and SHA256 against the current immutable release, and checks that the site
revision did not change during the probe. Only that result sets
`clientReachabilityVerified: true`. It proves byte-level reachability from the
calling machine, not browser behavior, authentication, TLS or other clients'
network access.

A remote `127.0.0.1` URL names the remote host's loopback and cannot be verified
as client-reachable. This check rejects it unless the configured SSH endpoint
itself is exactly `127.0.0.1`; it never invents SSH forwarding or rewrites the URL.
Actual remote hosting acceptance still requires the approved host/interface,
an explicit deployment, verified reachability from the intended client, update,
rollback, stop/remove and retained receipts on that host.
