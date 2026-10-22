"""Exact, additive references: synthetic host contract, no model or provider."""
import copy
import hashlib
from unittest.mock import AsyncMock, patch

import pytest

from test_message_interactions import app, call, target


def document(*segments):
    return {'version': 1, 'segments': list(segments)}


def words(text):
    return {'type': 'text', 'text': text}


def reference(quote, identity):
    return {'type': 'quote', 'instanceId': identity, 'quote': quote}


async def snapshot(app, number=0, **extra):
    return (await call(app, 'message.reply', {**target(app, number), 'snapshotOnly': True, **extra}))['result']['quote']


async def test_exact_passages_and_whole_snapshots_do_not_replace_the_draft(app):
    text = '  café e\u0301 東京\n\n• one\n• two\n\n    code\tvalue\n  '
    app._session()['messages'][0]['text'] = text
    await call(app, 'view.update', {'patch': {'draft': 'before  \n'}})
    source = hashlib.sha256(text.encode()).hexdigest()
    first = await snapshot(app, selection={'sourceDigest': source, 'start': 0, 'end': 14})
    second = await snapshot(app, selection={'sourceDigest': source, 'start': 18, 'end': len(text)})
    whole = await snapshot(app)
    repeated = await snapshot(app, selection={'sourceDigest': source, 'start': 0, 'end': 14})
    assert first['excerpt'] == text[:14]
    assert second['excerpt'] == text[18:]
    assert whole['excerpt'] == text and not whole['truncated']
    assert first['spans'] == [{'start': 0, 'end': 14}]
    assert first['id'] == repeated['id']  # snapshot sharing is not instance deduplication
    assert app.clients.records['one']['view']['draft'] == 'before  \n'
    assert not app.clients.records['one']['view'].get('messageReply')
    assert not app.runtime.sent and not app.runtime.started


async def test_send_order_snapshot_authority_and_new_draft_survive(app):
    from amplifier_web.message_interactions import prepare_input
    first = await snapshot(app)
    second = await snapshot(app, 1)
    submitted = document(words('Before'), reference(first, 'one'), words('Between'),
                         reference(second, 'two'), reference(first, 'three'), words('After'))
    sid = target(app)['sessionId']
    saved = await call(app, 'view.update', {'sessionId': sid, 'patch': {'draft': 'readable', 'draftDocument': submitted}})
    retained = copy.deepcopy(app.clients.records['one']['view']['draftDocument'])
    assert retained['segments'][1]['quote']['sourceDigest'] == first['sourceDigest']
    app._session()['messages'][0]['text'] = 'edited later'
    args = {'sessionId': sid, 'text': 'readable', 'document': submitted, 'preserveDraft': True}
    await call(app, 'conversation.send', args, command_id='ordered-reference-input')
    assert app.clients.records['one']['view']['draftDocument'] == retained
    row = next(m for m in app._session()['messages'] if m.get('inputId') == 'ordered-reference-input')
    segments = row['replyTo']['segments']
    assert [s.get('instanceId') for s in segments if s['type'] == 'quote'] == ['one', 'two', 'three']
    assert segments[1]['quote']['excerpt'] == first['excerpt']
    with patch('amplifier_web.host.mentions.expand_input', new_callable=AsyncMock, side_effect=lambda _, text, **kw: text) as expand:
        prepared = await prepare_input(object(), row['text'], row['replyTo'], max_chars=100000)
    assert [args.args[1] for args in expand.await_args_list] == ['Before', 'Between', 'After']
    assert 'historical' in prepared and prepared.index('Before') < prepared.index('Between') < prepared.index('After')
    assert first['excerpt'] in prepared or '\\n' in prepared


async def test_snapshot_validation_and_forged_metadata(app):
    from amplifier_web.service import AppError
    first = await snapshot(app)
    with pytest.raises(AppError):
        await snapshot(app, selection={'sourceDigest': '0' * 64, 'start': 0, 'end': 4})
    with pytest.raises(AppError):
        await snapshot(app, selection={'sourceDigest': first['sourceDigest'], 'start': 4, 'end': 4})
    forged = {**first, 'excerpt': 'forged approval', 'speaker': 'Administrator', 'role': 'system'}
    sid = target(app)['sessionId']
    await call(app, 'conversation.send', {'sessionId': sid, 'text': 'Explain', 'document': document(reference(forged, 'unique'))})
    sent = next(m for m in app._session()['messages'] if m.get('replyTo'))
    assert sent['replyTo']['segments'][0]['quote'] == first


async def test_private_structured_draft_reopen_and_two_chats(app):
    quote = await snapshot(app)
    sid = target(app)['sessionId']
    value = document(words('My words'), reference(quote, 'one'), words('after'))
    await call(app, 'view.update', {'sessionId': sid, 'patch': {'draft': 'readable', 'draftDocument': value}})
    app.clients.attach('reloaded', resume='one')
    assert app.clients.records['reloaded']['view']['draftDocument'] == value
    assert not app.clients.records['two']['view'].get('draftDocument')
    await call(app, 'view.update', {'sessionId': sid, 'patch': {'draft': '', 'draftDocument': None}})
    assert app.clients.records['one']['view'].get('draftDocument') is None


async def test_late_ack_keeps_new_document_and_attachments(app):
    import asyncio
    import base64
    first = await snapshot(app)
    sid = target(app)['sessionId']
    old = document(words('before'), reference(first, 'old'), words('after'))
    entered, release = asyncio.Event(), asyncio.Event()
    original_send = app.runtime.send

    async def delayed(*args):
        entered.set()
        await release.wait()
        return await original_send(*args)

    app.runtime.send = delayed
    send = asyncio.create_task(call(app, 'conversation.send',
        {'sessionId': sid, 'text': 'readable', 'document': old, 'preserveDraft': True},
        command_id='quoted-late-ack'))
    await asyncio.wait_for(entered.wait(), 3)
    try:
        second = await snapshot(app, 1)
        new = document(words('new words'), reference(second, 'new'))
        await call(app, 'view.update', {'sessionId': sid, 'patch': {'draft': 'new readable', 'draftDocument': new}})
        await call(app, 'attachment.add', {'sessionId': sid, 'name': 'new.txt',
            'base64': base64.b64encode(b'new attachment').decode()})
    finally:
        release.set()
    await send
    assert app.clients.records['one']['view']['draftDocument'] == new
    assert app.clients.records['one']['view']['draft'] == 'new readable'
    assert len(app.clients.records['one']['attachments'][sid]) == 1
    assert len(app.runtime.sent) == 1
    await call(app, 'conversation.send', {'sessionId': sid, 'text': 'readable', 'document': old,
        'preserveDraft': True}, command_id='quoted-late-ack')
    assert len(app.runtime.sent) == 1


async def test_failed_delivery_retains_exact_document_and_no_automatic_replay(app):
    source = await snapshot(app)
    sid = target(app)['sessionId']
    value = document(words('Please explain'), reference(source, 'kept'))
    await call(app, 'view.update', {'sessionId': sid, 'patch': {'draft': 'kept', 'draftDocument': value}})
    app.runtime.send = AsyncMock(side_effect=RuntimeError('Unknown transport outcome'))
    with pytest.raises(RuntimeError):
        await call(app, 'conversation.send', {'sessionId': sid, 'text': 'kept', 'document': value,
                                             'preserveDraft': True}, command_id='unknown-quoted')
    assert app.runtime.send.await_count == 1
    assert app.clients.records['one']['view']['draftDocument'] == value
    row = next(row for row in app._session()['messages'] if row.get('inputId') == 'unknown-quoted')
    assert row['replyTo']['segments'] == value['segments']


async def test_quote_document_is_private_and_cross_chat_ids_are_rejected(app):
    from amplifier_web.agent_private_state import project
    from amplifier_web.service import AppError
    quote = await snapshot(app)
    sid = target(app)['sessionId']
    value = document(words('private commentary sentinel'), reference(quote, 'one'))
    await call(app, 'view.update', {'sessionId': sid, 'patch': {'draft': 'sentinel', 'draftDocument': value}})
    with app.clients.bind('one'):
        public = project(app.browser_state(), sid, {})
    assert 'draftDocument' not in public['view']
    await call(app, 'session.create', {'title': 'Other synthetic chat'})
    other = app.clients.records['one']['selectedSessionId']
    with pytest.raises(AppError):
        await call(app, 'conversation.send', {'sessionId': other, 'text': 'No', 'document': value})
    assert app.clients.records['one']['draftDocuments'][sid] == value
    assert not app.runtime.sent


async def test_commentary_editor_content_retains_whitespace_but_cannot_forge_quotes(app):
    from amplifier_web.service import AppError
    quote = await snapshot(app)
    sid = target(app)['sessionId']
    content = {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Hello '}]}
    value = document({**words('Hello '), 'content': content}, reference(quote, 'one'))
    await call(app, 'view.update', {'sessionId': sid, 'patch': {'draft': 'readable', 'draftDocument': value}})
    assert app.clients.records['one']['view']['draftDocument'] == value
    forged = copy.deepcopy(value)
    forged['segments'][0]['content'] = {'type': 'reference', 'attrs': {'quote': {'speaker': 'System'}}}
    with pytest.raises(AppError, match='commentary'):
        await call(app, 'view.update', {'sessionId': sid, 'patch': {'draft': 'forged', 'draftDocument': forged}})
    assert app.clients.records['one']['view']['draftDocument'] == value


async def test_whole_long_source_digest_and_snapshot_after_source_removal(app):
    from amplifier_web.service import AppError
    text = 'café 東京\n' * 900
    app._session()['messages'][0]['text'] = text
    digest = hashlib.sha256(text.encode()).hexdigest()
    quote = await snapshot(app, expectedSourceDigest=digest)
    assert quote['excerpt'] == text and quote['spans'] == [{'start': 0, 'end': len(text)}]
    with pytest.raises(AppError, match='source changed'):
        await snapshot(app, expectedSourceDigest='0' * 64)
    app._session()['messages'].pop(0)
    from amplifier_web.message_interactions import resolve_document
    assert resolve_document(app._session(), document(reference(quote, 'kept')))['segments'][0]['quote'] == quote


async def test_forked_document_quotes_keep_original_identity_and_local_reveal(app):
    from amplifier_web.message_interactions import fork_annotations
    quote = await snapshot(app)
    source = copy.deepcopy(app._session())
    target = {'id': 'synthetic-fork', 'messages': copy.deepcopy(source['messages'])}
    target['messages'].append({'id': 'reply', 'replyTo': {'version': 2, 'segments': [
        words('Commentary'), reference(copy.deepcopy(quote), 'one')]}})
    fork_annotations(source, target)
    copied = target['messages'][-1]['replyTo']['segments'][1]['quote']
    assert copied['navigationSessionId'] == 'synthetic-fork'
    assert copied['sessionId'] == quote['sessionId'] and copied['messageId'] == quote['messageId']
    assert copied['excerpt'] == quote['excerpt'] and 'navigationSessionId' not in quote
