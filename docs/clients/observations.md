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
normalized query, page size, host instance and projection generation. Any saved
projection invalidation, including saves without a global revision change,
invalidates continuation conservatively. Return `409` with
`code:snapshot_changed`; discard the accumulated pages and restart. No retained
old snapshots, unnoticed gaps or duplicate continuation records are promised.
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

Sessions additionally accept `workspace=<exact-host-path>`,
`pinned=all|true|false`, and `activity=all|execution|preparing|loading|idle|active|active-or-pinned`.
Filters intersect. `active` is execution or preparation; `active-or-pinned`
includes pinned idle chats too. Returned fields are ID, bounded title, status,
role, pinned flag and the three independent activity flags. No transcript or
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
A single host-wide accumulator shares work across authenticated readers. It
retains ten counters and two positions, not a second catalog. A changed revision
restarts the scan; an old continuation receives 409. Ready counts are reused
until invalidated. No background scan runs for hosts without an observer.

All resources return private, revalidated ETags. Send `If-None-Match` and retain
your observation on 304. Poll at most once per second while actively showing work;
back off to 5–30 seconds while idle/hidden and exponentially (with jitter, up to
60 seconds) on transport errors/503. Follow collection/count continuation
sequentially, with at most one request in flight per client. On 401 stop and
re-authenticate; on 409 replace the observation. Reconnection never replays
commands. Ordinary browser use adds no observation polling. A lightweight
invalidation subscription is a future extension, not a requirement of v1.

Collection requests inspect at most 1024 candidate slots and materialize only
one page. Continuation seeks directly to a session/notice slot, including within
a chat with thousands of approvals. Pages are not cached by arbitrary queries.
The pinned-ID set is a single current-generation shared lookup, O(number of
pins); it is not retained per client/query or across generations. Existing
canonical host catalog/read receipts remain owned by their existing stores.

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
new ID automatically. Mismatched URL/argument targets and changed-ID payloads
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
