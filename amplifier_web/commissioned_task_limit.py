"""Explicit commissioning-owner limit corrections; never recipient model input.

The commands journal retains admission/uncertainty. TaskController's existing
control-state transaction remains the only authority for the mutation itself.
"""
import copy
import hashlib
import json

from amplifier_operations.coordination import fingerprint
from .task_continuity import _load_control, _saved_receipt

CORRECT = 'assignment.taskLimit.correct'
READ = 'assignment.taskLimit.read'
NATIVE = 'assignment.taskLimit.apply'


def _task_boundary(task):
    return {key: (task or {}).get(key) for key in ('id', 'revision', 'status')}


def _identity(session):
    return {
        'nativeId': session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id'],
        'workspace': session.get('workspace'),
        'workingDirectory': session.get('workingDirectory'),
        'executionRevision': session.get('executionRevision', 0),
    }


def definitions(schema, string):
    owner = {'sessionId': string(200)}
    return {
        CORRECT: ('Explicitly correct only maxTurns for your directly commissioned idle member within the original delegation. Preserve explicit user, owner and host limits; agent origin is not permission to discard a limit. Requires exact assignment/task/Stop revisions and a stable dispatch id. Retains used turns and task identity; never sends input, resumes, clears Stop or changes other budgets. Read an uncertain receipt; never replay it.', schema({
            **owner, 'targetSessionId': string(200), 'taskId': string(200),
            'expectedAssignmentRevision': {'type': 'integer', 'minimum': 1},
            'expectedTaskRevision': {'type': 'integer', 'minimum': 1},
            'expectedInterruptionRevision': {'type': 'integer', 'minimum': 0},
            'maxTurns': {'type': ['integer', 'null'], 'minimum': 1},
            'reason': {**string(4000), 'minLength': 1},
        }, ['sessionId', 'targetSessionId', 'taskId', 'expectedAssignmentRevision',
            'expectedTaskRevision', 'expectedInterruptionRevision', 'maxTurns', 'reason'])),
        READ: ('Read your exact limit-correction admission and canonical task receipt without starting a worker or repeating the mutation. Missing task evidence remains unknown, not permission to retry.', schema({
            **owner, 'commandId': string(200)}, ['sessionId', 'commandId'])),
    }


def _member(app, source, target):
    from .manager_placement import member
    assignment = app.assignments.current(source)
    row = (assignment or {}).get('members', {}).get(target['id'], {})
    if row.get('role') != 'commissioned' or row.get('creatorSessionId') != source['id']:
        raise ValueError('Limit correction requires the direct commissioning owner.')
    # Also verify the original creation receipt, workspace and current membership.
    return member(app, source, target)


def _boundary(app, target, *, preparation_guard=None):
    from .management import Management
    from .peer_resume import safety_halt
    active = target.get('collaborationGeneration') or {}
    own_preparation = (preparation_guard is not None and
        getattr(app.runtime, '_starting_guards', {}).get(target['id']) is preparation_guard)
    if (target.get('status') not in ({'idle', 'starting', 'ready'} if own_preparation else {'idle'})
            or active.get('id') and not active.get('terminal')
            or any(target.get(key) for key in ('configurationBusy', 'configurationPending', 'fileAccessPending'))
            or Management.unresolved_configuration(target)
            or any(row.get('status') in {'queued', 'starting', 'running', 'working', 'stopping'}
                   for row in target.get('workers', []))):
        raise ValueError('Limit correction requires an idle member with settled work and settings; it does not resume Stop.')
    if reason := safety_halt(target):
        raise ValueError(reason)


def _scope(app, source, target, args):
    from .schedule_handoff import latest_human
    assignment = _member(app, source, target)
    _boundary(app, target)
    lineage = app.collaboration.lineage(source, 'agent')
    if not lineage['sourceInputIds']:
        raise ValueError('Limit correction requires current admitted owner input.')
    if (assignment['revision'] != args['expectedAssignmentRevision']
            or target.get('interruptionRevision', 0) != args['expectedInterruptionRevision']):
        raise ValueError('Assignment or Stop revision changed; inspect before correction.')
    return {
        **lineage, 'assignmentId': assignment['id'],
        'assignmentRevision': assignment['revision'],
        'sourceIdentity': _identity(source), 'targetIdentity': _identity(target),
        'sourceTask': _task_boundary(source.get('task') or app.coordination.task(source['id'])),
        'sourceHumanMessageId': (latest_human(source) or {}).get('id'),
        'targetHumanMessageId': (latest_human(target) or {}).get('id'),
        'targetGenerationId': (target.get('collaborationGeneration') or {}).get('id'),
    }


def _guard(app, record, *, preparation_guard=None):
    """Synchronous host revalidation, including after runtime preparation awaits."""
    from .schedule_handoff import latest_human
    source = app._session(record['senderSessionId'], hydrate=False)
    target = app._session(record['targetSessionId'], hydrate=False)
    scope, args = record['scope'], record['arguments']
    assignment = _member(app, source, target)
    _boundary(app, target, preparation_guard=preparation_guard)
    active = source.get('collaborationGeneration') or {}
    if (_identity(source) != scope['sourceIdentity']
            or source.get('status') in {'stopped', 'stopping', 'error', 'interrupted'}
            or active.get('terminal') or active.get('id') != scope['sourceGenerationId']
            or source.get('interruptionRevision', 0) != scope['sourceInterruptionRevision']
            or active.get('interruptionRevision', 0) != scope['sourceInterruptionRevision']
            or not set(scope['sourceInputIds']).issubset(active.get('inputIds', []))
            or _task_boundary(source.get('task') or app.coordination.task(source['id'])) != scope['sourceTask']
            or (latest_human(source) or {}).get('id') != scope['sourceHumanMessageId']):
        raise ValueError('The commissioning owner instruction or Stop changed.')
    if (_identity(target) != scope['targetIdentity']
            or assignment['id'] != scope['assignmentId'] or assignment['revision'] != scope['assignmentRevision']
            or target.get('interruptionRevision', 0) != args['expectedInterruptionRevision']
            or (latest_human(target) or {}).get('id') != scope['targetHumanMessageId']
            or (target.get('collaborationGeneration') or {}).get('id') != scope['targetGenerationId']):
        raise ValueError('Assignment membership, member input or Stop changed before correction.')
    task = target.get('task') or app.coordination.task(target['id'])
    if (task.get('id'), task.get('revision')) != (args['taskId'], args['expectedTaskRevision']):
        raise ValueError('The exact member task changed before correction.')
    return target


def _forwarded(record):
    args = record['arguments']
    return {'commandId': record['commandId'], 'expectedRevision': args['expectedTaskRevision'],
            'maxTurns': args['maxTurns'], 'correction': args['reason'], 'origin': 'agent'}


def _observe(app, record):
    result = {key: copy.deepcopy(record[key]) for key in
              ('commandId', 'senderSessionId', 'targetSessionId', 'arguments', 'scope', 'state')}
    result.update(inputSubmitted=False, taskResumed=False)
    result['effectAdmission'] = copy.deepcopy(record.get('effectAdmission'))
    try:
        # Observe the original native authority even if later placement or
        # membership changed. Never substitute another task's current identity.
        target = {'id': record['targetSessionId'],
                  'runtimeSessionId': record['scope']['targetIdentity']['nativeId']}
        saved = _load_control(app, target)
        observed = _saved_receipt(saved.get('taskReceipts', {}), record['commandId'])
        receipt = observed.get('receipt') or {}
        expected = hashlib.sha256(json.dumps(['task.update', _forwarded(record)], sort_keys=True).encode()).hexdigest()
        if (receipt.get('fingerprint') == expected and receipt.get('taskId') == record['arguments']['taskId']
                and receipt.get('revision') == record['arguments']['expectedTaskRevision'] + 1):
            result.update(state='applied', receipt=receipt)
        else:
            result.update(state='unknown', receipt=None, receiptObservation=observed['status'])
    except (ValueError, KeyError, TypeError, AttributeError):
        result.update(state='unknown', receipt=None, receiptObservation='unavailable')
    result['readRef'] = {'action': READ, 'args': {'sessionId': record['senderSessionId'], 'commandId': record['commandId']}}
    return {'accepted': True, 'result': result}


async def dispatch(app, action, args, origin, command_id, caller):
    from .service import AppError
    from .collaboration import PRINCIPAL
    if origin != 'agent' or not caller or args['sessionId'] != caller:
        raise AppError('Limit corrections belong to the authenticated commissioning root.', 409)
    source = app.collaboration.source({}, origin, caller)
    if PRINCIPAL.get() != _identity(source)['nativeId']:
        raise AppError('Limit correction requires its authenticated root principal.', 409)
    if action == READ:
        record = app.collaboration.receipt(args['commandId'])
        if record.get('commandAction') != CORRECT or record.get('senderSessionId') != source['id']:
            raise AppError('Read only your exact limit-correction receipt.', 409)
        return _observe(app, record)
    if not isinstance(command_id, str) or not command_id or len(command_id) > 200:
        raise AppError('Limit correction requires a stable dispatch command ID.', 400)
    digest = fingerprint([CORRECT, caller, args])
    async with app.lock:
        previous = app.collaboration.duplicate(command_id, digest)
        if previous:
            if previous.get('commandAction') != CORRECT or previous.get('senderSessionId') != caller:
                raise AppError('This command belongs to another operation.', 409)
            return {**_observe(app, previous), 'duplicate': True}
        try:
            target = app.collaboration.authorize(source, {'sessionId': args['targetSessionId']}, hydrate=False)
            scope = _scope(app, source, target, args)
            record = {'accepted': True, 'commandAction': CORRECT, 'commandId': command_id,
                      'senderSessionId': caller, 'targetSessionId': target['id'],
                      'arguments': copy.deepcopy(args), 'scope': scope, 'state': 'pending'}
            _guard(app, record)
        except ValueError as exc:
            raise AppError(str(exc), 409) from None
        # Commit admission BEFORE any native effect. An interrupted operation is
        # observed through this ID, never sent a second time by duplicate dispatch.
        app.collaboration.insert(command_id, digest, record)
        app.db.commit()
    try:
        def preparation_guard():
            return _guard(app, record, preparation_guard=preparation_guard)
        await app.management.ensure_runtime(target, restart_guard=preparation_guard,
            preserve_configuration=True,
            settled=lambda: app._session(target['id'], hydrate=False).get('status') == 'idle')
        _guard(app, record)
        result = await app.runtime.control(target['id'], NATIVE, {'commandId': command_id},
                                           guard=lambda: _guard(app, record))
        from .task_continuity import publish_result
        await publish_result(app, target['id'], result)
    except BaseException:
        async with app.lock:
            record = app.collaboration.receipt(command_id)
            record['state'] = 'unknown'
            app.collaboration.save(command_id, record)
            app.db.commit()
        raise
    async with app.lock:
        record = app.collaboration.receipt(command_id)
        record['state'] = 'applied'
        app.collaboration.save(command_id, record)
        app.db.commit()
    return _observe(app, record)


def authorize(app, sid, args):
    """Private native callback. Payload comes from the original host admission."""
    from .collaboration import PRINCIPAL
    if set(args) != {'commandId', 'stage'} or args['stage'] not in {'check', 'commit'}:
        raise ValueError('Limit admission requires its original command ID and native stage.')
    record = app.collaboration.receipt(args['commandId'])
    target = app._session(sid, hydrate=False)
    native = target.get('runtimeSessionId') or target.get('nativeIdentity') or sid
    if (PRINCIPAL.get() != native or record.get('commandAction') != CORRECT
            or record.get('targetSessionId') != sid or record.get('state') != 'pending'):
        raise ValueError('The original native limit-correction admission is unavailable.')
    _guard(app, record)
    if args['stage'] == 'commit':
        if record.get('effectAdmission'):
            raise ValueError('This limit effect was already admitted; inspect its receipt.')
        # Linearization point: later owner changes cannot retract an admitted
        # native effect. A later local recipient Stop is still checked by native.
        record['effectAdmission'] = {
            'assignmentRevision': record['scope']['assignmentRevision'],
            'sourceInterruptionRevision': record['scope']['sourceInterruptionRevision'],
            'targetInterruptionRevision': record['arguments']['expectedInterruptionRevision'],
            'taskId': record['arguments']['taskId'],
            'taskRevision': record['arguments']['expectedTaskRevision'],
        }
        app.collaboration.save(record['commandId'], record)
        app.db.commit()
    return {'taskId': record['arguments']['taskId'], 'mutation': _forwarded(record)}


async def native_apply(controls, args, authorize, stop_epoch):
    """Runs under native command custody, serialized with ordinary task edits."""
    epoch = stop_epoch()
    async with controls.lock:
        for stage in ('check', 'commit'):
            permission = await authorize({**args, 'stage': stage})
            controls.require_idle()
            task = controls.tasks.record() or {}
            mutation = permission['mutation']
            if (stop_epoch() != epoch or task.get('id') != permission['taskId']
                    or task.get('revision') != mutation['expectedRevision']):
                raise ValueError('Native task or Stop changed before limit correction.')
        # perform has no await before its synchronous canonical save. The same
        # TaskController CAS, history, progress and idempotent receipt are used.
        return await controls.tasks.perform('task.update', mutation, preserve_controls=True)
