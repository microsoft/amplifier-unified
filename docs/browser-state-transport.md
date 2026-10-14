# Browser state transport and idle work

## Shell navigation resources

The bundled browser must send `navigationResources` to `GET /api/shell` as a JSON
array of resource names (at most 16 entries and 4,096 characters). The allowed
names are `chatNavigation`, `homeNavigation`, `pinnedNavigation`,
`recentNavigation`, `workspaceShortcuts`, `workspaceExplorer`, `sidebarSearch`,
`sidebarWorkspace`, `sidebarRecent`, `sidebarPinned`, and `workspaceOverview`.
The host rejects missing or invalid demand before constructing projections.
Resource selection applies to the built-in Chats and Workspaces snapshots; custom
navigation packages and explicit `shell.query`/`shell.inspect` reads keep their
complete existing snapshots. `compact=1` changes wire encoding, not demand. The
bundled browser and server ship together and use this contract directly; no
old-bundle fallback or capability negotiation is required. Independently released
clients and custom packages remain governed by their explicit contracts.

Built-in browser snapshots contain `navigationResources`, the supplied resource names.
An omitted resource was not requested; it is not an empty result. Base instance
generation, view/controller metadata, selected registration and permitted actions
remain available. `recentNavigation` also supplies `recentShortcuts` and
`recentScope`; `workspaceShortcuts` supplies pinned-excluding shortcuts too.
Sidebar page names map to the corresponding `sidebarNavigation` fields. Attention
receipts and organization summaries cover the requested visible rows.

The browser asks for sidebar pins, recent shortcuts and workspace shortcuts,
then adds search, workspace chats or the workspace explorer when that surface
needs it. Demand and workspace tab participate in request identity. Superseded
responses cannot populate a newly selected surface; absent/mismatched pages show
loading until their matching resource arrives. Retained controller/draft state
does not require constructing a hidden workspace page. Releasing read demand
does not cancel accepted work or replay commands. This changes the current bundled navigation transport; the separate AHP
development track remains unchanged.

## Selected browser context

Ordinary attached web snapshots and action receipts carry the selected chat's
context. `/api/shell` owns chat/header/sidebar and workspace navigation pages;
the stream does not construct or include `chatNavigation`,
`headerChatNavigation`, or `workspaceExplorer`. The bundled client and server
use this contract together, without an old-bundle fallback.

Every such snapshot carries sorted `browserResources` coverage containing
`selectedContext`. Only an open `view.panel = "subagent-history"` adds
`subagentNavigation` and its bounded child page. Its `scope` identifies the exact
requested `sessionId`, `filter`, and `index`; the page's own `index` is the
clamped result index. Missing or mismatched coverage means not loaded, never an
empty history. Closing the dialog retires its page from the next stream frame.

The selected summary retains `subagentCount`, obtained by the canonical indexed
parent predicate without decoding a child page. A selected child's exact parent
summary remains available for Open parent. Selected messages, Work details,
approvals/questions, private drafts and Canvas, voice context, attention and
receipts keep their existing behavior. Shell changes still use their existing
change token and data key. This slice does not suspend selected-chat history or
Work when another surface is open.

Explicit complete-state reads, custom component context, unbound/integration
clients and independent single-session streams retain their existing contracts.
No omitted browser page changes durable membership, execution, or persistence.

## Existing stream and receipt behavior

Browsers opt into compact action receipts with `X-Amplifier-State-Transport:
delta-v1`. Current hosts advertise `websocket` at client attachment; browsers
connect to `/api/events/ws?transport=delta-v1` with their attached `clientId`.
Older clients and hosts retain `/api/events?transport=delta-v1` SSE. Actions retain their
result, effects, idempotency receipt and revision. `stateRevision` and
`hostInstanceId` identify the authoritative state that must arrive before an
optimistic control settles. Shell composition receipts use a separate revision
domain and continue to synchronize through shell events.

Each event stream starts with a complete `state` frame. Later `state-delta`
frames contain a base revision, result revision and changed JSON paths. An array
is replaced when its length or record identities change. The browser applies
patches immutably, keeping references to unchanged branches. A reconnect always
starts with a complete snapshot; a mismatched baseline starts a fresh stream.
If an action's state does not arrive, waiting actions share one recovery fetch
after 1.5 seconds. This does not introduce a polling loop.

The stream has its own baseline: an independently fetched HTTP snapshot may be
ahead of it. Full snapshots remain the default for older clients and agent
callers. Historical retired usage records remain in authoritative storage;
browser projections retain aggregate accounting and omit that internal ledger.

Repeated identical view, visibility and Canvas mount reports acknowledge the
command without republishing the conversation catalog. Canvas renderer evidence
still validates the exact client, generation, resource and edit order. It does
not save unrelated conversation history.

Appearance controls paint an optimistic patch immediately. Saves are serialized
with revision checks; a failed save removes only that edit, preserving any newer
pending choice. Authoritative execution and agent actions keep their existing
admission and confirmation behavior.

Native history uses file notifications to invalidate affected sessions/projects.
Empty metadata deltas avoid rebuilding the catalog or publishing unchanged state.
Metadata/transcript changes and directory creation or removal matter; runtime
event logs and browser checkpoints do not. Cold metadata discovery, including
startup without a usable cache, advances in slices of at most 64 generator steps
with a cooperative 25 ms budget, normally once per second. Individual filesystem
operations can exceed that budget. Session-specific notifications are serviced
before cold work; project hints join the recovery queue at directory boundaries.
Pending notifications wake follow-up work after 100 ms. Selected/subscribed
transcript checks and fresh canonical execution admission remain independent of
cold discovery.

After a completed pass with working notifications, cold reconciliation is due
again after 15 minutes. Without notifications, or after an unavailable pass, a new
bounded pass is due after 60 seconds; losing a working watch shortens any later
deadline. Neither failure restores eager whole-library background scans. Manual
refresh requests a new bounded reconciliation pass. Discovery exposes its actual phase and last
completed time; partial coverage is not advertised as complete and there is no
fixed whole-library completion deadline. Diagnostic counters distinguish empty
deltas, skipped/full/delta merges, metadata reads and actual recovery steps.
Activity projections independently avoid rereading unchanged event files and
rebuild when a canonical association or lifecycle input, file identity, size,
timestamp or child changes. Drafts, streamed text and worker progress labels do
not invalidate the canonical activity cache. Transcript changes are checked as
well as event-file changes.

Tool and worker progress share the 250 ms publication batch used by streamed
text. Delivery acknowledgments, messages, approvals, errors and lifecycle
completion still publish immediately. Diagnostics capture continues normally,
but record counts and oldest/newest timestamps publish in the background only
while a connected client is inspecting Diagnostics. Errors and dropped-record
changes are still published when that panel is closed.

Publication still checks every saved generation for canonical changes. Root
classification and root counts are reused from the generation's SessionIndex;
this does not allow caches to survive a save with changed semantics. Empty
question/worktree projections retain independent per-session lists instead of
allocating new empty views. Worktree source files are reread and grouped once per
sync, and attention counts group unread outcomes by workspace once rather than
scanning all outcomes for each registration. No canonical event/transcript,
receipt durability, freshness interval, ownership fence or publication boundary
is weakened by these CPU-path changes.

## Durable directory and admitted owners

The shared server keeps the complete native/local history directory in SQLite.
Warm startup reads only global records and selected or pending owners; it does
not restore every cached session or file signature. Native discovery writes
changed metadata into indexed scalar rows. Navigation, counts, archive/search
filters and public observation pages query those facts before decoding the
requested metadata page. They do not admit a runtime or transcript.

Application `sessions` memory is an admitted working set, not proof of complete
membership. Exact scoped lookup resolves dormant owners from the directory and
restores their committed presentation/runtime record when detail or execution
actually needs it. Client selection and Canvas pointers survive non-residency.
An ambiguous raw native ID stays ambiguous even when only one project is loaded.

Ordinary global/scoped saves preserve all unmaterialized durable records.
Explicit deletion/reset records the exact local owners it removes. Garbage
collection still enumerates complete durable roots, including dormant records;
cache eviction has no deletion authority. Idle metadata retirement retains
connected selections, active and pending work, recently used owners and borrowed
mutable aliases. It removes only disposable memory/cache entries after a keyed
commit; uncertain ownership remains resident. Retirement probes and ownership
checks have cooperative budgets rather than an unbounded publication walk.

Explicit legacy full-state reads, recovery backup and export may deliberately
enumerate saved records. Ordinary JSON Pointer reads page the directory before
loading the requested owners. Indexed `/sessions` reads carry `directoryRevision`
alongside the app `revision` when the caller requires a stable index; see the
[Canvas state access contract](canvas.md). The browser shell and ordinary metadata
refresh must never invoke those complete reads. The same directory serves all
clients; it is not a browser-specific cache. Native-only equal-sort ties use
stable project/native identity order, while migrated local overlays preserve
their prior saved order.

## Acceptance

### Demand-scoped model and worker data

Catalogs are rebuildable caches, not historical evidence. The public catalog
collection retains exact keys needed by connected selections/session streams,
running chats, the current setup inventory and the incoming catalog result.
Pruning occurs only when catalogs are published, not on ordinary progress ticks.
Dormant references may miss: closed controls retain the chat's own selection;
opening the picker resolves current configuration rather than reviving an old
account's catalog. Full-state readers also observe this derived-cache eviction.
The private discovery cache retains at most 128 identities and 3.9 MB of encoded
entries, including failed probes, with a 24-hour maximum age checked on access
and publication. Its age limit is retention, separate from freshness/refresh.
Oversized results reach their requesting caller but are not cached. These bounds
cover derived data, not Python heap overhead or in-flight caller-owned results;
eviction never changes accepted model selections or native history.

Browser model options are requested by an open picker/settings surface, not by
an optimistic navigation placeholder. Closed controls receive only the selected
connection's display metadata. Exact connection keys and configuration revisions
remain separate from model-list content equality.
Explicit refresh bypasses the private cache's 60-second failure backoff.
Fresh options replace the browser fallback even at the same configuration
revision, including a successful empty list; saved/default selections remain
visible independently of available options.

Worker observations remain in the existing durable store. Browser summaries keep
raw nonterminal rows and their terminal job overrides, recent finished rows and
the identities used by visible recovery notices. Legacy timeline fallback keeps
its full compact rows. Older message pages carry recovery identity independently
of the current worker window; full worker text/rows are explicit detail reads.
Counts describe observations, not an invented count of unique agents.

Qualification measures navigation requests/publications separately from full
snapshot/reconnect bytes. A small SSE delta does not prove cheap construction.
It must also exercise model-picker/account changes, two private clients, older
recovery pages and unchanged-message rendering. No synthetic timing is a claim
that the production host's entire navigation delay has been explained.

`npm run test:state-transport-browser` starts a disposable real HTTP/WebSocket server
and serves the production build. Its synthetic catalog and initial state exceed
3 MB. It measures actual action and stream bytes, checks immediate painting with
500 ms delayed writes, verifies failed-save rollback and reconnects a fresh
browser. It makes no model calls. This is a local browser acceptance gate, not a
claim about a production host's observed CPU or installed build.

The Python transport tests cover a real event stream, duplicate commands and
legacy full-state clients. Native history tests cover real file notifications,
watch failure, late metadata, external workers and missed-event reconciliation.
Event-log tests cover idle reuse, append and atomic replacement. The complete
usage ledger remains intact in the projection regression test.

## Unchanged conversation rendering

Keep immutable browser-branch identity through message annotations and paging
overlays. Memoized transcript derivations depend on their actual inputs, not a
global state revision. The elapsed activity clock belongs to its leaf component.
Message controls still receive current permissions, delivery and edit state;
only the text body is memoized, with text, detail reference, role, chat/message
identity, workspace and action explicitly passed. Never compare only message IDs
or text: that would retain stale file actions or detail references.

`npm run test:message-render-stability-browser` uses the real disposable service
and a test-instrumented Vite client. It counts actual Markdown processor visits,
drives three activity ticks, and checks draft edits, changed text, streaming,
editing, older paging and cross-chat drafts. It requires zero unchanged-message
parses on ticks/draft updates and zero parent renders on ticks. `--measure-only`
records the same workload on a baseline without asserting those new limits.
This is work-count evidence, not a production CPU percentage or latency claim.
The unit suite also checks loaded-detail retention and rejection of late results.

Source changes must include the regenerated `amplifier_web/static` assets.
The integration workflow rebuilds the client and requires a clean
`git diff --exit-code -- amplifier_web/static`; a passing build alone does not
prove that the committed application serves the changed source. Verify a repeat
build reproduces the committed assets, without editing generated bundles by hand.

## Multiple browser windows

Each page retains its own private presentation identity. Its live observations
use a WebSocket so long-lived streams do not exhaust the HTTP/1.1 connection
pool shared by tabs and installed app windows. Frames carry the existing event
name and data; snapshots, delta revision fences, shell events and bounded
subscriber coalescing retain the same owners and semantics. Commands still use
HTTP with existing receipts. The socket rejects application messages and never
starts workers, retries model requests or replays commands.

The handshake passes the same Host, Origin and authentication gates as HTTP and
requires an attached client. Transport close releases the subscriber promptly.
The page's connection policy explicitly names only its validated socket authority
for browsers that do not include WebSocket schemes in CSP `self`.
Reconnect uses bounded backoff and a fresh snapshot, preserving private drafts
and mounted Canvas state. SSE is a compatibility path for hosts that do not
advertise WebSocket support, not an automatic response to a broken socket.

### Recent history and scoped execution updates

The normal working set is the few actively viewed or running chats, not the
entire saved catalog. Historical existence does not authorize background history
loading, worker startup, prefetch or execution reconstruction. Catalog metadata
and explicit search/paging remain separate from conversation payloads. A test
with 23,128 catalog entries verifies that repeated demand selection visits only
subscriptions and running work after the catalog index is built.

Completed model/tool events still commit before acknowledgment. When a scoped
save proves shared navigation and attention facts unchanged, it retains the
catalog and unrelated client snapshots. Preparation/loading facts participate
in that comparison; a changed status, attention fact or global scope broadens
publication. This is a semantic guard, not a time-based cache.

Work-group membership remains metadata over the loaded execution view. Expensive
usage summaries and nested detail fingerprints are evaluated after selecting the
requested group page or explicitly opened group/window. Older tool bodies are
not examined to construct the newest hundred summaries. The current native
reader still reconstructs loaded execution metadata; this change does not claim
fully disk-paged execution reconstruction.
The remaining legacy event-to-turn fallback indexes eligible message timestamps
and saved turn anchors once per requested reconstruction. It preserves message
presentation order (including unsorted timestamps) and the first saved matching
turn; these indexes do not establish call identity or accounting authority.

When a requested native index exceeds the 64 MiB RAM working-set budget or is
evicted, a disposable checkpoint under `cache/event-index-v1` preserves parsed
descriptors and byte references. A later read validates canonical file identity,
size, timestamps and the indexed tail before resuming at the saved byte offset.
Checkpoints are created by requested reads, never by sweeping dormant histories.
Raw provider request/response bodies remain solely in their existing recordings;
exact detail access still checks the referenced bytes against their digest.
Descriptors may contain existing bounded tool previews; prompts contribute only
association fingerprints. Cache files have private permissions and use JSON,
not executable serialization.

Checkpoint limits are 64 MiB decoded, 16 MiB compressed per file, 256 MiB and
1,024 files in total, retired oldest first. The count allows referenced child
logs of a few active root chats; it is not a preload target. Missing, invalid,
incompatible or unwritable caches fall back to canonical reads without losing
history or replaying work. Source replacement/truncation rebuilds the index.
The first uncached read and reconstruction of metadata for the requested chat
can still be proportional to that chat's retained events; recent-page summaries
do not claim that remaining cost has been removed.

### Bounded performance diagnostics

Authenticated clients may read `GET /api/diagnostics/performance`. It returns
schema version 1, host instance and application version, inclusive operation
counts/total/max milliseconds, bounded counters, the last 60 slow operations,
last 60 event-loop delays and recent request timing metadata. It does not build
an application snapshot, publish state, start workers or start a profile.
`session.inspect` includes the same process-local performance snapshot in its
health diagnostics so Copy diagnostics can carry this evidence.

Operation labels are fixed in code; there are at most 32 timing and counter
labels each. Slow spans record wall time, and synchronous spans also record
thread CPU time. Asynchronous lock/reader spans include waiting and scheduling.
Event-index counters include bytes read from canonical logs, checkpoint hits
and misses, RAM hits, evictions, and unchanged projection reuse.
The 200 ms event-loop heartbeat records excess delays of at least 100 ms.
No prompts, request bodies, credentials, query strings, filesystem paths or
stack locals are collected. Records expire by count and disappear on restart.
Optional stack capture remains owned by the existing opt-in profiling facility.

`Server-Timing: app` and `X-Amplifier-Request-Id` now cover `/build.json` as well
as API responses; request records include handler start and completion times.
The handler timer does not include time before the event loop admitted the
request, nor static-body transfer. A short handler time does not exonerate the
server: compare it with the loop-delay samples and client TTFB. Host sleep and
CPU scheduling can also create loop delay, so a delay alone is not attribution.
Inclusive nested operations overlap and their durations must not be summed.
