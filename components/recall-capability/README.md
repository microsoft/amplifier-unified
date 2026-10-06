# Unified Recall capability

An independent product owner over the optional `amplifier-recall` Foundation library. It owns workspace consent, personalization, retention (50 versions), note limits (8,000 characters), model budgets, selected-source authority and background task lifecycle. No Core/Foundation/Unified engine package is imported. The owner uses host callbacks; it does not read native history paths itself.

```js
const recall = createRecallCapability({
  owner: {command: '/owned/venv/bin/amplifier-unified-recall',
          args: ['--config', '/owned/recall.json']},
  inspectSession: (...args) => host.inspectSession(...args),
  listRecallSources: (...args) => host.listRecallSources(...args),
  inspectRecallSource: (...args) => host.inspectRecallSource(...args),
  readRecallSource: (...args) => host.readRecallSource(...args),
  readUserMessage: (...args) => host.readUserMessage(...args),
  readSessionContext: (...args) => host.readSessionContext(...args),
  nativeControlExisting: (...args) => host.nativeControlExisting(...args),
  onInvalidate: (topic, session) => invalidate(topic, session),
});
```

Configuration is `{"dataDir":"/owned/recall"}`. Install the public Recall wheel and this owner wheel in an isolated environment. Use the installed launcher, or `python -I -m amplifier_unified_recall.server`; `-I` prevents an unrelated working directory or inherited PYTHONPATH from substituting another package. Never start two owners over one state directory: a process lease enforces this.

## Offline legacy memory import

`python -I -m amplifier_unified_recall.migrate --source /captured/recall.sqlite3
--sha256 <reviewed-snapshot-digest> --mapping /reviewed/mapping.json
--destination /owned/new-recall` imports explicit saved notes into a **new,
inactive** owner directory. Use a closed captured SQLite database without WAL,
SHM or journal companions. This command does not stop or capture a running old
installation and does not activate the receiving owner.

The reviewed mapping has `sessions` and `workspaces` objects. Session values are
exact AHP session URIs; workspace values are the receiving Host's exact
`historyHome` paths. For example:

```json
{"sessions":{"old-chat":"ahp-session:/new-chat"},"workspaces":{"old-workspace":"/owned/project"}}
```

Every task/workspace scope and excluded chat must resolve explicitly. Distinct
source scopes cannot merge. The importer retains note IDs, wording, all captured
revisions, original write provenance, workspace consent, exclusions and command
receipts. Receipt fingerprints are retained, so reusing an old command ID with
changed arguments fails instead of creating another note. The original database
is retained byte for byte as `legacy-recall.sqlite3`, with digest, counts and scope
in `migration.json`. The source is read-only. Publication of the new directory is
atomic; retries never overwrite an existing owner.

Referenced notes and automatic consolidation history additionally require
`--evidence /reviewed/evidence.json`. Without it they are refused as a whole.
Evidence contains `messages` and `attempts` arrays:

```json
{
  "messages": [{"sessionId":"old-chat","messageId":"old-message","sha256":"<original text digest>","mappedMessageId":"receiving-host-message"}],
  "attempts": [{"id":"<original attempt digest>","sourceRevision":"<original window digest>","mappedSourceRevision":"<receiving window digest>"}]
}
```

Digests use the original Recall `digest` function (SHA-256 of sorted-key,
Unicode-preserving JSON). Source revisions digest the ordered `(id, text)` human
message window used by consolidation; they are not native file revisions. The
attempt ID must equal `digest([oldSessionId, sourceRevision])`. Resolve these
from retained source evidence and the receiving Host's actual message identities;
do not substitute a recent window for an uncertain older attempt. Missing or
ambiguous evidence blocks the import before publication.

All attempts keep their timestamps, outcomes and old identity, while their retry
guards bind to the receiving source window. Claimed outcomes become `unknown`,
never queued work. Workspace call budgets still count those attempts. Deleted or
corrected notes retain suppression against the mapped original quotation;
missing evidence for a deleted note also blocks the import. Current automatic
notes seed the new owner's dedup index. Supersession links and historical write
provenance are preserved. The current main schema stores automatic identities on
notes; a populated `memory_automation` table from a different owner format is
refused instead of guessed.
Explicit reference locators retain their original attribution, including its
absence. They are not promoted to attributable human quotations. Unrecognized
reference formats require their own source adapter and remain refused.

The mapping does **not** mint Host admission or permission. Current automatic
extraction and agent-requested changes still require a Host-admitted human input.
Previously saved memories have a separate historical verification path: the
retained database digest, reviewed mapping/evidence digests, original saved note
and receiving Host's exact projected source must agree. The source must retain
its explicit native user-input identity, complete original text digest and
quotation. Peer, scheduled, question, feedback and recorded-only rows are refused;
unmarked older rows need additional source evidence. Current workspace consent,
exclusions and the provider-boundary recheck apply to both paths. This lets a
previously approved memory remain historical reference without making its source
a new instruction, authorizing changes, or rerunning consolidation. The preserved
database and `identity-mapping.json` retain original outcomes and translations.
No note is silently dropped or represented as newly authorized. Qualified Native
readers select the exact source independently of recent turns, using the existing
64 MiB canonical history bound and a 64 KiB complete-source response. Older readers
retain a bounded compatibility path covering their latest 50 complete turns.
Archived context segments, unmarked legacy sources and oversized histories remain
saved but need further migration qualification before automatic use.
Derived search indexes are rebuilt through the regular explicit refresh action.
This adapter qualifies memory transfer only, not a complete installation
switch or rollback of work created after switching.

`manifest`, `actionSchemas`, `read`, `action`, and `close` implement the scoped capability interface. Topic `recall` projects `{recall:{[sessionURI]:{coverage,memory}}}`. `memoryContext(session,{expected?})` handles the trusted native `memory.context` request; `idle(session)` is an explicit host completion hook. Only advertise native memory when these hooks are connected. The owner does not register a timer or eagerly observe every catalog session.

## Bounds and authority

Startup reads no session catalog/history. Explicit index refresh traverses authorized metadata pages of at most 25 sources, then complete native history pages of at most 50 turns/1,000 rows/2 MiB. It stages batches of 100 rows and atomically publishes only after a final matching source revision. Failed or cancelled stages preserve the previous generation. Coverage records retain at most 25 errors; at most four background jobs run. Search pages contain at most 50 matches, and candidate visibility checks use indexed metadata only. Selected reads validate the native stamp before returning at most 4,000 characters. Missing or changed sources fail visibly. Native logs remain read-only.

Original Recall and memory action names are retained. `recall.search` adds a query/index-revision cursor; `recall.refresh` adds explicit task/workspace/all scope, defaulting to workspace. Child/internal sources are refused until the host advertises an appropriate inventory. `memory.command {commandId}` reads the exact selected-session mutation ledger after a missing response, without repeating a mutation. Mutation IDs bind scope, operation and arguments.

Contribution and automatic use default off. Contribution uses at most three model attempts per workspace/day by default (explicitly configurable 1–10), at most 16,000 complete source characters, and the native 4,096-output-token limit. Only exact complete host-admitted user text qualifies. Native/imported provenance is retained as unverified and cannot authorize changes. Scheduled/question inputs are excluded. Model results retain verbatim source quotations and explicit derived-wording provenance; unsupported quotations and stale source/consent revisions are rejected. Claims are durable before the model call; interruption, restart and lost transport never replay it.

Consolidation compares a complete maximum of 100 scoped notes and the policy's 12,000-character reference window; exceeding either refuses with visible activity rather than omitting possible contradictions. Automatic context likewise reports omitted coverage above 100 scoped notes. This conservative bound remains a scalability limitation for large note stores, distinct from the bounded conversation index. Source logs, existing backups and previously published references are not rewritten by note deletion.

## Qualification

`python -I -m pytest -c python/pyproject.toml --import-mode=importlib python/tests` exercises atomic index changes, hidden sources, exact host-attributed consent, opt-in generation/quote verification, context delivery and restart/no-replay. `RECALL_PYTHON=/installed/python npm test` runs the actual Node → installed Python owner boundary with passive fixture callbacks and no model. Public host native-history acceptance and web browser acceptance are separate gates. The copied pure policy helpers are pinned and attributed in `PROVENANCE.json`; the engine-bearing amplifier-memory package is not installed here.

## Restart admission

`quiescenceParticipant(ownerId)` uses the optional Foundation operations durable intake ledger under the owner's existing process lease. Background indexing and consolidation retain ownership through completion; a busy acquisition emits an advisory `onMayBeIdle` after all work settles. Held and unknown fences survive restart. Only exact authenticated release proof reopens intake. Historical model attempts alone never imply live work and are never replayed. `quiescenceAccess` permits selected status, wait, note and command reads; search, context delivery and source validation can update projections and remain fenced.

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

Unknown consolidation work protects selected conversations. Settled notes/provenance are copied data or canonical history references, not lazy execution-file handles; literal paths in note prose are not interpreted as authority.

Inspection uses bounded selected metadata or indexed overlap probes, not native transcripts, a global history projection, or worker/model startup. Existing retention-hide and service-stop lifecycles are unchanged. The package tests cover the independently installed owner transport and held/unknown/exact-release behavior; full host/native/browser disposal acceptance remains a composition responsibility.

## Authoritative startup storage

The embedded owner's durable base schema is validated read-only under its existing lease before any writable open, migration or interrupted-outcome normalization. Missing authoritative tables or columns refuse startup without repair or replay. A new store requires its main file and all WAL, SHM and rollback-journal paths to be absent; zero-length and dangling-link evidence also prevents replacement. Complete deletion of every file cannot be detected.

Validation reads fixed schema metadata and zero-row column queries. It does not scan or hydrate saved rows, copy the database, or add persistent state. Unmarked historical layouts missing current base authority are ambiguous and fail closed; this owner does not infer an addon migration from missing evidence.

The base schema includes explicit notes/versions/receipts, workspace consent, attempt budgets, suppression/automation markers, adapter command/admission journals and context-delivery references. These tables have been present since the initial embedded adapter profile. Derived search sources/documents/FTS, coverage progress and activity summaries keep their existing bounded rebuild/migration behavior. Optional retention indexes are not authority requirements. The standalone Foundation RecallStore profile remains independent; an index-only database is not silently promoted into this embedded profile.

With a main database present, read-only SQLite may create empty WAL/SHM bookkeeping or maintain its SHM read cache. Qualification preserves authoritative main and existing WAL/journal bytes, rather than claiming cache-byte identity. Missing-main refusal opens no SQLite connection and preserves every surviving sidecar and link.

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
