# Human-authorized schedule repair across chats

An agent in one workspace root can carry a current human repair request to a
schedule-owning root through `coordination.send`. This is a scoped host receipt,
not authorization inferred from peer prose. Direct cross-chat schedule mutations
remain unavailable.

1. Read the owner's schedules and exact revisions.
2. Send `coordination.send` with the owner `sessionId`, repair instructions and
   `scheduleRepair: {sourceMessageId, schedules: [{id, expectedRevision}]}`.
   The source message must be the latest actual human input admitted into the
   sending root's current generation. Use queue or steer, not notify.
3. The owner retrieves the receipt via `coordination.context` or
   `coordination.result`. While that exact input is admitted into its current
   generation, preview the existing schedule under current configuration.
4. Call `schedule.resume` with its ID, expected revision, current preview hash,
   original human `sourceMessageId` and the receipt's `handoffRequestId`.

Only the listed existing schedules (maximum 16) can be resumed. Prompt, timing,
notification policy, destination and other schedule configuration stay unchanged.
A new-task schedule can bind the current owner configuration after review. The
handoff does not permit create, update, reconcile, arbitrary cross-chat writes,
or replay of an uncertain run. Changes to the saved schedule need a separate
explicit owner-local request using the existing update path.

The host binds both roots' workspace and stop revisions, the source human ID and
text digest, latest owner human input, exact schedule revision and configuration.
New human input in either chat before repair, edits, stops, workspace changes or
schedule changes invalidate the handoff. Stored authorization references the
original human and receipt. At due admission the original authorizing chat is
loaded; missing/edited authority, workspace changes and stops prevent execution,
including changes that race the final runtime admission guard. Normal task,
preview, configuration, permissions and uncertain-run protections still apply.

`tests/test_schedule_handoff.py` exercises real host dispatch and durable receipts
with synthetic guarded workers, including existing-task and new-task due launch,
configuration rebinding, forged/stale scope and corrections winning admission.
These tests do not demonstrate live provider inference or a user's repaired job.
