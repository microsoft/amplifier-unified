# Amplifier TUI: Unified-backed live sessions

Status: Ready for TUI implementation. Use Unified 0.11.9 or later.

## Outcome and scope

Add a connected backend to the TUI. A user can open the same Unified-hosted
conversation in the TUI and the web app, submit from either, and follow the same
accepted messages, live responses, tool/worker progress and pending approvals.
Each interface keeps its own selection and unsent drafts.

Unified runs the session on its host. The connected TUI does not construct an
Amplifier execution runtime, acquire the Foundation session lock, write session
history, or move execution to the terminal's machine. Existing standalone CLI
ownership and explicit takeover remain separate operations.

This document describes the service integration; it does not prescribe a TUI
framework or require replacing an existing standalone backend. No external
amplifier-app-tui repository has been modified by this work.

## Release baseline and source of truth

- Protocol: version 1, HTTP commands and SSE snapshots.
- Recommended host: [Unified 0.11.9](https://github.com/bkrabach/amplifier-unified/releases/tag/v0.11.9)
  or a later compatible release, which corrects canvas resource retention across
  clients. Use the immutable release tag to resolve its commit.
- Live-client validation baseline: Unified 0.11.7. Its exact evidence and runtime
  revisions remain recorded in the validation document linked below.
- Pinned loop-live: `11a730ac24463cb5bea8bd65385f494aa8f0f454`.
  This includes the scheduling/ownership fix from
  [loop-live PR 5](https://github.com/bkrabach/amplifier-module-loop-live/pull/5).
- Empty-composer draft support first shipped in Unified 0.11.4; the live-client
  transport first shipped in 0.11.1. The combined live-client checks were validated on 0.11.7; use 0.11.9 or later
  for the resource-retention correction.
- Detailed contract: [live-sessions.md](live-sessions.md).
- Server: `amplifier_web/live_clients.py`.
- Python adapter: `amplifier_web/session_client.py`.
- Schemas and command behavior: `amplifier_web/service.py`.
- Contract tests: `tests/test_live_clients.py`.
- Actual worker/terminal lifecycle probe: `tests/test_live_terminal_runtime.py`.
- Validation and limitations: [tui-handoff.md](../validation/tui-handoff.md).

For TUI settings or a separate standalone runtime, see the companion
[shared-configuration guide](https://github.com/bkrabach/amplifier-unified/blob/85013b9b7a98b377dbfe04d2ed53f7108abaf515/docs/TUI-SHARED-CONFIGURATION.md)
from [PR 69](https://github.com/bkrabach/amplifier-unified/pull/69). That optional
adapter is a separate contribution, not part of the 0.11.7 host baseline. A
connected TUI uses the host's configured runtime and does not need that helper
to attach to live sessions.

Discover action schemas with `GET /api/actions`; do not infer them from button
labels. Use the adapter from the verified Unified release. The package currently
requires Python 3.13 or newer; the protocol can be implemented by other languages
or Python versions without importing the server or runtime.

## TUI implementation work

1. Add a Unified connection configuration: base URL, credential reference and
   server certificate trust. Show which host owns the workspace.
2. Implement a connected backend using `SessionClient` or equivalent HTTP/SSE
   calls. Keep it separate from any in-process execution backend.
3. Add session listing, creation and selection. Keep navigation local to the TUI;
   fetch and subscribe using the explicitly chosen session ID.
4. Run the snapshot reader independently of input and command requests. A slow
   command acknowledgement must not freeze typing, navigation or rendering.
5. Add an outbox for uncertain command delivery, with stable IDs and payloads.
6. Map messages, streaming text, work status, approvals and ownership to terminal
   views. Keep unknown or unsupported display fields harmless.
7. Implement explicit stop/takeover actions and ordinary detach/exit separately.
8. Run the acceptance checklist below against the pinned release.

The first useful milestone is session selection, text send, live replies,
independent drafts, reconnect and detach. Approvals and ownership errors must be
visible even before richer worker controls are added. Attachments, artifact
viewers and advanced runtime controls can follow.

## Connect and authenticate

Use the existing Unified control bearer token for a native client:

```http
Authorization: Bearer <control-token>
X-Amplifier-Client: <client-id>
```

On the host, the owner-only token file is
`<Unified data directory>/config/auth/control-token`. The data directory is the
configured app directory, not necessarily a hard-coded home path. For remote
clients, provision the credential through the app's credential setup and store
it privately. Do not log it or put it in a URL. This is currently a service
control credential, not a per-client restricted capability.

Client IDs are presentation identities, not authentication. Use a distinct ID
for each independent running TUI instance. A reconnect for the same instance
retains its ID. Two simultaneously running instances must not accidentally
share a persisted ID. IDs accept letters, digits, underscores and hyphens,
with a maximum of 100 characters; a UUID works.

Register before making scoped requests:

```http
POST /api/clients/attach
Content-Type: application/json

{"clientId":"<uuid>","kind":"tui","protocolVersion":1}
```

The response supplies `clientId`, `protocolVersion`, `hostInstanceId`,
`transports: ["http", "sse"]`, `reconnect: "snapshot"` and initial app state.
Optional `resumeClientId` copies an earlier presentation into a different
identity; it does not transfer command retry identity.

Host/Origin checks still apply. Verify TLS for remote hosts using their trusted
certificate; do not disable verification. A local development host may use
loopback HTTP. A server-side workspace path refers to the host filesystem;
the terminal's current directory is not automatically a valid remote workspace.

## Essential endpoints

| Operation | Request | Important behavior |
| --- | --- | --- |
| List conversations | `GET /api/sessions?offset=0&limit=100` | Summaries in `items`, then `nextOffset`; optional `workspace` host path. |
| Read one conversation | `GET /api/sessions/{id}` | Current session projection, including loaded history; does not change another client's selection. |
| Follow one conversation | `GET /api/sessions/{id}/events` | SSE `snapshot` events; see reconciliation below. |
| Submit a session command | `POST /api/sessions/{id}/commands` | Explicit target and required stable command ID. |
| Create a conversation | `POST /api/actions`, action `session.create` | Arguments may include `title`, `bundle` and host `workspace`. |
| Discover commands | `GET /api/actions` | Current argument schemas. |
| Save private presentation | `POST /api/actions`, action `view.update` | Optional; a TUI can keep drafts and navigation locally. |

List summaries intentionally have empty `messages`, `workers` and `approvals`.
They are not empty histories. Open the selected session before rendering its
conversation. Follow all `nextOffset` pages needed by the TUI's own navigation.
An imported conversation can have earlier history outside its loaded window.
When `sharedHistoryOffset` is positive, use `session.history` with that value as
`before` and a `limit` of up to 100; reconcile subsequent snapshots and inspect
`historyLoading`/`historyError`. `sharedHistoryTotal` describes the saved history.
Earlier-page requests with `before` work while the runtime is ready or busy.
They prepend saved messages without changing current work, live responses, or
drafts. They also tolerate saved appends when the already loaded history still
matches; incompatible rewrites report `historyError`. A page does not refresh
the latest messages or acquire execution ownership.
Do not interpret the latest loaded message window as the entire event log.

Examples of session command arguments:

| Action | Arguments excluding the target in the URL |
| --- | --- |
| `conversation.send` | `{"text":"Continue the work"}` |
| `conversation.stop` | `{}` |
| `approval.respond` | `{"id":"<approval-id>","decision":"allow"}` or `"deny"` |
| `worker.spawn` | `{"instruction":"Review these files"}`; optional `bundle`. |
| `worker.steer` | `{"id":"<worker-id>","text":"Focus on the tests"}` |
| `worker.stop` | `{"id":"<worker-id>"}` |
| `session.rename` | `{"title":"Release review"}` |
| `session.takeover` | `{}`; only following explicit user intent. |
| `session.history` | Optional `before` and `limit`; discover schema before use. |
| `session.warm` | `{}`; optional background preparation on selection, when advertised by the host. |
| `runtime.control` | `operation` and optional `args`; discover schema before use. |

On hosts advertising `session.warm`, the TUI may display the selected
conversation immediately and request preparation in parallel. Do not wait for
preparation before rendering history or accepting a draft. A normal send is
valid whether preparation is pending, warm, cold, or unavailable. Command
acceptance only schedules preparation; it is not a readiness guarantee.
`session.preparation.status` is advisory host state. Preparation never performs
takeover. Older hosts without this action remain usable through ordinary send.
See [optional preparation](live-sessions.md#optional-preparation-on-navigation)
for retention and lifecycle details. This optional addition is newer than the
released live-client baseline described above; discover it rather than assuming
that every supported host provides it.

The command body is:

```json
{
  "id": "<globally-unique-command-uuid>",
  "action": "conversation.send",
  "args": {"text": "Continue the work"}
}
```

Capture the session ID when the user submits. Navigation while admission is
pending must not retarget that command. Let the host serialize accepted work;
simultaneous clients do not receive separate execution ownership.

## Acceptance, retries and uncertain outcomes

Persist the host/connection identity, client ID, command ID, action, explicit
session target and exact arguments before sending. Reuse that same tuple after
an uncertain network response. A new user action gets a new command ID.

Command IDs are stored in a host-wide receipt table; use globally unique IDs.
The baseline fingerprint includes the client identity and contents. Hosts
advertising `conversation.send.preserveDraft` in the action schema omit the
presentation client identity for sends, so an exact send can be checked after
reattaching with a new client ID. Other commands still include client identity.
Reusing an ID with different arguments is a conflict, not a safe retry.

A successful response says the command was accepted; it does not mean the model
or a tool finished. A duplicate receipt returns `duplicate: true`. Read current
session state to show progress. Never send another command merely because a
socket closed or a model response has not appeared.

On hosts advertising `preserveDraft`, show a local user bubble immediately and
clear the composer, preserving the captured input in an outbox. Merge the
server bubble by `inputId`. Receipts expose `delivery: sending | accepted |
unknown`; message projections expose `delivery.status`. A provisional bubble
or a `sending` receipt does not confirm delivery to the runtime. Keep uncertain
input visible and let **Check delivery** repeat its exact ID and payload. A
confirmed rejection permits **Retry** with a new ID. A duplicate rejection can
arrive as HTTP 200 with `accepted: false` and its original `status`/`code`;
the adapter raises `SessionClientError` for that result too. Never silently
repeat unknown work after reconnect or restart.

When using host draft storage, save the empty draft separately and include
`preserveDraft: true` in the captured send arguments. This prevents a delayed
acknowledgement from clearing newer typing, including an identical next message.
Show activity on the pending bubble and keep input and navigation responsive.
For option reloads, retain the current same-source content and mark that region
busy until refresh completes; do not blank the entire interface.

Discover `message.edit` and its `mode` schema before offering editing. Explicit
`mode: current` edits within the conversation; `mode: fork` creates a new one.
Omitted mode keeps the legacy fork behavior. Locally rejected input can be
edited in the outbox and retried without a history operation. Current-conversation
edits require idle execution and ownership; later active context is replaced
while original event evidence remains. Tool effects are not undone or replayed.
See [message delivery and editing](../MESSAGE-DELIVERY-EDIT.md) for details.

Receipt deduplication does not prove exactly-once external tool effects after a
crash. An interrupted or unknown outcome requires recovery/reconciliation; the
TUI must not silently repeat it. The v1 API does not provide a separate public
receipt-lookup endpoint.

`expectedRevision`, if used, checks the app's global revision, including UI
changes. It is not a per-conversation version and should not be a mandatory
precondition on every send during streaming.

## Snapshot rendering and reconnect

Each SSE frame is `event: snapshot`, with JSON shaped as:

```json
{
  "protocolVersion": 1,
  "hostInstanceId": "<host-start-identity>",
  "revision": 42,
  "session": {
    "id": "<session-id>",
    "status": "working",
    "messages": [],
    "streaming": "Current partial response"
  }
}
```

The session object contains additional fields; the example is not a complete
schema. Render its current `messages` by stable message ID, its optional
`streaming` text as the current partial response, and its optional
`execution`, `workers`, `approvals`, `ownership` and `error` state.

Replace/reconcile the previous projection. Do not append each received snapshot
as new messages or concatenate successive `streaming` values. Coalescing can
skip intermediate snapshots. Preserve local scroll position and draft text.

SSE IDs are `<hostInstanceId>:<revision>`. Reconnect sends a complete current
snapshot. v1 does not honor `Last-Event-ID` as a durable incremental replay
cursor. Accept the new revision range when the host instance changes.

Heartbeats arrive about every 20 seconds. The Python adapter uses a 45-second
socket-read timeout and reconnects transient transport failures with backoff
from 0.25 to 5 seconds. It does not retry application/authentication errors.
On `client_not_attached`, explicitly attach the retained identity before
resubscribing. A missing/deleted client record is not permission to resend work.

An already-open stream ends with `deleted: true` when its session is deleted.
A fresh request to an unknown session returns 404. Stop subscribing and return
to navigation; do not recreate the conversation automatically.

## Drafts and navigation

TUI-local draft storage is sufficient for an initial connected client. If using
Unified's presentation storage, send `view.update` with an explicit
`sessionId` and `patch.draft`. Before a conversation exists, an explicit null
target saves this client's pre-conversation draft without creating a chat.
That draft survives reload/restart and transfers when the first chat is created.

Keep the captured draft target through any debounce. A late empty-composer save
must remain null-targeted after the user chooses another chat. A late save for
chat A must never overwrite chat B.

The public Python adapter currently covers attach, session list/create,
snapshot, session command and streaming snapshots. It has no public generic
action or draft-save helper. For optional presentation, attachments or app-wide
commands, add an explicit wrapper around the documented `/api/actions` route;
do not make a long-term dependency on its private `_json` method.

## Errors, approvals and ownership

Preserve the HTTP status and JSON `error`/`code` in a structured TUI error.
Display concise text and retain the draft; do not repeatedly turn an ownership
conflict into new global notifications.

| Condition | TUI behavior |
| --- | --- |
| 401/403 | Explain authentication, host or origin failure; do not loop requests. |
| 400 | Show validation failure and keep the user's input. |
| 404 | Refresh navigation; the target may no longer exist. |
| 409 | Refresh and resolve the actual conflict; do not silently retry a changed command. |
| `session_busy` | Show ownership details and optional explicit takeover. |
| Transport timeout/lost response | Keep an uncertain outbox item and reconcile with the original identity. |

An approval can be answered once. Another client's answer makes a stale answer
conflict; refresh the prompt rather than issuing a replacement approval.
Never approve automatically just because a reconnect displayed a prompt.

Unified owns the Foundation lock; all its attached clients share that ownership.
Attaching and switching views do not take over a CLI session. A takeover request
must be explicit and is not successful until ownership is acquired. Show the
session's `ownership` progress. If a CLI takes ownership, keep the TUI connected
as an observer where possible and explain why work cannot be submitted.

Closing the TUI, changing selection or cancelling a reader task only detaches.
Use `conversation.stop` for an intentional session-wide stop, which other
clients will also observe. Never map ordinary application shutdown to stop.

## Minimal adapter integration

```python
from amplifier_web.session_client import SessionClient

async def follow(client, session_id, publish_to_ui):
    async for state in client.snapshots(session_id):
        await publish_to_ui(state)  # reconcile; do not append the whole snapshot
        if state.get("deleted"):
            return

async def send(client, session_id, text, persisted_command_id):
    return await client.command(
        session_id,
        "conversation.send",
        {"text": text},
        command_id=persisted_command_id,
    )

# Inside the TUI's existing asynchronous lifecycle:
# async with SessionClient(base_url, token, unique_client_id) as client:
#     run follow(...) as a background reader alongside input handling.
#     Closing this context releases HTTP connections, not session ownership.
```

The TUI owns task cancellation and UI dispatch. Keep the reader alive while
awaiting commands, stop an obsolete reader when selecting a different session,
and discard late snapshots for an obsolete selection.

## What the host already verifies

The release validation covers independent browser presentation, simultaneous
session updates, slow send admission, retries, reconnect, and the empty-composer
draft regression. A separate actual-worker probe uses two `SessionClient`
instances over authenticated HTTP/SSE with a deterministic local provider. It
verifies idle resume, duplicate delivery, detach while work continues, reconnect
history, and explicit cancellation of an in-flight provider request.

Coordinated real-provider browser testing separately verifies text streaming and
corrections during child work and compaction. Its exact host/runtime revisions
and limits are recorded in the validation document. These checks do not claim
the as-yet-unmodified TUI already works; its integration must pass the checklist
below.

## Required TUI acceptance checklist

Run with isolated app/settings/ownership/workspace directories and synthetic
task data. Use a real released host; clearly distinguish a local fixture provider
from a live provider. Keep credentials and raw transcripts out of commits.

- [ ] TUI and web attach with different identities to the same existing session.
- [ ] A send from either appears exactly once in both; partial and final replies
      are visible without changing the other client's draft or selection.
- [ ] A session idles/parks, then accepts another turn while UI controls refresh.
- [ ] A slow acknowledgement does not freeze input or retarget a queued send.
- [ ] Composer clears immediately, a pending bubble indicates delivery, and a
      late acknowledgement cannot erase a newer identical draft.
- [ ] A lost rejection is recovered as a rejection; explicit retry uses a new
      ID, while an uncertain result keeps the original ID and exact payload.
- [ ] Repeating the exact uncertain command uses its existing ID and produces
      one accepted input. Reusing the ID for changed contents fails.
- [ ] Detaching/closing TUI leaves a bounded running task alive; reconnect catches
      up without resubmission. Explicit stop does stop work and is seen by web.
- [ ] Restart/reconnect accepts a new host instance and lower revision range,
      preserves saved history, and does not replay interrupted tool effects.
- [ ] Competing approval responses resolve once and refresh the losing client.
- [ ] CLI-owned sessions show a lock conflict; explicit takeover follows the
      released ownership contract in both directions.
- [ ] Large Unicode snapshots are read intact; pagination does not imply lost
      history; a deleted session and invalid credentials fail clearly.
- [ ] Empty-composer drafts save after an actual debounce delay and survive
      reopening without creating a session.

Record host version/commit, runtime pin, TUI commit, platform, provider type,
checks, failures and limitations. Passing server/adapter tests does not replace
testing the actual TUI's event loop and rendering.

## Outside this handoff

Event-log-only runtime recovery, replay of compaction from an authoritative
`events.jsonl`, direct event-file editing, execution migration between machines,
native device capabilities and multi-device voice arbitration remain separate
work. They are not prerequisites for the first connected TUI text client.
The current contract preserves existing persistence and ownership; it does not
claim those future recovery mechanisms are implemented.


### Canonical names across standalone and connected clients

Use `session.rename` for an attached client. The host persists the name in native
`metadata.json`, and list/detail projections reflect later standalone CLI renames.
Do not create a TUI naming sidecar. Standalone hosts should adopt Foundation's
`SessionMetadataStore` and checkpoint metadata merging; common scoped settings
I/O is available in `amplifier_foundation.settings`. An empty composer does not
create a native execution session solely to store its title. Once native state
exists, the provisional or generated title is visible to CLI readers as well.
