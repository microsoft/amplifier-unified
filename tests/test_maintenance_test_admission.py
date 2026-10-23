"""Maintenance fences real queue/operation journals; controlled backend only."""
import asyncio
from contextlib import closing
import threading
from types import SimpleNamespace

import pytest

from amplifier_web import task_environments as env
from amplifier_web import test_environment_setup as setup
from amplifier_web import registered_test_runners as runners
from amplifier_web.maintenance_auxiliary import snapshot
from amplifier_web.service import AppError
from amplifier_web.update_blockers import blockers
from amplifier_web.test_queue import TestQueue
from test_test_queue import app, queue, backend, submit, drive, record
from test_task_environments import scope, start, act
from test_coordination_generation import until


def hold(app, value=True):
    app.maintenance.value['held'] = value
    app.state['hostMaintenance']['held'] = value


async def test_waiting_intent_and_duplicate_survive_hold_then_execute_once(app, queue, backend):
    await submit(app)
    hold(app)
    assert (await submit(app))['duplicate']
    with pytest.raises(AppError) as rejected:
        await submit(app, 'new')
    assert rejected.value.code == 'maintenance_paused'
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'queued' and not backend
    assert snapshot(app)['settled']
    hold(app, False)
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'passed'
    assert [a for a, _ in backend].count('execute') == 1


async def test_hold_after_capacity_wait_preserves_queued_intent(app, queue, backend, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def capacity(*_):
        entered.set()
        assert release.wait(5)
        return {'available': True, 'counts': {'active': 0}}
    monkeypatch.setattr(env, 'capacity', capacity)
    await submit(app)
    tick = asyncio.create_task(queue.tick())
    try:
        await until(entered.is_set)
        hold(app)
        release.set()
        await tick
        assert record(queue, app)['job']['status'] == 'queued' and not backend and not queue.tasks
    finally:
        release.set()
        await asyncio.gather(tick, return_exceptions=True)


async def test_hold_after_local_allocation_retires_only_unused_owner(app, queue, backend, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = env.prepare
    def prepare(*args):
        row = original(*args)
        entered.set()
        assert release.wait(5)
        return row
    monkeypatch.setattr(env, 'prepare', prepare)
    await submit(app)
    await queue.tick()
    try:
        await until(entered.is_set)
        hold(app)
        release.set()
        await asyncio.gather(*queue.tasks.values())
        assert record(queue, app)['job']['status'] == 'queued' and not backend
        hold(app, False)
        await drive(queue)
        assert record(queue, app)['job']['status'] == 'passed'
        assert [a for a, _ in backend].count('start') == 1
    finally:
        release.set()


async def test_hold_during_source_preparation_declines_launch_and_requeues(app, queue, backend, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = env.invoke
    def invoke(config, home, row, action, args):
        result = original(config, home, row, action, args)
        if action == 'prepare':
            entered.set()
            assert release.wait(5)
        return result
    monkeypatch.setattr(env, 'invoke', invoke)
    await submit(app)
    await queue.tick()
    try:
        await until(entered.is_set)
        hold(app)
        release.set()
        await asyncio.gather(*queue.tasks.values())
        assert record(queue, app)['job']['status'] == 'queued'
        assert [a for a, _ in backend] == ['prepare']
        hold(app, False)
        await drive(queue)
        assert record(queue, app)['job']['status'] == 'passed'
        assert [a for a, _ in backend].count('execute') == 1
    finally:
        release.set()


async def test_hold_after_launch_cleans_before_requeue_without_executing(app, queue, backend, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = env.invoke
    def invoke(config, home, row, action, args):
        result = original(config, home, row, action, args)
        if action == 'start':
            entered.set()
            assert release.wait(5)
        return result
    monkeypatch.setattr(env, 'invoke', invoke)
    await submit(app)
    await queue.tick()
    try:
        await until(entered.is_set)
        hold(app)
        assert not snapshot(app)['settled']
        release.set()
        await asyncio.gather(*queue.tasks.values())
        assert record(queue, app)['job']['status'] == 'queued'
        assert [a for a, _ in backend] == ['prepare', 'start', 'stop', 'remove']
        hold(app, False)
        await drive(queue)
        assert record(queue, app)['job']['status'] == 'passed'
        assert [a for a, _ in backend].count('execute') == 1
    finally:
        release.set()


async def defer_after_launch(app, queue, backend, monkeypatch, *, independent):
    original = queue.effect
    async def pause_after_launch(receipt, config, action, **kwargs):
        result = await original(receipt, config, action, **kwargs)
        if action == 'start':
            hold(app)
        return result
    with monkeypatch.context() as patch:
        patch.setattr(queue, 'effect', pause_after_launch)
        await submit(app, 'first', independent=independent)
        await drive(queue)
    saved = record(queue, app, 'first')
    assert saved['job']['status'] == 'queued' and saved['job']['maintenanceAttempt'] == 1
    assert [action for action, _ in backend] == ['prepare', 'start', 'stop', 'remove']
    hold(app, False)


async def test_resumed_independent_owner_counts_once_for_chat_capacity(app, queue, backend, monkeypatch):
    current = (await app.dispatch('testing.execution.read', {}))['result']
    await app.dispatch('testing.execution.save', {'expectedRevision': current['revision'],
        'queue': {}, 'profiles': {}, 'concurrency': {'maxEnvironmentsPerChat': 2}})
    await defer_after_launch(app, queue, backend, monkeypatch, independent=True)
    entered, release = [], threading.Event()
    original = env.invoke
    def executing(config, home, row, action, args):
        if action == 'execute':
            entered.append(row['id'])
            assert release.wait(10)
        return original(config, home, row, action, args)
    monkeypatch.setattr(env, 'invoke', executing)
    try:
        await queue.tick()
        await until(lambda: len(entered) == 1)
        await submit(app, 'second', independent=True)
        await queue.tick()
        assert record(queue, app, 'second')['requestId'] in queue.tasks
        await until(lambda: len(entered) == 2)
        assert len(set(entered)) == 2
    finally:
        release.set()
        await asyncio.gather(*queue.tasks.values())
    assert all(record(queue, app, name)['job']['status'] == 'passed' for name in ('first', 'second'))
    assert [action for action, _ in backend].count('execute') == 2


async def test_resumed_unknown_ordered_owner_blocks_independent_work_until_cleanup(app, queue, backend, monkeypatch):
    await defer_after_launch(app, queue, backend, monkeypatch, independent=False)
    original, unknown = env.invoke, True
    def uncertain(config, home, row, action, args):
        if unknown and action == 'execute':
            backend.append((action, args))
            raise TimeoutError('lost acknowledgement after maintenance')
        if unknown and action == 'job_read':
            return {'ok': True, 'job': {'id': args['requestId'], 'state': 'unknown', 'terminal': False}}
        return original(config, home, row, action, args)
    monkeypatch.setattr(env, 'invoke', uncertain)
    await drive(queue)
    first = record(queue, app, 'first')
    assert first['job']['status'] == 'unknown'
    recovered = app.test_queue = TestQueue(app)
    recovered.start = lambda: None
    await submit(app, 'second', independent=True)
    await drive(recovered)
    second = record(recovered, app, 'second')['job']
    assert second['status'] == 'queued' and 'earlier check' in second['detail']
    assert [action for action, _ in backend].count('execute') == 1
    unknown = False
    await recovered.recover(first['requestId'])
    await drive(recovered)
    assert record(recovered, app, 'second')['job']['status'] == 'passed'
    assert [action for action, _ in backend].count('execute') == 2


async def test_direct_new_effects_block_but_duplicate_and_cleanup_remain(app, backend):
    first = await start(app)
    hold(app)
    assert (await start(app))['request'] == first['request']
    with pytest.raises(AppError) as rejected:
        await act(app, 'execute', first['resource'], 'held-execute', command='must not run')
    assert rejected.value.code == 'maintenance_paused'
    stopped = await act(app, 'stop', first['resource'], 'stop')
    removed = await act(app, 'remove', stopped['resource'], 'remove')
    assert removed['request']['state'] == 'completed'
    assert 'execute' not in [a for a, _ in backend]


@pytest.mark.parametrize('lost_at', ['deferred-receipt', 'removed-owner', 'second-check'])
async def test_deferred_check_recovery_uses_exact_attempt_receipts(app, queue, backend, monkeypatch, lost_at):
    original_effect = queue.effect
    async def boundary(receipt, config, action, **kwargs):
        result = await original_effect(receipt, config, action, **kwargs)
        if action == 'start':
            hold(app)
        if action == ('execute' if lost_at == 'deferred-receipt' else 'remove'):
            raise asyncio.CancelledError
        return result
    monkeypatch.setattr(queue, 'effect', boundary)
    await submit(app)
    await queue.tick()
    await asyncio.gather(*queue.tasks.values(), return_exceptions=True)
    recovered = TestQueue(app)
    app.test_queue = recovered
    await drive(recovered)
    assert record(recovered, app)['job']['status'] == 'queued'
    assert not any(action == 'execute' for action, _ in backend)
    assert len([action for action, _ in backend if action == 'remove']) == 1
    if lost_at == 'second-check':
        async def lose_cleanup(*args, **kwargs):
            raise asyncio.CancelledError
        monkeypatch.setattr(recovered, 'cleanup', lose_cleanup)
    hold(app, False)
    await recovered.tick()
    await asyncio.gather(*recovered.tasks.values(), return_exceptions=True)
    if lost_at == 'second-check':
        recovered = TestQueue(app)
        app.test_queue = recovered
        await drive(recovered)
    receipt = record(recovered, app)
    assert receipt['job']['status'] == 'passed'
    assert len([action for action, _ in backend if action == 'execute']) == 1
    result = await app.dispatch('workspace.tests.result', {**scope(app), 'requestId': 'check'})
    assert result['result']['operation']['state'] == 'completed'
    assert ':maintenance:1' in result['result']['operation']['requestId']


def test_auxiliary_unknown_owner_blocks_without_remote_probe_and_retirement_settles(tmp_path):
    app = SimpleNamespace(data_dir=tmp_path, state={})
    assert snapshot(app)['settled']
    with closing(env.storage.connect(tmp_path)) as db, db:
        row = {'id': 'owned', 'workspaceId': 'project', 'sessionId': 'chat', 'status': 'ready',
               'kind': env.KIND, 'backendAdmitted': True, 'environmentState': 'unknown'}
        env.storage.save(db, row)
    result = snapshot(app)
    assert not result['settled'] and result['items'][0]['kind'] == 'test-environments'
    assert not result['remoteCancellationConfirmed']
    assert 'not suspended or stopped' in result['items'][0]['detail']
    with closing(env.storage.connect(tmp_path)) as db, db:
        env.storage.save(db, {**row, 'status': 'retired'})
    assert snapshot(app)['settled']


def test_auxiliary_setup_retained_unknown_blocks_without_directory_census(tmp_path):
    app = SimpleNamespace(data_dir=tmp_path, state={})
    folder = setup.root(app)
    setup.write_json(folder / 'latest.json', {'id': 'a' * 32})
    record = {'state': 'outcome_unknown'}
    setup.write_json(folder / ('a' * 32 + '.json'), record)
    assert [r['kind'] for r in snapshot(app)['items']] == ['test-setup']
    setup.write_json(folder / ('a' * 32 + '.json'), {**record, 'cleanup': {'state': 'completed'}})
    assert snapshot(app)['settled']


def test_legacy_configuration_stays_blocked_with_truthful_label():
    app = SimpleNamespace(state={'sessions': [{'id': 'chat', 'configurationBusy': True,
        'pendingConfiguration': {'recoveryRequired': True, 'error': 'interrupted'}}]})
    result = blockers(app)
    assert result[0]['kind'] == 'configuration'
    assert result[0]['label'] == 'Earlier conversation settings need inspection; no change is being replayed'


async def test_setup_fresh_admission_holds_before_installation(tmp_path):
    from unittest.mock import AsyncMock
    app = SimpleNamespace(data_dir=tmp_path, state={'hostMaintenance': {'held': True}},
                          smart_tools=SimpleNamespace(install=AsyncMock()))
    with pytest.raises(AppError) as rejected:
        await setup.request(app, {'workspaceId': 'project', 'sessionId': 'chat',
            'requestId': 'setup', 'backend': 'docker'}, {'sessionId': 'chat'})
    assert rejected.value.code == 'maintenance_paused'
    app.smart_tools.install.assert_not_awaited()
    assert not (setup.root(app) / 'latest.json').exists()


async def test_setup_rechecks_admission_after_authority_wait(tmp_path):
    from unittest.mock import AsyncMock
    app = SimpleNamespace(data_dir=tmp_path, state={'hostMaintenance': {'held': False}},
                          smart_tools=SimpleNamespace(install=AsyncMock()))
    async def guard():
        app.state['hostMaintenance']['held'] = True
    with pytest.raises(AppError) as rejected:
        await setup.request(app, {'workspaceId': 'project', 'sessionId': 'chat',
            'requestId': 'setup', 'backend': 'docker'}, {'sessionId': 'chat'}, guard)
    assert rejected.value.code == 'maintenance_paused'
    app.smart_tools.install.assert_not_awaited()
    assert not (setup.root(app) / 'latest.json').exists()


async def test_setup_existing_receipt_is_readable_without_completing_while_held(tmp_path, monkeypatch):
    import hashlib
    import json
    from unittest.mock import AsyncMock
    app = SimpleNamespace(data_dir=tmp_path, state={'hostMaintenance': {'held': True}})
    args = {'workspaceId': 'project', 'sessionId': 'chat', 'requestId': 'setup', 'backend': 'docker'}
    actor = {'sessionId': 'chat'}
    identity = hashlib.sha256(json.dumps(['chat', 'setup'], separators=(',', ':')).encode()).hexdigest()[:32]
    setup.write_json(setup.root(app) / (identity + '.json'), {'id': identity, 'intent': args,
        'actor': actor, 'state': 'failed', 'candidateConfiguration': {'enabled': True},
        'proof': {'probeRemoved': True}})
    monkeypatch.setattr(setup, 'complete_retained', AsyncMock(side_effect=AssertionError('completion crossed hold')))
    monkeypatch.setattr(setup, 'readiness', AsyncMock(return_value={'status': 'disabled'}))
    result = await setup.request(app, args, actor)
    assert result['duplicate'] and result['operation']['state'] == 'failed'
    setup.complete_retained.assert_not_awaited()


def test_auxiliary_uses_canonical_configuration_categories_without_queued_task_inference():
    pending = SimpleNamespace(done=lambda: False)
    management = SimpleNamespace(pending_config_tasks={'queued': pending}, maintenance_blockers=lambda: [])
    app = SimpleNamespace(state={}, management=management)
    assert snapshot(app)['settled']
    management.maintenance_blockers = lambda: [{'kind': 'configuration-unresolved',
        'label': 'Earlier conversation settings need inspection', 'sessionId': 'chat'}]
    assert snapshot(app)['settled']
    assert blockers(app)[0]['kind'] == 'configuration-unresolved'
    management.maintenance_blockers = lambda: [{'kind': 'configuration-active',
        'label': 'Conversation settings are being applied', 'sessionId': 'chat'}]
    assert not snapshot(app)['settled']
    assert snapshot(app)['items'][0]['status'] == 'waiting'


def registration_app(tmp_path):
    return SimpleNamespace(data_dir=tmp_path, lock=asyncio.Lock(), state={'hostMaintenance': {'held': False}},
        configuration_authority=SimpleNamespace(check=lambda _: None),
        test_queue=SimpleNamespace(tasks={}, backoff={}, wakeup=asyncio.Event(), start=lambda: None))


@pytest.mark.parametrize('action', ['register', 'refresh'])
@pytest.mark.parametrize('boundary', ['initial', 'prepared-package'])
async def test_registration_install_rechecks_maintenance(tmp_path, monkeypatch, action, boundary):
    app = registration_app(tmp_path)
    old = {'id': 'a'*64, 'name': 'Test', 'enabled': True, 'connection': 'test-target',
           'machineId': 'b'*32, 'profiles': ['linux-python'], 'configPath': ''}
    runners.publish(app, {'version': 1, 'runners': [old]})
    def package():
        app.state['hostMaintenance']['held'] = True
        return {}
    monkeypatch.setattr(runners, 'bundle', package)
    monkeypatch.setattr(runners, 'ssh', lambda *a, **kw: pytest.fail('Installation crossed maintenance hold'))
    app.state['hostMaintenance']['held'] = boundary == 'initial'
    with pytest.raises(AppError) as rejected:
        await runners.dispatch(app, 'testing.runners.' + action, {**old,
            'expectedRevision': runners.snapshot(app)['revision']}, None)
    assert rejected.value.code == 'maintenance_paused'
    assert runners.store(app)['runners'] == [old]


async def test_registration_caller_cancellation_keeps_active_setup_accounting(tmp_path, monkeypatch):
    app = registration_app(tmp_path)
    entered, release = threading.Event(), threading.Event()
    def register(connection, name, path, admit):
        admit()
        entered.set()
        assert release.wait(5)
        return {}
    monkeypatch.setattr(runners, 'register', register)
    task = asyncio.create_task(runners.dispatch(app, 'testing.runners.register', {
        'connection': 'test-target', 'name': 'Test', 'expectedRevision': runners.snapshot(app)['revision']}, None))
    try:
        await until(entered.is_set)
        app.state['hostMaintenance']['held'] = True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not snapshot(app)['settled'] and app._test_setup_tasks
        release.set()
        await asyncio.gather(*app._test_setup_tasks.values())
        await asyncio.sleep(0)
        assert snapshot(app)['settled']
    finally:
        release.set()
        await asyncio.gather(task, *getattr(app, '_test_setup_tasks', {}).values(), return_exceptions=True)
