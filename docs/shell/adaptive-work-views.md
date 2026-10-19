# Adaptive work views — preview contract

Requested for an isolated user trial on 2026-10-19. This changes the web client's
arrival experience; it does not adopt the separate AHP/ACP architecture or change
conversation execution. No release or production adoption is implied.

## Three independent choices

- Appearance detail chooses an initial view: Focused → Focus, Balanced → Brief,
  Everything → Studio. `presentation.workStartView` can override that default.
- `view.workHomeView` is the current view. Switching it never changes Appearance,
  the starting preference, selected conversation, unsent draft or saved output.
- Available space determines navigation: docked on a wide screen, a drawer on a
  tablet and a full browse surface on a phone. All views remain available.

Changing Appearance does not navigate. Home returns to the last view, status
filter, scope, search and page choices in this client; its scroll position is
retained in the browser tab, like chat reading positions. Search pending at
navigation is saved before leaving. Resuming a browser restores its existing
surface and choices. A new web client arrives at Home with the chosen starting
view while retaining the selected conversation behind it. Only the explicit
“Go to starting view” control resets the view and status filter; it keeps scope
and search. Appearance popovers close when leaving Home.

## Canonical owners

`workHome` is a bounded shell resource over the existing native chat catalog and
its activity projection, with the same search, identity, archive and permissions
semantics as chat navigation. Each status group is independently paged. Browsing
must not initialize a dormant worker or read a transcript. Pending or failed
resources must not appear as an empty catalog. Scope and query changes discard
obsolete rows; counts describe the host's query, not a loaded-page estimate.

Needs you contains required attention; Ready for you contains unread responses;
Working reports current
execution; Pick up again contains other saved conversations. Idle is never called
finished work. Reviewing a response uses its exact attention fingerprint and does
not answer a question, approve an action, or declare a project complete. Existing
chat and Canvas controls own conversation/result interaction.

Projects in this preview are human-facing views of registered workspaces, with
recent/pinned projects offered before the complete folder inventory. Choosing a
scope only filters the view. It never moves files, changes instructions, assigns
a conversation to a new folder, or grants access. On its own means managed chats.
Project Work opens the existing workspace goal/work projection; it is not a fourth
global mode. Logical cross-folder project membership remains future work.

If the Chats component is pinned to one project, Home keeps that boundary: its
scope control shows only that project, and creation uses that project's confirmed
path. Requested query identity and effective project identity are tracked
separately. An unavailable pinned project shows an unavailable state and cannot
offer an all-work fallback; changing the component's pin invalidates cached pages.

## Trial gates and deliberate limits

Verify all three views at desktop, tablet and phone widths; keyboard navigation;
search/scope/page isolation; attention/review freshness; a draft and an open output
surviving navigation/appearance changes; real saved-chat opening; and a synthetic
send through the actual runtime. Record exact source and built-client identity.
Synthetic responses demonstrate interaction, not live-provider quality or latency.

The user's preferred mix is the design evidence. It is not measured usability
evidence. Revisit if they cannot find waiting work, if a quiet mode conceals a
required action, or if project grouping still requires reasoning about folders.

## Trial refinement — 2026-10-19

The user approved a refinement after reviewing their Focus screenshot. The
observed problem was duplicate navigation and a first decision halfway down the
screen. Keep the warm typography and independent Appearance/view choices.

- Home owns status filtering, scope and search. The sidebar owns pinned
  chats, projects and recent chats; its separate search and creation entries are omitted on Home. The single Home
  creation control uses its current project scope, or starts standalone under All
  work / On its own. Unavailable scopes cannot silently create somewhere else.
- Focus uses one reading column. The decision comes before the single status
  filter row, with a prominent action that opens the original conversation.
- The header uses the existing shell navigation, breadcrumbs and chat controls.
  The trial-added Resume and Home back links have been removed at the user’s
  request. Home is now the first breadcrumb in the header, followed by the project
  and chat when applicable. It stays visible on phones; the current Home page is
  shown once, without a redundant link. The shell owns opening/closing Browse.
- Navigation labels say Projects. Folder-backed workspace ownership, creation,
  permissions and history contracts are unchanged; this is not logical regrouping.
- Returning Home preserves the last view and an explicitly opened or closed
  desktop sidebar. Narrow navigation closes when choosing Home. Appearance
  changes and view switches preserve selection, search, draft and saved output.
- Counts describe chats in the current scope; activity timestamps explicitly say
  Updated. Typography uses the app's text-size tokens, including small metadata.

Check an ordinary laptop viewport with the sidebar open, as in the user's
screenshot: the Focus action should fit above the fold, navigation should have
one aligned label column, and required attention must stay reachable. Verify
phone/tablet navigation, keyboard focus, enlarged text and dark/forced colors.
These checks establish usable behavior, not measured user preference. Revisit if
moving statuses out of the sidebar makes returning to waiting work harder.

## Continuity refinement — 2026-10-19

The user requested that Home remember its state after visiting chats/projects,
and that the trial-added Resume link be removed in favor of the existing header.
This supersedes the first trial’s reset-on-Home behavior. Confirm navigation away
and back, reload, immediate departure while typing, and scroll restoration after
results load. Preserve independent starting preferences and chat/Canvas state.

Two timing defects were found during those journeys: search blur must not disable
New chat when its project is already confirmed, and closing a modal sidebar must
release its inert background before React reveals a retained Canvas. The latter
uses layout-effect cleanup in the existing modal-focus owner. Browser acceptance
includes a phone opening that saved output and using its normal Back to chat.

Main was integrated through `b164044ad` (0.20.181), including configuration
recovery, saved chat-reference navigation, patch placement recovery, passive
environment inspection and approval layout fixes. The Home integration extends
client presentation and resource demand without replacing these owners.

## Home breadcrumb — 2026-10-19

At the user’s request, Home moved from the sidebar to the start of the header
breadcrumb. The link uses the same remembered Home navigation and preserves
chat/draft/Canvas state. Phone headers keep Home visible while the existing
project breadcrumb remains compacted. Removed the old sidebar entry and styles.
