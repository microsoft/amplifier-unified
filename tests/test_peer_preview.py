"""Focused peer preview contract. Synthetic inputs; no live account/model claims."""
import copy
from unittest.mock import AsyncMock

import pytest

from amplifier_operations.coordination import peer_input
from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import generation, agent_action
from test_canvas_views import command, view, target
from fixtures.peer_message_content import TEXT, LONG, CRLF


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'app', workspace=tmp_path)
    source, receiving = [service._new_session({'title': name}) for name in ('Source review', 'Receiving')]
    service.state['sessions'] = [source, receiving]
    service.state['selectedSessionId'] = receiving['id']
    for i in range(24):
        service._message(source, 'assistant', TEXT if i == 0 else 'Earlier %d' % i)
    service._message(source, 'assistant', LONG)
    service._message(source, 'assistant', CRLF)
    await generation(service, source, 'source-generation', ['synthetic-human'])
    result = await agent_action(service, source, 'coordination.send', {
        'sessionId': receiving['id'], 'mode': 'notify', 'text': TEXT}, 'peer-preview-input')
    service._publish()
    service.clients.attach('preview-client', kind='web')
    service.clients.attach('other-client', kind='web')
    try:
        yield service, source, receiving, result['messageId']
    finally:
        await service.close()


def descriptor(service, receiving, mid):
    with service.clients.bind('preview-client'):
        original = next(m for m in receiving['messages'] if m['id'] == mid)
        return service.peer_attribution.resolve(receiving, [original])[0]['attribution']['peer']


async def test_exact_transport_markdown_readonly_preview_and_page_boundary(app, monkeypatch):
    service, source, receiving, mid = app
    row = next(m for m in receiving['messages'] if m['id'] == mid)
    assert row['text'] == TEXT
    assert TEXT in peer_input(row['peerEnvelope'], row['text'])
    meta = descriptor(service, receiving, mid)
    before = copy.deepcopy(receiving)
    # The preview must never use the full transcript reader or history hydrator.
    monkeypatch.setattr(service.history, 'ensure_loaded', AsyncMock(side_effect=AssertionError('eager hydrate')))
    with service.clients.bind('preview-client'):
        selected = service.state['selectedSessionId']
        result = await service.dispatch('peer.preview', {'proof': meta['proof'], 'sourceSessionId': receiving['id']})
        assert result['accepted'] and result['effects'] == []
        snapshot = await service.peer_preview.read({'proof': meta['proof']})
        assert len(snapshot['page']['items']) == 20
        assert snapshot['page']['hasEarlier']
        crlf = next(m for m in snapshot['page']['items'] if m['text'].startswith('## CRLF'))
        assert crlf['text'] == CRLF
        long = next(m for m in snapshot['page']['items'] if m.get('textDetail'))
        ref = long['textDetail']
        assert ref['peerPreview'] == meta['proof']
        first = await service.peer_preview.read_text({**ref, 'offset': 0, 'limit': 3000})
        chunks = [first['value']]
        while first['nextOffset'] is not None:
            first = await service.peer_preview.read_text({**ref, 'offset': first['nextOffset'], 'limit': 3000})
            chunks.append(first['value'])
        assert ''.join(chunks) == LONG
        earlier = await service.peer_preview.read({'proof': meta['proof'], 'before': snapshot['page']['before']})
        assert earlier['page']['items'][0]['text'] == TEXT
        assert service.state['selectedSessionId'] == selected
    assert receiving == before


@pytest.mark.parametrize('change', ['proof','origin','client','text','duplicate','receipt','target','parent','workspace','deleted'])
async def test_preview_fail_closed_without_retargeting(app, change):
    service, source, receiving, mid = app
    meta = descriptor(service, receiving, mid)
    args = {'proof': meta['proof'], 'sourceSessionId': receiving['id']}
    origin = 'ui'
    with service.clients.bind('preview-client'):
        assert service.peer_preview.open(args, origin)['accepted']
        if change == 'proof': args['proof'] += 'x'
        if change == 'origin': origin = 'agent'
        if change == 'client': service.clients.record()['selectedSessionId'] = source['id']
        if change == 'text': receiving['messages'][0]['text'] = 'changed'
        if change == 'duplicate': receiving['messages'].append(copy.deepcopy(receiving['messages'][0]))
        if change == 'receipt': service.db.execute("DELETE FROM commands WHERE id='peer-preview-input'")
        if change == 'target': source['createdAt'] += 10
        if change == 'parent': receiving['createdAt'] += 10
        if change == 'workspace': source['workspace'] = '/unavailable-synthetic-workspace'
        if change == 'deleted': source['_deleting'] = True
        with pytest.raises((AppError, ValueError)):
            service.peer_preview.open(args, origin)


async def test_dirty_canvas_and_exact_guarded_navigation(app):
    service, source, receiving, mid = app
    meta = descriptor(service, receiving, mid)
    await command(service, 'canvas.show', {'kind':'markdown', 'content':'Unfinished'}, client='preview-client')
    with service.clients.bind('preview-client'):
        current = copy.deepcopy(service.canvas_views.summary('primary'))
        await service.dispatch('canvas.views.dirty', {**target(current), 'dirty':True})
        with pytest.raises(AppError, match='edit'):
            service.peer_preview.open({'proof':meta['proof'], 'sourceSessionId':receiving['id']}, 'ui')
        await service.dispatch('canvas.views.dirty', {**target(current), 'dirty':False})
        await service.dispatch('session.archive', {'id':source['id']})
        saved = service.state['conversationOrganization']['archived'][source['id']]
        assert service.peer_preview.open({'proof':meta['proof'], 'sourceSessionId':receiving['id']}, 'ui')['result']['archived']
        navigation = {'id':source['id'], 'peerPreview':{'proof':meta['proof'], 'sourceSessionId':receiving['id']}}
        # The edit is declared after a successful preview; Open chat must
        # revalidate the same primary view under the selection lock.
        await service.dispatch('canvas.views.dirty', {**target(current), 'dirty':True})
        dirty = copy.deepcopy(service.canvas_views.summary('primary'))
        draft = copy.deepcopy(service.clients.record().get('drafts'))
        with pytest.raises(AppError, match='edit'):
            await service.dispatch('session.select', navigation)
        assert service.state['selectedSessionId'] == receiving['id']
        assert service.canvas_views.summary('primary') == dirty
        assert service.clients.record().get('drafts') == draft
        assert service.state['canvas']['content'] == 'Unfinished'
        await service.dispatch('canvas.views.dirty', {**target(current), 'dirty':False})
        await service.dispatch('session.select', navigation)
        assert service.state['selectedSessionId'] == source['id']
        assert service.state['conversationOrganization']['archived'][source['id']] == saved
    assert service.clients.records['other-client']['selectedSessionId'] == receiving['id']


async def test_preview_survives_page_invalidation_and_unrelated_arrivals(app):
    service, source, receiving, mid = app
    meta = descriptor(service, receiving, mid)
    with service.clients.bind('preview-client'):
        first = await service.peer_preview.read({'proof':meta['proof']})
        later = service._message(source, 'assistant', '## New captured note\n\nManual refresh only.')
        service._message(receiving, 'assistant', 'An independent reply does not revoke an original.')
        service._publish()
        second = await service.peer_preview.read({'proof':meta['proof']})
        assert later['id'] not in {row['id'] for row in first['page']['items']}
        assert later['id'] in {row['id'] for row in second['page']['items']}
        assert service.peer_preview.open({'proof':meta['proof'], 'sourceSessionId':receiving['id']}, 'ui')['accepted']
        receiving['messages'][0]['text'] = 'Original changed after refresh'
        with pytest.raises(AppError):
            await service.peer_preview.read({'proof':meta['proof']})


async def test_rename_refresh_and_revocation_during_read(app, monkeypatch):
    service, source, receiving, mid = app
    meta = descriptor(service, receiving, mid)
    with service.clients.bind('preview-client'):
        source['title'] = 'Renamed review'
        assert (await service.peer_preview.read({'proof':meta['proof'], 'metadata':'true'}))['source']['title'] == 'Renamed review'
        original = service.history.windows.read
        async def revoked(*args, **kwargs):
            result = await original(*args, **kwargs)
            source['workspace'] = '/revoked-synthetic'
            return result
        monkeypatch.setattr(service.history.windows, 'read', revoked)
        with pytest.raises(AppError):
            await service.peer_preview.read({'proof':meta['proof']})


def test_flattening_negative_control_is_discriminating():
    flattened = ' '.join(TEXT.split())
    assert flattened != TEXT
    assert '\\n' in TEXT and '\nThis is a real soft line break.' in TEXT
    assert '\n```python\n' in TEXT and '\n| Case | Result |\n' in TEXT


def make_cold(service, receiving, rows):
    from amplifier_web.cold_display import ColdRecord, raw
    from amplifier_web.resource_files import put
    reference = put(service.db, rows)
    cold = ColdRecord({**raw(receiving), '_coldFields': {'messages': reference}}, service.db)
    dict.pop(cold, 'messages', None)
    service.state['sessions'] = [cold if row['id'] == receiving['id'] else row
                                 for row in service.state['sessions']]
    return cold


@pytest.mark.parametrize('cold', [False, True])
async def test_complete_bounded_receiver_proof_is_immediately_usable(app, monkeypatch, cold):
    from amplifier_web import cold_display
    service, source, receiving, mid = app
    if cold:
        receiving = make_cold(service, receiving, copy.deepcopy(receiving['messages']))
    monkeypatch.setattr(cold_display, 'load', lambda *a: (_ for _ in ()).throw(AssertionError('Full hydrate')))
    with service.clients.bind('preview-client'):
        page = await service.history.windows.read(receiving['id'], limit=20)
        shown = next(row for row in page['items'] if row['id'] == mid)
        meta = shown['attribution']['peer']
        assert meta['available']
        result = await service.dispatch('peer.preview', {'proof': meta['proof'], 'sourceSessionId': receiving['id']})
        assert result['accepted'] and result['effects'] == []
        assert (await service.peer_preview.read({'proof': meta['proof']}))['page']['items']
    if cold:
        assert not dict.__contains__(receiving, 'messages')


@pytest.mark.parametrize('change', ['text', 'replaced', 'duplicate', 'receipt', 'partial', 'postread'])
async def test_cold_proof_rechecks_canonical_original_and_receipt(app, monkeypatch, change):
    from amplifier_web import cold_display
    service, source, receiving, mid = app
    rows = copy.deepcopy(receiving['messages'])
    receiving = make_cold(service, receiving, rows)
    monkeypatch.setattr(cold_display, 'load', lambda *a: (_ for _ in ()).throw(AssertionError('Full hydrate')))
    with service.clients.bind('preview-client'):
        page = await service.history.windows.read(receiving['id'], limit=20)
        proof = next(row for row in page['items'] if row['id'] == mid)['attribution']['peer']['proof']
        if change == 'text': rows[0]['text'] = 'Changed canonical body'
        if change == 'replaced': rows[0]['id'] = 'replacement'
        if change == 'duplicate': rows.append(copy.deepcopy(rows[0]))
        if change == 'partial':
            rows = [copy.deepcopy(rows[0])] + [{'id':str(i), 'role':'assistant','text':'Other'} for i in range(101)] + rows
        if change in {'text', 'replaced', 'duplicate', 'partial'}:
            receiving = make_cold(service, receiving, rows)
        if change == 'receipt': service.db.execute("DELETE FROM commands WHERE id='peer-preview-input'")
        if change == 'postread':
            read = service.history.windows.read
            async def replaced_after_source_read(*args, **kwargs):
                result = await read(*args, **kwargs)
                rows[0]['text'] = 'Replaced while source read was in flight'
                make_cold(service, receiving, rows)
                return result
            monkeypatch.setattr(service.history.windows, 'read', replaced_after_source_read)
        with pytest.raises((AppError, ValueError)):
            await service.peer_preview.read({'proof':proof})
        assert not dict.__contains__(receiving, 'messages')
