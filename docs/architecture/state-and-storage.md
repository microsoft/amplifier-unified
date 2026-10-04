# State, storage and working sets

Design supporting [CS](../../contracts/client-state.v1.md),
[HP](../../contracts/host-protocol.v1.md) and [WS](../../contracts/working-set.v1.md).
This describes the target and its selected storage boundaries. The
[implementation status](implementation-status.md) records which exact paths and
packages have passed qualification; it does not imply production migration.

## Ownership and durability

| Data | Authority and durability | When resident / synchronized |
| --- | --- | --- |
| Private drafts, unfinished settings edits, selection, scroll, expansion, layout | Client-local storage, keyed by host/account/conversation/view as appropriate | Only that client; no backend mutation for ordinary edits |
| Intentionally shared draft | Optional AHP chat draft | Explicit feature; standard `chat/draftChanged`, convenient/debounced writes |
| Agent-visible editor context | Explicit targeted capability, bounded snapshot or lease | Requested nonsecret fields only; no permanent mirror of every keystroke |
| Submitted but uncertain input | Client durable intent journal and host admission receipt | Retain exact identity and recovery status; independent of optimistic reducer state |
| Accepted messages, native tool history, metadata | Native agent store; existing Amplifier transcript/metadata files | Selected history window or active execution; no second resume authority |
| `events.jsonl` and historical logs | Retained native files on disk | Targeted inspection/export or background indexing when required; no ordinary catalog parse |
| Workspace/session summaries and parent links | Rebuildable disk index over native facts | Query pages and incremental invalidations; not a global in-memory model |
| AHP projected chat/session state | Derived host resource projection over native facts plus host-owned fields | Subscribed/active resources and bounded cache; explicit upstream snapshot semantics |
| Scheduling, operation receipts and pending decisions | Their independent capability owners' scoped durable records | Active requests and indexed lookups; survive viewer loss and process restart |
| Admission receipts and protocol/native ID mapping | Scoped durable host records | Indexed exact lookups; independent of subscribers and native resume storage |
| Active model context, modules, hooks, provider connections | Agent execution process | Executing/protected sessions plus deliberately bounded idle reuse |
| Catalogs of providers, modules and capabilities | Disk/package metadata and independently invalidated caches | Requested settings/agent preparation; no mount/test/login during a listing |
| Attachments, tool bodies, artifacts | Stable durable resource references and native ownership | Content on demand; previews/cache bounded by bytes, no inline global replication |
| Audio frames, camera frames, cursor animation, transient progress | Transient stream unless explicitly captured as an artifact | No durable copy by default; accepted transcript/artifact remains durable |
| Credentials | Authorized host/device secret store | Secret-aware path only; configuration status may be shared, raw values may not |

Use asynchronous IndexedDB for browser drafts, outbox and meaningful caches;
reserve synchronous localStorage for tiny preferences. TUI uses its own local disk
store. Distinguish persistent device preferences from independent view drafts so
two tabs do not accidentally share one editor key. Define tab duplication and
restore behavior explicitly. Partition by authenticated host/account, not a raw
session ID that could collide or expose another account's cache.

Clearing a **derived cache** is recoverable from the host. Clearing a **private
unsent draft** is not: the host deliberately never received it. Local restart
survival is the default; cross-device draft continuity is an explicit shared-draft
feature. Quota/storage failures leave a visible unsaved state, not a false promise.
Logout removes sensitive caches under the client policy; private drafts are not
silently uploaded as a workaround.

## Follow AHP's cache and reconciliation model

Use [upstream reconciliation](https://microsoft.github.io/agent-host-protocol/guide/reconciliation.html):
confirmed resource state, pending optimistic actions, and the resulting derived
optimistic state. Keep local presentation state outside that reducer. Route view
subscriptions and render selectors by stable resource identity so an unrelated
chat delta neither rebuilds the app tree nor forces a whole-state serialization.

1. On initial attach, negotiate capabilities, fetch a bounded session page, and
   subscribe to needed root/session/chat resources. Acquire snapshots before actions.
2. During normal use, apply upstream action envelopes, own echoes, rejection and
   rebase rules. Private input/scroll/layout causes no host action. Persist draft
   recovery asynchronously, without putting disk work in each rendering update.
3. On reconnect with a valid base, use `lastSeenServerSeq` and subscriptions. Apply
   replay or fresh snapshots exactly as specified and clear protocol pending actions.
   Re-fetch the session catalog because its ephemeral notifications are not replayed.
4. If local state is missing, corrupt, schema-incompatible or belongs to a different
   authority, subscribe afresh. Never apply deltas to an empty guessed baseline.
   Do not persist AHP catalog cursors across connections.
5. Reconcile the separate durable intent journal with host command/turn identities.
   Do not automatically resend cleared optimistic actions. When a downstream ACP
   agent's acceptance cannot be established after failure, display an unknown
   outcome and retain evidence; do not replay the prompt to discover what happened.

Upstream [draft synchronization](https://microsoft.github.io/agent-host-protocol/specification/chat-channel.html#drafts)
is optional. A new clean editor should initialize from an existing shared draft
when present. A remote shared draft must not silently overwrite dirty private
input. Surface the choice/conflict in the explicit shared-drafting feature.
Private unsaved forms are likewise local. File editors may expose a dirty-buffer
guard to prevent an agent overwriting unsaved work; that safety-relevant fact is
different from publishing every UI field to the backend.

Agent parity for local UI actions uses a targeted client capability: request a
bounded view or ask that client to select, open or edit through its own action
handler. Return the observed result or an explicit offline/unavailable outcome.
This preserves user/agent parity without a permanently synchronized backend copy
of all panels and drafts. Shared work still uses the host's authoritative commands.

## Catalog that fits the actual installation

Target the user's roughly 4,000 historical projects and 25,000 native sessions,
with many fewer existing directories and relevant root conversations. Index small
metadata in the SQLite catalog (an implemented derived index, not a new canonical store):

```text
workspace: host, stable identity, canonical path, existence status, checked_at
conversation: native owner/kind, native ID, host ID mapping, workspace ID,
              parent/root ID, title, modified time, status, storage locator
discovery: writer/source revision, durable change cursor, last reconciliation
query: authorized existing workspaces -> root conversations -> order -> page
```

Filter authorization, directory eligibility and root status in the indexed query,
**before** pagination and detail reads. Index parent/root and `(workspace, modified,
stable ID)` for deterministic page order. Do not stat 4,000 paths on every query.
Use tracked directory freshness, watches where useful and throttled revalidation.
Confirmed missing paths leave ordinary results; inaccessible/offline paths become
unknown rather than confirmed missing. Preserve last-known facts for recovery and
show an explicit unavailable source when useful. Archive inspection is separate.

Map one root conversation/execution context to an AHP session with its main chat
and relevant related chats. Workspace grouping is a catalog concern. Do not put
all of a workspace's historical sessions in one giant AHP session chat catalog.
Keep a separate mapping for each chat's ACP/native execution session: an AHP
session grouping is not evidence that one ACP session can run all its chats.
Project-scoped filtering beyond the standard request shape is an optional host
catalog capability, not invented AHP core fields. Standard `listSessions` still
works with a useful bounded default list and upstream pagination semantics.

Children stay indexed and durable but outside ordinary top-level lists. Opening
delegation details requests child summaries/history progressively. Large families
may need a negotiated paged child-discovery extension if the current standard
cannot express the desired bound; the ordinary parent view must remain useful.
Do not silently truncate required standard state to pretend the extension exists.

## Incremental discovery and legacy writers

Make the native adapter publish discovery hints after durable metadata/session
changes, including identity, workspace, parent link, storage locator and revision.
The host indexes hints idempotently. They are not copies of full transcripts and
do not become the authority for resuming execution.

Reconstruction pauses and joins admitted discovery hints before seeding retained
identities. A bounded durable outbox coalesces observations while repair runs;
overflow makes discovery explicitly incomplete. A timed-out writer may still be
writing, so reconstruction requires the original response or observed process exit.
Replacing its process is not settlement evidence. Exact-ID access and unrelated
execution remain available. Independent scanners and legacy inbox writers require
the same exclusion policy; a host-local queue cannot prove those writers quiet.

Modify the legacy CLI through shared Foundation persistence hooks where possible:
emit the same small hint or append to a per-writer durable change journal. A killed
writer can miss the post-commit hint, so background reconciliation remains necessary.
An initial import walks lightweight metadata incrementally; historical roots not
yet classified remain an explicit backlog. An old format lacking parent/workspace
facts is enriched once, off the interactive path, with progress and resumable cursors.

Support duplicates, out-of-order hints, watcher overflow, journal rotation and
partial writes. Bounded scans reconcile known roots; schedule periodic discovery
of newly created roots. Never require a full `events.jsonl` parse after each app
action or silently ignore writers that predate the hint mechanism. The CLI bridge
is transitional; the connected TUI becomes the maintained live client.

## Separate runtime and display lifecycles

Persisted does not mean indexed in RAM; indexed does not mean subscribed; subscribed
does not mean an agent is warm. A historical view does not request runtime preparation.
Explicit execution admission may prepare a cold runtime, with visible startup latency,
or reuse a compatible prepared runtime. Residency is host-managed within explicit
budgets; it is not a user-maintained pool of prepared historical conversations.

The [2026-10-04 retirement decision](ready-conversations-retirement.md) removes
legacy Ready conversations controls and preparation on selection from the desired
product. It preserves native history, admitted work and unresolved decisions.
Existing host source provides bounded reuse and idle retirement; this design does
not claim a newly implemented adaptive policy, completed UI removal or live adoption.

Use separate budgets for concurrent execution, idle agents, display bytes, native
history windows, replay bytes/age, resource bodies and outbound subscriber queues.
Deduplicate compatible projections, but do not hold an idle worker simply because
a history viewer is open. Pin running work, pending approvals and other genuinely
live effects until their safe boundary; overload queues/refuses new work explicitly.
Retire idle workers with native ownership and durable state intact.

Shared conversation visibility is a retained host presentation choice. Hiding a
row does not revoke execution or direct access, and rebuilding discovery does not
block authorized exact-ID history, receipts, artifacts or deferred work. Reversible
visibility changes use selected-row identity/revision checks and a durable marker
journal; the catalog projection can be reconstructed from those markers. Keep
listing unavailable until projection is consistent, without holding unrelated
capability owners. Client-private selection, expansion and drafts remain local.
The host, recovery owner and root composition now implement this boundary. Their
focused source, component-package, final assembled-package and installed-static
browser checks pass. Live adoption remains separate. The earlier globally held
candidate is superseded.
See [the qualification boundary](evidence/presentation-boundary-20261003.json).

AHP's `view.turns` is advisory. If omitted, upstream requires all retained turns;
`fetchTurns` prepends older turns to reduced chat state. Older-turn operations must
load their referenced history. Our clients should request bounded windows, and
we must test cumulative backfill and other clients that omit the hint. Do not
delete canonical history or secretly change “retained” to evade that requirement.
If large histories need a stronger bound, settle an explicit compatible retention/
archive design or upstream improvement before claiming strict memory bounds there.

## Make update cost proportional to affected work

Replace the global application state as read/write unit with repository methods:
one command receipt, metadata record, catalog invalidation, resource action or
configuration dependency set. Use direct indexed reads for agent queries instead
of building `state_context()` and selecting a JSON pointer afterward. An agent
needing the catalog uses the same filtered pages as a UI.

Scoped snapshots remain valid for initial/recovery reads; incremental actions are
the normal stream. Avoid a hidden global deep-copy/hash/diff merely to compute a
tiny action. Protocol sequencing is a small ordering operation, not a lock held
while files are read, projections built or agent calls awaited. Bound buffering
with standard delivery/reconnect behavior; never drop stateful actions arbitrarily.

Configuration invalidation follows changed source keys and actual dependents.
Persisted, effective and mounted revisions remain separate. One provider setting
may affect many dependent sessions legitimately; it must not mount them all or
rebuild unrelated histories just to report the saved value.
