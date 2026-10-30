# On-demand shared history directory

## Problem and acceptance

Native history is already canonical on disk, and transcript loading is demand
driven. The remaining startup bottleneck restores every cached native metadata
row, copies it into an in-memory catalog, and merges every row into application
state before filtering a navigation page. A warm library of 27,000 dormant chats
must instead decode only requested pages and the approximately six admitted
chats. Deferring the same complete restore into background batches does not meet
this requirement.

This implements the existing VISION principles of attention-owned working sets
and durable history without residency. It preserves the history.v1 ownership,
scoped identity, local presentation, deletion, and recovery contracts. The
directory is shared server infrastructure for all clients, not a browser cache.

## Ownership and invariants

- Canonical native metadata/transcripts continue to decide native existence,
  identity, parentage, and resume safety. The existing native catalog is a
  rebuildable indexed directory; a negative cache result is not canonical proof
  that a new session ID or deletion target is safe.
- Query and count operate on indexed scalar metadata before JSON decoding.
  Exact lookup is keyed by native project and native ID, with indexed aliases.
  Unscoped duplicates remain ambiguous regardless of which chats are resident.
- Local app records and presentation overrides remain durable in state_records.
  Materialization and eviction are not creation or deletion. A routine global
  save must never reconcile an incomplete working set as a complete library.
- Live sessions, pending operations, and connected client selections are admitted
  explicitly. Reading a summary never starts a model or replays execution.
- Native discovery updates affected rows and reports coverage/reconciliation
  explicitly. Warm startup does not decode all cached file signatures or rows.
- Durable GC roots include unmaterialized records. Existing explicit full-state,
  export, and recovery operations may enumerate the library deliberately; shell
  and ordinary refresh paths must not invoke that compatibility path.
- Ordering, search case folding/wildcards, archive/pin/status filtering, exact
  matching counts, parent scope, legacy aliases, and independent client views
  retain their current externally observable behavior.

## Implemented ownership boundaries

`NativeCatalog` stores typed identity, workspace, parent, name, activity-order,
availability and transcript-source columns with indexes for exact lookup, aliases
and navigation. A separate small source-facts table retains file signatures and
metadata-format validity without the cached metadata payloads.
`NativeHistory` restores project metadata and bounded requested/changed rows.
Warm startup does not restore the session JSON or file-signature catalog. Forced
refresh and watcher recovery advance bounded reconciliation; deliberately complete
legacy scans remain separate.

`HistoryDirectory` combines native facts, committed local metadata, current
admitted observations, suppression and read receipts before selecting a page.
Counts do not decode session JSON. Exact native and local alias lookup pushes its
predicate into indexed source branches; it does not first materialize the whole
union. Wildcard/filter queries can inspect matching scalar rows in SQLite, but
only their requested result page becomes Python session metadata. Canonical
parent links resolve the matching project and any durable app alias.

Visible-summary reads use batches of at most 128 exact public IDs. Indexed source
probes include the local and live owners of candidate native rows before applying
canonical overrides, suppression and the final exact-ID filter. Reading a native
alias therefore cannot resurrect an overlaid owner or resolve an ambiguous raw
ID as an exact public ID. Final rows retain directory order across batches and
only requested results are decoded. Child-history pages similarly probe indexed
parent candidates and then apply the existing canonical parent predicate; they
do not scan unrelated rows to count a selected parent's children. Explicit app
parent links, scoped native aliases and existing saved-parent fallback remain
unchanged. Both scopes use the same pinned connection for snapshot readers.

`state_records` keeps durable membership independent of the working set. Routine
saves update admitted keys and explicit global fields; an omitted owner is not a
deletion. Local pointers, directory projections and explicit deletions commit in
the same transaction. An uncustomized native owner can discard its local pointer
only while the native directory has its exact backing row; uncertain cache
availability retains a compact native reference. Explicit cleanup/reset/deletion
and backup enumerate their intended complete scope independently of residency.
GC continues to use complete durable ownership, never the in-memory working set.

Keyed admission restores one saved owner and its runtime pointers, applies the
existing restart normalization and ownership fences, and never replays execution.
Collaboration authority records carry a private host-incarnation marker, so a
prior-host generation and its legacy approvals retire on admission while
same-host readmission preserves them. Nonterminal generations remain resident.
Legacy cold execution compaction commits its new immutable body and exact owner
pointer together when admission owns the write; an existing caller transaction
defers that optional compaction without committing unrelated commands.
Startup admits selected and pending owners instead of the dormant library.
Sequential browsing retires idle committed owners after a bounded ownership proof.
Recent chats, connected selections, pending operations, running workers and mutable
aliases borrowed by callers stay resident. Retirement removes disposable caches
and leaves durable records intact; inability to prove exclusive ownership retains
the owner.

Navigation, workspace counts, conversation organization, history queries and client
observations read the shared directory. Observation cursors hold bounded WAL read
leases and decode small blocks from the pinned view; semantic generations exclude
unrelated transcript-only changes and working-set admission. Independent client
selection, Canvas and drafts remain valid when their owner is dormant.

Recall's explicit refresh/prune uses complete directory membership and detaches
one saved owner at a time without live admission. Scalar authorization preserves
dormant search matches; partial native discovery defers pruning and reports
`discoveryComplete: false`. Its existing full-text API still builds a scalar
allowed-ID set for an explicitly requested all-scope search. That can scale with
the requested scope, but does not decode library summaries/bodies or run during
ordinary browsing/startup. Dormant task facts use keyed saved runtime pointers.

Explicit workspace-file cleanup proves complete saved placement, including
dormant and hidden owners, before using the existing idle/transcript checks.
Unresolved placement or missing saved ownership fails closed; symlink aliases
cannot bypass overlap checks. Storage inspection reads managed-chat directory
summaries so dormant named chats retain their review destinations, without
admitting their histories. Progressive scan continuation reuses its own inputs.

## Qualification and limits

The dedicated native scale test seeds 27,000 rows and verifies zero session/file
JSON decoding on warm restore, six requested point decodes, and exactly 25 decodes
for a 25-row page. Whole-service acceptance seeds 26,994 native plus six active
local owners and verifies six resident owners through startup and repeated ordinary
refresh, exact counts, filtered pages, list without admission, and keyed selection
without runtime sends. Exact native and local/alias lookups additionally have
SQLite VM-work bounds to catch accidental full typed-row scans.

A separate reconciliation regression creates 27,000 actual canonical session
directories and a matching persisted directory. Its first warm reconciliation
and a later periodic pass decode zero cached session or source-metadata payloads;
after one missed rename, only that identity takes the canonical metadata path.
The same test checks 64-ID batches, bounded process-local file caching and scratch
generation cleanup. Controlled suspensions exercise watched changes to another
ID, concurrent deletion/count updates, incomplete traversal, cancellation,
denied absence probes and sessions-directory replacement. The additive cache
migration test forbids Python payload decoding and UUID recomputation, then
reopens with SQLite reads of both payload columns denied while repeated source
fact reads continue to work.

Navigation resolves workspace, pin and archive membership with per-query direct
lookups instead of joining each history row against a JSON registry. Scale
qualification combines 27,000 native rows, 187 local overrides and 1,750
workspaces. A one-entry semantic revision/count memo reuses unchanged results
across selection and text updates; native/local generations, exact query inputs
and database versions retain invalidation. Mutable transactions bypass reuse,
while read snapshots keep their pinned view. Counts and first-page filtering can
still inspect scalar directory rows; these are not constant-work queries.
The bound web display prepares admitted scalar overlays once within each
synchronous browser projection. Nested queries share that preparation only
until the projection returns or raises; no scope survives an event-loop yield.
The next frame observes in-place changes again. Different owner inputs, new
admissions and durable writes invalidate preparation within the frame, while
native/query generation checks remain active. Explicit hydrating session reads
and non-web/full projections retain ordinary fresh preparation. Read callbacks
must not mutate existing admitted rows inside a frame.
Dedicated exact-summary and child-scope tests compare 270 and 27,000 native rows
with local overlays, and separately 27,000 local owners. SQL instruction budgets
and poisoned unrelated payloads verify that small requested sets do not traverse
the unrelated catalog. The bounds depend on the requested IDs, relevant parent
membership and admitted owners; they do not make a parent's own large child
history or explicit all-library search constant work. The parent indexes are an
additive one-time scalar schema update and require no session-body migration.

Other focused cases cover scoped duplicate IDs, canonical rename precedence and
parentage, local overrides, first migration, rollback, dormant save/restart/GC,
borrowed aliases, cyclic-GC-independent retirement, connected and disconnected
clients, explicit backups/resets and stale indexed-pointer tokens. Exact-source
suite results and browser evidence are retained separately; a passing storage
scale test alone does not qualify the whole application.

The first migration may enumerate legacy saved metadata once. Native cache schema
migration likewise derives typed source stamps once from existing cached JSON.
After that migration, a healthy warm restart and periodic reconciliation of a
resolved project read only persisted scalar source facts and canonical file
signatures. They do not decode unchanged cached session or file metadata, rebuild
every project row, or retain a Python list of that project's sessions. Only a
changed identity takes the existing canonical metadata parser and summary path.

Warm reconciliation keeps at most 64 IDs/signatures and 64 issue details per
project in Python; a disposable SQL generation holds seen membership. A complete,
stable traversal is required before absent IDs enter the normal cache-deletion
journal. Concurrent watched writes are protected, uncertain filesystem probes
cannot prove deletion, and cancellation discards the incomplete generation. The
canonical root is checked independently of the periodic deadline: loss reports
incomplete discovery immediately while retaining known rows. Workspace-only
changes refresh just their admitted owners through keyed metadata reads, so an
open chat receives updated availability and read-only reasons without restoring
the workspace's dormant histories. The periodic sweep still probes filesystem
signatures across its requested canonical scope, cooperatively; it does not
promise constant total work for full discovery.

New or unresolved projects, and changed workspace-path interpretation, retain the
separate cold repair path. That path can temporarily accumulate one project's
scalar summaries while establishing its workspace and membership; it never reads
transcript/event bodies. Coverage remains explicit during this recovery, and a
failed cache retains uncertainty instead of advertising a complete empty library.
The existing background GC budget is a responsiveness safeguard, not incremental
root ownership: it can defer reclamation on large durable roots. Protected or
borrowed active owners can exceed the recent working-set target. Explicit complete
exports, destructive scope checks, and requested full observation traversal can
scale with their requested scope.

## Bounded JSON Pointer reads

Agent `get_state` and `GET /api/state/detail` resolve `/sessions` pages and
`/sessions/<index>/...` against the durable directory before loading any owner.
Indices use the same stable directory order as explicit full snapshots, never
the current resident list. A targeted settings or Canvas read does not assemble
the complete catalog. `/runtimeControl` pages and owner reads likewise include
dormant saved owners without admitting them.

Indexed responses and agent overviews return `directoryRevision` alongside the
application's existing integer `revision`. Follow-up `/sessions` reads that
specify `revision` must also include `directoryRevision`; stale directory tokens
are rejected before resolving the index. Native discovery can commit before the
event loop publishes a new application revision, so the integer alone cannot
certify an ordinal. Each operation derives its token and rows from the same
short-lived SQLite read snapshots and closes them on success or error. These
snapshots identify the observed local/native version pair, not a distributed
atomic commit. Reads without `revision` explicitly use the current directory.

## Background name discovery

Automatic naming still chooses at most 32 eligible metadata candidates per scan,
and at most two naming attempts, while the host is idle. Its catalog count and
ordered candidate selection run in a disposable read-only query process. A small
result alone did not bound the old synchronous SQL work on the event loop.

Only admitted scalar overlays and suppression/read receipts enter this worker;
it does not receive the workspace registry or conversation bodies. It cannot
admit a chat, invoke a provider, or write a name. The host rejects changed-source
results, repeats its idle/eligibility checks, and retains the existing policy and
attempt receipts. Cancellation and completion close the exact worker and spool.
Failed or uncertain reads defer to the next ordinary naming tick. Fixed-name
`naming.candidates.*` diagnostics expose read duration, decoded rows, SQL steps
and deferrals without recording conversation content.

Directory SQL reuses at most one scalar encoding and one full-summary encoding
of its private admitted overlay until that overlay changes. Metadata-only queries
omit retained bodies before binding SQL parameters; authorized detail reads still
receive the complete summary. Mutable application rows are prepared afresh at
their existing boundary; native generations, deletion rules and read receipts
are still checked for each read. This retains no per-page history cache.
