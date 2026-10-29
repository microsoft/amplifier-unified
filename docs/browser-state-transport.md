# Browser state transport and idle work

Apply the [performance validation guidance](validation/performance.md) when changing
these projections. Measure construction and subscriber work as well as frame bytes;
a small delta can still conceal a full-history copy, scan or encoding pass.

## Revision-only observations

A delta may advance only the application revision when a committed change has
no other fields in this client's projection. It is still a causal watermark:
the browser validates the delta chain, updates current state readback, observes
navigation receipts, wakes command waiters and notifies state subscribers.
These frames are not dropped or replaced by a separate polling path.

When all non-revision top-level fields retain their exact immutable identities,
the bundled browser avoids repainting the unchanged application and scheduling
another rendered-view scan. New baselines, changed or removed fields and host
changes use normal rendering. The advanced agent-state panel also keeps normal
rendering because it displays the revision itself. Reconnect, optimistic drafts
and navigation, receipt ordering and hidden-tab recovery retain their owners.

## Saved task history

Bound browser frames keep the current task fields used by Work and runtime
panels, including objective, constraints, corrections, evidence and linked IDs.
They do not repeat recorded `changes`, internal `goalState`, `originalObjective`
or completed task bodies in both the session and `task.get` presentation. Counts
and existing `task.history` / `task.detail` references identify retained history.
Opening a panel does not automatically read those pages or start model work.

This projection runs before browser snapshot copying and serialization. It does
not alter canonical task/control records, persistence, command receipts, explicit
task reads or their revision/authorization checks. Currently rendered fields are
preserved in full; this is not a total-byte bound on the current task. The existing
bounded history/detail actions remain the complete historical read path.

## Selected media availability

The selected session in a bound web frame carries `linkedMediaAvailable`, an
indexed existence fact from the output registry. False means there are no linked
media records for that session, so inline media performs no metadata request and
releases any previous request or figures. Missing coverage remains pending.
True allows the existing visible-message lookup; it does not validate the origin,
grant access to bytes, or assert that every message has media.

This fact is recomputed from committed registry ownership when the selected
frame is projected, rather than inferred from `mediaRevision`. Forked and
imported records can exist at revision zero. The same indexed predicate follows
attach, unlink, relink, transfer, deletion and restart. Explicit media metadata,
download, playback and observation requests retain their canonical identity and
authorization checks. Images and other saved artifacts keep their own paths.

## Installed interface discovery

Bound web state baselines include `client.build` with `serverVersion` and
`frontend: {version, id}`, captured once from the serving host's package at
startup. `client.hostInstanceId` identifies that lifetime. Reconnect supplies
the current identity through the same state stream; unchanged deltas do not
repeat it. A validated baseline, rather than socket-open alone, suppresses
`build.json` requests. Focus and visibility do not poll a healthy connection.

This identifies the installed interface, not available published releases.
The server's configured release-check interval and explicit update actions are
unchanged. A different installed frontend offers the existing explicit reload,
after pending actions, Canvas edits and drafts are saved; a failed save prevents
reload. Discovery and the reload notice also work before shell startup completes.

If React starts but no valid baseline arrives within ten seconds, or an established
connection remains unavailable for sixty seconds, a visible tab may read the
small `build.json` fallback. Reads have a five-second deadline, a 1 KiB body
limit, one request at a time, and retry delays of sixty seconds, two minutes,
then five minutes. Hidden tabs stop reads; baseline recovery cancels pending
reads and fences late results. A known changed build stops further fallback
polling. This does not add a second stream or replay commands. Existing offline
entry recovery remains separate; a missing or malformed main JavaScript module
still requires browser/manual reload because it cannot run this React recovery.

## Shell bootstrap and active resources

The bundled browser gets shell descriptors once from `GET /api/shell?bundled=1`,
with its attached `clientId`, a positive safe-integer `demandId`, `scope`, and
`navigationResources`. Both arrays use JSON query encoding; resources have at
most 16 entries and 4,096 characters. The scope is exactly
`[selectedSessionId, selectedWorkspaceId, workSurface, workWorkspaceId, workWorkspaceTab]`;
the last two values are null outside the workspace surface. The host validates
the scope against that client's authoritative presentation before accepting it.
The response has `hostInstanceId`, `shellBootstrapKey`, `instanceGenerations`,
composition/package descriptors and the initial `shellResources` envelope.
Every bound web state frame advertises the descriptor key, even before resource
registration. The canonical `client.hostInstanceId` also fences old-host HTTP
completions, so a server restart initiates a fresh bootstrap rather than retaining
the previous host's descriptors.
It does not construct snapshots for built-in non-navigation controls: those
already consume ordinary selected browser state.

`POST /api/shell/resources` changes read interest with
`{clientId, demandId, resources, scope, reason}`. IDs increase within a document;
an older ID, or reuse with different scope/resources, is rejected. The response
contains the current bounded resource baseline. This operation cannot execute
or replay a command. Built-in Recent Retry uses `host.refreshResources()` to
read the same demand after uncertainty; it never replays the view write or issues
a complete `shell.query`. Hidden pages release navigation interest. A dropped
subscription can be registered again after reconnect without fetching descriptors
whose bootstrap key is unchanged.

Subsequent updates travel in `state.shellResources` through the existing
WebSocket/SSE state transport. Its complete bounded envelope is
`{hostInstanceId, clientId, demandId, bootstrapKey, scope, instances}`. Each
instance has a generation, monotonic `stateRevision`, a `pending` map, and named
`{revision, value}` resources. The existing
immutable state-delta baseline transmits changed branches only. Bounded queue
coalescing remains safe because retained frames represent the complete current
resource set, not an unacknowledged chain of partial shell events. Reconnect
supplies a fresh state baseline. Late HTTP responses cannot overwrite a newer
instance state revision, including equal-value pending-to-ready transitions.
Old host, module, demand or scope identities are rejected.

Page names are `chatNavigation`, `homeNavigation`, `pinnedNavigation`,
`recentNavigation`, `workspaceShortcuts`, `workspaceExplorer`, `sidebarSearch`,
`sidebarWorkspace`, `sidebarRecent`, `sidebarPinned`, and `workspaceOverview`.
The implicit `context` resource owns controller metadata and receipts for the
union of the active pages' visible rows. Page values contain only their fields;
`recentNavigation` additionally owns `recentShortcuts`, and
`workspaceShortcuts` owns inclusive, recency-ordered workspace shortcuts. Missing coverage means
loading, not an empty result. Changes to private module views have their own
monotonic `viewRevision`; the HTTP `result.shellView` receipt prevents an older
queued context frame from briefly restoring an acknowledged prior value.

The implicit context also carries exact `recentScope` controller identity while
its page is pending. A temporary missing page cannot change client, instance,
generation or canonical workspace identity. Recent expansion and explicit Retry
wait for the matching page through the existing stream; selection changes do not
replay the view write. Genuine query or host changes still fence the wait.

During a selection-demand transition the quiet sidebar may retain its previous
Pinned, workspace-shortcut and Recent rows as local reading state. This bounded
state is separate from `navigationResources`: it never grants current coverage,
satisfies a page wait, or fills main search/workspace results. Same-query rows
remain visibly updating so removing them cannot collapse the sidebar and reset
its scroll position. Changed host, bootstrap, module generation, effective pinned
workspace, filters or page identity discard incompatible reading state. Retained
pin rows remain selectable, but cannot start a reorder until current coverage
returns. A failed Recent read keeps its prior rows and Retry without also claiming
that it is still loading.

Current activity for visible, admitted chat owners travels separately as the
bounded context map `navigationActivityById`. The sidebar overlays those exact-ID
facts on retained page rows while membership, order and counts remain pending;
it does not query or hydrate dormant histories to refresh an icon. Removing a
context entry falls back to that page's activity, without borrowing another
owner's status. Workspace highlighting follows the current chat/workspace route,
not a remembered workspace browsing location from a different route.

The server reuses immutable resource values while semantic dependencies match.
Completed built-in resource envelopes also reuse their page/context assembly
when the current bound client's effective views, selection scope, descriptor,
semantic generations, committed global epochs, recent limits and resource budget
are unchanged. Effective views include initial preferences and selected resident
chat scope; a composition revision alone is not a view revision. Pending or
failed reads keep their completion/retry path, and custom modules retain their
existing snapshot contract. This reuse neither delays canonical writes nor
changes the returned resource revisions.
Workspace explorer and shortcut aggregates have a separate directory generation:
root membership, placement, stable recency, activity and notice/read facts can
change them; chat titles and child/internal progress cannot. Native membership
changes conservatively invalidate because duplicate identities can alter public
ownership. Workspace registry commits and pin preferences remain separate inputs.

Root chat pages likewise use their own semantic generation: root labels, filtering,
ordering, activity and matching notice receipts, plus conservative identity/role
membership changes that can affect duplicate aliases or suppression. Worker and
internal title, progress or notice updates do not rebuild root-only pages. Root
queries retain their exact scoped page and recent cursor until a true dependency
changes; committed registry, pin and organization epochs remain separate inputs.
Old query generations are retired within the existing bounded projection cache.
Child pages, custom snapshots and workspace overview keep their broader contracts.

Ordinary selection changes reuse Recent shortcuts and pinned page rows. Their
selection scope receipt is copied for the new owner; cached pages remain immutable.
An exact directory lookup preserves the selected agent-created root exception.
General and searched pages retain selection-dependent saved-page matching and
workspace rank inference. Workspace-scoped queries first select native candidates
by registered workspace ID or its canonical fallback path, then apply unchanged
local-owner overrides, duplicate identity rules, suppression and final filters.
Unrelated native workspaces do not participate in that candidate scan. Arbitrary
global filtered counts and first-page ordering can still scan eligible root facts;
the reuse and workspace indexes do not claim to eliminate that cold-query cost.

For opted-in bundled navigation, cold chat pages, workspace aggregates and mixed
pin queries run in one lazy, disposable read-only process. The host captures the
authorized scalar query and admitted scalar overlays; the worker opens its own
SQLite connections and pins the existing application/native catalogs. It runs the
same canonical SQL, including exact counts, aliases, suppression, ordering and
page selection. It does not construct an AppService, admit a session, open a
transcript, start a runtime or commit application data. Relevant root/workspace semantic generations,
application-connection external-write tokens, file identities and current read demand
must still agree before the host inserts a result into the existing bounded query
cache. Unrelated application/selection revisions and child transcript/progress changes
are not completion fences. Native cache changes use its canonical versioned
writers; file discovery/rename/removal is reconciled through those generations.
Unversioned direct SQL edits to the disposable native cache are not a supported
mutation path: repair must invalidate or rebuild it. Source-file replacement and
out-of-band application-database writes are still fenced.

Native query receipts separate root recency from structural root/workspace
changes. For at most 128 admitted owners, an exact project/native-ID lookup may
discount recency only when the canonical join freezes it: a web-owned overlay
or a busy native-managed overlay. Any additional stored alias that could expose
recency prevents that discount; more than 128 aliases is conservative as well.
Titles, naming policy, availability, membership, idle/dormant recency and local
status changes still invalidate. The page dependency and worker's pinned source
receipt use the same rule, so unchanged busy roots cannot repeatedly replace an
otherwise current pending query. An unaccounted raw native generation falls back
to broad invalidation and rotates the effective epoch on the next canonical save.
The two disposable scalar tables are populated only by changed metadata; startup
does not backfill the native library, and unchanged observation memo hits perform
no additional per-owner receipt reads.

A cold miss returns promptly with `pending[name] = {status, retryAfterMs}`.
Status is `pending` or `retry`. A prior value is retained only for the same
descriptor and normalized query scope, and is explicitly displayed as updating;
its counts are previous observations, not a completed current query. A changed
filter/workspace/page omits old coverage and displays loading. Controller metadata
stays immediate. Completion uses the existing client state notification, not a
new transport or command replay. A resource failure has bounded backoff and an
explicit read-only Retry; it cannot become an empty result or a busy retry loop.
A source-change rejection gets one delayed retry for the still-current read,
after one second. Another rejection requires explicit Retry. Replaced demand or
host close cancels that delay; no command is replayed. Ordinary frames do not
clear a failure when its cooldown expires. Only an explicit resource Retry/manual
POST (after its five-second cooldown), a changed query or the one source-change
retry can admit another read. An early explicit retry returns a bounded 429 error.
Each complete instance entry advances `stateRevision` when pending status changes,
even if the page value is equal. Late HTTP baselines therefore cannot restore a
retry marker after a newer streamed completion.

This process boundary is currently qualified on Linux. Other server platforms
keep the existing synchronous canonical reader until native process-policy
qualification is complete; they do not enter an unqualified retry loop.

The query owner admits at most eight coalesced jobs and one active process. Each
input is limited to 8 MiB (with bounded pre-copy scalar accounting), each result
to 4 MiB and transport spool to 64 MiB; the existing display transport bounds
worker address space/commit at 768 MiB. Workspace inputs contain only ID, path,
name, availability and archive facts;
archive membership and notice predicates exclude their payloads. SQLite
work has a 10-second cooperative deadline within a 12-second transport deadline.
Current clients refused by the queue cap receive an existing-stream wakeup when
a slot opens. Queued obsolete work is dropped. An active read with no remaining
subscriber is retired; one client's departure cannot cancel a shared reader. The process retires
after 30 idle seconds and is awaited on host shutdown. Canonical data and commands
have no lifetime dependency on this disposable process.

Explicit full reads and independent custom snapshots preserve their synchronous
contracts. This lane does not claim to move every snapshot operation: controller
metadata, bounded exact selection lookups, archived-ID checks, and workspace page
assembly remain on the host. Native discovery/reconciliation isolation is a
separate implementation step.

The shared workspace cache retains only its current aggregate/registry generation
within the existing bounded query cache. Old local public-fact rows derive the
additional dependency on keyed access, without a full-library migration read.
Canonical conditional global writes report the keys that actually changed;
their dependency epochs advance only after successful commit. Session facts
retain the directory's semantic revision authority. Ordinary telemetry, streamed
text, selected Canvas and host revision changes do not cause a descriptor GET.
Composition, module generation or host changes do. No global `shellDataKey` or
app-revision fallback drives the bundled client's reads.

Resource subscriptions are disposable and explicitly bounded: at most 32 clients,
4 MiB of encoded values per client and 32 MiB total. These are encoded-value limits,
not a Python heap guarantee. Disconnected clients can be retired after a 60-second
grace period; a durable saved client record alone does not retain read interest.
An oversized resource reports a bounded error without blocking unrelated chat
publication or retaining its payload. Unsent drafts and accepted work remain owned
by their original authorities when read caches are released.

Independent custom packages still receive their complete existing snapshot via
`customSnapshot`; explicit `shell.query`, `shell.inspect` and unbundled shell reads
retain their contracts. This is not compatibility negotiation for older bundled
clients: web and server adopt this protocol together. The separate AHP track is
unchanged. DOM diagnostics expose bounded reason labels, resource names, request
IDs, timings and numeric revision-change counts only, never titles, filters,
paths, prompts or drafts. The `data-amplifier-shell-diagnostics` attribute retains
at most 24 entries and 8 KiB. Its `resourceRevisions` totals distinguish accepted
envelopes from unchanged revision tuples, and changed context, page and custom
records by fixed resource name. They compare accepted metadata after stale-version
rejection, without inspecting values; replacement host/demand/scope/instance
identities count as removed and newly installed records. These are client receipt
counts, not wire frames, transferred bytes, payload equality or server builds.

## Selected browser context

Ordinary attached web snapshots and action receipts carry the selected chat's
context. The shell resource owner supplies requested navigation pages through
`shellResources`; ordinary state does not duplicate `chatNavigation`,
`headerChatNavigation`, or `workspaceExplorer` at the root. The bundled client and server
use this contract together, without an old-bundle fallback.

Every such snapshot carries sorted `browserResources` coverage containing
`selectedContext`. Only an open `view.panel = "subagent-history"` adds
`subagentNavigation` and its bounded child page. Its `scope` identifies the exact
requested `sessionId`, `filter`, and `index`; the page's own `index` is the
clamped result index. Missing or mismatched coverage means not loaded, never an
empty history. Closing the dialog retires its page from the next stream frame.

The selected summary supplies `subagentCount` when a visible control can use it:
Work & automation, its legacy runtime Direction view, the worker-history dialog,
or an open non-settings Canvas while viewing a chat. An open Canvas is treated
conservatively because its artifact views are projected separately. Ordinary
chat streaming with those controls closed omits the count and performs no child
history query. Opening a control reads current membership; omission is not an
empty history. The open worker page supplies its own unfiltered total; other
count consumers use the canonical indexed parent predicate without decoding a
child page. Explicit and independent-reader contracts retain their count.
A selected child's exact parent
summary remains available for Open parent. Selected messages, Work details,
approvals/questions, private drafts and Canvas, voice context, attention and
receipts keep their existing behavior. Shell descriptor and resource revisions
have separate owners. This slice does not suspend selected-chat history or
Work when another surface is open.

Selected-context frames retain `library.bounded`, `detailPath` and
`workspaceCount` (used by workspace-removal controls). They do not compute or
send the unused `library.sessionCount` or `continueSessionId`; selected roots,
worker views and New chat therefore do not count or search the dormant library
just to publish a frame. Exact totals and continuation metadata remain in the
existing explicit/general browser projections for independent readers.

Explicit complete-state reads, custom component context, unbound/integration
clients and independent single-session streams retain their existing contracts.
No omitted browser page changes durable membership, execution, or persistence.

## Existing stream and receipt behavior

Recent Work readiness notifies an attached, selected web reader as soon as its
state stream exists, including before shell-resource registration finishes.
That notification invalidates the reader's cached frame through the existing
loss-safe shell lane. It does not depend on an unrelated command or view report
to expose a completed read. Registered/current selected web readers retain the
same behavior; unrelated selections, dormant client records and non-web clients
do not gain a notification or a new display read.

Browsers opt into compact action receipts with `X-Amplifier-State-Transport:
delta-v1`. Current hosts advertise `websocket` at client attachment; browsers
connect to `/api/events/ws?transport=delta-v1` with their attached `clientId`.
Older clients and hosts retain `/api/events?transport=delta-v1` SSE. Actions retain their
result, effects, idempotency receipt and revision. `stateRevision` and
`hostInstanceId` identify the authoritative state that must arrive before an
optimistic control settles. Shell composition receipts use a separate revision
domain; the bundled stream carries the descriptor key and resource generations.

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

Native refresh updates dormant local overlays directly. Admitted owners reconcile
native metadata through the live history merge and persist once through their
normal save, preserving parent aliases and stable busy-chat navigation activity.
Detached refresh must not temporarily overwrite the same admitted owner and
create semantic navigation revisions for values its live save will restore.
Canonical removals still mark both dormant and admitted saved overlays unavailable.

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

Operation labels are fixed in code; there are at most 64 timing and counter
labels each. Slow spans record wall time, and synchronous spans also record
thread CPU time. Asynchronous lock/reader spans include waiting and scheduling.
Event-index counters include bytes read from canonical logs, checkpoint hits
and misses, RAM hits, evictions, and unchanged projection reuse.
The 200 ms event-loop heartbeat records excess delays of at least 100 ms.
No prompts, request bodies, credentials, query strings, filesystem paths or
stack locals are collected. Records expire by count and disappear on restart.
Optional stack capture remains owned by the existing opt-in profiling facility.

`shellResourceRebuilds` separately counts builtin navigation page rebuild attempts and
fixed invalidation reasons, retaining the last 60 decisions. Each decision names
the resource and builtin package, changed key components (initial, bootstrap,
native/local/overlay navigation, attention, global, workspace, selection, scope
or query), and changed global owner names. It never includes key values, query
text, selected identities or page contents. A reason is evidence that a cache
dependency changed, not proof that the resulting page differed or that the
dependency was necessary. Attempts are recorded before building, including failures;
`initial` means no prior key, including new demand or an instance generation reset.
Unchanged pages add no records. These counters do not
consume the generic counter-label budget and also disappear on restart.

`Server-Timing: app` and `X-Amplifier-Request-Id` now cover `/build.json` as well
as API responses; request records include handler start and completion times.
The handler timer does not include time before the event loop admitted the
request, nor static-body transfer. A short handler time does not exonerate the
server: compare it with the loop-delay samples and client TTFB. Host sleep and
CPU scheduling can also create loop delay, so a delay alone is not attribution.
Inclusive nested operations overlap and their durations must not be summed.
