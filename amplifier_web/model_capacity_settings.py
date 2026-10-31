"""Host-wide model admission waiting policy; no runtime restart."""
import json
import uuid

from amplifier_operations.coordination import fingerprint
from .errors import AppError

DEFAULTS = {'revision': 0, 'maxWaitingCalls': 0, 'waitTimeoutSeconds': 0}
RECORD = 'model-capacity-policy'


def definitions(schema, string):
    return {
        'modelCapacity.read': ('Read global model-capacity waiting settings. Does not start workers.', schema({}, [])),
        'modelCapacity.set': ('Save global model-capacity waiting settings under standing Full or user control. Zero queue size or timeout means no count or time limit; work waits until capacity or Stop. Does not change spending limits or restart work.', schema({
            'expectedRevision': {'type': 'integer', 'minimum': 0},
            'maxWaitingCalls': {'type': 'integer', 'minimum': 0},
            'waitTimeoutSeconds': {'type': 'integer', 'minimum': 0},
        }, ['expectedRevision', 'maxWaitingCalls', 'waitTimeoutSeconds'])),
    }


def policy(service):
    row = service.db.execute('SELECT receipt FROM commands WHERE id=?', (RECORD,)).fetchone()
    return {**DEFAULTS, **(json.loads(row[0])['result'] if row else {})}


def public(receipt):
    return {key: value for key, value in receipt.items() if key != '_authority'}


def saved_command(service, identity, digest):
    previous = service.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (identity,)).fetchone()
    if previous:
        if previous[0] != digest:
            raise AppError('This command ID was used with different contents or a different calling conversation.', 409)
        return {**public(json.loads(previous[1])), 'duplicate': True}


def capture(service, source, origin):
    authority = service.access_mode.capture_full(source, origin)
    if authority is None:
        raise AppError('Only the user or a calling conversation with Full access can change global model-capacity settings.', 403)
    return {**authority, 'workspace': source['workspace'],
            'nativeSessionId': source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id'],
            'executionRevision': source.get('executionRevision', 0),
            'interruptionRevision': source.get('interruptionRevision', 0)}


def check(service, authority):
    if authority is None:
        return
    source = service._session(authority['sessionId'], hydrate=False)
    if (not service.access_mode.full(source, authority['accessRevision'])
            or service.assignments.blocked(source)
            or source['workspace'] != authority['workspace']
            or (source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id']) != authority['nativeSessionId']
            or any(source.get(key, 0) != authority[key] for key in ('executionRevision', 'interruptionRevision'))):
        raise AppError('Model-capacity authority changed or its work was stopped. No settings were changed.', 409)


async def dispatch(service, action, args, origin, command_id, caller_session_id=None):
    if action == 'modelCapacity.read':
        return {'accepted': True, 'result': policy(service)}
    source = None
    if origin not in {'ui', 'user'}:
        if origin not in {'agent', 'system'} or not caller_session_id:
            raise AppError('Only the user or a calling conversation with Full access can change global model-capacity settings.', 403)
        source = service._session(caller_session_id, hydrate=False)
    identity = command_id or str(uuid.uuid4())
    digest = fingerprint([action, args] + ([{'sourceSessionId': source['id']}] if source else []))
    previous = saved_command(service, identity, digest)
    if previous:
        return previous
    authority = capture(service, source, origin) if source else None
    async with service.lock:
        previous = saved_command(service, identity, digest)
        if previous:
            return previous
        check(service, authority)
        current = policy(service)
        if current['revision'] != args['expectedRevision']:
            raise AppError('Model-capacity settings changed. Reload them before saving.', 409)
        value = {key: args[key] for key in ('maxWaitingCalls', 'waitTimeoutSeconds')}
        value['revision'] = current['revision'] + 1
        receipt = {'accepted': True, 'commandAction': action, 'result': value}
        if authority is not None:
            receipt['_authority'] = authority
        service.db.commit()
        try:
            service.db.execute('INSERT OR REPLACE INTO commands VALUES(?,?,?)',
                               (RECORD, 'host-model-capacity-policy', json.dumps({'commandAction': 'modelCapacity.policy', 'result': value})))
            service.db.execute('INSERT INTO commands VALUES(?,?,?)', (identity, digest, json.dumps(receipt)))
            service.db.commit()
        except BaseException:
            service.db.rollback()
            raise
        service.coordination.notify()
    return public(receipt)
