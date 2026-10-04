# Optional Amplifier Unified diagnostics owner

This component indexes explicit live observations in its private SQLite store.
It does not receive application state, enumerate projects/sessions, scan native
events, resolve model credentials, mount modules or read private client drafts.
Capture defaults off. A configured distribution may omit this owner entirely.

The Node package owns protocol routing and its bounded subprocess connection;
the independently installed Python package owns policy, records, exact command
receipts, Context Intelligence delivery and durable intake fences. The public
`createDiagnosticsCapability` factory accepts a trusted `owner` launcher and
invalidation/idle callbacks. `observe` receives one explicit observation;
`nativeEvent` admits only selected metadata and ignores streaming deltas. No
callback may enrich an observation by opening a canonical history file.

## Client contract

The host-scoped `diagnostics` topic has URI
`amplifier-capability://diagnostics/settings`. Its `data.diagnostics` value contains
the shared revision, configuration, stream choices, bounded destination summaries
and local counters. Record bodies are absent until explicitly queried.

- `diagnostics.get {}` reads this bounded state.
- `diagnostics.configure {expectedRevision,config}` saves the complete policy with
  compare-and-swap. Its exact outer command ID identifies the durable receipt.
- `diagnostics.records {sessionId?,stream?,before?,limit?}` returns at most 50
  selected records and about 24 KiB. Filters precede keyset pagination. Agent
  queries require their own authenticated conversation.
- `diagnostics.environment {name}` returns availability, never a value.
- `diagnostics.test {id}` authenticates and sends one synthetic event to that saved
  destination. Server acceptance does not establish graph indexing.
- `diagnostics.retry {id}` retries only known rejected deliveries, preserving the
  original envelope and idempotency key. Unknown deliveries remain unknown.
- `diagnostics.receipt {commandId}` recovers the original result without another
  configuration write, probe or delivery.

Draft policy and destination editors, selected filters, expanded details and page
exports belong to the client. A configuration change does not route old records.
Changing a destination cancels its unsent prior queue; an already issued request
can remain uncertain. Explicit conversation-content capture remains sensitive even
after best-effort redaction. Authentication names are stored, credential values are
resolved only for a selected send by the current Context Intelligence client SDK.
Static environment credentials and Entra use that SDK's independent auth paths.

## Bounds and recovery

The Node admission window is 32 observations; private protocol frames are 128 KiB,
with at most 64 outstanding requests. The Python record batch accepts at most 32
observations, each stored projection at most 20 KiB. Policy bounds retained records
at 100–100,000 and age at 1–365 days. Retention reductions delete at most 1,000 old
records per maintenance pass, progressing with later observations/configuration;
this is not an immediate physical-file-size guarantee. No periodic session scan
or UI-driven backend poll runs. Ten destinations and 2,000 unresolved envelopes per
destination bound outboxes. Four sends can run concurrently, each with a 15-second
deadline. Normal sends are attempted once. The SDK refuses redirects and validates
the acceptance receipt; response bodies and exception text are never published.

Quiescence closes new observations, pauses unsent delivery, waits for issued sends
and uses the Foundation operations library's durable fence. Read-only settings,
records, environment availability and receipts remain accessible while held.
Release must match the exact recorded proof, including after process replacement.
An interrupted dispatch/probe becomes unknown and is never replayed on restart.
Exclusive process ownership protects each store. Durable command receipts are
retained separately from the configured diagnostic record retention policy.

## Explicit limits

This owner implements live metadata and explicitly supplied content; it does not
claim to import old diagnostic records or reproduce the native hook's full
provider-request capture. The advertised `providerRequests` field accepts false
only. Existing native capture logs remain under their original owner. Direct Entra
account acceptance, deployed-worker use and the complete product event mapping are
separate qualifications. Source tests and a local SDK HTTP fixture do not establish
real-account forwarding. Full-product backup needs this owner's explicit snapshot
contract; selected native archives do not include this database implicitly.


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

Pending, failed and unknown delivery records protect selected conversations. Settled records are copied metadata/text in the owner store; their workspace labels do not cause later reads of execution-directory files.

Inspection uses bounded selected metadata or indexed overlap probes, not native transcripts, a global history projection, or worker/model startup. Existing retention-hide and service-stop lifecycles are unchanged. The package tests cover the independently installed owner transport and held/unknown/exact-release behavior; full host/native/browser disposal acceptance remains a composition responsibility.

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

A Node-local busy admission persists its original refusal through private
`quiescence/refuseAdmission` before returning `null`. The Python gate records
that exact distribution acquisition attempt with pending work; a later retry
cannot turn the original refusal into acquisition. Other purposes retain their
existing behavior.
