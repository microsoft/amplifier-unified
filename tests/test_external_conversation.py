import asyncio
import json
from pathlib import Path

import pytest

from amplifier_web.service import AppError
from amplifier_web.session_projection import view_path
from test_acp_participant import operations, peer
from test_automatic_history import app_factory


async def until(predicate):
    async with asyncio.timeout(10):
        while not predicate():
            await asyncio.sleep(.01)


async def create(app, tmp_path):
    app.server_config = {'externalAgents': [peer(tmp_path)]}
    receipt = await app.dispatch('session.create', {'workspace': app.default_workspace,
                                'externalAgentId': 'fixture'}, command_id='create-external')
    return app._session(receipt['result']['sessionId'])


async def send(app, session, text, identity):
    receipt = await app.dispatch('conversation.send', {'sessionId': session['id'], 'text': text}, command_id=identity)
    assert receipt['accepted'] and receipt['delivery'] == 'sending'
    await until(lambda: session['id'] not in app.external_conversations.turns)
    return receipt


async def test_host_transcript_identity_rename_pin_and_same_remote_resume_after_restart(app_factory, tmp_path):
    app = app_factory()
    session = await create(app, tmp_path)
    sid = session['id']
    await send(app, session, 'first', 'first-external')
    remote = session['externalAgent']['sessionId']
    assert remote != sid and not {'runtimeSessionId', 'nativeProject', 'nativeIdentity'} & session.keys()
    assert [row['text'] for row in session['messages']] == ['first', 'Reply: first']
    assert session['messages'][1]['source'] == 'external'
    assert app.runtime.sent == [] and app.runtime.started == []
    for projection in (app.browser_state(sid), app.session_state(sid)):
        public = next(row for row in projection['sessions'] if row['id'] == sid)
        assert public['externalAgent']['sessionId'] == remote
        assert '_externalAgentSource' not in public
    assert app.directory.resolve(sid)['externalAgent']['id'] == 'fixture'
    assert view_path(app.data_dir, session).is_relative_to(app.data_dir / 'external-conversations')
    assert not list((Path(app.default_workspace) / '.amplifier').glob('**/' + remote))
    await app.dispatch('session.rename', {'id': sid, 'title': 'External project chat'})
    await app.dispatch('session.pin', {'id': sid, 'pinned': True})
    await app.close()

    reopened = app_factory()
    reopened.server_config = {'externalAgents': [peer(tmp_path)]}
    restored = reopened._session(sid)
    assert restored['title'] == 'External project chat'
    assert restored['externalAgent']['sessionId'] == remote
    assert [row['text'] for row in restored['messages']] == ['first', 'Reply: first']
    assert [row['kind'] for row in operations(tmp_path)] == ['new', 'prompt']
    await send(reopened, restored, 'second', 'second-external')
    assert [row['kind'] for row in operations(tmp_path)] == ['new', 'prompt', 'resume', 'prompt']
    assert restored['externalAgent']['sessionId'] == remote
    duplicate = await reopened.dispatch('conversation.send', {'sessionId': sid, 'text': 'second'}, command_id='second-external')
    assert duplicate['duplicate'] and duplicate['delivery'] == 'accepted'
    assert len([row for row in operations(tmp_path) if row['kind'] == 'prompt']) == 2


async def test_stop_uses_remote_cancel_preserves_partial_text_and_allows_next_turn(app_factory, tmp_path):
    app = app_factory()
    session = await create(app, tmp_path)
    result = await app.dispatch('conversation.send', {'sessionId': session['id'], 'text': 'wait-for-stop'}, command_id='wait-external')
    assert result['delivery'] == 'sending' and session['status'] == 'working'
    await until(lambda: bool(session.get('streaming')))
    await app.dispatch('conversation.stop', {'sessionId': session['id']}, command_id='stop-external')
    await until(lambda: session['id'] not in app.external_conversations.turns)
    assert session['externalAgent']['stopReason'] == 'cancelled'
    assert session['status'] == 'stopped'
    assert session['messages'][-1]['text'] == 'Reply: '
    assert app.runtime.stopped == []
    await send(app, session, 'after-stop', 'after-stop-external')
    assert session['status'] == 'idle' and session['messages'][-1]['text'] == 'Reply: after-stop'


async def test_lost_reply_remains_unknown_and_new_explicit_input_does_not_replay(app_factory, tmp_path):
    app = app_factory()
    session = await create(app, tmp_path)
    await send(app, session, 'drop-after-effect', 'lost-external')
    old = session['messages'][0]
    assert old['delivery']['status'] == 'unknown'
    assert session['externalAgent']['status'] == 'unknown'
    duplicate = await app.dispatch('conversation.send', {'sessionId': session['id'], 'text': 'drop-after-effect'}, command_id='lost-external')
    assert duplicate['duplicate'] and duplicate['delivery'] == 'unknown'
    await send(app, session, 'new explicit input', 'new-external')
    assert old['delivery']['status'] == 'unknown'
    assert [row['text'] for row in operations(tmp_path) if row['kind'] == 'prompt'] == ['drop-after-effect', 'new explicit input']
    status = (await app.dispatch('conversation.delivery', {'sessionId': session['id'], 'inputId': 'lost-external'}))['result']
    assert status['delivery'] == 'unknown' and not status['resent']
    assert status['sessionId'] == session['id']
    assert status['source']['externalAgentId'] == 'fixture' and 'nativeIdentity' not in status['source']


async def test_external_full_uses_existing_access_choice_without_native_remount(app_factory, tmp_path):
    app = app_factory()
    session = await create(app, tmp_path)
    await send(app, session, 'permission', 'ask-external')
    assert [row for row in operations(tmp_path) if row['kind'] == 'permission'][-1]['outcome']['outcome'] == 'cancelled'
    report = (await app.dispatch('access.get', {'sessionId': session['id']}))['result']
    await app.dispatch('access.set', {'sessionId': session['id'], 'scope': 'session',
                       'mode': 'full', 'expectedRevision': report['revision']}, command_id='full-external')
    assert app.access_mode.full(session)
    assert not session.get('fileAccessPending') and not session.get('configurationPending')
    await send(app, session, 'permission', 'full-permission-external')
    outcome = [row for row in operations(tmp_path) if row['kind'] == 'permission'][-1]['outcome']
    assert outcome == {'outcome': 'selected', 'optionId': 'allow-this'}
    assert app.runtime.started == [] and app.runtime.sent == []


async def test_unsupported_native_controls_and_changed_endpoint_do_not_execute(app_factory, tmp_path):
    app = app_factory()
    session = await create(app, tmp_path)
    with pytest.raises(AppError, match='not supported'):
        await app.dispatch('session.fork', {'id': session['id']})
    app.server_config['externalAgents'][0]['args'].append('--changed')
    with pytest.raises(AppError, match='endpoint changed'):
        await app.dispatch('conversation.send', {'sessionId': session['id'], 'text': 'must not run'}, command_id='changed-external')
    assert not session['messages']
    assert not (tmp_path / 'peer/operations.jsonl').exists()


async def test_stop_fences_admitted_message_before_connection_and_overlapping_input(app_factory, tmp_path, monkeypatch):
    app = app_factory()
    session = await create(app, tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = app._send

    async def held(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(app, '_send', held)
    sending = asyncio.create_task(app.dispatch('conversation.send',
        {'sessionId': session['id'], 'text': 'stopped before launch'}, command_id='fenced-external'))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(AppError, match='still working'):
            await app.dispatch('conversation.send', {'sessionId': session['id'], 'text': 'overlap'}, command_id='overlap-external')
        await app.dispatch('conversation.stop', {'sessionId': session['id']}, command_id='fence-stop')
        release.set()
        receipt = await sending
        assert receipt['delivery'] == 'failed'
        assert session['status'] == 'stopped'
        assert [row['text'] for row in session['messages']] == ['stopped before launch']
        assert session['messages'][0]['delivery']['status'] == 'failed'
        assert not (tmp_path / 'peer/operations.jsonl').exists()
    finally:
        release.set()
        await asyncio.gather(sending, return_exceptions=True)


async def test_explicit_configuration_target_is_not_replaced_by_selected_chat(app_factory, tmp_path):
    app = app_factory()
    created = await app.dispatch('session.create', {'workspace': app.default_workspace}, command_id='native-control-create')
    native = app._session(created['result']['sessionId'])
    external = await create(app, tmp_path)
    # The ordinary native request reaches its existing unavailable-manager path.
    with pytest.raises(AppError, match='Management service is unavailable'):
        await app.dispatch('configuration.inspect', {'id': native['id']})
    app.state['selectedSessionId'] = native['id']
    with pytest.raises(AppError, match='not supported'):
        await app.dispatch('configuration.inspect', {'id': external['id']})
    app.state['selectedSessionId'] = external['id']
    assert (await app.dispatch('call.end', {}, include_state=False))['accepted']


async def test_durable_stop_revokes_full_permission_before_async_cancellation(app_factory, tmp_path, monkeypatch):
    from acp.schema import PermissionOption
    app = app_factory()
    session = await create(app, tmp_path)
    report = (await app.dispatch('access.get', {'sessionId': session['id']}))['result']
    await app.dispatch('access.set', {'sessionId': session['id'], 'scope': 'session',
                       'mode': 'full', 'expectedRevision': report['revision']})
    await app.dispatch('conversation.send', {'sessionId': session['id'], 'text': 'wait-for-stop'}, command_id='permission-stop-work')
    await until(lambda: bool(session.get('streaming')))
    entered, release = asyncio.Event(), asyncio.Event()
    original = app._stop

    async def held(sid):
        entered.set()
        await release.wait()
        await original(sid)

    monkeypatch.setattr(app, '_stop', held)
    stopping = asyncio.create_task(app.dispatch('conversation.stop', {'sessionId': session['id']}))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        result = await app.external_conversations._permission(session['id'],
            [PermissionOption(option_id='allow-this', name='Allow', kind='allow_once')])
        assert result == {'outcome': {'outcome': 'cancelled'}}
    finally:
        release.set()
        await stopping


async def test_stop_settles_when_cancelled_task_did_not_enter_turn_owner(app_factory, tmp_path, monkeypatch):
    app = app_factory()
    session = await create(app, tmp_path)
    entered = asyncio.Event()

    async def not_started(*_args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(app.external_conversations, '_run', not_started)
    await app.dispatch('conversation.send', {'sessionId': session['id'], 'text': 'cancel before connect'}, command_id='cancel-new-turn')
    await asyncio.wait_for(entered.wait(), 5)
    await app.dispatch('conversation.stop', {'sessionId': session['id']}, command_id='cancel-new-turn-stop')
    await until(lambda: session['id'] not in app.external_conversations.turns)
    assert session['status'] == 'stopped'
    assert session['messages'][0]['delivery']['status'] == 'failed'
    assert session['id'] not in app.external_conversations.turns
    assert not (tmp_path / 'peer/operations.jsonl').exists()
