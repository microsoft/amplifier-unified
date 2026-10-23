"""Explicit forced restart keeps qualification, shutdown and durable fences."""
import asyncio
import copy
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import app_updates, application_generations
from amplifier_web.service import AppService, AppError
from amplifier_web.state_records import load as load_saved_state
from amplifier_web.update_restart import target, RestartRejected
from amplifier_web.updates import UpdateManager, work_paused
from test_service import Runtime


@pytest.fixture
async def staged(tmp_path, monkeypatch, request):
    legacy = None
    if getattr(request, 'param', None) == 'legacy-configuration':
        seed = AppService(tmp_path, Runtime(), workspace=tmp_path)
        await seed.dispatch('session.create', {'title': 'Retained legacy configuration'})
        original = seed._session()
        original.update(status='error', configurationBusy=True,
                        pendingConfiguration={'phase': 'error', 'error': 'Historical update failure'})
        from amplifier_web.management import Management
        seed.management = Management(seed)
        payload = seed.management.queued_path(original['id'])
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_bytes(b'{"config":{"fixture":"private-original-payload"}}\n')
        legacy = {'id': original['id'], 'payload': payload, 'bytes': payload.read_bytes(),
                  'mtime': payload.stat().st_mtime_ns}
        seed._save()
        await seed.close()
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    if legacy:
        from amplifier_web.management import Management
        service.management = Management(service)
    await service.dispatch('session.create', {'title': 'Preserved conversation'})
    await service.on_runtime_event('assistant.message', {'sessionId': service._session()['id'], 'text': 'Saved answer'})
    manager = UpdateManager(service)
    service.update_manager = manager
    host = [{'name': 'amplifier-core', 'version': '1.6.1'}]
    graph = [*host, {'name': 'amplifier-unified', 'version': '99.0.0', 'url': app_updates.SOURCE, 'revision': 'a'*40}]
    pending = {'revision': 'a'*40, 'generation': 'b'*32, 'attemptId': 'qualified-attempt', 'latest': 'v99.0.0'}
    folder = manager.directory/'applications'/pending['revision']/pending['generation']
    python = folder/'tools/amplifier-unified/bin/python'
    python.parent.mkdir(parents=True)
    python.touch()
    receipt = {**pending, 'version': '99.0.0', 'extras': [], 'componentGraph': graph,
               'componentDigest': app_updates.components.digest(graph), 'hostGraph': host,
               'hostDigest': app_updates.components.digest(host)}
    (folder/'validated.json').write_text(json.dumps(receipt))
    (folder/'components.txt').write_text(app_updates.components.requirements(graph))
    monkeypatch.setattr(app_updates, 'installed_extras', lambda: [])
    monkeypatch.setattr(app_updates.components, 'installed_graph', lambda: host)
    monkeypatch.setattr(app_updates.components, 'read_graph', AsyncMock(return_value=graph))
    monkeypatch.setattr(app_updates, 'installed_target', AsyncMock(return_value=(
        '/synthetic/uv', '/synthetic/launcher', python, {'installation': str(tmp_path/'bootstrap'), 'version': 'old'})))
    monkeypatch.setattr(application_generations, 'supports', lambda path: True)
    calls = []
    restarted = asyncio.Event()
    async def close():
        assert work_paused(service.state)
        with sqlite3.connect(service.data_dir/'app.sqlite3') as durable:
            assert load_saved_state(durable)['updates']['pendingReplacement']['forceRestart']['generation'] == pending['generation']
        calls.append('stop')
    monkeypatch.setattr(service.runtime, 'close', close)
    def promote(*args):
        assert calls == ['stop']
        assert work_paused(service.state)
        calls.append('promote')
        return {'prefix': python.parent.parent}
    monkeypatch.setattr(application_generations, 'promote', promote)
    async def process(*args, **kwargs):
        assert '-I' in args and '-c' in args
        calls.append('probe')
        return '99.0.0'
    monkeypatch.setattr(app_updates, 'process', process)
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed', lambda home: True)
    async def restart(owner):
        assert work_paused(service.state) and service.state['updates']['pendingRestart']
        calls.append('restart')
        restarted.set()
    monkeypatch.setattr(app_updates, 'request_managed_restart', restart)
    await manager.publish(phase='app-staged', pendingApp=pending, application={'canInstall': True})
    value = SimpleNamespace(service=service, manager=manager, folder=folder, pending=pending, calls=calls, legacy=legacy,
                            args={**target(manager), 'confirm': True}, restarted=restarted)
    yield value
    # The synthetic close assertions concern activation, not fixture teardown.
    monkeypatch.setattr(service.runtime, 'close', AsyncMock())
    await service.close()


async def test_busy_waits_normally_but_confirmed_restart_fences_stops_then_promotes(staged):
    s = staged.service
    session = s._session()
    session.update(status='working', configurationBusy=True,
                   worktreeHandoffs=[{'id': 'unknown', 'phase': 'unknown'}])
    before = copy.deepcopy(session['messages'])
    await app_updates.activate(staged.manager)
    assert not staged.calls
    assert any(row['kind'] == 'checkout-handoff' for row in staged.manager.blockers())
    await app_updates.activate(staged.manager, force=staged.args)
    assert staged.calls == ['stop', 'promote', 'probe', 'restart']

    assert session['configurationBusy'] is True
    assert session['worktreeHandoffs'] == [{'id': 'unknown', 'phase': 'unknown'}]
    assert session['messages'] == before and not s.runtime.sent
    assert s.state['updates']['pendingRestart']['forceRestart']['revision'] == 'a'*40
    with pytest.raises(AppError, match='update is activating'):
        await s.dispatch('conversation.send', {'text': 'Must not slip into restart'})
    with pytest.raises(RestartRejected):
        await app_updates.activate(staged.manager, force=staged.args)
    assert staged.calls == ['stop', 'promote', 'probe', 'restart']


@pytest.mark.parametrize('staged', ['legacy-configuration'], indirect=True)
@pytest.mark.parametrize('shutdown', ['confirmed', 'unconfirmed', 'still-owned'])
async def test_legacy_configuration_survives_cold_restore_and_confirmed_update_without_replay(staged, monkeypatch, shutdown):
    from amplifier_web.management import Management
    s, legacy = staged.service, staged.legacy
    session = s._session(legacy['id'])
    assert session['configurationBusy'] and session['pendingConfiguration']['recoveryRequired']
    assert not session['pendingConfiguration'].get('operationId')
    assert not session['pendingConfiguration'].get('requestHash')
    assert not s.runtime.started and not s.runtime.sent
    await app_updates.activate(staged.manager)
    assert not staged.calls  # Absence of a worker never clears the unknown-effect fence.
    args = dict(staged.args)
    if shutdown == 'unconfirmed':
        args['confirm'] = False
        with pytest.raises((AppError, ValueError)):
            await s.dispatch('updates.forceRestart', args)
    else:
        if shutdown == 'still-owned':
            s.runtime.workers = {'unconfirmed-native-owner': object()}
        await app_updates.activate(staged.manager, force=args)
    assert legacy['payload'].read_bytes() == legacy['bytes']
    assert legacy['payload'].stat().st_mtime_ns == legacy['mtime']
    assert not s.runtime.started and not s.runtime.sent
    assert session['configurationBusy'] and session['pendingConfiguration']['recoveryRequired']
    assert ('promote' in staged.calls) is (shutdown == 'confirmed')
    if shutdown != 'confirmed':
        assert 'restart' not in staged.calls
        return
    # A new host observes the same original payload; it cannot reconstruct an
    # apply permission or replay a native start/input from this legacy record.
    monkeypatch.setattr(s.runtime, 'close', AsyncMock())
    await s.close()
    runtime = Runtime()
    reopened = AppService(s.data_dir, runtime, workspace=s.default_workspace)
    try:
        reopened.management = Management(reopened)
        restored = reopened._session(legacy['id'])
        assert restored['configurationBusy'] and restored['pendingConfiguration']['recoveryRequired']
        assert legacy['payload'].read_bytes() == legacy['bytes']
        assert legacy['payload'].stat().st_mtime_ns == legacy['mtime']
        assert not runtime.started and not runtime.sent
        view = await reopened.dispatch('configuration.operation.read',
            {'sessionId': legacy['id'], 'includeRecovery': True})
        assert not view['result']['recovery']['canResume']
        assert not view['result']['recovery']['canReconcile']
        assert not runtime.started and not runtime.sent
    finally:
        await reopened.close()


@pytest.mark.parametrize('field', ['confirm', 'hostInstanceId', 'revision', 'generation', 'attemptId'])
async def test_stale_or_unconfirmed_request_has_no_effect(staged, field):
    args = {**staged.args, field: False if field == 'confirm' else 'different'}
    before = copy.deepcopy(staged.service.state['updates']['pendingApp'])
    with pytest.raises((AppError, ValueError)):
        await staged.service.dispatch('updates.forceRestart', args)
    assert not staged.calls
    assert staged.service.state['updates']['pendingApp'] == before
    assert not work_paused(staged.service.state)


async def test_agent_cannot_supply_its_own_user_confirmation(staged):
    with pytest.raises(AppError, match='explicit user confirmation'):
        await staged.service.dispatch('updates.forceRestart', staged.args, origin='agent')
    assert not staged.calls


async def test_action_returns_owned_request_receipt_and_duplicate_does_not_replay(staged):
    staged.service._session()['status'] = 'working'
    receipt = await staged.service.dispatch('updates.forceRestart', staged.args, command_id='confirmed-restart')
    assert receipt['accepted'] and receipt['requestId'] == 'confirmed-restart'
    await asyncio.wait_for(staged.restarted.wait(), 5)
    repeated = await staged.service.dispatch('updates.forceRestart', staged.args, command_id='confirmed-restart')
    assert repeated['requestId'] == receipt['requestId']
    assert staged.calls == ['stop', 'promote', 'probe', 'restart']


async def test_action_without_explicit_id_generates_correlatable_receipt(staged):
    receipt = await staged.service.dispatch('updates.forceRestart', staged.args)
    assert receipt['accepted'] and isinstance(receipt['requestId'], str) and receipt['requestId']
    await asyncio.wait_for(staged.restarted.wait(), 5)
    assert staged.service.state['updates']['forceRestartResult']['requestId'] == receipt['requestId']
    assert staged.calls == ['stop', 'promote', 'probe', 'restart']


@pytest.mark.parametrize('fault', ['marker', 'components', 'bootstrap'])
async def test_force_never_bypasses_candidate_or_installation_validation(staged, monkeypatch, fault):
    if fault == 'marker':
        (staged.folder/'validated.json').write_text('{}')
    elif fault == 'components':
        monkeypatch.setattr(app_updates.components, 'read_graph', AsyncMock(return_value=[]))
    else:
        monkeypatch.setattr(application_generations, 'supports', lambda path: False)
    with pytest.raises(ValueError):
        await app_updates.activate(staged.manager, force=staged.args)
    assert not staged.calls
    assert not work_paused(staged.service.state)


async def test_target_change_during_external_probe_rejects_without_clearing_new_candidate(staged, monkeypatch):
    async def changed(*args, **kwargs):
        staged.service.state['updates']['pendingApp'] = {**staged.pending, 'attemptId': 'new-attempt'}
        return app_updates.components.validate_graph(json.loads((staged.folder/'validated.json').read_text())['componentGraph'])
    monkeypatch.setattr(app_updates.components, 'read_graph', changed)
    await staged.manager.command('forceRestart', staged.args, 'stale-request')
    assert not staged.calls
    state = staged.service.state['updates']
    assert state['pendingApp']['attemptId'] == 'new-attempt' and state['phase'] == 'app-staged'
    assert state['forceRestartResult']['status'] == 'rejected'


@pytest.mark.parametrize('fault', ['stop-error', 'still-owned', 'promotion'])
async def test_failed_interruption_or_promotion_keeps_admission_fenced(staged, monkeypatch, fault):
    if fault == 'stop-error':
        monkeypatch.setattr(staged.service.runtime, 'close', AsyncMock(side_effect=RuntimeError('stop failed')))
    elif fault == 'still-owned':
        staged.service.runtime.workers = {'unconfirmed': object()}
    else:
        monkeypatch.setattr(application_generations, 'promote', lambda *args: (_ for _ in ()).throw(OSError('promotion failed')))
    await app_updates.activate(staged.manager, force=staged.args)
    assert work_paused(staged.service.state)
    assert staged.service.state['updates']['pendingReplacement']['forceRestart']['revision'] == 'a'*40
    assert not staged.service.state['updates'].get('pendingRestart')
    assert 'restart' not in staged.calls and 'probe' not in staged.calls
    assert staged.calls == ([] if fault == 'stop-error' else ['stop'])


async def test_real_owned_runtime_exits_before_promotion(staged, monkeypatch):
    import sys
    from amplifier_web.runtime import RuntimeManager
    from test_runtime_retention import SCRIPT
    script = SCRIPT.replace("parked=False;result={'accepted':True}",
        "print(json.dumps({'type':'runtime.activity','phase':'processing','detail':'input held'}),flush=True);continue")
    runtime = RuntimeManager(command=[sys.executable, '-c', script])
    runtime.home = staged.service.data_dir
    runtime.retention.wake = lambda: None
    session = staged.service._session()
    events = []
    held = asyncio.Event()
    async def emit(kind, data):
        events.append((kind, data))
        if kind == 'runtime.status' and data.get('detail') == 'input held':
            held.set()
    original = runtime.close
    sending = asyncio.create_task(runtime.send(session, 'accepted synthetic work', 'one-input', emit))
    try:
        await asyncio.wait_for(held.wait(), 5)
        assert runtime.has_pending_operations() and not sending.done()
        process = runtime.workers[session['id']]['process']
        staged.service.runtime = runtime
        async def closed():
            await original()
            assert process.returncode == 0 and not runtime.workers
            assert not runtime.has_pending_operations()
            staged.calls.append('stop')
        monkeypatch.setattr(runtime, 'close', closed)
        await app_updates.activate(staged.manager, force=staged.args)
        assert staged.calls == ['stop', 'promote', 'probe', 'restart']
        result, = await asyncio.gather(sending, return_exceptions=True)
        from amplifier_web.runtime import RuntimeHostClosingError
        assert isinstance(result, RuntimeHostClosingError)
        assert runtime._closed
        assert len([data for kind, data in events if kind == 'runtime.status' and data.get('detail') == 'input held']) == 1
        with pytest.raises(Exception):
            await runtime.send(session, 'not admitted', 'second-input', emit)
    finally:
        await original()
        await asyncio.gather(sending, return_exceptions=True)
