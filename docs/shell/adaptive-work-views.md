# Adaptive work views — implementation and review notes

These notes record the web client's Home/sidebar design progression, concrete
behavior, and bounded acceptance evidence. The recorded Home/sidebar baseline is
[v0.20.188](https://github.com/microsoft/amplifier-unified/releases/tag/v0.20.188).
The [Home/sidebar contract](work-discovery.v1.md) owns the observable promises;
these notes supply implementation detail and historical context.

Dated trial restrictions below describe their original checkpoints. Publication,
live adoption, and designer acceptance remain separate facts. This work does
not adopt the separate AHP/ACP architecture or change conversation execution.

## Shared landing pages — October 20 follow-up

Home, All chats, All workspaces and individual workspace pages now share the
landing frame, stable Layout switch, Display controls, status filters and Focus
paging. Existing chat/workspace rows own their actions. Files, Details, plans,
and Workspace work remain in the workspace page; archive, sort, folder browsing,
creation and attachment remain with their existing controls.

Each destination stores its layout, scroll and Focus cursor in the current tab.
Home retains its existing view owner. Other landing pages reuse their existing
bounded catalogs; returning to a later Focus page requests that page once when
selection-based catalog hints no longer match. This adds no workers, model calls,
summary processing or relationship inference.

The sidebar includes a Home icon beside Browse/Status. The header Home control
has an icon and a visible button boundary elsewhere; Home itself has no redundant
header label. Visible status filters remain reachable in every layout, and clearing
status does not clear search or scope. New chat/workspace remains labeled on phones.

See the [accepted scope](../ux/decisions/20261020-landing-pages.md) and
[designer review journeys](../ux/review.md). The retained DTU preview and synthetic
checks qualify implementation; they do not establish human usability or live adoption.

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

Workspaces are registered folders, with recent/pinned workspaces offered before
the complete folder inventory. The October 20 follow-up restores this name in UI
labels, replacing the earlier Projects wording. Choosing a
scope only filters the view. It never moves files, changes instructions, assigns
a conversation to a new folder, or grants access. No workspace means managed chats.
Workspace work opens the existing workspace goal/work projection; it is not a fourth
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

- Home owns its status filtering, scope and search. The sidebar has independent
  Browse and Status organizations (see the 2026-10-20 refinement below).
  The Home creation control uses its current project scope, or starts standalone under All
  work / On its own. Unavailable scopes cannot silently create somewhere else.
- Focus uses one reading column. The decision comes before the single status
  filter row, with a prominent action that opens the original conversation.
- The header uses the existing shell navigation, breadcrumbs and chat controls.
  The trial-added Resume and Home back links have been removed at the user’s
  request. Home is now the first breadcrumb in the header, followed by the project
  and chat when applicable. It stays visible on phones when away from Home;
  Home itself shows only app identity. The shell owns opening/closing Browse.
- Navigation labels say Workspaces. Folder-backed workspace ownership, creation,
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
includes a phone opening that saved output and using the container-header
**Hide canvas** control to return to chat, following [Canvas](../canvas.md).

Main was integrated through `b164044ad` (0.20.181), including configuration
recovery, saved chat-reference navigation, patch placement recovery, passive
environment inspection and approval layout fixes. The Home integration extends
client presentation and resource demand without replacing these owners.

## Home breadcrumb — 2026-10-19

At the user’s request, Home moved from the sidebar to the start of the header
breadcrumb. The link uses the same remembered Home navigation and preserves
chat/draft/Canvas state. Phone headers keep Home visible while the existing
project breadcrumb remains compacted. Removed the old sidebar entry and styles.

Home project controls use current context independently of asynchronous search pages. The context carries requested/effective scope and the current available registration; scope, pin, availability, and path changes invalidate old identity. Pending search rows never count as current results. Project New chat submits both registration ID and path to the canonical draft owner, which revalidates them at acceptance.

## Focus passes and visible location — 2026-10-20

The user's live-data review requested temporary next-item browsing, removal of
the Home label while already on Home, and correction of a retained chat highlight.

- Focus offers Previous, an item counter, and Next within its current status
  group. The last item offers Start again. Status filters keep one card in Focus;
  See all explicitly opens that group's list in Brief.
- The pass stores one small cursor in this browser tab's session storage. It
  survives chat/project visits and reload, uses chat identity across loaded-page
  reorder, and resets on a different search or scope. If the item leaves its
  group, the nearest remaining item is shown. Next does not mark read, dismiss,
  snooze, approve, archive, select a chat, or start work.
- Paging uses the existing bounded query. Moving within a loaded page needs no
  catalog query; crossing a page requests only the next/previous bounded page.
  Keyboard focus returns to the paging control after that page settles. An
  in-progress pass keeps its group when unrelated new attention arrives.
- The header omits its breadcrumb on Home; compact Home headers keep the app
  identity visible. Other surfaces retain Home as the first breadcrumb.
  Sidebar selection and document title describe the visible surface. Retaining
  a chat for draft/Canvas continuity does not highlight it while browsing.
- Conversation-specific error/recovery/status banners stay on the conversation.
  Global connectivity, update, runtime and active voice indicators retain their
  existing owners and visibility.

`focus-navigation-browser.mjs` exercises real catalog pages, unchanged attention
and runtime state while stepping, chat/draft continuity, error-banner ownership,
scope/search resets, restart, and narrow/forced-color layouts. The live review
is qualitative product evidence, not a measured usability or accessibility study.

## Browse / Status and calmer Home — 2026-10-20

The user approved the sidebar mockup and explicitly wants Search chats and New
chat accessible from both independently scrolled surfaces, including on Home.

- The sidebar's first line switches Browse / Status. Browse retains pinned chats,
  projects and recent chats. Status groups the same chats under Needs you, Ready
  for you, Working and Pick up again. Blocked work stays in Needs you.
- The visibility toggle always stays in the app header. Phone navigation fills
  the space below it; the toggle participates in the drawer's keyboard focus
  loop. Escape closes navigation, and choosing a chat restores the main surface.
- Sidebar mode, group expansion and bounded page choices belong to this client.
  They survive navigation and reload independently of Home's view, filters,
  search, Focus pass, and the retained chat/draft/Canvas.
- Each open Status group requests one existing catalog page. Closed groups and
  hidden navigation request no Status rows. Identical Home/sidebar queries reuse
  the canonical projection cache. A pinned Chats component remains fenced to its
  project, including when that project becomes unavailable.
- Brief pairs Needs you and Ready for you with one prominent card each and
  compact additional rows, followed by running work and recent chats. Studio
  gives Needs you, Ready for you and Working separate columns, with recent chats
  below. Narrow layouts stack these sections in that order. When Brief has no
  attention or unread responses, one quiet confirmation replaces the empty
  pair; its running strip appears only when something is running.
- Cards use existing saved titles/descriptions and activity labels. Action labels
  distinguish an existing question, approval or blocked chat and open that chat;
  they do not answer, approve, acknowledge, classify, or create a new work entity.
  Large saved-history totals are available in browsing instead of presented as
  an obligation on the overview.

No extra model calls, summary jobs, history reprocessing or session lifecycle
changes are introduced. Existing summary input/output is unchanged.

## Changelog

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-10-20 | Apply the accepted Home/sidebar UX amendment. | [Product-owner decision](../ux/decisions/20261020.md). |
