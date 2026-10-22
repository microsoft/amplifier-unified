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


async def test_reaction_commits_and_publishes_while_generation_is_blocked(app):
    import asyncio
    import json
    from amplifier_web.session_projection import view_path

    args = {**target(app, 1), 'emoji': '\u2764\ufe0f', 'present': True}
    session = app._session()
    originals = copy.deepcopy(session['messages'])
    entered, release = asyncio.Event(), asyncio.Event()
    original_send = app.runtime.send

    async def blocked(*values):
        entered.set()
        await release.wait()
        return await original_send(*values)

    app.runtime.send = blocked
    send = asyncio.create_task(call(app, 'conversation.send',
        {'sessionId': session['id'], 'text': 'Synthetic blocked generation'},
        command_id='blocked-generation'))
    subscriber = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        starts = list(app.runtime.started)
        with app.clients.bind('two'):
            subscriber = app.subscribe()
        receipt = await asyncio.wait_for(call(app, 'message.reaction', args,
            command_id='reaction-during-generation'), 3)
        assert receipt['accepted'] and receipt['result']['sent'] is False
        assert not send.done() and not release.is_set()
        assert app.runtime.started == starts and app.runtime.sent == []
        assert session['messages'][:len(originals)] == originals
        saved = json.loads(view_path(app.data_dir, session).read_text())
        assert saved['messageAnnotations'][args['messageId']]['reactions'] == ['\u2764\ufe0f']
        snapshot = await asyncio.wait_for(subscriber.get(), 3)
        owner = next(row for row in snapshot['sessions'] if row['id'] == session['id'])
        message = next(row for row in owner['messages'] if row['id'] == args['messageId'])
        assert message['reactions'] == ['\u2764\ufe0f']
    finally:
        if subscriber is not None:
            app.unsubscribe(subscriber)
        release.set()
        await send


async def test_passive_reaction_receipt_is_exact_and_never_replays(app):
    from amplifier_web.message_interactions import reaction_status
    args = {**target(app), 'emoji': '\u2764\ufe0f', 'present': True}
    read = {**args, 'commandId': 'receipt-only', 'clientId': 'one'}
    assert reaction_status(app, read)['status'] == 'unknown'
    receipt = await call(app, 'message.reaction', args, command_id='receipt-only')
    await call(app, 'message.reaction', {**args, 'present': False}, client='two')
    before = app.state['revision']
    value = reaction_status(app, read)
    assert value['status'] == 'accepted' and value['receipt']['result'] == receipt['result']
    assert value['current']['reactions'] == [] and value['revision'] > receipt['result']['annotationRevision']
    assert app.state['revision'] == before and not app.runtime.sent
    for changed in [{'present': False}, {'messageId': 'other'}, {'clientId': 'two'}, {'emoji': '\u2705'}]:
        with pytest.raises(AppError, match='does not belong'):
            reaction_status(app, {**read, **changed})
    unavailable = {**args, 'messageId': 'missing'}
    with pytest.raises(AppError):
        await call(app, 'message.reaction', unavailable, command_id='rejected-reaction')
    rejected = reaction_status(app, {**unavailable, 'commandId': 'rejected-reaction', 'clientId': 'one'})
    assert rejected['status'] == 'rejected' and rejected['receipt']['accepted'] is False
    assert rejected['receipt']['status'] == 404
    duplicate = await call(app, 'message.reaction', unavailable, command_id='rejected-reaction')
    assert duplicate['duplicate'] and duplicate['accepted'] is False
    assert not app.runtime.sent


async def test_reaction_reads_never_return_dirty_annotations_after_a_failed_save(app, monkeypatch):
    from amplifier_web.message_interactions import reaction_status
    args = {**target(app), 'emoji': '\u2764\ufe0f', 'present': True}
    await call(app, 'message.reaction', args, command_id='saved-reaction')
    original_save = app._save
    def fail_save():
        raise OSError('Synthetic storage failure')
    monkeypatch.setattr(app, '_save', fail_save)
    with pytest.raises(OSError):
        await call(app, 'message.reaction', {**args, 'present': False}, command_id='unsaved-reaction')
    read = {**args, 'commandId': 'saved-reaction', 'clientId': 'one'}
    try:
        async with app.lock:
            with pytest.raises(OSError):
                reaction_status(app, read)
        with pytest.raises(OSError):
            await app.app_bridge('message.reactions.context', {}, args['sessionId'])
        assert app._progress_dirty
    finally:
        monkeypatch.setattr(app, '_save', original_save)
    commit = app._commit_pending_progress
    def locked_commit():
        assert app.lock.locked(), 'Flush and snapshot must share the same lock'
        return commit()
    monkeypatch.setattr(app, '_commit_pending_progress', locked_commit)
    context = await app.app_bridge('message.reactions.context', {}, args['sessionId'])
    assert not app._progress_dirty and context['items'][-1]['reactions'] == []
    async with app.lock:
        assert reaction_status(app, read)['current']['reactions'] == []


async def test_archived_stopped_message_reaction_does_not_resume_or_restore_chat(app):
    args = {**target(app), 'emoji': '\u2705', 'present': True}
    session = app._session()
    await call(app, 'session.archive', {'id': session['id']})
    session['interruptionRevision'] = 7
    organization = copy.deepcopy(app._state['conversationOrganization'])
    receipt = await call(app, 'message.reaction', args)
    assert receipt['accepted'] and receipt['result']['sent'] is False
    assert session['interruptionRevision'] == 7
    assert app._state['conversationOrganization'] == organization
    assert not app.runtime.sent and not app.runtime.started
    session['messages'] = [row for row in session['messages'] if row['id'] != args['messageId']]
    with pytest.raises(AppError, match='not available'):
        await call(app, 'message.reaction', {**args, 'present': False})
    assert session['messageAnnotations'][args['messageId']]['reactions'] == ['\u2705']


async def test_reaction_context_is_caller_bound_bounded_and_passive(app):
    args = {**target(app), 'emoji': '\u2705', 'present': True}
    await call(app, 'message.reaction', args)
    before = copy.deepcopy(app._session()['messages'])
    context = await app.app_bridge('message.reactions.context', {}, args['sessionId'])
    assert context['sessionId'] == args['sessionId']
    assert context['items'][0]['messageId'] == args['messageId']
    assert context['items'][0]['reactions'] == ['\u2705']
    assert app._session()['messages'] == before and not app.runtime.sent
    from amplifier_web.message_interactions import reaction_context
    class MetadataOnly(dict):
        def get(self, key, *args):
            assert key != 'messages', 'Do not trigger lazy transcript hydration'
            return super().get(key, *args)
    bounded = reaction_context(MetadataOnly(id='s', messageAnnotations={
        f'm{index}': {'reactions': ['\u2705']} for index in range(1000)}))
    assert len(bounded['items']) == 32 and bounded['omittedMessageCount'] == 968
    session = app._session()
    first = session['messages'][0]
    for index in range(40):
        row = app._message(session, 'assistant', f'Reaction target {index}')
        await call(app, 'message.reaction', {'sessionId': session['id'], 'messageId': row['id'],
                                           'emoji': '\u2705', 'present': True})
    await call(app, 'message.reaction', {**target(app), 'emoji': '\u2764\ufe0f', 'present': True})
    context = reaction_context(session)
    assert context['items'][-1]['messageId'] == first['id']
    assert '\u2764\ufe0f' in context['items'][-1]['reactions']
    await call(app, 'message.reaction', {**target(app), 'emoji': '\u2764\ufe0f', 'present': False})
    assert '\u2764\ufe0f' not in reaction_context(session)['items'][-1]['reactions']


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
        assert app._session()['messageAnnotations'][args['messageId']]['assistantReactions'] == ['🎉']
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


async def test_bounded_annotations_read_has_explicit_removals_and_never_reads_bodies(app):
    from amplifier_web.message_interactions import read_annotations
    session = app._session()
    before = copy.deepcopy(session['messages'])
    identity = before[0]['id']
    first = await call(app, 'message.reaction', {**target(app), 'emoji': '❤️', 'present': True})
    second = await call(app, 'message.reaction', {**target(app), 'emoji': '❤️', 'present': False})
    assert second['result']['annotationRevision'] > first['result']['annotationRevision']
    class MetadataOnly(dict):
        def get(self, key, *args):
            assert key != 'messages', 'Annotation read must never access transcript bodies'
            return super().get(key, *args)
    result = read_annotations(MetadataOnly(session), [identity, 'not-annotated'])
    assert result == {'sessionId': session['id'], 'revision': second['result']['annotationRevision'],
                      'items': {identity: {'reactions': [], 'assistantReactions': []}, 'not-annotated': {'reactions': [], 'assistantReactions': []}}}
    result['items'][identity]['reactions'].append('👍')
    assert session['messageAnnotations'][identity]['reactions'] == []
    assert session['messages'] == before and app.runtime.sent == []
    for invalid in [[], ['same', 'same'], ['x'] * 101, [1], ['x' * 201], 'x']:
        with pytest.raises(ValueError):
            read_annotations(session, invalid)


async def test_repeated_reaction_desired_state_does_not_invalidate_annotations(app):
    first = await call(app, 'message.reaction', {**target(app), 'emoji': '❤️', 'present': True})
    repeated = await call(app, 'message.reaction', {**target(app), 'emoji': '❤️', 'present': True})
    assert repeated['result']['annotationRevision'] == first['result']['annotationRevision']
    assert repeated['result']['reactions'] == ['❤️']


async def test_annotation_route_requires_exact_owner_and_avoids_hydration():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from amplifier_web.message_interactions import install_routes
    router = SimpleNamespace(add_post=Mock())
    service = SimpleNamespace(_session=Mock(return_value={'id': 'owner'}))
    app = {'service': service}
    class Routes(dict):
        pass
    routed = Routes(app)
    routed.router = router
    install_routes(routed)
    handler = next(call.args[1] for call in router.add_post.call_args_list
                   if call.args[0] == '/api/conversation/annotations')
    for args in [{}, {'sessionId': ''}, {'sessionId': None}, []]:
        with pytest.raises(ValueError, match='explicit conversation'):
            await handler(SimpleNamespace(json=AsyncMock(return_value=args)))
    service._session.assert_not_called()
    response = await handler(SimpleNamespace(json=AsyncMock(return_value={'sessionId': 'owner', 'messageIds': ['one']})))
    service._session.assert_called_once_with('owner', hydrate=False)
    assert response.status == 200 and response.headers['Cache-Control'] == 'no-store'


async def test_assistant_reactions_have_independent_ownership_and_survive_restart(app):
    from amplifier_web.message_interactions import read_annotations
    args = {**target(app), 'emoji': '👍', 'present': True}
    original = copy.deepcopy(app._session()['messages'])
    agent = await app.app_bridge('dispatch', {'action': 'message.reaction', 'args': args, 'id': 'agent-thumbs'}, args['sessionId'])
    assert agent['result']['reactionActor'] == 'assistant'
    replay = await app.app_bridge('dispatch', {'action': 'message.reaction', 'args': args, 'id': 'agent-thumbs'}, args['sessionId'])
    assert replay['duplicate']
    repeated = await app.app_bridge('dispatch', {'action': 'message.reaction', 'args': args}, args['sessionId'])
    assert repeated['result']['annotationRevision'] == agent['result']['annotationRevision']
    user = await call(app, 'message.reaction', args)
    assert user['result']['reactionActor'] == 'user'
    await call(app, 'message.reaction', {**args, 'present': False})
    annotation = read_annotations(app._session(), [args['messageId']])['items'][args['messageId']]
    assert annotation == {'reactions': [], 'assistantReactions': ['👍']}
    await call(app, 'message.reaction', {**args, 'emoji': '❤️'})
    await app.app_bridge('dispatch', {'action': 'message.reaction', 'args': {**args, 'present': False}}, args['sessionId'])
    assert read_annotations(app._session(), [args['messageId']])['items'][args['messageId']] == {'reactions': ['❤️'], 'assistantReactions': []}
    await app.app_bridge('dispatch', {'action': 'message.reaction', 'args': args}, args['sessionId'])
    assert app._session()['messages'] == original and not app.runtime.sent and not app.runtime.started
    directory, workspace = app.data_dir, app._session()['workspace']
    app._save(); await app.close(); app._test_closed = True
    reopened = AppService(directory, Runtime(), workspace=workspace)
    try:
        saved = reopened._session(args['sessionId'])
        assert read_annotations(saved, [args['messageId']])['items'][args['messageId']] == {'reactions': ['❤️'], 'assistantReactions': ['👍']}
        assert page(saved, 'messages')['items'][0]['assistantReactions'] == ['👍']
    finally:
        await reopened.close()


@pytest.mark.parametrize('origin,via,role', [('agent','chat','user'), ('peer','peer','user'), ('scheduler','schedule','user'), (None,'chat','assistant')])
async def test_assistant_reactions_reject_nonhuman_messages(app, origin, via, role):
    session = app._session()
    row = app._message(session, role, 'Not a human request', via, inputOrigin=origin)
    with pytest.raises(AppError, match='only to a human message'):
        await app.app_bridge('dispatch', {'action':'message.reaction','args':{'sessionId':session['id'],'messageId':row['id'],'emoji':'👍','present':True}}, session['id'])
    assert row['id'] not in session.get('messageAnnotations', {})
    assert not app.runtime.sent


async def test_reaction_actor_cannot_be_supplied_in_action_arguments(app):
    with pytest.raises(AppError):
        await call(app, 'message.reaction', {**target(app), 'emoji':'👍','present':True,'actor':'assistant'})
    assert not app._session().get('messageAnnotations')
