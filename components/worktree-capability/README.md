# Unified worktree capability

This adapter owns selected-session indexes and durable effect receipts. It uses
the separately installed `amplifier-worktrees` Python library for Git operations
and the host's public relocation and directory-guard APIs for session authority.
It does not import AppService, native runtimes, sibling source trees, or clients.

```js
import {createWorktreeCapability} from '@amplifier/unified-worktree-capability';
const owner=createWorktreeCapability({
  directory:'/private/owned/worktrees', python:'/owned/venv/bin/python',
  inspectSession, relocateSession, directoryInUse, withDirectoryGuard,
  readUserMessage, onChanged
});
```

The Python interpreter must contain the public `amplifier-worktrees>=0.1,<0.2`
package. The Node package includes its small one-shot worker; no permanently
running Git process or native session is needed to display a checkout page.
Install the library from its published package or an independently built wheel.
Do not add a sibling checkout to `PYTHONPATH`.

## Public host contract

- `inspectSession(uri,{clientId?})` authenticates the session and returns
  `{session,historyHome,workingDirectory,executionDirectory,executionRevision,
  configurationBusy?}`. `historyHome` is the original canonical workspace, not
  a private transcript directory. `workingDirectory` is a compatibility fallback.
- `relocateSession(uri,{commandId,target,expectedExecutionRevision,reconciles?,
  evidence?})` owns idle admission, native writer release, durable location CAS,
  history identity, and configuration fences. Success is
  `{applied:true,executionDirectory,executionRevision,receipt}`. Preflight refusal
  is `{applied:false,executed:false,reason}`. An error after admission carries a
  durable receipt and remains unknown. `reconciles` invokes evidence inspection,
  never replay of the old relocation.
- `directoryInUse(path)` returns `{inUse,sessions:[{session,reason}],pending}`.
  `withDirectoryGuard(path,async remove=>...)` checks indexed history/execution and
  unresolved transition references and excludes overlapping host admissions
  until the callback finishes. The callback performs Git cleanup only. If this
  authority is absent, cleanup fails closed.
- `readUserMessage(uri,id)` returns actual durable input provenance. Agent
  reconciliation requires a newer user/voice message, excluding question replies
  and scheduled inputs. An authenticated UI may explicitly provide its own finding.
- `onChanged('worktrees',uri)` invalidates only this topic and session.

The host must authorize the adapter's managed checkout directory as an execution
root. This does not change catalog visibility or migrate history to that root.
One owner process has exclusive responsibility for one state directory. Call
`close()` on orderly shutdown; it awaits admitted work and never retries it.

## Wire and storage

The manifest advertises the original eight `worktree.*` operations and an
evidence-only `worktree.resolve` operation through the
versioned capability contract. Exported `actionSchemas` support client and agent
discovery. Ordinary editor text, source-ref choices, inspection results, paging,
and reconciliation drafts stay on the client. Only explicit shared actions cross
the protocol boundary.

The `worktrees` topic contains one selected-session map with at most 25 checkout
summaries, 25 handoffs, and 25 other receipts. `worktree.list` has collection-scoped
keyset cursors and a maximum of 50 items. No read walks all Git records or sessions.
Named `amplifier-worktree://records/<uuid>?session=<uri>&offset=<n>` resources expose
the original manifest in pages of 100 files. Git source status and inspection
lists are bounded, with counts/truncation explicit. Source evidence remains in the
library's private durable storage. No full manifest appears in the topic.

SQLite indexes commands by session, status, and reservations. Receipts are saved
before effects. Restart changes pending receipts to unknown in one indexed SQL
update. A duplicate command returns its original known/unknown receipt without
rerunning Git or native work. Failed cleanup never invokes `--force`. Unknown
cleanup is not automatically repeated. Explicit follow-on work requires inspection.
An uncertain Git command retains its deterministic library record ID before
dispatch. `worktree.status` can inspect that exact command's record even when its
acknowledgement was lost, without scanning storage or recreating the checkout.
`worktree.resolve` records an explicit user finding: completion additionally
requires matching durable library evidence; abandonment preserves all files and
evidence. Neither outcome runs Git effects. Unknown handoffs instead use the
host's native-location reconciliation contract.

The Python worker accepts one allowlisted 64 KiB request, returns at most 1 MiB,
has a 6-minute response deadline, and uses fixed Git argv through the library. Concurrent
read workers are capped at eight; queued mutations are capped at 32. The owner has no workspace/history watchers, historical record scan, polling
loop, or warm runtime. During a quiescence attempt blocked by an orphan worker,
one temporary watcher on its private lifetime journal provides an advisory
completion wakeup; that signal never substitutes for a held lease.

## Qualification

```sh
uv venv .venv
uv pip install --python .venv/bin/python /release/amplifier_worktrees-0.1.0-py3-none-any.whl
npm test
```

Tests use real temporary Git repositories and an installed wheel. They preserve
staged/unstaged/untracked source data, verify clean removal and history-home
retention, fence unknown outcomes, enforce scope and provenance, and query an
indexed 25,000-record fixture. Native relocation itself is qualified by the host
and native adapter; the owner suite labels its host callbacks as fixtures.


## Held quiescence and process ownership

The factory accepts `onMayBeIdle()` and returns `quiescenceParticipant(ownerId =
'worktrees')`, `inspectQuiescence()`, and `quiescenceAccess`. Only `worktree.list`
is declared a read-only action: `worktree.status` refreshes shared projection
records, and explicit repository inspection can acquire Git's own locks. Selected
metadata topics, command/handoff lists, and immutable saved manifest pages remain
readable during held/unknown intake. A custom injected Git worker must explicitly
provide the same `quiescenceCoverage:1` lifetime contract; otherwise the participant
refuses with `custom-git-worker-lifetime-unverified`.

A private SQLite DELETE-journal lifetime exclusive lease excludes a second Node
owner before opening/migrating any journal. The intake fence is durable, becomes
unknown on restart, and closes complete async actions/queued mutations. Detached
handoff jobs retain their own active admission until native relocation actually
settles, even after the action returned a pending receipt. Closing awaits admitted
actions/jobs and actual worker exit. No unknown operation is replayed.

Git workers have a second cross-process lease and an indexed lifetime reservation
made before spawn. Each actual Python worker holds a shared SQLite lease before
checking intake and invoking the public Git library. Quiescence holds the exclusive
side, so a worker cannot slip in after admission closes. A dead Node parent does
not make its existing Python worker idle: that process records settlement only
when the synchronous library call has joined its Git subprocesses. A response
timeout/oversized frame reports unknown but retains the bounded live child slot
and drains output; it never kills work just to permit an update. A proven spawn
failure is known no-effect and clears only its exact lifetime reservation.

Unexpected worker death leaves a separate unresolved lifetime reservation. The
owner cannot prove a killed worker's descendants completed, so automatic
quiescence refuses until an authoritative external process reconciliation exists.
This compatibility limit is deliberately distinct from old unknown command
receipts: ordinary lost acknowledgements with a settled worker lifetime do not
block later quiescence. No generic PID polling or stale-file deletion claims
absence of work.

The participant uses `{fenceId,commandId,purpose,instanceId,dataScope}` (purpose
`recovery` or `distribution-update`). Held lease release and `reconcileRelease`
require exact fence/owner identity and the host's trusted proof
`{verified:true,fenceId,commandId,outcome,instanceId,dataScope,receiptId}`.
`unchanged` binds the prior host instance; `ready` binds a verified replacement.
Unknown retains intake. A still-held lease may release after definitive aggregate
admission refusal using `{kind:'admission-refused'}` plus `unchanged`.

Tests exercise real Git, installed public wheels, parent SIGKILL with an existing
worker, worker death uncertainty, retained durable fences, detached native handoff
fixtures, and read availability. They do not claim production supervisor or
cross-host recovery acceptance.

Release retry proof is immutable: the owner atomically retains the canonical
outcome and entire proof with its released receipt. After acknowledgement loss
or restart, only the identical context, outcome and proof are accepted; changing
the receipt identity, authority scope or outcome is rejected. JSON object key
order is irrelevant. Older released receipts without exact proof evidence are
not inferred or rewritten into verified release evidence.

## Persistent service fences

The trusted quiescence participant advertises `serviceStop: {version: 1}` only
for the retained service-stop contract. Acquisition copies the complete
`serviceIdentity` (`installationId`, `dataScope`, `ownerId`, `instanceId`,
`releaseDigest`) into its existing durable intake fence. Restarting does not
open intake or replay admitted work. Python owners require the installed
Foundation `DurableIntakeFence.SERVICE_STOP_VERSION` marker.

Release requires the host-authenticated `kind: 'service-lifecycle'` proof bound
to the exact fence, command, original identity, and observed instance. A resumed
service needs distinct-instance exit/readiness receipts; a refused stop needs
the exact original-instance refusal receipt. The complete proof is retained.
Exact completed retries are passive, and changed proofs refuse even as the first
request after restart. Generic recovery/update proofs do not release this fence.

An `admission-refused` rollback is restricted to the newly acquired live lease.
It is unavailable through reconciliation or after an unknown outcome. Reads of
existing receipts stay available while intake is held. Platform authentication,
process ownership, stop/resume signaling, and aggregate coverage remain with the
host and supervisor; this owner does not infer them from a PID or missing socket.

The distribution's installed owner-service matrix covers fresh reopen, changed
proof, unknown rollback, and public bridge behavior on Node22/Python311+313.


## Held retention inspection

The actual quiescence participant advertises `retentionHide: {version: 1}`.
Acquire it with purpose `retention-hide` and the exact coordinator fence context.
The returned live lease exposes
`inspectRetentionReferences({sessions: ["ahp-session:/..."], limit: 101})`.
The family must contain 1–101 distinct explicit session URIs. The result is
`{coverage: "complete" | "partial", protected: [{session, reasons}], omissions}`.
Only the original held lease can inspect; release, uncertainty or replacement
invalidates that authority. Release reconciliation requires the same authenticated
coordinator proof and never retries effects. A pre-effect admission rollback is
allowed only on the original live lease. The purpose grants no mutation rights.

Inspection reads bounded indexed owner metadata while intake remains closed.
It never starts a native worker, scans canonical histories, deletes product records,
or interprets absence of a runtime as absence of deferred work. `complete` describes
this owner's reference coverage, not permission to hide or delete a conversation.
The coordinator must hold every configured owner, check all results, and use the
native history owner's separate preservation-first hide boundary.

Active work blocks acquisition. Retained uncertain owner commands protect their explicitly associated conversations; unscoped uncertainty cannot produce a false absence proof.

## Managed-file disposal protection

`quiescenceParticipant.managedFiles = {version: 1, preservesCanonical: true}` is a separate contract from `retentionHide`. It holds the real owner intake with purpose `managed-files-disposal`, preserving every owner record. The acquired lease exposes:

```ts
inspectManagedFilesReferences({
  sessions: [rootSession, ...descendants], // explicit AHP URIs, at most 101
  limit: 101,
  allocation: {allocationId, executionDirectory, allocationHash, treeHash, entryCount, bytes}
}) // {coverage: 'complete' | 'partial', protected: [{session, reasons}], omissions: [...] }
```

The allocation comes from the trusted host's native-reviewed managed allocation, never a browser path. It is bound with the exact selected family on the first inspection of the live lease; a different review is refused. This read does not grant file deletion. The coordinator must bind release to its exact durable managed-files effect receipt, keep unknown fences held, and refuse partial/protected coverage. Lease inspectors expire before release begins, including unknown release; admission-refused rollback is available only on the original live lease. Restart reconciliation requires the existing exact verified release proof.

Unsettled commands and every selected retained Git/worktree record protect files. Indexed overlapping source/target paths protect files even for another conversation. This is intentionally conservative until an explicit worktree-reference detachment contract exists.

Inspection uses bounded selected metadata or indexed overlap probes, not native transcripts, a global history projection, or worker/model startup. Existing retention-hide and service-stop lifecycles are unchanged. The package tests cover the independently installed owner transport and held/unknown/exact-release behavior; full host/native/browser disposal acceptance remains a composition responsibility.

### SQLite startup authority

After acquiring the existing owner lease, startup inspects existing authority databases read-only before opening any authority database for writes. It checks fixed schema metadata and, for history, the bounded revision singleton. Missing required tables, incompatible required columns, and unsupported schema versions refuse startup. The original main database and existing WAL/journal bytes remain available for authoritative recovery. SQLite may create its own shared-memory or empty WAL sidecar during read-only inspection; startup does not copy the database or scan historical rows.

A new database requires its main file and all WAL, SHM, and journal sidecars to be absent. Even an empty orphaned sidecar refuses initialization. Existing valid unversioned profiles upgrade to schema marker 1. This check protects authority structure; it does not certify every stored row or reconstruct deleted authority.

The original worktree profile (ee651d9) included records, commands, and topics. Quiescence and worker registries arrived together (195bd7e). A complete unversioned original profile may introduce both authorities; a missing sibling refuses. Schema marker 1 requires both. The later managed-command indexes also identify an unversioned modern profile, which requires both authorities. Loss of both auxiliary databases and all later-profile evidence cannot be distinguished from the original layout using schema metadata alone.

### Exact abort of an incomplete update admission

The trusted host participant has `abortAdmission(context)` for
`purpose: "distribution-update"` only. Context binds the original `commandId`,
`fenceId`, `instanceId`, and `dataScope`; its exact proof is
`{kind:"distribution-admission-abort", verified:true, purpose:"distribution-update",
commandId,fenceId,instanceId,dataScope,receiptId}`. The host must authenticate this
proof through its supervisor verifier before calling the internal port. There
is no browser or agent action that accepts a caller's `verified` assertion.

The owner records its attempted acquisition, actual acquisition or known busy
refusal in its own fence database. A busy refusal is final for that exact fence:
repeated acquisition returns the original refusal even after work finishes or
the owner restarts. A new attempt requires a new fence. Abort requires that
original evidence and
returns `{ownerId,fenceId,commandId,instanceId,dataScope,status,receiptId}` with
`status` equal to `released` or `not-acquired`. The exact proof and result are
durable in the same transaction that removes a matching hold. Identical retries
return the retained result without reacquisition or business-command replay;
changed identity/proof refuses. An already completed abort receipt describes
that original attempt, not a new claim that all current work is idle.

Active callbacks or pending business work block a new abort settlement. Existing
unknown business commands remain untouched and are not inferred to have failed
before effects. Ordinary release proofs and absence of a current hold are not
abort authority. Schema version 2 adds the admission journal atomically after
read-only validation of the existing fence/receipt schema under the original
OS lease. Valid legacy version 0/1 databases migrate without manufacturing old
attempt records: a legacy hold or missing attempt remains unavailable for this
new recovery path. Missing version 2 authority refuses before writable recovery.
The small local helper is packaged with this standalone owner; no dependency on
the distribution updater is introduced.

Worktree acquisition records an intermediate attempt before taking the separate
worker OS lock; an interrupted intermediate attempt is not confirmed acquired
or not-acquired. A restarted abort must regain that worker exclusion and refuses
pending worker reservations or unverified custom-worker lifetime coverage.
Receipt persistence precedes worker-lock release. No Git process is started to
settle admission.
