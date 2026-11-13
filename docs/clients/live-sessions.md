# Unified live session contract, version 1

This documents the existing HTTP/SSE transport. The [AHP/ACP design](../architecture/README.md)
defines its proposed successor, including client-local draft storage. Those changes
are not implemented by this documentation update; the behavior below remains the
legacy integration reference during migration.

TUI implementers: start with the [integration handoff](tui-handoff.md) for the
supported release, client lifecycle, retry rules and acceptance checklist.

Unified owns execution while holding the Foundation session lock. Web, terminal,
and native clients attach to that host. Attaching or disconnecting a client never
acquires, releases, stops, or transfers execution ownership. Existing CLI takeover
remains an explicit, separate operation.

## Shared work and independent presentation

Accepted input, replies, streaming text, tool/worker progress, approvals, session
configuration, and ownership status belong to the session. All attached clients
observe those changes. An approval can be answered once; a competing answer gets
a conflict and must refresh.

Each client owns its selection, per-conversation draft and pending attachments,
panels, layout, canvas selection/tabs/controls, modular-shell composition, and
device effects. A clipboard or permission request goes to its initiating client.
Accepted attachments and published artifacts become shared session content.

The browser creates a fresh client ID for each page load. It restores a copy of
the prior page's presentation using `resumeClientId`; duplicate tabs therefore
start with the same view but diverge independently. Saved client drafts are
retained in the app database, including when no stream is connected. Client IDs
are presentation identities, not credentials or security boundaries.

The bundled browser loads older conversation pages only for an explicit page
action or current scrolling input toward older history. Restored reading
positions, focus, layout changes and browser anchoring do not request additional
pages. Leaving the selected chat or returning to its latest window aborts pending
history reads and discards their late results. That releases reader interest;
it never cancels an accepted history-expansion or execution command.

## Authentication and attachment

Use the host's existing authenticated browser cookie or control bearer token.
All endpoints retain existing Host, Origin, and authentication checks. Native
clients should verify TLS with the host's trusted certificate.

1. `POST /api/clients/attach` with
   `{"clientId":"unique-client-id","kind":"tui","protocolVersion":1}`.
   Optional `resumeClientId` copies a prior client's view. Supported kinds:
   `web`, `tui`, `native`, `api`.
2. Send `X-Amplifier-Client: unique-client-id` on subsequent API requests.
   Native browser EventSource and iframe URLs can use `?clientId=...`.
   Conflicting header/query identities are rejected.
3. Attachment returns `protocolVersion`, `hostInstanceId`, supported transports,
   reconnect behavior (`snapshot`), and initial browser state.

Reusing an existing client ID intentionally reconnects to that same presentation.
Use a different ID for each concurrently independent client.

## Optional observations and browser links

See [client observations v1](observations.md) for optional capability discovery,
lightweight attachment without browser state, revision-bound collection pages,
activity counts and acknowledgement semantics. [Browser chat links](chat-links.md)
are independent of observation support.

## Session operations

| Endpoint | Contract |
| --- | --- |
| `GET /api/sessions?offset=0&limit=100` | Bounded summaries, optional exact `workspace` path and `kind=root` filter, and `nextOffset`. Filters apply before pagination; omitting `kind` includes all session kinds. |
| `GET /api/sessions/{id}` | Full current session snapshot without changing selection. |
| `POST /api/sessions/{id}/commands` | Explicit session target and stable command ID. |
| `GET /api/sessions/{id}/events` | SSE `snapshot` events containing the complete current session. |
| `POST /api/actions` | Existing app operations, including creation and client-local presentation. |
| `GET /api/actions` | Discover current action names and schemas. |

Offset pages are stateless current catalog reads, not a retained snapshot or a
full-library enumeration. Workspace resource pickers request a root-chat page
only while open, retain the explicit selection when paging, and refresh on demand.
Concurrent catalog changes can alter later pages; accepted commands revalidate
the selected chat's workspace and authority independently of the discovery row.

Agent metadata discovery and explicitly requested navigation are described in
[agent results](../AGENT-RESULTS.md#chat-discovery-and-client-navigation).
`coordination.list` supports title/ID queries without transcript retrieval and
returns a query-bound `directoryRevision` for stable continuation. An agent
`session.select` requires the intended originating or explicit connected client
displaying its calling chat; a saved global selection is not a visible target.
Its receipt and passive `session.selection` readback bind that exact command to
client/selection revision without exposing drafts. Host selection and observed
browser rendering remain separate evidence, including when private Settings
editors retain their prior view pending the user's choice.

Example command body:
```json
{
  "id": "persist-this-id-before-sending",
  "action": "conversation.send",
  "args": {"text": "Continue the work"}
}
```

Session commands include send/stop, worker spawn/stop/steer, approval responses,
runtime controls, background preparation, history loading, rename, and takeover. A session argument that
conflicts with the URL is rejected. The SPA uses the same service dispatch through
`/api/actions`; session operations capture their target before awaiting history or
runtime admission.

Ordinary `conversation.send` is conversation-addressed. Do not supply a displayed
generation: the native owner chooses live provider steering when ready, otherwise
the next request boundary within the current turn, or a new turn when idle.
Explicit `expectedGenerationId` remains strict and refuses an ended/changed run;
the former `allowNextTurn` option is removed. See
[message delivery](../MESSAGE-DELIVERY-EDIT.md#ordinary-messages-follow-the-conversation)
for receipt phases and the no-replay boundary.
Optional `expectedInterruptionRevision` captures the conversation's existing Stop
revision at submission. A stale value refuses fresh admission with `input_stopped`;
it is not a generation/route choice. Exact duplicate receipts still reconcile after
Stop. The bundled browser preserves this value across uncertain delivery.

Keep the same command ID and arguments after an uncertain transport failure.
Persisted receipts deduplicate retries by client, action, arguments, and origin.
A changed command with the same ID is rejected. Command acceptance is distinct
from a completed model/tool operation. Interrupted or uncertain work is never
automatically rerun by attachment, reload, or SSE reconnection.

The bundled browser can dismiss a settled failed/uncertain delivery warning into
reversible **Delivery details**. This stores only the exact chat/input identities
in that browser tab's session storage; it neither deletes the original message,
attachments or uncertain outbox nor claims delivery. **Show warning** restores
the emphasis. Active sending, queued input, the current running input and pending
checks cannot be dismissed. Another input or chat does not inherit the choice.
An in-flight check stays expanded across transcript remounts in the same page and
cannot be issued twice for that identity. Its in-memory ownership ends on settlement;
reload does not restore fake pending work. Exact acceptance removes that input's
saved dismissal key without scanning other history.
Later unrelated conversation activity is not delivery evidence. **Check delivery**
never resends: only an exact positive saved receipt or runtime confirmation can
settle that input, including after its message leaves the bounded view. The
saved receipt must have a valid delivery shape and no contradictory rejection,
error or action label; legacy send receipts without an action label remain valid.
Generic command admission is not delivery evidence. The
client retires only the matching accepted outbox entry; late transport errors
cannot recreate it. Missing evidence remains uncertain/not saved, not proven
non-delivery. Dismissal leaves drafts, selection and shared history unchanged.

The bundled client automatically performs that same passive reconciliation when
an unresolved input becomes visible and after a connection is restored. Manual
and automatic checks share one in-flight owner for the exact host/account source,
conversation and input. They do not poll on a timer, send the message, start a
worker or relax Stop. A source/authentication change invalidates an outstanding
result. Each check returns its bound conversation/input identities and observed
workspace/effective native identity, including compact replies without state.
Both that source and any returned target-session frame must match the original
scope before acknowledgement or state adoption. A generic, malformed or
conflicting acknowledgement cannot retire the outbox.
Passive checks do not occupy the mutation queue, so they cannot delay Stop.
New outbox entries retain their original nonsecret host/account and native-source
namespace. Projection and acknowledgement require that exact namespace. Legacy
entries without it remain stored and inert, without being presented as a current
chat delivery problem or silently assigned to the connected account or sent again.

An input that left the resident message window may still have positive canonical
runtime history, even when its command receipt is absent or unreadable. The
existing passive runtime reader can establish that exact positive fact without
loading a worker for execution. Missing evidence remains unresolved, not proof
that admission never occurred. If there is no original client submission or
saved retryable message, the UI does not recommend an unavailable retry.
Reconciliation never automatically resubmits even a request dropped before
admission: that case is indistinguishable from an unrecorded effect without
stronger canonical evidence. Any deliberate uncertain resend remains explicit.

The optional `expectedRevision` checks the app's global state revision, including
presentation changes; it is not a per-session revision. Do not use it as a
mandatory send precondition during active streaming.

### Per-input work observations

User messages with an exact `inputId` (or a validated native `amplifier_input`
marker exposed as `nativeInputId`) may carry additive `inputStatus`:

```json
{"version":1,"inputId":"exact-input","phase":"working","basis":"execution_observed","startedAt":1794300000.25,"updatedAt":1794300000.25,"generationId":"exact-generation"}
```

Phases are `queued`, `included`, `working`, `waiting`, `done`, `failed`, and
`unknown`. `basis` is a
content-free evidence label: `generation_started`, `execution_observed`,
`generation_finished`, `waiting_workers`, `generation_failed`,
`generation_detached`, `steering_queued`, `steering_context`, `steering_failed`,
`steering_unknown`, `human_decision`, `execution_interrupted`, or `no_evidence`.
Optional `startedAt`, `updatedAt`, and `endedAt` are observed Unix seconds;
`generationId` is included only when exact. Missing timestamps are not zero.
The timer starts at relevant root execution/generation observation, never at
optimistic send admission. Steering queueing is `queued`; confirmed context
insertion is `included`. Neither starts a work timer or proves execution,
fitted-request membership, or model reading. A later observed execution whose
exact `admissionInputId` matches can promote either to `working`; an ambient
generation for another input cannot. Only `working` supports a live timer;
waiting freezes at `updatedAt` (or its observed root `endedAt`), and queued,
included, and unknown never imply an active timer.
Provider-call `admissionInputId` identifies its execution cause, **not** membership
in the final fitted model request or proof that a model read/obeyed that input.
Display `turnId`, session busy/idle, and tool outcomes cannot establish success.

`done` means that exact root turn/response finished, not that the user's objective
was accomplished. A finished generation with `active_job_ids` is `waiting`
(`waiting_workers`), not done. Exact failure/detachment evidence takes precedence
over completion; late calls/starts cannot reopen a settled generation. An
explicit newer observed generation can start a retry under the same input ID.
After loss/Stop marks an observation `execution_interrupted`, late terminals of
any outcome cannot replace it; only a genuine newer generation start can retry.
Waiting for a human requires exact input/generation question or approval
evidence; ambient pending questions/approvals do not qualify. Current native
approval events without that binding do not produce `human_decision`.

A failed exact input may also carry `inputStatus.failure`: the existing bounded
diagnosis (`category`, `errorType`, `summary`, `guidance`, generation/input binding,
and sanitized exception frames when recorded). It stays beside that input when
a newer generation clears the current `session.failure`. Merely accepting a send
or reporting ready does not erase a failed request; successful startup can clear
its own startup failure. A failed provider/tool/naming call stays on that operation
unless terminal request evidence confirms the request failed.

A checkpointed completed response remains completed after a later runtime fault.
That fault is retained in bounded runtime diagnostics, not relabelled as a stopped
turn or a reason to resend the prior input. Explicit `persistence.failed` remains
a saving-operation failure: response completion does not promise that later job
evidence or state was saved. Pending/unknown operations and actual Stop remain
truthful. An ordinary new human send may start an absent runtime, never replaying
the earlier request as a side effect of viewing or reconnecting.

`session.inspect` is a private on-demand report; its ordered causal timeline and
loaded runtime versions do not borrow unrelated current performance/call history.
Shareable `feedback.diagnostics` uses the existing feedback pipeline with a closed
failure report: fixed categories/types, approved source-file/line frames, hashed
input/generation correlations, loaded versions and a bounded causal tail. It
excludes prompts, titles, paths, arbitrary hook/file names, credentials and SDK
payloads. Optional `failureContext: {sessionId, inputId, generationId}` on
`feedback.diagnostics`, `feedback.report` and `feedback.submit` resolves only that
exact retained failed-input annotation. Missing/mismatched context fails rather
than substituting the latest fault. Accepted feedback freezes these sanitized
facts in its existing receipt; opting out still excludes diagnostic bytes.
Missing original frames/versions remain unavailable rather than inferred.

These small receipts use existing durable annotations and are joined by exact
input identity after message-history/window overlays; they survive reload and
generation-tail eviction without storing another transcript or platform ledger.
Bounded native windows preserve valid input markers without claiming global
marker uniqueness; detected ambiguous/conflicting markers are not joined.
Lost execution, Stop, or host restart changes unresolved observations to unknown,
stopping their active timer without inferring rollback or replay. Legacy or
missing evidence stays unknown (or omits this field when no exact identity is
available). This observation never changes delivery/admission authority:
**Received** is a client fallback only for that input's exact
`delivery.status: "accepted"`, not generic command acceptance or later activity.

### Next-message choices

`conversation.configure` saves `{sessionId, expectedRevision, bundle?, selection?,
resetModel?}` without mounting a conversation. `expectedRevision` is the
conversation's `nextMessageRevision`, not the global app revision. The receipt
returns the new revision and `mounted:false`. A stale edit conflicts; the client
must not silently retry it over another browser's choice.

These future choices remain editable while this host is running or stopping a
turn, including active child work and pending delivery. Saving never changes
that turn's captured model, effort, bundle, ownership, or execution status.
Steering remains part of the current run and keeps that run's settings; it is
not a fresh-turn configuration boundary.
Unresolved configuration mutations, read-only history, and ownership on another
host still block a save and must be explained at the controls.

`pendingBundleSelection` and `pendingModelSelection` are desired configuration.
`bundle` and runtime reports describe applied or last-observed configuration.
`appliedConfigurationRevision` is updated only after matching runtime readback.
The bundled composer includes `expectedConfigurationRevision` on a genuine send.
The send captures its selected revision. Later edits wait for a later input;
an older mount acknowledgment cannot erase them. Stop, execution placement,
connection settings and original bundle authority are checked again after
preparation waits. Reading or choosing settings never resumes a goal.

Bundle previews compose declared settings without mounting either root. An
unavailable source remains an explicit preview failure. Actual native conversion
uses the existing writer-owned bundle transaction at input preparation, preserving
history and retaining invalid choices. Public `bundle.switch` and `bundle.fork`
remain explicit immediate runtime operations; they are not the bundled selector's
save action. Active turns and unresolved configuration operations are not bypassed.

### Explicit optional preparation

After discovering `session.warm` in `GET /api/actions`, a client can issue it
through the session command endpoint with empty arguments. It schedules
background preparation under the host's policy without submitting input or
changing the client's selection. Acceptance means scheduled, not ready.
`session.preparation.status` exposes `preparing`, `ready`, `warm`, `active`,
`cold`, or `unavailable` when known. Preparation requires an explicit request;
`session.select`, history reads and stream attachment never schedule it. Clients
must not send `session.warm` merely because the user selected a conversation.

Reading a session or opening its event stream does not itself warm it. Warmth
is advisory: a worker can retire or another host can acquire ownership before
the next command. Send through the normal command API regardless of warmth.
Preparation never requests takeover, submits input, or replays earlier work.
The host defaults to 32 idle workers for 12 hours, with two simultaneous
background starts. Idle live workers retain the Foundation writer lock until
acknowledged retirement and cleanup. Retirement rechecks pending work after saving;
the next ordinary input starts a fresh worker from native history. Client
disconnect does not stop active work or reset the idle-retention clock.

## Reconnect and lifetime

An SSE ID is `<hostInstanceId>:<revision>`. Every connection starts with a complete
current snapshot, including partial assistant text. Replace the prior projection;
do not append the snapshot as new messages. A host restart changes
`hostInstanceId`, so clients must accept its new revision range.

Version 1 does **not** promise replay of every intermediate event or honor
`Last-Event-ID` as an incremental cursor. Slow subscribers may receive coalesced
snapshots. Heartbeats run every 20 seconds. Session deletion emits a final
`deleted: true` snapshot. Reconnect uses the same attached client identity and
does not resubmit commands or replay device effects.

The browser stream remains `/api/events`, with independent client snapshots and
the shell's `shell` invalidations. Dedicated session streams contain session
snapshots only. An admitted command continues if its HTTP connection disappears.

## Terminal adapter

`amplifier_web.session_client.SessionClient` implements authenticated attachment,
session listing/creation, explicit commands, and reconnecting SSE snapshots using
aiohttp. It never imports an execution runtime or acquires a Foundation lock.

```python
from amplifier_web.session_client import SessionClient

async def send_and_follow(url, token, session_id, command_id):
    # Retain these IDs for retries; use a new client ID for an independent window.
    async with SessionClient(url, token, "my-terminal") as client:
        await client.command(
            session_id, "conversation.send", {"text": "Hello from the terminal"},
            command_id=command_id,
        )
        async for snapshot in client.snapshots(session_id):
            render_session(snapshot["session"])  # replace the displayed projection
```

A TUI integration supplies this adapter behind its runtime interface, translates
snapshots into its own widgets, and keeps navigation/drafts local to that client.
Its disconnect/exit action closes the adapter; stop/cancel invokes an explicit
session command. This change provides the shared contract and tested adapter.
It does not modify a separately hosted amplifier-app-tui repository.

## Storage and scope

Conversation persistence and existing CLI history discovery remain in place.
Client presentation is separately rebuildable app data. This change does not
implement event-log-only runtime recovery, compaction replay, log pruning,
execution migration to another machine, or multi-device voice-call arbitration.
Those require their own contracts; Foundation ownership stays configurable.

### Drafts before a conversation exists

An attached client may save its empty-composer draft with
`view.update {sessionId: null, patch: {draft: "..."}}`. The explicit null target
means its private pre-conversation draft, even if selection changes before the
request arrives. This does not create a conversation or start work. The draft
survives reload and host restart; creating the first conversation carries it
forward. A string targets that conversation; omitting the target uses the
client's current selection. Other session commands still require string IDs
when a target is supplied.


## Stream display identity and shared names

This additive v1 extension supplies `session.streamingId` with `session.streaming`.
The corresponding final assistant message carries the same `streamId`; replace
that partial display item instead of appending a second copy. These are display
identities, not command/input provenance. Never infer an input ID from the most
recent user message. Older compatible hosts may omit them; clients can show a
receiving indicator until the identified final message arrives.

Stop, error, yield and host recovery settle interrupted partial text as explicitly
labelled partial display evidence and clear the active identity. Historical editing
clears it too. A subsequent response receives a new identity. Settling display
text does not claim model completion or replay any tool action.

Automatic naming runs on the execution host through its configured ecosystem hook.
The host controls scheduling while the hook supplies context sampling, prompting,
model routing and parsing. `session.rename` is an explicit manual choice stored
through Foundation's shared metadata API. Custom names, including legacy names
with unspecified source, skip automated renaming; compare-and-set storage also
protects against an already-running naming call completing after a manual rename.
Clients display the resulting shared title instead of running their own naming model.

Unified defaults to the first name after one completed user exchange, then reviews
the title and description at every fifth completed user input. During ongoing
work, it checks every five minutes once that initial threshold has been met.
Unchanged sampled conversation text does not trigger another model call. Existing
`initial_trigger_turn` and `update_interval_turns` hook settings remain authoritative;
`update_interval_seconds` can change the ongoing-work interval. Refresh prompts
keep a suitable title stable rather than renaming it for small changes.

Explicit regeneration also works during active conversation work. It uses a
bounded snapshot of the conversation and selected provider, and its result cannot
overwrite a later manual rename or naming-policy change. Naming calls retain
diagnostic and usage records as background activity, without adding chat work rows
or changing the conversation's working state. A second naming request can start
after the current naming request finishes; a provider/bundle configuration change must
finish before an explicit naming request starts.
