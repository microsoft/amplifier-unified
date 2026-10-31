"""Bounded regression checks for the reviewed multi-worker defects."""
from amplifier_web import test_execution_settings as execution_settings

import asyncio
import json
from pathlib import Path

import pytest

from test_collaborative_workspaces import app, agent_action, declared_result, finish, settled, generation
from test_worktrees import fixture as worktree_fixture, settle
from amplifier_web.service import AppError


@pytest.mark.parametrize('kind,outcome', [('result', 'failed'), ('defer', 'deferred'), ('decline', 'declined')])
@pytest.mark.parametrize('subscribe_after', [False, True])
async def test_negative_reply_wakes_once_without_satisfying_dependency(app, kind, outcome, subscribe_after):
    source, target = app.state['sessions']
    source['status'] = 'working'
    await declared_result(app, kind=kind, outcome=outcome)
    if subscribe_after:
        await finish(app, target, 'result-request')
    wait = await agent_action(app, source, 'coordination.subscribe',
        {'sessionId': source['id'], 'requestId': 'result-request'}, 'wait')
    if not subscribe_after:
        await finish(app, target, 'result-request')
    request = app.collaboration.receipt('result-request')
    assert request['response']['status'] == 'sealed'
    assert request['response']['qualified'] is False
    assert request['subscription']['status'] == 'claimed'
    identity = wait['result']['continuationId']
    continuation = app.collaboration.receipt(identity)
    assert continuation['dependencySatisfied'] is False
    assert continuation['continuationReason'] == outcome
    assert app.collaboration.pending_continuation(source['id'])['requestId'] == identity
    message = next(m for m in source['messages'] if m.get('inputId') == identity)
    assert 'does not satisfy the dependency or authorize a retry' in message['text']
    await finish(app, target, 'result-request')
    await agent_action(app, source, 'coordination.subscribe',
        {'sessionId': source['id'], 'requestId': 'result-request'}, 'wait-again')
    assert app.db.execute('SELECT count(*) FROM commands WHERE id=?', (identity,)).fetchone()[0] == 1


@pytest.mark.parametrize('subscribe_after', [False, True])
async def test_runtime_failure_without_model_reply_notifies_exact_waiter(app, subscribe_after):
    source, target = app.state['sessions']
    source['status'] = 'working'
    request = 'failed-before-reply'
    await agent_action(app, source, 'coordination.send', {'sessionId': target['id'], 'text': 'Inspect'}, request)
    await settled(app, request, 'accepted')
    await generation(app, target, 'recipient-' + request, [request])
    if subscribe_after:
        await finish(app, target, request, event='generation.failed')
    wait = await agent_action(app, source, 'coordination.subscribe',
        {'sessionId': source['id'], 'requestId': request}, 'failure-wait')
    if not subscribe_after:
        await finish(app, target, request, event='generation.failed')
    retained = app.collaboration.receipt(request)
    assert not retained.get('response')
    assert retained['terminalOutcome']['qualified'] is False
    identity = wait['result']['continuationId']
    assert app.collaboration.receipt(identity)['dependencySatisfied'] is False
    assert app.collaboration.pending_continuation(source['id'])['requestId'] == identity
    await finish(app, target, request, event='generation.failed')
    assert app.db.execute('SELECT count(*) FROM commands WHERE id=?', (identity,)).fetchone()[0] == 1


async def test_new_peer_keeps_actual_checkout_and_canonical_history_home(app, tmp_path):
    source = app.state['sessions'][0]
    execution = tmp_path / 'separate-checkout'
    execution.mkdir()
    source['workingDirectory'] = str(execution)
    directory = app.data_dir / 'sessions' / source['id']
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'effective-configuration.json').write_text(json.dumps({'providers': [], 'tools': []}))
    made = await agent_action(app, source, 'coordination.create',
        {'title': 'Peer', 'text': 'Inspect the assigned files'}, 'create-peer')
    await settled(app, 'create-peer', 'created')
    target = app._session(made['sessionId'])
    assert target['workingDirectory'] == str(execution)
    assert target['workspace'] == source['workspace']
    assert target['collaboration']['workingDirectory'] == str(execution)
    assert source['workingDirectory'] == str(execution)


async def test_full_inbox_preserves_accepted_inputs_and_duplicate_receipts(app, monkeypatch):
    monkeypatch.setitem(execution_settings.CONCURRENCY_DEFAULTS, 'maxPendingPeerInputs', 2)
    source, target = app.state['sessions']
    target['status'] = 'working'
    args = {'sessionId': target['id'], 'text': 'Inspect once'}
    await agent_action(app, source, 'coordination.send', args, 'one')
    await agent_action(app, source, 'coordination.send', args, 'two')
    before = len(target['messages'])
    with pytest.raises(AppError, match='inbox is full') as error:
        await agent_action(app, source, 'coordination.send', args, 'three')
    assert error.value.status == 429 and len(target['messages']) == before
    duplicate = await agent_action(app, source, 'coordination.send', args, 'one')
    assert duplicate['duplicate'] and len(target['messages']) == before
    # The guard is destination-scoped, not a project-wide checkout lock.
    peer = app._new_session({'title': 'Unrelated peer'})
    peer['status'] = 'working'
    app.state['sessions'].append(peer)
    accepted = await agent_action(app, source, 'coordination.send', {**args, 'sessionId': peer['id']}, 'other')
    assert accepted['accepted']


async def test_notify_bursts_are_bounded_without_waking_receiver(app, monkeypatch):
    monkeypatch.setattr('amplifier_web.collaboration.MAX_PAIR_MESSAGES_PER_MINUTE', 1)
    source, target = app.state['sessions']
    args = {'sessionId': target['id'], 'text': 'Observation', 'mode': 'notify'}
    await agent_action(app, source, 'coordination.send', args, 'notice')
    before = len(target['messages'])
    with pytest.raises(AppError, match='Too many messages'):
        await agent_action(app, source, 'coordination.send', args, 'flood')
    assert len(target['messages']) == before and not app.runtime.inputs
    assert (await agent_action(app, source, 'coordination.send', args, 'notice'))['duplicate']


async def test_saved_wait_reserves_outcome_slot_and_old_stop_releases_reservation(app, monkeypatch):
    monkeypatch.setitem(execution_settings.CONCURRENCY_DEFAULTS, 'maxPendingPeerInputs', 1)
    source, target = app.state['sessions']
    # A successful declaration awaits terminal evidence. Negative reports now
    # legitimately exchange this reservation for an immediate queued notice.
    await declared_result(app)
    await agent_action(app, source, 'coordination.subscribe',
        {'sessionId': source['id'], 'requestId': 'result-request'}, 'wait')
    with pytest.raises(AppError, match='reserved replies'):
        app.collaboration.require_inbox_slot(source['id'])
    source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    app.collaboration.require_inbox_slot(source['id'])
    await finish(app, target, 'result-request')
    retained = app.collaboration.receipt('result-request')
    identity = retained['subscription']['continuationId']
    assert retained['subscription']['status'] == 'suppressed'
    assert retained['subscription']['open'] is False
    assert retained['response']['status'] == 'sealed'
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone()
    assert not any(message.get('inputId') == identity for message in source['messages'])
    app.collaboration.require_inbox_slot(source['id'])


@pytest.mark.parametrize('assignment', [False, True])
async def test_actual_stop_releases_queued_notice_before_worker_interrupt_returns(app, monkeypatch, assignment):
    monkeypatch.setitem(execution_settings.CONCURRENCY_DEFAULTS, 'maxPendingPeerInputs', 1)
    source, target = app.state['sessions']
    if assignment:
        await app.dispatch('assignment.start', {'sessionId': source['id']})
    await declared_result(app, outcome='failed')
    wait = await agent_action(app, source, 'coordination.subscribe',
        {'sessionId': source['id'], 'requestId': 'result-request'}, 'wait-negative')
    identity = wait['result']['continuationId']
    assert app.collaboration.receipt(identity)['delivery'] == 'queued'
    with pytest.raises(AppError, match='reserved replies'):
        app.collaboration.require_inbox_slot(source['id'])
    released = asyncio.Event()
    async def held_stop(_):
        await released.wait()
    monkeypatch.setattr(app.runtime, 'stop', held_stop)
    try:
        if assignment:
            value = app.assignments.current(source)
            await app.dispatch('assignment.stop', {'sessionId': source['id'], 'expectedRevision': value['revision']})
        else:
            await app.dispatch('conversation.stop', {'sessionId': source['id']})
        await settled(app, identity, 'suppressed')
        assert source['status'] == 'stopping'
        app.collaboration.require_inbox_slot(source['id'])
        assert len(app.runtime.inputs) == 1  # Only the recipient's original request.
    finally:
        released.set()


@pytest.mark.parametrize('restart', [False, True])
async def test_preflight_failure_leaves_source_usable_without_release(tmp_path, monkeypatch, restart):
    from amplifier_web.service import AppService
    app, runtime, root, sid = await worktree_fixture(tmp_path, monkeypatch)
    try:
        await app.dispatch('worktree.inspect', {'sessionId': sid})
        revision = app._session(sid)['worktreeInspection']['repository']['sourceRevision']
        await app.dispatch('worktree.create', {'sessionId': sid, 'sourceRevision': revision})
        record = next(r for r in app.worktrees.git.records() if r['sessionId'] == sid)
        def reject(*args):
            raise ValueError('Target preflight rejected')
        monkeypatch.setattr('amplifier_web.worktrees.common', reject)
        await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': record['id'], 'expectedExecutionRevision': 0})
        await settle(app)
        if restart:
            receipt = app.worktrees.receipts(sid)[0]
            receipt.update(phase='pending', step='preflight')
            app.worktrees.save(receipt)
            app._session(sid)['configurationBusy'] = True
            app._publish()
            await app.close()
            app = AppService(tmp_path / 'app', runtime, workspace=root)
        receipt = app.worktrees.receipts(sid)[0]
        assert receipt['phase'] == 'rejected'
        assert app.worktrees.operation_shape(receipt)['state'] == 'failed'
        assert app.worktrees.execution_state(sid)['fenced'] is False
        assert app._session(sid)['configurationBusy'] is False
        assert app._session(sid).get('workingDirectory', str(root)) == str(root)
        runtime.quiesce_for_handoff.assert_not_awaited()
    finally:
        await app.close()
