"""A stopped settings-recovery fence is not executing work.

Real persisted host restart and MCP discovery/switch; no provider calls, user
configuration, or conversation execution. Unknown effects remain unknown.
"""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from amplifier_web.management import Management
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService, AppError
from amplifier_web.smart_tools import SmartToolsManager
from amplifier_web.update_blockers import blockers
from amplifier_web.updates import UpdateManager
from test_smart_tool_update_cleanup import OLD, configure, package


@pytest.fixture
async def held(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_UNIFIED_IMPORT_HOME', str(tmp_path / 'absent'))
    home = tmp_path / 'app'
    first = AppService(home, RuntimeManager(), workspace=tmp_path)
    first.management = Management(first)
    first.update_manager = UpdateManager(first)
    await first.dispatch('session.create', {})
    session = first._session()
    sid = session['id']
    session.update(status='error', pendingConfiguration={'phase': 'error', 'error': 'Old interrupted change'})
    path = first.management.queued_path(sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b'{"tools": [{"module": "retained-old-tool"}]}\n'
    path.write_bytes(payload)
    await configure(first)
    await package(first.smart_tools, 'b' * 24, 'b' * 40, staged=True)
    await first.update_manager.publish(phase='staged', pendingSmartTools=[{'previous': OLD, 'target': 'b' * 24}])
    first._save_changes(sessions={sid})
    await first.close()

    app = AppService(home, RuntimeManager(), workspace=tmp_path)
    app.management = Management(app)
    app.smart_tools = SmartToolsManager(app)
    app.update_manager = UpdateManager(app)
    assert app._session(sid)['configurationBusy']
    assert app._session(sid)['pendingConfiguration']['recoveryRequired']
    assert not app.runtime.has_pending_operations()
    yield app, sid, path, payload
    await app.close()


async def test_cold_legacy_hold_allows_tool_update_but_retains_chat_fence_and_payload(held):
    app, sid, path, payload = held
    session = app._session(sid)
    original = copy.deepcopy(session['pendingConfiguration'])
    await app.smart_tools.connect('board')
    assert not app.update_manager.busy()
    await app.update_manager.activateSmartTools()
    assert app.smart_tools._server('board')['installationId'] == 'b' * 24
    assert not app.state['updates']['pendingSmartTools']
    assert session['configurationBusy'] and session['pendingConfiguration'] == original
    assert path.read_bytes() == payload
    assert not (app.data_dir / 'tool-effects').exists()
    assert not app.runtime.workers
    with pytest.raises(AppError, match='settings|configuration|Configuration'):
        await app.dispatch('conversation.send', {'sessionId': sid, 'text': 'Must stay held'})
    assert not app.runtime.workers
    home, workspace = app.data_dir, app.default_workspace
    await app.close()
    reopened = AppService(home, RuntimeManager(), workspace=workspace)
    reopened.management = Management(reopened)
    reopened.smart_tools = SmartToolsManager(reopened)
    reopened.update_manager = UpdateManager(reopened)
    try:
        assert reopened._session(sid)['configurationBusy']
        assert reopened._session(sid)['pendingConfiguration'] == original
        assert path.read_bytes() == payload
        assert reopened.smart_tools._server('board')['installationId'] == 'b' * 24
        assert not reopened.update_manager.busy()
        assert not reopened.runtime.workers
    finally:
        await reopened.close()


@pytest.mark.parametrize('guard', ['settings-task', 'cancelling-task', 'management-lock', 'runtime',
    'queued', 'applying', 'refresh', 'refresh-applying', 'modern-operation', 'new-fence',
    'handoff', 'working', 'worker', 'working-worker', 'running-turn', 'unobservable-runtime'])
async def test_actual_or_unobservable_work_still_holds_update(held, guard):
    app, sid, path, payload = held
    session = app._session(sid)
    task = None
    release = asyncio.Event()
    if guard in {'settings-task', 'cancelling-task'}:
        async def pending():
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
        task = asyncio.create_task(pending())
        app.management.pending_config_tasks[sid] = task
        await asyncio.sleep(0)
        if guard == 'cancelling-task':
            task.cancel()
            await asyncio.sleep(0)
    elif guard == 'management-lock':
        await app.management.lock.acquire()
    elif guard == 'runtime':
        app.runtime.has_pending_operations = lambda: True
    elif guard in {'queued', 'applying'}:
        session['pendingConfiguration']['phase'] = guard
    elif guard == 'refresh':
        session['configurationPending'] = True
    elif guard == 'refresh-applying':
        session['configurationRefresh'] = {'phase': 'applying'}
    elif guard == 'modern-operation':
        session['pendingConfiguration']['operationId'] = 'retained-operation'
    elif guard == 'new-fence':
        session['configurationOperationFence'] = 'different-operation'
    elif guard == 'handoff':
        session['worktreeHandoffs'] = [{'phase': 'unknown'}]
    elif guard == 'working':
        session['status'] = 'working'
    elif guard == 'worker':
        session['workers'] = [{'status': 'running'}]
    elif guard == 'working-worker':
        session['workers'] = [{'status': 'working'}]
    elif guard == 'running-turn':
        session['execution'] = {'turns': [{'id': 'running-turn', 'phase': 'running'}]}
    elif guard == 'unobservable-runtime':
        app.runtime.has_pending_operations = None
    try:
        assert app.update_manager.busy()
        await app.update_manager.activateSmartTools()
        assert app.smart_tools._server('board')['installationId'] == OLD
        assert app.state['updates']['pendingSmartTools']
        assert path.read_bytes() == payload
    finally:
        release.set()
        if task:
            await task
        if guard == 'management-lock':
            app.management.lock.release()


def test_absent_management_cannot_prove_hold_is_quiescent():
    service = SimpleNamespace(state={'sessions': [{'id': 'chat', 'configurationBusy': True,
        'pendingConfiguration': {'phase': 'error', 'recoveryRequired': True}}]},
        runtime=SimpleNamespace(has_pending_operations=lambda: False))
    assert blockers(service)
