import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_web.runtime_controls import RuntimeControls
from amplifier_web.service import AppService, AppError


class Coordinator:
    def __init__(self):
        self.session_state, self.capabilities = {}, {"live.continuation_guard_supported": True}
        self.config = {'session': {'context': {'module': 'context-simple'}}, 'providers': []}
        self.loop = SimpleNamespace(goal_stall_threshold=3)
        self.context = SimpleNamespace(get_messages=AsyncMock(return_value=[]))
    def get(self, name): return {'orchestrator': self.loop, 'context': self.context, 'providers': {}}.get(name)
    def get_capability(self, name): return self.capabilities.get(name)
    def register_capability(self, name, value): self.capabilities[name] = value


def controls(sid='task'):
    coordinator = Coordinator()
    return RuntimeControls(SimpleNamespace(session_id=sid, coordinator=coordinator), SimpleNamespace(generation=None, queued_inputs=0))


async def create(controller, **changes):
    return await controller.perform('task.create', {'commandId': 'create', 'expectedRevision': 0, 'objective': 'Ship the verified report', 'constraints': ['Preserve originals'], 'operationIds': ['unknown-operation'], 'artifactRefs': ['report.md'], **changes})


async def test_cas_receipts_pause_restart_and_explicit_completion(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    first = controls()
    record = (await create(first))['task']
    assert record['appliedRevision'] == 1
    first.runtime.generation = 'running'
    update = {'commandId': 'correct', 'expectedRevision': 1, 'correction': 'Use the corrected source', 'origin': 'ui'}
    record = (await first.perform('task.update', update))['task']
    assert record['revision'] == 2 and record['appliedRevision'] == 1
    assert (await first.perform('task.update', update))['duplicate']
    with pytest.raises(ValueError, match='different contents'):
        await first.perform('task.update', {**update, 'correction': 'Wrong'})
    with pytest.raises(ValueError, match='revision changed'):
        await first.perform('task.pause', {'commandId': 'stale', 'expectedRevision': 1})
    hook = await first.tasks.boundary('provider:request', {'session_id': 'task'})
    assert 'Use the corrected source' in hook.context_injection
    assert first.tasks.record()['appliedRevision'] == 2
    await first.perform('task.pause', {'commandId': 'pause', 'expectedRevision': 2})
    assert not await first.tasks.continuation_allowed()
    assert first.coordinator.session_state['goal'] is None
    restored = controls()
    await restored.restore()
    state = await restored.perform('task.get')
    assert state['task']['status'] == 'paused' and state['goal'] is None
    assert state['task']['operationIds'] == ['unknown-operation']
    assert state['task']['artifactRefs'] == ['report.md']
    assert (await restored.perform('task.update', update))['duplicate']
    with pytest.raises(ValueError, match='saved task controls'):
        await restored.perform('goals.set', {'condition': 'Bypass pause'})
    await restored.perform('task.resume', {'commandId': 'resume', 'expectedRevision': 3})
    assert await restored.tasks.continuation_allowed()
    restored.coordinator.session_state['goal'] = None  # A model's goal judge or turn completion is not task completion.
    assert restored.tasks.record()['status'] == 'active'
    with pytest.raises(ValueError, match='evidence'):
        await restored.perform('task.complete', {'commandId': 'no-evidence', 'expectedRevision': 4, 'condition': 'Ship the verified report', 'evidence': []})
    with pytest.raises(ValueError, match='exact current objective'):
        await restored.perform('task.complete', {'commandId': 'wrong-goal', 'expectedRevision': 4, 'condition': 'Old condition', 'evidence': ['Tests pass']})
    await restored.perform('task.complete', {'commandId': 'finish', 'expectedRevision': 4, 'condition': 'Ship the verified report', 'evidence': ['Report checked against revised source']})
    await create(restored, commandId='next', expectedRevision=5, objective='Next task')
    newest = controls(); await newest.restore()
    assert newest.tasks.history[0]['completionEvidence'] == ['Report checked against revised source']
    assert newest.tasks.record()['objective'] == 'Next task'
    for item in (first, restored, newest): await item.close()


async def test_shared_agent_ui_actions_target_session_preserve_draft_and_never_send(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'home'))
    worker = controls()
    runtime = SimpleNamespace(control=AsyncMock(side_effect=lambda sid, operation, args: worker.perform(operation, args)), close=AsyncMock())
    # AsyncMock side effects returning coroutines are not awaited automatically.
    async def control(sid, operation, args): return await worker.perform(operation, args)
    runtime.control = control
    app = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    app.management = SimpleNamespace(ensure_runtime=AsyncMock(), close=AsyncMock())
    try:
        await app.dispatch('session.create', {'title': 'Target'})
        sid = app._session()['id']
        await app.dispatch('session.create', {'title': 'Selected'})
        selected = app._session()['id']
        await app.dispatch('view.update', {'patch': {'draft': 'Keep typing'}})
        result = await app.app_bridge('dispatch', {'action': 'task.create', 'id': 'agent-create', 'args': {'expectedRevision': 0, 'objective': 'Verify outputs'}}, sid)
        assert result['result']['task']['objective'] == 'Verify outputs'
        with pytest.raises(AppError, match='calling conversation'):
            await app.app_bridge('dispatch', {'action': 'task.pause', 'args': {'sessionId': selected, 'expectedRevision': 1}}, sid)
        with pytest.raises(AppError, match='calling conversation'):
            await app.app_bridge('dispatch', {'action': 'runtime.control', 'args': {'sessionId': selected, 'operation': 'task.pause', 'args': {'expectedRevision': 1}}}, sid)
        pause = await app.dispatch('task.pause', {'sessionId': sid, 'expectedRevision': 1}, command_id='ui-pause')
        assert pause['result']['task']['status'] == 'paused'
        assert app.state['selectedSessionId'] == selected
        assert app.state['view']['draft'] == 'Keep typing'
        assert (await app.app_bridge('dispatch', {'action': 'task.get', 'args': {}}, sid))['result']['task']['status'] == 'paused'
    finally:
        app.management = None
        await app.close(); await worker.close()


async def test_failed_durable_write_does_not_acknowledge_or_change_task(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    def fail(**kwargs): raise OSError('Read-only storage')
    monkeypatch.setattr(worker, 'persist', fail)
    with pytest.raises(OSError, match='Read-only'):
        await create(worker)
    assert worker.tasks.record() is None
    assert worker.tasks.receipts == {}
    assert worker.coordinator.session_state['goal'] is None
    await worker.close()


async def test_older_runtime_cannot_claim_durable_pause_support(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    worker.coordinator.capabilities.pop('live.continuation_guard_supported')
    with pytest.raises(ValueError, match='continuation guard support'):
        await create(worker)
    assert worker.tasks.record() is None
    await worker.close()


async def test_downgraded_runtime_restores_record_without_enabling_unsafe_goal(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    first = controls(); await create(first)
    old = controls(); old.coordinator.capabilities.pop('live.continuation_guard_supported')
    await old.restore()
    snapshot = await old.perform('task.get')
    assert snapshot['task']['status'] == 'active'
    assert snapshot['goal'] is None and not snapshot['continuationSupported']
    assert not await old.tasks.continuation_allowed()
    await first.close(); await old.close()
