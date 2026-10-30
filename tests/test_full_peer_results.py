"""Full observers read exact receipts without becoming their participants."""
import asyncio
import copy
import json
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_web.service import AppError
from test_access_mode import save
from test_collaborative_workspaces import app, agent_action, finish, generation
from test_peer_creation_operation import commission, initial_accepted, plan
from test_result_artifacts import setup, declare, read


async def observer(app, *, workspace=None):
    row = app._new_session({'title': 'Independent observer', **({'workspace': str(workspace)} if workspace else {})})
    app.state['sessions'].append(row)
    await generation(app, row, 'observer-generation', ['observer-input'])
    app._publish_changes(sessions={row['id']})
    return row


async def result(app, reader, request='artifact-request', **args):
    return (await agent_action(app, reader, 'coordination.result',
        {'requestId': request, **args}, 'inspect-result'))['result']


@pytest.mark.parametrize('scope', ['session', 'workspace', 'app'])
async def test_full_observer_reads_exact_results_without_changing_owners(app, tmp_path, scope):
    source, peer, file, args = await setup(app, tmp_path)
    declared = (await declare(app, peer, args))['result']
    reader = await observer(app)
    with pytest.raises(AppError, match='request and workspace'):
        await result(app, reader)
    with pytest.raises(AppError, match='request and workspace'):
        await read(app, reader, args)
    await save(app, reader, scope=scope)
    before = copy.deepcopy(app.collaboration.receipt(args['requestId']))
    output = app.outputs.store.read(declared['artifacts'][0]['outputId'])
    saved_output = copy.deepcopy(output)
    observed = await result(app, reader)
    assert observed['receipt'] == before and not observed['qualified']
    assert (await read(app, reader, args, limit=8))['text'] == 'Original'
    assert app.collaboration.receipt(args['requestId']) == before
    assert app.outputs.store.read(output['id']) == saved_output
    assert output['sessionId'] == peer['id'] != reader['id']
    assert observed['receipt']['senderSessionId'] == source['id']
    assert observed['receipt']['target']['sessionId'] == peer['id']
    assert not reader.get('assignmentId') and not reader.get('approvals')
    await finish(app, peer, args['requestId'])
    file.unlink()
    assert (await result(app, reader))['qualified']
    assert (await read(app, reader, args))['text'] == 'Original exact bytes'


async def test_full_observer_explicit_scope_and_ask_participants(app, tmp_path):
    source, peer, _, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    elsewhere = tmp_path / 'observer-workspace'
    elsewhere.mkdir()
    reader = await observer(app, workspace=elsewhere)
    await save(app, reader)
    with pytest.raises(AppError, match='scope=all'):
        await result(app, reader)
    with pytest.raises(AppError, match='scope=all'):
        await read(app, reader, args)
    assert (await result(app, reader, scope='all'))['receipt']['requestId'] == args['requestId']
    assert (await read(app, reader, args, scope='all'))['text'] == 'Original exact bytes'
    await save(app, reader, mode='ask')
    with pytest.raises(AppError):
        await result(app, reader, scope='all')
    assert (await read(app, source, args))['text'] == 'Original exact bytes'
    assert (await read(app, peer, args))['text'] == 'Original exact bytes'


async def test_full_observer_reads_cold_registered_results_without_history_or_admission(app, tmp_path, monkeypatch):
    source, peer, _, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    await finish(app, peer, args['requestId'])
    reader = await observer(app)
    await save(app, reader)
    app._publish_changes(sessions={source['id'], peer['id'], reader['id']})
    app.state['sessions'] = [reader]
    with monkeypatch.context() as patch:
        patch.setattr(app.history, 'ensure_loaded', AsyncMock(side_effect=AssertionError('No transcript loading')))
        patch.setattr(app.cold_display, 'hydrate', Mock(side_effect=AssertionError('No cold-field hydration')))
        patch.setattr(app, '_admit_session', Mock(side_effect=AssertionError('No dormant owner admission')))
        before = app.db.execute('SELECT id,receipt FROM commands ORDER BY id').fetchall()
        assert (await result(app, reader))['qualified']
        assert (await read(app, reader, args))['text'] == 'Original exact bytes'
        assert app.db.execute('SELECT id,receipt FROM commands ORDER BY id').fetchall() == before
        assert [row['id'] for row in app.state['sessions']] == [reader['id']]


@pytest.mark.parametrize('participant', ['sender', 'recipient'])
async def test_full_observer_cannot_rebind_original_locations(app, tmp_path, participant):
    source, peer, _, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    reader = await observer(app)
    await save(app, reader)
    (source if participant == 'sender' else peer)['workspace'] += '/moved'
    with pytest.raises(AppError, match='workspace scope'):
        await result(app, reader, scope='all')
    with pytest.raises(AppError, match='workspace scope'):
        await read(app, reader, args, scope='all')


@pytest.mark.parametrize('fault', ['owner', 'hash', 'provenance', 'body'])
async def test_full_observer_never_substitutes_forged_or_missing_artifacts(app, tmp_path, fault):
    _, peer, file, args = await setup(app, tmp_path)
    declared = (await declare(app, peer, args))['result']
    reader = await observer(app)
    await save(app, reader)
    output = app.outputs.store.read(declared['artifacts'][0]['outputId'])
    if fault == 'body':
        app.db.execute('DELETE FROM state_resources WHERE id=?', (output['body']['$resource'],))
    else:
        if fault == 'owner':
            output['sessionId'] = reader['id']
        elif fault == 'hash':
            output['sha256'] = 'a' * 64
        else:
            output['collaborationResult']['requestId'] = 'different-request'
        app.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(output), output['id']))
    app.db.commit()
    observed = await read(app, reader, args)
    assert observed['status'] == 'unavailable' and 'text' not in observed
    assert file.read_bytes() == b'Original exact bytes'
    with pytest.raises(AppError, match='exact artifact'):
        await read(app, reader, {**args, 'references': ['unregistered.md@sha256:' + 'a' * 64]})


async def test_observer_revocation_is_rechecked_after_retained_io(app, tmp_path, monkeypatch):
    source, peer, _, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    reader = await observer(app)
    await save(app, reader)
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.outputs.content_async
    async def delayed(record):
        entered.set()
        await release.wait()
        return await original(record)
    monkeypatch.setattr(app.outputs, 'content_async', delayed)
    pending = asyncio.create_task(read(app, reader, args))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await save(app, reader, mode='ask')
    finally:
        release.set()
    with pytest.raises(AppError, match='request and workspace'):
        await pending
    assert (await read(app, source, args))['text'] == 'Original exact bytes'


async def test_observation_does_not_impersonate_reply_or_consume_sender_subscription(app, tmp_path):
    source, peer, _, args = await setup(app, tmp_path)
    await agent_action(app, source, 'coordination.subscribe',
        {'sessionId': source['id'], 'requestId': args['requestId']}, 'sender-subscription')
    await declare(app, peer, args)
    reader = await observer(app)
    await save(app, reader)
    before = copy.deepcopy(app.collaboration.receipt(args['requestId']))
    await result(app, reader)
    await read(app, reader, args)
    for action, values in (
        ('coordination.reply', args),
        ('coordination.subscribe', {'sessionId': reader['id'], 'requestId': args['requestId']}),
        ('coordination.acknowledge', {'sessionId': reader['id'], 'requestId': args['requestId'],
                                    'outcomeRevision': 1, 'reviewKey': 'not-the-requester'}),
        ('outputs.read', {'sessionId': peer['id'], 'id': before['response']['artifacts'][0]['outputId']}),
    ):
        with pytest.raises(AppError):
            await agent_action(app, reader, action, values, 'observer-' + action)
    assert app.collaboration.receipt(args['requestId']) == before
    assert before['subscription'] and before['senderSessionId'] == source['id']


async def test_full_observer_reads_commission_and_exact_initial_request(app):
    source = app.state['sessions'][0]
    plan(app, source)
    created = await commission(app, source)
    await initial_accepted(app, created['initialInputId'])
    reader = await observer(app)
    await save(app, reader)
    observed = await result(app, reader, created['requestId'])
    assert observed['receipt']['senderSessionId'] == source['id']
    assert observed['initialRequest']['requestId'] == created['initialInputId']
    assert observed['initialRequest']['target']['sessionId'] == created['sessionId']
