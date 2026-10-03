# Distribution update owner

`@amplifier/unified-distribution-update-owner` is an independently installable
TypeScript/Node package for distribution release replacement. It owns a private
durable command ledger, catalog, preferences, qualified candidate references,
current/previous release pointers, scheduling, and safe diagnostic projections.
It does not register host actions, import AppService, write native runtime
state, or replay conversations.

Requires Node 22.16 or later (`node:sqlite`). The hardened `tar` parser and its dependencies are bundled in the npm artifact.
Build and test with `npm ci && npm test` from this directory.

The external supervisor, authenticated client/CLI and signed release adapter are
documented in [SUPERVISOR.md](SUPERVISOR.md). The public host quiescence
connection and authenticated release proof are in [HOST-CONTROL.md](HOST-CONTROL.md).
Bounded signed release history, high-impact notices, and exact review receipts
are documented in [RELEASE-NOTES.md](RELEASE-NOTES.md). Notes travel with the
existing channel response; they add no availability lookup or session scan.

## Host composition

The caller supplies authorization, command IDs, release preparation, lifecycle
admission, and an authenticated readiness reader. Keep this owner in a supervisor
that outlives the distribution process being replaced. The component exports raw
operations; the host may expose them under `updates.application.*`. Do not
register another `updates.check/install/rollback` authority over native runtime
generations. Those remain owned by amplifier-app-acp and the existing native
maintenance capability.

```ts
import {DistributionUpdateOwner} from
  '@amplifier/unified-distribution-update-owner';

const owner = new DistributionUpdateOwner({
  directory: privateOwnerDirectory,
  dataScope: opaqueInstanceScope,
  initial: qualifiedCurrentRelease,
  releases: publicReleaseAdapter,
  lifecycle: publicLifecycleAdapter,
  native: passiveNativeInspectionAdapter, // optional
  onChange: receipt => publishMaintenanceReceipt(receipt),
});

owner.start(); // explicitly enable the persisted background check preference
owner.check(commandId); // manual: fresh check starts immediately
owner.install(anotherCommandId); // queue even while the check is running
// Subscribe to onChange; receipt(id) is safe after a client disconnect.
```

All operations must enter through the same authorized host boundary for agents
and users. Command IDs and release IDs are opaque bounded identifiers, never
paths, account names, secrets, or hostnames. Reusing an ID and its normalized
arguments returns the existing receipt; changing its arguments is rejected.
`waitFor(id)` is an optional in-process terminal receipt convenience. It does not
poll, time out inference, or replace durable `receipt(id)` inspection.

| Interface | Required behavior |
| --- | --- |
| `releases.check({commandId, signal, fresh})` | Return at most 100 release identities and one optional recommended ID. Manual `fresh` bypasses availability TTL; coalescing an actual in-flight remote request is permitted. Failed checks invalidate the old recommendation. |
| `releases.prepare(release, context)` | Stage an inactive candidate, retain exact provenance and immutable package/dependency proof, and return an opaque handle. Preserve explicit source overrides, pins, and local edits. This callback cannot restart or activate the service. |
| `releases.verify(candidate, context)` | Read-only check of the exact candidate artifact, dependency graph, source rules, and qualification receipt. Verify the candidate that will be launched, including after waiting for idle and on rollback. Availability is insufficient. |
| `lifecycle.inspect()` | Passive authenticated readiness, or null. Report release ID/version/revision/digest, launch instance ID, opaque data scope, and ready boolean. No cold starts or source scans. |
| `lifecycle.admitRestart(context)` | Atomically check all active app/voice/work activity and close intake; return null while busy. A granted lease continuously holds that gate and includes `activeWork: 0`, `intakeClosed: true`, current process instance, data scope, and observation time. A sampled idle status is insufficient. |
| `lease.release(outcome)` | `ready` permits admission to the verified replacement; `unchanged` permits the unchanged process. `unknown` retains a host fence until authoritative passive reconciliation confirms readiness. |
| `lifecycle.restart(request)` | Compare the previous launch identity, replace only the owned process, and preserve data. Persist the supplied new instance ID through startup. It can have effects even if it throws; the owner never automatically retries it. |
| `native.inspect()` | Optional bounded public native-generation status: current/previous opaque IDs and active/pending worker counts. Inspection only; no native promotion, repair, or reset authority. |
| `onChange(receipt)` | Publish accepted/progress/terminal receipts immediately. Notification exceptions cannot change operation truth. After reconciliation, the host can use the durable succeeded receipt to release its retained restart fence. |

Release `revision` identifies source; `digest` is the adapter's cryptographic
proof of the qualified artifact/dependency graph. Bind both to the installed
candidate and authenticated running identity. Never satisfy readiness by copying
expected values into a response without verifying the running installation.
The opaque candidate handle is private and absent from support diagnostics.

`inspectRunning()` exposes the sanitized public readiness evidence independently
of persisted catalog/current state. `inspect()` is synchronous and reads only
local owner state. Quiescence evidence is retained with the restart receipt;
its public projection contains the active-work count, closed-intake flag, and
observation time. This gives a future offline snapshot coordinator an explicit
admission/readiness seam. This package provides no backup or reset implementation
and never copies live mutable application databases.

## Immediate scheduling and bounded parallel work

- Manual check, install, and rollback requests wake the queue in a microtask.
  They do not wait for a periodic poll. An install submitted during a check is
  retained and continues as soon as that check succeeds.
- Preparation starts before restart admission, so downloads and qualification can
  proceed while users work. Only actual process replacement requires the gate.
- An eligible automatic install follows its completed check immediately. Manual
  installation remains available with automatic installation disabled.
- A busy replacement waits for `notifyIdle()`. Wire the host's idle transition to
  that method. Notifications racing with admission are retained; no idle polling
  loop or guessed delay is needed.
- `AvailabilityCache` joins actual in-flight reads, separates access scopes via
  hashes, bounds cached entries, and supports fresh reads. It is availability
  caching only; it never qualifies an installation. Do not pass credentials as
  scope identifiers or cache secret-bearing values.
- `DownloadScheduler` defaults to four simultaneous downloads globally across
  batches. It serializes writes to each target. On failure it cancels and awaits
  sibling work, then reports the first failure. All writers must share that
  scheduler instance, and callbacks must settle after cancellation.

The host's release adapter may use the cache and scheduler helpers; the owner
cannot parallelize opaque work inside a callback. Promotion remains serialized.
Background checks are scheduled at their next due time rather than by a frequent
poll. `start()` is explicit; construction does not cause network access.

## Durable uncertainty and recovery

The private SQLite WAL ledger uses full synchronous commits and a live-owner
lease. Protect its directory and do not share it across host identities or
network filesystems. Command IDs are retained for durable deduplication. Public
inspection returns the latest 50 receipts, at most 100 catalog entries and 100
events; the command ledger itself is not a bounded eviction cache.

Before calling `restart`, the owner durably records its target, predecessor,
quiescence evidence, new launch ID, and `restart_requested` phase. Success
requires exact release identity, data scope, readiness, and a new launch ID.
A lost response or readiness mismatch is `unknown`; further installation and
rollback are fenced. Use `reconcile(commandId)` to inspect and reverify the
candidate. It can acknowledge an already running replacement but never prepares
or launches one. Reusing the original command ID never replays its effects.

On owner restart, interrupted queued/running/waiting commands become `unknown`;
the queue is never reconstructed. An interrupted check invalidates the previous
recommendation. Non-restart interruptions may be inspected and superseded by an
explicit new command, since their preparation callback could affect only an
inactive candidate. Unknown restart effects need passive reconciliation or
operator investigation, not a retry button. A retained host intake fence must
also survive supervisor recovery. Rebind notifications and inspect receipts
before admitting new work; do not rely on delivery of one in-memory callback.

`rollback(id, expectedCurrentId)` compares the current release, reverifies the
retained previous candidate, and uses the same admission and new-process proof.
It disables automatic installation so the next background check cannot undo the
rollback. The current and previous candidate handles must remain resolvable.
Candidate installation, retention/garbage collection, and a deployment's package
signature policy are responsibilities of the release adapter. This owner does
not delete candidate directories or external state.

`close()` aborts owned pending operations and records uncertainty. Adapters must
honor cancellation before external effects and settle their promises. It does
not stop the distribution process or imply rollback. A stale OS PID can keep a
reused ledger conservatively locked; do not delete the ledger to force retries.
Investigate ownership first. Only one supervisor on one host may use a ledger.

## Portable owned-process adapter

`OwnedProcessLifecycle` implements the lifecycle interface for direct child
processes. Its trusted resolver maps qualified handles to a command, arguments,
working directory, and an explicit environment. It uses no shell and never
signals PIDs discovered from readiness responses or old receipts. Supply all
required environment entries; ambient credentials are not copied implicitly.
The adapter supplies per-launch `AMPLIFIER_DISTRIBUTION_INSTANCE_ID` and
`AMPLIFIER_DISTRIBUTION_DATA_SCOPE` to the child. The distribution must separately
report its actual installed source/dependency identity.

Only an explicit `startInitial()` can start the first owned process. `inspect()`
is passive. The adapter must remain outside the process being replaced. It
cannot adopt an existing system service or reclaim child ownership after its
supervisor dies. A new owner can passively reconcile a running process, but an
appropriate external service-manager adapter is needed for later replacement.

The adapter has separate stop/readiness deadlines (10/30 seconds by default,
configurable up to 10 minutes). These bound lifecycle observation, not provider
inference or compaction. A readiness deadline leaves the new process alive and
the command unknown. It never restarts again or rolls back automatically.
`close()` explicitly stops only its own child and does not forcibly kill a
process whose graceful stop was unconfirmed.

## Acceptance and limits

`npm test` exercises command durability, stale checks, immediate scheduling,
idle admission, wrong readiness identity, rollback, bounded diagnostics,
cache behavior and concurrent downloads. It also packs this component, installs
it into an independent consumer directory, and imports the installed package.
That consumer separately installs two fixture distribution packages and runs
real authenticated loopback readiness servers through actual process promotion
and rollback. It drops a rollback response, reopens the owner, and reconciles
without another restart. The fixture has no user workload; its explicit
quiescence gate is not proof of production workload fencing.

Set `DISTRIBUTION_OWNER_ACCEPTANCE_DIR` to an owned artifact directory when
running tests to retain the exact component tarball, SHA-256 and sanitized
acceptance receipt. Temporary installations and owned processes are cleaned up.
This demonstrates a real installed-package lifecycle on the recorded platform.
It does **not** establish systemd/launchd/Windows-service, production deployment,
full Unified rendering, physical device, or real-account acceptance. Host action
composition, production release preparation, production quiescence, and OS
service adapters must be qualified separately. See [PROVENANCE.md](PROVENANCE.md)
for the source behavior preserved from the released updater.

Explicit prepare/activate controls are documented in [STAGED-UPDATES.md](STAGED-UPDATES.md). Direct Install remains immediate.
