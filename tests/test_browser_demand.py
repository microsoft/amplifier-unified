from copy import deepcopy
import json

import pytest

from amplifier_web.browser_demand import project
from amplifier_web.shell_wire import compact
from test_live_clients import live, command, snapshot


def heavy():
    return {'selectedSessionId': 'chat', 'view': {'panel': None},
        'attention': {'sessions': {'chat': 1}, 'workspaces': {'empty': 0, 'busy': 2},
                      'items': [{'id': 'old', 'read': True}, {'id': 'session:chat', 'sessionId': 'chat', 'read': True}, {'id': 'update', 'page': 'updates'}]},
        'updates': {'application': {'current': '1', 'releaseNotes': 'x' * 100000}, 'items': ['x' * 100000]},
        'smartTools': {'installations': ['x' * 100000], 'servers': [{'id': 's', 'name': 'Tool'}]},
        'runtimeControl': {'chat': {'task.get': {'history': ['x' * 100000]}, 'configuration.providers': {'provider': 'ready'}}},
        'sessions': [{'id': 'chat', 'task': {'status': 'blocked', 'blockedReason': 'Dependency', 'corrections': ['x' * 100000]}, 'messages': []}],
        'modelCatalogs': {'provider': [{'id': 'model', 'reasoning': ['high']}]},
        'setup': {'providerCatalogs': {'provider': {'metadata': {'model': {'efforts': ['high']}}}}},
        'events': ['x' * 100000]}


def test_normal_chat_retains_live_facts_without_settings_history():
    state = heavy()
    original = deepcopy(state)
    result = project(dict(state))
    assert state == original
    assert len(json.dumps(result)) < len(json.dumps(state)) / 100
    assert result['attention']['workspaces'] == {'busy': 2}
    assert result['attention']['sessions'] == {'chat': 1}
    assert [item['id'] for item in result['attention']['items']] == ['session:chat']
    assert result['sessions'][0]['task'] == {'status': 'blocked', 'blockedReason': 'Dependency'}
    assert result['modelCatalogs'] == {}
    assert 'providerCatalogs' not in result['setup']
    assert result['runtimeControl']['chat']['configuration.providers'] == {'provider': 'ready'}


def test_off_chat_notification_candidates_are_content_free_before_demand_filter():
    state = heavy()
    state['attention']['items'] += [
        {'id': 'question:q', 'sessionId': 'other', 'fingerprint': 'v1', 'read': False,
         'title': 'Private question', 'detail': 'Private details'},
        {'id': 'approval:a', 'sessionId': 'other', 'fingerprint': 'v2', 'read': True,
         'detail': 'Private arguments'}]
    result = project(state)
    assert result['notificationAttention'] == [
        {'id': 'question:q', 'sessionId': 'other', 'fingerprint': 'v1', 'read': False},
        {'id': 'approval:a', 'sessionId': 'other', 'fingerprint': 'v2', 'read': True}]
    assert not any(row.get('sessionId') == 'other' for row in result['attention']['items'])
    assert 'Private' not in json.dumps(result['notificationAttention'])


@pytest.mark.parametrize('body_size', [32, 100_000])
async def test_bound_browser_receives_published_off_chat_decision_headers(live, monkeypatch, body_size):
    from amplifier_web.attention import session_item_at, session_item_count
    from amplifier_web.directory_attention import DirectoryAttention, receipts
    from amplifier_web.transcript_window import TranscriptWindowReader

    service, first, second = live
    await command(service, 'browser-a', 'session.select', {'id': first})
    await command(service, 'browser-a', 'view.update', {'patch': {'panel': None}})
    assert snapshot(service, 'browser-a')['notificationAttention'] == []
    other = service._session(second)
    private = 'Private off-chat body ' + 'x' * body_size
    other.update(messages=[{'id': 'other-message', 'role': 'user', 'text': private}],
                 approvals=[{'id': 'off-chat-approval', 'status': 'pending', 'title': private}],
                 error=private, errorAt=1, completion={'id': 'other-completion', 'at': 2})
    question = {'id': 'off-chat-question', 'sessionId': second, 'createdAt': 1,
                'status': 'pending', 'required': True, 'revision': 1, 'prompt': private}
    service.questions.store.put(question)
    service.questions.sync({second})
    service._publish_changes(sessions={second})

    def current_headers():
        return [{key: item[key] for key in ('id', 'sessionId', 'fingerprint', 'read')}
                for slot in range(session_item_count(other))
                if (item := session_item_at(other, service._state.get('attentionRead', {}), slot))]

    def observe():
        expected = current_headers()
        commands = service.db.execute('SELECT count(*) FROM commands').fetchone()[0]
        def forbidden(*args, **kwargs):
            raise AssertionError('Ordinary browser projection must not decode notices or load history')
        with monkeypatch.context() as guarded:
            guarded.setattr(DirectoryAttention, 'decode', forbidden)
            guarded.setattr(service.cold_display, 'hydrate', forbidden)
            guarded.setattr('amplifier_web.automatic_history.read_transcript', forbidden)
            guarded.setattr(TranscriptWindowReader, 'read_window', forbidden)
            result = snapshot(service, 'browser-a')
            # The same scoped producer still carries exact reviewed errors, but
            # neither off-chat completions nor decision bodies.
            projected = service.projections.attention(service.state, session_ids=(first,))
            assert [item for item in projected['items'] if item.get('sessionId') == second] == [
                item for item in expected if not item['id'].startswith('completion:')]
            # Existing navigation receipt consumers do not opt into decisions.
            assert receipts(service.directory, service.state, {second}) == [
                item for item in expected if item['id'].startswith(('session:', 'completion:'))]
        candidates = [item for item in expected if item['id'].startswith(('question:', 'approval:'))]
        assert {item['id'] for item in candidates} == {
            'question:off-chat-question', 'approval:off-chat-approval'}
        assert result['notificationAttention'] == candidates
        assert result['selectedSessionId'] == first
        assert all(item.get('sessionId') != second for item in result['attention']['items'])
        assert all(not row.get('messages') for row in result['sessions'] if row['id'] == second)
        assert private not in json.dumps(result)
        assert service.db.execute('SELECT count(*) FROM commands').fetchone()[0] == commands
        assert not result.get('deviceCommands')
        assert not any(record.get('deviceCommands') for record in service.clients.records.values())
        assert not service.runtime.started and not service.runtime.sent
        return candidates

    initial = observe()
    assert all(not item['read'] for item in initial)
    # Explicit acknowledgement changes the scalar read fact, not the decision.
    review = current_headers()
    await command(service, 'browser-a', 'attention.read', {
        'ids': [item['id'] for item in review if not item['id'].startswith('completion:')],
        'fingerprints': {item['id']: item['fingerprint'] for item in review}})
    assert observe() == [{**item, 'read': True} for item in initial]
    question['revision'] += 1
    service.questions.store.put(question)
    service.questions.sync({second})
    other['approvals'][0]['title'] = private + ' changed'
    service._publish_changes(sessions={second})
    changed = observe()
    assert all(not item['read'] for item in changed)
    assert all(item['fingerprint'] != old['fingerprint'] for item, old in zip(changed, initial))
    assert other['questions'][0]['status'] == other['approvals'][0]['status'] == 'pending'
    assert other['messages'][0]['text'] == private


def test_requested_resources_arrive_and_retire_without_deleting_evidence():
    state = heavy()
    for page, key in [('updates', 'updates'), ('smart-tools', 'smartTools')]:
        result = project({**state, 'view': {'panel': 'settings', 'settingsExpanded': [page]}})
        assert result[key] == state[key]
    runtime = project({**state, 'view': {'panel': 'runtime'}})
    assert runtime['runtimeControl']['chat']['task.get'] == {
        'historyCount': 1, 'historyRef': {'action': 'task.history', 'args': {'sessionId': 'chat'}}}
    assert runtime['runtimeControl']['chat']['configuration.providers'] == state['runtimeControl']['chat']['configuration.providers']
    assert runtime['sessions'] == state['sessions']
    activity = project({**state, 'view': {'panel': 'activity'}})
    assert activity['attention']['items'] == state['attention']['items']
    assert 'events' not in project(dict(state))
    assert state['events']


async def test_demand_is_client_scoped_and_full_reads_keep_evidence(live):
    service, first, second = live
    service._state.setdefault('updates', {})['items'] = [{'id': 'large-history', 'detail': 'x' * 100000}]
    await command(service, 'browser-a', 'view.update', {'patch': {'panel': 'settings', 'settingsExpanded': ['updates']}})
    await command(service, 'browser-b', 'view.update', {'patch': {'panel': None}})
    assert snapshot(service, 'browser-a')['updates']['items']
    assert 'items' not in snapshot(service, 'browser-b')['updates']
    with service.clients.bind('browser-b'):
        assert service.get_state()['updates']['items']
    await command(service, 'browser-a', 'view.update', {'patch': {'panel': None}})
    assert 'items' not in snapshot(service, 'browser-a')['updates']
    assert service._state['updates']['items']


def test_shell_dedup_preserves_different_scopes_and_inputs():
    result = {'snapshots': {'a': {'view': {'scope': 'all'}, 'shared': ['x' * 100000], 'generation': 1},
                            'b': {'view': {'scope': 'workspace'}, 'shared': ['x' * 100000], 'generation': 2}}}
    before = deepcopy(result)
    wire = compact(result)
    assert result == before
    assert len(json.dumps(wire)) < len(json.dumps(result)) * .6
    decoded = deepcopy(wire['snapshots'])
    for identity, refs in wire['snapshotRefs'].items():
        for key, index in refs.items():
            decoded[identity][key] = wire['sharedSnapshotValues'][index]
    assert decoded == result['snapshots']


def test_new_chat_does_not_accidentally_match_global_notices():
    state = heavy()
    state['selectedSessionId'] = None
    assert project(state)['attention']['items'] == []


async def test_delta_attach_has_one_authoritative_stream_baseline(authenticated_client, tmp_path):
    from amplifier_web.server import create_app
    from test_service import Runtime
    from test_live_clients import read_event
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False, preload_providers=False)
    client = await authenticated_client(app)
    headers = {'X-Amplifier-State-Transport': 'delta-v1'}
    response = await client.post('/api/clients/attach', json={'clientId': 'web'}, headers=headers)
    assert response.status == 200
    assert 'state' not in await response.json()
    stream = await client.get('/api/events?transport=delta-v1&clientId=web')
    initial = await read_event(stream)
    stream.close()
    assert initial['client']['id'] == 'web'
    assert 'sessions' in initial and 'view' in initial
    # Older and integration clients retain the original attach contract.
    response = await client.post('/api/clients/attach', json={'clientId': 'api', 'kind': 'api'})
    assert 'state' in await response.json()


def test_settings_canvas_demands_same_resources_as_modal():
    state = heavy()
    state.update(registry={'modules': ['fixture']}, diagnostics={'enabled': True})
    state['setup'].update(configurationTarget={'kind': 'global'}, operations={'providers.list:': {'phase': 'ready'}})
    for page in ('ai-connections', 'routing', 'registries', 'diagnostics', 'updates', 'smart-tools'):
        view = {'settingsExpanded': [page]}
        modal = project({**state, 'view': {**view, 'panel': 'settings'}})
        canvas = project({**state, 'view': {**view, 'panel': None}, 'canvas': {'kind': 'settings', 'open': True}})
        for field in ('setup', 'registry', 'diagnostics', 'updates', 'smartTools'):
            assert canvas.get(field) == modal.get(field), (page, field)
    for canvas, extra in (({'kind': 'settings', 'open': False}, {}),
                          ({'kind': 'text', 'open': True}, {}),
                          ({'kind': 'settings', 'open': True}, {'canvasDraft': {'library': True}})):
        hidden = project({**state, 'view': {'settingsExpanded': ['diagnostics'], **extra}, 'canvas': canvas})
        assert 'diagnostics' not in hidden


async def test_chat_runtime_pages_receive_results_only_for_the_requesting_client(live):
    service, first, second = live
    sid = service.state['selectedSessionId']
    receipts = {'catalog.inspect': {'tools': [{'name': 'desktop'}]},
                'tool.invoke': {'result': {'success': True, 'output': 'doctor observation'}},
                'budget.get': {'maxIterations': 10}, 'goals.get': {'goal': {'condition': 'saved'}}}
    service._state.setdefault('runtimeControl', {})[sid] = deepcopy(receipts)
    await command(service, 'browser-b', 'view.update', {'patch': {'panel': None}})
    for panel in ('chat-models', 'chat-work', 'chat-context', 'chat-computer'):
        await command(service, 'browser-a', 'view.update', {'patch': {'panel': panel}})
        assert snapshot(service, 'browser-a')['runtimeControl'][sid] == receipts
        assert snapshot(service, 'browser-b')['runtimeControl'][sid] == {}
    for panel in (None, 'chat-advanced', 'chat-help'):
        await command(service, 'browser-a', 'view.update', {'patch': {'panel': panel}})
        assert snapshot(service, 'browser-a')['runtimeControl'][sid] == {}
    assert service._state['runtimeControl'][sid] == receipts
