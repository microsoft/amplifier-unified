"""Standing schedules retain real callers and fence delayed effects."""
import asyncio
import copy
import json

import pytest

from amplifier_web.service import AppError, AppService
from test_full_child_configuration import app, mounted, dispatch as child_dispatch
from test_full_feedback import call, mode
from test_schedules import fixture as same_fixture, attach, config, finish
from test_schedule_new_tasks import fixture as new_fixture, ConfiguredRuntime


async def setup(tmp_path, monkeypatch, destination):
    app, runtime, now, sid = await (new_fixture if destination == 'new_task' else same_fixture)(tmp_path, monkeypatch)
    source = app._session(sid)
    source['collaborationGeneration'] = {'id': 'system-assigned-generation',
        'inputIds': ['system-assignment'], 'terminal': False, 'interruptionRevision': 0}
    await mode(app, source)
    return app, runtime, now, source


async def preview(app, source, now, destination='same_task', **extra):
    args = {'sessionId': source['id'], **config(now, 'task'), 'destination': destination, **extra}
    reviewed = (await app.dispatch('schedule.preview', args))['result']
    return {**args, 'expectedRevision': 0, 'previewHash': reviewed['previewHash']}


async def invoke(app, source, action, args, identity, origin='agent'):
    return (await call(app, source, action, args, origin=origin, command=identity))['result']


@pytest.mark.parametrize('origin', ['agent', 'system'])
@pytest.mark.parametrize('destination', ['same_task', 'new_task'])
async def test_full_create_update_resume_and_exact_receipts(tmp_path, monkeypatch, origin, destination):
    app, runtime, now, source = await setup(tmp_path, monkeypatch, destination)
    try:
        assert not source['messages'] and not app.clients.records
        before = copy.deepcopy(app.state['view'])
        args = await preview(app, source, now[0], destination)
        created = await invoke(app, source, 'schedule.create', args, 'full-create', origin)
        record = created['schedule']
        actor = record['authorization']['authority']
        assert actor['sessionId'] == source['id'] and actor['origin'] == origin
        assert actor['nativeSessionId'] == source['id']
        assert actor.get('generationId') == ('system-assigned-generation' if origin == 'agent' else None)
        assert actor.get('inputIds') == (['system-assignment'] if origin == 'agent' else None)
        assert 'messageId' not in record['authorization']
        changed = await preview(app, source, now[0], destination, prompt='Inspect the revised report.')
        changed.update(id=record['id'], expectedRevision=record['revision'])
        updated = (await invoke(app, source, 'schedule.update', changed, 'full-update', origin))['schedule']
        paused = (await invoke(app, source, 'schedule.pause', {'sessionId': source['id'],
            'id': record['id'], 'expectedRevision': updated['revision']}, 'full-pause', origin))['schedule']
        resume = {'sessionId': source['id'], 'id': record['id'], 'expectedRevision': paused['revision'],
            'previewHash': changed['previewHash']}
        resumed = (await invoke(app, source, 'schedule.resume', resume, 'full-resume', origin))['schedule']
        assert resumed['status'] == 'active'
        assert not source['approvals'] and app.state['view'] == before
        await mode(app, source, 'ask')
        source.update(interruptionRevision=1, status='stopped')
        source['collaborationGeneration']['terminal'] = True
        # Completed commands remain readable without another task/runtime read.
        async def forbidden(*args, **kwargs):
            raise AssertionError('Exact retry prepared a runtime')
        monkeypatch.setattr(app.schedules, 'task', forbidden)
        assert await invoke(app, source, 'schedule.create', args, 'full-create', origin) == {**created, 'duplicate': True}
        assert (await invoke(app, source, 'schedule.resume', resume, 'full-resume', origin))['schedule'] == resumed
        with pytest.raises(AppError, match='different schedule contents'):
            await invoke(app, source, 'schedule.create', {**args, 'prompt': 'Different'}, 'full-create', origin)
        peer = app._new_session({'title': 'Other owner'})
        app.state['sessions'].append(peer)
        with pytest.raises(AppError, match='different schedule contents'):
            await invoke(app, peer, 'schedule.create', {**args, 'sessionId': peer['id']}, 'full-create', 'system')
    finally:
        await app.close()


@pytest.mark.parametrize('boundary', ['stop', 'revoke', 'off-on', 'placement', 'native', 'turn-finished'])
async def test_full_capture_precedes_prepare_and_is_checked_at_save(tmp_path, monkeypatch, boundary):
    app, runtime, now, source = await setup(tmp_path, monkeypatch, 'same_task')
    try:
        args = await preview(app, source, now[0])
        entered, release = asyncio.Event(), asyncio.Event()
        original = app.schedules.task
        async def held(*args, **kwargs):
            result = await original(*args, **kwargs)
            entered.set()
            await release.wait()
            return result
        monkeypatch.setattr(app.schedules, 'task', held)
        pending = asyncio.create_task(invoke(app, source, 'schedule.create', args, 'held-create'))
        await asyncio.wait_for(entered.wait(), 5)
        if boundary in {'revoke', 'off-on'}:
            await mode(app, source, 'ask')
            if boundary == 'off-on': await mode(app, source)
        elif boundary == 'stop': source['interruptionRevision'] = 1
        elif boundary == 'placement': source['executionRevision'] = 1
        elif boundary == 'native': source['runtimeSessionId'] = 'replacement'
        else: source['collaborationGeneration']['terminal'] = True
        release.set()
        if boundary == 'turn-finished':
            result = await pending
            assert result['schedule']['authorization']['authority']['generationId'] == 'system-assigned-generation'
        else:
            with pytest.raises(AppError, match='authority changed|preview changed'):
                await pending
            assert not app.schedules.store.list(source['id'])
    finally:
        await app.close()


@pytest.mark.parametrize('destination', ['same_task', 'new_task'])
@pytest.mark.parametrize('boundary', ['revoke', 'off-on', 'stop'])
async def test_full_due_final_admission_keeps_original_capture(tmp_path, monkeypatch, destination, boundary):
    app, runtime, now, source = await setup(tmp_path, monkeypatch, destination)
    try:
        args = await preview(app, source, now[0], destination)
        record = (await invoke(app, source, 'schedule.create', args, 'full-create'))['schedule']
        original = runtime.scheduled_input
        async def held(sid, args, guard):
            if boundary == 'stop': source['interruptionRevision'] = 1
            else:
                await mode(app, source, 'ask')
                if boundary == 'off-on': await mode(app, source)
            return await original(sid, args, guard)
        runtime.scheduled_input = held
        source['collaborationGeneration']['terminal'] = True
        now[0] += 61
        await app.schedules.tick()
        assert not runtime.inputs
        run = app.schedules.store.runs(source['id'])[0]
        assert run['phase'] == 'skipped', run
        assert run['authorizationCommandId'] == record['authorization']['commandId'] == 'full-create'
        assert app.schedules.store.get(source['id'], record['id'])['status'] == 'needs_review'
    finally:
        await app.close()


@pytest.mark.parametrize('boundary', ['conversation', 'task', 'task_transport'])
@pytest.mark.parametrize('change', ['revoke', 'stop'])
async def test_full_new_task_checks_each_effect_boundary(tmp_path, monkeypatch, boundary, change):
    app, runtime, now, source = await setup(tmp_path, monkeypatch, 'new_task')
    try:
        args = await preview(app, source, now[0], 'new_task')
        await invoke(app, source, 'schedule.create', args, 'full-create')
        original = app.dispatch
        async def held(action, *args, **kwargs):
            if kwargs.get('origin') == 'scheduler' and action == {'conversation': 'session.create', 'task': 'task.create'}.get(boundary):
                if change == 'stop': source['interruptionRevision'] = 1
                else: await mode(app, source, 'ask')
            return await original(action, *args, **kwargs)
        app.dispatch = held
        control = runtime.control
        async def held_control(sid, action, args, **kwargs):
            if boundary == 'task_transport' and action == 'task.create':
                if change == 'stop': source['interruptionRevision'] = 1
                else: await mode(app, source, 'ask')
            return await control(sid, action, args, **kwargs)
        runtime.control = held_control
        source['collaborationGeneration']['terminal'] = True
        now[0] += 61
        await app.schedules.tick()
        run = app.schedules.store.runs(source['id'])[0]
        assert run['phase'] == 'skipped', run
        assert bool(run.get('creationConfirmed')) == (boundary != 'conversation')
        assert len(app.state['sessions']) == (1 if boundary == 'conversation' else 2)
        assert not runtime.inputs
        assert not run.get('taskId')
    finally:
        await app.close()


async def test_full_recurring_work_survives_generation_and_host_end(tmp_path, monkeypatch):
    app, runtime, now, source = await setup(tmp_path, monkeypatch, 'new_task')
    args = await preview(app, source, now[0], 'new_task')
    record = (await invoke(app, source, 'schedule.create', args, 'full-create'))['schedule']
    source['collaborationGeneration']['terminal'] = True
    source['status'] = 'idle'
    app._publish_changes(sessions={source['id']})
    now[0] += 61
    await app.schedules.tick()
    run = app.schedules.store.runs(source['id'])[0]
    assert run['phase'] == 'accepted', run
    await finish(app, run['destinationSessionId'], run)
    home, sid = app.data_dir, source['id']
    await app.close()
    runtime = ConfiguredRuntime()
    restored = AppService(home, runtime, workspace=tmp_path)
    attach(restored, runtime, now)
    try:
        now[0] += 61
        await restored.schedules.tick()
        second = restored.schedules.store.runs(sid)[0]
        assert second['phase'] == 'accepted', second
        assert second['id'] != run['id']
        assert second['destinationSessionId'] != run['destinationSessionId']
        assert second['authorizationCommandId'] == run['authorizationCommandId'] == record['authorization']['commandId']
        assert restored._session(second['destinationSessionId'])['scheduledOrigin']['sessionId'] == sid
        assert restored.access_mode.full(restored._session(second['destinationSessionId']))
        assert len(runtime.inputs) == 1
    finally:
        await restored.close()


@pytest.mark.parametrize('origin', ['agent', 'system'])
async def test_full_unknown_reconciliation_keeps_original_run_and_exact_receipt(tmp_path, monkeypatch, origin):
    app, runtime, now, source = await setup(tmp_path, monkeypatch, 'same_task')
    try:
        args = await preview(app, source, now[0])
        record = (await invoke(app, source, 'schedule.create', args, 'full-create'))['schedule']
        runtime.failure = RuntimeError('Input receipt lost')
        now[0] += 61
        await app.schedules.tick()
        run = app.schedules.store.runs(source['id'])[0]
        assert run['phase'] == 'unknown' and len(runtime.inputs) == 1
        # A fresh current actor reports a finding; never label it a human source.
        source['status'] = 'idle'
        source['collaborationGeneration'] = {'id': 'reconcile-generation',
            'inputIds': ['reconcile-input'], 'terminal': False, 'interruptionRevision': 0}
        args = {'sessionId': source['id'], 'runId': run['id'], 'expectedRevision': run['revision'],
            'resolution': 'abandoned', 'evidence': 'Inspected the retained effect and chose no replay.'}
        result = await invoke(app, source, 'schedule.reconcile', args, 'full-reconcile', origin)
        saved = result['run']
        assert saved['authorizationCommandId'] == record['authorization']['commandId']
        assert json.loads(app.schedules.store.db.execute(
            'SELECT result FROM schedule_commands WHERE id=?', (saved['authorizationCommandId'],)
        ).fetchone()[0])['schedule']['authorization'] == record['authorization']
        assert saved['reconciliation']['provenance']['origin'] == origin
        assert 'messageCreatedAt' not in saved['reconciliation']['provenance']
        assert app.schedules.store.get(source['id'], record['id'])['status'] == 'needs_review'
        await mode(app, source, 'ask')
        source.update(status='stopped', interruptionRevision=1)
        duplicate = await invoke(app, source, 'schedule.reconcile', args, 'full-reconcile', origin)
        assert duplicate == {**result, 'duplicate': True}
        now[0] += 61
        await app.schedules.tick()
        assert len(runtime.inputs) == 1
    finally:
        await app.close()


@pytest.mark.parametrize('kind', ['finite', 'persistent', 'nested'])
async def test_mounted_full_child_retains_actor_without_human(mounted, kind):
    from amplifier_web.host.children import _PERSISTENT

    h = mounted
    await mode(h.app, h.source)
    # The native root has a separate saved identity in this fixture.
    original = h.app.data_dir / 'sessions' / h.source['id']
    native = h.app.data_dir / 'sessions' / 'native-root'
    native.mkdir(exist_ok=True)
    for name in ('effective-configuration.json', 'control-state.json'):
        (native / name).write_bytes((original / name).read_bytes())
    actors = []
    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        actor = coordinator.session_id
        actors.append(actor)
        args = {**config(h.app.schedules.clock(), 'task'), 'destination': 'new_task'}
        control = tools['app_control']
        async def invoke(action, values):
            result = await child_dispatch(control, action, values, action + actor)
            assert result.success, result.error
            return result.output['result']
        reviewed = await invoke('schedule.preview', args)
        created = await invoke('schedule.create', {**args, 'expectedRevision': 0, 'previewHash': reviewed['previewHash']})
        authority = created['schedule']['authorization']['authority']
        assert authority['runtimeSessionId'] == actor != 'native-root'
        assert authority['sessionId'] == h.source['id'] and authority['nativeSessionId'] == 'native-root'
        assert authority['generationId'] == 'generation-1' and authority['inputIds'] == ['assigned-input']
        assert not h.source['messages'] and not h.source['approvals'] and not h.app.clients.records
        record = created['schedule']
        recovery = {'id': record['id'], 'expectedRevision': record['revision']}
        repair_preview = await invoke('schedule.repairPreview', recovery)
        repaired = await invoke('schedule.repairResume', {**recovery, 'previewHash': repair_preview['previewHash']})
        assert repaired['schedule']['authorization']['authority']['runtimeSessionId'] == actor
        assert repaired['schedule']['authorization']['recoveryInitiation'] == 'current_native'
        if kind == 'finite':
            current = repaired['schedule']
            recovery['expectedRevision'] = current['revision']
            next_preview = await invoke('schedule.repairPreview', recovery)
            h.source.update(status='stopped', interruptionRevision=1)
            denied = await child_dispatch(control, 'schedule.repairResume', {**recovery,
                'previewHash': next_preview['previewHash'], 'acknowledgeStop': True}, 'stopped-child')
            assert not denied.success
            assert h.app.schedules.store.get(h.source['id'], record['id']) == current
        if kind == 'nested' and actor == 'child':
            await h.registry.spawn('worker', 'schedule another task', h.registry.sessions['child'], sub_session_id='grandchild')
        return 'scheduled'
    h.install_execute(execute)
    token = _PERSISTENT.set(kind == 'persistent')
    try:
        await h.registry.spawn('worker', 'schedule future work', h.parent, sub_session_id='child')
        assert actors == (['child', 'grandchild'] if kind == 'nested' else ['child'])
        h.approval_prompt.assert_not_awaited()
    finally:
        _PERSISTENT.reset(token)
