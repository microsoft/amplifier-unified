# Smart Tools and MCP Apps

Unified adds first-class support for optional Smart Tool adapters while retaining its
AmplifierSession, bundles, worker lanes, chat, notifications, and loop-live voice flow.
The host contains no imports, routes, or special cases for individual Smart Tools.

## Add a tool

1. Open **Settings → Capabilities → Smart Tools**. Browse the community catalog or
   paste a credential-free HTTPS Git repository URL into **Add from Git**.
2. Inspect the source. The descriptor and manifest are read from one resolved commit;
   inspection does not run a tool or interpret install instructions as commands.
3. Install the package when ready. This first installer supports Python projects;
   optional package extras such as `mcp` can be supplied. Each commit/extras combination
   gets its own environment. Other runtimes can be installed with their documented
   setup and then registered as MCP connections.
4. Add an MCP connection using the tool's documented executable and argument array.
   Each argument occupies one line. Set its retained-data directory explicitly.
   Environment entries map child variable names to existing host variable names,
   for example `OPENAI_API_KEY=MY_TOOL_KEY`; the form never takes key values.
5. Connect. Unified discovers the server's schemas and optional views. A catalog
   entry, an installed package, and a connected server are different states.
6. Run a tool through its schema-derived form, or ask the conversation agent to do it.
   For a tool advertising an MCP App, **Open interactive view** sends the latest
   completed call from this chat into a new canvas tab. No launch call is replayed.

Tool-owned AI does not automatically use the chat's model or provider credentials.
Configure generation using that tool's documented setup and bounded grants. For
example, an adapter that requires `--model-env` still requires that explicit option
and the intended environment references. Deterministic inspection, review, and
export need not invoke a model.

## Shared actions and state

The UI, `window.amplifier`, and agent `app_control` use the same actions:

- `smartTools.catalog`, `inspect`, `install`, `configure`, `connect`, `disconnect`, `remove`.
- `smartTools.call {id,name,arguments,sessionId?}` returns an `operationId` receipt.
- `smartTools.resources {id,kind?,cursor?}` lists resources or templates one page
  at a time; `smartTools.readResource {id,uri}` reads through that server.
- `smartTools.open {id,tool,operationId?,sessionId?}` attaches a standard view.
- `smartTools.result {operationId}` exposes a retained receipt at
  `/smartTools/inspectedOperation`, including pageable references for large results.
- `smartTools.appCall` is scoped to the current canvas's server and granted tool names.
  Agent callers still obey model visibility; views obey app visibility.
- `smartTools.context` records bounded view context. It is observation, not an instruction
  or permission to generate, and does not automatically start a conversation turn.

Server instructions and tool schemas are discoverable under `/smartTools/servers`.
Interactive views use a scoped HTTP wait for their normal `smartTools.appCall`
action, so completed calls return immediately without a browser polling delay.
The same admission, tool visibility, retained receipt and request-ID deduplication
apply. Closing the HTTP request never cancels or replays an admitted tool call.
Interactive calls commit admission and operation receipts immediately, while batching
full application-view persistence and broadcasts. Explicit state reads and revision
checks flush pending view updates; a crash preserves completed receipts and marks
unfinished work interrupted rather than replaying it.
Routine tool interaction does not toggle the host's rendering indicator; the
tool owns its progress controls, while errors remain visible in the host.
Small operation receipts persist in SQLite. Full results are retained for at most
30 days, 200 completed operations and 32 MB (whichever limit comes first), in
pageable artifact files. Expiry keeps the execution receipt and never replays work.
Saved canvas artifacts remain reachable independently of result retention; static
HTML is stored once even when live view context changes. Failed operations produce the same acknowledgeable
attention indicators used elsewhere in Settings. A request accepted by the host is
not proof of domain completion: tools may return their own durable operation handles.

App backups include tool work stored under `smart-tools/work` in the app data directory. Tools configured to retain work elsewhere need their own backup; a canvas snapshot is not a complete tool-store backup.

Each canvas artifact saves its HTML, server configuration binding, launch operation,
and observed context in the chat's artifact library. The tool owns live work, immutable
revision IDs, drafts, and conflict rules. `updateModelContext` does not itself promise
view restoration; domain drafts should be persisted through the tool's public API.
Closing a tab hides it without canceling work. Reopening retrieves saved input/result
and reconnects presentation. After host restart, connections stay disconnected until
explicitly connected; uncertain work is marked interrupted and never replayed.

A fork copies a reference to the same tool work, not a tool-specific clone. Forked
views display a shared-work notice. Ask the tool to clone if it exposes that capability.
Updating a server configuration invalidates its old view bindings and launch receipts;
open a fresh view after reconnecting.

## Supported protocol profile

Uses the official Python MCP SDK and official MCP Apps `AppBridge`, not a new protocol.
The client advertises `io.modelcontextprotocol/ui` with `text/html;profile=mcp-app`.
Tools advertise `_meta.ui.resourceUri`; the host reads the corresponding `ui://`
resource. UI permission/CSP requests are read from the resource's metadata.

This initial host profile supports:

- Local stdio MCP servers with typed tool discovery, calls, and UI resource reads.
- Self-contained MCP App HTML; scripts/styles are inline, images/media are embedded,
  and nested generated previews may use isolated blob frames.
- Scoped `tools/list`, `tools/call`, and text/structured `ui/update-model-context`.
- Standard `resources/list`, `resources/templates/list`, and `resources/read`
  through the view's saved server binding. The host never fetches resource URIs
  itself. Encoded responses are limited to 2 MB; large media uses tool-defined
  bounded chunk resources assembled into blob URLs by the App. Domain resource
  authorization belongs to the configured server. Closed views and changed
  connections cannot continue reads. Media reads do not bloat chat history.
- Explicit environment references, input validation, source-window checks, opaque
  iframe origins, stale-configuration checks, and recorded results.
- User-clicked downloads from tool views. Other device permissions and network/asset
  domains are denied and the granted sandbox profile is advertised to the view.

Remote Streamable HTTP/SSE servers, host sampling, elicitation, MCP Tasks,
resource subscriptions, and external UI asset/network grants are not implemented.
A server's native operation handle is not claimed to be an MCP Task. Model costs
remain server-provided data; missing usage is unknown, not zero, and is not rolled
into chat spending as if independently metered by Unified.

The installer executes a package build only on an explicit install action. Connecting
executes a configured process. The app scopes iframe access; it is not an OS sandbox
for installed code. Tool metadata and rendered content remain untrusted data.

## Validation

`pytest tests/test_smart_tools.py tests/test_smart_canvas.py` covers real SDK subprocess
calls, visibility, environment isolation, schema/resource validation, timeouts,
configuration races, restart and duplicate receipts, durable paging, and canvas bindings.
`npm --prefix frontend run test:smart-tools-browser` uses an unfamiliar independent
MCP App and the real host to verify rendering, shared state, nested-message isolation,
reopen behavior, and stale configuration failures without provider calls.

Possibly's optional adapter is tested separately, including against an independent
MCP Apps host. Tool-specific integration tests live with that adapter or in development
reports, keeping Unified's production implementation generic. The collaborative-surface
proposal remains a discussion draft, not an adopted standard or conformance claim.
