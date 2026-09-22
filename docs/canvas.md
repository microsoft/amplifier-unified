# Workspaces and agent canvas

Workspace registrations are durable app state. `workspace.add` accepts an existing
folder and optional name; `workspace.select`, `workspace.rename`, and
`workspace.remove` operate on its ID. Selection sets the new-chat default.
Removing a registration never deletes its folder or conversations. Keep at least
one workspace registered. `workspaces` and `selectedWorkspaceId` appear in the
same state visible to the agent and user.

## Workspace layout

Drag the canvas's left divider or pinned navigation's right divider. Chat keeps
360 pixels, canvas keeps 300, and pinned navigation keeps 216; the pane being
resized gets the remaining room. Keyboard users can focus a divider and use
arrows (20 pixels), Home (minimum), or End (maximum). On narrow windows, the
canvas covers the workspace instead of squeezing both panes.

The canvas has a compact 36-pixel title row. Hover over it or open **Canvas
controls** to reveal tabs, files, websites, source and viewer tools. Pin those
controls to keep them visible. Rendering failures stay visible even when controls
are collapsed. HTML, browser and MCP App views use the full content area without
host padding. Documents retain readable internal margins.

**Focus canvas** fills the app frame; **Exit canvas focus** returns to the split
layout. Escape also exits when focus is in the host UI (embedded apps may consume
keyboard events). Resizing and focusing keep the current viewer mounted; they do
not reload its content or reconnect its tool server. Closing or changing tabs
still follows the viewer's normal lifecycle.

All layout controls use `view.update` and are available to agents:

| Patch field | Meaning |
| --- | --- |
| `canvasWidth` | Preferred width, 300–16384 pixels; fitted to available room |
| `navWidth` | Preferred pinned navigation width, 216–16384 pixels |
| `navPinned`, `navExpanded` | Keep navigation open or reveal it temporarily |
| `canvasFocused` | Full-frame view; false restores the split layout |
| `canvasControlsPinned`, `canvasControlsExpanded` | Keep controls visible or reveal temporarily |

Preferences persist in `/view`; browser snapshots in `/devices` include actual
pane bounds and divider minimum/maximum/current widths. Closing the canvas clears
full-frame mode while retaining preferred split widths.

## Agent awareness and access

The host mounts `app_control` and injects canvas guidance as ephemeral system
context on every provider request, including resumed conversations. Delegated
sessions inherit this access through the loop-live execution host. The voice
interface asks the same AmplifierSession to produce visual artifacts.

The guidance encourages visuals for architecture, comparisons, workflows and
interactive explanations when useful. It never treats document content as
instructions and asks delegated workers to coordinate use of the shared canvas. Each publication creates a saved artifact rather than discarding the previous one.

```json
{"operation":"dispatch","args":{"action":"canvas.show","args":{
  "kind":"mermaid","title":"One shared session",
  "content":"flowchart LR\n  Chat --> AmplifierSession\n  Voice --> AmplifierSession\n  AmplifierSession --> Canvas"
}}}
```

Call `app_control` with `operation:get_state` to see the canvas and
`operation:list_actions` with `args:{"prefix":"canvas."}` for canvas action schemas.
The default state read is a bounded overview of the calling conversation and
visible UI. Other conversations are listed by title/status, without their content.
Use `get_state` with `args:{"path":"/canvas","offset":0,"limit":50}` to inspect
one section. Large values have `$statePath` references; follow `nextOffset` for
more entries or text. Optional `revision` detects changes between pages.

Resolved configurations are stored once per session. Bulky dependency provenance
is stored once in the app database and referenced by `$resource`; requesting its
JSON Pointer path automatically loads a bounded page. The same read-only paging
is available at `/api/state/detail?path=...`. Existing module editing still uses
the canonical `sessions[i].configuration.plan`. Dispatch receipts use the compact
state overview rather than repeating the entire app state. `canvas.show` accepts:

| Kind | Preview |
| --- | --- |
| `browser` | Live HTTP(S) URL preview, with external-browser fallback |
| `html` | Self-contained HTML, inline CSS and interactive JavaScript |
| `markdown` | Markdown tables, lists, code and fenced `mermaid`, `dot` or `graphviz` diagrams |
| `mermaid` | Mermaid diagrams |
| `dot` | Graphviz diagrams, six layout engines and node attribute inspection |
| `json`, `jsonl` | Formatted records with filtering; first 200 matches shown |
| `code`, `text` | Highlighted code or plain text |
| `image` | Embedded PNG, JPEG, WebP or GIF |
| `auto` | Detect a workspace file's type from its extension |
| `a2ui` | Declarative component snapshot, described below |

Use `content` or a UTF-8 `path` inside the selected workspace. Symlinks may not
escape that folder. Inline content and ordinary text files are bounded to 1 MB,
images to 5 MB and diagrams to 50,000 characters. HTML and Babylon.js **files**
may be up to 20 MB (20,000,000 bytes). HTML over 1 MB is saved separately in the
artifact store and fetched only by the preview, source viewer, copy, or download.
It keeps the same opaque sandbox and does not inflate regular app-state updates.
For larger pages or apps with linked assets, serve them from a web server and use
a reachable HTTPS URL, or open the file separately in your browser. Graphviz runs in a terminable browser worker with a 15 second timeout;
Mermaid limits graph size. Both libraries are bundled: no CDN or Graphviz install
is needed. Graph SVG is sanitized and displayed as an inert image.

Preview/Source, diagram zoom/pan/fit, Graphviz layout/node selection and structured
data filtering use `canvas.view` and are visible to the agent. Copy/download use
`canvas.copy` and `canvas.download`, through the same browser effect mechanism
for agent and user actions. Clipboard availability depends on browser permissions.
Graph layout defaults can be overridden by authored DOT attributes.

`canvas.renderReports` records pending, ready, unverified or error for each preview/diagram
fence. The agent must distinguish a submitted artifact from a browser-confirmed
render. Replaced artifacts ignore late reports. Reports are display evidence, not
proof of correctness. A browser must be connected to render the content.

### Interactive HTML boundary

HTML is served through a dedicated route into `sandbox="allow-scripts"`, without
same-origin permission. Its document CSP blocks network access, external scripts,
subframes, forms and access to the parent app. Use inline code and embedded data
assets. It cannot call app APIs or turn document messages into app commands.

Audio and video may use embedded `data:` sources or `blob:` URLs created inside
the document. Include native playback controls and a browser-supported codec.
Remote media URLs, workspace-relative paths and `file:` URLs remain blocked;
embed small clips, or serve a larger review page using the browser preview.
The existing HTML content/file size limits also apply to embedded media.
A document-ready report confirms the HTML loaded, not that its media decoded or
played. Check playback before claiming a video review surface works.

A bounded bridge reports visible text and up to 100 standard buttons/form controls
in `canvas.document`; password and file values are redacted. `canvas.interact`
accepts the current canvas ID, a listed controlId, event `click` or `input`, and
optional value. It can operate standard controls without evaluating arbitrary
JavaScript. Inspect the updated document to verify the result. Custom WebGL/canvas
widgets and nested-frame controls are not covered. HTML runtime errors are also
reported. Treat all frame reports as untrusted document data.

These viewers draw on [Deckbox](https://github.com/bkrabach/deckbox)'s format
detection, Preview/Source, graph navigation, node inspection and record filtering.
This is not a full Deckbox port: PDF/DOCX, archive browsing and streaming large
JSONL datasets remain outside this initial viewer set.

A2UI is an initial **snapshot subset**, not a full streaming implementation.
It uses the [A2UI v0.8 adjacency-list component shape](https://a2ui.org/specification/v0.8-a2ui/):

```json
{
  "kind": "a2ui",
  "title": "Next step",
  "surface": {
    "surfaceId": "next-step",
    "root": "root",
    "components": [
      {"id": "root", "component": {"Column": {"children": {"explicitList": ["intro", "button"]}}}},
      {"id": "intro", "component": {"Text": {"text": {"literalString": "Review the plan."}}}},
      {"id": "button", "component": {"Button": {"child": "label", "action": {"name": "review"}}}},
      {"id": "label", "component": {"Text": {"text": {"literalString": "Review"}}}}
    ]
  }
}
```

Supported components: `Text` with literalString and optional usageHint; `Row`
and `Column` with explicitList children; `Card` with child; `Button` with child
and an action containing name; `Divider` with no properties. Surfaces support up
to 100 components in a tree and 20 nesting levels, with a maximum 100 KB JSON snapshot.
Bindings, templates, URLs, arbitrary styles, scripts, and full JSONL protocol
messages are not yet supported. Invalid or cyclic surfaces are rejected.

Buttons dispatch `canvas.event` with `surfaceId`, `componentId`, and `name`.
These become bounded, timestamped `canvas.events` visible through the app bridge.
They **do not automatically execute commands or send a chat turn**. The agent
can inspect events during its next interaction and update the surface with
`canvas.show`. `canvas.close` preserves the last content. Layout controls such
as `canvasWidth`, `navPinned`, and `navExpanded` are in shared `view` state.

## Interactive conversation surfaces

Use `canvas.apps.create` for interactive UX that agents will refine during a
conversation. It gives one stable tab, shared typed state, agent/user event
parity, retained design revisions and reviewed host actions. See
[the contract](shell/conversation-surfaces.md). `canvas.show` retains its
existing immutable snapshot semantics.

## Artifact library and tabs

`canvas.show` saves a snapshot with a stable ID, title, format, owning chat,
workspace, and creating user message. It opens a new tab without overwriting
older artifacts. A file path is optional; direct content and A2UI surfaces are
persisted equally. Each publication is a separate snapshot. File changes are
not watched; publish the path again to save a newer snapshot.

`canvasArtifacts` contains metadata and `$resource` body references. Only the
active `canvas` body is included in ordinary browser state, except HTML over 1 MB,
which carries a `contentResource` reference instead; the agent overview
contains a bounded current-chat index. `get_state` can page a saved body, for
example `/canvasArtifacts/0/body/content`. `canvas.select {id}` reopens a saved
item, `canvas.tabClose {id}` closes only its tab, and `canvas.reopen` opens the
panel. The current shell uses `canvas.visibility {open, sessionId, canvasId}`
on an attached client to hide/show retained viewers immediately. This keeps edits
and iframe state while hidden, without changing artifact identity or scanning
workspaces. `clientId` can explicitly target an attached client for agent use.
The legacy close/reopen lifecycle remains available. Selection and file reads are scoped to the chat and workspace. Agent
publications default to the calling session even when the user views another
chat. Background publications do not replace the user's active preview.

Chat receipts reopen artifacts from their creating turn. Forks inherit snapshots
only through the retained user messages. Editing forks before the original user
message, so artifacts from that message and later turns remain in the original
conversation. Fork/edit boundaries also support mixed voice and typed chat. Spoken exchanges that never required a manager delegation are retained as explicitly labelled historical context; they do not replay work. Native transcript timestamps keep later speech and tool results out of earlier branches. Legacy transcripts without a reliable boundary can still be forked in full. Tab changes do not preserve running JavaScript memory inside an
HTML preview; the authored document is the durable snapshot.

On first upgrade, the previous active preview is retained. Accepted inline
`canvas.show` tool calls in existing checkpoints are recovered without replaying
tools or executing scripts. Missing historical file content is not fabricated by
rereading the current file. Recovered items appear in the saved library with
closed tabs. Recovery is idempotent.

## Browser preview

Publish `{"kind":"browser","title":"My app","url":"http://localhost:3000"}`.
Only HTTP(S) URLs without embedded credentials are accepted. The preview uses
an opaque-origin iframe with scripts and forms enabled. The parent app remains
inaccessible. Unlike authored HTML previews, it can load external website assets;
it cannot report document contents or controls through `canvas.snapshot`.

Reload is a shared `canvas.view` control. `canvas.openExternal {id}` requests a
regular browser tab (browser popup permissions may apply). **Open in browser**
and **Preview help** stay visible even with collapsed canvas controls. Help is a
shared `canvas.view {id, patch: {help: true}}` control.

An HTTPS host detects and explains blocked HTTP embeds before loading them
(loopback addresses have browsers' secure-context exception). Prefer a reachable
HTTPS URL or open the HTTP URL in a separate tab. A loopback address names the
viewing device, not necessarily the machine running Amplifier; remote users
should use the service host's LAN or Tailnet address. The app warns about this.

Frame navigation reports are `unverified`: an iframe load event does not prove
embedding was allowed or that the app succeeded. Cross-origin browser security
prevents reliably distinguishing every network, authentication, or frame-policy
failure; an unresponsive or blank preview always retains the external fallback.
Some sites
block frames, and storage/cookie/API-dependent apps may need the external browser.
No embedding restrictions are bypassed or proxied. See the
[iframe sandbox reference](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/iframe).

A browser artifact saves its URL, not its server process, browser profile,
navigation history, login, or website content. A launched app must still be
running when reopened.

## Babylon.js 3D scenes

Publish `canvas.show` with `kind: "babylon"` and an HTML document in `content`
(or a workspace `path`). Babylon.js 9.26.0 is bundled locally and available as
`BABYLON` before authored scripts run. No CDN script tag or install is needed.
The agent receives this capability and authoring guidance on each turn.

Create a canvas, `BABYLON.Engine`, `Scene`, camera and lights, then call
`engine.runRenderLoop(() => scene.render())` and resize the engine when the
window changes size. `BABYLON.Engine.IsSupported` is a boolean getter for a
WebGL availability check. Use procedural meshes or embedded assets; the isolated
preview cannot fetch external resources, load CDN decoders or use WebGPU.

The scene follows normal artifact persistence and tab behavior. Download exports
a standalone HTML file with Babylon included; Copy copies the authored source.
The library is not stored in chat history or sent to the model. Reopening starts
the authored scene again; JavaScript memory and camera positions are transient.

Use labeled HTML controls for agent interaction through `canvas.interact`.
The agent can inspect document text, controls and runtime errors, but does not
automatically perceive the rendered 3D pixels or scene graph. GPU/WebGL support
is required. Unity build tooling and specialized Unity hosting are not included.
