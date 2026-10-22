# Host-owned isolated checks

`workspace.tests.submit` queues bounded checks per root conversation. The global
**Settings → Testing & resources → Advanced execution limits** controls both the
total pending queue (default 64) and **Maximum pending checks per chat** (default
0: no per-chat count ceiling). **Owned test environments per chat** also defaults
to 0. Positive saved values remain operator ceilings and count all waiting/active
checks or retained environments, including those beyond the first displayed page.
A request with `independent: true` may overlap other independent checks in distinct
owned environments, subject to physical resource admission and retained-owner
bounds; default requests preserve ordered work. Queue wait defaults to 0 (until
Stop or cancellation). An explicit wait limit and already-accepted deadlines keep
their original meaning; changing defaults never rewrites a queued receipt. The
host saves the request in the existing command journal, waits for physical
backend capacity, takes a working-folder snapshot when admitted, executes the
command once, stops the exact owned environment, exports source and artifacts,
then removes the environment. Export or removal uncertainty preserves ownership
and evidence. Docker exports the working folder and artifact directory; do not
put unique output elsewhere. The first Incus adapter retains a bounded complete
instance archive, with its storage cost described in
[Incus test environments](workspaces/incus-test-environments.md).

The user and agent use the same actions. The workspace's existing test section
shows waiting and completed checks; there is no new chat-header control. User
submission does not automatically start a model turn. Agent submission defaults
to a guarded completion notification and can use `notify:false` for inspection
only. A busy chat receives that notification at its ordinary idle boundary.
The result does not satisfy a shared-task dependency or prove product acceptance.

## Lifecycle and ownership

- Keep `requestId` stable. Re-reading or repeating that exact request returns
  its receipt; changed arguments with the same ID are rejected.
- `workspace.tests.read` returns bounded pages of compact outcomes. Full check
  output stays in the existing environment operation receipt. It is not copied
  into every queue row or workspace catalog.
- `workspace.tests.result` pages that exact recorded output after cleanup,
  preserving the exit code and any capture-limit indicator.
- Cancellation before admission starts no command. New admitted checks run in a
  durable guest supervisor, independent of host request/connection lifetime.
  Cancellation requests termination of the exact command group; the supervisor
  escalates from terminate to kill after two seconds and records the actual
  exit before cleanup. Cancelling a model alone does not undo an admitted check.
  Cancellation during dependency preparation requests the exact owned builder
  to stop, without waiting for the full preparation timeout.
- Stop, moved placement, assignment cancellation, revoked capability and changed
  backend configuration invalidate waiting admission. Cleanup of an admitted
  owned check is part of the original request and can still stop/export/remove
  that environment after Stop; it cannot start a new check or affect a peer.
- Restart recovers queued intents and observes admitted owners. It uses exact
  operation receipts to recover known results, never reruns a test to discover
  its result, and resumes only authorized cleanup. Unknown cleanup remains
  inspectable; no age-based deletion or global prune occurs.
- New checks retain a guest job identity bound to the root, launch, environment,
  command hash and guest deadline. Exclusive admission precedes process creation.
  Restart during a check reattaches observation; a lost start response never
  calls start again. Missing admission, an incomplete launch, a dead supervisor,
  or changed owner becomes unknown and preserves the environment. Automatic
  stop/export/remove requires a recorded terminal guest result. Old queue
  receipts retain their original bounded-execute recovery behavior.
- Checks use submission order across local and registered remote machines.
  A standard request waits for earlier checks and owned cleanup; it also blocks
  later independent requests until it settles. Consecutive explicitly independent
  requests may overlap within any positive per-chat environment ceiling. Each
  request keeps its own source snapshot, resource ID, result and cleanup journal.
  An unknown outcome never discards its retained owner, even after restart.
  Independent work may use available capacity without retrying or resolving that
  unknown; an earlier ordered check remains a barrier until its cleanup settles.
  Manual environments count against the same per-chat ceiling. Waiting reasons
  remain visible in the existing check list.
- A host admits FIFO among its eligible roots. One physical capacity observation
  is shared by a batch; unavailable backends back off to at most one observation
  per minute. Local cleanup wakes the queue immediately. Physical backend
  admission performs its own atomic recheck; a snapshot is not a reservation.

## Capacity policy

The optional per-source **Outstanding task chats per manager** count also defaults
to 0. Explicit zero disables only that count gate, not assignment, causal-depth,
notification or ownership checks. Saved positive settings remain unchanged.
The global pending queue, retained resource-owner and reservation bounds remain
positive protections for bounded journals and ownership records; zero does not
disable them. Removing a chat count ceiling does not itself provide machine
resource admission for ordinary model workers. Test environments continue to use
their separate physical resource authority described below.

New managed setups with a verified native machine authority use CPU/memory
reservations and observed memory, disk and CPU pressure instead of a fixed
running/retained-environment count. Dependency preparation uses those same
resource reservations; its default is no separate build-count ceiling. Explicit
saved operator ceilings remain effective. Existing configurations are not
silently rewritten: an operator can set the running and retained counts to zero
in Settings to use resource admission while keeping the disk reserve. A daemon
without verified native resource admission keeps conservative setup defaults.

The fixed updater qualification probe is independent of ordinary count quotas,
including retained-owner counts. Its exact owner still reserves one CPU and
512 MiB, checks current physical pressure and backend storage, and journals each
effect before dispatch. Unknown launches and cleanup keep their owner; quota
independence never authorizes deleting another environment or replaying work.
App/component updates can proceed while this isolated check waits for genuine
resource availability. No ordinary action argument grants the updater lane.

Global **Settings → Testing & resources → Advanced limits** exposes running
and total retained environment ceilings and the free-disk reserve. These apply
across workspaces on this app installation. The screen shows each effective
value and whether it comes from a global setting, an optional selected-runner
connection override, the configured backend, or the backend default. An empty
field inherits; explicit zero disables that individual limit. Saving requires
an exact settings/selected-runner revision. Agents additionally need global
`host_defaults` configuration authority. Ordinary checks cannot raise limits.

The host keeps these mutable overrides in a separate private `test-capacity.json`
policy, not the immutable backend configuration used by owned environments.
Consequently existing queued receipts, source bindings, and cleanup snapshots
remain valid after settings edits, including receipts written before this UI.
A save clears queue backoff and wakes waiting checks. Capacity observations and
the final guarded launch admission use the current policy. Setup readiness uses
the same effective limits and invalidates its observation cache when they change;
retained backend defaults must not appear as a current capacity refusal. A policy decrease
never kills already admitted work. Changes to backend identity, installation,
endpoint, credentials, source/image or placement retain their existing refusal
and cleanup rules. Direct edits to the old backend configuration remain exact
configuration changes, not a supported live policy update.

Runner overrides identify the qualified **connection**, not a universal machine.
Changing connection aliases can select a different override; global defaults
still apply. Docker and Incus retain their own physical daemon/project admission
locks, but this UI does not synchronize policies across other app installations,
users, or container systems. The machine resource authority and queue settings
remain separate owners; their current controls are shown separately in Settings.
Registered machines use their own verified target authority.

The Docker configuration may set `maxRunning` (default 15), `maxInstances`
(default 256 retained plus active), and `minFreeBytes` (default 10 GiB). Zero
explicitly disables that individual limit. Stopped environments consume storage
but not running slots. Unknown/paused/starting physical instances remain active.
Docker admission is serialized for cooperating processes of one host user,
keyed by the actual daemon identity rather than socket spelling. It does not
enforce a limit on old tools or unrelated callers that bypass this admission.
An interrupted launch retains a pending reservation until its exact physical
owner is observed or confirmed cleanup releases it. Missing acknowledgements
do not make capacity free, and a visible project is counted only once.

Docker storage is observed at the explicit local daemon's data directory. An
unreadable daemon/storage observation waits with a distinct blocker; it is not
reported as a full execution pool. The queue never raises operator limits or
deletes someone else's environment to gain room.

The legacy Incus backend needs the separately qualified adapter and launcher.
An older CLI invocation does not become a queued request automatically. Updated
agent guidance must prefer `workspace.tests.submit` for bounded checks. Existing
manual environment controls remain available for interactive sessions, whose
caller owns lifecycle and cleanup. The original direct execute action remains
bounded to five minutes. Prepared Python and browser queue profiles default to
twenty minutes and accept up to thirty minutes; the small standard-library
profile remains limited to five minutes. The guest enforces this deadline while
host observations remain short, so longer work does not hold one host request
open for the duration.

## Guest result ownership

The supervisor's control directory is root-owned and separate from writable
source/artifacts. Commands run as uid/gid 65534 with no supplementary groups.
Docker retains a read-only root, no-new-privileges, no host mounts and no exposed
ports; only CHOWN, SETUID, SETGID and KILL capabilities are available to its root
supervisor. Incus remains unprivileged and offline. Prepared dependencies must
be readable by the workload user; writable project dependencies are initialized
inside the owned source snapshot.

Output is drained continuously with an 8 KiB limit per stream. Exact bounded
output and terminal status are retained in the operation journal before cleanup;
queue lists expose only compact job progress. Linux boot identity and process
start ticks distinguish the supervisor from a reused PID. The command group is
terminated before terminal publication; final owned-environment cleanup also
contains processes that intentionally detached from that group. This provides
bounded check evidence, not a security verdict about the tested source.

## Qualification boundaries

Unit tests exercise dispatch, durable journals, cancellation, revocation,
capacity refusal, exact-request retry, uncertain commands and restart cleanup
against controlled backend effects. Physical Docker/Incus and native worker
continuation evidence must be recorded separately. A passing command proves
that command's result, not release adoption, real-provider behavior, browser
rendering or integrated acceptance.

## Shared native-machine resource admission

Settings → Testing & resources also exposes **This machine’s test resources**.
A native Linux machine uses one private authority per OS account, independent of
Amplifier installation data roots, workspaces, runner aliases, Docker daemons and
Incus projects. The location is derived from the OS account database and machine
identity, not the helper's temporary HOME or an agent-provided path. A local Unix
socket kernel peer must identify the actual Docker or Incus daemon. A proxy,
remote connection or Desktop VM does not prove a native local execution target.

The shared policy has advanced overrides for reserved CPU, reserved memory,
minimum observed available memory, free home-storage bytes, CPU load, and
concurrent dependency builds. Empty fields use automatic values; zero disables
that individual check. Automatic ceilings are twice the logical CPU count,
80% of total memory and two dependency builds. The automatic pressure thresholds
keep 1 GiB memory and 10 GiB home storage available, with a load-average ceiling
of twice the logical CPU count. Backend free-storage limits still independently
protect the actual container storage pool. These are admission controls and
observations, not resource isolation from arbitrary programs or OS guarantees.

The queue freezes the application-owned CPU/memory/process/tmp/shared-memory
request when accepting a job. A direct environment freezes it at allocation.
Later code updates cannot silently reinterpret that admitted request. Historical
jobs without a frozen request use the then-current profile when first allocated.
The same frozen request is used by dependency builders and test environments.

Immediately before admitting new work, the shared lock serializes an authoritative
policy read, target-machine resource observation and reservation. Reservations
refer to the existing canonical allocation and launch operation; they are not a
second job ledger or scheduler. Admitted preparation, active work and uncertain
launches continue to count. Lowering a policy never kills admitted work. Source
snapshot/ownership is retained when a queued check waits; no build or test starts
when the machine budget refuses it. Current request, effective budgets and exact
wait reasons are available in the existing environment/queue receipts.

A confirmed completed build frees its build slot, retaining the request's CPU and
memory reservation for launch. Confirmed owner stop/removal or an exact verified
reconciliation releases the reservation. An unknown result, process exit, elapsed
time or missing UI record does not release it. The allocation-before-index-receipt
commit gap is recoverable only through the same positive owner cleanup evidence.
There is no administrative “forget uncertain work” escape hatch.

Machine policy updates use a shared revision comparison. Two installations cannot
silently overwrite one another. The Settings UI reconciles a lost save response by
reading the exact desired values, without replaying a mutation. The saving app
wakes its queue; other cooperating installations see the shared policy on their
next admission check.

### Explicit scope and remaining work

- Native Linux (including a local Linux runner in WSL) is the first supported
  shared authority. Its policy covers cooperating current clients under the same
  OS account. Other accounts and older/unmanaged clients are observed as resource
  pressure, not governed by reservations.
- Remote Incus, Docker Desktop and unsupported native hosts retain their existing
  supported testing route and backend limits. They report shared-machine authority
  as unavailable; they do not acquire a false local budget or a new generic stop.
- Remote Incus dependency preparation now checks the target's memory instead of
  taking the minimum of target and controller memory.
- Registered remote target controllers, cross-account administration and automatic
  machine routing remain separate work. Multiple client connections to one remote
  machine must share that machine's authority before being called machine-wide.
- Queue depth/wait controls, profile execution timeout controls, artifact retention
  limits and broader host registration remain distinct from these resource budgets.

### Returned preservation failure cleanup continuation

A qualified legacy returned export-limit failure keeps its original UNKNOWN
receipt. If its separately admitted cleanup verifies guest removal but cannot
finish dependency cleanup, the next `finishCleanup` names that confirmed guest
removal request. It revalidates the persisted legacy qualification against the
original receipt and unchanged owner/source/backend bindings, then finishes only
dependency cleanup. It does not issue another guest removal or create another
operation identity. Missing or changed qualification, another unresolved removal,
and unverified guest removal remain held. Restart does not weaken these checks.

The same continuation also accepts an exact retained helper response reporting
`verify-exports` / `phase_failed` / `destroyEffect: not_dispatched`, after the
host drained and reaped that helper. Passive `workspace.environments.removal.read`
reports `returned_archive_verification_failure` for this case. It does not read
archive contents or infer execution from diagnostic files. A generic UNKNOWN,
missing response, dispatch uncertainty or another unresolved removal stays held.

Fresh archive validation failures report `archive_verification_failed` before
any removal effect, so existing queue cleanup can continue through `finishCleanup`.
The continuation exports both filesystems again, verifies bounded archive-only
custody without extracting or following links, rechecks the stopped owner and
backend, then verifies removal and finishes dependency cleanup. Existing exports
alone never authorize destruction. The original uncertain receipt remains intact;
its ordered queue barrier clears only when the canonical resource owner retires.

## Work concurrency settings

The same Advanced execution limits form exposes global limits for outstanding
task chats per manager (default 0: no count ceiling), owned test environments per
chat (default 0: no count ceiling),
all non-retired resource owners (default 512), and resource owners per chat
(default 32), and cooperative reservations per installation (default 1024) and
per chat including nested workers (default 64). Retained-owner and reservation
bounds must stay positive; the first two counts accept zero. These are operator
settings, not model-selected overrides.
An empty setting restores the default. The host-defaults authority and exact
settings revision protect changes through either the UI or agent actions.
Machine CPU, memory, storage and backend limits remain additional admission gates;
no workspace setting can make another machine's capacity count as local capacity.

Concurrency is evaluated for new admission, including already waiting checks.
Lowering a ceiling never cancels existing work, retires unknown ownership, changes
a saved execution budget, or invalidates cleanup. Existing exact allocation
recovery remains possible above the newly lowered cap. Raising a ceiling wakes
the queue. The allocator counts all non-retired owners under its existing SQLite
writer transaction, including the allocation-before-queue-link crash gap.

Environment inspection is paged and every action names the exact resource and
revision. Separate checks can be inspected, cancelled, and cleaned up without
selecting or stopping another check from the same chat.
