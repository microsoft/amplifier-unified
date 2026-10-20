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
or cancel. No Spark-1 adoption, main merge or release is part of this work.

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

Resume claims persist before runtime unlock. A lost claim/response or an
interrupted host operation becomes uncertain and is not replayed on startup.
Explicit Resume can reopen admission while leaving uncertain, blocked or
unsupported roots untouched. Current Stop, task, history, placement, ownership,
configuration and budget checks apply again to every resumed effect. Disabled
schedules remain disabled; the canonical scheduler records occurrences missed
during the hold as skipped, including schedules configured to run the latest
missed occurrence.

The maintained owner checks are in `tests/test_maintenance.py`; adjacent service,
schedule, collaboration input and force-restart checks cover admission and the
unchanged update confirmation boundary. These checks alone do not qualify native
checkpoint contents, provider behavior, browser controls or installed packaging.
