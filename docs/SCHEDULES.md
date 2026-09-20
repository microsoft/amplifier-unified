# Scheduled continuation and monitors

Scheduled work appears beside the saved task under Goals & modes. It records a
concrete prompt, timezone recurrence, missed-run behavior, notification intent,
current task identity/revision, and the user stop revision. UI and app_control
share schedule.preview/create/list/read/update/pause/resume/cancel, plus report
and reconcile. An active saved task is required; schedules never create a second
task lifecycle or independently complete the original objective.

Preview shows future local/UTC occurrences and the exact saved instructions. Its
hash binds timing, prompt, policies, task revision and stop revision. UI activation
is an explicit user action. Agent activation must cite the actual user message or
voice transcript requesting scheduling; agent-authored user-role bubbles and
question-answer receipts are rejected. The authorization snapshot and source
hash survive history paging/restart. New task corrections, an edited source, or
user stop intent require visible review with fresh user provenance. No pending
question counts as schedule authorization or tool permission. Required linked
questions gate dependent scheduled work; optional questions do not globally pause
independent work.

The reusable [policy/store](../amplifier_scheduling/README.md) is authoritative for
due identity and leases. The app runs one lightweight owner loop, checks existing
task state, global update/stop state and questions, writes a durable submitting
receipt, then uses internal schedule.submit. That worker control checks the
exact task revision/status under the ordinary command lock just before queueing
input. It releases the lock after input admission, never after model execution or
permission waits. The public runtime.control action cannot invoke this internal
path. Normal tool approval hooks still run on every execution.

The existing conversation receives a stable input ID and labelled schedule
message, preserving selection, drafts and task identity. Session interruption
revision is captured in the receipt and checked again under runtime admission;
a stop that wins this boundary produces an explicit skipped run. Ambiguous
handoff, runtime exit, or scheduler restart leaves unknown, never automatic replay.
Pause/cancel prevent future runs; currently admitted work may finish. Reconcile
records a user's evidence that a run completed or was abandoned without replay;
future scheduling still requires separate review/resume. The app must be running
for due checks; on restart the chosen missed-run policy applies.

A monitor can report actual compared values (up to 64 KB), an explanation, and
changed/unchanged. Stored-value comparison takes precedence over the reported
claim. Its source is labelled `compared_reported_values`; without values the
source remains `agent_report`/`user_report`, which is not proof. Unchanged reports
stay quiet under the changes policy. Initial compared values, confirmed changes,
or failures notify according to the saved intent; normal app notification/privacy
settings still govern delivery. Notification decisions persist before delivery
attempts, so a crash may lose a notification rather than send it twice. Ordinary
assistant blocks from scheduled inputs do not emit separate generic notifications.
Mixed generations containing a manual input retain normal completion notifications.
A monitor temporarily suspends the goal continuation loop through delegated
reports until session idle; it then restores only the current task revision. A
new correction wins restoration, and a user pause keeps the goal disabled.

Generation completion settles a scheduled run, not its task. Active delegated
jobs must report terminal outcomes before the run settles. Runtime death or
interrupted worker evidence remains unknown. History/failures are bounded and
available through schedule.read; the optional P01 operations registry projects
these authoritative receipts read-only without a second journal.

Validation uses fixed clocks, DST gaps/folds, missed policies, ownership/restart,
CAS/idempotency, scope/provenance rejection, quiet comparisons, task corrections,
required questions, stop winning admission, and the actual loop-live input queue.
The Chromium fixture exercises the real app/controller/store with deterministic
provider work. Live provider behavior, physical voice and deployment are not
claimed by that fixture. Source composition and release remain integration work.
