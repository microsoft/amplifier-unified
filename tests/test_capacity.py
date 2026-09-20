import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_web.capacity import aggregate, evaluate, usage_snapshot, admission
from amplifier_web.execution import ingest
from amplifier_web.execution_events import ExecutionEvents, public_usage
from amplifier_web.history_revision import trim_execution
from amplifier_web.service import AppService, AppError
from test_task_continuity import controls


def call(identity='one', sid='root', **changes):
    return {'id': identity, 'sessionId': sid, 'rootSessionId': 'root', 'kind': 'llm', 'phase': 'completed',
            'provider': 'provider-a', 'model': 'model-a', 'revision': 2, 'startedAt': 1, 'endedAt': 2,
            'usage': {'inputTokens': 8, 'outputTokens': 2, 'totalTokens': 10, 'costUsd': .01, 'costType': 'reported'}, **changes}


def test_dedup_cumulative_replacement_corrections_descendants_and_unknowns():
    root = {'id': 'root', 'workers': [{'sessionId': 'child'}]}
    first = call()
    for row in [first, first, call('two', 'child'), call('ignore', 'unrelated'), {'id': 'summary', 'kind': 'tool', 'aggregateUsage': {'totalTokens': 99999}}]:
        ingest(root, row)
    assert usage_snapshot(root)['metrics']['totalTokens']['value'] == 20
    ingest(root, call(usage={'totalTokens': 7}, revision=3))  # A correction replaces the whole receipt, including removed cost.
    ingest(root, first)  # Reconnect delivers an old cumulative receipt.
    snapshot = usage_snapshot(root)
    assert snapshot['calls'] == 2 and snapshot['excludedUnboundCalls'] == 1
    assert snapshot['metrics']['totalTokens']['value'] == 17
    assert snapshot['metrics']['costUsd']['value'] == .01 and snapshot['metrics']['costUsd']['unknownCalls'] == 1
    assert snapshot['metrics']['costUsd']['status'] == 'partial'
    assert len(snapshot['providers']) == 1
    assert all('output' not in row for row in snapshot['receipts'])
    ingest(root, call('three', usage={'totalTokens': float('nan'), 'costUsd': False}))
    snapshot = usage_snapshot(root)
    assert snapshot['metrics']['totalTokens']['unknownCalls'] == 1
    assert snapshot['metrics']['costUsd']['unknownCalls'] == 2


def test_history_edit_transfers_usage_and_late_correction_is_not_double_counted():
    root = {'id': 'root', 'messages': [{'id': 'new', 'role': 'user'}]}
    ingest(root, call(turnId='input'))
    root['execution']['turns'] = [{'id': 'input', 'inputId': 'input', 'anchorMessageId': 'old'}]
    trim_execution(root, [{'id': 'old', 'inputId': 'input', 'role': 'user'}], 'old')
    assert root['execution']['nodes'] == []
    assert usage_snapshot(root)['metrics']['totalTokens']['value'] == 10
    ingest(root, call(revision=3, usage={'totalTokens': 12}))
    snapshot = usage_snapshot(root)
    assert snapshot['calls'] == 1 and snapshot['metrics']['totalTokens']['value'] == 12
    assert snapshot['metrics']['costUsd']['value'] is None


def test_policy_reports_estimates_pending_unknown_and_inflight_overshoot():
    root = {'id': 'root'}
    ingest(root, call(usage={'totalTokens': 80, 'costUsd': .8, 'costType': 'estimated'}))
    ingest(root, call('pending', phase='running', endedAt=None, usage={}))
    usage = usage_snapshot(root)
    policy = {'enabled': True, 'maxTotalTokens': 100, 'maxCostUsd': 1, 'warningFraction': .8, 'unknownPolicy': 'pause'}
    state = evaluate(policy, usage)
    assert not state['allowed'] and 'unknown' in state['reasons'][0]
    assert state['inFlightCalls'] == 1
    policy['includeEstimatedCost'] = True
    assert evaluate(policy, usage)['state'] == 'warning'
    policy['maxTotalTokens'] = 80
    assert evaluate(policy, usage)['state'] == 'paused'
    policy['maxTotalTokens'] = None; policy['maxCostUsd'] = None
    assert evaluate(policy, usage)['allowed']
    empty = aggregate([])['costUsd']
    assert empty['value'] is None and empty['status'] == 'empty'


async def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    app = AppService(tmp_path, workspace=tmp_path)
    await app.dispatch('session.create', {'title': 'Budget task'})
    sid = app._session()['id']
    worker = controls(sid)
    async def control(session_id, operation, args):
        assert session_id == sid
        return await worker.perform(operation, args)
    app.runtime = SimpleNamespace(control=control, close=AsyncMock())
    app.management = SimpleNamespace(ensure_runtime=AsyncMock(), close=AsyncMock(), setup_manager=None, provider_catalog=SimpleNamespace(close=AsyncMock()))
    return app, worker, sid


async def test_running_cas_duplicate_restart_ui_agent_parity_and_wrong_session(tmp_path, monkeypatch):
    app, worker, sid = await setup(tmp_path, monkeypatch)
    try:
        worker.runtime.generation = 'in-flight'
        args = {'sessionId': sid, 'expectedRevision': 0, 'enabled': True, 'maxTotalTokens': 10}
        result = await app.app_bridge('dispatch', {'action': 'capacity.set', 'id': 'same', 'args': args}, sid)
        assert result['result']['budget']['revision'] == 1
        result = await app.app_bridge('dispatch', {'action': 'capacity.set', 'id': 'same', 'args': args}, sid)
        assert result['result']['budget']['revision'] == 1
        with pytest.raises(AppError, match='different contents'):
            await app.app_bridge('dispatch', {'action': 'capacity.set', 'id': 'same', 'args': {**args, 'maxTotalTokens': 11}}, sid)
        with pytest.raises(AppError, match='revision changed'):
            await app.dispatch('capacity.set', args, command_id='stale')
        selected_before = app.state['selectedSessionId']; app.state['view']['draft'] = 'Retain this unsent message'
        with pytest.raises(AppError, match='calling conversation'):
            await app.app_bridge('dispatch', {'action': 'capacity.read', 'args': {'sessionId': 'wrong'}}, sid)
        restored = controls(sid); await restored.restore()
        assert restored.capacity.policy['maxTotalTokens'] == 10
        assert (await restored.perform('capacity.set', {k: v for k, v in {**args, 'commandId': 'same', 'origin': 'agent'}.items() if k != 'sessionId'}))['duplicate']
        assert app.state['selectedSessionId'] == selected_before and app.state['view']['draft'] == 'Retain this unsent message'
        await restored.close()
    finally:
        await worker.close(); await app.close()


async def test_actual_provider_guard_counts_children_blocks_future_calls_and_retains_correction(tmp_path, monkeypatch):
    app, worker, sid = await setup(tmp_path, monkeypatch)
    events = []
    try:
        await app.dispatch('capacity.set', {'sessionId': sid, 'expectedRevision': 0, 'enabled': True, 'maxTotalTokens': 10}, command_id='budget')
        async def admitted(row): return await app.app_bridge('capacity.admit', {'call': row}, sid)
        worker.capacity.admit = admitted
        telemetry = ExecutionEvents(sid, events.append); telemetry.admission_guard = worker.capacity.guard
        class Provider:
            def __init__(self): self.calls = 0
            def get_info(self): return SimpleNamespace(id='synthetic', defaults={'model': 'test'})
            async def complete(self, request):
                self.calls += 1
                return SimpleNamespace(usage={'input_tokens': 8, 'output_tokens': 2})
        provider = Provider(); telemetry.instrument_provider(sid, provider)
        await provider.complete(SimpleNamespace(model='test'))
        for event in events:
            await app.on_runtime_event('execution.event', event['event'])
        assert provider.calls == 1
        assert usage_snapshot(app._session(sid))['receipts'][0]['budgetRevision'] == 1
        await worker.perform('task.create', {'commandId': 'task', 'expectedRevision': 0, 'objective': 'Keep latest objective'})
        await worker.perform('task.update', {'commandId': 'correction', 'expectedRevision': 1, 'correction': 'Use the corrected source'})
        telemetry.lifecycle({'type': 'child.updated', 'sessionId': 'child', 'status': 'running'})
        for event in events[-1:]: await app.on_runtime_event('execution.event', event['event'])
        child = Provider(); telemetry.instrument_provider('child', child)
        with pytest.raises(ValueError, match='budget paused'):
            await child.complete(SimpleNamespace(model='test'))
        assert child.calls == 0 and not await worker.tasks.continuation_allowed()
        assert worker.tasks.record()['corrections'][0]['text'] == 'Use the corrected source'
        restored = controls(sid); await restored.restore()
        assert not await restored.tasks.continuation_allowed()
        await app.dispatch('capacity.set', {'sessionId': sid, 'expectedRevision': 1, 'maxTotalTokens': 30}, command_id='increase')
        await child.complete(SimpleNamespace(model='test'))
        assert child.calls == 1
        assert worker.tasks.record()['objective'] == 'Keep latest objective'
        assert await worker.tasks.continuation_allowed()
        await restored.close()
    finally:
        await worker.close(); await app.close()


async def test_crash_receipt_retained_and_duplicate_admission_does_not_invoke_provider(tmp_path, monkeypatch):
    app, worker, sid = await setup(tmp_path, monkeypatch)
    try:
        await app.dispatch('capacity.set', {'sessionId': sid, 'expectedRevision': 0, 'enabled': True, 'maxTotalTokens': 20}, command_id='budget')
        row = call('unknown', sid, rootSessionId=sid, phase='running', endedAt=None, usage={})
        assert (await admission(app, sid, {'call': row}))['allowed']
        assert not (await admission(app, sid, {'call': row}))['allowed']
        await app.on_runtime_event('runtime.ended', {'sessionId': sid, 'backgroundCallIds': [], 'status': 'interrupted'})
        await app.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'interrupted'})
        usage = usage_snapshot(app._session(sid))
        # Explicitly settle a lost provider receipt; no usage or cost is invented.
        ingest(app._session(sid), {**row, 'phase': 'interrupted', 'endedAt': 3, 'revision': 3})
        app._save()
        result = await app.dispatch('capacity.read', {'sessionId': sid})
        assert result['result']['admission']['state'] == 'paused'
        assert result['result']['usage']['metrics']['totalTokens']['value'] is None
        other = AppService(tmp_path, workspace=tmp_path)
        assert usage_snapshot(other._session(sid))['calls'] == 1
        assert usage_snapshot(other._session(sid))['metrics']['totalTokens']['unknownCalls'] == 1
        await other.close()
    finally:
        await worker.close(); await app.close()


async def test_provider_owned_quota_is_read_only_allowlisted_and_unknown_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path)); worker = controls()
    class Supported:
        async def get_usage_limits(self):
            return {'secret': 'private', 'windows': [{'name': 'daily', 'unit': 'tokens', 'remaining': 25, 'resetsAt': 1800000000, 'api_key': 'private'}]}
    old_get = worker.coordinator.get
    worker.coordinator.get = lambda name: {'known': Supported(), 'unknown': object()} if name == 'providers' else old_get(name)
    result = await worker.perform('capacity.quota', {})
    assert result['providers'][0]['windows'][0]['remaining'] == 25
    assert result['providers'][1]['remaining'] is None and result['providers'][1]['status'] == 'unsupported'
    assert 'private' not in json.dumps(result)
    await worker.close()


def test_public_usage_estimate_provenance_reasoning_and_invalid_cost():
    assert public_usage({'cost_usd': True})['costType'] == 'unavailable'
    usage = public_usage({'cost_usd': .01, 'cost_type': 'estimated', 'cost_source': 'provider price snapshot', 'reasoning_tokens': 12})
    assert usage['costType'] == 'estimated' and usage['costSource'] == 'provider price snapshot'
    assert usage['reasoningTokens'] == 12


async def test_immutable_provider_cannot_bypass_admission():
    class Immutable:
        __slots__ = ()
        def get_info(self): return {'id': 'immutable', 'defaults': {'model': 'test'}}
        async def complete(self, request): raise AssertionError('Must never execute')
    telemetry = ExecutionEvents('root', lambda event: None)
    async def deny(row): raise ValueError('budget reached')
    telemetry.admission_guard = deny
    provider = Immutable(); wrapped = telemetry.instrument_provider('root', provider)
    assert wrapped is not provider and telemetry.instrument_provider('root', provider) is wrapped
    with pytest.raises(ValueError, match='budget reached'):
        await wrapped.complete(SimpleNamespace(model='test'))
    assert not telemetry.nodes


async def test_new_worker_keeps_old_inflight_receipt_unknown_without_replay(tmp_path, monkeypatch):
    app, worker, sid = await setup(tmp_path, monkeypatch)
    try:
        await app.dispatch('capacity.set', {'sessionId': sid, 'expectedRevision': 0, 'enabled': True, 'maxTotalTokens': 100}, command_id='budget')
        first = call('old', sid, rootSessionId=sid, producerId='old-process', phase='running', endedAt=None, usage={})
        assert (await admission(app, sid, {'call': first}))['allowed']
        second = {**first, 'id': 'new', 'producerId': 'new-process'}
        denied = await admission(app, sid, {'call': second})
        assert not denied['allowed'] and 'unknown' in denied['reasons'][0]
        receipt = usage_snapshot(app._session(sid))['receipts'][0]
        assert receipt['phase'] == 'outcome_unknown' and receipt['id'] == 'old'
        assert len(app._session(sid)['execution']['nodes']) == 1
        await worker.perform('task.create', {'expectedRevision': 0, 'commandId': 'task', 'objective': 'Keep pause'})
        await worker.perform('task.pause', {'expectedRevision': 1, 'commandId': 'pause'})
        await app.dispatch('capacity.set', {'sessionId': sid, 'expectedRevision': 1, 'unknownPolicy': 'allow'}, command_id='allow')
        assert not await worker.tasks.continuation_allowed()  # Capacity editing is not task resume.
    finally:
        await worker.close(); await app.close()


async def test_receipt_pages_keep_full_totals_and_restart_observation_unknown(tmp_path, monkeypatch):
    app, worker, sid = await setup(tmp_path, monkeypatch)
    try:
        session = app._session(sid)
        for i in range(4): ingest(session, call(str(i), sid, rootSessionId=sid, producerId='old'))
        ingest(session, call('inflight', sid, rootSessionId=sid, producerId='old', phase='running', endedAt=None, usage={}))
        app._save()
        first = (await app.dispatch('capacity.read', {'sessionId': sid, 'limit': 2}))['result']['usage']
        assert len(first['receipts']) == 2 and first['nextOffset'] == 2
        assert first['metrics']['totalTokens']['value'] == 40
        second = (await app.dispatch('capacity.read', {'sessionId': sid, 'offset': 2, 'limit': 2}))['result']['usage']
        assert first['receipts'][0]['id'] != second['receipts'][0]['id']
        restarted = AppService(tmp_path, workspace=tmp_path)
        snapshot = usage_snapshot(restarted._session(sid))
        assert snapshot['metrics']['totalTokens']['pendingCalls'] == 0
        assert snapshot['metrics']['totalTokens']['unknownCalls'] == 1
        assert snapshot['calls'] == 5
        await restarted.close()
    finally:
        await worker.close(); await app.close()


async def test_runtime_control_alias_cannot_escape_capacity_target_or_forge_actor(tmp_path, monkeypatch):
    app, worker, sid = await setup(tmp_path, monkeypatch)
    try:
        with pytest.raises(AppError, match='calling conversation'):
            await app.app_bridge('dispatch', {'action': 'runtime.control', 'args': {'sessionId': 'wrong', 'operation': 'capacity.set', 'args': {'expectedRevision': 0, 'enabled': True, 'maxTotalTokens': 10}}}, sid)
        with pytest.raises(AppError, match='Additional properties'):
            await app.app_bridge('dispatch', {'action': 'runtime.control', 'args': {'sessionId': sid, 'operation': 'capacity.set', 'args': {'expectedRevision': 0, 'actor': 'ui', 'origin': 'ui', 'enabled': True, 'maxTotalTokens': 10}}}, sid)
        reply = await app.app_bridge('dispatch', {'id': 'alias', 'action': 'runtime.control', 'args': {'sessionId': sid, 'operation': 'capacity.set', 'args': {'expectedRevision': 0, 'enabled': True, 'maxTotalTokens': 10}}}, sid)
        assert reply['result']['budget']['origin'] == 'agent'
        with pytest.raises(AppError, match='revision changed'):
            await app.app_bridge('dispatch', {'id': 'alias-stale', 'action': 'runtime.control', 'args': {'sessionId': sid, 'operation': 'capacity.set', 'args': {'expectedRevision': 0, 'maxTotalTokens': 100}}}, sid)
        with pytest.raises(AppError, match='Unknown action'):
            await app.app_bridge('dispatch', {'action': 'runtime.control', 'args': {'sessionId': sid, 'operation': 'capacity.admit', 'args': {}}}, sid)
    finally:
        await worker.close(); await app.close()
