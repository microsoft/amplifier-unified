# Approachable shell: isolated trial

## Historical trial

This record describes an isolated trial. Access details, account information,
private paths and exact owner-checked operation commands belong in the private
workspace handoff, outside this repository. This document does not assert that
the historical service is still running.

The source base was the latest fetched `origin/main` at setup:
`51dfcd5a92e450efcde90fbbe5f497348de8dab2`, release **0.20.20**.
The trial exercised a running application with real actions, storage and file
viewers. It was a design trial, not a production release or complete qualification
of all existing integrations. Authentication and certificate validation remained
enabled; browser trust of the trial certificate authority required a user action.

## What changed

- A steady sidebar contains collapsible Pinned, Workspaces and Recent sections. The same pinned component and existing reusable reorder behavior serve the sidebar and full chat browser. Workspace shortcuts show six recent workspaces; recent chats show eight. Full browsing moves to the main pane and retains existing search, filters and pagination (40 per page after 50).
- Workspaces open a main-area page with Chats, Files and Details. Workspace browsing changes presentation only. Selecting a chat is the action that changes the active conversation.
- New chat has a focused welcome and a composer workspace menu: No workspace, an existing workspace, Create new workspace, or Use an existing folder. Existing name-first placement and native history discovery are reused.
- Model, effort and bundle controls remain inside the composer. A fresh instance with no providers offers model setup instead of an indefinite loading label. The ownership gate still blocks input and uses owner metadata and the existing takeover action.
- Chat title actions live beside the title. App settings live in the sidebar footer; Appearance is inside Settings. The theme system is retained.
- The right pane has Chat overview, retained file tabs and compact file controls. Overview uses actual outputs, attachments in loaded messages, worker records and existing activity controls. No sample AI replies or results are fabricated.

## Behavioral contracts

1. Browsing must preserve the selected session, its execution folder, draft and canvas state. `view.update` exposes `workSurface`, `workWorkspaceId` and `workWorkspaceTab` through the same action path to UI and agents. Client presentation remains client-specific.
2. Native Amplifier history remains authoritative. Sidebar shortcuts and full pages are bounded projections, not a second session roster. Existing-folder attachment does not copy, move or replay history.
3. Viewing a folder or file does not silently attach it to a message. Existing file renderers remain mounted while browsing or showing overview, preserving their normal lifecycle and editor state.
4. Isolation of this trial is operational, not a filesystem security sandbox. A user can explicitly attach another server folder. The initial trial uses only its private workspace and does not attach production folders.
5. The composer ownership gate and host takeover protocol remain authoritative. No app name is hardcoded into the gate.
6. Presentation preferences change disclosure, not tool permissions, execution semantics or session membership.

## Validation

- Frontend: **339 unit tests passed**, including compact/full navigation, shared scope behavior, canvas suppression, draft setup and empty-provider setup.
- Backend: **96 targeted tests passed** across shell modules, workspaces, sidebar sections, new chat and ownership notices. After the final navigation changes, **17 focused tests passed** covering the trial and bounded browser state; these overlap with the first run and are not an additional independent total.
- Production frontend build passed. Git whitespace checks passed.
- Real isolated-instance HTTP action checks passed using a dedicated client identity: name-first workspace creation, shell projection, draft preservation during workspace browsing, and return to New chat.
- The isolated worker environment installed successfully and imported its runtime modules. No live model request was made.
- Browser inspection on the Mac verified the final landing layout, theme application, stable expanded/collapsed navigation, composer workspace picker, new-workspace form, Files tab, workspace-local New chat, chat title actions, Chat overview and opening the real `Start here.md` in the retained Markdown viewer.
- An empty native chat named **Shell layout check** was created for UI validation. It contains no invented conversation or model response. The **Shell trial** workspace contains a setup guide.
- Existing host services remained active with their original process IDs. The production host was not changed.

## Visual refinement after PWA comparison

The follow-up pass compared the real Mac PWA against the production host with this trial. The trial now uses Graphite in Light mode through its existing Appearance controls; the theme system and other choices remain available.

- Restored the full-width Amplifier header, with workspace/chat context and title actions. The sidebar starts with Your work instead of repeating cramped branding.
- Left-aligned section controls and search, corrected old fixed-height rules that made single-line shortcuts too tall, and aligned shared pinned rows and their drag preview.
- Reduced welcome typography, softened the composer border, and bounded the workspace browser's reading width. Search inputs now fill their available space.
- Made contextual details reusable outside the sidebar and anchored their popovers near the clicked control in the main browser.
- Kept file controls in the document layout instead of floating over its first lines; shortened their toolbar and grouped the path with Open.
- Corrected workspace-picker label spacing and kept the existing creation, history, ownership, model and bundle behavior.

Validation for this pass: 339 frontend tests and the production build passed. Native browser inspection covered the desktop welcome/composer, workspace browser, pinned row, chat actions, contextual details, workspace picker and real Markdown viewer. The empty Shell layout check chat is pinned for inspecting the shared row. No model calls were made. The production host remained read-only; only the owned trial was updated. Mobile and every theme still need broader visual acceptance.

## Navigation and workspace-picker refinement

The next pass removes the duplicate header Chat controls, fully hides collapsed navigation and puts its reopen button in the header. The cog now has Settings, Send feedback and What the agent sees; Appearance is reached inside Settings.

New chat follows the visible workspace context through the shared host action. All chats / All workspaces start without a workspace; an existing unsent draft retains its explicit choices. The new chooser has separate actions, a visible name/path search, bounded 40-result pages, parent-path disambiguation, loading/error recovery, and existing server-folder browsing in the attachment flow.

Validation: 343 frontend tests and 55 targeted backend tests passed, plus the production build. See the [feature inventory against freshly fetched main 0.20.21](shell-inventory-2026-09-23.md) for retained capabilities, omissions and integration work. The trial base remains 0.20.20; upstream 0.20.21 was inspected, not silently merged.

Native browser acceptance for this revision verified: no residual app rail after collapse; keyboard focus returns to the header reopen control; expansion works; the cog lists no Appearance entry; All chats → New chat selects No workspace; a workspace page → global New chat selects that workspace; ancestor-path search narrows results; Use existing folder opens the real server path browser and cancellation does not attach anything; an existing chat has one composer Chat controls button and no duplicate in the header. The final polish also aligns the new-chat header with its chosen workspace and focuses chooser search on open.

Deployment file hashes matched the local commit. The existing host services retained their process IDs. No model call, live ownership takeover, or production change was performed. Search pagination was exercised by automated fixtures; the live trial has only its two private workspaces. Mobile layouts were not requalified in this pass.

## Trial limits

- Connect a model provider in this instance's Settings before asking it to perform AI work. Provider credentials, production settings and conversation history were not copied. Model calls, voice and live cross-client ownership takeover were not exercised here.
- Files can be browsed without changing the active chat. File preview currently requires an active chat in that same workspace; the UI explains this. A separate workspace document viewer is not implemented.
- Workspace shortcuts currently follow recent activity. User-curated workspace shortcuts are a future refinement.
- Populated pin dragging, multiple live workers, dirty-editor transitions, every theme, mobile breakpoints and thousands of production histories have not all received fresh visual acceptance in this trial. Existing checks cover portions of these behaviors.
- The right-pane Sources list is limited to attachments in currently loaded messages, labelled accordingly.

## Instance ownership and operation

The historical trial used a separately installed application environment and
worker runtime. Its session storage, native history, caches and TLS material were
instance-owned. It bound HTTPS on loopback and a private network interface; boot
activation and automatic updates were disabled. Credentials stayed outside Git.
The existing instance helper was not used because its older dependency baseline
did not match the repository. Its checks were left intact.

The private owner record retains the resource mapping and exact commands. Before
any stop, restart or update, verify the named resource's ownership and reject
active work, calls or pending actions. Recheck immediately before mutation. Target
only that owned instance; preserve saved chats, drafts, credentials and files.
Do not run broad service restarts or delete instance data. These historical notes
are not instructions to recreate an instance on a shared application host.

## Approved integration candidate

Integrated main `f2286ab9` (0.20.22), retaining the current Canvas single-viewer, file paths, immutable document versions and provider diagnostics. Final user polish combines the duplicate naming/export entries into **Chat details** and keeps one search focus ring. The integration also restores discovery/refresh/attention access and explicit path preferences, keeps call and screen-sharing controls visible while browsing, and exposes a scoped Stop action for ongoing work.

The integrated frontend passes 346 unit tests, the production build, and a clean repeat-build comparison. Native Edge inspection on the owned isolated instance verified the single search focus outline, autofocus, one Chat details menu item, continued naming/export access, and focus return from Chat details to its menu trigger. Deployment hashes matched all 141 changed files at source `196212933f4ac0aa33b462b216e5adf1dc1eb44c`. The later chooser-trigger focus correction does not alter placement or history data.

The full Python suite is run as part of the release handoff; its exact result is recorded there. Live AI/voice/takeover and every mobile/theme combination are not newly qualified by this visual pass. Existing component and protocol regressions remain the evidence for those paths.
