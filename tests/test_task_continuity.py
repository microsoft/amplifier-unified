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


async def test_observation_epoch_survives_progress_but_fences_changed_authority(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        record = (await create(worker))['task']
        epoch = record['continuationEpoch']
        for changes in ({'artifactRefs': ['verified-report.md']},
                        {'constraints': ['Preserve originals', 'Review findings before publishing'],
                         'correction': 'Use the latest findings, do not replay the check'},
                        {'maxTurns': 2}):
            record = (await worker.perform('task.update', {'commandId': str(record['revision']),
                'expectedRevision': record['revision'], **changes}))['task']
            assert record['continuationEpoch'] == epoch
        restored = controls(); await restored.restore()
        assert restored.tasks.record()['continuationEpoch'] == epoch
        await restored.close()
        for operation, changes in [('task.update', {'objective': 'A different objective'}),
                                   ('task.pause', {}), ('task.resume', {}),
                                   ('task.block', {'reason': 'Waiting for a decision'}),
                                   ('task.resume', {}),
                                   ('task.complete', {'condition': 'A different objective', 'evidence': ['Finished']})]:
            record = (await worker.perform(operation, {'commandId': operation + str(record['revision']),
                'expectedRevision': record['revision'], **changes}))['task']
            assert record['continuationEpoch'] > epoch
            epoch = record['continuationEpoch']
    finally:
        await worker.close()


# Preparation-only contract candidates, intentionally not xfailed. New read DTOs
# need interface review before implementation. Never use real task data here.
TASK_CONTEXT_BYTES = 12 * 1024


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


async def corrected_task(worker, count=113):
    await create(worker, objective='Original promise: preserve the exact source.')
    for index in range(count):
        await worker.perform('task.update', {
            'commandId': f'correction-{index}',
            'expectedRevision': worker.tasks.record()['revision'],
            'correction': f'Historical correction {index}: ' + 'retain evidence; ' * 280,
        })
    await worker.perform('task.update', {
        'commandId': 'current-promise',
        'expectedRevision': worker.tasks.record()['revision'],
        'objective': 'Corrected promise: verify the source and retain earlier evidence.',
        'constraints': ['Do not publish.', 'Read current constraints before dependent work.'],
    })


@pytest.mark.parametrize('projection', ['get', 'boundary'])
async def test_large_correction_history_is_not_repeated_in_current_projection(
        tmp_path, monkeypatch, projection):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        await corrected_task(worker)
        original = copy.deepcopy(worker.tasks.record())
        if projection == 'get':
            text = json.dumps(await worker.perform('task.get'), ensure_ascii=False)
        else:
            hook = await worker.tasks.boundary('provider:request', {'session_id': 'task'})
            text = hook.context_injection
        assert len(text.encode('utf-8')) <= TASK_CONTEXT_BYTES
        assert 'Historical correction 0:' not in text
        assert 'Corrected promise: verify the source' in text
        assert 'Do not publish.' in text
        projected = await worker.perform('task.get')
        summary = projected['task']
        assert summary['correctionCount'] == 113
        assert summary['historyRef']['action'] == 'task.history'
        # "Old" is not a semantic supersession decision. The preceding update
        # did not explicitly fold correction-only constraints into current scope.
        assert projected['detailRequired'] is True
        assert any(ref['args']['field'] == 'corrections' for ref in projected['detailRefs'])
        assert worker.tasks.record()['corrections'] == original['corrections']
        assert worker.tasks.record()['objective'] == original['objective']
    finally:
        await worker.close()


async def test_exact_mutation_receipt_is_small_and_does_not_become_current_state(
        tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        await corrected_task(worker)
        revision = worker.tasks.record()['revision']
        args = {'commandId': 'saved-update', 'expectedRevision': revision,
                'artifactRefs': ['evidence:unchanged-candidate']}
        first = await worker.perform('task.update', args)
        assert encoded_size(first) <= TASK_CONTEXT_BYTES
        assert first['receipt']['commandId'] == 'saved-update'
        assert first['receipt']['revision'] == revision + 1
        await worker.perform('task.pause', {
            'commandId': 'new-pause', 'expectedRevision': revision + 1})
        replay = await worker.perform('task.update', args)
        assert replay['duplicate'] is True
        assert replay['receipt'] == first['receipt']
        assert worker.tasks.record()['status'] == 'paused'
        assert worker.tasks.record()['revision'] == revision + 2
        saved = await worker.perform('task.receipt', {'commandId': 'saved-update'})
        assert saved['receipt'] == first['receipt']
        assert encoded_size(saved) <= TASK_CONTEXT_BYTES
    finally:
        await worker.close()


async def test_failed_update_preserves_full_record_history_receipts_and_saved_bytes(
        tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        await corrected_task(worker, count=3)
        before = copy.deepcopy((worker.tasks.record(), worker.tasks.history,
                                worker.tasks.receipts, worker.coordinator.session_state['goal']))
        disk = worker.state_path().read_bytes()
        def fail(**kwargs):
            raise OSError('Synthetic write refusal')
        monkeypatch.setattr(worker, 'persist', fail)
        with pytest.raises(OSError, match='Synthetic write refusal'):
            await worker.perform('task.update', {
                'commandId': 'failed-update', 'expectedRevision': worker.tasks.record()['revision'],
                'objective': 'Must not replace the promise', 'correction': 'Must not land'})
        assert (worker.tasks.record(), worker.tasks.history, worker.tasks.receipts,
                worker.coordinator.session_state['goal']) == before
        assert worker.state_path().read_bytes() == disk
        assert 'failed-update' not in worker.tasks.receipts
    finally:
        await worker.close()


async def test_correction_history_pages_recover_every_exact_body_after_reopen(
        tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    first = controls()
    restored = None
    try:
        await corrected_task(first)
        expected = copy.deepcopy(first.tasks.record()['corrections'])
        identity = first.tasks.record()['id']
        restored = controls()
        await restored.restore()
        args = {'taskId': identity, 'limit': 20}
        found, cursors = [], set()
        for _ in range(10):
            page = await restored.perform('task.history', args)
            assert encoded_size(page) <= TASK_CONTEXT_BYTES
            assert len(page['items']) <= 20
            assert page['coverage'] != 'unknown'
            for row in page['items']:
                if row['kind'] != 'correction':
                    continue
                assert 'text' not in row
                ref = row['detailRef']
                text, detail_args = '', dict(ref['args'])
                for _ in range(10):
                    detail = await restored.perform(ref['action'], detail_args)
                    assert encoded_size(detail) <= TASK_CONTEXT_BYTES
                    text += detail['text']
                    if detail['nextOffset'] is None:
                        break
                    detail_args['offset'] = detail['nextOffset']
                else:
                    pytest.fail('Correction detail made no bounded progress')
                found.append((row['revision'], text))
            if page['nextAfter'] is None:
                break
            assert page['nextAfter'] not in cursors
            cursors.add(page['nextAfter'])
            args['after'] = page['nextAfter']
        else:
            pytest.fail('History made no bounded progress')
        assert found == [(row['revision'], row['text']) for row in expected]
        assert restored.tasks.record()['corrections'] == expected
        assert restored.tasks.record()['status'] == 'active'
    finally:
        await first.close()
        if restored is not None:
            await restored.close()


async def test_oversized_current_constraints_require_exact_paged_detail(
        tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        constraints = [f'Constraint {index}: ' + '\u754c' * 3900 for index in range(100)]
        await create(worker, constraints=constraints)
        task = worker.tasks.record()
        summary = await worker.perform('task.get')
        assert encoded_size(summary) <= TASK_CONTEXT_BYTES
        assert summary['detailRequired'] is True
        ref = next(ref for ref in summary['detailRefs'] if ref['args']['field'] == 'constraints')
        assert ref['args']['expectedRevision'] == task['revision']
        pieces, args = [], dict(ref['args'])
        for _ in range(500):
            page = await worker.perform(ref['action'], args)
            assert encoded_size(page) <= TASK_CONTEXT_BYTES
            assert page['revision'] == task['revision']
            pieces.append(page['text'])
            if page['nextOffset'] is None:
                break
            assert page['nextOffset'] > args.get('offset', 0)
            args['offset'] = page['nextOffset']
        else:
            pytest.fail('Current constraints cannot be read completely through bounded pages')
        assert json.loads(''.join(pieces)) == constraints
        old_args = dict(ref['args'])
        await worker.perform('task.update', {
            'commandId': 'new-constraints', 'expectedRevision': task['revision'],
            'constraints': ['New constraint: no dependent effects.']})
        with pytest.raises(ValueError, match='revision|changed|stale'):
            await worker.perform(ref['action'], old_args)
        assert worker.tasks.record()['constraints'] == ['New constraint: no dependent effects.']
    finally:
        await worker.close()


async def test_original_and_corrected_objectives_remain_distinct_after_reopen(
        tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    first = controls()
    restored = None
    try:
        await corrected_task(first, count=2)
        identity = first.tasks.record()['id']
        restored = controls()
        await restored.restore()
        original = await restored.perform('task.detail', {
            'taskId': identity, 'expectedRevision': restored.tasks.record()['revision'],
            'field': 'originalObjective'})
        assert original['text'] == 'Original promise: preserve the exact source.'
        assert restored.tasks.record()['objective'] == (
            'Corrected promise: verify the source and retain earlier evidence.')
        assert len(restored.tasks.record()['corrections']) == 2
    finally:
        await first.close()
        if restored is not None:
            await restored.close()


async def test_delayed_completion_cannot_close_corrected_paused_task(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        await create(worker)
        late = {'commandId': 'late-worker-completion', 'expectedRevision': 1,
                'condition': worker.tasks.record()['objective'], 'evidence': ['Old worker return']}
        await worker.perform('task.update', {
            'commandId': 'correction', 'expectedRevision': 1,
            'objective': 'Current corrected promise', 'constraints': ['Do not run old work.']})
        await worker.perform('task.pause', {'commandId': 'stop', 'expectedRevision': 2})
        before = copy.deepcopy(worker.tasks.record())
        with pytest.raises(ValueError, match='revision changed'):
            await worker.perform('task.complete', late)
        assert worker.tasks.record() == before
        assert not await worker.tasks.continuation_allowed()
        assert 'late-worker-completion' not in worker.tasks.receipts
    finally:
        await worker.close()


async def test_saved_task_get_is_cold_and_does_not_return_the_app_snapshot(
        tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'home'))
    app = AppService(tmp_path / 'app', None, workspace=tmp_path)
    worker = None
    try:
        await app.dispatch('session.create', {'title': 'Cold task'})
        sid = app._session()['id']
        worker = controls(sid)
        await create(worker)
        await worker.perform('task.pause', {'commandId': 'pause', 'expectedRevision': 1})
        await worker.close()
        worker = None
        app.runtime = SimpleNamespace(
            control=AsyncMock(side_effect=AssertionError('No worker control on a cold read')),
            close=AsyncMock())
        app.management = SimpleNamespace(
            ensure_runtime=AsyncMock(side_effect=AssertionError('No worker warming')),
            close=AsyncMock())
        monkeypatch.setattr(app.history, 'ensure_loaded',
                            AsyncMock(side_effect=AssertionError('No transcript hydration')))
        monkeypatch.setattr(app, 'browser_state',
                            lambda *a, **kw: pytest.fail('No full app snapshot'))
        result = await app.dispatch('task.get', {'sessionId': sid}, include_state=True)
        assert result['result']['task']['status'] == 'paused'
        assert result['result']['task']['objective'] == 'Ship the verified report'
        assert encoded_size(result) <= TASK_CONTEXT_BYTES
        assert 'sessions' not in result.get('state', {})
        app.runtime.control.assert_not_called()
        app.management.ensure_runtime.assert_not_called()
    finally:
        app.management = None
        await app.close()
        if worker is not None:
            await worker.close()


async def test_mutation_dispatch_acknowledges_without_serializing_app_state(
        tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'home'))
    app = AppService(tmp_path / 'app', None, workspace=tmp_path)
    worker = None
    try:
        await app.dispatch('session.create', {'title': 'Mutation target'})
        sid = app._session()['id']
        worker = controls(sid)
        await corrected_task(worker)
        async def control(target, operation, args):
            assert target == sid
            return await worker.perform(operation, args)
        app.runtime = SimpleNamespace(control=control, close=AsyncMock())
        app.management = SimpleNamespace(ensure_runtime=AsyncMock(), close=AsyncMock())
        monkeypatch.setattr(app, 'browser_state',
                            lambda *a, **kw: pytest.fail('Mutation must not serialize app state'))
        revision = worker.tasks.record()['revision']
        result = await app.dispatch('task.update', {
            'sessionId': sid, 'expectedRevision': revision,
            'artifactRefs': ['exact-evidence-ref']},
            command_id='compact-mutation', include_state=True)
        assert result['accepted'] is True
        assert result['result']['receipt']['commandId'] == 'compact-mutation'
        assert result['result']['receipt']['revision'] == revision + 1
        assert encoded_size(result) <= TASK_CONTEXT_BYTES
        assert 'sessions' not in result.get('state', {})
        assert len(worker.tasks.record()['corrections']) == 113
    finally:
        app.management = None
        await app.close()
        if worker is not None:
            await worker.close()


async def test_task_history_cursor_keeps_its_high_water_when_a_correction_arrives(
        tmp_path, monkeypatch):
    """Old history remains readable; it cannot masquerade as current scope."""
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        await corrected_task(worker, count=21)
        task_id = worker.tasks.record()['id']
        revision = worker.tasks.record()['revision']
        args = {'taskId': task_id, 'limit': 20}
        first = await worker.perform('task.history', args)
        assert first['snapshotRevision'] == revision
        assert first['currentRevision'] == revision
        assert first['snapshotIsCurrent'] is True
        assert first['nextAfter']
        await worker.perform('task.update', {
            'commandId': 'late-history-append', 'expectedRevision': revision,
            'correction': 'Late restriction: do not execute the old candidate.'})
        page = await worker.perform('task.history', {**args, 'after': first['nextAfter']})
        assert page['snapshotRevision'] == revision
        assert page['currentRevision'] == revision + 1
        assert page['snapshotIsCurrent'] is False
        assert all(row['revision'] <= revision for row in page['items'])
        assert encoded_size(page) <= TASK_CONTEXT_BYTES
        current = await worker.perform('task.get')
        assert current['task']['revision'] == revision + 1
        assert current['detailRequired'] is True
        assert len(worker.tasks.record()['corrections']) == 22
    finally:
        await worker.close()


async def test_history_cursor_cannot_be_rebound_to_a_replacement_task(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        await corrected_task(worker, count=21)
        old = copy.deepcopy(worker.tasks.record())
        page = await worker.perform('task.history', {'taskId': old['id'], 'limit': 20})
        assert page['nextAfter']
        await worker.perform('task.complete', {
            'commandId': 'explicit-completion', 'expectedRevision': old['revision'],
            'condition': old['objective'], 'evidence': ['Synthetic retained check']})
        await create(worker, commandId='next-task',
                     expectedRevision=worker.tasks.record()['revision'],
                     objective='Separate next objective')
        current = copy.deepcopy(worker.tasks.record())
        with pytest.raises(ValueError, match='cursor|scope|task|changed'):
            await worker.perform('task.history', {
                'taskId': current['id'], 'limit': 20, 'after': page['nextAfter']})
        assert worker.tasks.record() == current
        assert worker.tasks.history[0]['corrections'] == old['corrections']
    finally:
        await worker.close()
