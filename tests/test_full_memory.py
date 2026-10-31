"""Standing memory authority through shared dispatch and actual child mounts."""
import asyncio
import copy
import json

import pytest

from amplifier_web.service import AppError
from test_full_child_configuration import app, mounted, dispatch
from test_full_feedback import call, mode


async def change(app, source, action, args, identity, *, origin='agent'):
    return (await call(app, source, action, {'sessionId': source['id'], **args},
                       origin=origin, command=identity))['result']


def receipt(app, identity):
    return json.loads(app.recall.store.db.execute(
        'SELECT result FROM memory_receipts WHERE id=?', (identity,)).fetchone()[0])


@pytest.mark.parametrize('origin', ['agent', 'system'])
async def test_full_notes_settings_and_exact_retries_need_no_human_or_browser(app, origin):
    source, peer = app.state['sessions']
    peer['collaborationGeneration'] = {'id': 'peer-generation', 'inputIds': ['peer-input'], 'terminal': False}
    await mode(app, source)
    assert not source['messages'] and not app.clients.records
    before = copy.deepcopy(app.state['view'])
    settings = app.recall.personalization.status(source)['settings']
    assert not settings['use'] and not settings['contribute']
    configured = await change(app, source, 'memory.configure',
        {'expectedRevision': 0, 'use': True}, 'full-settings', origin=origin)
    assert configured['settings']['use'] and not configured['settings']['contribute']
    created = await change(app, source, 'memory.create',
        {'scope': 'workspace', 'text': 'Autonomous finding.'}, 'full-create', origin=origin)
    updated = await change(app, source, 'memory.update',
        {'id': created['id'], 'expectedRevision': 1, 'text': 'Corrected finding.'}, 'full-update', origin=origin)
    record = app.recall.store.memory(created['id'])
    assert record['text'] == 'Corrected finding.' and record['source'] is None
    assert len(app.recall.store.versions(created['id'])) == 2
    for value in (configured['settings'], created, updated, record):
        provenance = value['provenance']
        assert provenance['origin'] == origin and provenance['sessionId'] == source['id']
        assert 'authorizationMessageId' not in provenance
        authority = provenance['authority']
        assert authority['origin'] == origin and authority['sessionId'] == source['id']
        if origin == 'agent':
            assert authority['runtimeSessionId'] == source['id']
            assert authority['generationId'] == 'generation-1'
        else:
            assert authority.get('runtimeSessionId') is None and authority.get('generationId') is None
    deleted = await change(app, source, 'memory.delete',
        {'id': created['id'], 'expectedRevision': 2}, 'full-delete', origin=origin)
    assert deleted['deleted'] and deleted['provenance']['authority'] == updated['provenance']['authority']
    for read in (app.recall.store.memory, app.recall.store.versions):
        with pytest.raises(ValueError, match='unavailable'):
            read(created['id'])
    assert app.recall.store.db.execute(
        'SELECT COUNT(*) FROM memory_versions WHERE id=?', (created['id'],)).fetchone()[0] == 0
    assert not app.recall.store.list_memories(None)['items']
    retained = {key: copy.deepcopy(receipt(app, key)) for key in
                ('full-settings', 'full-create', 'full-update', 'full-delete')}
    await mode(app, source, 'ask')
    await app.dispatch('conversation.stop', {'sessionId': source['id']})
    commands = [
        ('memory.configure', {'expectedRevision': 0, 'use': True}, 'full-settings'),
        ('memory.create', {'scope': 'workspace', 'text': 'Autonomous finding.'}, 'full-create'),
        ('memory.update', {'id': created['id'], 'expectedRevision': 1, 'text': 'Corrected finding.'}, 'full-update'),
        ('memory.delete', {'id': created['id'], 'expectedRevision': 2}, 'full-delete'),
    ]
    for action, args, identity in commands:
        retried = await change(app, source, action, args, identity, origin=origin)
        result = retried['settings'] if action == 'memory.configure' else retried
        assert result == {**retained[identity], 'duplicate': True}
        assert receipt(app, identity) == retained[identity]
        # Another conversation cannot borrow a receipt by reusing transport ID.
        with pytest.raises(AppError, match='different contents'):
            await change(app, peer, action, args, identity, origin='system' if origin == 'system' else 'agent')
    with pytest.raises(AppError, match='different contents'):
        await change(app, source, 'memory.create', {'scope': 'workspace', 'text': 'Changed intent.'},
                     'full-create', origin=origin)
    encoded = json.dumps(app.recall.store.db.execute('SELECT result FROM memory_receipts').fetchall())
    assert 'Autonomous finding.' not in encoded and 'Corrected finding.' not in encoded
    assert app.state['view'] == before and not source['approvals']


@pytest.mark.parametrize('kind', ['finite', 'resumed', 'persistent', 'nested'])
async def test_mounted_full_child_mutations_retain_actual_actor(mounted, kind):
    from amplifier_web.host.children import _PERSISTENT

    h = mounted
    await mode(h.app, h.source)
    assert not h.source['messages']
    actors = []

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        actor = coordinator.session_id
        actors.append(actor)
        suffix = str(len(actors))
        control = tools['app_control']

        async def invoke(action, args):
            result = await dispatch(control, action, args, action + suffix)
            assert result.success, result.error
            return result.output['result']

        revision = h.app.recall.personalization.status(h.source)['settings']['revision']
        settings = await invoke('memory.configure', {'expectedRevision': revision, 'use': True})
        note = await invoke('memory.create', {'scope': 'task', 'text': 'Child finding ' + suffix})
        changed = await invoke('memory.update', {'id': note['id'], 'expectedRevision': 1, 'text': 'Revised ' + suffix})
        deleted = await invoke('memory.delete', {'id': note['id'], 'expectedRevision': 2})
        for row in (settings['settings'], note, changed, deleted):
            authority = row['provenance']['authority']
            assert authority['runtimeSessionId'] == actor != 'native-root'
            assert authority['sessionId'] == h.source['id'] and authority['origin'] == 'agent'
            assert authority['generationId'] == 'generation-1'
            assert authority['inputIds'] == ['assigned-input']
        assert not h.source['approvals'] and not h.app.clients.records
        if kind == 'nested' and actor == 'child':
            await h.registry.spawn('worker', 'keep a distinct note', h.registry.sessions['child'], sub_session_id='grandchild')
        return 'saved'

    h.install_execute(execute)
    token = _PERSISTENT.set(kind == 'persistent')
    try:
        await h.registry.spawn('worker', 'remember findings', h.parent, sub_session_id='child')
        if kind == 'resumed':
            await h.registry.resume('child', 'remember another finding', h.parent)
        assert actors == (['child', 'grandchild'] if kind == 'nested' else
                          ['child', 'child'] if kind == 'resumed' else ['child'])
        h.approval_prompt.assert_not_awaited()
    finally:
        _PERSISTENT.reset(token)


@pytest.mark.parametrize('boundary', ['stop', 'revoke', 'off-on', 'placement', 'workspace', 'native', 'turn-finished'])
@pytest.mark.parametrize('action', ['memory.update', 'memory.delete', 'memory.create'])
async def test_full_pending_note_rechecks_before_commit(app, monkeypatch, boundary, action):
    source = app.state['sessions'][0]
    await mode(app, source)
    original = await change(app, source, 'memory.create', {'scope': 'workspace', 'text': 'Original note.'}, 'seed')
    app.recall.store.replace({'id': source['id']}, 'fixture-signature', 1,
        [{'id': 'evidence', 'role': 'assistant', 'text': 'Attributed source evidence.'}])
    args = ({'scope': 'workspace', 'text': 'New note.',
             'source': {'sessionId': source['id'], 'messageId': 'evidence', 'sourceRevision': 1}}
            if action == 'memory.create' else {'id': original['id'], 'expectedRevision': 1,
                **({'text': 'Changed note.'} if action == 'memory.update' else {})})
    entered, release = asyncio.Event(), asyncio.Event()
    to_thread = asyncio.to_thread
    read = app.recall.store.message if action == 'memory.create' else app.recall.store.memory

    async def held(function, *values, **kwargs):
        result = await to_thread(function, *values, **kwargs)
        if function == read:
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(asyncio, 'to_thread', held)
    pending = asyncio.create_task(change(app, source, action, args, 'pending-note'))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if boundary == 'stop':
            await app.dispatch('conversation.stop', {'sessionId': source['id']})
        elif boundary in {'revoke', 'off-on'}:
            await mode(app, source, 'ask')
            if boundary == 'off-on':
                await mode(app, source)
        elif boundary == 'turn-finished':
            source['collaborationGeneration']['terminal'] = True
            source['status'] = 'idle'
        elif boundary == 'placement':
            source['executionRevision'] = source.get('executionRevision', 0) + 1
        elif boundary == 'workspace':
            source['workspace'] += '/moved'
        else:
            source['runtimeSessionId'] = 'another-native-owner'
    finally:
        release.set()
    if boundary == 'turn-finished':
        result = await pending
        assert result['provenance']['authority']['generationId'] == 'generation-1'
        if action == 'memory.create':
            saved = app.recall.store.memory(result['id'])
            assert saved['source']['sessionId'] == source['id']
            assert saved['source']['messageId'] == 'evidence'
            assert 'kind' not in saved['source']  # Never becomes human evidence.
    else:
        with pytest.raises(AppError, match='authority changed|stopped or moved'):
            await pending
        assert app.recall.store.memory(original['id'])['text'] == 'Original note.'
        assert len(app.recall.store.list_memories(None)['items']) == 1
        assert not app.recall.store.db.execute('SELECT 1 FROM memory_receipts WHERE id=?', ('pending-note',)).fetchone()


async def test_full_mutations_do_not_hydrate_or_copy_human_history(app, monkeypatch):
    source = app.state['sessions'][0]
    await mode(app, source)

    class UnvisitedHistory(list):
        def __iter__(self):
            raise AssertionError('Full memory traversed history to find human authorization')

        def __deepcopy__(self, memo):
            raise AssertionError('Full memory copied history')

    original = source['messages']
    source['messages'] = UnvisitedHistory()
    lookup = app._session
    hydrated = []

    def owner(identity=None, *args, **kwargs):
        if identity == source['id'] and kwargs.get('hydrate', True):
            hydrated.append(identity)
        return lookup(identity, *args, **kwargs)

    monkeypatch.setattr(app, '_session', owner)
    try:
        # Call the domain with the same explicit trusted caller as shared dispatch;
        # this isolates admission cost from unrelated client response projections.
        settings = await app.recall.dispatch('memory.configure',
            {'sessionId': source['id'], 'expectedRevision': 0, 'use': True}, 'system', 'bounded-settings', source['id'])
        result = await app.recall.dispatch('memory.create',
            {'sessionId': source['id'], 'scope': 'task', 'text': 'Bounded admission.'}, 'system', 'bounded-note', source['id'])
        assert not hydrated
        assert settings['result']['settings']['provenance']['authority']['sessionId'] == source['id']
        assert result['result']['provenance']['authority']['origin'] == 'system'
        assert receipt(app, 'bounded-note')['id'] == result['result']['id']
    finally:
        source['messages'] = original


async def test_full_scope_cas_and_ask_provenance_are_preserved(app):
    source, peer = app.state['sessions']
    await mode(app, source)
    await mode(app, peer)
    created = await change(app, source, 'memory.create', {'scope': 'task', 'text': 'Owned task note.'}, 'scoped')
    with pytest.raises(AppError, match='outside'):
        await change(app, peer, 'memory.update', {'id': created['id'], 'expectedRevision': 1, 'text': 'Peer edit.'}, 'outside', origin='system')
    with pytest.raises(AppError, match='peer mutations|calling conversation'):
        await app.dispatch('memory.delete', {'sessionId': source['id'], 'id': created['id'], 'expectedRevision': 1},
                           origin='system', caller_session_id=peer['id'], command_id='wrong-owner')
    with pytest.raises(AppError, match='changed'):
        await change(app, source, 'memory.update', {'id': created['id'], 'expectedRevision': 2, 'text': 'Stale edit.'}, 'stale')
    with pytest.raises(AppError, match='change its scope'):
        await change(app, source, 'memory.update', {'id': created['id'], 'expectedRevision': 1, 'scope': 'global', 'text': 'Wrong scope.'}, 'scope-change')
    await mode(app, source, 'ask')
    for action, values in [
        ('memory.create', {'scope': 'workspace', 'text': 'Needs human provenance.'}),
        ('memory.configure', {'expectedRevision': 0, 'use': True}),
    ]:
        with pytest.raises(AppError, match='attributable'):
            await change(app, source, action, values, action + '-ask')
    # Preserve explicit allScopes curation; this is already an independent note
    # management choice, not permission to impersonate the originating chat.
    removed = await change(app, peer, 'memory.delete',
        {'id': created['id'], 'expectedRevision': 1, 'allScopes': True}, 'curate', origin='system')
    assert removed['deleted'] and removed['provenance']['sessionId'] == peer['id']


@pytest.mark.parametrize('action', ['memory.create', 'memory.configure'])
async def test_lost_acknowledgement_returns_committed_receipt_without_repeating_write(app, monkeypatch, action):
    source = app.state['sessions'][0]
    await mode(app, source)
    owner = app.recall.store if action == 'memory.create' else app.recall.personalization.data
    method = 'mutate' if action == 'memory.create' else 'configure'
    write = getattr(owner, method)
    calls = []

    def committed_then_lost(*args, **kwargs):
        result = write(*args, **kwargs)
        calls.append(copy.deepcopy(result))
        raise RuntimeError('Committed response lost')

    monkeypatch.setattr(owner, method, committed_then_lost)
    args = ({'scope': 'workspace', 'text': 'Only one committed note.'} if action == 'memory.create'
            else {'expectedRevision': 0, 'use': True})
    with pytest.raises(RuntimeError, match='response lost'):
        await change(app, source, action, args, 'lost-reply', origin='system')
    retained = copy.deepcopy(receipt(app, 'lost-reply'))
    await app.dispatch('conversation.stop', {'sessionId': source['id']})
    await mode(app, source, 'ask')
    retried = await change(app, source, action, args, 'lost-reply', origin='system')
    result = retried['settings'] if action == 'memory.configure' else retried
    assert result == {**retained, 'duplicate': True} and len(calls) == 1
    assert receipt(app, 'lost-reply') == retained
    if action == 'memory.create':
        assert len(app.recall.store.list_memories(None)['items']) == 1
        assert len(app.recall.store.versions(retained['id'])) == 1
    else:
        assert app.recall.personalization.status(source)['settings']['revision'] == 1
