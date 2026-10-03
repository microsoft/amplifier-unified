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
