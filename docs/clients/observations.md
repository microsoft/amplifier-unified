# Optional client observations, version 1

These resources extend the existing [HTTP/SSE client contract](live-sessions.md).
They serve authenticated web, TUI, native and API clients; no replacement
transport or AHP/ACP adoption is required. They do not start a polling task,
subscribe a browser, hydrate history, submit input, or acknowledge attention.

## Discover and attach

Attach with protocol version 1 and `includeState:false` to avoid receiving the
browser state. The response adds `hostIdentity` (stable for the host's data
location), `hostInstanceId` (changes on restart), and
`capabilities.observations` with `version:1`, resource URLs, `maxPageSize:200`,
`conditionalRequests:true`, and `incrementalCounts:true`. The existing
`X-Amplifier-State-Transport: delta-v1` attachment also omits browser state.
Existing clients that omit both retain their previous attachment behavior.

The minimum compatible observation protocol is the advertised capability
version 1, independent of the application release number. An older host may
reject `includeState`; retry attachment without that optional field and with the
existing delta-v1 header. If the capability is missing, a version is unsupported,
or a resource returns 404, disable observation features and keep supported
session commands available. Never infer observation support from the product
name. Ignore unknown additive fields and display unknown notice kinds as generic
updates; never infer permission or automatically act on them.

Use an existing owner-authorized authenticated connection and verify TLS. Client
IDs distinguish presentation, not authority. This slice adds no scoped credentials
and must not be advertised as least-privilege device access. Public native-client
enrollment is a separate delivery slice described below.

## Collections and continuation

`GET /api/sessions?view=observation` and `GET /api/attention` return
`{revision, items, nextCursor}`. Each response holds at most `limit` items
(default 100, range 1–200). Supply `cursor=<nextCursor>` with the same filters and
limit to continue until `nextCursor:null`. A page can be empty with a non-null
cursor when a selective query reaches its scan budget; it is not the end.

Order is retained catalog order, then canonical per-session notice order
(questions, manual unread, completion, session error, approvals). It is stable
within a revision. Cursors are signed and bound to the collection, complete
normalized query, page size, host instance and a coherent public-facts snapshot.
Sessions, attention and activity have independent semantic revisions. Drafts,
layout and transcript-only publications preserve those revisions and ETags.
Membership/filter/lifecycle changes update sessions; notice content, fingerprints
and acknowledgements update attention; changes to roles or actual counts update
activity. A new first-page request sees current facts.

A continuation reads its original snapshot even during sustained real changes.
Its revision describes that snapshot, not a guarantee that the host is unchanged.
Each unfinished traversal has a fixed ten-minute lease; reads do not extend it.
Leases are distinct per client, normalized query and generation. Retrying the
first page of that same unfinished traversal reuses its cursor and ETag. A new
first page after completion begins a new traversal; separate clients/queries
can finish independently while sharing the same immutable snapshot.

A terminal response (`nextCursor:null`) releases that traversal immediately.
An obsolete snapshot is evicted once it has no unfinished readers. Retrying the
exact cursor that produced a terminal response replays that response for up to
30 seconds, subject to a 64-response cache per collection; only the terminal
page/count result is retained, not its catalog. Earlier cursors of a completed
traversal, evicted terminal retries, and expired continuations return `409
snapshot_changed`: discard accumulated pages and restart. Retry caches and
leases are reclaimed on the next observation request.

Each collection has its own limits of eight snapshot generations and 64 unfinished
traversals. Admission returns `503 observation_busy` only when those live-reader
limits are reached; existing readers can still finish. A busy session catalog
cannot exhaust attention or activity admission. Completed generations do not
occupy history slots for the remaining ten-minute period. Back off and retry only
the observation on 503; finish existing continuations instead of abandoning them.
Malformed, tampered, cross-query, or old-instance cursors return `400
invalid_cursor`; restart discovery and the collection. Unsupported/repeated
parameters and invalid limits are rejected, not silently ignored.

Cursor collections do not accept `offset`. The existing unfiltered
`/api/sessions?offset=0&limit=100[&workspace=...]` remains the legacy catalog for
older clients, with its existing `nextOffset` behavior; it is not a coherent
snapshot during mutations. New observation clients use the advertised view.

Both collections accept `scope=roots|delegates|all` (default roots). Internal
sessions are always excluded. Root/delegate classification uses the same
`is_top_level` helper as browser navigation, including independent forks. Counts
are per session; delegates are not rolled up into roots or double-counted.

Sessions additionally accept `workspace=<exact-host-path>` (up to 512 UTF-8 bytes),
`pinned=all|true|false`, and `activity=all|execution|preparing|loading|idle|active|active-or-pinned`.
Filters intersect. `active` is execution or preparation; `active-or-pinned`
includes pinned idle chats too. Returned fields are ID, bounded title, status,
role, pinned flag, `agentCreated`, and the three independent activity flags. No transcript or
workspace paths are returned in this view.

## Activity and polling

`GET /api/activity[?scope=roots|delegates|all]` returns an exact ready summary:
`{revision,status:"ready",nextCursor:null,executionCount,preparingCount,loadingCount,
attentionCount,requiresActionCount}`.

- Execution means status working, running or stopping. Starting a runtime alone
  is preparation, not execution.
- Preparation means status starting, configuration busy, or preparation status
  preparing. It can overlap execution during a live configuration operation.
- Loading means historyLoading. It never implies execution or preparation.
- Attention counts unread covered notices, not distinct chats. Requires-action
  counts the subset requiring a decision, answer or recovery.

Lifecycle facts are shared with browser navigation. The UI may use a general
busy presentation for preparation, but the public execution count remains exact.

Large count scans inspect at most 4096 catalog/notice slots per request. Until
finished they return HTTP 202 with `{revision,status:"collecting",nextCursor}`
and **no partial numeric totals**. Continue with `cursor` and the same scope.
Each retained activity snapshot shares its on-demand accumulator across readers.
Drafts and transcript deltas neither discard partial counts nor restart them.
Genuine changes start a new revision for new readers; old cursors finish their
original exact totals. Ready counts are reused for that snapshot. No background
scan runs for hosts without an observer.

All resources return private, revalidated ETags. Send `If-None-Match` and retain
your observation on 304. Poll at most once per second while actively showing work;
back off to 5–30 seconds while idle/hidden and exponentially (with jitter, up to
60 seconds) on transport errors/503. Follow collection/count continuation
sequentially, with at most one request in flight per client. On 401 stop and
re-authenticate; on 409 replace the observation. Reconnection never replays
commands. Ordinary browser use adds no observation polling. A lightweight
invalidation subscription is a future extension, not a requirement of v1.

Page selection inspects at most 1024 candidate slots and materializes at most
one page. Continuation seeks directly into a session/notice array, including
within a chat with thousands of approvals. Arbitrary query keys are not cached.

Coherent continuation has an explicit resource cost: the first observation builds
one shared allowlisted public-facts index in O(sessions + notice slots) time and
O(sessions + emitted notices) memory. This cold construction is **not** included
in the 1024/4096 page/count budgets. Unknown/broad saves reconcile the same
index; scoped saves reconcile only affected rows, while the existing verified
transcript-only publication path does no reconciliation. Unchanged row facts are
reused by identity. Pin/read-receipt changes currently require broad reconciliation.
The current pin lookup/read receipts are shared, not copied per query. A snapshot
retains one tuple of row references; revisions share unchanged row bodies. At
most eight snapshots per collection (24 total), 64 active leases per collection,
and 64 terminal response retries per collection are retained, plus the current
index; never whole
transcripts or private prompt/tool/error bodies. Expired leases are reclaimed on
the next observation request, without a background timer. See the acceptance
report for cold/reconciliation costs measured separately from warm continuation.

## Notices, acknowledgement, and commands

Each notice has a stable `id`, canonical `fingerprint`, `kind`, typed `target`
(with `type:session`, `sessionId`, and questionId/approvalId where applicable),
`requiresAction`, `read`, bounded title/summary, and `timestamp`. Timestamp is a
host-retained event time or null when older state has no reliable timestamp; it
is never invented from observation time. Filter `read=false|true|all` (default
false). Fingerprints and read receipts are exactly those used by web attention.

| Kind | Included condition | Requires action |
| --- | --- | --- |
| completion | Durable response-ready marker | No |
| question | Pending question or uncertain/rejected answer delivery | Required question or delivery recovery |
| approval | Pending approval | Yes |
| session-error | Retained session error | Yes |
| manual-unread | User's explicit unread marker | No |

V1 covers session facts, not host maintenance/provider/feedback notices or
`action:*` error receipts. Those retain existing web attention/discovery behavior;
an action error is not relabeled as an execution/session error. This policy avoids
duplicating a request error as an agent failure. It can be extended by new kinds
and typed targets in a future capability version. Transcript bodies, question
prompts, tool arguments, error details, paths and device-local views never enter
this summary surface.

Explicitly acknowledge only observed items via existing `attention.read`, always
supplying IDs and their exact fingerprints. A changed fingerprint is not marked
read; a disappeared ID can reject the request and requires refresh. Reading a
question or approval does not answer/approve it, and a read notice may still
represent unresolved work. Opening a view does not acknowledge anything.
Host-shared read receipts are distinct from each device's local record of which
notifications it has delivered.

Use the existing `/api/sessions/{id}/commands` for reply, stop and approval.
Capture the target ID before navigation and persist a stable command ID before
sending. Retry uncertain delivery with the same ID and arguments; never create a
new ID automatically. Mismatched URL/argument targets and changed payloads under the same command ID
are rejected. Observation/reconnection never submits or replays commands.

## Compatibility and delivery slices

1. [Browser links](chat-links.md) stand alone and may land independently.
2. This owner-authorized observation slice adds optional resources and discovery.
   `/api/companion` is only a temporary preview adapter over the same queries.
   It advertises generic resource URLs and `sessionsNextCursor`/`itemsNextCursor`;
   use corresponding `sessionsCursor`/`itemsCursor` parameters for continuation.
   Unsupported `offset` is rejected. Its legacy `workingCount` means execution.
   If the shared count pass is incomplete, it returns `503 observation_pending`;
   retry observation only. Private client distribution/setup belongs elsewhere.
3. General device enrollment/permissions remain separate, unimplemented here.
   Reuse `TerminalDevices` one-use enrollment and revocation, rather than a new
   credential store. Its current credentials are revocable but not scoped.
   Before claiming limited native-device access, central middleware must enforce
   observation, session read/reply/control, approval and administration rights.
   Acceptance must cover redemption-once, revocation of HTTP/live connections,
   permission denials, Host/Origin/TLS protections, and absence of bypass through
   client IDs, generic actions, session commands or alternate transports. A public
   setup flow must not require copying a full-control host token. No native
   enrollment rollout, deployment or shared-host adoption is part of this PR.

Session and attention rows also expose `agentCreated`, using the same positive
host-retained creation evidence as Recent chats (`is_agent_created`). It does not
expose creator IDs or prompts and does not change the generic root/delegate scope.
Clients wanting Recent-like presentation can hide these roots unless pinned or
explicitly followed. Missing metadata on an older host is unknown, not proof of
agent creation. Tempo's compatibility adapter preserves this field in both rows.
