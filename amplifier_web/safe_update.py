"""User-requested admission hold across a staged app and its included components."""
import asyncio
import uuid

from .errors import AppError
from .update_restart import target


def start(manager, args, origin):
    """Called under the action lock, in the same commit as the command receipt."""
    if origin not in {'ui', 'user'}:
        raise AppError('Start a safe update from Updates.', 403)
    state = manager.service.state['updates']
    if getattr(manager, '_manual_handoff_commit_unknown', False) or manager.lock.locked():
        raise AppError('An update operation is already settling. Try again shortly.', 409)
    if not args or args != target(manager):
        raise AppError('The prepared update changed. Open Updates to see its current state.', 409)
    pending = state['pendingApp']
    if pending.get('featureSelection') or not pending.get('generation'):
        raise AppError('This action requires a prepared application update.', 409)
    prior = state.get('drain') or {}
    selected = {k: args[k] for k in ('revision', 'attemptId', 'generation')}
    if prior.get('phase') == 'waiting' and prior.get('target') == selected:
        drain = prior
    elif prior.get('phase') in {None, 'released'}:
        from .update_drain import pending_waiters
        drain = {'phase': 'waiting', 'workers': pending_waiters(prior), 'messages': [],
                 'target': selected, 'attemptId': args['attemptId'], 'hostInstanceId': args['hostInstanceId']}
    else:
        raise AppError('Another update is still settling.', 409)
    if drain.get('safeUpdate'):
        return
    drain['safeUpdate'] = True
    state['drain'] = drain
    # The explicit whole-update gesture also covers the included-component tier.
    state['applicationIntent'] = None
    pending.pop('updateIntent', None)
    state['sequence'] = {**(state.get('sequence') or {}), 'install': True}
    manager.wakeup.set()


def waiting(state):
    drain = state.get('drain') or {}
    return bool(drain.get('safeUpdate') and drain.get('phase') == 'waiting')


async def enroll(manager):
    """Enroll each eligible native owner once. Ordinary and external turns settle normally."""
    service = manager.service
    state = service.state['updates']
    if not waiting(state):
        return
    drain = state['drain']
    staged = target(manager)
    if not staged or any(staged[k] != v for k, v in drain['target'].items()):
        return
    from .update_drain import wait_for_update
    for sid, worker in list(getattr(service.runtime, 'workers', {}).items()):
        if sid in drain['workers'] or not worker.get('runtimeOwnerId'):
            continue
        session = service._session(sid, hydrate=False)
        if session.get('status') != 'working' or session.get('externalAgent') or session.get('configurationBusy'):
            continue
        task = session.get('task') or service.coordination.task(sid)
        if not task or task.get('status') != 'active' or service.assignments.blocked(session):
            continue
        generation = session.get('collaborationGeneration') or {}
        gen = generation.get('id')
        if not gen or generation.get('terminal'):
            continue
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, f"safe-update:{drain['attemptId']}:{sid}:{gen}:{task['id']}:{task['revision']}"))
        try:
            result = await wait_for_update(service, {'sessionId': sid, 'expectedTaskId': task['id'],
                'expectedRevision': task['revision'], 'target': staged}, identity, origin='user')
            boundary = result['result'].get('boundary') or {}
            if boundary.get('waitingFor') == ['unsupported-native-boundary']:
                # This specific response precedes installation of any hook.
                # app_busy() will still wait for natural completion.
                async with service.lock:
                    row = drain['workers'].get(sid)
                    if row and row['commandId'] == identity:
                        row['naturalFinish'] = True
                        row['suppressed'] = 'This agent will finish its current turn normally.'
                        service._publish_changes(globals={'updates'})
        except AppError as exc:
            if exc.status != 409:
                raise
            # A finished/revised owner will be re-evaluated from current state.


def adoption_target(drain):
    return drain.get('componentRestart') or drain.get('target', {})


def component_restart(drain, release):
    return bool(drain.get('safeUpdate') and drain.get('phase') == 'replacing'
                and drain.get('requiredHost') and not drain.get('componentRestart')
                and release.get('safeUpdateAttempt') == drain['attemptId']
                and release.get('revision') == drain['target']['revision'])


def require_host_graph(drain, graph):
    installed = {row['name']: row for row in graph}
    for name, expected in drain.get('requiredHost', {}).items():
        row = installed.get(name, {})
        if any(row.get(k) != v for k, v in expected.items()):
            raise ValueError('The prepared host does not contain the requested component revision.')


async def check_host(manager):
    """A long wait can leave a qualified app with older host dependencies.
    Resolve these once on the healthy replacement; keep the same closed gate
    across an additional same-release restart when its interpreter needs one.
    """
    from . import app_updates
    from .update_drain import verified_target
    state = manager.service.state['updates']
    drain = state.get('drain') or {}
    if not drain.get('safeUpdate') or drain.get('phase') != 'replacing':
        return True
    if not verified_target(manager, drain):
        return False
    if 'requiredHost' not in drain:
        changes = await app_updates.components.updates(app_updates.process, app_updates.git_environment())
        drain['requiredHost'] = {
            row['name']: ({'url': row['url'], 'revision': row['latest'],
                            **({'subdirectory': row['subdirectory']} if row.get('subdirectory') else {})} if row.get('url')
                           else {'version': row['latest']}) for row in changes}
        await manager.publish()
    if drain['requiredHost'] and not drain.get('componentRestart'):
        app = {**state['application'], 'latest': manager.running_identity['version'],
               'revision': drain['target']['revision'], 'status': 'update',
               'safeUpdateAttempt': drain['attemptId']}
        await manager.publish(application=app, detail='Preparing the required host components.')
        await app_updates.stage(manager)
        manager.wakeup.set()
        return False
    if drain['requiredHost']:
        require_host_graph(drain, await asyncio.to_thread(app_updates.components.installed_graph))
    return True


def included_result(manager, rows, *, promoted=False):
    """Compare all included members to the qualified selection, ignoring optional rows."""
    from .update_drain import verified_target
    from .updates import active_release
    state = manager.service.state['updates']
    drain = state.get('drain') or {}
    if (not drain.get('safeUpdate') or drain.get('phase') != 'replacing'
            or 'requiredHost' not in drain or not verified_target(manager, drain)):
        return
    required = {}
    ready = True
    for group in rows:
        if group.get('updateTier') != 'included':
            continue
        for row in group.get('members') or [group]:
            current = group.get('current') if group.get('status') == 'current' else row.get('current')
            status = group['status'] if group.get('status') == 'current' else row.get('status')
            revision = row.get('latest') or current
            required[row['id']] = revision
            ready &= (status in {'current', 'local', 'pinned'} and current == revision
                      and (bool(current) or status == 'local') and (promoted or not row.get('missing')))
    if promoted:
        # A partial success must not silently drop a previously required member.
        ready &= drain.get('requiredSources') == required
    else:
        drain['requiredSources'] = required
    drain['componentsReady'] = bool(ready)
    if ready:
        drain['componentGeneration'] = active_release(manager.home).get('current')
        manager.wakeup.set()


def ready_to_release(manager, drain):
    from .updates import active_release
    return (not manager.lock.locked() and not manager.service.state['updates'].get('pendingRelease')
            and drain.get('componentsReady') and 'componentGeneration' in drain
            and drain['componentGeneration'] == active_release(manager.home).get('current'))
