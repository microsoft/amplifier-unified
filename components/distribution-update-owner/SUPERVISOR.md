# External supervisor and signed release adapter

Version 0.2 adds an external process boundary, a public client, an installed CLI,
and a signed release adapter. The existing owner, durable receipt semantics,
manual scheduling and native-owner separation remain unchanged.
Version 0.13 adds [bounded startup diagnostics](STARTUP-DIAGNOSTICS.md) to
failed initial launches and update/service receipts while preserving ownership
checks and uncertain-effect semantics.

## Public client and transport

```ts
import {SupervisorClient, connectSupervisorFile} from
  '@amplifier/unified-distribution-update-owner';

const supervisor = await connectSupervisorFile(privateDiscoveryFile);
// Or: new SupervisorClient({url: 'http://127.0.0.1:PORT/', token});

supervisor.outlivesDistribution; // true
await supervisor.owner.inspect();
await supervisor.owner.inspectRunning();
await supervisor.owner.check(commandId, true);
await supervisor.owner.install(nextCommandId, null); // recommendation from check
await supervisor.owner.rollback(rollbackCommandId, expectedCurrentId);
await supervisor.owner.setPreferences(preferencesCommandId, preferences);
await supervisor.owner.receipt(commandId);
await supervisor.owner.reconcile(commandId); // observe, never restart again
await supervisor.owner.diagnostics();
const unsubscribe = supervisor.subscribe(notification => invalidateUpdates());
```

The object matches the distribution's application-updates capability facade.
The facade retains its user/agent authorization. The private supervisor token
is authority over this one distribution owner and must stay in the host process;
do not send it to a browser or place it in action arguments.

`serveSupervisor({owner, token, port?})` binds only to `127.0.0.1`. All requests
require both `Authorization: Bearer <64 hex characters>` and
`X-Amplifier-Supervisor: 1`. Browser-origin requests and redirects are rejected.
There is no CORS enablement or arbitrary host binding.

- `POST /v1/rpc`: JSON `{operation, commandId?, args?}`. Operations are `inspect`,
  `running`, `diagnostics`, `check`, `install`, `rollback`, `preferences`,
  `receipt`, and `reconcile`. Argument names mirror the client methods; receipt
  and reconcile use `args.commandId`. Mutations need the outer command ID.
- `GET /v1/events`: authenticated SSE. `Last-Event-ID` resumes a bounded cursor.
  Missing/expired cursors or a restarted supervisor produce `reset: true`, so
  consumers refetch the authoritative state. Notifications contain a cursor
  and optional sanitized receipt. The retained event stream is not the ledger.

Limits are 16 KiB requests, 512 KiB responses, 32 concurrent RPCs, 32 subscribers,
128 retained notifications, and 16 KiB individual notifications. Slow readers
are disconnected. A client reconnects observations with a bounded backoff;
it **never retries a mutation automatically**. A lost mutation response reports
`supervisor_unreachable_outcome_unknown`; query the original command receipt.
Manual commands enter the owner immediately and progress notifications are pushed.
Manual checks bypass availability TTL and replace a scheduled background wake.
An install submitted during a check begins as soon as that check completes;
independent source lookups and artifact downloads use their bounded concurrent
adapters. Commands sharing installation state serialize their activation, not
independent network work. Keep this direct dispatch: do not reintroduce a timer
or client poll between the user's action and the owner's command acceptance.
There is no update-install polling loop. The client RPC network deadline is ten
seconds and does not cancel a durably accepted command or constrain model calls.

### Lazy private discovery (version 0.5)

The child must be composable before initial provisioning publishes the supervisor
endpoint. Use the synchronous constructor in child composition:

```ts
import {connectSupervisorFileLazy} from
  '@amplifier/unified-distribution-update-owner';
const supervisor = connectSupervisorFileLazy(absolutePrivateDiscoveryFile);
// Register supervisor.owner and subscribe immediately; no file or endpoint read
// occurs during construction. Do not await a supervisor RPC to become ready.
const unsubscribe = supervisor.subscribe(event => invalidateUpdates());
```

This returns the same `SupervisorClient` facade with `outlivesDistribution:true`.
Every owner RPC resolves the current private discovery and token file once.
Missing/unavailable reads throw `supervisor_unreachable`, never a ready/idle
result. A failed mutation reports uncertainty and is neither queued nor retried;
retain its command ID and inspect its receipt when the endpoint is available.

Subscriptions watch discovery appearance and atomic replacement. A missing
parent directory is handled by watching its nearest existing ancestor, descending
as provisioning creates it, and re-reading after watch registration to close the
creation/replacement race. Rename bursts coalesce into a 25 ms observation wake;
there is no recurring filesystem or update-state poll. Stream reconnect uses
bounded backoff if notifications or the endpoint are unavailable. Discovery
changes interrupt the old observation stream even while it is healthy, so a new
endpoint/token takes effect. Only observations reconnect; RPCs are never replayed.
Unsubscribing the last listener or `close()` cancels watching and reconnection.

Custom private discovery can use `new SupervisorClient({connect, onChange?})`.
`connect()` returns `{url,token}` and is called for each RPC/reconnection;
`onChange(notify)` returns an unsubscribe callback. The eager
`connectSupervisorFile` and fixed `{url,token}` constructors remain available.
Private file discovery validates its exact schema, absolute token path, private
regular non-symlink files and loopback authenticated endpoint before requests.

The supervisor's initial authority order is unchanged: qualify the installation,
start its owned child, confirm actual readiness, then publish transport/discovery
and start automatic scheduling. The lazy client breaks the composition cycle
without exposing mutating supervisor commands before provisioning is complete.

## Installed CLI

The npm package includes the `amplifier-distribution-supervisor` executable.

```text
amplifier-distribution-supervisor serve --config /absolute/private/config.json
amplifier-distribution-supervisor call --connection /absolute/private/connection.json --operation inspect
amplifier-distribution-supervisor call --connection /absolute/private/connection.json --operation check --command-id opaque-check-id
amplifier-distribution-supervisor call --connection /absolute/private/connection.json --operation install --command-id opaque-install-id
amplifier-distribution-supervisor watch --connection /absolute/private/connection.json
```

`--args` accepts bounded JSON for operations requiring arguments. Credentials
are read from private files, not printed or passed in command arguments. The
configuration and token/discovery files must be private regular files (0600 on
POSIX). A missing token file is created with cryptographic random bytes.
The daemon writes a private discovery file containing its URL and token-file
location. The file is removed during orderly supervisor shutdown.

Configuration shape (all filesystem locations are absolute):

```json
{
  "schema": "distribution-supervisor-v1",
  "dataDirectory": "/absolute/private/supervisor",
  "dataScope": "opaque-instance-scope",
  "tokenFile": "/absolute/private/supervisor-token",
  "discoveryFile": "/absolute/private/supervisor-connection.json",
  "adapterModule": "/absolute/installed/public-host-ports.mjs",
  "adapterConfig": {},
  "release": {
    "directory": "/absolute/private/distribution-releases",
    "channelUrl": "https://releases.example.org/distribution/channel.json",
    "trustedKeys": {"release-key": "PEM-ENCODED-ED25519-PUBLIC-KEY"},
    "accessScope": "opaque-release-access-scope",
    "allowedArtifactOrigins": ["https://releases.example.org"],
    "launchArgs": [],
    "launchEnv": {}
  }
}
```

The operator-selected local module exports `createSupervisorPorts(adapterConfig)`.
It returns public `inspect`, `admitRestart`, `reconcileAdmission`, `resolveSources`,
and optionally credential-aware `fetch` and `onIdle(callback): unsubscribe`.
No module path or arbitrary executable is accepted over RPC. There is no fallback
that samples idle or assumes configured native owners are inactive.

The supervisor must run outside its child application. Optional `initial` is an
already qualified `{identity, handle}` from this adapter's `prepare` operation.
`serve --config ... --start-initial` is explicit one-shot initial provisioning:
it refuses an existing owner ledger, verifies the candidate and starts only a
new owned child. It is not a service restart/adoption policy. Without that flag,
starting the supervisor is passive with respect to application launch.

On SIGTERM/SIGINT the CLI stops accepting transport requests, records pending
owner uncertainty and exits. It does **not** kill an application or claim idle
admission merely because the supervisor is stopping. A child may remain alive.
A replacement supervisor can inspect/reconcile that process but the portable
adapter cannot adopt its OS process handle or kill it. Production service-manager
ownership, orderly stop, supervisor crash recovery and process adoption need a
separately qualified platform adapter. Do not configure a service manager to kill
an entire cgroup on supervisor exit and call that safe workload quiescence.

## Atomic host admission

The owner always calls:

```ts
lifecycle.admitRestart({
  commandId, purpose: 'distribution-update', dataScope, signal
});
```

The optional argument in the TypeScript signature permits old no-argument
adapters to compile. Production host adapters must require the context and bind
it to the host's durable `admitQuiescence({commandId, purpose})` fence. Forward
configured-owner coverage and held leases through the public host port; do not
replace them with worker counts sampled before restart.

The returned lease retains its existing evidence and `release(outcome)` contract.
`reconcileAdmission({commandId, purpose, dataScope, outcome, observed})`
is called during explicit passive reconciliation **only after** an exact
`succeeded`/`ready` owner receipt (`outcome:'ready'`) or a
`failed`/`pre_restart_refused` receipt (`outcome:'unchanged'`) is durable and
the observed installation matches that receipt. It can verify the owner receipt ID,
actual process identity, data scope and persisted host fence before releasing
that fence. This works after a supervisor restart without an old in-memory
closure. It must be idempotent. Reconciliation of an already succeeded update
may repeat this fence reconciliation; it never launches a process again.
The host owns proof of all required participants and decides whether the fence
can actually be released. Version 0.3 adds authenticated public-host control and
explicit unchanged-release reconciliation after a durable pre-restart refusal;
see [HOST-CONTROL.md](HOST-CONTROL.md). Production participant coverage remains
an integration requirement.

## Signed, self-contained release contract

`SignedReleaseAdapter` implements the public release port. A channel response is
`{schema:'distribution-signed-channel-v1', keyId, payload, signature}`. Payload is
canonical base64 of UTF-8 JSON; signature is base64 Ed25519 over those exact bytes.
The private signing key belongs to the publisher and is never needed by this
adapter. Public trusted keys are supplied out of band.

Decoded payload:

```ts
interface ReleaseChannel {
  schema: 'distribution-channel-v1';
  expiresAt: number; // Unix milliseconds
  recommendedId: string | null;
  releases: ReleaseDescriptor[];
  releaseNotes?: ReleaseNotesPublication; // optional signed editorial data
}
```

The exported `ReleaseDescriptor` binds:

- Release ID, version, source revision, and a canonical `releaseDigest`.
- Artifact URL, exact SHA-256 and byte length.
- Platform, architecture and a JavaScript entrypoint inside the package.
- Every file's relative path, SHA-256, byte length and regular/executable mode.
- Every installed package's name, version, relative package root, repository,
  configured tracking ref and exact source revision, including bundled packages.

The archive is a complete npm-format gzip tarball under `package/`, with all
runtime dependencies bundled. The adapter performs no online npm resolution,
install scripts, source overlay or in-place upgrade. It starts the verified
JavaScript entrypoint using the supervisor's Node binary and explicit environment.
No ambient credentials are inherited implicitly.

The publisher must generate this manifest from the **actual packaged artifact**,
include complete component provenance, sign it, and provide the configured
channel endpoint. Existing legacy Python release assets are not silently
interpreted as this new format. `releaseDigest(descriptor)` binds normalized
file and component inventories, entrypoint/platform/architecture and release
source identity; the tarball SHA is checked separately to avoid circular hashes.
Changing an inventory requires a new digest and signature.

Manual checks bypass TTL. Forward preparation re-fetches the channel and calls
`resolveSources` with each unique repository/tracking-ref pair. The source port
must return exactly one `{repository, ref, revision, protected: boolean}` record
per requested source. Protected local edits, explicit overrides or source pins
block forward replacement and stay untouched. A tracking ref that has advanced
past the signed inventory produces `source_advanced`; the publisher must qualify
and publish a current artifact. The adapter does not rebuild a mixed graph or
silently install stale source. Native worker source selection remains entirely
with the native owner.

Downloaded archives are stored by hash; qualified candidates are stored by
release digest. An existing modified artifact/candidate is preserved and returns
`local_source_changes`. The adapter never deletes another installation's caches,
settings, native generations or workspace content. Retained signed candidate
receipts can qualify rollback after channel expiry. They are reproduction and
rollback evidence, not standing forward-update pins; forward preparation always
checks the current channel and source inventory again.

Archives are checked before extraction and the extracted tree is checked again.
Links, devices, duplicate/case-colliding paths, traversal, unsafe portable names,
undeclared files/packages, excessive sizes and checksum mismatches are rejected.
Channel responses are bounded to 16 MiB, archives to 256 MiB, individual files to
128 MiB, extracted content to 1 GiB, catalogs to 20 releases, and component lists
to 500 entries. The hardened `tar` library is bundled with this package; its
license and dependencies remain in the installed npm artifact. Downloads are
currently buffered up to the declared archive limit; this is an explicit memory
bound, not a claim of streaming installation.

A receipt path is provided to the child in
`AMPLIFIER_DISTRIBUTION_RELEASE_RECEIPT`. The child readiness implementation must
validate the signed descriptor and actual installed tree before reporting the
bound digest. Version 0.4 provides `createRuntimeIdentity` to verify the actual
process entrypoint, signed installed inventory and launch binding, and expose
`inspectRunning` with a separate actual-readiness callback. See
[HOST-CONTROL.md](HOST-CONTROL.md). The lower-level `readSignedChannel` and
`verifyReleaseTree` helpers remain available. Copying the expected identity from
that file without validating the installation does not establish readiness.

## Acceptance boundary

The additional independent-install test packs and installs this public package,
starts its installed daemon CLI in another process, and drives it with both the
installed client and installed command CLI. A fixture publisher serves signed
channels and two packaged releases with a real bundled dependency. The fixture
application verifies its installation before publishing authenticated readiness.
Actual process promotion, a new launch identity, exact digest/data scope,
rollback, notifications and command deduplication are asserted. Atomic admission
is exercised through a separate public **fixture** host port that persists its
fence across child replacement; it is not the production AHP host implementation.

Set `DISTRIBUTION_SUPERVISOR_ACCEPTANCE_DIR` to retain the exact installed package,
SHA-256 and sanitized receipt. Existing owner acceptance can also be retained via
`DISTRIBUTION_OWNER_ACCEPTANCE_DIR`. No test changes a production installation.
Systemd, launchd, Windows services, production signed publication, host coverage
leases, native recovery coordination, real credentials and full Unified UI/device
acceptance remain separate integration gates. No release has been published by
this component workstream.

## Explicit production composition (version 0.6)

`createPristineInstallation({directory, dataScope, initial})` is an **installer
command**, never a detection heuristic. It exclusively creates a new private
installation directory and writes/fsyncs authority for one exact initial signed
release identity. An existing directory is refused, even if discovery is absent.
The returned layout includes `authorityFile`, `dataDirectory`, `releaseDirectory`,
`applicationStateDirectory`, and private host/supervisor token/discovery paths.
The installer must use those paths in the actual signed application's launch
configuration; this helper does not parse arbitrary application configuration or
authorize adoption of another service or state directory. It leaves interrupted
allocation/claim evidence in place; there is no automatic reset/delete operation.

`createProductionSupervisorPorts({dataScope, dataDirectory, releaseDirectory,
hostDiscoveryFile, provisioningAuthorityFile?, resolveSources, fetch?})` connects
only authenticated private host control, forwards idle events, and provides
`initialProvisioning` when explicit authority is configured. Ordinary `inspect`
never returns inferred absence. Missing, malformed, unauthenticated or mismatched
host discovery fails closed, including after first launch or supervisor restart.

`runProductionSupervisor(configuration, {resolveSources, fetch?, startInitial?})`
composes these ports with the existing signed release adapter and owned-process
lifecycle, without an `adapterModule`. Configuration extends the existing schema
with `hostDiscoveryFile` and optional `provisioningAuthorityFile`; all existing
release/trust/initial/data/token/discovery options remain. `startInitial:true`
requires authority. It rechecks the signed initial candidate and independently
observes every source in that release's component inventory before claiming it.
It returns the same owner/lifecycle/close surface as `runSupervisor`.

`runSupervisor(configuration, {ports?, startInitial?})` adds direct public port
injection; the existing operator `adapterModule` path remains supported. Optional
`ports.qualifyInitial` runs after initial artifact verification and before any
first-launch effect. Optional `ports.close` releases its observation resources.

`OwnedProcessLifecycle` accepts an additive `initialProvisioning.claim(request)`
port. Only explicit `startInitial` can consume it. The production claim validates
namespace, data scope, exact target digest, absence of application state and host
discovery, then exclusively writes and fsyncs a bound claim before spawning.
Two processes cannot consume the same authority. A partial write, failed spawn,
lost readiness, aborted attempt, or process interruption never authorizes replay.
An already claimed installation needs explicit installer/operator reconciliation;
reopening the factory does not turn it pristine again. Ordinary restart cannot
consume initial authority. Existing lifecycle adapters without this port retain
their prior contract.

Source resolution is deliberately **not implemented by copying signed publisher
revisions**. The caller must supply the public source owner's actual observations
for each requested repository/ref. Protected overrides and advanced refs retain
the existing fail-closed behavior. This verifies the packaged distribution graph,
not separately managed native generations or arbitrary external imports. The
installer and source resolver still require deployment-specific qualification.
Existing-service adoption, supervisor crash adoption, OS service ownership and
native-generation currency remain outside this factory's guarantees.


## Forward activation currency (version 0.7)

Preparing a candidate can precede a long wait for active work to finish. The
optional public `ReleasePort.qualifyActivation(target, context)` is therefore a
separate **read-only** gate. The signed adapter implements it; the owner calls it
for forward installs after obtaining held admission and checking the running
identity, immediately before `restart_requested`. It freshly verifies the trusted
channel still contains the exact retained signed descriptor and independently
observes its tracking refs. It does not download, prepare, edit source overrides,
or rewrite candidate/rollback receipts. `verify()` remains offline integrity
verification. Rollback never invokes the new latest-source gate.

`resolveSources` now receives `SourceResolutionContext`, extending the existing
operation context with `fresh:true` and `reason:'preparation'|'activation'`.
The source owner must bypass completed cache entries for these observations;
concurrent live requests may coalesce. The owner records `qualifying_activation`
and `activation_qualified` phases, and an additive public receipt field
`activation:{startedAt,completedAt?}` distinguishes this observation from earlier
candidate preparation. Timestamps describe the check interval, not a source lease:
refs can still advance immediately after observation.

An advanced, protected, superseded, unavailable, or invalid source/channel refuses
activation before restart, durably records `pre_restart_refused`, and releases
held admission as unchanged. A lost restart response remains unknown and is never
retried. Process interruption during either activation phase retains an unresolved
admission fence; construction, notifications and inspection cannot replay it.
Generic adapters without the optional hook keep their existing contract and do
not gain a source-currency guarantee. External Python/native-generation inventory
remains outside signed npm graph qualification.

### App readiness and admission settlement (version 0.8)

A successful `ready` receipt proves the replacement app's exact identity; the
host may still be reopening its intake. Receipts now expose a separate durable
`admissionSettlement: {state, outcome, updatedAt}`. Its `state` is `pending`,
`settled`, or `unknown`; outcome is `ready`, `unchanged`, or `unknown`.
Do not enable the next update mutation from `status: succeeded` alone.

`inspect().actionReadiness` is `{state: 'available'|'busy'|'reconciliation_required',
commandId?}` and accounts for every retained unresolved admission, including
older operations outside the recent receipt list. This expresses update-owner
readiness, not the absence of unrelated application work. Subscribe to pushed
receipt changes and re-read inspection. No polling or inferred delay is needed.

`waitFor(id)` waits through pending settlement, then returns settled completion
or explicit unknown. A lost release response preserves app readiness but marks
settlement unknown and blocks further replacement effects. Explicit `reconcile`
checks the authenticated running identity and idempotently reconciles the held
host fence; it never repeats a launch. Settled records are not downgraded by a
late lost reply. A restarted owner retains pending or legacy unproven settlement
as unknown rather than assuming that an earlier ready receipt opened intake.

RPC progress is optional JSON whitespace negotiated by the authenticated client.
The ten-second transport limit measures silence rather than total operation time;
the original command stays pending through runtime verification and participant
release. Keepalives never settle it or authorize another update. Reconciliation
uses the same transport, so a long exact reconciliation can return its final
receipt instead of timing out while the server completes it. Lost connections
still leave unknown outcomes; receipt inspection does not replay a launch.

Adoption requires the external supervisor package as well as the child package.
A signed application update does not replace the running supervisor. Use the
supervisor's supported stopped-state/service handoff under its existing ownership
contract; do not restart it with an owned child still attached or patch its files.
The staged-file mode correction also belongs in that external package. The tar
`chmod: true` option alone does not restore every new file's mode under a
restrictive service umask. After validating the archive, preparation explicitly
restores the signed 0644/0755 file modes in its fresh private stage, then performs
full inventory verification. It never changes installed files or the global
umask. Candidate directories and receipts retain private permissions.

Optional release history is described in [RELEASE-NOTES.md](RELEASE-NOTES.md).
The publisher includes it before signing the channel. Do not fetch or scrape
unsigned release text during manual checks. Published links are fallback
navigation only. Historical notes are not installable artifacts or source pins.
