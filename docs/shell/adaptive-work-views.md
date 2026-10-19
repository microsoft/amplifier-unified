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

Changing Appearance does not navigate. The Home action deliberately applies the
chosen starting view. Resuming a browser restores its existing surface. A new web
client arrives at Home while retaining the selected conversation behind it.

## Canonical owners

`workHome` is a bounded shell resource over the existing native chat catalog and
its activity projection, with the same search, identity, archive and permissions
semantics as chat navigation. Each status group is independently paged. Browsing
must not initialize a dormant worker or read a transcript. Pending or failed
resources must not appear as an empty catalog. Scope and query changes discard
obsolete rows; counts describe the host's query, not a loaded-page estimate.

For you contains required attention and unread responses; Working reports current
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

## Trial gates and deliberate limits

Verify all three views at desktop, tablet and phone widths; keyboard navigation;
search/scope/page isolation; attention/review freshness; a draft and an open output
surviving navigation/appearance changes; real saved-chat opening; and a synthetic
send through the actual runtime. Record exact source and built-client identity.
Synthetic responses demonstrate interaction, not live-provider quality or latency.

The user's preferred mix is the design evidence. It is not measured usability
evidence. Revisit if they cannot find waiting work, if a quiet mode conceals a
required action, or if project grouping still requires reasoning about folders.
