from copy import deepcopy
import json

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
    assert result['modelCatalogs'] == state['modelCatalogs']
    assert result['setup']['providerCatalogs'] == state['setup']['providerCatalogs']
    assert result['runtimeControl']['chat']['configuration.providers'] == {'provider': 'ready'}


def test_requested_resources_arrive_and_retire_without_deleting_evidence():
    state = heavy()
    for page, key in [('updates', 'updates'), ('smart-tools', 'smartTools')]:
        result = project({**state, 'view': {'panel': 'settings', 'settingsExpanded': [page]}})
        assert result[key] == state[key]
    runtime = project({**state, 'view': {'panel': 'runtime'}})
    assert runtime['runtimeControl'] == state['runtimeControl']
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
