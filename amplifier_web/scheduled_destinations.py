"""One fresh conversation per occurrence, with no automatic creation/input replay."""
import asyncio

from .session_creation import template
from .managed_chats import is_managed


def creation_guard(schedules, source, identity, destination):
    """Runs under the same app lock as shared session.create."""
    run = schedules.store.run(source, identity)
    record = schedules.store.get(source, run['scheduleId'])
    if not schedules.store.owns(schedules.clock()) or run['owner'] != schedules.store.owner:
        raise ValueError('Scheduler ownership changed before conversation creation')
    if run['phase'] != 'creating' or run.get('destinationSessionId') != destination:
        raise ValueError('This occurrence has no matching new-task reservation')
    if record['status'] != 'active' or record['revision'] != run['scheduleRevision']:
        raise ValueError('The schedule changed before conversation creation')
    reason = schedules.dependency_reason(schedules.app._session(source), None, record)
    if reason: raise ValueError(reason)


async def create_destination(schedules, current, run):
    app, sid, identity = schedules.app, current['sessionId'], run['destinationSessionId']
    config = current['newTaskConfiguration']
    location = {'location': {'kind': 'managed'}} if is_managed(config) else {'workspace': config['workspace']}
    try:
        await app.dispatch('session.create', {
            'id': identity, 'select': False, 'title': current['newTaskTitle'],
            **location, 'bundle': config['bundle'],
            'inheritConfiguration': {'sessionId': sid, 'configurationHash': config['configurationHash'], 'scheduledRunId': run['id']},
        }, origin='scheduler', command_id=run['id'] + ':create', include_state=False)
        async with app.lock:
            destination = app._session(identity)
            destination['scheduledOrigin'] = {'sessionId': sid, 'scheduleId': current['id'], 'runId': run['id']}
            run = schedules.store.transition(sid, run['id'], ['creating'], 'creating', schedules.clock(),
                destinationConfigurationHash=template(app, destination)[1]['configurationHash'],
                destinationInterruptionRevision=0, destinationExecutionRevision=0, creationConfirmed=True)
            schedules.changed()
        result = await app.dispatch('task.create', {'sessionId': identity, 'expectedRevision': 0,
            'objective': current['prompt'], 'maxTurns': current['newTaskMaxTurns']},
            origin='scheduler', command_id=run['id'] + ':task', include_state=False)
        task = result['result']['task']
        async with app.lock:
            run = schedules.store.transition(sid, run['id'], ['creating'], 'creating', schedules.clock(), taskId=task['id'], taskRevision=task['revision'])
            schedules.changed()
        return run, destination, task
    except BaseException as exc:
        # Creation or task configuration can have committed before a connection
        # failed. Save the reserved identity, never guess that a retry is safe.
        async with app.lock:
            detail = 'New-task preparation outcome requires inspection; no automatic retry: ' + str(exc)[:500]
            saved = schedules.store.transition(sid, run['id'], ['creating'], 'unknown', schedules.clock(), detail=detail)
            schedules.store.review(sid, current['id'], detail, schedules.clock())
            schedules.decide_notification(app._session(sid), saved)
            schedules.changed()
        if isinstance(exc, asyncio.CancelledError): raise
        return None
