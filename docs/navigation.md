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

The default sidebar keeps one list in focus. **All chats** shows pinned and recent
conversations across available workspaces. **Workspaces** opens a recent-workspace
index or the existing folder browser. Selecting a workspace opens its chats;
**All workspaces** returns to the index without changing the current conversation.
Browsing folders, searching, filtering, and opening details never mount a runtime,
send a message, change recency, or acknowledge an unread response.

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

## Shared actions and shell modules

The `builtin.workspaces` and `builtin.chats` instances keep their public host,
instance IDs, capabilities, independent state, and bounded projections. When the
standard workspaces instance is paired to chats through its existing `hideWhen`
relationship, the two builtins present the shared drill-in layout. Standalone and
custom modules retain their independent composition.

Agents use the same `shell.view.update` and `shell.command` paths as the UI:

- Chat instance: `navWorkspaceList` (boolean), `navStatusFilter` (`all`,
  `attention`, `working`, `unread`), plus the existing scope, search, and page keys.
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
