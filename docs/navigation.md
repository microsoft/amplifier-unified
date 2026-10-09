# Workspace and chat navigation

The header keeps the chat title visible at every width. At 760 px and below,
chat uses the full screen width; the navigation icon opens a full-screen modal
with Close, Escape, and trapped keyboard focus. Navigation fills the viewport,
including narrow folded-phone displays, and respects display safe areas.
Selecting a chat or New chat returns to the conversation. Desktop pin/width
preferences survive resizing.
Workspace browsing stays in the drawer. Tap **…** for a details sheet with the
full title, canonical session ID, path, and actions.

**Canvas** is an icon beside More in the top toolbar. On phones, it fills the
content area and offers **Back to chat**; the chat's scroll and draft stay intact.
On larger screens it docks when the chat and panel have usable widths.
Visibility paints immediately through `canvas.visibility {open,sessionId,canvasId}`;
it has its own ordered queue and no busy spinner. The host persists only client
presentation, using cached catalog projections when available. It does not scan
workspaces or rewrite chat history and artifact catalogs. Cold source recovery
can still load inside the already-open panel.

Hiding retains the current viewer slots, including local edits and iframe state.
It does not initialize newly selected hidden artifacts. Dirty editors still block
replacement, tab closure, or navigation that would discard their input, including
while hidden. Legacy `canvas.close`/`canvas.reopen` retain their previous lifecycle
semantics for older clients. Agents use the same visibility action, optionally
with an explicit attached `clientId`; session and artifact bindings reject stale
targets. Hidden content is excluded from visible-control observations.

Below 1025 px, **More app options** includes chat details/export, Activity, and
Settings, alongside appearance, agent view, and feedback. On phones it is a modal
sheet. Escape, Close, and outside clicks dismiss it and return focus. Attention
appears on More and the relevant destination. Agents can control the disclosure
with `view.update {patch:{toolbarMenuOpen:true}}`, or open a destination via `panel`.

Default UI typography uses 14 px normal text, 12–13 px supporting labels, and
16 px larger text. Mobile editable fields are at least 16 px. The tokens
`--a-text-xs`, `--a-text-sm`, `--a-text-base`, and `--a-text-lg` let skins adjust this
scale without zooming layouts or icons. Only exact unmodified historical default
skins upgrade automatically; edited or renamed skins retain their saved CSS.

The default sidebar has three independently collapsible sections: **Pinned**,
**Workspaces**, and **Recent**. There is one pinned-chat control and one workspace
explorer. Pins keep the same title, workspace subtitle, activity time, and actions
while browsing folders or filtering chats. Quiet Recent initially shows **20
eligible unpinned chats**, not messages. Downward user scrolling within 120 px
of the list end adds 20 summaries at a time, including beyond 100, until actual
exhaustion. **Load older chats** is a quiet keyboard/screen-reader fallback
while older rows remain, including a tall viewport with no overflow. At
exhaustion the last chat ends the list: no count or All/View/Review chats footer.
**Search chats** still opens the full browser. Pins have their own bounded
pages and never duplicate an ID in Recent.

The initial 20–100 limit and origin toggle persist per client and mounted module.
Expansion beyond 100 is scoped to the attached client/module session, not copied
to a reload or another client. Reload uses the saved bounded initial preference.
Eligibility (including the current-root exception) is applied before slicing;
total and remaining use that same eligible population. Toggling origin retains
the loaded limit. Shrinking the population retains the saved limit while
deterministically reducing rows/counts. Requested limits remain multiples of 20:
requesting 120 with only 101 eligible chats returns 101 rows, limit 120 and zero
remaining. There is no mount, resize, SSE, section-open or recursive fill.
Hidden, inert and collapsed Recent do not load. Programmatic scroll restoration
and append anchoring cannot trigger another page. Paging reads summaries only,
not transcripts or offscreen title previews; it never creates a task.
Ordinary control/read errors keep the previous rows and offer **Retry**.
Retry performs the read-only `shell.query` action; it never automatically replays
an unknown presentation write or any task/input action. A host-owned module
view revision and query scope reject stale responses; controls and a surviving
scroll anchor are retained while rows change. Progress retains the existing
stable navigation recency until the ready/attention boundary.

The full All chats browser retains its existing filters and `navChatPage`;
legacy Recent retains independent filters and 50-row/40-row paging. Quiet Recent
does not share either counter, search, or sort setting. It uses activity ordering
across available workspaces and managed chats.

Workspaces uses the recent-workspace index and existing folder browser, with an
eight-workspace preview and **More workspaces** in the recent index. Available
registered folders appear even before their first chat; worker sessions never
become top-level chats. Selecting a
workspace opens its full chat list inside that section; **All workspaces** returns
to the index. Recent remains independently available. Search is visible; archive,
sort, location, and activity controls are under **Filters**. Collapse, filters,
and pages persist per client. The former simple view and Workspaces/All chats
switch are replaced by these shared sections.
Browsing folders, searching, filtering, and opening details never mount a runtime,
send a message, change recency, or acknowledge an unread response.

Recent hides agent-created independent roots by default. **Show agent-created**
reveals them for this client's mounted chat-list instance; reload retains that
instance's choice. Only the host-retained `collaboration.creatorSessionId` and
`requestId` together establish this origin. Navigation exposes an `agentCreated`
boolean, never the creation brief or grant. Missing/legacy evidence and human
fork lineage remain visible; workers and internal sessions retain their existing
classification and exclusion. All chats, full history and direct access remain
available regardless of this toggle. Pins stay in Pinned without duplicates.
The currently open commissioned root is eligible but receives no forced insertion
or displacement in a recency slice. Browsing never wakes or changes its task.
For example, an eligible current commissioned root ranked 25 remains open but
absent from Recent 20; it appears after downward loading or the fallback to 40.

### Isolated regression checks for deterministic Recent

Run these checks serially in an isolated test environment against the installed
application package and its built frontend. These instructions do not establish
that checks have run. Do not use a production service or model/SDK runtime.

1. Run the owned backend regressions:
   ```sh
   "$AMPLIFIER_TEST_PYTHON" -m pytest -q tests/test_chat_navigation.py tests/test_approachable_shell.py tests/test_live_clients.py
   ```
2. Run the frontend unit source:
   ```sh
   cd frontend
   node --test tests/work-shell.test.mjs
   ```
3. Run the new real HTTP/SSE receiving-build harness:
   ```sh
   AMPLIFIER_RECENT_EXPECTED_PACKAGE="$INSTALLED_AMPLIFIER_WEB_PACKAGE" AMPLIFIER_RECENT_EVIDENCE=/test-evidence/recent.json node tests/recent-visibility-browser.mjs
   ```
   Set `INSTALLED_AMPLIFIER_WEB_PACKAGE` to the interpreter's actual installed
   `amplifier_web` directory. The fixture rejects a different import and records
   the resolved module path and Python executable. It does not rewrite import
   paths to a checkout. It uses one synthetic backend fixture and one browser context, no SDK/model
   runtime. Raw action receipts, DOM, screenshot and JUnit are retained beside
   the evidence path. Require 20→40→60→80→100→120→actual exhaustion; toggle at
   20 and 100; current rank 25 and distinct pins; read failures/explicit Retry;
   unknown-write reconciliation without replay; two clients/two modules/reload;
   keyboard focus, 44 px fallback, narrow/coarse touch, count shrink and progress
   anchor without programmatic extra loads; no mount/resize/hidden/inert/collapsed
   fill; bounded reload after expansion; and
   unchanged draft, attachments, Canvas, model/bundle and saved message under
   passive browsing. Fixture progress/settlement is a separate, synthetic event.
4. Keep syntax/AST/diff results separate from these unrun checks. Older browser
   fixtures which assert an eight-row Quiet Recent cutoff describe the prior
   presentation, not this requested 20-row contract. Their recency/admission
   invariants still need independent qualification; do not claim they passed
   unchanged or weaken those invariants to accept this source.

Compact workspace rows reserve a **New chat** icon immediately beside **…**.
Hover or keyboard focus reveals both; touch keeps New chat visible with a 44 px
target. It opens the existing unsent-chat setup for that row's full folder path,
not the active chat's folder or a globally selected workspace. The shared
`session.draft {workspace,workspaceId,location:{kind:"workspace"}}` action rejects
stale, invalid or unavailable registrations before changing the draft. Opening
setup creates neither a chat nor a model turn. Returning to the original chat
restores its text, attachments and saved Canvas; another client's chat is unchanged.

Workspace details offer **Pin workspace** / **Unpin workspace** independently of
chat pins. Pinned workspaces lead the recent index in pin order, followed by
unpinned recents without duplicate folder rows. Empty workspaces can be pinned.
**Move up** / **Move down** reorder pins in the details flyout. Agents use the
same `workspace.pin {id,pinned}` and `workspace.pinOrder {ids}` actions; ordering
requires every currently pinned ID exactly once, including unavailable folders.
`shell.query.pinnedWorkspaceIds` is the complete vector, not just the visible
shortcut page. The compact sidebar keeps its six-row limit; the full workspace
index retains its bounded pages. Search and folder browsing remain available.

These are durable app preferences stored with the existing state records,
checkpoint/export and SQLite backup lifecycle, not shared conversation files or
a new database. Pinning, unpinning and ordering do not select a workspace/chat,
change another client's filters/pages, acknowledge activity or start execution.
Renaming keeps the registration ID and its pin position. A missing folder stays
hidden but retains its pin, so it returns at the same position when available.
Explicitly removing a registration prunes its pin through the existing remove
action; it does not delete the folder or its saved chats. Pin actions themselves
do not remove registrations or hide history.

**Sort** offers **Recent activity**, **Newest created**, and **Name**. Recent
activity keeps a chat in place while it streams or runs tools, then advances it
when the turn ends or needs approval. Live working/error indicators still update.
The separate navigation timestamp is saved across reloads; history and diagnostics
retain the actual progress timestamps. Imported idle history retains its saved
activity time. Each chat-list instance stores its own sort choice.

Pins always follow the order in which they were pinned, independent of sorting.
Drag a pin's grip to reorder it, focus the grip and press Up/Down (or Alt+Up/Down), or use
**Move up** / **Move down** in its details. Reordering preserves pins outside the
current search, workspace, or page. Agents use the same `session.pinOrder` action
with the complete `pinnedSessionIds` vector returned by `shell.query`.
Pins and Settings orders use the same `ReorderList` pointer/keyboard control:
full row preview, placeholder, insertion marker, scroll assistance, focus return,
and cancellation with Escape, pointer cancellation, or an outside drop. A concurrent
order change cancels a held drag. Pins persist on drop; Settings preserves its
draft-only preview and explicit Save/Cancel. A pending pin receipt blocks duplicate
moves; failure restores the saved order and exposes a retryable error.

Rows reserve their action and activity space. Names and parent labels truncate
within a fixed 60 px row (52 px with the existing compact shell density). Hovering
for 550 ms opens details without changing the row geometry. The full title,
selectable/copyable path, session ID, and pin/rename/remove actions are also
available through the keyboard- and touch-accessible **…** button. Escape and
outside clicks close the flyout; explicit open/close restores focus. A short
pointer grace period lets people move from a row into its details. Saved skins
continue to supply colors, while the structural row rules prevent old wrapping
and hidden-action styles from reintroducing the layout problem.

Working, unresolved approval/error, and unread-response states are distinct.
Elapsed time comes from actual conversation activity, refreshed every 30 seconds;
unknown timestamps display a dash. Pending approval titles, tool arguments,
provider errors, and transcripts are not copied into the navigation projection.
Working and attention filters run on the complete matching library before paging,
so counts include conversations beyond the current page.

Full paths remain the workspace identity and search target. Compact labels use
unique path suffixes from the complete available registry; equal folder names
retain the ancestors needed to distinguish them. Folder selection and browsing
remain separate for a workspace that contains nested workspaces. Missing folders
remain hidden without removing their registrations or saved histories.

Registry path labels are cached by the complete set of paths, with a bounded
cache and a fresh result per caller. Activity changes do not recompute suffixes;
adding or removing a registry path does. Progress projections also avoid creating
workspace summary dictionaries for each chat already grouped under a workspace.
Keep these catalog loops allocation-light: small per-row costs multiply across
large libraries and every active client. The active-client performance gate
exercises these paths with 22,915 sessions, four browsers, and a Terminal stream.

Attached-client geometry updates (`navExpanded`, pinning, dimensions and Canvas
control disclosure) save only the client's presentation and command receipt.
They reuse unchanged catalog projections and notify only that client; they do
not rewrite conversation or artifact storage. Pending runtime progress retains
its normal scheduled publication. Mixed view changes, drafts, and legacy actions
without an attached client continue through the existing shared action path.

## Shared actions and shell modules

The `builtin.workspaces` and `builtin.chats` instances keep their public host,
instance IDs, capabilities, independent state, and bounded projections. When the
standard workspaces instance is paired to chats through its existing `hideWhen`
relationship, the two builtins present the shared drill-in layout. Standalone and
custom modules retain their independent composition.

Agents use the same `shell.view.update` and `shell.command` paths as the UI:

- Chat instance: `navWorkspaceList` (boolean), `navStatusFilter` (`all`,
  `attention`, `working`, `unread`), `navSort` (`activity`, `created`, `name`),
  plus the existing scope, search, and page keys.
- Sidebar sections: `navSectionsCollapsed` contains zero or more of `pinned`,
  `workspaces`, and `recent`. `navPinnedPage` is a zero-based page index.
  `navRecentView` stores Recent's independent `navFilter`, `navSort`, `navArchive`,
  `navCollection`, `navLocationFilter`, `navStatusFilter`, and `navChatPage` keys.
  UI filter changes clear Recent's saved page. Agents send the complete Recent
  view object when changing it. Workspace chat filters retain the existing keys.
- `shell.query.sidebarNavigation` exposes bounded `pinned`, `recent`, and
  `workspace` pages, plus `recentView`. The complete native-history index remains
  authoritative; no workspace-specific chat membership list is stored. Existing
  `chatNavigation` and `homeNavigation` projections remain available to older
  clients. Saved `navSimple` is accepted for compatibility but no longer selects
  a different renderer.
- Workspace instance: `navWorkspaceMode` (`recent`, `folders`), plus the existing
  folder path, wildcard search, and page keys.
- `shell.command` for `workspace.select` or `workspace.create` on the paired
  workspace instance opens that client's associated chat list. It preserves the
  other clients' library views and the normal app command semantics.
- `shell.query` includes bounded activity summaries, full and compact paths,
  activity counts, and session identities. Clipboard copying is a local browser
  convenience over those same queryable strings.

Hover state and flyout placement are ephemeral browser presentation. Viewing
these summaries does not mark an approval, error, or completion as reviewed.

## Browser observations and refresh

The existing SSE stream carries state changes. Shell snapshots refresh when
navigation membership, stable activity order, status, or client presentation
changes; text deltas alone do not invalidate the shell. Hidden documents defer
shell refresh and view reports, and catch up on visibility restore or reconnect.

Automatic `/api/view` observations identify their `visibleTextScope` as
`interface`. They include controls, focus, draft values, selected text, geometry,
and client-owned content such as custom shell components. They exclude repeated
conversation text and elapsed activity labels that already exist in authoritative
session state. `sessionId` binds the observation to its selected chat. Explicit
`window.amplifier.getState().renderedView` inspection still includes full rendered
text. Reports stay single-flight, retain the latest pending observation, and
invalidate deduplication on reconnect so the fresh connection receives a report.

`npm run test:sidebar-activity-browser` exercises the packaged UI against real
HTTP/SSE with synthetic progress: stable ordering, sort/pin persistence, drag and
keyboard reorder, custom content, drafts, hidden clients, and request counts.
It makes no model calls. The separate active-client performance gate covers
four browsers and one Terminal stream together.

## Session identity

**Session ID** in the flyout and conversation details is the shared native ID used
by Amplifier CLI and the session directory. Imported CLI sessions also have an
internal Unified record key; it stays unchanged for selection, pins, artifacts
and app commands, and is only identified as **App ID** in diagnostic details.
Unified-created sessions normally use the same UUID for both. Chat search accepts
the shared ID (including a prefix), as well as existing internal keys.

The CLI's `amplifier session list` is scoped to its current project directory.
Run it from the full workspace path shown in the flyout, or pass that path using
`amplifier session list --project /path/to/workspace`. A list from a different
folder does not determine whether this workspace's session was saved.
