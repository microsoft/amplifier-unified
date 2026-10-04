# Unified publishing capability

Product composition over the independent `amplifier-publishing` package. No
AppService, global state object, native engine or sibling source import is used.
The Python owner retains the existing target selection, exact RPC proof and
immutable capture contracts. The TypeScript provider speaks a private bounded
stdio protocol and exports the normal Unified capability interface.

```ts
const publishing = createPublishingCapabilities({
  owner: {command: '/owned/venv/bin/amplifier-unified-publishing',
          args: ['--config', '/owned/publishing.json']},
  inspectSession: (uri, context) => host.inspectSession(uri, context),
  withSessionWorkspace: (uri, expected, callback) =>
    host.withSessionWorkspace(uri, expected, callback),
  // Optional genuine human permission flow for agent-origin deploy/rollback:
  authorizePublication: async request => ({approved: true, approvalId: exactApproval}),
  onInvalidate: (topic, session) => invalidate(topic, session),
});
```

The approval example assumes `exactApproval` comes from the application's real
permission system for the supplied exact request. Omitting the callback refuses
agent deploy/rollback. Direct authenticated UI deploy/rollback is explicit
approval. A release review records the user's note; it never fabricates visual
inspection or publication approval. Approval evidence is durably bound to the
operation and exact arguments before effects; changed retries conflict.

Python configuration is `{"dataDir":"/owned/publishing"}`. One owner process
holds an exclusive lease; its SQLite admission database uses WAL/FULL commits.
No model, source builder, installation, public network route, authentication or
TLS service is created. Preview/deploy listeners retain the library's separate
origin and actual loopback/private access policy. SSH remains the account's
existing explicit configuration; the owner does not store SSH credentials.

## Public provider contract

Exports `createPublishingCapabilities`, `PublishingCapabilities`, `OwnerConnection`,
`Options`, `Launcher`, and `Context`. Methods are `manifest`, `actionSchemas()`,
`read`, `action`, `close`. Schema loading starts only the lightweight owner; it
does not instantiate Publisher, discover projects or connect to a remote service.
`read` is scoped to `amplifier-capability://publishing/publishing` and returns
`{topic:'publishing',scope:<AHP session URI>,revision:<number>,data:{publishing:{[uri]:snapshot}}}`.
The small first snapshot has ten rows per collection. Mutations return their
original exact result and invalidate the selected topic. They do not append a
second remote read that could turn a confirmed mutation into a misleading error.

Trusted `inspectSession` supplies current `executionDirectory`. For build, the
Node provider holds `withSessionWorkspace` while the owner captures existing
static output and optionally transfers its immutable bytes. The host mapping
cannot relocate or admit a turn during that callback; independent sessions keep
running. External filesystem writers are not frozen. Source paths remain relative,
inside the authoritative workspace and free of symlink traversal. The library
also verifies no-follow reads, size bounds, immutable hashes and manifest integrity.
Exact retries use their saved original source/target even after workspace changes.

All original names and argument contracts remain:

- `publishing.list`, `status`, `logs`, `build`, `preview`, `review`, `deploy`,
  `rollback`, `stop`, `remove`.
- `publishing.target.list`, `save`, `inspect`, `select`, `remove`.

Build returns a full immutable release. Other lifecycle mutations return the
original receipt shape with `state: running|unknown|succeeded|failed`.
Remote mutations require the inspected `targetRevision` and `serviceId`; selecting
or editing another target cannot retarget an admitted request. AHP URIs are mapped
durably to opaque plain IDs for the library and remote protocol and projected
back only in `sessionId` fields. Existing legacy session IDs require an explicit
migration binding; this owner never guesses or scans the former application DB.

Bounded read additions:

- `publishing.list {collection?:'sites'|'releases'|'receipts',limit?:1..100,cursor?,siteId?,targetId?}`.
  Without collection: original `{sites,releases,receipts,target,targets,...}` plus
  `pages:{sites:{nextCursor},releases:{nextCursor},receipts:{nextCursor}}`.
  With collection: `{items,nextCursor,collection,target}`.
- `publishing.logs {siteId,limit?,cursor?,targetId?}` retains `items` and adds cursor.
- `publishing.release {releaseId,targetId?}` returns the complete selected manifest.
- `publishing.receipt {requestId,targetId?}` returns exactly one receipt or null,
  retaining unknown admissions and reconciling remote proof without repeating work.

Pages use stable lexical identifier order, not chronological offsets. Cursors are
query scoped; old cursors cannot select another session or collection. Release
metadata pages use `fileCount`; `files` are hydrated only on selected release read.
Receipts omit embedded manifests in page projections. Target configuration lists
remain complete and bounded to 64 retained configurations per session. Capture
store handles are capped at four; old capture directories remain durable on disk.
Unknown remote outcomes reconcile only exact RPC payload digest plus service ID;
loss or malformed evidence never triggers mutation replay. Error data preserves
`code`, `receipt`, and HTTP-like status for the legacy UI's explicit retry controls.

## Qualification and remaining boundaries

`npm test` builds and runs Node → installed Python owner → actual filesystem and
loopback listener. `cd python && uv run pytest -q` exercises workspace rejection,
review/approval, deploy/update/rollback, exact retries after source removal,
unknown admission, pagination and real Unix-socket service transport with an
injected SSH transport boundary. The public publishing wheel is installed as a
consumer dependency, not imported from sibling source.

Real SSH account access, deployed site/browser acceptance, legacy publishing DB
migration and genuine host permission UI integration are separate gates. No live
site, Spark environment or external account was modified during qualification.
Old remote services without negotiated pagination version 1 explicitly refuse
bounded browsing; mutation and direct receipt contracts remain compatible.

Target configuration actions also journal their outer `commandId` before any
mutation or remote inspection. `publishing.command {commandId}` returns
`{commandId,sessionId,operation,state,result,error}` or null. States are
`running|unknown|succeeded|failed`. Exact retries use the saved result; changed
arguments or actor conflict. An unfinished command becomes unknown on restart
and is never repeated. The client must retain its exact outer command locally
and inspect this receipt after loss, rather than infer success from today's
registry. Lifecycle requests continue to use `publishing.receipt {requestId}`.

## Restart admission

`quiescenceParticipant(ownerId)` holds the Foundation operations durable intake ledger under the existing process lease. Queued requests, capture/upload worker threads and callbacks remain counted through actual settlement, including after a caller cancels its wait. Unknown fences survive restart and require exact authenticated release proof. `onMayBeIdle` is advisory and fires after a prior busy observation becomes idle.

A local publishing listener is live work even between requests. Restart admission refuses until the user explicitly stops its site (including previews); it does not stop or rebind a published URL on the user's behalf. Stored releases and remote hosted sites alone are passive historical state. Most browsing calls reconcile receipt or listener projections, so only `publishing.command` is classified as a passive action while fenced. The reusable Publisher exposes `inspect_lifetime()` without requiring product imports or historical scans.

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

Retained build/approval bindings are conservatively protected as `publication-requires-review`; this version does not infer terminal deployment state from a missing listener or contact a remote target during retention. This can prevent hiding an otherwise inactive published conversation.

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

Unsettled commands and retained selected builds/approvals protect files pending a publication-specific detachment review. An indexed overlapping build source protects files even when another conversation owns that build. No remote status request is used to guess that a source is disposable.

Inspection uses bounded selected metadata or indexed overlap probes, not native transcripts, a global history projection, or worker/model startup. Existing retention-hide and service-stop lifecycles are unchanged. The package tests cover the independently installed owner transport and held/unknown/exact-release behavior; full host/native/browser disposal acceptance remains a composition responsibility.


## Existing authority at startup

The embedded owner validates its admission database read-only under the existing
lifetime file lease before intake recovery or a writable admission open. Preflight
checks fixed table and referenced-column metadata for `scopes`, `builds`,
`approvals`, `commands`, `publishing_targets`, `publishing_target_selection`
and `publishing_target_requests`. All seven tables were present in the original
independent adapter profile. Missing authority refuses without a guessed migration,
new approval, repeated capture, target rebinding or reconstructed receipt.

The `scopes` inverse URI mapping is authority: held selected-family protection
joins depend on it. It is not a disposable index. Ordinary derived indexes and
existing indexed running-to-unknown recovery remain unchanged. Healthy original
local fallback and historical requests without newer RPC-digest fields stay
supported; startup does not invent those fields or newer proof.

Initialization creates authority only when its main database and all WAL, SHM
and rollback-journal paths are absent. Empty and dangling sidecars count as
surviving evidence; a dangling main is not a fresh path. Existing main and
sidecar paths must be regular non-symlink files before SQLite opens them; stable
FIFOs refuse without blocking. Refused startup closes
partial handles and releases its lease, preserving main and pre-existing nonempty
WAL contents. Read-only SQLite may update derived SHM or create empty coordination
sidecars.

This guard performs no database row scan, hash, copy or full-state reload during
normal preflight. It does not establish arbitrary schema/constraint validation,
general row-loss detection, or recovery after the main database and every sidecar
disappear. Library-owned publishing/intake stores retain their separate authority
and qualification boundaries.

## Pre-retirement distribution admission abort

The trusted Host participant forwards `abortAdmission(context_with_proof)` to the
private owner `quiescence.abortAdmission` route (slash form for workspace,
notifications and diagnostics). It negotiates `quiescence.admissionAbort.version=1`,
validates exact Host13 proof and typed owner receipt, and reports unsupported older
workers explicitly. The owner delegates to Foundation Operations' shared
`DurableIntakeFence` using its existing authority database, original acquisition or
refusal journal, and current active/background/pending accounting. No missing held
row, generic release proof or changed retry authorizes settlement. The private
`quiescence.admissionAbortReceipt` route observes an original completed receipt.

This is additive future-source support. Old deployed stageless/unrecorded attempts
remain unknown. Actual authenticated Updates proof, complete composition and live
adoption require independent qualification; this adapter does not reopen intake by
itself. Feedback's Python participant is only one subowner; its Node aggregate and
resources-upload participant require separate complete attempted-subowner receipts.
