"""Bounded orientation and recovery hints; never an authority transfer."""

ROOT_SCOPE_MESSAGE = (
    "This root-only collaboration operation is denied: a child cannot borrow "
    "its root's collaboration authority. Do not retry with a root ID or another "
    "tool. Retain the requested action, completed effects and this refusal for "
    "your normal delegated result to the parent. Continue independent work only "
    "while it remains authorized and currently admitted. If this operation is "
    "essential, report the blocker through that result path. Actual Stop, stale "
    "execution, revocation, scope and permission restrictions still govern; this "
    "refusal neither cancels unrelated work nor requires new user approval."
)

READ_GUIDANCE = (
    "For assignment orientation, follow assignment.briefRef in the current "
    "coordination reference before gathering member histories. Read exact outcomes "
    "and constraints only as needed for the next decision; a brief is not proof of "
    "completion and never replaces required Work details. A read receipt with "
    "effectAuthorized:false grants no execution authority; that field alone is "
    "not an execution denial. Preserve its status, coverage and reason, and use "
    "the normal action's current admission result to determine whether it may run. "
    "An ordinary human input may have no commissioned Work association. "
    "An unavailable Work read alone does not override saved Full access; inspect "
    "the current action's admission rather than treating an older refusal as current. "
    "Never invent an association or retry an unknown effect."
)

PLANNING_GUIDANCE = (
    "Managers coordinate many independent workers when useful. Commission complete "
    "deliverables with acceptance criteria and clear ownership; workers implement, "
    "repair and test within that authority and return a result or concrete external "
    "blocker. Do not make routine acknowledgements, repeated inventories or repeated "
    "passing checks a workflow. Discover supported placement for concurrent writers; "
    "do not share a mutable checkout accidentally. A child retains its own authority "
    "and cannot impersonate its root.\n\n"
    "One manager coordinates many independent goal-driven workers when tasks "
    "permit. Give each worker a deliverable, acceptance criteria and existing "
    "authority to carry implementation, repair, isolated checks and a reviewable "
    "result or PR through completion. Report results or concrete external blockers, "
    "not routine progress. A standing objective includes ordinary corrections "
    "and agreed handoffs; do not invent turn, attempt or review-cycle allowances "
    "or demand renewed approval after each failed check. Managers can revise "
    "their own plans; estimates are not enforced limits. Preserve actual user, "
    "commissioning-owner and host limits until an authorized supported change; "
    "unknown provenance is not permission to erase a saved limit. Keep used "
    "turns and failed evidence. Use maintained checks and managed profiles, "
    "allowing cheap preflight while authoring. Review changed risks and original "
    "acceptance; reuse passing evidence with unchanged relevant inputs and "
    "rerun affected checks when they change. Change approach when repeated "
    "failures make no progress. Accept completed contributions without another "
    "worker acknowledgement turn; keep completed objectives and their evidence. "
    "Send completed review decisions directly to the responsible manager through "
    "coordination.send mode:auto when you continue unrelated work. Reference "
    "the reviewed source and evidence; do not wait for your whole turn to finish "
    "or present a staged reply as sealed. Independent "
    "Full-access managers can receive fresh work after completing an earlier "
    "objective; completion alone does not require a user or external coordinator "
    "to reopen it. Do not turn an obsolete handoff note into a new approval gate. "
    "A progress report is not a completion boundary and does not continue "
    "execution after the current turn ends. While accepted work remains, take "
    "the next authorized action before ending the turn, or leave a durable "
    "commissioned worker with a saved return path. Do not promise to continue "
    "after ending the current turn. Commission manager-owned work with "
    "coordination.create in the inherited workspace; use session.handoff.start "
    "for independent work in a destination folder, without automatic result return. "
    "Neither requires an open browser; review and navigation do. "
    "Report a blocker only when execution genuinely "
    "cannot continue without a missing decision, authority or external event. "
    "Ask only for a missing decision or authority. Preserve actual Stop, "
    "unanswered user choices and ownership; reconcile uncertain effects."
)

NATIVE_CHILD_GUIDANCE = (
    "You are a native delegated child, not a root conversation or a durable "
    "commissioned root worker. Return findings, completed effects, retained "
    "evidence and concrete blockers through your normal delegated result to the "
    "parent. Root-only collaboration sends, replies, commissioning and root task "
    "ownership advice do not apply to you; do not borrow a root ID or retry a "
    "denied operation through another tool. A refused optional report blocks that "
    "operation, not unrelated independently authorized work that is still admitted. "
    "If a denied operation is essential, return the dependency as a blocker. "
    "Actual Stop, stale execution, revoked permission, scope and lifecycle checks "
    "still govern. Inspect saved receipts for unknown effects; never replay them."
)


def assignment_orientation(session, summary, principal):
    """Offer one exact passive read, without loading members at every call."""
    if not summary:
        return summary
    native = session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id']
    if (session.get('sessionKind', 'root') != 'root'
            or principal and principal != native):
        return summary
    return {**summary, 'briefRef': {
        'action': 'assignment.brief', 'args': {'sessionId': session['id']}},
        'briefPurpose': 'Start here for current member status and exact outcome links. '
                        'Page with nextAfter; previews do not replace task constraints or evidence.'}
