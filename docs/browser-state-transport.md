# Browser state transport and idle work

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

Native history uses file notifications to invalidate the affected project.
Idle checks avoid rebuilding the catalog. Metadata/transcript changes and
directory creation or removal matter; runtime event logs and browser checkpoints
do not. A full stat reconciliation runs at least once per minute, manual refresh
forces reconciliation, and unavailable file watches fall back to stat discovery.
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
