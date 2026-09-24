# Approachable shell: isolated Spark-2 trial

## Running trial

Open **https://100.93.134.115:18463/** from a machine connected to the same Tailnet. Sign in using the Spark-2 account. This instance uses its own local certificate authority; browser certificate trust is a user action. No authentication or certificate validation was disabled.

The source base was the latest fetched `origin/main` at setup: `51dfcd5a92e450efcde90fbbe5f497348de8dab2`, release **0.20.20**. The implementation branch on the Mac is `codex/approachable-shell-spark2-20260923`.

This is a running application with real actions, storage and file viewers. It is a design trial, not a production release or a complete qualification of all existing integrations.

## What changed

- A steady sidebar contains collapsible Pinned, Workspaces and Recent sections. The same pinned component and existing reusable reorder behavior serve the sidebar and full chat browser. Workspace shortcuts show six recent workspaces; recent chats show eight. Full browsing moves to the main pane and retains existing search, filters and pagination (40 per page after 50).
- Workspaces open a main-area page with Chats, Files and Details. Workspace browsing changes presentation only. Selecting a chat is the action that changes the active conversation.
- New chat has a focused welcome and a composer workspace menu: No workspace, an existing workspace, Create new workspace, or Use an existing folder. Existing name-first placement and native history discovery are reused.
- Model, effort and bundle controls remain inside the composer. A fresh instance with no providers offers model setup instead of an indefinite loading label. The ownership gate still blocks input and uses owner metadata and the existing takeover action.
- Chat title actions live beside the title. App settings and appearance live in the sidebar footer. The theme system is retained.
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
- Real Spark-2 HTTP action checks passed using a dedicated client identity: name-first workspace creation, shell projection, draft preservation during workspace browsing, and return to New chat.
- The isolated worker environment installed successfully and imported its runtime modules. No live model request was made.
- Browser inspection on the Mac verified the final landing layout, theme application, stable expanded/collapsed navigation, composer workspace picker, new-workspace form, Files tab, workspace-local New chat, chat title actions, Chat overview and opening the real `Start here.md` in the retained Markdown viewer.
- An empty native chat named **Shell layout check** was created for UI validation. It contains no invented conversation or model response. The **Shell trial** workspace contains a setup guide.
- Existing Spark-2 services remained active with their original process IDs. Spark-1 was not changed.

## Visual refinement after PWA comparison

The follow-up pass compared the real Mac PWA on Spark-1 with this trial. The trial now uses Graphite in Light mode through its existing Appearance controls; the theme system and other choices remain available.

- Restored the full-width Amplifier header, with workspace/chat context and title actions. The sidebar starts with Your work instead of repeating cramped branding.
- Left-aligned section controls and search, corrected old fixed-height rules that made single-line shortcuts too tall, and aligned shared pinned rows and their drag preview.
- Reduced welcome typography, softened the composer border, and bounded the workspace browser's reading width. Search inputs now fill their available space.
- Made contextual details reusable outside the sidebar and anchored their popovers near the clicked control in the main browser.
- Kept file controls in the document layout instead of floating over its first lines; shortened their toolbar and grouped the path with Open.
- Corrected workspace-picker label spacing and kept the existing creation, history, ownership, model and bundle behavior.

Validation for this pass: 339 frontend tests and the production build passed. Native browser inspection covered the desktop welcome/composer, workspace browser, pinned row, chat actions, contextual details, workspace picker and real Markdown viewer. The empty Shell layout check chat is pinned for inspecting the shared row. No model calls were made. Spark-1 remained read-only; only the owned Spark-2 trial was updated. Mobile and every theme still need broader visual acceptance.

## Trial limits

- Connect a model provider in this instance's Settings before asking it to perform AI work. Provider credentials, production settings and conversation history were not copied. Model calls, voice and live cross-client ownership takeover were not exercised here.
- Files can be browsed without changing the active chat. File preview currently requires an active chat in that same workspace; the UI explains this. A separate workspace document viewer is not implemented.
- Workspace shortcuts currently follow recent activity. User-curated workspace shortcuts are a future refinement.
- Populated pin dragging, multiple live workers, dirty-editor transitions, every theme, mobile breakpoints and thousands of production histories have not all received fresh visual acceptance in this trial. Existing checks cover portions of these behaviors.
- The right-pane Sources list is limited to attachments in currently loaded messages, labelled accordingly.

## Instance ownership and operation

Remote checkout:
`/home/bkrabach/dev/amplifier-unified-worktrees/approachable-shell`

Private owner directory:
`/home/bkrabach/.local/share/amplifier-unified-instances/approachable-shell`

User service:
`amplifier-unified-instance-approachable-shell.service`

It binds HTTPS port **18463** on loopback and the Spark-2 Tailnet IP only. It is running but is not enabled at boot. Its automatic update/install settings are disabled. Its environment, session storage, native history, caches, runtime and TLS material are instance-owned. Secret files stay outside the repository.

The existing instance helper was not used because its older dependency baseline did not match the latest repository. Its checks were left intact. This trial has a separately installed application environment and worker runtime. Owner metadata includes the service unit hash and task identity.

Before restarting or stopping, run the owner guard, which verifies unit ownership and rejects active work, calls or pending actions:

```sh
ssh spark-2 '/home/bkrabach/dev/amplifier-unified-worktrees/approachable-shell/.venv/bin/python /home/bkrabach/.local/share/amplifier-unified-instances/approachable-shell/idle-guard.py'
```

Only after that succeeds, use the exact unit:

```sh
ssh spark-2 'systemctl --user restart amplifier-unified-instance-approachable-shell.service'
# Or stop the trial without deleting its data:
ssh spark-2 'systemctl --user stop amplifier-unified-instance-approachable-shell.service'
```

For updates, build the frontend, run relevant checks, verify the target unit is idle, sync only this checkout, and restart only this unit. Recheck the guard immediately before restarting. Keep saved chats, drafts, credentials and workspace files in the owner directory. Do not run a broad service restart or remove instance data.
