# State persistence ownership

Routine mutations declare their owners. `_publish_changes(sessions={...},
globals={...})` commits only those session references, per-session runtime records,
and named application fields, together with dirty private client records and
command receipts. Empty session scope means no conversation writes. Coalesced
progress declares the same owners; publication joins pending scopes, including
when a commit fails and must be retried.

`_save_changes` is the transaction-only boundary for controllers that already own
their receipts and publication. It does not acknowledge or flush unrelated queued
progress. Pinning has a preference transaction that also preserves pending progress
and sends navigation frames without replacing conversation details.

`_publish_full(reason=...)` and `_save_full(reason=...)` are deliberate complete
reconciliation boundaries: startup/shutdown, catalog membership changes, imports,
confirmed deletion, reset, resource restoration, and task transfer. New field edits
must not use them. The offline storage-layout upgrade and confirmed-deletion
recovery also reconcile the complete saved catalog before accepting requests.
The architecture test rejects unscoped calls outside the shared
persistence implementation.

## Ownership

| Mutation | Records saved |
| --- | --- |
| Chat delivery, model/tool lifecycle, steering, warmup, configuration | Affected session and its runtime control |
| Mounted model catalog | Owning session's runtime control |
| Provider settings/catalog, update progress, diagnostics | Corresponding application field |
| Preferences, pins, drafts and browser layout | Preference/client records; native pin keeps a thin session reference |
| Canvas content, versions, tool views | Canvas/artifact metadata and private client presentation; bodies remain external resources |
| Questions and schedules | Their durable store plus affected session projections |
| Worktree changes | Sessions that own worktree/handoff records |
| Collaboration | Receipt and exact sender/recipient owners, without replay |
| Outputs and feedback | Their durable receipts and corresponding overview fields |
| Import, fork, deletion, reset and transfer | Explicit full reconciliation where membership or multiple store authorities change |

The command scope table covers admission/bookkeeping. An asynchronous command's
controller declares its own result scope; queuing a provider or update operation
must not save every conversation.

## Loading

Metadata operations validate identity without hydrating cold conversation bodies.
Rendering and runtime admission request the history they actually need. Canonical
history remains in the context-intelligence event log; app state references and
lazy presentation resources are not a replacement event log. Search/browse retains
its existing bounded projections.

Scoped commits are tested against complete reconciliation, including transaction
failure/retry, native pin survival, multiple clients, and pending runtime progress.
A reduced write scope is incorrect if a restart loses data, a private view crosses
clients, or an uncertain tool effect is replayed.

This does not make startup a paged database reader: startup still loads the thin
catalog and controller records, and some shared fields (such as the workspace
catalog) remain single records. Resource retention also retains its periodic
reachability sweep. These are separate costs from per-command persistence; this
change does not claim to remove every library-size-dependent operation.
