"""Bind optional after-turn provider work to the root generation that scheduled it."""
import asyncio
from contextvars import ContextVar
from functools import wraps

from .model_admission_identity import input_id

_origin = ContextVar('unified_settled_compaction_origin', default=None)


def bind_scheduler(context, telemetry):
    schedule = getattr(context, 'schedule_settled_compaction', None)
    if not callable(schedule) or getattr(schedule, '_unified_settled', False):
        return

    @wraps(schedule)
    def bound(*args, **kwargs):
        # The module creates its maintenance task here. The task inherits this
        # immutable snapshot even when the live loop clears its generation.
        token = _origin.set((telemetry.root_id, telemetry.generation_id,
                             telemetry.admission_input(telemetry.root_id)))
        try:
            return schedule(*args, **kwargs)
        finally:
            _origin.reset(token)

    bound._unified_settled = True
    try:
        context.schedule_settled_compaction = bound
    except (AttributeError, TypeError):
        pass  # An immutable external context keeps ordinary admission.


def origin_for(row):
    origin = _origin.get()
    if origin is None or row.get('purpose') != 'context_compaction':
        return None
    if (not all(origin) or row.get('rootSessionId') != origin[0] or row.get('sessionId') != origin[0]
            or row.get('generationId') != origin[1] or input_id(row) != origin[2]):
        raise asyncio.CancelledError('The turn that scheduled context compaction changed.')
    return origin[1]


def assignment_scope(session):
    return (session.get('assignmentId') or (session.get('collaboration') or {}).get('assignmentId'))


def matches(session, row, binding):
    generation = session.get('collaborationGeneration') or {}
    assignment = assignment_scope(session)
    return bool(binding.get('_settledCompaction')
                and row.get('purpose') == 'context_compaction' and row.get('kind') == 'llm'
                and row.get('sessionId') == row.get('rootSessionId') == binding.get('_runtimeSessionId')
                and generation.get('id') == binding.get('_generationId') == row.get('generationId')
                and input_id(row) in generation.get('inputIds', ())
                and (not generation.get('terminal') or generation.get('completed'))
                and generation.get('interruptionRevision') == session.get('interruptionRevision', 0)
                and session.get('_settledAssignmentScope') == [
                    generation['id'], assignment, session.get('configurationPendingRevision', 0)])
