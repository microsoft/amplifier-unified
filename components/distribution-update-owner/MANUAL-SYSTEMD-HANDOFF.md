# Instrumented manual systemd source

Version 0.15 adds a first-party source adapter for a **previously instrumented**
manual Linux user service. It hands the same qualified release and existing
roots to `launchExistingStateHandoff`. It does not adopt a PID or reconstruct
past ownership. An already-running uninstrumented launcher cannot use it without
a separately coordinated launcher transition.

## Source and destination contract

The source calls `createManualSystemdHandoffLauncher` before opening application
or network listeners. Inputs are the actual `ServiceIdentity`, canonical
`ExistingStateBinding[]`, private authority directory, Linux observer, and a
trusted `qualifyCurrent()` callback. The callback independently qualifies the
installed launch bytes and returns a `PreparedRelease`; a configuration label or
copied expected digest is insufficient. Source and destination use the same
release, config file bindings and mutable directory identities. History is never
copied, reset or replayed.

The source passes `launcher.serviceLifecycle` to the public host and calls
`launcher.attach({host, requiredOwners, expectedOwners, close, exit})`. The
runtime's configured required census must exactly match the explicit reviewed
census. `close()` closes and awaits every source-owned application, access,
control, and ledger lifetime; `exit()` must terminate the source afterward. The
source verifier cannot release a completed source fence. The new supervisor's
normal public service-release verifier settles that fence after qualified launch.

`examples/manual-systemd-launcher.mjs` is reviewable composition code with explicit
peer callbacks. `createApplication` receives `serviceLifecycle` and
`ingressBinding: {owner, storage: {packageName, packageVersion, revision,
configKey, rootRole, stateDirectory}}`. `owner` is the exact gate participant
object, not an ID lookup. The callback validates source revision against the
assembled component graph and the authoritative configuration root, registers
them with the full actual owner inventory, and returns
`{host, requiredOwners, close}`. `createAccess` receives the gate and returns an
awaitable `close`. The accompanying minimal ingress patch hooks the existing
preview gateway's HTTP and WebSocket lifetimes. Both are integration examples;
they do not alter an installed launcher or supply a product-specific root graph.

The controller creates `createManualSystemdHandoffSource({sourceDirectory,
claimDirectory, bindings, observer})` and passes it as the existing-state
handoff source. It never starts/stops/enables a systemd unit or signals the
source. The destination uses a fresh supervisor service ledger and the ordinary
owned-child IPC handshake. This trusted operator API is absent from browser RPC.

1. Exclusively create and sync the private one-shot source launch authority
   before application creation. Existing authority refuses relaunch, including
   after crashes; never delete it to retry an uncertain operation.
2. Authenticate private source control and verify current installed bytes,
   configuration/root bindings, source instance and configured owner census.
3. Bind a Linux pidfd to the exact process **before** asking it to retire. Bind
   boot ID, process start ticks, cgroup, unit digest and systemd invocation too.
4. Persist admission intent; obtain and inspect the real host's continuously held
   full-owner fence. Busy refusal leaves the live source running.
5. Persist permanent source retirement before allowing any owned closure. Persist
   stop uncertainty before the stop request. Neither lost replies nor controller
   restart automatically repeat an operation.
6. Await source-owned closure and actual retained kernel exit. Require the signed
   durable closed receipt, unchanged qualified unit policy, inactive original
   invocation and empty original cgroup. Missing endpoints or PID files do not
   qualify. A nonparent observer leaves exit code/signal unknown.
7. Import the one-use proof, launch exactly one owned destination with the same
   release and paths, and qualify ready identity plus fence settlement.

The source user unit must be disabled for boot, `Type=simple`, `Restart=no`,
`KillMode=control-group`, without triggers, drop-ins or pending daemon reload.
The observer uses absolute Python with `pidfd_open` support and read-only
`systemctl --user show`. Exit waiting is kernel-event-driven; no periodic process
poll or wall-clock success inference is used. A failed closure remains unresolved.

## Ingress participant and authoritative roots

`createManualIngressGate({directory, id, onMayBeIdle?})` owns network forwarding lifetimes and
its own durable hold ledger only. Register the returned `participant` exactly
once as a required owner. Suggested integration ID: `manual-preview-ingress`.
Package: `@amplifier/unified-distribution-update-owner`, version `0.15.1`; source
revision is the exact committed packet revision. Authoritative private root role:
`service-ingress`, resolving to the supplied canonical `directory`. Root composition
must explicitly register the package name, source revision, configuration key and
private root provenance. An owner name alone does not establish archive coverage.

The directory holds `key`, signed `authority.json`, `lock.sqlite3`, and
`intake.sqlite3` plus SQLite sidecars. Preserve the whole root while stopped for
an archive. The lock prevents concurrent gate instances; it is released only on
owned close/process exit. Source launch authority and controller claim directories
are separate permanent service evidence, not retention candidates. Their paths,
authentication keys and root bindings must remain private.

| Public API | Exact contract |
| --- | --- |
| `participant.serviceStop` | `{version: 1}` |
| `participant.retentionHide` | `{version: 1}` |
| `participant.managedFiles` | `{version: 1, preservesCanonical: true}` |
| `participant.acquire(context)` | Explicitly supports `service-stop`, `distribution-update`, `recovery`, `retention-hide`, `managed-files-disposal`; other purposes refuse. Returns held lease or null. |
| `gate.enter()` | Synchronous admission before any async HTTP/WS work. Returns an idempotent completion callback, or null while service-stop/distribution-update is held. |
| `onMayBeIdle()` option | Called in a microtask when the last admitted network lifetime finishes. Wire to `HostControl.notifyMayBeIdle()` (and its supervisor idle subscription) so a busy manual update resumes immediately. Notification failure cannot alter admission truth. |
| `gate.inspect()` | `{active, held}`; `active` counts admitted HTTP response and complete upgraded WebSocket lifetimes, including pending upstream handshake. Local private inspection only. |
| held `lease.inspectRetentionReferences({sessions, limit})` | Returns `{coverage: 'complete', protected: [], omissions: []}` only for this ingress owner's scope. |
| held `lease.inspectManagedFilesReferences({sessions, limit, allocation})` | Same projection; refuses allocations overlapping this owner's private state root. |
| `lease.release(outcome, proof)` | Unknown retains the durable fence. Invalidates held inspectors. |
| `participant.reconcileRelease(request)` | Exact trusted verified receipt/fence/context required. Cannot turn unknown into no-effect merely by assertion. |
| `gate.close()` | Refuses with active admitted network lifetimes; releases only its own database handles/lock. |

Reference inspection binds the exact bounded selected session family and, for
managed files, the exact allocation tuple (`allocationId`, `allocationHash`,
`executionDirectory`, `treeHash`, `entryCount`, `bytes`). Changed selection refuses;
inspection after lease release/unknown refuses. At most 101 unique canonical
session refs are accepted. Only this adapter's empty reference scope is asserted:
it retains no chat history, queued business mutation, session-to-file link or
managed allocation. Authentication-cookie lifetimes in the access gateway are
network state, not chat references. Every downstream business owner remains an
independent required participant with its own references and mutation fence.

**Maintenance differs from process replacement.** Service stop and the actual
`HostControl.admitRestart` distribution-update path require no active HTTP/WS
lifetime and block new admission. Recovery, retention and disposal can be initiated
inside an HTTP request: its exclusive durable maintenance lease holds competing
service/handoff authority but leaves network forwarding open for that request,
receipt reads and recovery. Real business owners gate mutation separately. Treating
maintenance as network shutdown would deadlock the initiating request. The access
gateway must not cache, queue or mutate business state under this empty-reference
contract; any such future feature needs its own owner coverage.

Recovery accepts only an exact authenticated `unchanged` release proof for the
original fence, command, instance, data scope and receipt. The first release is
verified by the trusted host/recovery coordinator; ingress then durably binds its
exact signature for duplicate inspection. `unknown` preserves the hold across
owner-ledger reopen, keeps receipt forwarding available and continues to block
competing service/update admission. A live no-effect admission refusal can roll
back its own original lease; a reopened or unknown lease cannot use that shortcut.
A `ready` claim for a different process is not evidence that the original recovery
job finished. New-process recovery settlement needs a separate explicit host/native
contract; this patch does not weaken the current original-instance rule.

## Qualification and limits

Contract tests spawn real processes, exercise authenticated retirement and
negative/uncertain outcomes, and use real HTTP requests to prove maintenance
receipt delivery with separately fenced business writes. An opt-in Linux test
uses a separately installed owner package and real public host, an isolated
disabled user unit with synthetic data and loopback port 0, retained pidfd exit,
new owned destination launch, saved-history preservation and old-launch refusal.
It qualifies this adapter on Linux, not a product's full configured owner census,
signed assembled release, physical browser session or live deployment.

The 0.15.1 tests additionally exercise recovery from inside a real HTTP request,
original receipt reads during the hold, separately fenced business writes,
same-instance host/owner-ledger reopen, changed-instance refusal, and the actual
HostControl distribution-update and idle-event path with registered ingress.
This is not qualification of the complete product recovery aggregate or a native
archive/reset operation.

Manual Check/Install still dispatch immediately, without starting the background
scheduler. Tests enqueue install during check and release busy admission through
`notifyIdle`; terminal events arrive without a client status poll. Download
parallelism remains bounded by the shared scheduler, while dependent promotion
and ownership transitions stay ordered.
