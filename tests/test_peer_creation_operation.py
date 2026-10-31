"""One commissioned-task handle, pre-admission returns and evidence-based recovery."""
from amplifier_web import test_execution_settings as execution_settings

import asyncio
import json
import sqlite3

import pytest

from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import app, Runtime, agent_action, finish, generation, settled, until


def plan(app, source):
    directory = app.data_dir / 'sessions' / source['id']
    directory.mkdir(parents=True, exist_ok=True)
    configuration = {'providers': [], 'tools': [], 'hooks': []}
    (directory / 'effective-configuration.json').write_text(json.dumps(configuration))
    return configuration


async def commission(app, source, identity='commission', **options):
    return await agent_action(app, source, 'coordination.create',
                              {'title': 'Reviewer', 'text': 'Check the candidate once', **options}, identity)


async def initial_accepted(app, identity):
    await until(lambda: app.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone())
    return await settled(app, identity, 'accepted')


async def test_initial_admission_observes_committed_wait_and_public_operation(app):
    source = app.state['sessions'][0]
    plan(app, source)
    original = app.runtime.collaboration_input
    async def observe(session, args, guard, emit):
        with sqlite3.connect(app.db.execute('PRAGMA database_list').fetchone()[2]) as db:
            operation = json.loads(db.execute("SELECT receipt FROM commands WHERE id='commission'").fetchone()[0])
            request = json.loads(db.execute('SELECT receipt FROM commands WHERE id=?', (args['inputId'],)).fetchone()[0])
            assert operation['phase'] == 'input_committed' and operation['delivery'] == 'created'
            assert operation['subscription']['status'] == 'linked'
            assert request['subscription']['status'] == 'waiting'
            assert request['subscription']['requestId'] == args['inputId']
            assert request['subscription']['continuationId'] == operation['subscription']['continuationId']
            assert not db.execute('SELECT 1 FROM collaboration_preparations').fetchone()
        return await original(session, args, guard, emit)
    app.runtime.collaboration_input = observe
    created = await commission(app, source)
    await initial_accepted(app, created['initialInputId'])
    result = await agent_action(app, source, 'coordination.result', {'requestId': created['requestId']}, 'inspect')
    assert result['result']['initialRequest']['requestId'] == created['initialInputId']
    assert 'configuration' not in result['result']['receipt']
    repeated = await commission(app, source)
    assert repeated['duplicate'] and len(app.runtime.inputs) == 1


async def test_root_is_published_at_captured_placement_before_preparation_resumes(app, tmp_path, monkeypatch):
    source = app.state['sessions'][0]
    plan(app, source)
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    source['workingDirectory'] = str(checkout)
    original = app.dispatch
    async def observe(action, *args, **kwargs):
        result = await original(action, *args, **kwargs)
        if action == 'session.create':
            peer = app._session(result['sessionId'])
            assert peer['workingDirectory'] == str(checkout)
            assert peer['workspace'] == source['workspace']
        return result
    monkeypatch.setattr(app, 'dispatch', observe)
    created = await commission(app, source)
    await initial_accepted(app, created['initialInputId'])


@pytest.mark.parametrize('outcome', ['success', 'failed', 'declined'])
async def test_creation_handle_resolves_exact_outcome_and_returns_once(app, outcome):
    source = app.state['sessions'][0]
    plan(app, source)
    created = await commission(app, source)
    request = created['initialInputId']
    await initial_accepted(app, request)
    target = app._session(created['sessionId'])
    await generation(app, target, 'recipient-' + request, [request])
    await agent_action(app, target, 'coordination.reply', {
        'requestId': request, 'kind': 'decline' if outcome == 'declined' else 'result',
        'outcome': outcome, 'text': 'Observed result', 'references': ['urn:fixture:candidate'],
    }, 'reply')
    await finish(app, target, request)
    await finish(app, target, request)
    result = await agent_action(app, source, 'coordination.result', {'requestId': created['requestId']}, 'read')
    assert result['result']['qualified'] is (outcome == 'success')
    assert result['result']['initialRequest']['response']['outcome'] == outcome
    continuation = created['subscription']['continuationId']
    assert app.collaboration.pending_continuation(source['id'])['requestId'] == continuation
    assert app.collaboration.receipt(continuation)['dependencySatisfied'] is (outcome == 'success')
    assert len([row for row in source['messages'] if row.get('inputId') == continuation]) == 1


async def test_independent_creation_can_explicitly_omit_automatic_return(app):
    source = app.state['sessions'][0]
    plan(app, source)
    created = await commission(app, source, returnNotification=False)
    await initial_accepted(app, created['initialInputId'])
    assert 'subscription' not in created
    assert 'subscription' not in app.collaboration.receipt(created['initialInputId'])


@pytest.mark.parametrize('failure', ['unknown', 'not_sent', 'cancelled'])
async def test_pre_generation_admission_outcome_returns_without_model_reply(app, failure):
    from amplifier_web.runtime import RuntimePreparationCancelled
    source = app.state['sessions'][0]
    plan(app, source)
    async def unavailable(*args):
        if failure == 'unknown':
            raise ConnectionError('Lost admission acknowledgment')
        if failure == 'cancelled':
            raise RuntimePreparationCancelled()
        return {'accepted': False, 'delivery': 'not_sent', 'reason': 'No worker was admitted'}
    app.runtime.collaboration_input = unavailable
    created = await commission(app, source)
    request = created['initialInputId']
    await until(lambda: app.db.execute('SELECT 1 FROM commands WHERE id=?', (request,)).fetchone())
    await settled(app, request, 'unknown' if failure == 'unknown' else 'not_sent')
    receipt = app.collaboration.receipt(request)
    assert receipt['terminalOutcome']['qualified'] is False
    assert receipt['terminalOutcome']['kind'] == 'admission'
    assert receipt['subscription']['status'] == 'claimed'
    continuation = created['subscription']['continuationId']
    assert app.collaboration.pending_continuation(source['id'])['requestId'] == continuation
    assert app.collaboration.receipt(continuation)['dependencySatisfied'] is False
    assert not app.runtime.inputs


async def test_return_capacity_reserved_before_root_effect_and_transferred_once(app, monkeypatch):
    source = app.state['sessions'][0]
    plan(app, source)
    monkeypatch.setitem(execution_settings.CONCURRENCY_DEFAULTS, 'maxPendingPeerInputs', 1)
    release = asyncio.Event()
    original = app.collaboration._finish_create
    async def delayed(*args):
        await release.wait()
        return await original(*args)
    monkeypatch.setattr(app.collaboration, '_finish_create', delayed)
    try:
        created = await commission(app, source)
        assert len(app.state['sessions']) == 2
        assert (await commission(app, source))['duplicate']
        with pytest.raises(AppError, match='inbox is full'):
            await commission(app, source, 'second')
        assert not app.db.execute("SELECT 1 FROM commands WHERE id='second'").fetchone()
    finally:
        release.set()
    await initial_accepted(app, created['initialInputId'])
    with pytest.raises(AppError, match='inbox is full'):
        await commission(app, source, 'third')
    # Explicitly independent work consumes no return slot.
    independent = await commission(app, source, 'independent', returnNotification=False)
    await initial_accepted(app, independent['initialInputId'])


@pytest.mark.parametrize('phase', ['planned', 'creating_root', 'root_acknowledged', 'root_retained'])
@pytest.mark.parametrize('stop', [False, True])
async def test_restart_reconciles_only_proven_phases(app, monkeypatch, phase, stop):
    source = app.state['sessions'][0]
    original_plan = plan(app, source)
    entered, release = asyncio.Event(), asyncio.Event()
    original_finish, original_dispatch, original_send = app.collaboration._finish_create, app.dispatch, app.collaboration.dispatch
    async def suspend():
        entered.set()
        await release.wait()
    if phase == 'planned':
        async def delayed(*args):
            await suspend()
            return await original_finish(*args)
        monkeypatch.setattr(app.collaboration, '_finish_create', delayed)
    elif phase in {'creating_root', 'root_acknowledged'}:
        async def delayed(action, *args, **kwargs):
            if action == 'session.create' and phase == 'creating_root':
                await suspend()
            result = await original_dispatch(action, *args, **kwargs)
            if action == 'session.create' and phase == 'root_acknowledged':
                await suspend()
            return result
        monkeypatch.setattr(app, 'dispatch', delayed)
    else:
        async def delayed(action, *args, **kwargs):
            if action == 'coordination.send':
                await suspend()
            return await original_send(action, *args, **kwargs)
        monkeypatch.setattr(app.collaboration, 'dispatch', delayed)
    created = await commission(app, source)
    await asyncio.wait_for(entered.wait(), 5)
    # The live source config can change while a captured creation is pending.
    directory = app.data_dir / 'sessions' / source['id']
    (directory / 'effective-configuration.json').write_text(json.dumps({'tools': [{'module': 'different'}]}))
    if stop:
        source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    app._publish()
    await app.close()
    reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    reopened.runtime.app = reopened
    try:
        assert not reopened.runtime.inputs
        reopened.collaboration.start()
        reopened.collaboration.start()  # Repeated startup cannot duplicate a preparation.
        expected = 'suppressed' if stop else 'unknown' if phase == 'creating_root' else 'created'
        await settled(reopened, created['requestId'], expected)
        peer_inputs = [row for row in reopened.runtime.inputs if row[0] == created['sessionId']]
        if expected == 'created':
            await settled(reopened, created['initialInputId'], 'accepted')
            peer = reopened._session(created['sessionId'])
            assert len(peer['messages']) == 1
            assert json.loads((reopened.data_dir / 'sessions' / peer['id'] / 'configuration.json').read_text()) == original_plan
            assert len([row for row in reopened.runtime.inputs if row[0] == peer['id']]) == 1
        else:
            assert not peer_inputs
            assert not reopened.db.execute('SELECT 1 FROM commands WHERE id=?', (created['initialInputId'],)).fetchone()
            if not stop:
                retained = reopened.collaboration.receipt(created['requestId'])
                assert retained['terminalOutcome']['outcome'] == 'unknown'
                assert retained['subscription']['status'] == 'claimed'
    finally:
        await reopened.close()


async def test_lost_ack_after_input_commit_never_downgrades_or_repeats(app, monkeypatch):
    source = app.state['sessions'][0]
    plan(app, source)
    original = app.collaboration.dispatch
    async def lost_ack(action, *args, **kwargs):
        result = await original(action, *args, **kwargs)
        if action == 'coordination.send':
            raise ConnectionError('Lost receipt after commit')
        return result
    monkeypatch.setattr(app.collaboration, 'dispatch', lost_ack)
    created = await commission(app, source)
    await initial_accepted(app, created['initialInputId'])
    assert app.collaboration.receipt(created['requestId'])['delivery'] == 'created'
    await app.collaboration.finish_create(created['requestId'])
    assert len(app.runtime.inputs) == 1
