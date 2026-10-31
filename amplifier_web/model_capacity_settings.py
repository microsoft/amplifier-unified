"""User-owned host-wide model admission waiting policy; no runtime restart."""
import json
import uuid

from amplifier_operations.coordination import fingerprint

DEFAULTS = {'revision': 0, 'maxWaitingCalls': 0, 'waitTimeoutSeconds': 0}
RECORD = 'model-capacity-policy'


def definitions(schema, string):
    return {
        'modelCapacity.read': ('Read global model-capacity waiting settings. Does not start workers.', schema({}, [])),
        'modelCapacity.set': ('User-only: save global model-capacity waiting settings. Zero queue size or timeout means no count or time limit; work waits until capacity or Stop. Does not change spending limits or restart work.', schema({
            'expectedRevision': {'type': 'integer', 'minimum': 0},
            'maxWaitingCalls': {'type': 'integer', 'minimum': 0},
            'waitTimeoutSeconds': {'type': 'integer', 'minimum': 0},
        }, ['expectedRevision', 'maxWaitingCalls', 'waitTimeoutSeconds'])),
    }


def policy(service):
    row = service.db.execute('SELECT receipt FROM commands WHERE id=?', (RECORD,)).fetchone()
    return {**DEFAULTS, **(json.loads(row[0])['result'] if row else {})}


async def dispatch(service, action, args, origin, command_id):
    from .service import AppError
    if action == 'modelCapacity.read':
        return {'accepted': True, 'result': policy(service)}
    if origin not in {'ui', 'user'}:
        raise AppError('Only the user can change global model-capacity settings.', 403)
    identity = command_id or str(uuid.uuid4())
    digest = fingerprint([action, args])
    async with service.lock:
        previous = service.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (identity,)).fetchone()
        if previous:
            if previous[0] != digest:
                raise AppError('This command ID was used with different contents.', 409)
            return {**json.loads(previous[1]), 'duplicate': True}
        current = policy(service)
        if current['revision'] != args['expectedRevision']:
            raise AppError('Model-capacity settings changed. Reload them before saving.', 409)
        value = {key: args[key] for key in ('maxWaitingCalls', 'waitTimeoutSeconds')}
        value['revision'] = current['revision'] + 1
        receipt = {'accepted': True, 'commandAction': action, 'result': value}
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
    return receipt
