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
