"""The app host's idle-only launcher restart and its run-bound admission hold."""
import asyncio
import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import time
import uuid

_WINDOWS = sys.platform == 'win32'
_POLL_SECONDS = 45

# The label that the bundled page gives its install button when the status
# has the action 'install'. The texts below name the button, so they change
# together with that label (tests/test_windows_launcher_follow.py reads it
# from the bundled page).
_BUTTON = 'Retry installation'

# These are server text, not a second update-health policy.
_TEXT = {
    'waiting': ('waiting', 'Amplifier needs one more restart',
        'One part of the update remains: the program that starts Amplifier still has version {X}. '
        'Amplifier waits until your work is finished. Then it restarts. This can take a few minutes.', None),
    'ready': ('available', 'One part of the update is ready',
        'The program that starts Amplifier still has version {X}. To update it, select ' + _BUTTON + '. '
        'Amplifier then restarts when your work is finished. This can take a few minutes.', 'install'),
    'restarting': ('working', 'Amplifier is restarting',
        'Amplifier is replacing the program that starts it. This can take a few minutes. During that time '
        'this page can show “Unable to reconnect to Amplifier”. The page keeps trying to reconnect.', None),
    'finishing': ('working', 'Amplifier is finishing the update',
        'Amplifier is checking the program that starts it. Amplifier can restart once more. '
        'New work waits until this check is done.', None),
    'failed': ('available', 'Amplifier is running. One part of the update did not finish.',
        'The program that starts Amplifier still has version {X}. Amplifier put the earlier program back.', 'install'),
    'failed-unchanged': ('available', 'Amplifier is running. One part of the update did not start.',
        'The program that starts Amplifier still has version {X}. It was not replaced.', 'install'),
    'repairing': ('working', 'Amplifier is repairing the program that starts it',
        'An update of that program did not finish. Amplifier is trying to put the earlier program back. '
        'Amplifier can restart once more. New work waits until this is done.', None),
    'needs-repair': ('action-required', 'Amplifier is running, but one part of the update did not finish',
        'The program that starts Amplifier still needs a repair. Amplifier tries the repair about once an hour, '
        'when no work runs. Until the repair is done, Amplifier does not check for updates.', None),
    'done': ('current', None, 'The program that starts Amplifier now has version {Y}.', None),
}


@dataclass
class _Follow:
    fence: bool = False
    facts: dict | None = None
    owner_tries: int = 0
    plan_errors: int = 0
    spec: object = None
    run: dict | None = None
    request: dict | None = None
    opened_here: bool = False
    held_at: float | None = None
    reopened: bool = False
    last_repair: float | None = None
    snapshot: tuple | None = None
    phase: str | None = None
    remove_pending: bool = False
    button_generation: str | None = None
    health: dict | None = None
    projection: dict | None = None
    shell_result: object = None
    announce: dict | None = None
    launch_pending: bool = False
    hold_pending: bool = False
    reopen_pending: bool = False
    remove_tries: int = 0
    remove_after: float = 0
    unsaved: bool = False


def _state(manager):
    return manager.service.state['updates']


def _saved(manager):
    return _state(manager).get('launcherRefresh') or {}


def _automatic(manager):
    settings = manager.service.state.get('settings', {}).get('updates', {})
    return settings.get('autoCheck', True) and settings.get('autoInstall', True)


def _allowed(manager, generation):
    return _automatic(manager) or (_saved(manager).get('requested') or {}).get('generation') == generation


def _open(request):
    return bool(request and request.get('status') == 'open')


def _target(target):
    return {key: target[key] for key in ('generation', 'revision', 'version')}


def _hold(manager, follow):
    from .update_drain import hold_for_restart
    request = follow.request
    hold_for_restart(manager.service, 'launcher-' + request['id'], request['target'])
    follow.held_at = time.time()


def _error(manager, error):
    value = {**_saved(manager), 'error': type(error).__name__}
    _state(manager)['launcherRefresh'] = value


def at_start(manager) -> None:
    """Fence a saved request, and hold only a refresh that has already started."""
    if not _WINDOWS:
        return
    follow = manager._launcher_follow = _Follow()
    try:
        from . import windows_launcher_refresh as refresh
        request = refresh.read_json(manager.home, refresh.REQUEST)
        journal = refresh.read_json(manager.home, refresh.JOURNAL)
        unfinished = journal and not journal.get('done')
        if not _open(request) and not unfinished:
            return
        from . import windows_service, windows_task_runtime as runtime, windows_app_recovery
        _, spec = windows_service._read(manager.home)
        run = runtime.read(spec) if spec else None
        if not run or run['pid'] != os.getpid():
            return
        follow.spec, follow.run = spec, refresh.run_identity(run)
        if _open(request) and request['registration'] != spec.registration_id:
            refresh.write_json(manager.home, refresh.REQUEST, {
                **request, 'status': 'closed', 'result': 'void', 'hold': None})
            request = None
        kind = refresh.journal_kind(manager.home, spec.registration_id)
        if kind == 'stale':
            with refresh.try_lock(manager.home) as locked:
                if locked:
                    refresh.set_stale_aside(manager.home)
        if kind == 'live' and not _open(request) and journal.get('request'):
            target = json.loads(base64.b64decode(journal['pointer']))
            request = {'schema': 1, 'id': journal['request'], 'registration': spec.registration_id,
                'launcher': journal['old'], 'target': _target(target),
                'status': 'open', 'result': None, 'hold': None, 'repair': True, 'at': time.time()}
            refresh.write_json(manager.home, refresh.REQUEST, request)
        if _open(request):
            follow.request, follow.fence, follow.phase = request, True, 'waiting'
            if refresh.started(manager.home, request):
                recovery = windows_app_recovery.read(spec) or {}
                if recovery.get('status') not in {'prepared', 'restoring'}:
                    _hold(manager, follow)
                    request = {**request, 'hold': follow.run}
                    follow.request, follow.phase, follow.hold_pending = request, 'finishing', True
                    refresh.write_json(manager.home, refresh.REQUEST, request)
                    follow.hold_pending = False
            _state(manager)['launcherRefresh'] = {**_saved(manager),
                'phase': follow.phase, 'app': request['target']}
        project(manager)
        manager.service._save_changes(globals={'updates'})
    except Exception as error:
        _error(manager, error)


def fenced(manager) -> bool:
    return bool(_WINDOWS and (follow := getattr(manager, '_launcher_follow', None)) and follow.fence)


def _facts(manager, follow):
    """Read the facts that one host run cannot change; Scheduler may not be listening yet."""
    from . import windows_service, windows_task_runtime as runtime
    from . import application_generations as generations, windows_launcher_refresh as refresh
    from .windows_launcher_files import differs
    from .app_updates import version_tuple
    spec = windows_service.update_owner(manager.home)
    if spec is None:
        return None
    prefix = Path(spec.python).parent.parent
    target = generations.active(manager.home)
    run = runtime.read(spec)
    if not run or run['pid'] != os.getpid():
        return None
    follow.spec, follow.run = spec, refresh.run_identity(run)
    if target is None:
        return {}
    old = refresh.launcher_facts(prefix)
    validated = json.loads((Path(target['folder']) / 'validated.json').read_text())
    app = {**_target(target), 'version': validated['version']}
    left, right = version_tuple(old['version']), version_tuple(app['version'])
    return {'launcher': {'version': old['version']}, 'identity': old['identity'],
        'app': app, 'python': str(Path(target['prefix']) / 'Scripts/pythonw.exe'),
        'root': refresh._consumer(manager.home, prefix), 'confirmed': None,
        'behind': bool(left and right and right > left and differs(prefix, target['prefix'])['differs'])}


def _confirmed(manager, follow):
    """Is the start of this app version confirmed? None: not decided yet.

    After an app update this same host writes the confirmation, and it does
    that after it listens. So a record that still says `prepared` for this
    app version is no answer: the next pass reads it again. Every other
    state stays as it is for as long as this host runs.
    """
    from . import windows_app_recovery
    app = follow.facts['app']
    recovery = windows_app_recovery.read(follow.spec) or {}
    restart = (recovery.get('restart') or {}).get('target') or {}
    if not all(restart.get(key) == app[key] for key in ('revision', 'generation')):
        return False
    if recovery.get('status') == 'prepared':
        return None
    if recovery.get('status') != 'acknowledged':
        return False
    if root := follow.facts['root']:
        try:
            from .windows_consumer_shell import settle_version, set_version
            settled = settle_version(root, home=manager.home)
            follow.shell_result = {'settled': settled,
                'result': set_version(root, app['version'], home=manager.home)}
        except Exception as error:
            follow.shell_result = {'errorType': type(error).__name__}
    return True


def _idle(manager):
    """No await or IO may separate this test from setting the fence."""
    state = _state(manager)
    return (not manager.closed and not manager.app_busy() and not manager.lock.locked()
        and not (manager.check_task and not manager.check_task.done())
        and not (manager.readiness_task and not manager.readiness_task.done())
        and state.get('phase') not in {'checking', 'staging', 'validating', 'staged', 'app-staged', 'activating'}
        and not any(state.get(key) for key in ('pendingApp', 'pendingRelease', 'pendingRestart', 'pendingRollback'))
        and state.get('pendingReplacement') is None
        and not (state.get('pendingSmartTools') and not manager.independent_tool_results(state))
        and (state.get('drain') or {}).get('phase') in {None, 'released'})


def _snapshot(manager):
    state = _state(manager)
    sequence = state.get('sequence') or {}
    return (state.get('phase'), sequence.get('stage'), sequence.get('nextStage'),
        state.get('lastCheck'), state.get('lastAttempt'), state.get('installedAt'))


async def _publish(manager, follow, **values):
    value = {**_saved(manager), **values, 'phase': follow.phase}
    if follow.facts:
        value.update(launcher=follow.facts['launcher'], app=follow.facts['app'])
    if follow.shell_result is not None:
        # For diagnostics: what this host run did with the Apps-list version.
        value['appsList'] = follow.shell_result
    if value != _saved(manager) or follow.unsaved:
        # publish() changes the state in memory before it saves. When the save
        # failed, the state already equals `value`: save it at the next call.
        follow.unsaved = True
        await manager.publish(launcherRefresh=value)
        follow.unsaved = False


def _attempt(manager, follow, identity, result=None, step=None):
    app = follow.facts['app']
    return {'id': identity, 'at': time.time(), 'generation': app['generation'],
        'automatic': (_saved(manager).get('requested') or {}).get('generation') != app['generation'],
        'result': result, 'step': step}


async def _begin(manager, follow):
    from . import windows_launcher_refresh as refresh
    identity = uuid.uuid4().hex
    attempt = _attempt(manager, follow, identity)
    step = None
    try:
        selected = await asyncio.to_thread(lambda: refresh.plan(refresh.TaskService(manager.home), manager.home))
        refused = selected.get('action') != 'refresh'
    except refresh.Refused:
        refused = True
    except Exception as error:
        # Nothing outside this process has started, and the fence is cleared.
        # The check is tried again after 5 minutes, twice. The third failure
        # in this host run is one counted attempt.
        _error(manager, error)
        follow.plan_errors += 1
        if follow.plan_errors < 3:
            follow.fence, follow.phase = False, None
            await _publish(manager, follow, retryAfter=time.time() + 300)
            return
        refused, step = True, 'plan'
    if refused:
        follow.fence, follow.phase = False, 'failed-unchanged'
        await _publish(manager, follow, attempt={**attempt, 'result': 'refused', 'step': step}, requested=None)
        return
    follow.phase, follow.announce = 'waiting', attempt
    await _announce(manager, follow)


async def _announce(manager, follow):
    """Save the attempt before any effect outside this process. A save that failed is done again here."""
    attempt = follow.announce
    await _publish(manager, follow, attempt=attempt, requested=None)
    request = {'schema': 1, 'id': attempt['id'], 'registration': follow.spec.registration_id,
        'launcher': follow.facts['identity'], 'target': follow.facts['app'],
        'status': 'open', 'result': None, 'hold': None, 'repair': False, 'at': attempt['at']}
    follow.request, follow.opened_here, follow.launch_pending, follow.announce = request, True, True, None
    await _launch(manager, follow)


async def _launch(manager, follow):
    from . import windows_launcher_refresh as refresh, windows_launcher_task as task
    refresh.write_json(manager.home, refresh.REQUEST, follow.request)
    try:
        await asyncio.to_thread(task.ensure, manager.home, follow.request['id'],
            follow.spec.registration_id, follow.facts['python'])
        await asyncio.to_thread(task.start, manager.home)
    except Exception:
        follow.launch_pending = False
        follow.request = {**follow.request, 'status': 'closed', 'result': 'refused'}
        refresh.write_json(manager.home, refresh.REQUEST, follow.request)
        await _finish(manager, follow, 'refused', 'task')
    else:
        follow.launch_pending = False


def _stopping(follow):
    from . import windows_task_runtime as runtime
    return runtime.stop_requested(follow.spec, follow.run) or runtime.stop_requested(
        follow.spec, follow.run, name=runtime.RECOVERY_STOP)


async def _release(manager):
    from . import update_drain
    drain = _state(manager).get('drain') or {}
    if drain.get('launcher') and update_drain.held(manager.service):
        await update_drain.finish(manager, 'withdrawn')


async def _finish(manager, follow, result, step=None):
    from . import windows_launcher_refresh as refresh
    with refresh.try_lock(manager.home) as locked:
        if not locked or _stopping(follow):
            return False
        if refresh.journal_kind(manager.home, follow.spec.registration_id) != 'none':
            return False
        request = {**follow.request, 'status': 'closed', 'result': result, 'hold': None}
        refresh.write_json(manager.home, refresh.REQUEST, request)
        await _release(manager)
    # Do not retain the refresh lock during publication or Scheduler calls.
    follow.request, follow.fence, follow.hold_pending = None, False, False
    follow.reopened, follow.held_at = False, None
    follow.remove_pending, follow.remove_tries, follow.remove_after = True, 0, 0
    attempt = _saved(manager).get('attempt') or {}
    if attempt.get('id') != request['id']:
        attempt = {'id': request['id'], 'at': request['at'],
            'generation': request['target']['generation'], 'automatic': True}
    attempt = {**attempt, 'result': result, 'step': step}
    follow.phase = _result_phase(result)
    await _publish(manager, follow, attempt=attempt, **(
        {'retryAfter': time.time() + 300} if result == 'withdrawn' else {}))
    await _remove(manager, follow)
    return True


def _result_phase(result):
    return {'refreshed': 'done', 'rolled-back': 'failed', 'withdrawn': None}.get(result, 'failed-unchanged')


async def _remove(manager, follow):
    from .windows_launcher_task import remove_task
    if follow.remove_pending and time.time() >= follow.remove_after:
        # A task that Windows keeps: ten passes ask for it, then one pass an hour.
        follow.remove_tries += 1
        follow.remove_pending = not await asyncio.to_thread(remove_task, manager.home)
        follow.remove_after = time.time() + 3600 if follow.remove_tries >= 10 else 0


async def _go(manager, follow, *, repair=False):
    from . import windows_launcher_refresh as refresh
    async with manager.service.lock:
        attempt = _saved(manager).get('attempt') or {}
        button = follow.opened_here and attempt.get('id') == follow.request['id'] and attempt.get('automatic') is False
        if not _idle(manager) or not (repair or button or _allowed(manager, follow.request['target']['generation'])):
            allowed = False
        else:
            allowed = True
            _hold(manager, follow)
            follow.phase, follow.reopened = 'restarting', False
            # publish() takes service.lock itself.
    if not allowed:
        if repair:
            return
        follow.request = {**follow.request, 'status': 'closed', 'result': 'withdrawn'}
        refresh.write_json(manager.home, refresh.REQUEST, follow.request)
        await _finish(manager, follow, 'withdrawn')
        return
    follow.hold_pending = True
    await _commit_hold(manager, follow)


async def _commit_hold(manager, follow):
    from . import windows_launcher_refresh as refresh
    await _publish(manager, follow)
    request = {**follow.request, 'hold': follow.run}
    refresh.write_json(manager.home, refresh.REQUEST, request)
    follow.request, follow.hold_pending = request, False


def _begun(manager, follow):
    """Did a refresh change anything for this request? Rule R repairs only such a refresh."""
    from . import windows_launcher_refresh as refresh
    return (refresh.journal_kind(manager.home, follow.spec.registration_id) != 'none'
        or refresh.started(manager.home, follow.request))


async def _reopen(manager, follow):
    from . import windows_launcher_refresh as refresh
    with refresh.try_lock(manager.home) as locked:
        if not locked or _stopping(follow):
            return
        request = {**follow.request, 'hold': None}
        refresh.write_json(manager.home, refresh.REQUEST, request)
        follow.request, follow.reopen_pending = request, True
        await _release(manager)
        follow.reopened, follow.phase = True, 'needs-repair'
        follow.last_repair, follow.reopen_pending = time.time(), False


async def _repair(manager, follow, interval):
    from . import windows_launcher_task as task
    now = time.time()
    if follow.last_repair is not None and now - follow.last_repair < interval:
        return
    follow.last_repair = now
    if not await asyncio.to_thread(task.running, manager.home):
        python = follow.facts['python'] if follow.facts else str(Path(sys.executable).with_name('pythonw.exe'))
        await asyncio.to_thread(task.ensure, manager.home, follow.request['id'], follow.spec.registration_id, python)
        await asyncio.to_thread(task.start, manager.home)


async def _poll_once(manager, follow):
    from . import windows_launcher_refresh as refresh
    record = refresh.read_json(manager.home, refresh.RECORD) or {}
    if record.get('request') != follow.request['id']:
        record = {}
    kind = refresh.journal_kind(manager.home, follow.spec.registration_id)
    request = refresh.read_json(manager.home, refresh.REQUEST)
    if not request or request.get('id') != follow.request['id']:
        raise ValueError('The launcher request changed.')
    follow.request = request
    if follow.request['registration'] != follow.spec.registration_id:
        return await _finish(manager, follow, 'void')
    if not _open(follow.request):
        return await _finish(manager, follow, follow.request.get('result'),
            'task' if follow.request.get('result') == 'refused' else None)
    started = kind == 'live' or record.get('state') in {'stopped', 'waiting'}
    mine = follow.request.get('hold') == follow.run
    if record.get('state') == 'settled' and kind == 'none':
        return await _finish(manager, follow, record.get('result'), record.get('step'))
    if follow.reopened:
        follow.phase = 'needs-repair'
        # A repair go needs no setting, so it is only for a refresh that started.
        if started and record.get('state') == 'ready' and record.get('run') == follow.run:
            await _go(manager, follow, repair=True)
        elif _idle(manager):
            await _repair(manager, follow, 3600)
    elif mine:
        if not started:
            return await _finish(manager, follow, 'not-started')
        follow.phase = 'repairing' if record.get('state') == 'waiting' else (
            'restarting' if follow.opened_here else 'finishing')
        if follow.held_at is not None and time.time() - follow.held_at >= 600:
            await _reopen(manager, follow)
        else:
            await _repair(manager, follow, 60)
    elif started:
        # A prior bound may have been saved before this host started.
        follow.reopened, follow.phase = True, 'needs-repair'
    elif not follow.opened_here:
        return await _finish(manager, follow, 'withdrawn')
    elif record.get('state') == 'ready' and record.get('run') == follow.run:
        await _go(manager, follow, repair=follow.request.get('repair', False))
    elif time.time() - follow.request['at'] >= 300:
        return await _finish(manager, follow, 'not-ready')
    if follow.request is None:
        return True
    await _publish(manager, follow)
    return False


async def _consider(manager, follow):
    if follow.remove_pending:
        await _remove(manager, follow)
    if follow.hold_pending:
        if follow.held_at is None or time.time() - follow.held_at < 600:
            await _commit_hold(manager, follow)
            return
        # The bound, also for a hold that could not be saved: admission does
        # not stay closed for it. Rule R decides how it ends. A refresh that
        # did not start changed nothing: the request is closed, as one attempt
        # (_finish needs the lock and no stop record, and tries again when it
        # has neither). A refresh that started stays an open request that
        # needs a repair.
        if not _begun(manager, follow):
            await _finish(manager, follow, 'not-started')
            return
        follow.hold_pending, follow.reopen_pending = False, True
    if follow.reopen_pending:
        await _reopen(manager, follow)
        await _publish(manager, follow)
        return
    if follow.facts is None and follow.owner_tries < 10:
        follow.owner_tries += 1
        follow.facts = await asyncio.to_thread(_facts, manager, follow)
    if follow.facts and follow.facts['confirmed'] is None:
        try:
            follow.facts['confirmed'] = await asyncio.to_thread(_confirmed, manager, follow)
        except Exception as error:
            # Not decided. An open request must still get its steps in this pass.
            follow.unsaved = follow.unsaved or _saved(manager).get('error') != type(error).__name__
            _error(manager, error)
    if follow.announce:
        await _announce(manager, follow)
        return
    if follow.launch_pending:
        await _launch(manager, follow)
        return
    if follow.request:
        deadline = time.monotonic() + _POLL_SECONDS
        while not manager.closed:
            if await _poll_once(manager, follow):
                return
            if time.monotonic() >= deadline:
                manager.wakeup.set()
                return
            await asyncio.sleep(1)
        return
    if not follow.facts:
        return
    facts, saved = follow.facts, _saved(manager)
    generation = facts['app']['generation']
    attempt = saved.get('attempt') or {}
    counted = attempt.get('generation') == generation and attempt.get('result') != 'withdrawn'
    offer = facts['behind'] and facts['confirmed'] is True
    if offer:
        from . import windows_launcher_refresh as refresh
        kind = refresh.journal_kind(manager.home, follow.spec.registration_id)
        if kind == 'stale':
            with refresh.try_lock(manager.home) as locked:
                if locked:
                    refresh.set_stale_aside(manager.home)
                    kind = 'none'
        offer = kind == 'none'
    follow.phase = _result_phase(attempt.get('result')) if counted else None
    requested = (saved.get('requested') or {}).get('generation') == generation
    allowed = _allowed(manager, generation)
    before, follow.snapshot = follow.snapshot, _snapshot(manager)
    if offer and allowed and (not counted or requested) and time.time() >= (saved.get('retryAfter') or 0):
        follow.phase = 'waiting'
        if _idle(manager) and before == follow.snapshot:
            follow.fence = True
            await _begin(manager, follow)
            return
    elif offer and not allowed and not counted:
        follow.phase = 'ready'
    await _publish(manager, follow)


async def consider(manager) -> None:
    if not _WINDOWS or not (follow := getattr(manager, '_launcher_follow', None)):
        return
    try:
        await _consider(manager, follow)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        _error(manager, error)
        try:
            await manager.publish(launcherRefresh=_saved(manager))
        except Exception:
            pass


def project(manager) -> None:
    """Overlay launcher text only at the specified update-health precedence."""
    if not _WINDOWS or not (follow := getattr(manager, '_launcher_follow', None)):
        return
    try:
        state, saved = _state(manager), _saved(manager)
        current = state.get('currentStatus') or {}
        base = follow.health if current is follow.projection else current
        follow.health, follow.button_generation = base, None
        phase = follow.phase
        if phase not in _TEXT:
            state['currentStatus'] = base
            return
        X = (saved.get('launcher') or {}).get('version', '?')
        Y = (saved.get('app') or {}).get('version', '?')
        status, title, detail, action = _TEXT[phase]
        detail = detail.format(X=X, Y=Y)
        if phase in {'failed', 'failed-unchanged'}:
            detail += (f' Amplifier tries again after the next app update. To try now, select {_BUTTON}.'
                if _automatic(manager) else f' To try again, select {_BUTTON}.')
        row = dict(base)
        if phase == 'done':
            if base.get('status') == 'current':
                row['detail'] = (base.get('detail', '') + ' ' + detail).strip()
        elif follow.request or base.get('status') in {'current', 'unchecked'}:
            row = {'status': status, 'title': title, 'detail': detail,
                'actionRequired': phase == 'needs-repair', 'action': action, 'component': None}
            if action == 'install':
                follow.button_generation = (saved.get('app') or {}).get('generation')
        elif phase in {'waiting', 'ready', 'failed', 'failed-unchanged'}:
            suffix = f'The program that starts Amplifier still has version {X}.'
            if phase in {'failed', 'failed-unchanged'}:
                suffix += ' Its update did not finish.'
            row['detail'] = (base.get('detail', '') + ' ' + suffix).strip()
        state['currentStatus'] = follow.projection = row
    except Exception:
        pass


async def request_by_button(manager) -> bool:
    if not _WINDOWS or not (follow := getattr(manager, '_launcher_follow', None)):
        return False
    generation = follow.button_generation
    if (not generation or follow.request or _state(manager).get('pendingApp')
            or (_saved(manager).get('requested') or {}).get('generation') == generation):
        return False
    follow.button_generation = None
    await _publish(manager, follow, requested={'generation': generation})
    manager.wakeup.set()
    return True