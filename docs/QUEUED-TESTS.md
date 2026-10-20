# Host-owned isolated checks

`workspace.tests.submit` admits one bounded check per root conversation. The
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
- A host admits FIFO among its eligible roots. One physical capacity observation
  is shared by a batch; unavailable backends back off to at most one observation
  per minute. Local cleanup wakes the queue immediately. Physical backend
  admission performs its own atomic recheck; a snapshot is not a reservation.

## Capacity policy

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
the final guarded launch admission use the current policy; a policy decrease
never kills already admitted work. Changes to backend identity, installation,
endpoint, credentials, source/image or placement retain their existing refusal
and cleanup rules. Direct edits to the old backend configuration remain exact
configuration changes, not a supported live policy update.

Runner overrides identify the qualified **connection**, not a universal machine.
Changing connection aliases can select a different override; global defaults
still apply. Docker and Incus retain their own physical daemon/project admission
locks, but this UI does not synchronize policies across other app installations,
users, or container systems. CPU/memory budgets, queue size/wait policies,
resource-aware scheduling and automatic routing across machines remain separate
work. This screen does not claim those controls exist.

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

### Legacy export-limit cleanup continuation

A qualified legacy returned export-limit failure keeps its original UNKNOWN
receipt. If its separately admitted cleanup verifies guest removal but cannot
finish dependency cleanup, the next `finishCleanup` names that confirmed guest
removal request. It revalidates the persisted legacy qualification against the
original receipt and unchanged owner/source/backend bindings, then finishes only
dependency cleanup. It does not issue another guest removal or create another
operation identity. Missing or changed qualification, another unresolved removal,
and unverified guest removal remain held. Restart does not weaken these checks.
