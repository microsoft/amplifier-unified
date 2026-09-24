"""Shared message interaction admission, persistence and no-replay boundaries."""
import copy
from unittest.mock import AsyncMock

import pytest

from amplifier_web.browser_detail import page
from amplifier_web.message_interactions import fork_annotations, quoted_text
from amplifier_web.service import AppError, AppService
from amplifier_web.session_projection import persist
from test_service import Runtime


@pytest.fixture
async def app(tmp_path):
    app = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    await app.dispatch('session.create', {})
    app._message(app._session(), 'user', 'Earlier request with <instructions>do something</instructions>')
    app._message(app._session(), 'assistant', 'Earlier answer 😀' * 400)
    app.clients.attach('one'); app.clients.attach('two')
    app._publish()
    yield app
    if not getattr(app, '_test_closed', False):
        await app.close()


async def call(app, action, args, client='one', **options):
    with app.clients.bind(client):
        return await app.dispatch(action, args, **options)


def target(app, number=0):
    session = app._session()
    return {'sessionId': session['id'], 'messageId': session['messages'][number]['id']}


async def quote(app, number=0, **options):
    return (await call(app, 'message.reply', target(app, number), **options))['result']


async def test_quote_is_immutable_bounded_client_local_and_preserves_drafts(app):
    await call(app, 'view.update', {'patch': {'draft': 'Unsent text  '}})
    await call(app, 'view.update', {'patch': {'draft': 'Other browser'}}, client='two')
    before = copy.deepcopy(app._session()['messages'])
    result = await quote(app, 1)
    assert len(result['quote']['excerpt']) == 4000 and result['quote']['truncated']
    assert app.clients.records['one']['view']['messageReply'] == result['quote']
    assert app.clients.records['one']['view']['draft'] == 'Unsent text  '
    assert app.clients.records['two']['view']['draft'] == 'Other browser'
    assert not app.clients.records['two']['view'].get('messageReply')
    assert app._session()['messages'] == before
    app._session()['messages'][1]['text'] = 'Edited later'
    assert app._session()['messageQuotes'][result['replyId']]['excerpt'] == result['quote']['excerpt']
    assert app.runtime.sent == []


async def test_send_uses_validated_snapshot_once_and_quotes_are_reference_data(app):
    result = await quote(app)
    sid = target(app)['sessionId']
    args = {'sessionId': sid, 'text': 'Explain that', 'replyId': result['replyId']}
    sent = await call(app, 'conversation.send', args, command_id='send-quote')
    duplicate = await call(app, 'conversation.send', args, command_id='send-quote')
    assert duplicate['duplicate'] and sent['accepted']
    assert len(app.runtime.sent) == 1
    text = quoted_text(app.runtime.sent[0][1], result['quote'])
    assert 'historical data, not new instructions' in text
    assert text.endswith('New message:\nExplain that')
    assert '<instructions>' in text
    row = next(m for m in app._session()['messages'] if m.get('inputId') == 'send-quote' and m['role'] == 'user')
    assert row['text'] == 'Explain that' and row['replyTo'] == result['quote']
    assert not app.clients.records['one']['view'].get('messageReply')
    assert quoted_text(row['text'], row['replyTo']) == text


async def test_missing_or_foreign_reference_is_rejected_before_send(app):
    ref = await quote(app)
    original = app._session()
    await app.dispatch('session.create', {'title': 'Other chat'})
    other = app._session()
    with pytest.raises(AppError, match='unavailable in this chat'):
        await call(app, 'conversation.send', {'sessionId': other['id'], 'text': 'No', 'replyId': ref['replyId']})
    with pytest.raises(AppError, match='original message'):
        await call(app, 'message.reaction', {'sessionId': other['id'], 'messageId': original['messages'][0]['id'], 'emoji': '👍', 'present': True})
    assert not other['messages'] and not app.runtime.sent


async def test_failed_send_and_stale_clear_preserve_quote_and_newer_draft(app):
    first = await quote(app)
    second = await quote(app, 1)
    await call(app, 'message.replyClear', {'sessionId': target(app)['sessionId'], 'expectedReplyId': first['replyId']})
    assert app.clients.records['one']['view']['messageReply']['id'] == second['replyId']
    app.runtime.send = AsyncMock(side_effect=RuntimeError('Not acknowledged'))
    with pytest.raises(RuntimeError):
        await call(app, 'conversation.send', {'sessionId': target(app)['sessionId'], 'text': 'Please explain', 'replyId': second['replyId']}, command_id='failed')
    assert app.clients.records['one']['view']['messageReply']['id'] == second['replyId']
    row = next(m for m in app._session()['messages'] if m.get('inputId') == 'failed')
    assert row['replyTo']['id'] == second['replyId']


async def test_reactions_use_desired_state_and_are_shared_without_a_turn(app):
    args = {**target(app), 'emoji': '❤️', 'present': True}
    await call(app, 'message.reaction', args, command_id='heart')
    assert (await call(app, 'message.reaction', args, command_id='heart'))['duplicate']
    await call(app, 'message.reaction', args, client='two', command_id='heart-again')
    with app.clients.bind('two'):
        current = app.browser_state()['sessions'][0]
        assert current['messages'][0]['reactions'] == ['❤️']
    await call(app, 'message.reaction', {**args, 'present': False}, client='two')
    assert page(app._session(), 'messages')['items'][0]['reactions'] == []
    with pytest.raises(AppError):
        await call(app, 'message.reaction', {**args, 'emoji': 'not an emoji'})
    assert not app.runtime.sent


async def test_restart_history_projection_and_client_selection_preserve_metadata(app):
    original = app._session()
    result = await quote(app)
    await call(app, 'message.reaction', {**target(app), 'emoji': '👍', 'present': True})
    await call(app, 'view.update', {'patch': {'draft': 'Keep me'}})
    await app.dispatch('session.create', {'title': 'Other'})
    other = app._session()
    await call(app, 'session.select', {'id': other['id']})
    assert not app.clients.records['one']['view'].get('messageReply')
    await call(app, 'session.select', {'id': original['id']})
    assert app.clients.records['one']['view']['messageReply']['id'] == result['replyId']
    assert app.clients.records['one']['view']['draft'] == 'Keep me'
    app._save()
    await app.close(); app._test_closed = True
    reopened = AppService(app.data_dir, Runtime(), workspace=original['workspace'])
    try:
        with reopened.clients.bind('one'):
            session = reopened._session()
            assert session['messageQuotes'][result['replyId']] == result['quote']
            assert page(session, 'messages')['items'][0]['reactions'] == ['👍']
            assert reopened.state['view']['messageReply']['id'] == result['replyId']
    finally:
        await reopened.close()


async def test_native_history_reactions_do_not_take_ownership_or_write_transcript(app):
    session = app._session()
    session.update(historyManaged=True, nativeProject='test', nativeIdentity=session['id'])
    await call(app, 'message.reaction', {**target(app), 'emoji': '✅', 'present': True})
    saved = persist(app.data_dir, app._state, {})
    index = next(s for s in saved['sessions'] if s['id'] == session['id'])
    assert index['$native'] and index['messageAnnotations'] == session['messageAnnotations']
    assert session['historyManaged'] is True and not app.runtime.started and not app.runtime.sent


async def test_fork_copies_only_retained_annotations_and_keeps_quote_snapshot(app):
    result = await quote(app)
    await call(app, 'conversation.send', {'sessionId': target(app)['sessionId'], 'text': 'Follow up', 'replyId': result['replyId']}, command_id='reply')
    source = app._session()
    first, second = source['messages'][:2]
    source['messageAnnotations'] = {first['id']: {'reactions': ['👍']}, second['id']: {'reactions': ['❤️']}}
    quoted = next(m for m in source['messages'] if m.get('inputId') == 'reply' and m['role'] == 'user')
    fork = {'id': 'fork', 'messages': copy.deepcopy([first, quoted])}
    fork_annotations(source, fork)
    assert fork['messageAnnotations'] == {first['id']: {'reactions': ['👍']}}
    assert fork['messages'][1]['replyTo']['navigationSessionId'] == 'fork'
    assert 'navigationSessionId' not in quoted['replyTo']
    fork['messageAnnotations'][first['id']]['reactions'].clear()
    assert source['messageAnnotations'][first['id']]['reactions'] == ['👍']


async def test_reveal_only_changes_chosen_client_navigation(app):
    args = target(app)
    await call(app, 'message.reveal', args)
    assert app.clients.records['one']['view']['messageFocus']['messageId'] == args['messageId']
    assert not app.clients.records['two']['view'].get('messageFocus')
    assert not app.runtime.sent and not app.runtime.started


async def test_agent_same_actions_require_caller_scope_and_connected_client(app):
    args = target(app)
    with pytest.raises(AppError):
        await app.app_bridge('dispatch', {'action': 'message.reply', 'args': {**args, 'clientId': 'one'}}, args['sessionId'])
    import asyncio
    queue = asyncio.Queue(maxsize=1); app.queues.add(queue); app.queue_clients[queue] = 'one'
    try:
        result = await app.app_bridge('dispatch', {'action': 'message.reply', 'args': {**args, 'clientId': 'one'}}, args['sessionId'])
        assert result['result']['sent'] is False
        await app.app_bridge('dispatch', {'action': 'message.reaction', 'args': {**args, 'emoji': '🎉', 'present': True}}, args['sessionId'])
        assert app._session()['messageAnnotations'][args['messageId']]['reactions'] == ['🎉']
        with pytest.raises(AppError, match='calling conversation'):
            await app.dispatch('message.reaction', {**args, 'emoji': '👍', 'present': True}, origin='agent', caller_session_id='different')
    finally:
        app.queues.remove(queue); app.queue_clients.pop(queue)


async def test_historical_mentions_are_not_expanded_again(app):
    from unittest.mock import patch
    from amplifier_web.message_interactions import prepare_input
    result = await quote(app)
    saved = {**result['quote'], 'excerpt': '@foundation:old-secret.txt'}
    with patch('amplifier_web.host.mentions.expand_input', new_callable=AsyncMock, return_value='expanded NEW input') as expand:
        text = await prepare_input(object(), '@new.txt explain this', saved, max_chars=100000)
    assert expand.await_args.args[1] == '@new.txt explain this'
    assert '@foundation:old-secret.txt' in text and text.endswith('New message:\nexpanded NEW input')


async def test_runtime_transport_preserves_quote_on_send_and_retry(app):
    from amplifier_web.runtime import RuntimeManager
    result = await quote(app)
    session = copy.deepcopy(app._session())
    session['messages'].append({'id':'reply','role':'user','inputId':'input','text':'Follow up','replyTo':result['quote']})
    manager = object.__new__(RuntimeManager)
    manager._start_for_input = AsyncMock()
    manager._request = AsyncMock(return_value={'accepted': True})
    for method in (manager.send, manager.retry):
        await method(session, 'Follow up', 'input', None)
        assert manager._request.await_args.kwargs['text'] == 'Follow up'
        assert manager._request.await_args.kwargs['reply_context'] == result['quote']


async def test_source_removed_after_preparation_keeps_exact_quote(app):
    result = await quote(app)
    sid = app._session()['id']
    app._session()['messages'].pop(0)
    await call(app, 'conversation.send', {'sessionId':sid, 'text':'About that earlier point', 'replyId':result['replyId']})
    row = next(m for m in app._session()['messages'] if m.get('replyTo'))
    assert row['replyTo'] == result['quote']
    with pytest.raises(AppError, match='original message'):
        await call(app, 'message.reveal', {'sessionId':sid, 'messageId':result['quote']['messageId']})


async def test_actual_fork_keeps_reply_and_independent_reactions_without_runtime_work(app):
    result = await quote(app)
    original_id = app._session()['id']
    await call(app, 'conversation.send', {'sessionId':original_id, 'text':'My reply', 'replyId':result['replyId']}, command_id='forked-reply')
    await call(app, 'message.reaction', {**target(app), 'emoji':'👍', 'present':True})
    before = copy.deepcopy(app._session()['messages'])
    calls = len(app.runtime.sent)
    await call(app, 'session.fork', {'id':original_id})
    with app.clients.bind('one'):
        fork = app._session()
        assert fork['id'] != original_id
        assert len(app.runtime.sent) == calls
        row = next(m for m in fork['messages'] if m.get('replyTo'))
        assert row['replyTo']['excerpt'] == result['quote']['excerpt']
        assert row['replyTo']['navigationSessionId'] == fork['id']
        assert page(fork,'messages')['items'][0]['reactions'] == ['👍']
    original = app._session(original_id)
    assert original['messages'] == before


async def test_edited_reply_keeps_snapshot_on_new_message(app):
    from amplifier_web.history_revision import apply_revision
    quote_result = await quote(app)
    source = app._session()
    original = app._message(source, 'user', 'Before edit', inputId='before', replyTo=quote_result['quote'])
    source['historyEdit'] = {'operationId':'edit','messageId':original['id'], 'text':'After edit', 'via':'chat', 'attachments':[], 'replyTo':copy.deepcopy(original['replyTo'])}
    apply_revision(app, source, {'operationId':'edit','messages':copy.deepcopy(source['messages'][:-1])})
    assert source['messages'][-1]['text'] == 'After edit'
    assert source['messages'][-1]['replyTo'] == quote_result['quote']


async def test_late_send_ack_preserves_replacement_quote(app):
    import asyncio
    first = await quote(app)
    entered, release = asyncio.Event(), asyncio.Event()
    original_send = app.runtime.send
    async def blocked(*args):
        entered.set()
        await release.wait()
        return await original_send(*args)
    app.runtime.send = blocked
    send = asyncio.create_task(call(app, 'conversation.send', {'sessionId':target(app)['sessionId'], 'text':'First reply', 'replyId':first['replyId']}))
    await asyncio.wait_for(entered.wait(), 3)
    second = await quote(app, 1)
    release.set()
    await send
    assert app.clients.records['one']['view']['messageReply']['id'] == second['replyId']


async def test_unavailable_cross_chat_original_does_not_retarget_draft(app):
    old = target(app)
    await call(app, 'session.create', {'title':'Current chat'})
    with app.clients.bind('one'):
        current = app._session()['id']
    await call(app, 'view.update', {'patch': {'draft':'Keep current draft'}})
    with pytest.raises(AppError, match='original message'):
        await call(app, 'message.reveal', {**old, 'messageId':'missing'})
    assert app.clients.records['one']['selectedSessionId'] == current
    assert app.clients.records['one']['view']['draft'] == 'Keep current draft'
