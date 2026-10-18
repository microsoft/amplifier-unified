# Everyday automation overview and schedule recovery

Ratified for the October 17, 2026 user-approved automation work (#277, #679).
This extends the existing chat-owned schedule contract; it adds no independent
global executor or transfer of authority between chats.

The everyday Automation entry reads a bounded, on-demand page of saved schedules
from the scheduling ledger. Each row identifies its owning chat, saved instruction,
next occurrence and current state. Due does not mean admitted or completed. A
missing owner, stopped owner, changed configuration and an uncertain prior run
remain distinct. Browsing neither loads conversation histories nor starts workers.
Refresh is explicit; there is no new polling loop. Create/manage opens the existing
owner-local schedule controls and preserves other clients' selection and drafts.

A person may review an existing **new-task** schedule directly in this overview
when its owner has exited. This is a host-owned human action, not peer-agent repair
authority. Preview uses the owner's saved effective configuration without warming
the old worker, displays the unchanged prompt/timing/destination and current
workspace/bundle/model, and binds exact schedule, execution and stop revisions.
Confirmation resumes future occurrences only. An owner explicitly stopped by the
user requires an additional explicit acknowledgement; a worker error is not Stop.
A subsequent Stop, configuration edit, assignment suspension, missing workspace,
or stale revision refuses confirmation/admission. Unknown prior work must first
be reconciled through the existing evidence controls, never automatically replayed.
Same-task schedules still use owner-local task review; this feature does not
restart their failed task or infer that its work is safe to repeat.

New-task execution uses reviewed saved configuration to create a fresh target and
does not require inference or warming in the owner. The source remains stopped or
failed; reauthorizing future scheduled targets does not resume its old generation.
Existing peer handoff rules in [schedule-repair-handoff.md](schedule-repair-handoff.md)
are unchanged. Account/credential selection is not rewritten by recovery.

Configuration comparison excludes only demonstrated representation differences:
empty/null no-op controls, ordering of disabled-module sets and control copies of
the already-bound model selection. Material configuration, tool permissions,
budgets, modules, source choices and effective model changes remain binding.
Changed-field diagnostics name categories only, without exposing configuration
values or credentials. Legacy bindings remain valid when their original hash still
matches; an unprovable old change requires review rather than guessed migration.

Synthetic qualification must include a real due admission after recovery and a
checkpoint, separate from active readback. It does not reproduce the original
M365 incident, explain why that owner exited, or establish live-provider success.
