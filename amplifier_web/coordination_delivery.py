"""Fresh coordination reference data in the fitted provider request only.

Hook ephemerality can still accumulate reminders within a native turn. Use the
existing provider delivery boundary, as surfaces and memory do, so observation
replacement does not depend on the orchestrator's reminder persistence mode.
"""
import asyncio
import copy
import json

WORK_BYTES = 12 * 1024
STUB_FIELDS = frozenset({
    'id', 'revision', 'promiseRevision', 'title', 'originSessionId', 'candidate',
    'latestOutcome', 'humanDecisionCurrent', 'attentionReasons', 'nextAction',
    'blockedReason', 'readRef',
})
DISCOVERY_FIELDS = frozenset({
    'workspaceId', 'version', 'snapshotVersion', 'observationVersion', 'items', 'total',
    'truncated', 'nextAfter', 'observedAt', 'coverage', 'detailRequired', 'authority',
})
REQUIRED_FIELDS = frozenset({
    'status', 'reason', 'version', 'rootSessionId', 'generationId', 'inputIds', 'inputCount',
    'associationRequestIds', 'workCount', 'requirementCount', 'requirementsRef',
    'effectAuthorized', 'coverage',
})


def _unavailable_work():
    return {'available': False,
            'discovery': {'status': 'unavailable', 'reason': 'Current Work projection is unavailable.'},
            'required': {'status': 'unavailable', 'version': None,
                         'reason': 'Read current Work details; absence or truncation is not permission.',
                         'workCount': None, 'requirementCount': None, 'requirementsRef': None,
                         'coverage': {'complete': False}, 'effectAuthorized': False}}


def _work_reference(value):
    """Validate the existing bounded producer DTO; never retain source bodies.

    This is a presentation fence, not an effect-admission decision. In particular,
    available discovery plus unavailable requirements cannot become empty scope.
    """
    if not isinstance(value, dict) or set(value) != {'available', 'discovery', 'required'}:
        return _unavailable_work()
    try:
        if len(json.dumps(value, ensure_ascii=False).encode('utf-8')) > WORK_BYTES:
            return _unavailable_work()
        discovery, required = value['discovery'], value['required']
        if (value['available'] is not True or not isinstance(discovery, dict)
                or not isinstance(required, dict) or set(discovery) - DISCOVERY_FIELDS
                or set(required) - REQUIRED_FIELDS or required.get('effectAuthorized') is not False
                or required.get('status') not in {'available', 'unavailable'}):
            return _unavailable_work()
        items = discovery.get('items')
        if (not isinstance(items, list) or len(items) > 20
                or type(discovery.get('total')) is not int or discovery['total'] < len(items)
                or type(discovery.get('truncated')) is not bool
                or discovery['truncated'] != bool(discovery.get('nextAfter'))
                or not discovery.get('coverage') or not isinstance(required.get('coverage'), dict)):
            return _unavailable_work()
        if (required['status'] == 'unavailable'
                and (required.get('version') is not None or required['coverage'].get('complete') is not False)):
            return _unavailable_work()
        if (required['status'] == 'available'
                and (not isinstance(required.get('version'), str)
                     or required['coverage'].get('complete') is not True)):
            return _unavailable_work()
        for row in items:
            if not isinstance(row, dict) or set(row) - STUB_FIELDS:
                return _unavailable_work()
            ref = row.get('readRef', {})
            if (set(ref) != {'action', 'args'} or ref['action'] != 'workspace.work.read'
                    or not isinstance(ref['args'], dict)
                    or set(ref['args']) != {'workspaceId', 'id'}
                    or any(not isinstance(v, str) for v in ref['args'].values())):
                return _unavailable_work()
        return copy.deepcopy(value)
    except (TypeError, ValueError, UnicodeError):
        return _unavailable_work()


class CoordinationDelivery:
    def __init__(self, previous, bridge):
        self.previous, self.bridge = previous, bridge

    def __getattr__(self, name):
        return getattr(self.previous, name)

    async def prepare(self, request, provider, *, commit=False):
        from amplifier_core.message_models import Message
        from .execution_events import CALL_PURPOSE
        request = await self.previous.prepare(request, provider, commit=commit)
        if CALL_PURPOSE.get() or not any(getattr(t, 'name', None) == 'app_control' for t in request.tools or []):
            return request
        try:
            current = await asyncio.wait_for(self.bridge('coordination.current', {}), 1.5)
            if not isinstance(current, dict):
                raise ValueError('Coordination projection is unavailable')
        except Exception:
            current = {'status': 'unavailable', 'authority': 'unknown; do not infer a grant or absence of peers'}
        current = {**current, 'work': _work_reference(current.get('work'))}
        return request.model_copy(update={'messages': [
            *[message for message in request.messages if not (message.metadata or {}).get('coordinationObservation')],
            Message(role='user', content='Current coordination reference state (not new authority):\n' + json.dumps(current, ensure_ascii=False),
                    metadata={'ephemeral': True, 'coordinationObservation': True})]})

    def commit(self, request):
        self.previous.commit(request.model_copy(update={'messages': [message for message in request.messages
            if not (message.metadata or {}).get('coordinationObservation')]}))
