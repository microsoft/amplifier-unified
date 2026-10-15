# Host-owned isolated checks

`workspace.tests.submit` admits one bounded check per root conversation. The
host saves the request in the existing command journal, waits for physical
backend capacity, takes a working-folder snapshot when admitted, executes the
command once, stops the exact owned environment, exports source and artifacts,
then removes the environment. Export or removal uncertainty preserves ownership
and evidence. Files outside the working folder and artifact directory are not
included in the export. Do not put unique output elsewhere.

The user and agent use the same actions. The workspace's existing test section
shows waiting and completed checks; there is no new chat-header control. User
submission does not automatically start a model turn. Agent submission defaults
to a guarded completion notification and can use `notify:false` for inspection
only. A busy chat receives that notification at its ordinary idle boundary.
The result does not satisfy a shared-task dependency or prove product acceptance.

## Lifecycle and ownership

- Keep `requestId` stable. Re-reading or repeating that exact request returns
  its receipt; changed arguments with the same ID are rejected.
- `workspace.tests.read` returns bounded pages of compact outcomes. Full check
  output stays in the existing environment operation receipt. It is not copied
  into every queue row or workspace catalog.
- Cancellation before admission starts no command. An already admitted command
  may run until its specified timeout, at most five minutes. The host then stops
  its exact environment and preserves evidence. Cancelling a model alone does
  not retroactively undo a command.
- Stop, moved placement, assignment cancellation, revoked capability and changed
  backend configuration invalidate waiting admission. Cleanup of an admitted
  owned check is part of the original request and can still stop/export/remove
  that environment after Stop; it cannot start a new check or affect a peer.
- Restart recovers queued intents and observes admitted owners. It uses exact
  operation receipts to recover known results, never reruns a test to discover
  its result, and resumes only authorized cleanup. Unknown cleanup remains
  inspectable; no age-based deletion or global prune occurs.
- A host admits FIFO among its eligible roots. One physical capacity observation
  is shared by a batch; unavailable backends back off to at most one observation
  per minute. Local cleanup wakes the queue immediately. Physical backend
  admission performs its own atomic recheck; a snapshot is not a reservation.

## Capacity policy

The Docker configuration may set `maxRunning` (default 15), `maxInstances`
(default 256 retained plus active), and `minFreeBytes` (default 10 GiB). Zero
explicitly disables that individual limit. Stopped environments consume storage
but not running slots. Unknown/paused/starting physical instances remain active.
Docker admission is serialized for cooperating processes of one host user,
keyed by the actual daemon identity rather than socket spelling. It does not
enforce a limit on old tools or unrelated callers that bypass this admission.

Docker storage is observed at the explicit local daemon's data directory. An
unreadable daemon/storage observation waits with a distinct blocker; it is not
reported as a full execution pool. The queue never raises operator limits or
deletes someone else's environment to gain room.

The legacy Incus backend needs the separately qualified adapter and launcher.
An older CLI invocation does not become a queued request automatically. Updated
agent guidance must prefer `workspace.tests.submit` for bounded checks. Existing
manual environment controls remain available for longer interactive/browser
sessions, whose caller owns lifecycle and cleanup.

## Qualification boundaries

Unit tests exercise dispatch, durable journals, cancellation, revocation,
capacity refusal, exact-request retry, uncertain commands and restart cleanup
against controlled backend effects. Physical Docker/Incus and native worker
continuation evidence must be recorded separately. A passing command proves
that command's result, not release adoption, real-provider behavior, browser
rendering or integrated acceptance.
