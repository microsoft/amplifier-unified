"""Manual update requests wake the owner without relaxing activation gates."""
import asyncio
import time

import pytest

from amplifier_web import app_updates, updates
from amplifier_web.service import AppService


class Runtime:
    async def close(self):
        pass


@pytest.fixture
async def manager(tmp_path, monkeypatch):
    service = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    manager = service.update_manager = updates.UpdateManager(service)
    service.state['settings']['updates'].update(autoCheck=False, autoInstall=True)
    service.state['updates'].update(phase='checked', available=0, lastCheck=time.time(),
                                   sequence={'stage': 'complete', 'install': False})
    async def application():
        return {'id': 'application', 'kind': 'app', 'status': 'current'}
    async def inventory(**kwargs):
        return [{'id': 'fixture', 'kind': 'smart tool', 'updateTier': 'other', 'label': 'Fixture',
                 'installationId': 'old', 'url': 'https://example.invalid/tool', 'ref': 'main',
                 'current': 'a' * 40, 'status': 'not_checked', 'eligible': True}]
    async def process(*args, **kwargs):
        return 'b' * 40 + ' refs/heads/main'
    monkeypatch.setattr(app_updates, 'check', application)
    monkeypatch.setattr(manager, 'inventory_sources', inventory)
    monkeypatch.setattr(updates, 'process', process)
    yield manager
    await service.close()


async def sleeping_owner(manager, monkeypatch):
    sleeping = asyncio.Event()
    original = manager.wait_for_work
    async def wait(delay):
        if delay == 3:
            return
        sleeping.set()
        await original(delay)
    monkeypatch.setattr(manager, 'wait_for_work', wait)
    manager.task = asyncio.create_task(manager.loop())
    await asyncio.wait_for(sleeping.wait(), 1)


async def test_manual_check_starts_auto_install_without_timer_tick(manager, monkeypatch):
    installed = asyncio.Event()
    async def install():
        assert manager.service.state['updates']['phase'] == 'available'
        assert manager.inventory[0]['latest'] == 'b' * 40
        manager.service.state['updates'].update(phase='checked', available=0)
        installed.set()
    monkeypatch.setattr(manager, 'install', install)
    await sleeping_owner(manager, monkeypatch)
    await manager.command('check')
    # The owner really waits for 60 seconds; only the command signal can get
    # here before this short test bound. No shortened production poll interval.
    await asyncio.wait_for(installed.wait(), 1)


async def test_check_does_not_enable_install_when_user_disabled_it(manager, monkeypatch):
    manager.service.state['settings']['updates']['autoInstall'] = False
    async def install():
        pytest.fail('Check alone must respect the automatic installation preference')
    monkeypatch.setattr(manager, 'install', install)
    await sleeping_owner(manager, monkeypatch)
    await manager.command('check')
    await asyncio.sleep(.02)
    assert manager.service.state['updates']['phase'] == 'available'
    assert not manager.service.state['updates']['sequence']['install']


async def test_install_click_during_check_is_retained_and_uses_new_inventory(manager, monkeypatch):
    manager.service.state['settings']['updates']['autoInstall'] = False
    entered, finish, installed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = manager.inventory_sources
    async def inventory(**kwargs):
        entered.set()
        await finish.wait()
        return await original(**kwargs)
    async def install():
        assert manager.inventory[0]['latest'] == 'b' * 40
        assert not manager.lock.locked()
        installed.set()
    monkeypatch.setattr(manager, 'inventory_sources', inventory)
    monkeypatch.setattr(manager, 'install', install)
    check = asyncio.create_task(manager.command('check'))
    await entered.wait()
    request = asyncio.create_task(manager.command('install'))
    await asyncio.sleep(0)
    assert not request.done() and not installed.is_set()
    finish.set()
    await asyncio.wait_for(asyncio.gather(check, request), 1)
    assert installed.is_set()


async def test_completed_stage_continues_without_a_poll_delay(manager, monkeypatch):
    calls = []
    complete = asyncio.Event()
    async def tick():
        calls.append(1)
        if len(calls) == 1:
            manager.service.state['updates'].update(phase='installed', sequence={'nextStage': 'other'})
        else:
            manager.service.state['updates'].update(phase='checked', sequence={'stage': 'complete'})
            complete.set()
    monkeypatch.setattr(manager, 'tick', tick)
    manager.wakeup.set()
    manager.task = asyncio.create_task(manager.loop())
    await asyncio.wait_for(complete.wait(), 1)
    await asyncio.sleep(.02)
    assert len(calls) == 2


async def test_busy_continuation_does_not_spin(manager, monkeypatch):
    calls = []
    async def tick():
        calls.append(1)
    monkeypatch.setattr(manager, 'tick', tick)
    manager.service.state['updates'].update(phase='installed', sequence={'nextStage': 'other'})
    async with manager.lock:
        await sleeping_owner(manager, monkeypatch)
        await asyncio.sleep(.02)
        assert len(calls) == 1


async def test_idle_publication_wakes_staged_update_without_interrupting_work(manager, monkeypatch):
    service = manager.service
    await service.dispatch('session.create', {})
    session = service._session()
    session['status'] = 'working'
    service.state['updates'].update(phase='staged', pendingSmartTools=[{'previous': 'old', 'target': 'new'}])
    installed = asyncio.Event()
    async def activate():
        if manager.busy():
            return
        service.state['updates'].update(phase='installed', pendingSmartTools=[])
        installed.set()
    monkeypatch.setattr(manager, 'activateSmartTools', activate)
    await sleeping_owner(manager, monkeypatch)
    assert not installed.is_set()
    session['status'] = 'idle'
    service._publish()
    await asyncio.wait_for(installed.wait(), 1)


async def test_failed_check_does_not_install_stale_inventory(manager, monkeypatch):
    entered, finish = asyncio.Event(), asyncio.Event()
    async def inventory(**kwargs):
        entered.set()
        await finish.wait()
        raise ValueError('check failed')
    async def install():
        pytest.fail('The click must not apply stale inventory after a failed check')
    monkeypatch.setattr(manager, 'inventory_sources', inventory)
    monkeypatch.setattr(manager, 'install', install)
    check = asyncio.create_task(manager.command('check'))
    await entered.wait()
    request = asyncio.create_task(manager.command('install'))
    await asyncio.sleep(0)
    finish.set()
    await asyncio.wait_for(asyncio.gather(check, request), 1)
    assert manager.service.state['updates']['phase'] == 'error'


async def test_parallel_refs_join_but_post_install_stage_refreshes_remote_result(manager, monkeypatch):
    requests = []
    original = manager.inventory_sources
    async def inventory(**kwargs):
        rows = await original(**kwargs)
        return rows + [{**rows[0], 'id': 'second', 'installationId': 'second'}]
    async def process(*args, **kwargs):
        requests.append(args)
        return 'b' * 40 + ' refs/heads/main'
    monkeypatch.setattr(manager, 'inventory_sources', inventory)
    monkeypatch.setattr(updates, 'process', process)
    await manager.check()
    assert len(requests) == 1
    async def current(**kwargs):
        return [{**row, 'current': 'b' * 40} for row in await inventory()]
    monkeypatch.setattr(manager, 'inventory_sources', current)
    manager.service.state['updates'].update(phase='installed', sequence={'nextStage': 'other', 'install': True})
    await manager.tick()
    # A stage boundary refreshes refs so newly published required components
    # cannot be missed. Duplicate refs still join within each fresh check.
    assert len(requests) == 2
    assert manager.service.state['updates']['sequence']['stage'] == 'complete'
    assert manager.service.state['updates']['checkTiming'].get('cached', 0) == 0
    await manager.check()
    assert len(requests) == 3  # Another manual check still fetches fresh results.


@pytest.mark.parametrize('ready', [False, True])
async def test_native_settlement_wakes_idle_update_without_timer_or_publication_loop(manager, monkeypatch, ready):
    from amplifier_web import update_drain
    service = manager.service
    await service.dispatch('session.create', {})
    session = service._session(); session['status'] = 'working'
    target = {'attemptId': 'a' * 32, 'revision': 'b' * 40, 'generation': 'c' * 32}
    state = service.state['updates']; state.update(phase='app-staged', pendingApp=target)
    runtime = service.runtime
    observations, settlement = [], [False]
    observed, installed = asyncio.Event(), asyncio.Event()
    runtime.has_pending_operations = lambda: not settlement[0]
    async def boundary(*args, **kwargs):
        pytest.fail('A staged update must not freeze or yield running workers')
    runtime.handoff_boundary = boundary
    async def activate():
        async with manager.lock:
            observations.append(1)
            if len(observations) > 1: observed.set()
            if await update_drain.prepare(manager, target):
                state.update(phase='installed', pendingApp=None)
                installed.set()
    monkeypatch.setattr(manager, 'app', activate)
    manager._storage_last_pass = time.time()
    await sleeping_owner(manager, monkeypatch)
    assert len(observations) == 1 and not manager.wakeup.is_set()
    settlement[0] = ready
    session['status'] = 'ready'
    await service.on_runtime_event('runtime.warmth', {'sessionId': session['id'], 'status': 'warm'})
    if ready:
        await asyncio.wait_for(observed.wait(), 1)
        await asyncio.wait_for(installed.wait(), 1)
        assert len(observations) == 2
    else:
        assert not installed.is_set()
        for _ in range(3): await manager.publish(detail='Update ready; work continues')
        await asyncio.sleep(.03)
        assert len(observations) == 1 and not manager.wakeup.is_set()
    assert not state.get('drain')


@pytest.mark.parametrize('ready', [False, True])
async def test_settlement_consumed_during_command_lock_rewakes_real_app(manager, monkeypatch, ready):
    from amplifier_web import update_drain
    service = manager.service
    await service.dispatch('session.create', {})
    session = service._session(); session['status'] = 'working'
    target = {'attemptId': 'a' * 32, 'revision': 'b' * 40, 'generation': 'c' * 32}
    state = service.state['updates']
    state.update(phase='app-staged', pendingApp=target, pendingAppCheckAt=time.time())
    observed, installed, entered, release, sleeping = (asyncio.Event() for _ in range(5))
    observations, settlement = [], [False]
    service.runtime.has_pending_operations = lambda: not settlement[0]
    async def boundary(*args, **kwargs):
        pytest.fail('A staged update must not freeze or yield running workers')
    service.runtime.handoff_boundary = boundary
    # Keep the real app(), command(), tick() and loop() owners. Only the
    # external package effect and the concurrent command's work are controlled.
    async def activate(owner):
        assert owner is manager and not manager.lock.locked()
        observations.append(1)
        if len(observations) > 1: observed.set()
        if await update_drain.prepare(owner, target):
            state.update(phase='installed', pendingApp=None)
            installed.set()
    async def command_work():
        async with manager.lock:
            entered.set()
            await release.wait()
    original_wait = manager.wait_for_work
    async def wait(delay):
        if delay == 3: return
        sleeping.set()
        await original_wait(delay)
    monkeypatch.setattr(app_updates, 'activate', activate)
    monkeypatch.setattr(manager, 'check', command_work)
    monkeypatch.setattr(manager, 'wait_for_work', wait)
    manager._storage_last_pass = time.time()
    manager.task = asyncio.create_task(manager.loop())
    await asyncio.wait_for(sleeping.wait(), 1)
    assert len(observations) == 1
    command = asyncio.create_task(manager.command('check'))
    await asyncio.wait_for(entered.wait(), 1)
    try:
        settlement[0] = ready; session['status'] = 'ready'
        await service.on_runtime_event('runtime.warmth', {'sessionId': session['id'], 'status': 'warm'})
        # Settling while the command owns the update lock cannot activate in
        # parallel. The command's finally must retain the idle observation.
        await asyncio.sleep(.02)
        assert manager.lock.locked() and not manager.wakeup.is_set()
        assert len(observations) == 1
    finally:
        release.set()
        await asyncio.wait_for(command, 1)
    await asyncio.wait_for(observed.wait(), 1)
    if ready:
        await asyncio.wait_for(installed.wait(), 1)
    else:
        assert not installed.is_set()
        await manager.publish(detail='Update ready; work continues')
        await asyncio.sleep(.03)
        assert len(observations) == 2 and not manager.wakeup.is_set()
    assert not state.get('drain')


async def test_drain_retention_protects_only_exact_unobserved_owner(manager):
    from types import SimpleNamespace
    from amplifier_web import update_drain
    service=manager.service
    await service.dispatch('session.create', {})
    sid=service._session()['id']
    runtime=service.runtime
    runtime.workers={sid: {'runtimeOwnerId':'current'}}
    runtime.retention=SimpleNamespace(protected=lambda identity: identity=='voice')
    update_drain.bind(service,runtime)
    service.state['updates']['drain']={'phase':'draining','workers':{sid:{'owner':'current','ready':False}}}
    assert runtime.retention.protected(sid) and runtime.retention.protected('voice')
    runtime.workers[sid]['runtimeOwnerId']='replacement'
    assert not runtime.retention.protected(sid)
    runtime.workers[sid]['runtimeOwnerId']='current'
    service.state['updates']['drain']['workers'][sid]['ready']=True
    assert not runtime.retention.protected(sid)
    service.state['updates']['drain']['workers'][sid]['ready']=False
    for phase in ('replacing','releasing','released'):
        service.state['updates']['drain']['phase']=phase
        assert not runtime.retention.protected(sid)
        assert runtime.retention.protected('voice')


async def test_explicit_stop_waits_for_pending_runtime_work_then_wakes_idle_update(manager, monkeypatch):
    from amplifier_web import update_drain
    service=manager.service
    await service.dispatch('session.create', {})
    session=service._session();session['status']='working'
    sid=session['id'];target={'attemptId':'a'*32,'revision':'b'*40,'generation':'c'*32}
    state=service.state['updates'];state.update(phase='app-staged',pendingApp=target)
    pending=[True]
    service.runtime.has_pending_operations=lambda: pending[0]
    async def boundary(*args,**kwargs):
        pytest.fail('Passive update readiness must not move or yield a worker')
    service.runtime.handoff_boundary=boundary
    installed=asyncio.Event()
    async def activate():
        async with manager.lock:
            if await update_drain.prepare(manager,target):
                state.update(phase='installed',pendingApp=None);installed.set()
    monkeypatch.setattr(manager,'app',activate)
    manager._storage_last_pass=time.time()
    await sleeping_owner(manager,monkeypatch)
    assert not installed.is_set()
    session['interruptionRevision']=1
    await service.on_runtime_event('runtime.status',{'sessionId':sid,'status':'stopped'})
    await asyncio.sleep(.02)
    assert not installed.is_set() and not manager.wakeup.is_set()
    pending[0]=False
    await service.on_runtime_event('runtime.warmth',{'sessionId':sid,'status':'cold'})
    await asyncio.wait_for(installed.wait(),1)
    assert session['status']=='stopped' and session['interruptionRevision']==1
    assert not state.get('drain')
