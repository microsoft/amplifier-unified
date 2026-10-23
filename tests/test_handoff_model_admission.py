"""A checkout move must let its exact originating turn reach the saved boundary."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_collaborative_workspaces import agent_action, generation, until
from test_handoff_boundary import wait_for_step
from test_handoff_refusal import checkout
from test_model_admission_wait import providers
from test_worktrees import fixture, settle


@pytest.fixture
async def app(tmp_path, monkeypatch):
    service, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    session = service._session(sid)
    await generation(service, session, 'originating-generation', ['originating-input'])
    await agent_action(service, session, 'assignment.start', {'sessionId': sid}, 'assignment')
    runtime.workers = {sid: {'process': SimpleNamespace(pid=100, returncode=None), 'started_at': 12.5}}
    runtime.collaboration_input = AsyncMock(return_value={'accepted': True})
    runtime.handoff_boundary = AsyncMock(return_value={'ready': False})
    yield service
    await service.close()


async def move(app):
    session = app._session()
    target = await checkout(app, session['id'])
    receipt = (await agent_action(app, session, 'worktree.handoff', {
        'sessionId': session['id'], 'id': target['id'],
        'expectedExecutionRevision': 0}, 'originating-move'))['result']
    await wait_for_step(app, session['id'], receipt['id'], 'waiting')
    return session, target, receipt


@pytest.mark.parametrize('prior_display_turn', [False, True])
async def test_originating_turn_finishes_before_checkout_release(app, providers, prior_display_turn):
    session, target, receipt = await move(app)
    source = session['workspace']
    provider = providers(session)
    if prior_display_turn:
        provider.telemetry.turn_id = 'earlier-user-turn'
    provider.release.set()
    await provider.start('finish-current-turn')
    assert provider.called == ['finish-current-turn']
    assert session['configurationBusy']
    assert app.worktrees.read(session['id'], receipt['id'])['step'] == 'waiting'
    app.runtime.quiesce_for_handoff.assert_not_awaited()
    assert session.get('workingDirectory', source) == source
    app.runtime.handoff_boundary.return_value = {'ready': True}
    await settle(app)
    await asyncio.gather(*list(app.tasks))
    saved = app.worktrees.read(session['id'], receipt['id'])
    assert saved['id'] == receipt['id'] and saved['phase'] == 'applied'
    assert saved['source'] == source and saved['inputsReplayed'] is False
    assert session['workingDirectory'] == target['path']
    app.runtime.quiesce_for_handoff.assert_awaited_once()
    app.runtime.collaboration_input.assert_awaited_once()
    assert app.assignments.usage(app.assignments.current(session))['calls'] == 1


@pytest.mark.parametrize('change', [
    'generation', 'input', 'stop', 'worker', 'missing-worker', 'worker-exited',
    'configuration-revision', 'configuration-pending', 'configuration-refresh',
    'configuration-operation', 'pending-configuration', 'file-access',
    'source-path', 'execution-revision', 'history-home', 'task', 'human-input',
    'unknown', 'releasing', 'released', 'manager', 'reconciliation',
    'legacy', 'missing-receipt', 'ambiguous', 'source-binding', 'child',
])
async def test_handoff_exception_rejects_unrelated_or_changed_authority(app, providers, change):
    session, target, receipt = await move(app)
    provider = providers(session, child_id='unrelated-child' if change == 'child' else None)
    if change == 'generation': session['collaborationGeneration']['id'] = 'later-generation'
    elif change == 'input': session['collaborationGeneration']['inputIds'].append('new-input')
    elif change == 'stop': session['interruptionRevision'] = 1
    elif change == 'worker': app.runtime.workers[session['id']]['started_at'] = 99
    elif change == 'missing-worker': app.runtime.workers.pop(session['id'])
    elif change == 'worker-exited': app.runtime.workers[session['id']]['process'].returncode = 0
    elif change == 'configuration-revision': session['configurationPendingRevision'] = 1
    elif change == 'configuration-pending': session['configurationPending'] = True
    elif change == 'configuration-refresh': session['configurationRefresh'] = {'phase': 'pending'}
    elif change == 'configuration-operation': session['configurationOperationFence'] = 'other-operation'
    elif change == 'pending-configuration': session['pendingConfiguration'] = {'phase': 'queued', 'operationId': 'other', 'requestHash': 'hash'}
    elif change == 'file-access': session['fileAccessPending'] = True
    elif change == 'source-path': session['workingDirectory'] = target['path']
    elif change == 'execution-revision': session['executionRevision'] = 1
    elif change == 'history-home': session['workspace'] = target['path']
    elif change == 'task': session['task'] = {'id': 'new-task', 'revision': 1}
    elif change == 'human-input': session['messages'].append({'id': 'later-human', 'role': 'user', 'inputOrigin': 'ui'})
    elif change == 'ambiguous': session['worktreeHandoffs'].append({**receipt, 'id': 'another-receipt'})
    elif change == 'missing-receipt': (app.worktrees.receipt_dir / (receipt['id'] + '.json')).unlink()
    elif change != 'child':
        current = app.worktrees.read(session['id'], receipt['id'])
        if change == 'unknown': current['phase'] = 'unknown'
        elif change == 'releasing': current['step'] = 'releasing'
        elif change == 'released': current['release'] = {'quiesced': True}
        elif change == 'manager': current['managerPlacement'] = {'sourceSessionId': 'manager'}
        elif change == 'reconciliation': current['reconciles'] = 'prior-unknown'
        elif change == 'legacy': current.pop('finishingTurn')
        elif change == 'source-binding': current['sourceBinding']['path'] = target['path']
        app.worktrees.save(current)
    provider.release.set()
    with pytest.raises(asyncio.CancelledError):
        await provider.start('must-stay-fenced')
    assert provider.called == []
    app.runtime.quiesce_for_handoff.assert_not_awaited()
    assert app.assignments.usage(app.assignments.current(session))['calls'] == 0


async def test_own_handoff_does_not_bypass_model_budget(app, providers):
    from test_assignments import limits
    session, _, receipt = await move(app)
    await limits(app, session, maxTotalTokens=1, reserveTokens=2)
    provider = providers(session)
    provider.release.set()
    # The ordinary assignment budget remains the owner of model admission.
    with pytest.raises(ValueError, match='insufficient unreserved'):
        await provider.start('budget-limited')
    assert not provider.called
    assert app.worktrees.read(session['id'], receipt['id'])['phase'] == 'pending'
    app.runtime.quiesce_for_handoff.assert_not_awaited()


async def test_graceful_stop_rejects_only_the_unperformed_move(app, providers):
    session, target, receipt = await move(app)
    source = session['workspace']
    session['artifactRefs'] = ['completed-evidence']
    manager = app._new_session({'title': 'Independent manager'})
    manager.update(status='working', collaborationGeneration={'id': 'manager-generation', 'terminal': False},
        artifactRefs=['manager-evidence'])
    app.state['sessions'].append(manager)
    retained_manager = copy.deepcopy(manager)
    app.runtime.stop = AsyncMock()
    await app.dispatch('conversation.stop', {'sessionId': session['id']}, command_id='human-stop')
    await settle(app)
    saved = app.worktrees.read(session['id'], receipt['id'])
    assert saved['phase'] == 'rejected' and 'user stopped' in saved['detail']
    assert not session['configurationBusy']
    assert session.get('workingDirectory', source) == source
    assert session.get('executionRevision', 0) == 0
    assert session['artifactRefs'] == ['completed-evidence']
    assert saved['inputsReplayed'] is False
    app.runtime.quiesce_for_handoff.assert_not_awaited()
    app.runtime.collaboration_input.assert_not_awaited()
    app.runtime.stop.assert_awaited_once_with(session['id'])
    assert manager == retained_manager
