# Terminal setup authority, v1

This optional root-owned capability enrolls the current AHP Terminal client for
one configured account. It does not install on the server or start a conversation.
The account comes from composition, never action arguments. A configured origin
must be exact HTTPS and equal the public gateway origin.

`config.terminal = {origin, artifacts}` adds the `terminal` host topic and real
quiescence participant. Its four action schemas are available through the normal
AHP action catalog: `terminal.prepare`, `terminal.devices`, `terminal.revoke`,
and `terminal.receipt`. The last two read projections are bounded and remain
available under a retained fence. Device registration is not local installation,
connectivity, or successful conversation acceptance.

Each configured artifact entry contains:

```text
{id, platform,
 artifact:{manifestPath, manifestSha256, filename, wheelPath, evidencePath},
 runtimes:{python, node}}
```

The TUI owner's [manifest v1](https://github.com/microsoft/amplifier-unified-client-tui/blob/main/release/manifest-v1.schema.json)
is validated together with its semantic filename/version/platform/runtime rules.
The configured raw manifest SHA-256 is the reviewed release authority. The exact
selected evidence bytes must match the manifest and bind source, wheel, runtime
acceptance, privacy, actual PTY, and every required check/log digest. Wheel bytes
are rechecked before rendering. No network catalog, fallback release, GitHub
credential, or caller-provided URL is accepted. An artifact ID is immutably bound
to its manifest/runtime identity in the owner database. Missing or changed feed
inputs disable new setup while old receipts and revocation remain accessible.

Runtime descriptors bind `version`, HTTPS `url`, `bytes`, `sha256`,
`archive:'tar.gz'`, `archiveRoot`, `executable`, and `{os,arch}` target; Node also
binds `executableSha256`. Managed Python 3.11+ and Node 22+ are required. Platform
support comes only from qualified entries, not from the build host. Feed limits:
32 configured entries, 16 artifacts per manifest, 128 KiB manifest/evidence,
64 MiB selected wheel, 96 MiB private script, two concurrent renderings,
20 live preparations, 100 active credentials, 50 devices per page. Download
buffers share a 96 MiB budget. Receipts/device history are indexed durable records.

The pure installer renderer is a trusted composition dependency. Its private
input contains the one-use grant; normal action results do not. The renderer
stages and verifies client/runtime inputs before redemption. A normal deployment
must bind the qualified renderer and runtime assets; synthetic test artifacts
cannot establish platform readiness.

## Original-command and credential boundaries

Preparation records an original command identity before rendering. A completed
receipt has only a private download descriptor and immutable artifact metadata.
The grant expires exactly 30 minutes after creation; retrying the same command
does not extend it, choose another release, or generate another grant. Changed
arguments refuse. Failed rendering or process death leaves an original unknown
receipt, never an automatically repeated effect.

Authenticated `GET /setup/terminal/download/<preparationId>` returns only the
original verified owner-private script. Downloads expire and are unavailable
after consumption. The grant itself authorizes only
`POST /setup/terminal/redeem`, a <=4 KiB exact JSON body defined in
[terminal-setup-v1.ts](contracts/terminal-setup-v1.ts). Grant consumption and
device credential insertion are one SQLite transaction. The 201 response emits
the credential once. A lost response is not recoverable by replay: an exact
repeat returns 409 `consumed`, metadata only. A different redemption ID never
creates another device. The owner stores credential/grant hashes, never token
bytes. Private installer files necessarily contain their grant; they are mode
0600 beneath a mode 0700 owner directory and are part of sensitive backup scope.

The private TLS proxy accepts a device Bearer credential only on `/ahp`; it may
omit Origin. Any supplied wrong Origin refuses. Browser cookie websocket access
still requires the exact Origin. A device token does not authorize arbitrary
HTTP/download paths. Preview access codes and internal gateway tokens are never
accepted as Terminal credentials or exposed to clients.

Authentication and socket registration are synchronous with revocation. Revoking
one device durably disables future authorization and closes only that device's
front/backend socket pairs, including pending handshakes. Already admitted host
work continues. Both socket sides must close before their device and ingress
leases are released; request closure alone is insufficient after upgrade.

## Quiescence and archive integration

The owner takes a lifetime SQLite OS lease before opening/migrating authority.
Prepare/revoke/redemption/download and device socket lifetimes enter its actual
durable `FacadeFence`. Idle acquisition closes intake atomically; an active
socket or request refuses acquisition. Receipt/device reads remain passive.
Unknown/restarted fences retain intake closure; a live partial-admission rollback
cannot be reconstructed after restart. Release uses the existing exact original
context and authenticated proof, including the service-specific proof where
required. Identical settled proof repeats are accepted; changed proofs refuse.

All owner state lives in `stateDirectory/capabilities/terminal`: binding,
command/device/grant records, private downloads, and intake journals. Composition
includes `terminal` in the actual required-owner census and binds source bytes
under the root package provenance. Full application-root backup therefore includes
this sensitive authority. An inactive restored fence remains closed; restoration
does not activate grants, reconnect devices, or replay preparations. No session or
filesystem allocation references are stored, so held retention/managed-file
inspection uses the real empty-reference facade lease.

The existing signed 21-owner preview profile is unchanged. Enabling Terminal in a
signed launcher needs a separately admitted optional owner-census profile and
qualified TUI/runtime artifacts. This work does not grant that deployment authority.

## Acceptance boundaries

`test/terminal-owner.test.mjs` covers one-use concurrency, exact receipts, expiry,
redaction, process death, owner exclusion, retained fence/release, and inactive
authority restoration. `test/terminal-access.integration.test.mjs` exercises the
installed public AHP host through actual TLS, gateway, and ManualIngressGate:
two devices plus browser, origin enforcement, no ordinary HTTP bearer authority,
selective revocation, and an accepted fixture turn finishing exactly once.
`test/preview-access-race.acceptance.mjs` retains the previous late-upgrade proof.
These fixtures do not establish real account, physical Terminal, qualified
runtime download, signed platform stop, or live preview acceptance.
