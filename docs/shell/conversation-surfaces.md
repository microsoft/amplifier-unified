# Interactive surfaces for a conversation

An agent can create a rich, isolated HTML/CSS/JavaScript interface for the
conversation it is helping with. The user may operate it directly, ask the
agent to operate it, or continue chatting. The shell owns identity, validated
state, revision history and host actions. These are session artifacts, not
installed apps or a distribution catalog. There is no built-in theme picker.

## Discover and create

Use `app_control(operation="list_actions", args={"prefix":"canvas.apps."})`
for current schemas. `canvas.apps.create` takes `title`, self-contained HTML
`content`, a `manifest`, and `initialState`. Agent calls are scoped to their
calling conversation. An optional `clientId` addresses a specific attached
browser; publishing in a background conversation never changes its selection.
Keep the returned `id`: every refinement uses it again.

Minimal manifest:

```json
{
  "version": 1,
  "theme": "inherit",
  "stateSchema": {
    "type": "object",
    "properties": {"selection": {"type": "string"}},
    "required": ["selection"],
    "additionalProperties": false
  },
  "events": {
    "choose": {
      "label": "Choose an option",
      "schema": {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": false
      },
      "updates": {"selection": "value"}
    }
  },
  "requests": {}
}
```

Use semantic HTML with labels and keyboard controls. The host injects a small
`window.canvasApp` bridge before the document runs:

```javascript
const draw = snapshot => {
  document.querySelector('#choice').value = snapshot.app.state.selection;
};
canvasApp.subscribe(draw);
canvasApp.ready.then(draw);
document.querySelector('#choice').onchange = event => {
  canvasApp.emit('choose', {value: event.target.value},
    {commit: canvasApp.getEditVersion()}).catch(showError);
};
```

- `ready`: promise of the first snapshot.
- `getSnapshot()` / `subscribe(fn)`: read shared state, manifest, revisions,
  events, host request status, and current theme context. Subscribe returns an
  unsubscribe function. Theme and state updates do not recreate the frame.
- `emit(name, payload, {commit}?)`: validate a declared event and apply its field mapping
  on the host. `updates` maps top-level state fields to payload fields. The
  agent invokes exactly the same operation with `canvas.apps.event`.
- `patch(object, {commit}?)`: merge top-level fields using `canvas.apps.state`.
- `request(name, input)`: queue a declared host request for review.
- `beginEdit()`: immediately protect a custom local edit and return its integer
  edit version. Call before changing a drawing or other non-form input.
- `getEditVersion()`: capture the current local edit version. Native form input
  advances it automatically before your input handler runs.
- Pass `{commit: capturedVersion}` to a state/event write only when that write
  saves all unfinished input through that version. A successful unrelated event
  does **not** mark input saved. Older acknowledgements cannot clear newer edits.
- `setDirty(true)` remains available and immediately begins an edit, then
  acknowledges its declaration. `setDirty(false)` explicitly discards the edit
  captured at call time; use only for a user-requested cancel, never after a failed save.
- `reportError(error)` / `reportReady()`: report a rendering failure or successful
  recovery. State acknowledgements do not clear rendering errors.
- `observeCanvas(canvas, draw)`: observe positive visible canvas dimensions,
  change the backing size only when needed, set the device-pixel transform,
  and call `draw({context, width, height, dpr})`. Returns `{redraw, disconnect}`.
  Hidden canvases are skipped and redrawn when revealed. Draw from saved state;
  changing the backing size still clears pixels and resets context attributes.

Existing surfaces that relied on implicit save acknowledgement must pass an
explicit commit version. Without it the host conservatively retains unsaved
input protection. Ordinary shared-state writes remain compatible.

State and event calls resolve with an acknowledged snapshot. Display errors;
do not silently retry a rejected edit over someone else's change. Avoid
replacing the active text field with older acknowledged text while the user
is still typing. This version uses one state revision per surface, so edits
from two users may conflict even when they touch different fields.

## Read, refine and restore

`canvas.apps.inspect {id}` returns the current manifest, state, requests and
retained versions. Use `includeSource: true` to read the complete current HTML;
use `requestId` to read the exact input of a pending host request. The result also lists view client IDs, dirty flags and browser render status.
Canvas action results use a compact context with target-client information;
use `get_state` with a JSON Pointer for broader app state.

Mutations require `id`, `expectedRevision` and `expectedStateRevision` from
inspection. Use stable command IDs when retrying an uncertain transport
outcome. A stale revision is a conflict, not permission to overwrite state.

`canvas.apps.revise` replaces HTML and optionally manifest/title, retaining the
same artifact ID, tab and compatible state. If the new schema rejects current
state, the action fails atomically. Supply an explicitly considered
`migratedState` to migrate it; preserve user values wherever possible.

`canvas.apps.restore {version, ...revisions}` restores an earlier definition
as a new revision, preserving current compatible state. It does not rewind
user inputs or replay host actions. Up to 20 definitions remain available.
Revising or restoring supersedes pending host requests.

Dirty mounted views block definition replacement. If an input races a remote
revision before the host receives the dirty declaration, the browser retains
the old frame and offers an explicit discard-and-load control. Read and
reconcile the shared state before retrying. Provide a Save/Retry control for
local edits whose first save failed. Save the complete compatible edit, inspect
again, then revise; explain any remaining unsaved work to the user. Never invoke viewer recovery to
discard someone else's unfinished work automatically.

State survives refresh and conversation reopening. Forks copy the retained
surface into a new identity with independent state and no pending approvals.
Closing a tab preserves the surface. Existing `canvas.show` continues to
publish separate immutable snapshots; use it for documents, not iterative UX.

## Host actions and theme context

HTML runs in an opaque-origin iframe with scripts allowed, inside a trusted
container whose CSP permits only the exact surface document path. This blocks
self-navigation to external URLs as well as to other host endpoints. It cannot read
host DOM/cookies, fetch the network, access arbitrary files, run tools or
dispatch arbitrary app actions. Parent messages are checked against the exact
frame, bridge channel and definition revision. The bridge exposes only state,
declared events and declared requests. Host approval is outside the iframe.

The initial action allowlist is `theme.preview`, `theme.apply`, `theme.revert`.
Declare a request with `action` and an input `schema`. Preview/apply inputs
are preferably `{name, tokens}` for palette changes. `tokens` is a nonempty
map of the host token names below to six-digit hex colors, for example
`{name: "Forest", tokens: {accent: "#3e7052", bg: "#eef3eb"}}`. The host
retains the complete applied skin and materializes the exact CSS for review;
authors do not need to copy the existing stylesheet. For deeper changes,
`{name, css}` accepts complete, self-contained shell CSS. Supply tokens or CSS,
not both. Revert uses `{}`.
The host validates both the declared schema and the actual action schema.
Queued requests have no effect until resolved. The host's review controls
show exact CSS, scope and approve/decline actions. They use the same theme
implementation as ordinary controls and agent commands.

Preview is local to the chosen client; apply changes the shared shell. Revert
ends a local preview first, otherwise undoes that client's last application
only if no newer theme has replaced it. Approval rejects a request based on
an outdated shell theme. `canvas.apps.resolve` lets an authorized agent use
the same path, with an explicit `clientId` and current revisions. The sandbox
cannot approve its own request. An agent should resolve only changes covered
by the user's instruction.

Every snapshot includes `theme.version: 1`, host color tokens, scheme, reduced
motion and contrast preferences. `manifest.theme` is `inherit` (default),
`accent-only`, or `fixed`. In inherited mode the iframe root receives
`--host-bg`, `--host-surface`, `--host-soft`, `--host-ink`, `--host-muted`,
`--host-line`, `--host-accent`, `--host-tint`, `--host-green`, `--host-danger`.
Accent-only supplies just the accent CSS token. Fixed supplies no CSS tokens;
the read-only accessibility context remains available. Authors are responsible
for semantic controls, adequate contrast and honoring reduced motion.

Limits: HTML 500,000 characters; manifest 32 KB; shared state 100 KB; event
payload 16 KB; host request input 300 KB; 32 declarations per group; 50 recent
events; 20 request results with at most five pending. Schemas exclude
references and regexes; data excludes reserved prototype/resource keys.
Complex state reducers, arbitrary host capabilities, automatic agent wakeups,
cross-host sync and app distribution remain outside this version.

## Drawing, tabs and save failures

User choices, sliders, pen selection and completed strokes belong in typed shared
state with declared events. Pointer position, animation frames and a stroke still
being drawn may stay local temporarily. Keep stroke data within the state/event
limits; bound/simplify point lists and commit completed strokes in batches.

```javascript
let local = null;
let pendingSave = false;
const painter = canvasApp.observeCanvas(canvas, ({context, width, height}) => {
  context.clearRect(0, 0, width, height);
  context.lineWidth = 2.5; context.lineCap = 'round';
  drawStrokes(context, local ?? canvasApp.getSnapshot()?.app.state.strokes ?? []);
});
function beginStroke() {
  canvasApp.beginEdit();
  local = structuredClone(canvasApp.getSnapshot().app.state.strokes);
  // Append normalized points to local, then painter.redraw() while drawing.
}
async function saveDrawing() {
  const commit = canvasApp.getEditVersion();
  const strokes = structuredClone(local);
  pendingSave = true;
  try {
    await canvasApp.patch({strokes}, {commit});
    if (canvasApp.getEditVersion() === commit) local = null;
  } catch (error) {
    showError(error); // Keep local strokes; a visible Save/Retry calls this again.
  } finally { pendingSave = false; painter.redraw(); }
}
canvasApp.subscribe(() => { if (!pendingSave) painter.redraw(); });
```

Render all strokes from normalized coordinates after resizing or returning to a
tab. Do not set canvas width/height on every click or snapshot. Use CSS dimensions
independent of the backing size. For a clock, skip hidden or very small bounds
before calculating its radius; redraw on reveal and respect reduced motion.
Subscriptions are deduplicated and deferred while local writes are pending, but
an author must still preserve unsaved local work when new shared state arrives.

For authorized theme application, the agent (outside the sandbox) performs:

1. Inspect the surface and intended browser client.
2. Queue the declared apply request using current revisions and small tokens.
3. Inspect its exact request input and current revisions.
4. Call `canvas.apps.resolve` with `id`, `clientId`, `requestId`, `approve: true`,
   `expectedRevision`, and `expectedStateRevision`.
5. Verify the request result and the target view. Do not ask again for permission
   already supplied by the user. Resolve only the change that was authorized.

## Acceptance evidence

The browser acceptance test generates a theme chooser as conversation data.
It exercises user selection, the shared agent action path, refinement in the
same tab, retained notes, previews, application, reversal, refresh, restoration,
and preserved composer drafts. It also checks sandbox DOM/network isolation.
The theme chooser is test/demo content and is not loaded into ordinary chats.

Run `npm run test:canvas-apps-browser` in `frontend` after building the frontend.
`AMPLIFIER_TEST_PYTHON` may point to the repository's Python environment.
Backend coverage lives in `tests/test_canvas_apps.py`.
