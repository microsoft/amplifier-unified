# Selected history projection

The native transcript and event logs remain authoritative. The host retains
command, runtime, permission and canonical-write authority. Selected Work is a
read-only, disposable projection; reading it cannot prepare or execute a runtime.
This implements the approved working-set plan incrementally, without importing
unselected historical sessions or changing search retention policy.

## Requested browser windows

The bundled browser opens a bounded recent message window and a separate root
event window. Neither first messages nor recent Work waits for a complete
selected-family tree. Earlier pages require an explicit reading action; opening
or hovering the conversation rail uses only already loaded message text. An
unknown total is not zero and does not start an automatic completeness loop.

`TranscriptWindows` owns at most 32 retained pages / 32 MiB and eight physical
reads. Native transcript reads examine at most 8 MiB / 512 raw records and return
at most 100 visible messages, with a 4 MiB record bound. They preserve the existing
primary/backup/pending-write recovery rules. Native rows have window-scoped
display identities and `displayOnly: true`; an unverified prefix cannot establish
global input uniqueness, editing/forking authority, or a canonical message/media
binding. Copy and exact text expansion use the accepted page owner instead.

Web-owned chats keep their canonical saved message IDs and command semantics.
Resident arrays receive a conservative record/page/depth/object preflight before
any copy or encoding; cold immutable message resources use a bounded reverse
array reader with the same byte/record limits. The latter validates the
requested window, resource ownership and file identity, not an unread prefix or
the complete content digest. It never hydrates a whole cold execution, message or
configuration body. A changed runtime transcript is offered as a separate,
explicit read-only history view; it is not silently merged into saved web rows.
Receipt-derived sender captions require a complete, budget-admitted saved message
set with no omitted ranges. Partial saved pages and native windows remain neutral:
a matching row within one page cannot prove global message/input uniqueness.
Long saved histories need a separate bounded uniqueness receipt/index before
those captions can be restored without a whole-history read. Existing canonical
saved IDs and message actions do not depend on a display caption.

Message cursors are authenticated by a service-owned key and bound to exact
source/resource identity, source stamps and the applicable published owner fence.
An older cursor cannot silently continue a different generation. Oversized or
unreadable records create explicit gaps with advancing continuations; a readable
neighbor is not dropped merely because the current request budget ran out.
Canceled HTTP waiters do not release ownership of a still-running physical read.
Cache retirement and source failures remain distinguishable from empty history.

`WorkWindows` keeps at most eight root event pages, four jobs and 32 monotonic
invalidation fences.
On qualified Linux hosts its lazy disposable process uses the existing transport
with 64 KiB inputs, 4 MiB outputs, 20 MiB aggregate spool reservation and the
unchanged 768 MiB process policy. Other platforms use the same bounded reader in
an owned thread until their process policy is qualified; this is not process RSS
isolation and never falls back to exhaustive Work reconstruction. Inputs contain
only canonical source identity, a requested window/cursor and the private signing
key. They exclude full execution, retired usage, workers and generation graphs.

Recent event observations stay separate from canonical execution/accounting.
There is no EventIndex/ModelStore, transcript scan, child traversal or inferred
turn pairing on this path. Existing accepted live evidence remains a separate
bounded overlay; summaries derived from a truncated live slice do not claim
complete call counts or usage. Admission, permissions and complete accounting
continue to use their existing host-owned facts, never these display pages.
Exact expanded fields resolve only retained private references through the
current canonical source owner; clients cannot supply paths or signing keys.

### Scoped cold binding candidate (qualification pending)

Cold root work can additionally use the **existing host diagnostic index** as
provenance for metadata-only lifecycle records. The worker receives its fixed
host cache home through process construction, never an HTTP database path. A
bounded unique-ID lookup must match the indexed event, stream, session, workspace
and exact source path/offset/length/hash/event-ID receipt. JSONL `id`, `turnId` or
`app` fields alone are not authority. Missing, suppressed, expired or unhashed
receipts remain unassociated; capture policy and recording are unchanged.
The read-only SQLite pager is initialized before recording the ownership-index
baseline; that initialization snapshot is discarded. Attestation uses a fresh
transaction, and its recorded dependency must remain unchanged through queries,
connection teardown and host publication. A writer change still refuses the
read. Partial raw windows do not consult or depend on the ownership index;
complete misses retain the dependency so later indexed receipts are reconsidered.

The ordinary page path qualifies only a whole root source fitting one existing
bounded page with no malformed records, gaps or partial tail. An older page
reaching offset zero does not qualify. Tools need one canonical pre/end pair and
one attested host pre/end pair, consistent root ownership and revisions; reused
IDs, conflicting aliases and cross-source candidates refuse binding. Model
lifecycle rows use their own attested pair, without inventing provider payloads.

### Finite cumulative root proof prerequisite (not consumer qualification)

An explicit host request may additionally call `WorkWindows.prove(session_id)`,
or the equivalent private latest-read option `read(..., proof=True)`. This is
not a new public HTTP parameter, a snapshot side effect or a field-read retry.
The consumer candidate below connects this prerequisite to selected Work.
A complete proof alone does not satisfy consumer or rendered acceptance.

Each operation owns one canonical owner/source/window generation and one
20-second aggregate deadline, including worker waits and aggregation. It asks
the existing worker for at most eight `proof-step` operations. Each step reads
exactly one page with the unchanged 100-item, 512-record and 4-MiB read bounds.
Transport remains 64-KiB input, 4-MiB output, 20-MiB aggregate spool and four
jobs. Proof support uses the same **eight total retained event pages**, not a
second proof cache. Descriptors contain only operation identity, status,
dependency stamps and support-page identities. Retained proof results contain
metadata and exact field references, never captured bodies or resident copies.

The worker's private page proof carries the actual consumed record span,
bounded scalar collision facts and offset/length/hash receipts for every
consumed record, including records omitted from visible observations. Read-ahead
bytes are not coverage. It also carries malformed/shape-invalid/partial-tail/gap
flags, authenticated cursor linkage, attested diagnostic occurrence identities
and the index revision measured through connection teardown. Ordinary partial
raw reads retain their existing no-index-dependency behavior.

Pure cumulative validation requires an authentic cursor chain from the same
frozen last byte to offset zero, contiguous consumed record boundaries, unchanged
owner/window/source stamps and index generation, and no missing, overlapping,
unaligned, malformed or ambiguous spans. An exact repeated step contributes once;
a conflicting receipt is refused. All collision facts are counted before any
display filtering. Only after complete validation is the existing exact
four-occurrence/lifecycle/attested-owner predicate applied. No tool-ID-only or
timestamp-only association is introduced.

`workProjection.rootProof` reports `pending`, `complete`, `incomplete` or `stale`,
with bounded step count and a reason. Polling, passive interests and field chunks
cannot start or resume a proof. A failed or stale operation stays held until an
explicit `prove(..., reopen=True)`; that creates a new finite operation, never a
ninth step or eviction-driven continuation. Same-operation waiters join. A timed
out physical step remains owned and counted until it settles, and cannot publish
a late proof. Shutdown retains ownership through worker cleanup.

Source/index changes, published invalidation, owner replacement or loss of any
supporting page invalidate every reference derived from the proof. Snapshot
status checks only already-known owner/fence/page facts; explicit proof use also
checks physical source and index stamps. A follow-on awaited field consumer must
repeat those checks after its await. Raw occurrences retain their independent,
unverified identity and normal exact-source field readability; proof incompleteness
does not delete them. No logging, capture opt-out, index writer, agent read scope
or historical accounting authority changes.

At source `59c2eccb7`, the fixed diagnostic executed all181 cases:178 passed and
three ordinary live/large-history cases failed. All70 new cumulative-proof cases
passed at the synthetic emitter/index and in-process reader boundary. That is
not native/subprocess/rendered consumer qualification. The original failures
remain open until the consumer increment executes its discriminating checks.

### Selected Work consumers (source candidate, unqualified)

The bundled conversation loader issues one explicit latest `groups` read when
selecting a deferred Work conversation or choosing Return/Open latest work.
Metadata publications, sync and field requests never start a proof. A latest
resident node open can also start/join it; a refusal has no automatic loop.
`read_selected` publishes cold placement only after complete proof. The existing
raw page cursor reuses retained proof pages, rather than replacing their support.

Live metadata remains untouched. The private overlay compares every terminal
invocation field against authenticated lifecycle facts: logical ID, kind,
canonical session/root, turn, parent, revision, phase and exact start/end. Tools
also require the exact call identity. No call-ID-only association is accepted.
References identify a client's admitted group/entry/position and a specific proof;
their versions include reference availability. Sync can therefore replace a row
without stale references when its proof expires. The canonical read uses the
existing bounded awaited worker field operation. Client, owner, entry, position,
lifecycle, digest, proof, source/index and invalidation dependencies are checked
before and after its await; failure never falls back to a weaker resolver.

Cold ordinary groups use completed resident host turns and existing within-turn
placement. Their node pages contain at most100 rows, with signed client/owner/
group/proof/revision/entry cursors. Browser field receipts are separate from raw
occurrence references. Eight bounded client entries retain IDs/cursors, never
page bodies or owners; support-page eviction invalidates their references.

The bundled sync protocol sends `paged:true` and at most100 `known` IDs per
request. Each response covers only that chunk, with complete replacement rows
for changed IDs. The first revision pins every later chunk. One final
`nodes&preserve=true&revision=...` read admits the latest100 without discarding
older admissions and returns the combined window's oldest cursor. The frontend
publishes only after all chunks and that latest page agree; errors preserve the
prior view with a visible error, not a partial merge or retry-until-success.
The live synchronization token covers every admitted physical position plus the
current tail, not only the latest group summary. A change to an older retained
row invalidates later chunks/finalization. Later explicit history paging extends
admission and issues a new token after validating the preceding scope.
The direct non-browser resident helper retains its previous bounded interface;
registered browser requests enforce100 IDs. No limits are raised.

The fixed diagnostic adds the resident consumer test file; its full default,
dependencies, permissions and timeout remain unchanged. The existing browser
workflow adds only fixed Node consumer/selection tests to its controls, retaining
the complete maintained browser manifest and build. Both commands remain unrun
for this consumer source candidate. The original144-call/full-Unicode/ownership/
no-duplicates assertions remain; only their sync transport is chunked.

Placement also requires the completed host turn and unambiguous message times
already resident in the requested window. Time places work only within that
proven turn, never identifies a turn. Page-backed subgroup IDs keep the existing
`event-window:` route; original input/turn IDs and raw occurrence IDs remain
distinct from the accepted page. Full fields still use the bounded field reader.
Source/index (including WAL) changes or invalidation expire bound reads. Snapshot
rendering does not perform source/index IO or hydrate cold execution.

Every accepted root group page also exposes an `evidence` reference containing
`sessionId`, `part: observations`, the original root `group` and its `revision`.
Bound groups and nodes carry that same reference. The registered-web detail
route accepts it only for a retained root page with the exact revision, returning
all original occurrences in that page (at most 100), including those deduplicated
from ordinary Work. Original IDs and independent field references remain intact;
raw field `windowKey` values identify the root page, not a merged bound subgroup.
This explicit read uses no ownership-index authority, adds no store or hydration,
and does not change ordinary Work deduplication. Awaited fields retain current
source/owner/page and invalidation-fence checks. Raw availability does not prove
turn association, and these additions remain unqualified until executed checks pass.

Unproven/off-page work remains in Recent saved work with explicit placement
coverage. The consumer candidate targets ordinary references within the existing
finite proof contract, not arbitrarily large legacy histories or capture policy.
The focused emitter/index, negative, HTTP and rendered-order checks must pass
before this candidate is described as qualified; source inspection is not proof.

The host checks owner/source identity and the published fence before accepting a
page. A rebind hides the previous owner's rows immediately. Same-source retained
rows can remain readable while explicitly noncurrent. Selection departure,
shutdown and bounded worker retirement release disposable interest without
canceling accepted commands. Status-only notifications use the existing selected
client stream and do not write canonical history.

A published invalidation or owner/source replacement supersedes a pending read;
it does not count as a failed worker or introduce a failure backoff. The existing
interest loop may request the current owner again. Genuine read failures retain
their visible retry state and backoff. A quiet, exact-source cache reuse restores
the retained page's accepted receipt as well as its current status; it cannot
claim readiness without that receipt or publish an obsolete owner's result.

The browser's version-2 DOM receipt has `scope: recent-events`, a `windowKey`,
`itemCount`, `sourceCount`, `scannedBytes` and `scannedEvents`, with
`historyComplete: false`. It certifies acceptance of that root event window only.
It must not satisfy the version-1 complete-reader readiness measurement below.
Older/frozen or alternate history views cannot inherit the latest window's
current receipt. First message visibility, event-window readiness and complete
historical Work remain separate measurements. Recovery observations remain in
canonical records but are not inserted as inline Saved work notices.

These windows remove the complete graph prerequisite for ordinary browser reads.
They do not yet provide exhaustive paged family reconstruction, globally proven
historical associations, or a bounded cold implementation of complete-read APIs.
Those explicit contracts continue through the complete-reader lane below;
independent readers are not silently given a partial browser window.

## Explicit complete-reader process and publication ownership

The existing complete-reader lane is retained for independent subscriptions and
explicit complete reads. On qualified Linux hosts it starts one lazy
fresh-interpreter history worker. macOS and Windows retain its threaded reference reader
until their native process-memory and lifecycle policies are qualified. Spawn
compatibility alone is not platform acceptance. Only a detached
projection input and private immutable spool paths cross that boundary. Input
construction, encoding, decoding, hashing, source checks and comparisons run off
the event loop. The service lock covers only the short final owner/fence check and
publication. Published mutations invalidate an in-flight fence. This internal
passive path requires host writers to publish or invalidate their mutations. The
explicit standalone refresh API retains its exact reference-reader comparison
under the service lock, including unpublished aliases; production callers use the
passive path. An extra off-loop digest rejects changes observed during computation.

Transport JSON encoding groups small fragments into at most 64 KiB writes.
Each write reserves aggregate spool bytes before touching the file; input limits
and stop checks still apply per fragment, with extra stop checks between slices
of a large string fragment. Failure deletes the partial artifact and releases
only that encoder's reserved bytes. This bounds additional batching storage,
not the existing Python input or a JSON encoder's whole-string allocation.

The worker returns a complete tree for a cold generation, a small changed-node
patch for a proven local update, or an unchanged receipt. The host rechecks source
identity and its exact input before accepting any result. A superseded result is
discarded. Retry, saturation, cancellation and worker failure preserve selection
and the last readable Work; retained interest retries without executing work or
turning a read failure into a conversation failure.

The host coalesces complete reads per canonical identity and admits at most eight
jobs. Bundled browser subscriptions use requested windows instead of this queue;
ordinary selection does not add an exhaustive descendant read. Worker computation
remains serial within the bounded queue. Loading/retry/ready status is visible while existing
conversation and Work remain readable. Transient retries back off 3–30 seconds.
Deterministic input/output limits instead show an unavailable state. At most eight
small negative receipts suppress repeated encoding/projection of that same rejected
generation. Input changes, or a changed proven source generation for output limits,
re-enable it. Bounded output receipts retain at most 2,048 paths / 256 KiB; if a
receipt cannot be retained, a published owner invalidation re-enables the request.
Refusal receipts contain only digests/source facts, never execution trees.

The qualified process reader also publishes a bounded completion receipt in
the selected owner's `workProjection`. `receipt` contains the host instance and
session IDs, a positive host-local `generation`, and `sourceCount`, `nodeCount`
and `turnCount`. The counts use the already accepted source receipt and tree;
collecting them adds no history reads, body traversal or hashing. A generation
is issued only after the existing exact-input, source-stamp and owner/fence
checks accept the read. Quiet passive validation preserves that generation and
does not publish a periodic diagnostic update. Status-only transitions use the
existing bounded selected-web notification and state-delta lane at the unchanged
application revision; they do not save state or execution. Each selected client
receives its own immutable frame with its private view/draft. Changed execution
continues through ordinary durable publication. These ephemeral display receipts
do not grant command, accounting or execution authority.

`current: false` preserves the previous receipt during loading, retry or refusal.
Published invalidation and projection retirement remove its current claim before
another frame can report it. This process-local status is excluded from saved
views. Missing receipts, another host/session, and the unqualified threaded
platform path do not certify completion. The paired browser exposes only these
fixed scalar facts in a bounded `data-work-projection` attribute on the canonical
messages pane. Acceptance records first messages and first Work headers separately
from `projectionReadyMs`; captures with `expectWork` require an explicit current
receipt for the exact host and session before reporting ready.

This proves acceptance of the existing reader's discovered source scope, not an
independent inventory of every canonical event or eventual undiscovered child.
Rendered headers can merge or split and are not canonical node/turn totals. The
bounded group page and any independently qualified source oracle remain separate
evidence; a missing loading banner is never a completion receipt.

The transport admits at most eight jobs, with 32 MiB encoded inputs, 64 MiB results
and 256 MiB aggregate spool reservation. Shared exact-key/input jobs outlive an
individual canceled waiter. EOF, shutdown, timeout and a failed worker retire
owned files and the process. The process budget is 768 MiB: Unix address-space
limits and cooperative resident checks, or a Windows process-commit Job Object.
Linux charges the worker's current resident pages from `/proc/self/statm`:
`ru_maxrss` can inherit the launching host's pre-exec high-water mark and reject
a small fresh worker. The address-space cap remains unchanged; unavailable or
malformed resident accounting refuses the worker rather than disabling its guard.
These are distinct memory measurements, not a universal hard RSS guarantee.
Platform acceptance must identify which policy was exercised.

Before a failed worker is retired, it reports a fixed processing-stage label and
available current/peak resident and virtual memory measurements, plus its actual
address-space limit. Unavailable platform measurements remain null. The host
retains only the most recent 16 sanitized failure records in performance
diagnostics; no source paths, identities, content, or exception text cross this
diagnostic boundary. A `memory_limit` outcome alone does not identify which
memory measurement was exceeded or justify increasing the budget.
Startup distinguishes memory-policy setup, model import, and model-store creation.
Only `MemoryError` or the explicit memory-policy refusal reports `memory_limit`;
other startup exceptions report `worker_failed`. An allowlisted exception type
and bounded numeric errno distinguish those failures without retaining their text.

## Generations and incremental dependencies

A generation includes projection schema, exact association/lifecycle input digest,
and each source's device/inode, size, nanosecond modification/change times, committed
newline offset and SHA-256 of its committed prefix. On an append, the changed
source's committed prefix is verified before reading at most 256 KiB or 256 new
events. This detects an earlier same-inode edit followed by an append. Partial final
lines remain uncommitted. Replacement, truncation, transcript edits, changed input
associations, new children, unknown event types and uncertainty rebuild through the
same reference reader. Source identity is checked before and after projection.

The first incremental path recognizes an explicit set of non-Work bookkeeping
events and existing tool completion/error events whose source/identity and dependency
closure are unambiguous. Root completions with transcript associations, delegation,
plans, new model calls and ambiguous aliases retain the reference path. Complete
turn timing, host lifecycle, accounting, tool-field references and original raw
recordings remain intact. A narrower path must have reference-reader equality tests
before its recognition set expands.

Sparse event checkpoint schema 2 adds committed-prefix proof. Old disposable
checkpoints are ignored only when their session is requested; there is no global
cache migration. The reader checks the writer's format-first header within 64
decoded bytes before expanding a checkpoint. Obsolete or malformed headers
never allocate the saved graph. Allocation failure while loading an optional
checkpoint resets its partial index and falls back to the canonical event log.
Prefix verification reads existing bytes of the changed log, but avoids
parsing unchanged events and restoring every descendant index.

## Bounded derived storage

The worker's private SQLite sidecar contains model receipts, tree headers and
positioned node rows. A source-only change updates a receipt; a proven completion
updates only affected node rows. A cold rebuild atomically replaces one generation.
The sidecar is never an execution checkpoint. Cache failure falls back to native
reads, and source-path checks prevent a cached receipt from granting a new scope.

Before loading a stored tree, the worker reads only that requested model's receipt,
bounded to 1 MiB encoded, and compares its exact input digest. A mismatch or an
oversized receipt takes the canonical read without fetching tree headers or node
rows. A mismatched in-memory model is released before that read; unrelated bounded
models remain available. This avoids retaining an already-unusable graph during a
cold rebuild. It does not change source validation or make a receipt authoritative.

Retain at most six selected/recent model generations, 96 MiB of logical decoded
model payload, 64 MiB encoded per model, and 256 MiB total encoded model rows.
SQLite also has a 512 MiB allocated-page cap; reusable free pages are not live
payload. Existing sparse event-index retention is independently bounded. No eviction
removes canonical logs. Cache schema or input mismatches rebuild only the requested
model. A worker-only restart can reuse a model when the host still owns the exact
post-projection inputs. A full application restart may have intentionally removed
canonical display rows during durable persistence; that changed input rebuilds
the selected tree rather than treating a cache as authority. Logical decoded charges are not process RSS; the process budget is a separate
limit.

Failure diagnostics include the host's requested chat ID, rather than the viewer's
current selection, so queued reads can be traced after rapid chat switches. Worker
payloads cannot supply or override this identity; source paths and content remain
excluded.

Event-index checkpoint writes stream JSON through a 64 KiB buffer into a private
gzip temporary file. The 64 MiB decoded and 16 MiB compressed quotas are checked
during serialization, before the complete checkpoint is atomically installed.
Individual strings above 256 Ki characters or nesting deeper than 64 levels skip
this optional cache before encoding; canonical reads remain unchanged. This also
bounds the encoder's individual scalar chunks, which may exceed the write buffer.
An allocation or storage failure while writing a disposable checkpoint preserves
the canonical read and the previous complete checkpoint. These bounds remove
whole-document serialization copies; they do not bound the full cold Work tree.

This complete-reader lane still materializes a complete selected cold Work tree
for its existing full-summary/detail consumers. It avoids repeated descendant
reconstruction for recognized small appends and isolates uncertain/cold
reconstruction. Fully paged complete-family projections and broader incremental
event families remain follow-up work; these bounds do not claim the plan's
complete latency exits are met.

## Bounded event-window primitive

`event_window.read_window` supplies the requested Work-window lane. Its trusted
input is the canonical session owner, exact requested event-window key,
host-owned 32-byte cursor signing key, and optional older-page cursor.
Execution, retired usage, transcript bodies, workers and generations are not
inputs. The host keeps the signing key private and stable across disposable
worker restarts. There is no new endpoint or independent transport.

One request reads at most 4 MiB / 512 complete event records from one exact root
file and returns at most 100 observations in source order. It does not construct
an EventIndex or ModelStore, scan a transcript, verify an unread prefix, or open
child logs. Delegation events can name a child but cannot admit its history.
Older bytes require explicit continuation. The authenticated cursor binds owner,
requested window, source identity/size/times and byte boundary. Replacement,
append, truncation or in-place edits invalidate it. The reader validates sources
before and after reading; publication still requires the host's current
owner/window/source check. Source references and signing keys are private owner
data, never client-supplied paths or keys.

Items are source-event observations, not reconstructed calls or full Work groups.
Event-offset/digest IDs preserve distinct observations when legacy call IDs repeat.
Recorded IDs remain available for a separate proven host binding. The primitive
never infers turn placement, prompt uniqueness, request/response pairing, running
state or accounting from a cut prefix. It returns no aggregate usage or complete
call totals. `read_field` resolves an accepted observation's exact private field
reference, validates canonical bytes, and returns at most 16,384 text characters
per explicit request. It does not require a full `execution.nodes` graph.

Coverage always says `historyComplete: false`, including an exhausted root file:
no transcript or descendant completeness was proved. `rootExhausted` describes
only the root file's range. Missing sources and unfinished final records remain
explicit. Oversized or structurally excessive records produce unavailable ranges,
never an empty-complete claim. Signed unaligned continuations advance through
oversized records without decoding suffixes; a readable neighbor that merely
exceeds the remaining request budget is retried whole on the next page. Structural
depth/value-count checks precede JSON allocation. No hidden loop fills older history.

The host/client integration distinguishes window availability from the complete
reader receipt above, keeps unproven observations separate, and preserves live
accounting and explicit complete-read APIs. Primitive tests alone do not qualify
integrated chat opening or complete historical Work; each needs its own proof.

## Durability and inline-read candidate — qualification pending

An explicit, joinable root proof now joins the existing Diagnostics flush lock
before its first source/index baseline. It drains the currently queued batch once
through the existing writer and checks storage-ready/error, configuration-error,
stopping and pending state while still holding that lock. A normal `_flush`
return alone is not successful persistence. No failed batch is reconstructed or
requeued. A reader-only owner without a Diagnostics writer still has to pass the
worker's independent source and attestation checks.

Lock waiting, persistence, page work and publication share the same 20-second
aggregate deadline. A timed-out physical writer remains owned in the existing
draining set; a waiter that acquires the writer lock only after expiry cannot
start another drain. Canonical owner, source and original epoch are rechecked
after the await. A legitimate publication during readiness still refuses the
stale operation; the client may explicitly reopen, but the host does not retry
until success or silently adopt a newer fence. Failed readiness produces an
incomplete proof and noncurrent, unavailable display. Passive state, sync and
field requests do not flush, discover or reopen proofs.

Latest resident node requests classify only the existing bounded group slice.
Inline-only rows retain the established client/group/position/version route
without a mandatory canonical proof. Metadata-only observer or recorded-field
rows still require the exact canonical proof route. This is decided before a
lookup, never as a fallback from a failed canonical field or weaker ownership
comparison. Existing field, cursor, sync and post-await checks are unchanged.

The synthetic ordinary-detail fixture now establishes valid CI metadata before
its authored event file becomes visible, following the existing metadata format
and append-lock contract without appending fake ownership records or flushing.
The test observer records only the first persistence exception's allowlisted
class and innermost repository function/line. The earlier diagnostic did not
capture that exception, so the fixture hazard is not a claimed measured cause.

These source changes and their added controls have not executed. In particular,
joining persistence does **not** resolve later index-revision expiry. The physical
DB/WAL fence remains intact; the proposed authority contract below is separate
and not implemented.

## Owner-scoped index authority — admitted source contract, unqualified

Manager engineering disposition, October 18, 2026, supersedes the unimplemented
proposal preserved in Git at `e1a94ccb4`. This is a supported-host contract for
the existing lazy full-field promise, not a new permission or runtime pass.

### Supported lifecycle and writers

The host owns the diagnostics database and schema lifecycle. Out-of-band backup
restore, copy-over replacement or schema surgery requires both the service and
history workers stopped and all disposable proofs retired. Protection against
a privileged actor dropping/recreating triggers or online same-inode rollback
is not promised. Initialization/migration installs authority atomically before
rewriting references; authority rebuild or metadata pruning rotates its epoch.
No live migration is performed as part of this source work.

Metadata lives only in the existing diagnostics database: one validated schema/
epoch row and owner revisions keyed by exact `(workspace, canonical session)`.
Triggers advance affected owners in the same transaction as every supported
`records` INSERT, UPDATE or DELETE, including independent SQL retention,
migration and managed deletion. UPDATE covers both old/new owners; identical
values do not advance it. Ignored duplicate `INSERT OR IGNORE` and rollbacks do
not change authority. Keep `capture_index` duplicate behavior unchanged.

Deletion/reinsertion and owner A→B→A cannot restore an old token. Within an epoch,
deleted-owner revisions are tombstones. Explicit privacy deletion prunes those
owner keys only with atomic epoch rotation; bounded maintenance tied to existing
record limits similarly rotates before removing unrepresented owners. Metadata
is not an indefinite ledger. Invalid metadata or integer overflow aborts a
mutation using an explicit trigger error that IGNORE cannot suppress.

REPLACE and conflict-replacing UPDATE are unsupported internal writer forms.
Do not claim arbitrary-SQL safety or alter duplicate-IGNORE behavior to enforce
the superseded broader contract. Schema metadata itself is host-maintained.

### Reader dependency and acceptance

Exact receipt authentication remains independent of revision equality: retain
event/stream/workspace/session and source path/offset/length/hash/event-ID
comparison, source generation, original owner/epoch and all support-page fences.
Revision zero is a negative dependency only in a validated schema/epoch.

A private typed dependency includes the trusted DB path/physical identity,
schema and epoch, exact owner and revision. Readers open a fresh read-only
snapshot and read that token and bounded receipts together. They validate
physical file kind/identity before and after opening/query/close and re-read
authority from a fresh transaction after teardown. No rows may be adopted under
a different authority token. Unrelated-owner writes, deliveries, capture offsets,
checkpoints and WAL housekeeping may preserve the token; replacement, unavailable
or malformed authority fails closed. Physical continuity is checked, not inferred
from equal counters or a new stamp attached to an old proof.

At the host, the final fresh dependency check is the acceptance/linearization
point. Commit the already-validated projection synchronously after that check,
without an intervening await. A later database commit is a later operation, not
a promise of exclusion through network delivery. Subsequent field chunks must
freshly reject revoked references. Validate before and after every awaited field
operation and after any asynchronous source/readiness boundary. Passive state
and sync do not initiate scans, reproof or writes. All eight-page/four-job/20-second
and payload bounds remain unchanged.

The implementation must identify its final check at `WorkWindows.read_selected`
immediately before committing the selected page/receipt; `_prove` and
`read_proven_field` retain their final validation before returning accepted
material. Worker attestation has a fresh post-close check. No stale field retry
or proof regeneration is implicit.

### Required source tests — runtime qualification pending

| Boundary | Required discriminator |
| --- | --- |
| INSERT/UPDATE/DELETE and direct SQL | Same-owner change revokes; other-owner change does not; owner changes advance old and new; exact no-op and rollback preserve. Duplicate IGNORE preserves both stored row and token. |
| Negative dependency and ABA | Missing-owner zero changes on insertion; delete/reinsert and A→B→A cannot reuse a token; overflow/malformed metadata abort even under IGNORE. |
| Real writers | Legacy migration, capture replacement, retention and managed-deletion SQL participate. Initialization is atomic before reference rewrites. |
| Privacy and bounds | Managed deletion removes identifiers from revision metadata while rotating epoch. Excess tombstones compact under existing limits, retiring prior tokens atomically. |
| Continuity | Delivery/capture-offset updates and checkpoint/WAL truncation preserve verified owner authority; DB replacement, schema/epoch loss and unknown file shape refuse. Offline restore retires proofs by contract. |
| Races | Writes before/during authority query, after query close, before final publication check and during field await reject old material. A commit after the synchronous acceptance point is detected by the next request, not retroactively called exclusion. |

The implementation and authored tests below this contract remain unqualified
until exact-candidate execution. Existing two behind-tail failures and six
historical browser failures remain open. This contract does not reinterpret a
generic index-stamp failure as proven harmless churn.

### Superseded proposal (historical, not an implementation requirement)

The text below records the earlier broader proposal for comparison only. Its
hostile-schema/online-restore/global-exclusion and duplicate-IGNORE rejection
requirements are superseded by the supported contract above.

## Historical PROPOSED index-authority contract — superseded

**Contract plan only. No schema, writer or reader change is admitted by this
section. Retain the existing source and DB/WAL physical fences pending review
and qualification.** The reported `5e38b29e7` diagnostic observed an index
mismatch at large-cold request 124, input offset zero, with six supporting pages,
owner and epoch still matching and two persistence returns. This was the first
chunk of that field, not the first field overall. The changed physical stamp
component and affected authority rows remain unknown; this does not establish
that a checkpoint or an unrelated write caused the refusal.

### Minimal authority and mutation rules

Propose metadata in the **existing diagnostics `events.sqlite3` only**, not a
new store or payload archive. A revision proves currentness, never receipt
authenticity: retain `_host_receipts`' exact event/stream/session/workspace and
path/offset/length/hash/event-ID match, canonical source checks, complete-root
predicate, owner fences and supporting-page checks.

| Proposed metadata | Key and invariant |
| --- | --- |
| `binding_meta(singleton, schema, epoch)` | One row: `singleton=1`, `schema=1`, `epoch` a non-null random 16-byte BLOB issued by the trusted writer. New installation, rebuild or authority-schema migration issues a new epoch atomically; never infer one from record counts or timestamps. |
| `binding_owner(workspace, session, revision)` | Composite primary key of exact, non-null TEXT values using BINARY comparison; session is the canonical capture identity used by the receipt query, not a browser alias or call ID. `revision` is a positive SQLite INTEGER, with explicit type/overflow checks. A missing owner means revision zero only within a validated epoch/snapshot. |

The private dependency is `(physical DB instance, schema fingerprint, epoch,
workspace, session, revision)`, in addition to the existing canonical owner,
source and window dependencies. The physical instance includes the trusted
resolved path and device/inode binding; epoch equality alone cannot identify it.
Schema validation includes the actual table, unique-index and trigger definitions,
not only `user_version` or a declared schema number.

All successful `records` mutations must change authority **in their own SQL
transaction**, including direct SQL outside `Diagnostics._persist`:

- Database triggers cover INSERT, UPDATE and DELETE. INSERT advances the new
  owner; DELETE advances the old owner; a changed UPDATE advances each distinct
  old/new owner. Compare every stored column, including `seq`, `id`, `at` and
  receipt data, with null-safe comparisons; a byte/value-identical UPDATE is a
  no-op. Do not filter triggers to currently recognized event types.
- Keep owner rows as tombstones after the last receipt is deleted. Delete/reinsert
  and A→B→A ownership changes cannot recover an old revision. An aborted statement
  or rolled-back transaction cannot publish a revision. Overflow must abort,
  never wrap or promote to REAL. Null/unkeyable legacy owners require explicit
  migration disposition, not silent exclusion.
- Cover startup legacy-reference UPDATEs, capture repair/replacement DELETEs and
  INSERTs, retention, and `managed_deletion`'s independent direct SQL. Install and
  seed metadata/triggers under one explicit migration transaction **before** any
  reference rewrite; do not assume the current `executescript`/context-manager
  sequence supplies that atomicity. Table rebuilds rotate the epoch.
- **Conflict policy:** reject conflicting INSERTs and conflicting key-changing
  UPDATEs in BEFORE triggers using `RAISE(ABORT)`, before implicit deletion can
  occur. This deliberately also rejects conflicting `OR IGNORE` and
  `ON CONFLICT DO UPDATE`; duplicates must become an explicit transactional
  existence check plus INSERT/UPDATE. SQLite REPLACE can delete a different
  owner without DELETE triggers when `recursive_triggers` is off; setting that
  PRAGMA on one connection is not coverage for another. Qualify both settings
  and every `id`/`seq` conflict, including UPDATE OR REPLACE. Revision bumps use
  guarded UPDATE then INSERT-if-absent, not a nested conflict clause that an outer
  OR policy can override. Invalid metadata/owners and overflow require explicit
  `RAISE(ABORT)`; a CHECK silently skipped by OR IGNORE is not atomic coverage.
- `deliveries`, outbox counters, capture scan offsets and no-op maintenance do
  not advance receipt-owner revisions. Record changes they accompany still do.
  Never advance revisions from a timer, flush invocation/return, pending count,
  or periodic summary. Tombstone pruning requires an epoch rollover and
  invalidation of all old proofs, not deletion followed by revision-one reuse.

This conflict policy requires a reviewed change to `capture_index`'s duplicate
handling; it is not permission to edit that writer now. A smaller policy must
demonstrate equivalent coverage with raw SQL and recursive triggers disabled.
`MAX(seq)`, row counts and hashes of currently present receipts miss revoke/ABA;
connection-local `PRAGMA data_version` is database-wide and cannot be compared
across newly opened connections. None alone supplies this owner-scoped contract.

### Fresh-read protocol and physical continuity

An explicit canonical read stays within its existing aggregate deadline and
budgets. Passive snapshots do no new IO; ordinary raw pages remain independent
of index authority. Missing metadata, corrupt schema or unavailable validation
refuses binding; it never grants weaker association or starts a retry loop.

1. Resolve the host-owned DB path and validate regular-file/no-symlink physical
   identity. Initialize the read-only pager as today, discard its snapshot, then
   establish a new baseline. Retain physical checks for both DB and WAL.
2. In a fresh read transaction, read epoch/schema/owner revision and the bounded
   receipt rows from **one snapshot**. Bracket queries with physical checks.
   A second revision SELECT inside that same transaction cannot detect a
   concurrent WAL commit and is not fresh validation.
3. End the authority transaction and close its connection. In a fresh transaction
   on a fresh read-only connection, validate physical instance, schema, epoch and owner revision
   again; close and check physical state again. Only unchanged authority can
   retain the original receipt result. Never relabel old rows with a newer token.
   Complete misses carry the same negative dependency, including revision zero.
4. Bracket host acceptance/publication with fresh validation; repeat before and
   after every awaited canonical field read, along with source, owner,
   invalidation and support-page checks. All cumulative proof pages share one token.
   A mismatch discards the candidate/chunk; prior successful fields do not
   authorize a later field. Post-publication invalidation cannot repair an
   already-delivered stale claim; the publication ordering constraint below
   must be resolved, not hidden by a later successful check.

**Do not replace the physical fence with counter equality.** DB replacement,
restore or epoch/schema loss invalidates old references even if copied rows and
counters are equal. WAL truncation/removal, checkpoint and maintenance may be
authority-neutral only after physical continuity and fresh authority are proven;
an unexplained transition still refuses. Unrelated-owner writes should not
expire a proof in the qualified design, but neither their existence nor a
successful flush identifies the cause of the measured mismatch.

Admission remains blocked on two enforcement details: a copied old epoch and
counter cannot detect same-inode restore, and validating today's trigger text
cannot detect drop-trigger/mutate/recreate sabotage between reads. Trusted
schema/restore control or an independently validated continuity mechanism must
close those gaps; otherwise retain the conservative physical refusal. Likewise,
fresh reads alone leave a commit gap before publication: the final fresh check
can define a linearization point, but a stronger current-at-publication claim
needs enforced writer serialization, including direct SQL. A service lock or
WAL read transaction alone is not that barrier. Review must fix this boundary
and test commits on both sides of publication before admitting reader changes.

### Discriminating checks required before admission — ALL UNRUN

| Synthetic case | Required observation |
| --- | --- |
| Delete/reinsert identical receipt; owner A→B→A; change then restore receipt data | Old proof stays invalid despite identical final rows. Both old/new owners advance; last-row deletion keeps a tombstone. Rollback preserves the old token; overflow refuses the write. |
| Other-owner INSERT/UPDATE/DELETE; no-op UPDATE, empty flush, delivery update and offset maintenance | Selected owner revision stays equal; qualified continuity preserves its proof. Same-owner receipt mutation invalidates it. Current physical-fence refusal is not a passing continuity result. |
| Raw SQL retention/deletion, legacy migration and capture replacement | Every affected owner advances atomically with its records. Failed migration leaves the prior usable schema/token or explicit refusal, never partially trusted authority. Rebuild/compaction changes epoch. |
| INSERT OR REPLACE, UPDATE OR REPLACE, OR IGNORE and UPSERT conflicts on `id` and `seq`, with recursive triggers ON and OFF | Conflicts abort without unnoticed deletion or partial authority change; explicit supported UPDATE/INSERT succeeds with correct old/new revisions. Outer conflict policies cannot suppress a failed revision bump or overflow guard. |
| Checkpoint, WAL TRUNCATE/removal, atomic DB replacement and same-inode backup restore carrying identical epoch/revisions | Proven neutral maintenance can retain authority; replacement/restore cannot reuse a prior proof. Unknown physical continuity refuses. Test equal size/mtime and a still-open old connection, not just a changed filename. |
| Missing/altered triggers, weakened uniqueness, metadata reset, drop/mutate/recreate schema | No accepted binding. A current-schema fingerprint alone is insufficient for the restored-schema case; failure to enforce this blocks physical-fence relaxation. |
| Commits before/during query, at rollback/close, between fresh check and publication, and during field await | Reads never mix snapshots or publish a stale currentness claim under the reviewed publication boundary. Changed authority refuses the affected page/chunk; unrelated-owner commits exercise continuity rather than a weaker fallback. |
| Missing-owner revision zero, later insertion; proof support eviction or canonical source/owner change with unchanged DB revision | Negative dependency becomes stale on insertion. Existing source/owner/support guards still refuse; unchanged counter cannot rescue a revoked reference. |

**Recommendation:** review this metadata-only, trigger-enforced design, but keep
the current physical fence until the continuity/publication boundary is resolved
and qualified. Set a bounded tombstone/epoch-compaction and deletion-privacy policy
before schema admission. No implementation, migration, database access, test or
runtime pass is claimed here; the exact cause of the observed stamp change remains
an investigation constraint, not a reason to weaken authority.
