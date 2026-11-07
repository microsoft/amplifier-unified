# Autonomous progress — proposed continuation contract

**Status: design for review, 2026-11-07. Not implemented or release-qualified.**

This applies the accepted [standing-authorization vision](../../architecture/VISION.md#10-standing-authorization-enables-unattended-work)
and [workspace execution direction](execution.v1.md) to routine waiting and recovery.
It does not require a new agent engine, Core contract, storage migration or scheduler.

## Decision

**An authorized objective stays authorized while the system waits for a routine
prerequisite. The owner of that prerequisite is responsible for getting the same
task moving again.** Users and managers should make decisions about objectives,
scope and results, not shuttle messages between internal lifecycle steps.

Task intent, permission and execution readiness are separate facts. A running
process can yield without pausing its task. An unavailable resource can delay
execution without withdrawing permission. A completed setting change should not
need another model turn merely to reactivate the task that requested it.

The observable path is:

```text
working → saved yield → waiting for the exact prerequisite → continuing
                         └→ recovery by its existing owner → continuing / actionable blocker
```

Actual user Stop, deliberate pause, revoked authority and unresolved external
effects retain their meaning. They must not be conflated with routine waiting.

## What is wrong today

Source baseline: Unified `efa6c0762a03cc8884e316fc4652e811a0449ed5` (0.60.0).
A retained live recovery confirmed that configuration reached `ready/readback/mounted`
while its task remained dependency-blocked. Recovery required a separate task
resume and fresh continuation; the earlier unsent message stayed unsent.

| Current mechanism | Friction | Proposed removal |
| --- | --- | --- |
| `Management.apply_queued` waits for root/child inactivity; the active goal can keep continuing. | The task works against the idle boundary its requested change needs. | Request a saved native yield as part of accepting the change. |
| Configuration completion drains eligible messages but does not itself own continuation of the requesting task. | Blocking the task to reach idle leaves it stranded afterward. | Save continuation intent before yielding; settle and wake it through the existing delivery owner. |
| `task.resume` changes status but sends no input. Dependency-blocked peer work needs an admitted message and a deliberate resume. | Routine host prerequisites become multi-chat management chores. | Machine waits keep the task active. A real external blocker can still require a decision. |
| Current Full authorization covers ordinary repair, but individual owners retain separate admission and recovery requirements. | Successful recovery in one owner is mistaken for complete readiness, or another owner cannot obtain the exact disposition. | Publish one authoritative, bounded disposition for the original operation and consume it directly. |
| Time-limited controller observations and standing operator permission can expire together in practice. | Already authorized work can need another person to renew an execution lease. | Separate permission from the renewable observation/ownership lease; qualify this at the device owner. |

These are distinct defects and design gaps. This proposal does not attribute every
previous interruption to configuration or promise that Full bypasses an external
account, operating-system permission or genuine resource conflict.

## Small runtime contract

### 1. Accept the wait with the operation

When an active task requests an authorized change to its own runtime that needs
an idle boundary, the configuration owner saves the change **and its continuation intent** before
asking the runtime to yield. The caller receives an ordinary accepted receipt
that says the task will continue after application or receive an actionable
failure. It needs no extra `task.block`, `task.resume`, self-message or wait tool.
Changes that can apply immediately do not acquire an unnecessary yield/wait cycle.

Attach this intent to the existing configuration operation; do not create a
parallel task journal. The minimal binding is the operation identity, owning task
and continuation epoch, originating runtime/boundary identity, Stop/authority
boundary and exact outcome reference. Reuse existing fields and receipts where
they already express those facts. Task progress-only edits must not invalidate
the wait; changed objectives, ownership or permission must be re-evaluated.

This first scope is a change requested by the active task for itself. Changing
settings on an idle, completed or deliberately paused chat must not start it.
Cross-chat changes do not grant the requester control of the target's task.
Later coverage for external changes to active work needs its own acceptance.

### 2. Yield the execution, preserve the objective

Use the existing native handoff boundary and goal-yield behavior. Stop further
automatic goal turns at the supported boundary and let admitted tool work and
children settle. Preserve the goal, used budget, completed effects and native
input identities. A persistent idle child must be settled by its native owner;
root-idle alone is not sufficient proof.

Configuration application then uses its existing preparation/save/readback owner.
It must not claim readiness from saved settings alone. A supported boundary that
cannot settle reports the exact outstanding owner/effect; waiting is not evidence
that a worker can be killed. No arbitrary sleep or manager polling makes it safe.

### 3. Resume from the result, once

On a qualified mounted result, enqueue one continuation through the existing
collaboration/goal admission owner. Keep the same task, objective and used budget.
There is one logical continuation identity derived from the operation and task;
duplicate events or restart cannot create another initial turn.

New user input takes precedence. If already-admitted fresh input or an eligible
existing continuation will carry the task forward, attach the disposition there
instead of adding a redundant wakeup. Re-evaluate against the resulting runtime,
not the obsolete runtime that yielded. Do not replay historical unsent messages,
failed tool calls or already accepted inputs.

Authority and intent changes suppress the old continuation with a specific reason.
Temporary capacity pressure leaves it queued and automatically reconsidered by
the existing capacity owner; it does not turn into a task block or new approval.
Completion of one wait does not clear another owner's unresolved prerequisite.

### 4. Recovery belongs to the operation

| Observed outcome | Automatic behavior within existing authority |
| --- | --- |
| Rejection proved before any effect | Release that operation's fence and return its actual failure to the same task for ordinary correction. Do not silently rerun the identical failing command. |
| Settings saved; mounted readback unfinished | Finish readback through the existing recovery owner. Do not repeat the settings write. |
| Outcome unknown | Perform exact passive readback/reconciliation by operation and backend identity. Retry delivery/observation when that is safe; never infer that an external effect did not happen. |
| Outcome cannot be determined, or a conflicting owner/decision is real | Leave a concrete blocker with responsible owner, next supported action and retained evidence reference. Escalate only the decision or access actually missing. |

Routine recovery must not require a model to start under the very uncertain
configuration it is being asked to repair. Deterministic inspection and proven
settlement belong to the host operation owner. Where that owner cannot establish
safety, expose the precise limitation; do not invent a generic repair agent with
broader authority or clear the fence just to create activity.

Restart re-reads saved pending operations and continuation receipts. Repeated
identical observations without new evidence do not generate model turns or retry
storms. Unknown dispatch stays unknown until authoritative readback resolves it.

### 5. Permission is not a timer

Standing operator permission lasts until revoked or its explicit scope ends.
A deliberately time-boxed user grant still ends at its deadline and is not renewed
implicitly. A host-generated freshness timeout is a different kind of boundary.
A short-lived lease can establish exclusive device ownership or a fresh
screen/target observation. Its expiration requires a current check, not automatically
another human authorization when standing policy already permits renewal.

For computer use, propose renewal by the same controller while the saved operator
policy permits unattended exclusive use and current target, ownership and presence
checks support it. Actual takeover, revocation or changed scope stops input.
Permission alone does not prove that a device is clear. Ambiguous presence must be
resolved through the device owner's supported policy; it is not silently ignored.
This needs a scoped shared computer-use contract change and tests. Do not implement
it as a longer global timeout, a Unified-only bypass or a guessed fresh clearance.

## Existing owners, not another coordination framework

| Owner | Change / responsibility |
| --- | --- |
| `management.py`, `configuration_recovery.py` | Save the own-task wait, observe application and resolve its exact fence/outcome. |
| `handoff_boundary.py`, `task_continuity.py` | Yield native execution while preserving the task. Generalize the necessary existing boundary behavior instead of introducing a second checkpoint system. |
| `task_binding.py`, `collaboration.py` | Preserve semantic task binding; admit one outcome-backed continuation and prioritize newer input. |
| `update_drain.py` | Existing `task.waitForUpdate` provides a reference for durable enrollment, exact boundary and guarded wakeup. Share only genuinely common mechanics; keep update verification with the updater. |
| Shared computer-use owner | Separate and renew execution leases under explicit standing policy. Unified consumes that result. |
| Context/skill and task UI owners | Explain the actual wait/result without turning internal state into another procedure. |

No additional database, broker, supervisor chat, permission hierarchy or mandatory
scratch file is proposed. Waiting watches only enrolled operations and reacts to
their transitions; it must not scan every session or reload full event histories.
Reuse existing startup recovery and bounded scheduling for missed notifications.
This is not a prerequisite for the separate storage/protocol redesign.

## Remove the workarounds as the capability ships

- Remove guidance to block/pause a task merely to let its own configuration apply,
  and remove routine resume/acknowledgement messages for that path.
- Use the existing authority check once at the owning effect boundary, with
  required delayed-effect rechecks. Do not add prompt-level permission steps.
- Make workers own ordinary correction and focused validation to their assigned
  result. Managers intervene for priorities, acceptance or genuine external
  decisions, not each failed check or settled host wait.
- Use typed wait/outcome references, not prose reasons or scratch files, to decide
  continuation. Existing `task.block` remains for actual unresolved dependencies;
  free-text legacy blocks are not silently reclassified.
- Show **Waiting for settings — will continue automatically**, **Recovering**, or
  the actionable blocker. A historical “Not sent” message remains visibly unsent
  but must not be presented as the current task's state.

Deliver implementation and guidance removal together. Do not advertise automatic
continuation in shipped prompts before the installed path is qualified. Keep
specialized failure recipes on demand, rather than adding this contract to every
model request.

## Delivery and acceptance

1. **Own-session settings first.** Qualify accepted change → native yield → mounted
   readback → one resumed turn, with no manager/human nudge. Reuse the existing
   supported native loop; no Core or consumer upgrade is assumed. If it cannot
   express the boundary, stop and present that concrete dependency before expanding
   scope. Exercise the actual installed host/worker and built UI in an owned DTU.
2. **Close recovery gaps.** Reconcile known partial/no-effect outcomes and consume
   their dispositions directly. Audit existing wait paths against the same contract,
   changing demonstrated failures rather than rewriting all coordination at once.
3. **Renew device leases at their owner.** Prove unattended authorized continuation
   plus actual takeover/revocation behavior before adopting the shared change.
4. **Observe real work.** Measure owner intervention, time spent waiting, duplicate
   wakeups and completed deliverables. Correct remaining reproducible blockers.
   An idle chat or a successful launch is not proof of productive autonomy.

Required focused scenarios for the first slice:

- A goal that would otherwise continue yields for its own configuration, resumes
  the same objective once and completes it without `task.block` or extra messages.
- Active children settle; persistent idle children do not retain a hidden wait.
- Successful application plus a lost acknowledgement or host restart produces no
  duplicate write, tool effect, input, continuation or budget reset.
- A proven no-effect failure releases only its fence and permits ordinary repair;
  a saved-not-mounted failure finishes readback without another write.
- Unknown dispatch remains retained; available exact observations drive recovery
  without requiring human permission merely to read them.
- Stop, deliberate pause, revoked permission and changed objective prevent the old
  wakeup. Progress-only task updates do not. Later explicit resume remains possible.
- New input is neither lost nor duplicated; old unsent drafts stay unsent. A
  settings change to a previously idle chat never starts work.
- Two completed prerequisites and duplicate notifications produce one eligible
  next turn. Capacity recovery wakes queued work without another manager turn.

Acceptance targets: **zero routine human/manager nudges**, **zero model polling
turns while waiting**, and **at most one logical continuation per saved result**,
with every eligible result consumed by resumed or already-continuing work.
On a warm owned fixture with capacity available, target under one second from
settled prerequisite to continuation enqueue; separately measure worker preparation,
model latency and total completion time. Report actual cold/warm results, not an
unconditional production latency promise. Add these to affected smoke coverage;
do not add another broad per-release campaign.

## Existing held tasks

Do not infer permission or completion from an old blocked reason. Reconcile each
already held operation through its existing owner. A legacy task needs an exact
supported binding or an explicit current continuation; preserve its task identity,
budget, user pauses and uncertain effects. Once recovered, future own-task changes
use the new path. Do not bulk resume paused chats or rewrite their histories.

The intended result is fewer controls for agents to orchestrate, not fewer facts
the system verifies: accept authorized work, wait safely, repair routinely and
continue without asking someone to connect the pieces.
