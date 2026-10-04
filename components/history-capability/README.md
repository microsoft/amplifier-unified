# Amplifier Unified history import capability

This optional product owner stages one explicitly selected JSON/JSONL file, asks
for review, commits inert native history, and registers the independently verified
new session. The original bytes and native creation receipts belong to the
configured native ACP owner. This package stores only bounded workflow metadata,
argument hashes and exact receipt results. It imports no Core/Foundation runtime,
never starts a worker, and never reads or rewrites transcripts directly.

## Composition

```js
const history = createHistoryCapability({
  directory: '/private/distribution/history-import', engineId: 'amplifier',
  authorizeWorkspace: (path, caller) => host.authorizeWorkspace(path),
  nativeAdmin: (operation, args, context) =>
    admin.importHistory(operation, args, context.workingDirectory),
  adoptImportedSession: input => host.adoptImportedSession(input),
  importAdoptionReceipt: (id, options) => host.importAdoptionReceipt(id, options),
  onInvalidate, onMayBeIdle,
});
```

Merge the manifest/read/action/schema ports through the authenticated distribution.
Register `history.quiescenceParticipant` for this topic, **and** retain the existing
shared AdminConnection participant for its native home. The product lease closes
its full request lifetime (chunk write, native callback, host adoption and receipt
reconciliation); the admin participant separately proves native writer quiescence.
Do not manufacture an idle participant. Configure launcher `adminHistoryImport:true`
and explicit `adminWorkspaceRoots`. Generic ACP agents without the extension
refuse. Workspace authority is independently canonicalized by the public host and
revalidated by native administration. Neither client args nor an imported native
ID grants authority.

## Browser and agent contract

Topic `history-import`, URI `amplifier-capability://history/import`, scope `host`,
version1. `data.historyImport` advertises configured engine, fixed limits and
preservation semantics; `nativeCapabilityRequired:true` is not a readiness or
account claim. New actions use ordinary negotiated `x-amplifier/capabilityAction`.
The top-level commandId is the exact durable identity for each mutation.

| Operation | Args | Result inside product receipt |
| --- | --- | --- |
| history.import.begin | workingDirectory, format json/jsonl, bytes1..1048576, sha256, optional title/bundle | workflowId, uploadId, receivedBytes0, reserved session hint |
| history.import.chunk | workflowId, offset, contentBase64 <=16384 raw bytes | workflowId, receivedBytes |
| history.import.preview | workflowId | previewHash, sourceSha256, bounded native summary (counts, interrupted calls, title, bundle, omissions) |
| history.import.commit | workflowId, previewHash | session (authoritative AHP URI), nativeSessionId, sourceSha256, historyRevision, creationConfirmed, workReplayed:false |
| history.import.receipt | commandId | exact product status and optional observed nativeStatus/requiresReconciliation |
| history.import.reconcile | commandId | original command receipt after explicit native-proof/mapping reconciliation |

Product receipts are `{commandId,operation,status,workflowId,result?,replayed:false}`
with status `succeeded`, `failed`, `dispatching` or `unknown`. `accepted:true` means
succeeded, not merely sent. Unknown commands remain unknown without evidence; they
block another mutation in that workflow. Exact duplicate command IDs return stored
results and never repeat native work. Changed arguments are refused. Clients keep
only workflow/command identity and hashes in recovery storage, not transcript bytes.
A fresh selection/upload is explicit; no lost-reply automatic effect retry exists.

`receipt` is passive and remains available during a maintenance fence. It does not
change workflows or register sessions. If native evidence is conclusive but product
acknowledgement/mapping is missing it reports `requiresReconciliation:true`.
`reconcile` is an explicit, fenced metadata operation: it invokes only native
**receipt**, then verified host adoption/registration repair. It never invokes the
old begin/chunk/preview/commit. Native Foundation writer proof remains authoritative.

The original upload is retained natively. Imports are new identities and inert:
old prompts, approvals, tasks and tool work never run. Imported user rows carry
unverified provenance and do not become user consent. The optional bundle identity
is preserved/reviewed but is not mounted or account-validated during import.
Cleanup, archive-age policies and legacy presentation-file purge are separate
unsupported authorities here; no directory/history deletion is provided.

## Bounds and lifecycle

At most16 requests,32KiB action args,16KiB decoded chunk,1MiB original and32KiB native
receipt. No global history/catalog scan, transcript cache or file-upload frame
expansion. The owner acquires a SQLite OS lifetime lease before opening its
WAL/FULL metadata journals. SIGKILL releases that OS lease only; unresolved commands
and held intake fences survive. The owner refuses a second process. Release requires
exact authenticated context/outcome/proof; changed-proof retries fail. Historical
unknown receipts retain evidence but do not alone imply a live background process.

The native import bytes are outside selected native-file archive coverage today;
a complete product backup must include the configured native import store. This
owner does not claim otherwise or copy those bytes into its own journal.

## Qualification

`npm test` runs bounded flow/privacy, concurrent lifetime, lost-reply/restart,
exact release, workspace/identity refusal, and actual SIGKILL/OS-lease fixtures.
`test/native.integration.mjs` uses explicit installed public host/catalog/native
bridge paths and a native launcher whose workerCommand cannot run. It proves
Foundation-written history can be passively selected without starting native work.
No test sends a real account request or changes a live host. Browser acceptance
belongs to the independent web client lane.

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

An unresolved import whose final session association is not proved returns partial owner coverage (`history-import-unsettled`). No native receipt is replayed or queried as a substitute for owner certainty.

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

Unsettled imports produce partial owner-wide coverage. Successful inputs and creation evidence belong to the native import owner; this product owner does not dereference execution-directory files from completed imports.

Inspection uses bounded selected metadata or indexed overlap probes, not native transcripts, a global history projection, or worker/model startup. Existing retention-hide and service-stop lifecycles are unchanged. The package tests cover the independently installed owner transport and held/unknown/exact-release behavior; full host/native/browser disposal acceptance remains a composition responsibility.

### SQLite startup authority

After acquiring the existing owner lease, startup inspects existing authority databases read-only before opening any authority database for writes. It checks fixed schema metadata and, for history, the bounded revision singleton. Missing required tables, incompatible required columns, and unsupported schema versions refuse startup. The original main database and existing WAL/journal bytes remain available for authoritative recovery. SQLite may create its own shared-memory or empty WAL sidecar during read-only inspection; startup does not copy the database or scan historical rows.

A new database requires its main file and all WAL, SHM, and journal sidecars to be absent. Even an empty orphaned sidecar refuses initialization. Existing valid unversioned profiles upgrade to schema marker 1. This check protects authority structure; it does not certify every stored row or reconstruct deleted authority.

History's original profile (75db103) already included workflows, commands, revision, and the separate fence/receipt database. A missing gate alongside an existing history database is never a migration.

### Exact abort of an incomplete update admission

The trusted host participant has `abortAdmission(context)` for
`purpose: "distribution-update"` only. Context binds the original `commandId`,
`fenceId`, `instanceId`, and `dataScope`; its exact proof is
`{kind:"distribution-admission-abort", verified:true, purpose:"distribution-update",
commandId,fenceId,instanceId,dataScope,receiptId}`. The host must authenticate this
proof through its supervisor verifier before calling the internal port. There
is no browser or agent action that accepts a caller's `verified` assertion.

The owner records its attempted acquisition, actual acquisition or known busy
refusal in its own fence database. Abort requires that original evidence and
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
