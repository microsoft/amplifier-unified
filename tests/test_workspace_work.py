"""Accepted promises survive execution loss; reads never admit a dormant worker.

Test-first contract for workspace.work. Synthetic records only. Runtime acceptance
requires the separately registered persisted browser/installed-candidate journey.
"""
import asyncio
import copy
import hashlib
import json

import pytest

from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import app, Runtime, agent_action, generation


def workspace_id(app, owner):
    return next(w['id'] for w in app.state['workspaces'] if w['path'] == owner['workspace'])


def source(app, owner, text='Keep this request after the worker stops.', origin='ui'):
    message = app._message(owner, 'user', text, inputOrigin=origin)
    app._save()
    return {'sessionId': owner['id'], 'messageId': message['id']}


async def create(app, owner, original=None, command='capture-one', intent='promise-one', **patch):
    args = {'sessionId': owner['id'], 'workspaceId': workspace_id(app, owner),
            'source': original or source(app, owner), 'intentKey': intent,
            'title': 'Retain accepted request', 'commitment': 'accepted',
            'interpretation': 'Retain the promise independently of its worker.',
            'criteria': [{'id': 'retained', 'text': 'The exact request survives a cold reopen.'}],
            **patch}
    return await agent_action(app, owner, 'workspace.work.create', args, command)


async def read(app, owner, identity):
    return (await app.dispatch('workspace.work.read', {
        'workspaceId': workspace_id(app, owner), 'id': identity}))['result']


async def revise(app, owner, item, command, **patch):
    return await agent_action(app, owner, 'workspace.work.revise', {
        'sessionId': owner['id'], 'workspaceId': workspace_id(app, owner),
        'id': item['id'], 'expectedRevision': item['revision'],
        'expectedPromiseRevision': item['promiseRevision'], **patch}, command)


async def test_capture_keeps_exact_source_and_does_not_create_execution(app):
    owner = app.state['sessions'][0]
    text = 'Keep punctuation, Unicode: caf\u00e9 \U0001d11e.\nDo not replace these words.'
    original = source(app, owner, text)
    before = copy.deepcopy(app.runtime.inputs)
    result = await create(app, owner, original)
    item = result['result']['item']
    detail = await read(app, owner, item['id'])
    assert detail['originalSource']['text'] == text
    assert detail['originalSource']['sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert detail['originalSource']['inputOrigin'] == 'ui'
    assert detail['originalSource']['messageId'] == original['messageId']
    assert item['commitment'] == 'accepted' and item['disposition'] == 'open'
    assert item['ownerSessionId'] == owner['id']
    assert item['promiseRevision'] == item['revision'] == 1
    assert app.runtime.inputs == before
    assert app.assignments.current(owner) is None
    assert 'state' not in result or 'sessions' not in result['state']


@pytest.mark.parametrize('origin', ['peer', 'agent', 'unknown'])
async def test_user_role_without_human_origin_cannot_be_captured_as_accepted(app, origin):
    owner = app.state['sessions'][0]
    original = source(app, owner, origin=origin)
    with pytest.raises(AppError):
        await create(app, owner, original)
    proposal = (await create(app, owner, original, command='proposal', commitment='proposed'))['result']['item']
    assert proposal['commitment'] == 'proposed'
    detail = await read(app, owner, proposal['id'])
    assert detail['originalSource']['inputOrigin'] == origin
    assert detail['item']['humanDecision'] == 'not_recorded'


async def test_capture_deduplicates_command_and_exact_source_intent(app):
    owner = app.state['sessions'][0]
    original = source(app, owner)
    first = await create(app, owner, original)
    same = await create(app, owner, original)
    assert same['duplicate'] and same['result'] == first['result']
    recovered = await create(app, owner, original, command='capture-after-lost-history')
    assert recovered['result']['item']['id'] == first['result']['item']['id']
    with pytest.raises(AppError):
        await create(app, owner, original, title='Changed command payload')
    listing = (await app.dispatch('workspace.work.list', {'workspaceId': workspace_id(app, owner)}))['result']
    assert listing['total'] == 1
    assert len(listing['items']) == 1


async def test_correction_preserves_original_and_rejects_stale_write(app):
    owner = app.state['sessions'][0]
    item = (await create(app, owner))['result']['item']
    original = (await read(app, owner, item['id']))['originalSource']
    correction = source(app, owner, 'Also retain paused workers; do not infer completion from idle.')
    changed = (await revise(app, owner, item, 'correct', source=correction,
        interpretation='Retain the promise for paused workers too.',
        criteria=[{'id': 'retained', 'text': 'Cold reopen retains active and paused-worker promises.'}]))['result']['item']
    assert changed['promiseRevision'] == 2 and changed['revision'] == 2
    assert (await read(app, owner, item['id']))['originalSource'] == original
    with pytest.raises(AppError):
        await revise(app, owner, item, 'late-edit', nextAction='Overwrite the correction')
    history = (await app.dispatch('workspace.work.history', {
        'workspaceId': workspace_id(app, owner), 'id': item['id']}))['result']
    assert any(row.get('source', {}).get('messageId') == correction['messageId'] for row in history['items'])
    change = next(row for row in history['items'] if row.get('source', {}).get('messageId') == correction['messageId'])
    ref = change['sourceRef']
    retained = await app.dispatch(ref['action'], ref['args'])
    assert retained['result']['source']['text'] == 'Also retain paused workers; do not infer completion from idle.'
    assert (await read(app, owner, item['id']))['item']['promiseRevision'] == 2


async def test_concurrent_edits_have_one_winner_without_silent_loss(app):
    owner = app.state['sessions'][0]
    item = (await create(app, owner))['result']['item']
    replies = await asyncio.gather(
        revise(app, owner, item, 'edit-one', nextAction='Check source'),
        revise(app, owner, item, 'edit-two', nextAction='Check browser'),
        return_exceptions=True)
    assert sum(isinstance(r, dict) for r in replies) == 1
    assert sum(isinstance(r, AppError) and r.status == 409 for r in replies) == 1
    current = (await read(app, owner, item['id']))['item']
    assert current['revision'] == 2 and current['promiseRevision'] == 1
    assert current['nextAction'] in {'Check source', 'Check browser'}


async def test_cold_list_and_detail_do_not_hydrate_or_start_workers(app, monkeypatch):
    owner = app.state['sessions'][0]
    item = (await create(app, owner))['result']['item']
    wid = workspace_id(app, owner)
    app._save()
    await app.close()
    runtime = Runtime()
    restored = AppService(app.data_dir, runtime, workspace=app.default_workspace)
    runtime.app = restored
    try:
        before = {row['id'] for row in restored.state['sessions']}
        def forbidden(*args, **kwargs):
            pytest.fail('Work inspection hydrated history or admitted a worker')
        monkeypatch.setattr(restored.history, 'ensure_loaded', forbidden)
        monkeypatch.setattr(restored.cold_display, 'hydrate', forbidden)
        if restored.management:
            monkeypatch.setattr(restored.management, 'ensure_runtime', forbidden)
        listing = (await restored.dispatch('workspace.work.list', {'workspaceId': wid}))['result']
        detail = (await restored.dispatch('workspace.work.read', {'workspaceId': wid, 'id': item['id']}))['result']
        assert listing['items'][0]['id'] == detail['item']['id'] == item['id']
        assert detail['originalSource']['text'] == 'Keep this request after the worker stops.'
        assert {row['id'] for row in restored.state['sessions']} == before
        assert runtime.inputs == []
    finally:
        await restored.close()


async def test_independent_peer_can_read_but_cannot_revise_or_impersonate(app):
    owner, peer = app.state['sessions']
    item = (await create(app, owner))['result']['item']
    await generation(app, peer, 'peer-generation', ['peer-input'])
    args = {'workspaceId': workspace_id(app, owner), 'id': item['id']}
    observed = await agent_action(app, peer, 'workspace.work.read', args, 'read-peer')
    assert observed['result']['item']['id'] == item['id']
    with pytest.raises(AppError):
        await revise(app, peer, item, 'unauthorized-revision', nextAction='Take over')
    with pytest.raises(AppError):
        await agent_action(app, peer, 'workspace.work.revise', {
            **args, 'sessionId': owner['id'], 'expectedRevision': 1,
            'expectedPromiseRevision': 1, 'nextAction': 'Borrow owner'}, 'borrow-owner')
    assert (await read(app, owner, item['id']))['item']['revision'] == 1
    assert app.assignments.current(peer) is None


async def test_open_items_page_without_silent_omission_and_reject_mixed_versions(app):
    owner = app.state['sessions'][0]
    identities = set()
    for index in range(21):
        row = await create(app, owner, command=f'capture-{index}', intent=f'promise-{index}')
        identities.add(row['result']['item']['id'])
    args = {'workspaceId': workspace_id(app, owner), 'limit': 20}
    first = (await app.dispatch('workspace.work.list', args))['result']
    assert first['total'] == 21 and first['truncated'] and first['nextAfter']
    second = (await app.dispatch('workspace.work.list', {**args, 'after': first['nextAfter']}))['result']
    assert {r['id'] for r in first['items'] + second['items']} == identities
    assert len(first['items']) + len(second['items']) == 21
    assert not second['nextAfter']
    await create(app, owner, command='capture-new', intent='promise-new')
    with pytest.raises(AppError):
        await app.dispatch('workspace.work.list', {**args, 'after': first['nextAfter']})


async def test_context_marks_overflow_without_copying_source_or_correction_bodies(app):
    owner = app.state['sessions'][0]
    for index in range(21):
        await create(app, owner, source(app, owner, 'Private source body ' + str(index)),
            command=f'capture-{index}', intent=f'promise-{index}')
    result = (await agent_action(app, owner, 'workspace.work.context', {
        'sessionId': owner['id']}, 'context-read'))['result']
    encoded = json.dumps(result, ensure_ascii=False).encode()
    assert len(encoded) <= 12 * 1024
    assert result['total'] == 21 and result['truncated'] and result['nextAfter']
    assert len(result['items']) <= 20
    assert b'Private source body' not in encoded
    assert all(row['id'] and row['readRef'] and row['promiseRevision'] for row in result['items'])
