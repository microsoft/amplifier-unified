# Approved decision queue — October 16, 2026

User approved #293, #294 and #745, and clarified #676 as incoming messages from peer chats, presented like delegate exchanges. Development stays in the owned spark-2 DTU; live spark-1 adoption remains separate.

## Delivery ownership and acceptance

- #676: compact collapsed incoming peer exchanges, source attribution, explicit expansion/copy, no suppression of human messages or approvals. Root owns implementation and real browser coverage.
- #745: assistant-attributed reactions to human messages through existing message.reaction; server derives actor from authenticated origin. User/assistant ownership remains independent; desired-state retries, persistence, bounded annotation refresh, and no generated turn. Root owns implementation and real browser coverage.
- #293: independently persisted step/turn/conversation observability preferences and per-metric display/copy/export, optional compact status snapshot. Reuse accounting; unknown metrics stay unknown. Stories acceptance agent owns source and focused/browser acceptance.
- #294: durable owner-bound Review changes with original/proposed/latest comparison, reviewed correction and existing attachment workflow. GitHub does not promise atomic conditional issue PATCH, so original title/body replacement remains an explicit limitation. Followup review agent owns source and focused/browser acceptance.
- Background: #820 final post-release fixture qualification; include verified fixes and merged #819 in integration without broad local reruns.

## #786 evidence reconciliation

Read only the user-named spark-1 Readiness Manager conversation. Nine recorded coordination.create calls have accepted receipts. All nine return the parent workspace, including the voice intake and spark-2 readiness chats. Creation within the current workspace already works; the unresolved #786 request is an independently targeted different workspace/folder. No live commands or changes were made. Build on the canonical creation path rather than duplicate this feature. Cross-workspace confirmation behavior remains a separate pending product decision.

## Completion checklist

- [x] Focused source/authority tests for peer/reaction behavior in owned DTU.
- [x] Real built-client peer/reaction desktop and narrow viewport checks with durable reload and negative cases.
- [x] Update reusable smoke inventory, current outcomes, and acceptance limits.
- [x] Independent review, integrate all accepted work, qualify combined assets. See release172-qualification.md.
- [ ] Create reviewable PR(s), report exact publication status and unresolved decisions.
- [ ] Retain evidence and active preview; clean obsolete owned resources and record manifest.
