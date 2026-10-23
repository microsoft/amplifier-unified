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
remains serial within the bounded queue. Initial empty loading shows progress;
routine refreshes keep readable conversation, local outgoing text, streaming replies
and retained Work in place without inserting a loading row. Explicit earlier-history
reads retain their paging feedback. Retry and unavailable notices remain visible,
and the DOM receipt continues to distinguish pending Work from a current result.
Transient retries back off 3–30 seconds.
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
