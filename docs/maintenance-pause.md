# Host maintenance pause and resume

Status: implementation contract approved by the user on 2026-10-20; qualification
is recorded separately. This document does not claim that unsupported runtimes or
remote effects can already be paused.

## Promises

- Pause all commits a durable host maintenance epoch before asynchronous work.
  New sends, starts, delegates, automatic continuations and schedules are held.
  Inspection, durable result recording, Stop and maintenance controls remain
  available. All clients use the same authority.
- Supported execution pauses at a native safe boundary: already admitted effects
  settle and their results are checkpointed before another effect starts. A paused
  receipt identifies native session, continuation and maintenance epoch. Waiting
  for a complete turn is not described as step suspension.
- Native parents and children retain identity, hierarchy, result attribution,
  task progress, limits and current policy. No generic “continue” message, old
  input replay, private coroutine serialization or second scheduler is used.
- Unsupported, blocked, uncertain and still-running owners remain visible. A
  timeout never silently upgrades graceful pause to termination.
- A successful update/restart leaves user maintenance paused. Explicit Resume
  restores only that epoch's unchanged, proven continuation once. Preexisting
  stops/blocks, later Stop/edit/revocation, budget denial and ownership changes win.
  Disabled/copied schedules remain disabled; missed or unknown runs are not
  replayed. Private drafts remain client-owned.
- Emergency Stop now is separately confirmed, needs no staged update, and stops
  positively identified owned execution without waiting for an application
  update. It cannot retract remote accepted requests or undo effects. Keep host
  inspection available where possible.
- Existing Force restart keeps exact candidate/host confirmation and installation
  qualification. The updater consumes maintenance readiness; it does not own a
  separate pause/resume state machine.

## Implementation sequence

1. Introduce and test the minimal public native suspension capability in owned
   loop dependencies: current effect -> durable boundary -> release -> restart ->
   explicit once-only continuation, first with one root and one delegate.
2. Add the persisted host maintenance owner and epoch/admission checks. Preserve
   per-owner receipts and separate maintenance holds from user task/assignment
   Stop. Register actual owner capabilities and honest unsupported outcomes.
3. Wire canonical controls into the ordinary shell and Settings > Updates,
   including progress, explicit resume and emergency confirmation.
4. Qualify deterministic side-effect counters, duplicate/lost replies, restart,
   pending children/results/approvals, old clients/queued admissions, schedules,
   current permission/task/Stop/budget guards, failed checkpoints and hung tools.
5. Add maintained browser/normal-UX scenarios; independently qualify installed
   packages and retain the isolated synthetic preview for review.

Changes to the public loop capability are reviewed alongside Unified. Supported
safe-boundary suspension must be distinguished from adapters that can only drain
or cancel. Live adoption, main merge and release are separate decisions.

## Host authority and native handoff

`amplifier_web.maintenance.Maintenance` owns the durable host epoch and bounded
root receipts in the application database. Its `hostMaintenance` shell projection
is separate from the existing backup/repair `maintenance` results. The ordinary
projection contains counts and readiness only; `maintenance.status` pages exact
root receipts. Pause and emergency Stop commit admission closed before requesting
runtime work. Existing command receipts remain passive, including after restart.
Failed storage keeps in-process admission closed; an incomplete write is never
claimed as a durable pause.

The runtime exposes a synchronous owned-root inventory and asynchronous pause,
resume and Stop requests. It installs the owner's awaited receipt observer,
once-only resume authorizer, non-consuming current-policy resume guard,
synchronous final admission guard, and canonical session loader. The admission
guard must not reenter the service lock. The loader permits paused-only remount;
it does not grant permission to execute. The runtime must require an accepted,
persisted observer response before reporting a safe boundary. An existing-worker
inspection may proceed while maintenance is held, but a new worker start still
checks the manager's final admission boundary.

The inventory is bounded to 512 roots, 256 children per root and 100 input IDs per
root. A proof may include up to five original generation/input descriptors (the
root and four background jobs), four exact job origins, and accepted pending input
IDs that do not yet belong to a generation. Their input union must match the root
receipt. A root may become terminal while an already-owned background job
suspends only when explicit terminal evidence preserves its original origin.
Finite children need no invented loop generation: native session, parent, tool
call and checkpoint identities bind them to the original completion owner.
Changing or missing input/job identities cannot be repaired by replay.

Resume first remounts every selected root while held and verifies every exact
checkpoint. One durable release decision then reopens ordinary admission before
any root can execute an app action. Each root still consumes its own claim once
and rechecks current authority. Failure before that decision keeps the hold.
Loss after that committed decision leaves unconfirmed roots uncertain without
rolling back already released work or replaying a claim; a new explicit Pause
captures the current inventory. Host restart during an unfinished handoff holds
unconfirmed work for inspection and never reissues the operation.
Explicit Resume can reopen admission only after every owned root has a confirmed
settled boundary. Waiting, uncertain or unsupported roots must first settle or
be positively stopped by a separate user action. A settled continuation whose
authority later changed remains excluded from Resume. Current Stop, task, history, placement, ownership,
configuration and budget checks apply again to every resumed effect. Disabled
schedules remain disabled; the canonical scheduler records occurrences missed
during the hold as skipped, including schedules configured to run the latest
missed occurrence.

The maintained owner checks are in `tests/test_maintenance.py`; adjacent service,
schedule, collaboration input and force-restart checks cover admission and the
unchanged update confirmation boundary. These checks alone do not qualify native
checkpoint contents, provider behavior, browser controls or installed packaging.

## Support limits and review dependencies

The coordinated implementation depends on public suspension changes in
[loop-streaming #67](https://github.com/microsoft/amplifier-module-loop-streaming/pull/67),
[loop-live #16](https://github.com/microsoft/amplifier-module-loop-live/pull/16), and
[Foundation #447](https://github.com/microsoft/amplifier-foundation/pull/447).
The application draft does not advance shipped dependency references. Review and
normal dependency qualification must complete before release.

A pending human approval is not serialized or answered automatically. It remains
waiting and prevents safe readiness. Denial and explicit Stop remain available;
allowing a new effect while admission is held is refused. A denied result can
settle into the native checkpoint and is retained through restart without asking
again. Unsupported provider-native engines, active voice and external tools do
not gain a fictitious resumable checkpoint.

Unresolved legacy settings remain a conversation-specific unknown-effect fence.
Maintenance does not clear `configurationBusy`, manufacture apply authority, or
replay that payload. Explicit Force restart retains the original record and
requires confirmed owned shutdown before promoting a qualified candidate.
Separating a historical recovery hold from normal update quiescence is a separate
update-owner change; a stale absent-worker observation is not sufficient proof.

Native qualification uses deterministic synthetic providers and counted effects.
It proves the exercised Core/Foundation and loop ownership, not remote-provider
cancellation, live-account parity or untested Windows descendant termination.

## Maintained acceptance ownership

The investigation's eleven requested smoke areas have explicit owners below.
Component, native-process and browser evidence remain distinct; passing one
does not imply that every combination or platform was exercised.

| Area | Maintained checks and explicit limits |
| --- | --- |
| Model, finite delegate and persistent child boundaries | `test_native_maintenance_pause.py`; `maintenance_native_probe.py` restart/completed-job modes. Actual Core/Foundation with deterministic providers; no live-provider claim. |
| Pause outside Updates and same-host Resume | `maintenance-controls-browser.mjs`; host receipt tests preserve private drafts and canonical authority. |
| Restart and independent clients | Native restart probe preserves root/child identity and counted effect; maintained browser checks a second client's reconnect. These are separate composed layers, not a live multi-device update claim. |
| Admission after awaited locks | `test_maintenance.py`, runtime maintenance admission checks, queued settings and test-environment owner regressions. |
| Pending child results and approvals | Completed-job and approval native probe modes. Pending approval waits; denial settles. Approval is never answered automatically or serialized as a reusable token. |
| Later Stop, edit, policy, task, assignment or ownership | Exact binding and once-only claim checks in `test_maintenance.py`; runtime guard runs before resumed effects. |
| Slow/hung accepted effects | Native held-tool probe waits beyond the old shutdown grace with no automatic escalation. External side effects cannot be undone. |
| Independent emergency Stop | Actual owned POSIX process groups and inspectable HTTP host in native emergency mode. Windows descendants remain unqualified. |
| Persistence failure, lost acknowledgement and duplicate command | Host SQLite failure/claim tests, streaming checkpoint-failure tests and browser same-ID lost-reply retry. Unknown consumed claims are not reissued. |
| Schedules and accepted versus unsubmitted queues | Maintained schedule/queued-settings/test-owner regressions; disabled and missed occurrences remain under their original owners. |
| Desktop/mobile focus and confirmation | Maintained browser checks Tab, Shift+Tab, Escape, Cancel, stale target and paged owner details. |
