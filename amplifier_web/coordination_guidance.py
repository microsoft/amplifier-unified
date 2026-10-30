"""Bounded orientation and recovery hints; never an authority transfer."""

ROOT_SCOPE_MESSAGE = (
    "A child cannot borrow its root's collaboration authority. "
    "End this delegated attempt and return the requested action, completed effects "
    "and this refusal to the parent that delegated it. Do not retry with a root ID "
    "or another tool. The owning root can inspect current state and perform an "
    "already-authorized step through its own controls. This refusal does not add "
    "a requirement for new user approval; existing Stop, scope and permission "
    "restrictions still apply."
)

READ_GUIDANCE = (
    "For assignment orientation, follow assignment.briefRef in the current "
    "coordination reference before gathering member histories. Read exact outcomes "
    "and constraints only as needed for the next decision; a brief is not proof of "
    "completion and never replaces required Work details. A read receipt with "
    "effectAuthorized:false grants no execution authority; that field alone is "
    "not an execution denial. Preserve its status, coverage and reason, and use "
    "the normal action's current admission result to determine whether it may run."
)

PLANNING_GUIDANCE = (
    "When the user delegates completion, carry the work through ordinary "
    "corrections, verification and agreed handoffs within that intent. Managers "
    "may revise their own planning checkpoints and worker attempt estimates; "
    "do not turn those plans into fresh user approval requirements. Use a hard "
    "task maxTurns only for an actual required limit, not as a routine planning "
    "estimate. Existing user, commissioning-owner and host limits remain binding. "
    "An agent-origin record can still encode a user limit; do not reclassify "
    "historical limits whose source is unknown. Revise an authorized saved task "
    "through task.update, preserving used turns, failed attempts and evidence. "
    "A completed task stays completed; an authorized follow-up can use a new "
    "explicit objective without asking the user to approve ordinary repair again. "
    "An unanswered product decision, explicit Stop, unavailable permission or "
    "uncertain prior effect is different from an adjustable plan. Reconcile "
    "uncertain effects before further action. Ask for an actual missing decision "
    "or authority, not permission to change your own implementation approach."
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
