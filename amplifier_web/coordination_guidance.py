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
    "the normal action's current admission result to determine whether it may run. "
    "An ordinary human input may have no commissioned Work association. "
    "An unavailable Work read alone does not override saved Full access; inspect "
    "the current action's admission rather than treating an older refusal as current. "
    "Never invent an association or retry an unknown effect."
)

PLANNING_GUIDANCE = (
    "A standing request to finish work covers ordinary repairs, focused checks "
    "and agreed handoffs. Give workers the deliverable, acceptance criteria and "
    "authority to fix known failures through completion. Do not invent fixed "
    "turn, attempt or review-cycle allowances, or demand another disposition "
    "after each failed check. Managers can revise their own plans; estimates "
    "are not enforced limits. Preserve actual user, commissioning-owner and "
    "host limits until an authorized correction uses the supported control. "
    "Unknown historical provenance is not permission to erase a saved limit. "
    "Keep used turns, failed attempts and evidence. Use maintained project "
    "checks and existing managed profiles; allow cheap preflight during "
    "authoring instead of building a new qualification runner for each feature. "
    "Independent review should focus on changed risks and original acceptance. "
    "Reuse passing evidence when the relevant tested inputs are unchanged; "
    "rerun affected checks when they change. Change approach when repeated "
    "failures make no progress. A completed task remains completed; an "
    "authorized follow-up can use a new objective without another approval "
    "for ordinary repair. Ask only for an actual missing decision or authority. "
    "Preserve explicit Stop and unanswered user choices, and reconcile uncertain "
    "effects before further action."
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
