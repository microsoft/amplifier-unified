import copy
import json

import pytest

from amplifier_web.automatic_history import merge_web_history
from amplifier_web.runtime import normalize_event
from amplifier_web.service import AppService
from amplifier_web.errors import AppError
from test_appearance_preferences import report, initialize, local_cas, ack


def test_steering_order_crosses_roles_without_duplicating_or_hiding_repeated_replies():
    current = [
        {'id': 'user-1', 'role': 'user', 'text': 'Fix it'},
        {'id': 'user-2', 'role': 'user', 'text': 'More detail'},
        {'id': 'live-1', 'role': 'assistant', 'text': 'Working'},
        {'id': 'voice', 'role': 'assistant', 'text': 'Voice-only reply', 'via': 'call'},
        {'id': 'live-2', 'role': 'assistant', 'text': 'Working'},
    ]
    rows = [('user', 'Fix it'), ('assistant', 'Working'), ('user', 'More detail'), ('assistant', 'Working'), ('assistant', 'Done')]
    incoming = [dict(id=f'native-{i}', role=role, text=text, nativeIndex=i) for i, (role, text) in enumerate(rows)]
    session = {'messages': copy.deepcopy(current), 'draft': 'Keep me'}
    merge_web_history(session, incoming)
    assert [m['id'] for m in session['messages']] == [m['id'] for m in current] + ['native-4']
    assert [m['nativeIndex'] for m in session['messages'] if 'nativeIndex' in m] == [0, 2, 1, 3, 4]
    saved = copy.deepcopy(session)
    merge_web_history(session, incoming)
    assert session == saved


def test_crossed_anchor_is_not_rematched_to_an_identical_later_reply():
    incoming = [dict(id=str(i), nativeIndex=i, role=role, text=text) for i, (role, text) in enumerate([
        ('user', 'Fix'), ('assistant', 'Working'), ('user', 'More detail')])]
    session = {'messages': [dict(id='web-'+str(i), role=role, text=text) for i, (role, text) in enumerate([
        ('user', 'Fix'), ('user', 'More detail'), ('assistant', 'Working')])]}
    merge_web_history(session, incoming)
    session['messages'].append(dict(id='later', role='assistant', text='Working'))
    incoming.append(dict(id='3', nativeIndex=3, role='assistant', text='Working'))
    merge_web_history(session, incoming)
    assert len(session['messages']) == 4
    assert [m['nativeIndex'] for m in session['messages'] if m['role'] == 'assistant'] == [1, 3]
    assert session['messages'][-1]['id'] == 'later'


async def test_context_failure_survives_generic_wrapper_and_diagnostics(tmp_path):
    app = AppService(tmp_path/'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        records = []
        app.diagnostics.record = lambda stream, event, **kwargs: records.append(event)
        first = {'generation_id': 'context-generation', 'initial_input_id': 'context-input',
                 'input_ids': ['context-input'], 'interruptionRevision': app._session().get('interruptionRevision', 0)}
        second = {**first, 'generation_id': 'next-generation', 'initial_input_id': 'next-input', 'input_ids': ['next-input']}
        for event in [
            {'type': 'generation.started', **first, 'observedAt': 1.0},
            {'type': 'generation.failed', **first, 'observedAt': 2.0, 'error_type': 'ContextLengthError'},
            {'type': 'provider.error', 'error_type': 'ContextLengthError'},
        ]:
            await app.on_runtime_event(*normalize_event(event, sid))
        await app.on_runtime_event('runtime.error', {'sessionId': sid, 'error': 'Manager turn failed; no automatic replay'})
        assert app._session()['errorType'] == 'ContextLengthError'
        assert 'context limit' in app._session()['error']
        assert 'not replayed' in app._session()['error']
        assert any(r['data'].get('errorType') == 'ContextLengthError' for r in records)
        saved = copy.deepcopy({key: app._session()[key] for key in ('error', 'failure', 'collaborationGeneration')})
        for event in [{'type': 'generation.started'}, {'type': 'generation.started', **first, 'observedAt': 3.0}]:
            await app.on_runtime_event(*normalize_event(event, sid))
            assert {key: app._session()[key] for key in saved} == saved, 'Unbound or duplicate starts cannot clear the cause'
        await app.on_runtime_event(*normalize_event({'type': 'generation.started', **second, 'observedAt': 4.0}, sid))
        assert app._session()['collaborationGeneration']['id'] == 'next-generation'
        assert app._session()['collaborationGeneration']['terminal'] is False
        status = app._session()['messageAnnotations']['input-status:next-input']['inputStatus']
        assert status['generationId'] == 'next-generation' and status['phase'] == 'working'
        assert 'error' not in app._session() and 'failure' not in app._session()
        await app.on_runtime_event('runtime.error', {'sessionId': sid, 'generationId': 'next-generation', 'error': 'Another failure'})
        assert app._session()['error'] == 'Another failure'
        assert app._session()['failure']['category'] == 'unknown'
    finally:
        await app.close()


async def test_agent_surface_results_are_focused_and_authorized_appearance_resolution_works(tmp_path):
    app = AppService(tmp_path/'app', workspace=tmp_path)
    queue = None
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        app.clients.attach('browser')
        with app.clients.bind('browser'):
            await app.dispatch('session.select', {'id': sid})
            queue = app.subscribe()
        await report(app, client='browser')
        await initialize(app, client='browser')
        app.state['devices']['browser'] = {'clientId': 'browser', 'updatedAt': 2, 'visibleText': 'Long unrelated UI' * 10000}
        manifest = {'version': 1, 'stateSchema': {'type': 'object'}, 'events': {},
                    'requests': {'apply': {'action': 'appearance.preferences.update', 'schema': {'type': 'object'}}}}
        async def agent(name, args):
            return await app.app_bridge('dispatch', {'action': 'canvas.apps.'+name, 'args': args}, sid)
        response = await agent('create', {'title': 'Synthetic surface', 'content': '<p>Test</p>', 'manifest': manifest, 'initialState': {}})
        assert len(json.dumps(response['state'])) < 2000
        assert response['state']['visibleUI']['clientId'] == 'browser'
        assert 'history' not in response['state']['_stateAccess']
        row = response['result']
        def cas(row):
            return {'id': row['id'], 'expectedRevision': row['app']['revision'], 'expectedStateRevision': row['app']['stateRevision']}
        before = app.appearance.read('browser')
        shared = copy.deepcopy(app.appearance.shared())
        legacy = copy.deepcopy(app._state.get('theme'))
        row = (await agent('request', {**cas(row), 'name': 'apply', 'input': {
            'clientId': 'browser', 'scope': 'client', **local_cas(app, client='browser'), 'patch': {'accent': '#224466'}}}))['result']
        assert row['app']['requests'][-1]['status'] == 'pending'
        assert app.appearance.read('browser')['effective'] == before['effective']
        row = (await agent('resolve', {**cas(row), 'clientId': 'browser', 'requestId': row['app']['requests'][-1]['id'], 'approve': True}))['result']
        request = row['app']['requests'][-1]
        assert request['status'] == 'accepted' and request['resolvedBy'] == 'agent'
        assert request['result']['status'] == 'awaiting-storage'
        assert request['result']['stored'] is False and request['result']['activated'] is False
        assert app.appearance.read('browser')['effective'] == before['effective']
        stored = await ack(app, client='browser')
        assert stored['stored'] and stored['activated'] and not stored['rendered']
        assert app.appearance.read('browser')['effective']['accent'] == '#224466'
        assert app.appearance.shared() == shared
        assert app._state.get('theme') == legacy
        confirmed = app.appearance.read('browser')
        with pytest.raises(AppError):
            await agent('resolve', {**cas(row), 'clientId': 'browser', 'requestId': request['id'], 'approve': True})
        assert app.appearance.read('browser') == confirmed, 'Duplicate resolution cannot queue another preference change'
        assert (await agent('inspect', {'id': row['id']}))['result']['views'] == []
    finally:
        if queue is not None:
            app.unsubscribe(queue)
        await app.close()
