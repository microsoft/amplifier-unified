# Integrating the execution view and new-chat lifecycle

Unified main0.19.7 (`cddbd7d4`) is merged into the unreleased0.20.0 candidate.
The canonical event reader and lazy request details remain authoritative for
execution content. New-chat drafts, idempotent first-send creation, attachments,
composer controls, Canvas recovery and export controls are preserved alongside
the shared operation/question/coordination controls and scheduled task creation.

Integration found an accounting conflict: marking every live model-call node as
transient would remove the exact budget receipt on restart. The existing
`execution.retiredUsageNodes` now retains only allowlisted accounting fields and
worker ancestry, deduplicated by session/call ID and revision. Tool/input/output
bodies and lazy request references remain excluded. Full restart regressions
confirm root-plus-child totals and exhausted budget admission survive; an
unfinished old receipt becomes unknown and cannot be replayed.

Validation:141 affected backend checks passed before the accounting refinement;
52 focused budget/event-reader/new-chat/scheduling checks passed afterward.
221 frontend tests, the actual new-chat browser, and the actual new-task scheduler
worker/browser fixture passed. Production assets build. These are isolated local
qualification results, not a0.20 deployment claim.
