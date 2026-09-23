# Moving a task between paired hosts

The Runtime settings page exposes **Move task between hosts** through the same
`portability.*` actions available to `app_control`. This is a staged ownership
transfer. It keeps the task ID, native session ID, saved conversation (including
voice-labelled messages), task controls, drafts, output IDs, comments and lineage.
No task message, schedule, worker or uncertain external operation starts during
transfer. A later explicit interaction resumes the task on its new owner.

The local `worktree.handoff` feature changes an execution folder without moving
history. Portability moves canonical history to a different host and creates a
new detached execution checkout there. The source retains private originals and
a permanent native ownership fence after release.

## Provision and pair

Both hosts need this Unified implementation and Foundation's durable native
transfer-fence API, including `SharedSessionStore.confirm_transfer_commit`.
Upgrade or disable **every** CLI, TUI, Unified runtime and other consumer that can
write participating native sessions before pairing. An old Foundation consumer
does not understand the marker and cannot be fenced by this implementation.
The destination probe checks its actual runtime; it cannot inventory arbitrary
old executables on either machine. This is a supported-runtime requirement, not
a guarantee against an uncooperative writer or an operator deleting state.

Use a local filesystem with advisory locks, atomic same-filesystem rename and
working file/directory sync. Foundation qualifies unsupported directory-sync
errors in its own contract; the Unified journal requires directory sync to
succeed. Network shares and power-loss behavior on unqualified filesystems have
not been accepted.

1. Start each host and choose **Inspect hosts and transfers**, or call
   `portability.inspect`. Record its public `host` object. Its ID is the SHA-256
   fingerprint of its Ed25519 public key. The display label is descriptive only.
2. Compare the public fingerprint over an independently authenticated channel.
   On each host, write the other host's public object into
   `<app-data>/portability/peers.json`, keyed by that object's `id`:

   ```json
   {"<peer-id>": {"id": "<peer-id>", "label": "Other host", "publicKey": "<public-key>"}}
   ```

   Keep the file private (mode `0600`). Never copy `identity.json`; that file
   holds the host's private signing key. There is no automatic trust enrollment.
3. Provision the same Git commit in a destination-owned repository. Provision
   the selected bundle, provider instance and model through destination settings
   and destination credentials. Source settings, module overrides and accounts
   are never mounted. The provider must be installed or independently provisioned;
   a provider source inside the transported checkout is rejected.

The UI deliberately uses manual pairing and private signed file exchange. It does
not discover remote machines or establish SSH access. Packages are signed, **not
encrypted**; copy them and receipts over an authenticated private connection and
keep their filesystem permissions private. The local two-process test uses local
file copy; actual SSH and Mac↔Spark transport require separate acceptance.

## Operator workflow

1. On the source, end any active voice call, select an explicit provider/model,
   and inspect the task repository. Review the saved history, outputs and files
   for credentials. Choose the paired destination and optionally include dirty
   work, then choose **Stage export and stop execution here**.
2. Wait for the export receipt to become `prepared`. `preparing` means cooperative
   native writer release and capture are still running. Copy its `package` file
   to the destination. Both UI and agent dispatch return the durable receipt.
3. On the destination, open **Import a staged task**, enter the copied package
   path and the provisioned repository path, then choose **Verify and stage
   import**. This creates a fresh checkout and an execution fence. Canonical
   native history is not published yet. A tiny provider request using only
   destination credentials verifies that the selected model accepts a request;
   it has no task history or tools. It may incur the provider's normal charge.
4. A successful `ready` receipt supplies a signed `.ready.json` path. Copy it to
   the source and choose **Release source ownership** with that path. The source
   first commits its permanent native fence, then archives canonical history,
   then publishes the signed `.release.json` file. Source release is irreversible.
5. Copy that release file to the destination and choose **Activate on this host**.
   The destination authenticates the exact transfer, rechecks its checkout and
   account, installs history/outputs, durably records `active`, and only then
   clears the destination's staging fence. The task remains idle. Confirm its
   history and outputs, then continue through normal task controls.

The account check attests only that the selected model accepted a small request
with destination-owned credentials. It does not attest a named person, billing
account, organization, all model entitlements, tool accounts or successful full
task resumption. A static provider catalog is never accepted as account proof.

Readiness uses one fixed, internal versioned policy for each stage and activation
attempt: at most one native input-token count and one generation, 1,024 total
output tokens (including reasoning), no elapsed-time deadline by default, no tools,
and no retries or continuations. The request is only `Reply with OK.` with the
saved model and effective reasoning effort. A saved explicit effort wins;
otherwise the destination must explicitly configure `reasoning_effort` or
`reasoning.effort`. An implicit provider default cannot authorize a probe.
Stage saves the policy and hash before restoring the checkout or probing.
Its signed readiness checks bind that policy, and activation authenticates those
checks and reuses the exact saved policy even if destination defaults change.
Success requires completed, nonempty, nonrefused output and confirmed client
closure with a receipt matching the request. New version 2 policies bind
`timeoutSeconds: null`; an explicitly admitted internal experiment can bind a
finite positive deadline instead. There is no action/UI budget option. The host
does not impose a separate default probe-process deadline. Explicit cancellation
still closes the owned process and preserves the uncertain, non-replaying attempt.
Close-only cleanup bounds are separate from the healthy completion lifetime.

The current bounded completion capability (`completion:single_attempt:v2`) is
implemented by the updated standard OpenAI provider using a fresh client.
Older provider versions, other providers and unsupported endpoints cannot pass
this check: they fail before a completion request is sent. Update the destination
provider or use a supported provider before starting a new transfer. Historical
version 1 policies and signed checks retain their originally admitted 45-second
deadline and v1 provider contract; an already-admitted activation reuses that
exact policy. They are not silently upgraded to the new default. Existing
legacy readiness records without policies and unknown policy versions are never
automatically upgraded or given a new probe budget; retain them for operator
investigation. A transfer already recorded as `active` can still finish native
fence clearing without a provider request.

## Cancel, retry and uncertain outcomes

Before source release begins, **Cancel unreleased export** requires a recorded
reason and the current receipt revision. It restores source admission without
sending input. Copy its `.cancel.json` receipt to the destination and choose
**Retain source cancellation**. The destination stage becomes `discarded`; its
files and native fence remain as evidence. It can never activate from that stage.
Cancellation and release are mutually exclusive signed decisions.

Restart marks unfinished preparation, staging, release or activation `unknown`.
Unknown does not mean failed or safe to repeat. Both hosts retain evidence and
execution stays blocked. Inspect both receipts before any further action:

- Repeating the same command/package returns its receipt; it does not repeat
  provider checks, task inputs or uncertain effects.
- Destination activation records its attempt before the provider check. A failed
  or interrupted check leaves an `unknown` receipt. Repeating that exact signed
  release and original ready revision returns the saved uncertainty without
  making another provider request.
- An interrupted source release may be explicitly continued with the current
  revision and exact readiness receipt. It confirms the permanent native fence
  and verifies each archived original before issuing the release certificate.
- If activation committed `active` but final native-fence clearing failed, repeat
  activation with the same signed release. It only finishes fence clearing; it
  does not reinstall history or rerun the provider request.
- Other `unknown` activation states require operator investigation. There is no
  general automatic rollback, destructive cleanup or replay repair command.
- Losing an acknowledgement never restores source ownership after release.
  Preserve both hosts' private portability directories for recovery.

## Shared action contract

All mutating calls use ordinary authenticated Unified action admission. Source
agent calls are scoped to their calling task. Receipt updates use
`expectedRevision`; export additionally binds `expectedExecutionRevision` and the
Git `sourceRevision` from `worktree.inspect`. Keep the dispatch command ID stable
when retrying an uncertain export response.

| Action | Required arguments | Result |
| --- | --- | --- |
| `portability.inspect` | Optional `sessionId` | Public host identity, peers and receipts |
| `portability.export` | `sessionId`, `destination`, `sourceRevision`, `expectedExecutionRevision`, `mode` (`clean` or `carry_dirty`), `reviewedContent: true` | Pending source receipt; later package path |
| `portability.stage` | `path`, `repository` | Destination readiness receipt/file |
| `portability.release` | `sessionId`, `id`, `expectedRevision`, `path` to ready receipt | Permanent source release/file |
| `portability.activate` | `sessionId`, `id`, `expectedRevision`, `path` to release | Idle destination owner |
| `portability.cancel` | `sessionId`, `id`, `expectedRevision`, `evidence` | Source cancellation/file |
| `portability.discard` | `sessionId`, `id`, `expectedRevision`, `path` to cancellation | Retained inactive destination stage |
| `portability.evidence` | `sessionId`, `id`, `section`; optional `offset`, `limit` ≤ 4000 | Bounded historical JSON text, never execution |

Evidence sections are `operations`, `operationRequests`, `liveJobs`, `workers`,
`approvals`, `questions`, `schedules` and `scheduleRuns`. Operation request
idempotence receipts are restored: retrying an unknown request returns that
saved uncertainty rather than resubmitting its effect. Schedules, worker
processes, approval tokens and native live jobs are not installed as live work.
After writer quiescence, export waits up to 30 seconds for pending operation
journal writes. Failure or timeout leaves the transfer fenced and `unknown`.
Hash-only output-event records retain their sequence, event identity and digest;
the bounded output archive retains the original chunks. An unsettled operation
admission receipt remains `outcome_unknown` on import and cannot replay its work.

## Boundaries and implementation

`amplifier_portability` owns signed protocol records and bounded Git packages.
`amplifier_web.portability` supplies host admission and transport-file actions;
`portability_data` projects supported task data. Foundation owns the native writer
fence shared by supported consumers. There is no second execution scheduler.

Dirty transfer includes staged and unstaged binary-capable patches plus regular
untracked files, with a 20 MiB aggregate change bound and 2,000 changed paths.
Task/history/output evidence is bounded at 32 MiB; encoded packages at 64 MiB.
Ignored files, submodules, symlinks, conflicts, special index flags, Git replacement
objects and partial/promisor repositories are rejected or excluded. Git filters
and hooks are disabled. No Git objects, remotes, source repository configuration
or automatic network fetch travel in the package; the destination must already
have the exact commit.

Dirty/untracked local configuration and credential paths, including `.amplifier`
at any depth, are rejected. Native history is an explicit file allowlist; raw
configuration, keys, module caches and provider process state are excluded.
Content and decoded structured history are scanned for recognizable credentials.
This is a bounded rejection filter, not proof that arbitrary prose contains no
secret; review is required. A rejected history is preserved, never silently
redacted or rewritten to make export succeed.

Transcript bytes and output identities remain unchanged. Operational native
metadata gets the destination working directory plus origin provenance. Saved
absolute references remain provenance; arbitrary external paths, local draft
attachments and external resources are not automatically rewritten or fetched.
Missing referenced stored resources block transfer. Prior destination-local
session settings are archived on a return hop so they cannot override imported
configuration intent. Both sides retain original evidence.

Active, connecting and closing voice calls block export. Voice events and
delayed output or conversation writes recheck ownership when they commit. Events
from a call or request started before a transfer remain stale after cancellation
or a return hop; a deliberate new call or request can use the current owner.
Import verifies existing stored resource bytes before publishing task data and
rejects conflicting output receipts, including matching request fingerprints
with a different saved result. Imported questions refresh the session projection.

Tasks with **any retained managed publishing state** cannot be exported. This
includes stopped/removed sites, immutable releases, previews, receipts and remote
target bindings: their management, rollback and audit ownership still belongs to
the source. Export checks the publishing ownership API under its shared lock and
does not stop, remove or migrate a publication. Continue managing it from the
source task. Transfer of publishing controls needs a separate ownership contract.

## Validation scope

The tests cover signatures, wrong hosts, stale revisions, replay, state changes,
dirty Git restoration, forbidden paths/content, native consumer fencing,
destination credential rejection, source archival interruption and final
activation-clear retry. The local process acceptance runs separate AppService
processes and native/data homes, calls the real standalone provider probe against
a deterministic local provider, moves both directions with restarts, and checks
history, outputs, receipts and no unknown-effect replay.

That evidence proves a local two-process roundtrip on one Mac. It does not prove
Spark/Linux deployment, SSH transport, a real external provider account, live
in-flight task handoff, full resumed bundle/tool behavior, physical voice audio or
unqualified filesystems. Live Mac↔Spark acceptance belongs to the release owner
after coordinating the host consumers and destination accounts.
