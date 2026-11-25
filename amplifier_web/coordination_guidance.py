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
    "Commission complete deliverables in parallel when useful. Give each worker "
    "clear ownership and acceptance criteria. Within existing authority, workers "
    "implement, repair, test in isolation and return a reviewable result or PR. "
    "Choose supported placement for concurrent writers to avoid unintended "
    "checkout sharing. A child acts under its own authority and cannot impersonate its root.\n\n"
    "Commission manager-owned work with coordination.create in the inherited workspace. "
    "Use session.handoff.start for independent work in a destination folder, without automatic result return. "
    "Neither requires an open browser; review and navigation do. "
    "A progress report is not a completion boundary. Before ending a turn with "
    "accepted work open, take the next authorized action or leave a durable worker "
    "with a saved return path. Do not promise to continue after ending the current turn.\n\n"
    "A standing objective covers ordinary corrections and agreed handoffs. Managers "
    "can revise their plans and estimates. Preserve user, commissioning-owner and "
    "host limits until an authorized, supported change, including when provenance is unclear. "
    "Keep used turns and failed evidence. Do not invent attempt, turn or review limits. "
    "Use maintained checks and managed profiles. Cheap preflight is fine while authoring. "
    "Review changed risks against the original acceptance criteria. Reuse passing "
    "checks with unchanged relevant inputs and rerun affected checks. Change approach "
    "when failures repeat without progress.\n\n"
    "Accept completed work and retain its objective and evidence without another "
    "acknowledgement turn or repeated inventory. When continuing unrelated work, "
    "send completed review decisions directly to the responsible manager with "
    "coordination.send mode:auto. Cite the reviewed source and evidence. A staged reply "
    "still needs delivery. Ordinary peer speech needs no prior result acknowledgement "
    "or qualified artifact report. Use typed results for saved deliverables and retain "
    "files only when returning file evidence.\n\n"
    "An independent Full-access manager can take fresh work after completion. "
    "An old handoff or failed check does not require renewed approval. Report a "
    "concrete blocker when a missing decision, authority or external event prevents "
    "progress. Ask only for the decision or authority you need. Respect actual Stop, "
    "unanswered user choices and ownership. Reconcile uncertain effects."
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
