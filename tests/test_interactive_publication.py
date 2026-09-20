"""Interactive results commit immediately; app snapshots can follow in a batch."""
import asyncio
import copy
import json
import sqlite3

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.smart_canvas import SmartCanvas
from amplifier_web.smart_tools import SmartToolsManager
from test_smart_canvas import Tools
from test_smart_tools import Service


@pytest.fixture
async def interactive(tmp_path, monkeypatch):
    service = AppService(tmp_path, workspace=tmp_path)
    example = Tools(service)
    advertised = copy.deepcopy(service.state['smartTools']['servers'][0]['tools'])
    service.smart_tools = SmartToolsManager(service)
    # Startup correctly clears stale discovery. This fixture represents the
    # synthetic server advertising its app after a fresh connection.
    service.state['smartTools']['servers'][0].update(
        status='connected', connectionState='connected', tools=advertised,
        catalogState='current')
    service.smart_tools.read_app = example.read_app
    service.smart_canvas = SmartCanvas(service)
    await service.dispatch('session.create', {'title': 'Interactive'})
    await service.smart_canvas.open({'id': 'one', 'tool': 'read'})

    async def execute(*args, **kwargs):
        return {'content': [], 'structuredContent': {'accepted': True}}

    async def hold_publication():
        # Assertions depend on the publication boundary, not timer speed.
        await asyncio.Event().wait()

    monkeypatch.setattr(service.smart_tools, 'execute', execute)
    monkeypatch.setattr(service, '_flush_progress', hold_publication)
    yield service
    await service.close()


def arguments(service):
    return {'canvasId': service.state['canvas']['id'], 'name': 'read', 'arguments': {}}


def persisted(service, identity):
    with sqlite3.connect(service.data_dir / 'app.sqlite3') as connection:
        assert connection.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone()
        row = connection.execute('SELECT value FROM smart_tool_operations WHERE id=?', (identity,)).fetchone()
        return json.loads(row[0])


async def test_interactive_burst_commits_before_effect_and_return_without_snapshots(interactive, monkeypatch):
    service = interactive
    calls, saves, snapshots = [], [], []
    original_save, original_snapshot = service._save, service.browser_state

    def save():
        saves.append(True)
        return original_save()

    def snapshot(*args, **kwargs):
        snapshots.append(True)
        return original_snapshot(*args, **kwargs)

    async def execute(action, args, **kwargs):
        identity = 'burst-' + str(len(calls))
        assert persisted(service, identity)['status'] == 'running'
        calls.append(identity)
        return {'content': [], 'structuredContent': {'count': len(calls)}}

    monkeypatch.setattr(service, '_save', save)
    monkeypatch.setattr(service, 'browser_state', snapshot)
    monkeypatch.setattr(service.smart_tools, 'execute', execute)
    queue = service.subscribe()
    revision = service.state['revision']
    for index in range(12):
        identity = f'burst-{index}'
        receipt = await service.dispatch('smartTools.appCall', arguments(service), command_id=identity, include_state=False)
        assert 'state' not in receipt
        assert persisted(service, identity)['status'] == 'queued'
        operation = await service.wait_smart_tool(identity)
        assert operation['status'] == 'completed'
        assert persisted(service, identity)['result'] == operation['result']
        duplicate = await service.dispatch('smartTools.appCall', arguments(service), command_id=identity, include_state=False)
        assert duplicate['duplicate'] and 'state' not in duplicate
    assert len(calls) == 12 and len(service.state['smartTools']['operations']) == 12
    assert saves == snapshots == []
    assert queue.empty() and service.state['revision'] == revision
    await service._flush_pending_progress()
    assert len(saves) == len(snapshots) == 1
    assert queue.get_nowait()['revision'] == revision + 1 and queue.empty()
    service.unsubscribe(queue)


async def test_interactive_progress_flushes_for_reads_cas_and_normal_duplicates(interactive):
    service = interactive
    revision = service.state['revision']
    await service.dispatch('smartTools.appCall', arguments(service), command_id='cas', include_state=False)
    await service.wait_smart_tool('cas')
    with pytest.raises(AppError, match='app changed'):
        await service.dispatch('view.update', {'patch': {'scheme': 'dark'}}, expected_revision=revision)
    assert service.state['view']['scheme'] == 'system'
    assert not service._progress_dirty
    await service.dispatch('smartTools.appCall', arguments(service), command_id='read', include_state=False)
    await service.wait_smart_tool('read')
    result = await service.app_bridge('get_state', {}, service.state['selectedSessionId'])
    assert result['revision'] == service.state['revision'] and not service._progress_dirty
    await service.dispatch('smartTools.appCall', arguments(service), command_id='duplicate', include_state=False)
    await service.wait_smart_tool('duplicate')
    duplicate = await service.dispatch('smartTools.appCall', arguments(service), command_id='duplicate')
    assert duplicate['duplicate'] and not service._progress_dirty
    assert duplicate['state']['canvas']['mcp']['lastOperationId'] == 'duplicate'


@pytest.mark.parametrize('stage', ['queued', 'running', 'completed'])
async def test_restart_uses_committed_receipt_over_stale_snapshot(interactive, tmp_path, stage):
    service = interactive
    gate, started = asyncio.Event(), asyncio.Event()

    async def execute(*args, **kwargs):
        started.set()
        await gate.wait()
        return {'content': [], 'structuredContent': {'survives': True}}

    service.smart_tools.execute = execute
    await service.dispatch('smartTools.appCall', arguments(service), command_id='crash-window', include_state=False)
    if stage != 'queued':
        await started.wait()
    if stage == 'completed':
        # A periodic snapshot can retain running even though completion commits
        # before the next snapshot. Preserve that stale overview in this fixture.
        stale = copy.deepcopy(service.state)
        stale['smartTools']['inspectedOperation'] = copy.deepcopy(stale['smartTools']['operations'][-1])
        gate.set()
        await service.wait_smart_tool('crash-window')
    else:
        stale = json.loads(service.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0])
    restored = Service(tmp_path, state=stale)
    service.db.backup(restored.db)
    recovered = SmartToolsManager(restored)
    operation = recovered.operation('crash-window')
    assert operation['status'] == ('completed' if stage == 'completed' else 'interrupted')
    assert next(row for row in recovered.state['operations'] if row['id'] == 'crash-window') == recovered._overview(operation)
    if stage == 'completed':
        assert operation['result']['structuredContent'] == {'survives': True}
        assert recovered.state['inspectedOperation']['status'] == 'completed'
    # Recovery retains admission, so the original request cannot execute again.
    assert restored.db.execute('SELECT 1 FROM commands WHERE id=?', ('crash-window',)).fetchone()
    await recovered.close()
    restored.db.close()
    gate.set()
    await service.wait_smart_tool('crash-window')


@pytest.mark.parametrize('failure', ['tool', 'unexpected', 'cancel'])
async def test_interactive_failures_have_durable_terminal_receipts(interactive, failure):
    service = interactive
    started = asyncio.Event()

    async def execute(*args, **kwargs):
        started.set()
        if failure == 'cancel':
            await asyncio.Event().wait()
        raise RuntimeError('Fixture tool failure')

    if failure == 'unexpected':
        service.smart_canvas.command = execute
    else:
        service.smart_tools.execute = execute
    await service.dispatch('smartTools.appCall', arguments(service), command_id=failure, include_state=False)
    await started.wait()
    if failure == 'cancel':
        task = service.smart_tool_requests[failure]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    else:
        await service.wait_smart_tool(failure)
    operation = persisted(service, failure)
    assert operation['status'] == ('failed' if failure == 'tool' else 'interrupted')
    assert (await service.dispatch('smartTools.appCall', arguments(service), command_id=failure, include_state=False))['duplicate']


async def test_normal_tool_calls_still_publish_immediately(interactive):
    service = interactive
    revision = service.state['revision']
    result = await service.dispatch('smartTools.appCall', arguments(service), command_id='normal')
    assert 'state' in result and result['state']['revision'] > revision
    await service.wait_smart_tool('normal')
    assert not getattr(service, '_progress_dirty', False)
