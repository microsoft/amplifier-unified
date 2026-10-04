# Amplifier Unified feedback capability

This product adapter owns explicit feedback submissions, reviewed excerpts,
immutable attachment staging and durable delivery receipts. All drafts, file
choices, selected reports and correction editors remain in client storage. It
imports no old application service or global state object.

`createFeedbackCapability({owner, uploadOwner, inspectSession, readExport,
authorizeFeedback?, onInvalidate?})` composes the independently installed Python
owner and a separate instance of the public resources package. That instance
uses the private `feedbackUploadScope` partition; it never creates a host chat,
native session or model. Only its immutable attachment provider is exposed,
under `amplifier-feedback-attachment:`. The public upload/create/inspect/commit
actions and standard resource writes have the same bounded/resumable behavior
as ordinary attachment uploads. Limits are 8 MiB per file, eight selected files
and 24 MiB per submission. Clients upload only on explicit Send and retain their
original files until completion is known.

The Python executable is `amplifier-unified-feedback --config CONFIG`, with
`{dataDir}` pointing at an owned private directory. The fixed publication target
is `microsoft/amplifier-unified`; the current authenticated GitHub CLI identity
owns all actual remote requests. No credentials are sent in capability state.
GitHub reads/writes have a 45-second transport deadline and bounded responses.
Tests inject an isolated GitHub implementation; they never publish real issues.

The host-scoped `feedback` topic contains at most 20 receipt summaries. Use
`feedback.list` for cursor pages (maximum 50), `feedback.receipt` for an exact
saved result, and `feedback.get` for a selected report and 20 comments. Bodies,
audit versions and reviewed text remain on disk and load only on explicit reads.
Actions return durable receipts with `dispatching`, `completed`, `failed` or
`unknown` status. Completed reads may contain `report`, `review` or `attachment`;
completed writes contain their canonical GitHub receipt. Draft changes are never
accepted by this protocol.

Every effect has a stable request ID. Accepted payloads are immutable; retries
return the saved receipt. A disconnected or uncertain GitHub write stays unknown
and is never reposted. Read-only reconciliation binds only a unique, freshly
verified original author/marker match; missing or ambiguous search results do not
authorize another write. Follow-ups verify the current GitHub user and original
issue marker. Corrections append a new comment and preserve the original issue;
close/reopen verify a fresh revision, while explicitly acknowledging that GitHub
does not provide an atomic conditional issue update.

Excerpt review requires a selected immutable minimal Markdown export from the
public host callback, with verified hash, scope and at most 64 KB. Edited text is
redacted best-effort, shown with warnings and checked against actual repository
visibility. Staging requires explicit disclosure acknowledgement. Sending requires
the exact complete selected excerpt ID/hash set and rechecks destination visibility
before any upload. Ordinary files require a private repository. Uploaded files are
retained in Git repository history; deleting a local draft does not retract them.

Agent-origin remote mutations and excerpt disclosure require an injected
`authorizeFeedback` policy. The authenticated UI uses the same commands and
receipt journal. No transcript prose or ordinary feedback request creates inferred
disclosure approval. No agent request is executed in these component tests.

Qualification: installed Python owner tests, an installed subprocess/public-resource
integration test and explicit fixture GitHub failures. Browser interactions,
actual GitHub identity and deployed-worker adoption are
separate acceptance boundaries. This owner does not run a model to prepare feedback.

## Explicit legacy receipt import

Stop the independent feedback owner, then use its installed Python to import one
bounded page from an explicitly chosen legacy application database:

```sh
python -I -m amplifier_unified_feedback.migrate \
  --source /retained/legacy/app.sqlite3 \
  --destination /owned/unified/capabilities/feedback \
  --table feedback_requests --limit 50
```

Continue with `--after` set to `nextCursor`, then repeat for `feedback_followups`.
The importer takes the destination owner's exclusive lock and a read-only source
snapshot. It retains exact accepted payload/receipt evidence in its private audit
journal and leaves the original database and attachment files untouched. Confirmed
receipts remain historical; queued/sending effects become unknown and cannot be
replayed. Existing IDs are never overwritten. Invalid, oversized or conflicting
rows are classified for explicit recovery rather than silently discarded.

This imports receipt history only. It does not import client drafts, restage local
files, submit feedback, execute models, or contact GitHub. Imported reports still
require current GitHub author/marker verification before later actions. Keep the
legacy database and file store until any classified omissions are resolved.

The trusted `quiescenceParticipant(ownerId)` is available only when the configured
public upload owner also supplies a real participant. It holds both the private
upload partition and the Python feedback owner. In-flight preparation/remote calls
refuse acquisition; safe receipt/diagnostic reads remain available. Durable fences
survive restart and prevent offline import, while historical unknown receipts do
not imply current work. Exact host proof releases both owners; a lost acquisition
or unknown outcome retains their fences. `onMayBeIdle` is an advisory completion
notification, never an authorization or proof. The shared ledger mechanism is the
optional Foundation operations library; feedback authorization stays here.

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

The dedicated upload partition is held alongside the feedback process. Unfinished uploads with no proved conversation association return partial coverage (`feedback-upload-unattributed`); unknown publication commands protect matching or unscoped conversations.

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

Unknown sends protect their selected conversation, or the whole requested family when unscoped. The actual private upload-resource participant is held too; unattributed pending uploads produce partial coverage. Finished attachments/excerpts are owned immutable copies, not lazy execution-file paths.

Inspection uses bounded selected metadata or indexed overlap probes, not native transcripts, a global history projection, or worker/model startup. Existing retention-hide and service-stop lifecycles are unchanged. The package tests cover the independently installed owner transport and held/unknown/exact-release behavior; full host/native/browser disposal acceptance remains a composition responsibility.

## Authoritative startup storage

The embedded owner's durable base schema is validated read-only under its existing lease before any writable open, migration or interrupted-outcome normalization. Missing authoritative tables or columns refuse startup without repair or replay. A new store requires its main file and all WAL, SHM and rollback-journal paths to be absent; zero-length and dangling-link evidence also prevents replacement. Complete deletion of every file cannot be detected.

Validation reads fixed schema metadata and zero-row column queries. It does not scan or hydrate saved rows, copy the database, or add persistent state. Unmarked historical layouts missing current base authority are ambiguous and fail closed; this owner does not infer an addon migration from missing evidence.

The base schema is commands, attachments and reviews, present since the initial feedback adapter. Optional legacy_imports metadata remains part of the explicit stopped-owner import workflow; it is not required by normal startup. Optional retention indexes are likewise not startup authority requirements. Unknown delivery receipts remain unknown on a healthy restart.

With a main database present, read-only SQLite may create empty WAL/SHM bookkeeping or maintain its SHM read cache. Qualification preserves authoritative main and existing WAL/journal bytes, rather than claiming cache-byte identity. Missing-main refusal opens no SQLite connection and preserves every surviving sidecar and link.
