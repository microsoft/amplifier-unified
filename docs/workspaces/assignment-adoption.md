# Adding an existing chat to an assignment

A manager can explicitly take responsibility for a settled, independent chat
in the same workspace. Sharing a folder never grants that responsibility.
This extends assignment membership without changing the conversation's native
identity, history, working folder, previous usage, or commissioning provenance.

`assignment.adoption` reviews one target and returns an opaque revision bound
to the assignment, target input, Stop, generation, task and execution placement.
`assignment.adopt` checks that revision while saving both the membership and
the command receipt. The UI offers **Add an existing chat** inside Shared tasks
and assignment; its picker reads twenty workspace chats per page. Inspection
does not start a worker.

An agent must be the assignment owner acting from its current actual human
input, identify that message, and honor the user's requested scope. Peer input,
scheduled input, and an old human message do not supply this authority. A UI
selection is direct user authorization. Existing assignment ownership, active
work, pending input, and settings changes prevent enrollment. The existing
member and open-task ceilings apply.

Membership has role `adopted` and its own adoption receipt. It does not invent a
parent or creation receipt. The manager may subsequently use the existing
guarded peer-resume and idle-checkout placement paths. Adoption itself sends no
input and does not clear Stop, a saved task pause, a failure or a budget.
Placement verifies the exact adoption receipt, with the same asynchronous
instruction/Stop/membership checks as commissioned placement.

Future calls use the assignment budget. Earlier calls keep their existing
attribution; detachment retains charged usage and revokes manager control.
Detached historical membership is not recycled into a new authority epoch:
use a different assignment to establish a new relationship.

## Finishing and reusing a member chat

The assignment owner can use `assignment.retire` for a finished helper. This
releases its current member slot without archiving the chat, deleting files,
forgetting its membership or subtracting any attributed usage. Running workers,
unsettled inputs or checks, unfinished saved tasks and shared task claims prevent
retirement. A native ownership check protects the transition. User Stop and
unknown operation outcomes are not cleared.

`maxMembers` counts current bound members and reservations. Retired, detached
and never-created records remain available as history without occupying those
slots. The brief and assignment controls distinguish current and retained
membership counts.

For a later follow-up, the existing `assignment.adoption` / `assignment.adopt`
path can add a retired chat to the same assignment again after a fresh review.
It rechecks capacity and native ownership, records a new membership boundary,
and retains the previous retirement receipt. Inputs and automatic returns from
before readmission remain historical; they cannot restart the chat. Prior
spending continues to count, and adoption itself sends no input. The current
agent adoption requirement for an admitted human instruction still applies;
this does not add a new approval screen or silently broaden that requirement.

Lost replies retry the same operation ID. A stale review needs a fresh
inspection; a failed durable save restores the target and assignment together.
No running work is stopped to make adoption possible. A known parked idle worker
may retire through its ordinary acknowledged release path; every binding is
checked again and the native ownership lease is held through the membership save.
Foreign native writers remain protected even when the UI last reported idle. Cross-workspace takeover,
automatic enrollment and cross-engine history conversion remain unsupported.
