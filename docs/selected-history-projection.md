# Selected history projection

The native transcript and event logs remain authoritative. The host retains
command, runtime, permission and canonical-write authority. Selected Work is a
read-only, disposable projection; reading it cannot prepare or execute a runtime.
This implements the approved working-set plan incrementally, without importing
unselected historical sessions or changing search retention policy.

## Process and publication ownership

On qualified Linux hosts, the application starts one lazy fresh-interpreter
history worker. macOS and Windows retain the existing threaded reference reader
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

The worker returns a complete tree for a cold generation, a small changed-node
patch for a proven local update, or an unchanged receipt. The host rechecks source
identity and its exact input before accepting any result. A superseded result is
discarded. Retry, saturation, cancellation and worker failure preserve selection
and the last readable Work; retained interest retries without executing work or
turning a read failure into a conversation failure.

The host coalesces reads per canonical identity, admits at most eight active roots,
and checks unrelated warm roots without awaiting a cold projection. Selected roots
get admission first; other active roots rotate. Worker computation remains serial
within that bounded queue. Loading/retry/ready status is visible while existing
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

This slice still materializes a complete selected cold Work tree for existing
host summary/detail consumers. It avoids repeated descendant reconstruction for
recognized small appends and isolates uncertain/cold reconstruction. Fully paged
host projections and broader incremental event families remain follow-up work;
these bounds do not claim the plan's complete latency exits are met.
