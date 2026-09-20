# Modular shell: first implementation

This milestone implements the navigation slice of the shell plan, based on
Unified `81f2182b6112e4162d982291151c489bdec6ef8a` (0.10.8). The workspace
manager and conversation list are independent registered components. Additional
instances can follow the active workspace, pin a workspace, or show all chats.
An external module can replace either component while the app stays open.

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

Shell compositions and view state are scoped to a browser client ID, stored in
session storage so reloads retain that client's shell. A new browser tab starts
with a default composition. A browser's Duplicate Tab operation may copy
session storage; durable user/device identity and synchronized preferences are
later work. Active conversation selection is still the existing shared app
selection. Independent module filters and pinned contexts do not imply
independent conversations in separate browsers.

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
use the existing theme service. Full theme bundles, arbitrary slots, canvas
renderer replacement and conversation decomposition are later milestones.

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

## Verification record

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

The concurrent live-client branch is introducing per-page attachment and
client-aware selection. Before integration, replace the temporary session-storage
identity with that attachment contract, adapt `ShellModules.scoped_state` to the
client's active selection, preserve the multiplexed shell event, and rebuild
assets once from the combined source. No version or release bump is included.
