# Saved task and context continuity

The Goals & modes page offers a saved task, retaining objective, constraints,
revisioned corrections, status, question IDs, operation IDs, artifact references,
and explicit completion evidence. UI and `app_control` use the same `task.*`
actions: `get`, `create`, `update`, `pause`, `resume`, `block`, `complete`.
Agent actions are bound to the calling conversation, including the legacy
`runtime.control` forwarding alias. These controls do not send input, change a
draft/selection, grant permissions, resolve questions, or create a scheduler.

Mutations require exact `expectedRevision` and an idempotent dispatch command ID.
The worker serializes edits and stores state and command receipts in the existing
app-owned `sessions/<id>/control-state.json`. Completed task records remain in
history when a new objective is created. An existing unfinished task is edited
rather than replaced. Task actions return authoritative state, unlike a queued
management acknowledgement. Linked operation IDs are observations; unknown
outcomes remain unknown until their actual evidence is reconciled.

The existing goal controller performs continuation. The task revision applies at
the next `provider:request` boundary during running work. Pause/block/completion
clear the current goal immediately; the optional loop-live continuation guard
also rejects a stale continuation captured by an in-flight evaluator. A current
turn may finish. Explicit later user inputs still receive a response. Resume is
deliberate and does not send a new prompt. Durable task creation requires the
loop's `live.continuation_guard_supported` capability. Older loops fail clearly.
Legacy goal setters cannot replace an unfinished saved task. Completion requires
the exact current objective/revision and nonempty evidence; a model turn ending
or the goal judge stopping never automatically completes the saved task.

For `context-managed` with `engine: boundary`, Unified opts into optional durable
checkpoints (an explicit `durable_checkpoints: false` remains respected). The host
adapter writes `context-checkpoint.json` beside control state and uses the
module's checkpoint API. See that module's README for its reusable public
contract. Restore occurs lazily before the first fitted request after provider
settings and the complete native transcript have been restored. Only an exact
covered-prefix hash, compatible model/provider instance, configuration/prompt,
format and record digest can restore a summary. Appended unsummarized messages
remain usable. Rejection/staleness is visible through `task.get`; originals are
never replaced or replayed. The summary is labelled reference history, not fresh
authorization. Older context modules report unsupported.

Before request fitting, the host commits the complete canonical transcript via
the existing native SessionStore and emits hash/index/session/source-revision
references for large tool messages. No second output archive is introduced.
Managed-process results may already contain operation-journal references; this
adapter does not invent them or claim capture of arbitrary past tool bytes.
Provider/tool modules that truncate before returning an output cannot be
recovered by this adapter. Cross-host export and retention policy remain owned
by the existing portability/storage integration.

Validation includes real boundary-module compactions plus host serialization and
restart, corruption/prefix/model rejection, task CAS and idempotency across
restart, a real base-loop evaluator/pause race, and a browser fixture exercising
the actual task controller from UI and agent paths. Browser provider execution is
deterministic; live model quality and physical voice are not claimed.
