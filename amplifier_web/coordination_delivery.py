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


def _text(value, maximum=200, *, empty=False):
    return isinstance(value, str) and (empty or bool(value)) and len(value) <= maximum


def _integer(value, minimum=0, maximum=None):
    return type(value) is int and value >= minimum and (maximum is None or value <= maximum)


def _texts(value, maximum):
    return isinstance(value, list) and len(value) <= maximum and all(_text(v) for v in value)


def _digest(value):
    return _text(value, 64) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _valid_required(required):
    if (not isinstance(required, dict) or set(required) - REQUIRED_FIELDS
            or required.get('effectAuthorized') is not False
            or not _text(required.get('status'), 20)
            or required['status'] not in {'available', 'unavailable'}):
        return False
    # Even unavailable headers must not smuggle source bodies in scalar slots.
    for field in ('reason', 'rootSessionId', 'generationId'):
        if required.get(field) is not None and not _text(required[field], empty=True):
            return False
    for field in ('inputIds', 'associationRequestIds'):
        if field in required and not _texts(required[field], 16):
            return False
    if 'inputCount' in required and not _integer(required['inputCount']):
        return False
    coverage = required.get('coverage')
    if (not isinstance(coverage, dict)
            or set(coverage) - {'complete', 'supportedInputIds', 'unsupportedInputs', 'reason', 'detail'}
            or type(coverage.get('complete')) is not bool
            or not _texts(coverage.get('supportedInputIds', []), 16)
            or any(not _text(coverage[k]) for k in ('reason', 'detail') if k in coverage)):
        return False
    unsupported = coverage.get('unsupportedInputs', [])
    if (not isinstance(unsupported, list) or len(unsupported) > 16
            or any(not isinstance(row, dict) or set(row) != {'inputId', 'reason'}
                   or not _text(row['inputId']) or not _text(row['reason'], empty=True)
                   for row in unsupported)):
        return False
    if required['status'] == 'unavailable':
        return (coverage['complete'] is False and all(required.get(k) is None for k in
                ('version', 'workCount', 'requirementCount', 'requirementsRef')))
    # Available is an exact producer observation, not an optional collection of
    # optimistic labels. Its read handle and input coverage must agree.
    inputs = required.get('inputIds')
    return (REQUIRED_FIELDS - {'inputCount'} <= set(required)
            and _digest(required['version']) and required['reason'] is None
            and _text(required['rootSessionId']) and _text(required['generationId'])
            and _texts(inputs, 16) and bool(inputs) and len(set(inputs)) == len(inputs)
            and len(required['associationRequestIds']) == len(inputs)
            and required.get('inputCount', len(inputs)) == len(inputs)
            and _integer(required['workCount'], 0, 8)
            and _integer(required['requirementCount'], 2 * required['workCount'],
                         4097 * required['workCount'])
            and coverage == {'complete': True, 'supportedInputIds': inputs, 'unsupportedInputs': []}
            and required['requirementsRef'] == {'action': 'workspace.work.invocation',
                'args': {'sessionId': required['rootSessionId'], 'expectedVersion': required['version']}})


def _valid_stub(row, workspace):
    if (not isinstance(row, dict) or set(row) != STUB_FIELDS
            or not _text(row['id']) or not _text(row['originSessionId'])
            or not _integer(row['revision'], 1) or not _integer(row['promiseRevision'], 1, row['revision'])
            or not _text(row['title'], 100)
            or not _text(row['nextAction'], empty=True) or not _text(row['blockedReason'], empty=True)
            or row['candidate'] is not None and not _text(row['candidate'], 100)
            or not _text(row['latestOutcome'], 100)
            or type(row['humanDecisionCurrent']) is not bool
            or not _texts(row['attentionReasons'], 16)):
        return False
    return row['readRef'] == {'action': 'workspace.work.read',
                              'args': {'workspaceId': workspace, 'id': row['id']}}


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
                or set(discovery) != DISCOVERY_FIELDS or not _valid_required(required)):
            return _unavailable_work()
        items = discovery.get('items')
        if (not isinstance(items, list) or len(items) > 20
                or not _text(discovery['workspaceId']) or not _integer(discovery['version'])
                or not _digest(discovery['snapshotVersion']) or not _digest(discovery['observationVersion'])
                or not _integer(discovery['total'], len(items))
                or type(discovery.get('truncated')) is not bool
                or discovery['truncated'] != bool(discovery.get('nextAfter'))
                or discovery['nextAfter'] is not None and not _text(discovery['nextAfter'], 4096)
                or type(discovery['observedAt']) not in (int, float) or not 0 <= discovery['observedAt'] < 10**15
                or not _text(discovery['coverage'], 500) or not _text(discovery['authority'], 500)
                or discovery['detailRequired'] is not True):
            return _unavailable_work()
        if any(not _valid_stub(row, discovery['workspaceId']) for row in items):
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
