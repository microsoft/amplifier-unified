# Modular shell: navigation, component contributions and artifact viewers

Agents and extension authors can start with the
[repository-owned shell skill](../../skills/amplifier-shell/SKILL.md).
See [behavior installation and app defaults](behavior.md) for discovery.
See the [customization status and proposed next milestones](customization-roadmap.md)
for the boundary between today's extension contracts and the remaining shell work.
For complete light/dark palettes, backgrounds and the decoration preference,
see [Themes](themes.md).
See [Component contributions](components.md) for registered header, status,
composer-action, canvas-toolbar and Settings slots.

The first milestone implemented the navigation slice of the shell plan, based on
Unified `81f2182b6112e4162d982291151c489bdec6ef8a` (0.10.8). The workspace
manager and conversation list are independent registered components. Additional
instances can follow the active workspace, pin a workspace, or show all chats.
An external module can replace either component while the app stays open.

The content-workspace milestone, integrated with Unified 0.11.9, adds a
renderer registry, validated hot-loaded artifact viewers, and a second pinned
artifact view. See [Artifact viewers](#artifact-viewers) below for its API,
ownership boundaries and acceptance checks.

The default shell retains workspace creation, folder browsing, chat search,
pagination, pinning, renaming, removal, unread indicators and history refresh.
Its declarative `hideWhen` binding hides the workspace manager when the default
chat list shows All chats, without discarding the manager's view state.

## Ownership and boundaries

| Contract | Owner | Evidence |
| --- | --- | --- |
| Canonical chats, workspace registrations, runtime ownership | Existing AppService and Amplifier Foundation | Existing navigation, history and handoff tests |
| Package manifests, immutable digests, host validation receipts | `amplifier_web/shell_modules.py` | Digest, stale-host, compatibility and capability tests |
| Client compositions, revisions, prepared changes, instance UI state | Shell records in the existing app database | Isolation, persistence, compare-and-set, idempotency and revert tests |
| Bounded navigation queries | Adapters over the existing workspace/chat projections | Existing 206-chat browser fixture; no additional conversation store |
| Loading, subscriptions, component errors and browser status | `frontend/src/shell/runtime.jsx` | Production browser proof and validation harness |
| Workspace and chat controls | `frontend/src/shell/navigation-components.jsx` | Existing component tests run against public host fixtures |
| Public authoring API | `packages/shell-sdk` | External navigator in `examples/shell-navigator` |
| Theme tokens and layout presentation | Host root and existing panel layout | Live preview, CSS reordering, revert and stable iframe checks |

The host has one event stream. A small shell invalidation event shares it with
normal app updates; a coalesced query supplies bounded instance snapshots.
Queries expose summaries, the selected workspace registration, folder rows and
navigation state. They do not expose transcripts, composer drafts, credentials
or runtime mount plans through the SDK. The existing app-wide observation API
continues to exist outside the module SDK.

Shell compositions and view state use the existing client-attachment contract.
Each page attaches with a new client ID; reload or duplication resumes a copy
of the previous client's presentation, drafts and shell preferences, so two
live pages do not share writable presentation state. A fresh tab starts with
defaults. Conversation selection is client-local, while canonical sessions,
artifacts and tool work remain host-owned. Durable cross-device preference
synchronization remains later work.

Legacy navigation preferences are copied once when a client is first seen.
Agents should use `shell.query` and `shell.view.update` for the actual module
state. Legacy `/chatNavigation`, `/workspaceExplorer` and `view.update` navigation
fields remain app defaults. The agent state overview explains this distinction.

## Package and component contract

A native package is one compiled ESM artifact, at most 250,000 characters, plus
a manifest. The manifest names a semantic version, `apiVersion: "1.0"`,
`profile: "trusted-native-navigation-v1"`, a `stateSchema`, and capabilities.
The artifact exports a default factory `({React}) => Component`; the component
receives `{host}`. React comes from the host. The dependency-free public SDK
provides `defineModule` and `useNavigation`.

`host` exposes the instance/client IDs, `getSnapshot`, `subscribe`, `dispatch`
and `setDirty`. Subscriptions must return cleanup functions. Components use
`dispatch('view.update', {patch})` for their own navigation state. Other
supported actions pass through `shell.command`, which checks the declared
capability before invoking the existing shared action handler. The namespace
does not provide runtime/provider/bundle configuration operations.

Supported capabilities are navigation reads and explicit selection, workspace
management, chat management, folder location reads and history refresh.
Changing a filter or browsing a folder never selects a conversation. Explicit
selection continues to use the existing selection operation.

Each composition holds up to 12 instances in the navigation slot, with stable
IDs. CSS order changes placement without moving or recreating mounted nodes.
Presentation supports light/dark/system appearance, accent color, density and
the existing balanced/conversation/work layouts. Custom CSS skins continue to
use the existing theme service. Full theme bundles, arbitrary slots and
conversation decomposition are later milestones. Artifact renderers have the
separate content-workspace contract below.

## Stage, validate, prepare, activate

Agents and people use the same `/api/actions` command interface:

1. `shell.packages.stage {manifest, source}` returns a digest; it executes nothing.
2. `shell.packages.validate {digest}` runs the host validator. Its receipt binds
   the manifest and compiled bytes to the API/profile and current host runtime.
3. `shell.inspect {clientId}` returns the current revision, composition, package
   registry and browser activation evidence. Find the client ID in the observed
   device or `window.amplifier.shellClientId`.
4. `shell.changes.prepare {clientId, expectedRevision, composition}` returns a
   reviewable change with its previous and proposed composition.
5. `shell.changes.preview` or `shell.changes.apply` takes the client ID, change
   ID and expected composition revision. Preview does not replace the committed
   composition. Both may initially report `awaiting-browser`.
6. Inspect again for the target revision's browser report. `ready` means its
   components mounted. It is separate from package validation and does not
   prove complete visual or functional acceptance.
7. `shell.changes.revert` restores that change's previous composition at the
   supplied current revision. `shell.query {clientId, instanceId}` reads a
   module's current bounded projection.

Use an explicit request `id` to retry a mutation after an uncertain response.
Reusing an ID with different arguments is rejected. Composition revisions are
separate from chat streaming revisions. Stale preparations cannot overwrite
newer compositions. Committed module bytes are rechecked before activation and
serving. Rebuilding/upgrading the host invalidates older validation receipts.

Removal or replacement defers while the instance has a navigation form or
declared dirty state. A replacement with a different `stateSchema` also defers;
the current instance remains active. Matching schemas preserve host-owned view
state; module authors must store durable edits there or mark them dirty. This
does not transfer arbitrary React local state or implement schema migrations.

## Validation and recovery

The validator requires Node and a toolchain containing Playwright/Chromium and
Rollup. A source checkout uses `frontend/node_modules`; a packaged installation
can set `AMPLIFIER_SHELL_NODE_MODULES` to an explicitly installed toolchain.
Missing tooling prevents approval rather than accepting an agent's test claim.

The host starts a sterile loopback fixture with the production React runtime,
checks the compiled artifact for unsupported imports/dynamic code generation,
and exercises empty/populated snapshots, mount/update/unmount/remount and host
subscription cleanup. Network access outside that fixture origin is blocked.
Validation has a 45-second limit and does not hold the app command lock.

This is a **trusted native code profile**, not an isolation boundary. Capabilities
constrain SDK dispatch, but same-origin JavaScript retains app authority. The
closed compiled import graph does not audit the provenance of every already
bundled source line. Validation is a compatibility check, not a security review,
and cannot prove all event-listener cleanup or arbitrary live-data behavior.
Untrusted modules need a future isolated execution profile.

Rendering failures are contained to the component and reported as incomplete
activation. The navigation footer offers last-working and default recovery.
Opening `/?shell=recovery` skips all optional modules and allows restoration of
the default composition. Native infinite loops cannot be caught by a React
error boundary; recovery requires opening that bypass URL. Explicit recovery
can discard module-local edits, but does not clear chat drafts or conversation
data. Last-working compositions are saved only after a successful browser
report; a broken live render cannot promote itself through package validation.

## Reproduce the proof

From the repository root:

```sh
uv sync --frozen
npm ci --prefix frontend
npm exec --prefix frontend playwright install chromium
npm run build --prefix frontend
npm run test:shell-browser --prefix frontend
```

The proof starts a disposable real service and production frontend, opens the
app, then builds the separately authored navigator. It performs stage/validation
and agent-driven preview/apply/reorder/replacement/revert without rebuilding the
host or restarting/reloading it. It checks independent instances/clients, draft
and session retention, the actual iframe node, and a synthetic running job.
It separately tests private imports, failed fixture validation and a live-only
render failure followed by the recovery URL. Real provider calls are not part
of this fixture.

The ordinary chat/workspace browser suites cover the default modules, including
pagination, folder creation, persistence, agent parity, touch controls and old
CSS skins. `tests/test_shell_modules.py` covers stale revisions, idempotency,
changed bytes, stale host receipts, state incompatibility and recovery.

## Baseline and deferred work

The initial focused navigation baseline passed 47 Python tests before edits.
The existing 206-root-chat fixture, bounded pages of 100, desktop and narrow
layouts remain the comparison workload. This proof does not establish
large-transcript performance, voice continuity or compatibility with every
third-party MCP view. Those need their own integration acceptance.

Concurrent work on settings acknowledgement delays, transcript performance,
MCP system-theme updates and update-cache behavior is outside this change.
Existing location-picker actions still use the app's shared listing broker;
this milestone does not provide independently concurrent filesystem pickers.
Package distribution, signing, unattended trust policy, sandboxing, retention
of unused packages/clients, rich layout editing and schema migration remain
separate work. The SDK is provisional until this first milestone is reviewed.

## Navigation milestone verification record

On this branch, the complete Python suite passed **833 tests, with 10 skips**;
all **142 frontend unit tests** passed. Production build, production shell
proof, chat-library browser tests and workspace-explorer browser tests passed.
The shell proof additionally checks a left-side canvas resize, unsaved module
form deferral, and incompatible state replacement deferral. Browser screenshots
and raw timing samples are written under `output/shell-proof/` locally.

A three-sample production comparison used the unchanged baseline checkout and
this checkout on the same machine, with disposable 206-root-chat histories.
These are local observations, not a benchmark or release performance claim.

| Observation | Baseline 0.10.8 | Modular shell |
| --- | ---: | ---: |
| Median startup to 100 chat rows | 98 ms | 135 ms |
| Median search to two matching rows | 28 ms | 16 ms |
| Event streams per page | 1 | 1 |
| DOM nodes after search | 377 | 386 |
| Additional shell query after search | 0 | 44,828 bytes |

The app's legacy navigation projection remains in its regular state response.
Searching inside a module no longer filters that legacy projection, so the
measured app state was approximately 285 KB versus 252 KB after the baseline
search. Removing this duplication belongs with client-aware browser projection
integration; this milestone does not claim to resolve the existing large-state
performance work. Reproduce these samples with
`node frontend/tests/shell-performance.mjs /path/to/baseline-checkout`.

The navigation milestone was subsequently integrated with per-page attachment
and client-aware selection. The historical measurements above are not a
performance claim for the combined application or the content workspace.

## Artifact viewers

`CanvasWorkspace` owns view containers and an Open with selector. A registry
routes every existing artifact format through its standard adapter: Markdown,
text, code, JSON/JSONL, images, HTML, Babylon, Mermaid, Graphviz, A2UI, websites
and MCP Apps. Existing format controls and sandbox restrictions remain in
those adapters. Contributed renderers enter the same host through a validated
manifest and compiled artifact, without a format-specific host branch.

The primary view follows the client's selected artifact. **Open a second
view** pins the current ordinary artifact below it; that view remains bound
when the primary artifact or selected conversation changes. It does not select
the pinned artifact's conversation or change the composer target. Both views
can render the same artifact with different settings and renderers.

Artifacts retain their existing library IDs and immutable stored bodies.
Renderer choice, generation, dirty state, controls and reports belong to the
view within the existing client presentation record. No second artifact store
or conversation store is introduced. Closing a pinned view retains its
preferences and artifact. Reload clones these preferences through normal
client attachment; restarting the host restores the saved records.

MCP Apps retain their existing single active tool binding per client. They can
occupy the primary view alongside an ordinary pinned artifact. Pinning a second
MCP App is rejected. This milestone does not provide arbitrary pane counts,
drag-and-drop layouts, multiple simultaneous tool bindings, conversation
renderer replacement, or automatic state-schema migrations.

### Renderer package and public SDK

Use the same `shell.packages.stage` and `shell.packages.validate` sequence.
A renderer manifest adds a label and its supported resource kinds:

```json
{
  "id": "example.document-reader",
  "label": "Reading view",
  "version": "1.0.0",
  "apiVersion": "1.0",
  "profile": "trusted-native-renderer-v1",
  "stateSchema": "canvas-view-v1",
  "resourceKinds": ["markdown", "text"],
  "capabilities": ["canvas.resource.read", "canvas.view.update"]
}
```

The package exports `({React}) => Component` and receives `{host}`. Public
`useCanvas(React, host)` subscribes to a snapshot containing only `viewId`, the
artifact's identity/content metadata and that view's presentation state.
It does not supply chat history, drafts, credentials or tool capabilities.

| Host API | Behavior |
| --- | --- |
| `getSnapshot()`, `subscribe(listener)` | Observe the bound artifact and view; return subscription cleanup |
| `readSource()` | Explicitly fetch the bound immutable text, including large stored documents; stale bindings reject |
| `dispatch('view.update', {patch})` | Merge presentation state, requiring `canvas.view.update`; accumulated state is limited to 16 KB |
| `dispatch('view.report', {part, status, message})` | Report rendered content with `canvas.view.report` |
| `setDirty(boolean)` | Defer renderer replacement and navigation while edits are pending |

Matching state schemas preserve stored view state across renderer changes.
Arbitrary React local state is not migrated. Modules should prefix their custom
view keys and use the host state for preferences they want to keep. Large HTML
and 3D sources stay out of routine snapshots; `readSource()` is an explicit
on-demand read. Independent example `examples/shell-reader` imports only the
public SDK and preserves its text-size choice without editing source content.

This remains the **trusted native code profile** described above. SDK
capability checks are not an isolation boundary. Validation exercises empty
and populated fixtures for each declared resource kind, plus mount/update/
unmount/remount and subscription cleanup. Fixture success is separate from
live browser activation and cannot establish correctness for all live data.

### Agent actions and precise targets

Attach the intended client and use its normal action transport. HTTP requests
can carry `X-Amplifier-Client`; agents without an attached transport can pass the
observed `clientId` in any `canvas.views.*` action. A conflicting attached client
is rejected, and an unknown ID never creates a presentation implicitly.
`canvas.views.inspect {clientId}` returns current views and compatible,
validated renderer choices. Every view mutation requires all four values from that observation:

```json
{
  "viewId": "primary",
  "resourceId": "observed-artifact-id",
  "resourceRevision": "observed-content-digest",
  "generation": 3
}
```

| Action | Additional arguments and outcome |
| --- | --- |
| `canvas.views.renderer` | Target plus `{renderer}`; choose a built-in ID or validated package digest |
| `canvas.views.command` | Target plus `{action, args}`; explicitly address supported view/report, HTML interaction, A2UI or export controls |
| `canvas.views.dirty` | Target plus `{dirty}`; declare unfinished local edits |
| `canvas.views.recover` | Target; restore its standard viewer, retaining the artifact |
| `canvas.views.status` | Target plus `{status, message}`; browser evidence of loading, ready or error, separate from package validation |

The host rejects changed resource identities, revisions and generations.
Renderer replacements and reopened views invalidate late actions from previous
mounts. Existing action receipt IDs support retries without repeating an
accepted mutation. A missing package retains its saved choice and artifact,
shows the standard viewer and exposes recovery. A live render failure stays
within its view. `?shell=recovery` bypasses optional renderer imports as well as
optional navigation modules; explicit recovery can discard module-local edits.

Opening Library hides the workspace with `hidden` and `inert` while retaining
the mounted viewer and its local state. The action host refuses parent
panel-close operations if the viewer has declared dirty edits. It also
refuses primary artifact/tab, chat, workspace, fork/edit and tool-app transitions
that would replace a dirty primary viewer, before changing the selection,
composer target, binding or generation. These parent operations return HTTP 409
with code `canvas_view_dirty`; existing view-specific replacements return their
deferred result. Shared chat deletion checks affected clients too,
and MCP App loading rechecks after asynchronous resource I/O. The browser
navigation queue waits for preceding dirty declarations so an immediate chat
switch cannot overtake the edit report. Finish or cancel
the edit in its renderer before retrying, or explicitly recover that view to
discard its local edit while retaining the saved artifact. Dirty state is a
navigation guard, not persistence of arbitrary React state across a page reload.

Source and iframe document requests also carry the view target. Each HTML
bridge receives only its own frame messages, even when both views show the same
artifact. Ordinary artifact bodies are fetched on binding changes, not on each
view preference, theme or chat-state update. Both views share the existing
event stream. This is not a large-workload performance claim.

### Content-workspace acceptance

After the dependencies and production build described above:

```sh
npm run test:canvas-renderers-browser --prefix frontend
npm run test:canvas-dirty-browser --prefix frontend
npm run test:smart-tools-browser --prefix frontend
uv run pytest tests/test_canvas_views.py -q
```

The renderer proof opens a disposable production app before independently
building/staging/validating the example. It checks two instances with separate
settings, immutable content, an unchanged HTML iframe and its unsaved input,
preserved composer draft and conversation target, renderer changes, appearance
changes, reload, stale HTTP reads/actions and failed-render recovery. Desktop
and narrow-layout screenshots and structured evidence are written under
`output/canvas-proof/`.

The dirty-view proof validates and loads a minimal editor whose text exists only
in React state. It verifies Library hide/show retains the same input element,
panel-close refusal, a pinned dirty edit surviving primary/chat navigation,
primary transitions leaving drafts and binding generations unchanged, a chat
switch racing a delayed dirty declaration, and explicit targeted recovery
followed by close/reopen.

The MCP proof uses an independently authored official-SDK counter app and a
real local MCP tool server. A delayed accepted tool action finishes once while
appearance/layout/focus change, the iframe survives, and its pinned ordinary
artifact remains bound. It also covers sandbox isolation, stale server
configuration denial and reopening without replaying mutations. These fixtures
make no real provider/model calls; they do not prove voice continuity or every
third-party renderer's behavior. This branch does not restart a user preview,
publish a release or change the application version.

Verification with main at 0.11.11 (`b804878`) and the dirty-view repair:
**1,104 Python tests passed, 11 skipped**, and **154 frontend unit tests passed**.
The production build and ten browser suites passed: dirty-view navigation,
renderer hot loading, MCP Apps, navigation shell, canvas, saved artifacts,
panel layout, actual host restart, empty host and live clients. The final
navigation-queue refinement additionally reruns dirty-view, shell, live-client
and review-during-send coverage. The source distribution and wheel built and
passed release verification. The renderer proof also exercises recovery
startup, including accurate fallback/ready status.

The independently consumable saved-body restore fix (PR #76) restores ordinary
content and surfaces from compact client records after a host restart. This
branch applies the same behavior to its pinned-view resource endpoint. Large
HTML/Babylon bodies remain indirect. The restart proof is
`node frontend/tests/canvas-restart-browser.mjs`; run it from the repository root.

The separate composer preservation fix (PR #79) is also included: an edit or
send in a different chat first queues the previous staged draft with its original
session and payload. It does not delay navigation behind general requests.
The controlled debounce regression checks both host-saved drafts, sends once to
the intended chat, and confirms a canceled timer cannot restore a sent draft.

## Conversation-owned interactive surfaces

Use [the surface contract](conversation-surfaces.md) for dynamic interfaces
that users and agents can operate together and refine in one tab.

Canvas has one viewer per client. Use `canvas.select` to change the selected saved
artifact and `canvas.close` to close it. Legacy secondary preferences are retained
as historical client data but are no longer mounted or advertised as actions.
For a file artifact, `canvas.copyPath {id, format: "absolute" | "relative"}`
copies its path on the app server. The same action is available through the
view command with an exact target; it never opens a local desktop application.
