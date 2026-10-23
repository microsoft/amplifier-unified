# Shared Work contract — v1 candidate

Status: proposed extension to the accepted workspace experience; not a claim of implementation or acceptance.

## Intent and existing contracts

A person and an assistant need the same inspectable list of accepted requests.
An accepted request must survive a long conversation, compaction, an interrupted
worker and changed priorities. Preserve original wording and corrections,
responsibility, acceptance criteria, evidence and unresolved effects.

This extends [Workspace work](experience.v1.md#workspace-work-and-combined-checks)
with a first-class Work view. It preserves the family vision's native conversation
history, private client state, bounded discovery and single execution authority.
It does not replace chat history, assignment claims, task goals, resource records,
approvals or Stop. No scheduler, alternate database or automatic execution is added.

## Identities and ownership

1. A **Work item** identifies a durable promise. Its originating conversation and
   exact source remain linked. A worker chat is one bounded execution context;
   several chats may serve one item. A goal or turn checklist controls execution,
   not acceptance of the promise.
2. The host owns Work records in its existing durable receipt store. UI and agent
   actions use the same service and revisions. An independent chat can record Work
   without creating or joining an assignment. Work links never enroll peers.
3. Existing assignment tasks own execution claims. Work has one accountable owner
   and explicit execution links, not a competing claim. Owner disappearance means
   responsibility needs review; it does not release a claim or grant takeover.
4. Capture accepted work before commissioning it. Suggestions remain proposals.
   Source classification and interpretations are attributed. A Work status never
   proves that a tool action is permitted.

## Data and authority

The current record is bounded. Immutable source, correction, evidence and
closure receipts are paged separately. Its record revision changes on every
mutation. Its promise revision changes only when scope, acceptance or constraints
change. Old checks remain tied to their original promise and candidate.

Source capture resolves the exact session/message and native anchors available
from its existing owner, retains the original text hash and snapshot, and records
host-derived input origin. `role=user` alone does not mean human: peer inputs use
that role too. Missing provenance stays unknown. A capture can load the one source
history without starting a worker; Work catalog/detail reads cannot hydrate history.

Every mutation uses a stable command ID, payload fingerprint and current expected
revision. A retry with identical intent returns the retained result after current
authorization checks. Changed payload fails. Current actor/root, workspace,
membership/claim and Stop fences remain authoritative. Neither a supplied actor
name nor a late success report overrides a newer revision. A historical return
may be retained for its original attempt without updating current acceptance.

Distinguish these observations:

| Observation | Meaning |
| --- | --- |
| Host fact | Retained state from its canonical operation/worker/claim owner. |
| Participant report | What an execution owner reported; not artifact verification. |
| Owner check | Exact check evidence recorded by a root that built/executed the candidate. |
| Independent check | Exact check evidence recorded by a different authorized root, with criterion, promise and candidate binding. |
| Human decision / delivery | A proven human decision or a separately attributed exact release observation; neither follows from a passing check. |

A delegated child review retains its parent-root association. A caller cannot
supply an independent identity or host-fact label. The host validates receipt
association and provenance, not the semantic truth of arbitrary prose. Failed,
unknown, skipped and unrun criteria remain visible. Self-reported shipment is not
host-observed delivery.

An explicit publication report binds the current promise and candidate, reporter,
destination, exact references and reported outcome. It is displayed as **Reported
published**, never **Shipped** or host-confirmed delivery. It neither invokes a
publisher nor fetches a reference. Failed, unknown and not-run publication reports
remain attributed observations. Existing publisher receipts without a proven
candidate/artifact association cannot establish that association by inference.
Publication reports do not alter verification, human decisions or closure gates.
Scope/candidate changes clear the current delivery projection, retaining history.

Explicit closure records satisfied, cancelled or superseded, its reason and
source/evidence basis. Reopening appends a new disposition while preserving the
previous one. It neither reopens a terminal assignment task nor retries an effect.
Uncertain operations and cleanup responsibility survive closure. Task/worker Stop,
grants and budgets are never reset by Work actions.

## Passive experience and continuity

Workspace navigation offers Chats, Work, Files and Details. Work opens without a
model call, worker initialization or chat selection. Existing chat coordination
inspection remains an advanced view of its existing authorities. Only an explicit
Open chat action navigates; ordinary inspection preserves composer text,
attachments, Canvas and Settings edits, and private per-client navigation state.

Default view: accepted open work. Proposals, closed items and attention have
explicit filters. Rows show title, owner, next action/blocker and evidence freshness.
Details expose commitment, execution, verification, human decision, delivery and
cleanup separately, followed by original source, corrections, acceptance and exact
evidence links. Narrow screens use a full-width detail and a return action with
keyboard focus restored. At 320 CSS pixels no page-level horizontal scroll is needed.

Catalogs filter before paging. Details and history are bounded. Late replies from
an old view or revision cannot overwrite the current view or an unfinished edit.
A failed read retains a visibly stale observation, never an empty-work claim.

Existing request-boundary delivery supplies a bounded current summary for the
root's owned/contributing items, with total count, explicit truncation and exact
page/detail links. Every still-open assigned item remains in the reachable set,
including inactive or archived chats. A whole-work claim requires the relevant
pages and coverage. Concurrent changes invalidate a page version explicitly.
Old task corrections remain durable but do not accumulate unboundedly in the
provider request or mutation receipt. No second continuation loop is introduced.

## Existing work and coverage

No automatic bulk import or title-based merge. Reconciliation records explicit
source ranges and mapped, unresolved, inaccessible and not-reviewed coverage.
Legacy items are not silently marked accepted, independently verified or shipped.
A count of proven human inputs since the last reviewed source cursor is a review
nudge, not a semantic count of missing tasks. Ordinary conversation does not become
work automatically. A maintained board is not a complete historical inventory.

## Required discriminators

- Capture an accepted item, compact/reopen the manager, lose its worker and restart
  the host. Original source and responsibility remain; no runtime starts on read.
- Retain a successful first report, then add a source correction and a failed second
  check. Old source/results remain, and old evidence cannot qualify the new promise.
- Duplicate a host event, lose a mutation acknowledgement, race two revisions and
  submit a stale claim result. No dropped/duplicate work or overwritten new state.
- Test peer `role=user`, unknown provenance, forged identity, Stop, denied grants,
  owner-associated review and unknown operations. No authority or success promotion.
- Inspect 21 and 1,000 open items with more than 113 old corrections, a delayed
  result and a newer source/Stop change. Human/API state agrees, context is bounded,
  paging/coverage is explicit, and current constraints remain retrievable.
- Use two clients at desktop, 390 and 320 pixels with keyboard navigation, drafts,
  attachments and dirty Canvas. Reading/updating work preserves unrelated input.
- Run the real persisted workflow against an exact installed candidate outside its
  source checkout; retain the negative control, review and resource cleanup evidence.
