"""HTTP opt-in must bound construction without changing legacy snapshot reads."""
import json

from amplifier_web.server import create_app
from test_service import Runtime


async def test_shell_resource_opt_in_preserves_legacy_and_rejects_bad_demand(
        authenticated_client, tmp_path, monkeypatch):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=Runtime(), voice=False, background_updates=False)
    client = await authenticated_client(app)
    service = app['service']
    for identity in ('demand-browser', 'legacy-browser', 'invalid-browser'):
        service.clients.attach(identity)
    calls = []
    original = service.shell.inspect

    def inspect(*args, **kwargs):
        calls.append(kwargs.get('navigation_resources'))
        return original(*args, **kwargs)

    monkeypatch.setattr(service.shell, 'inspect', inspect)
    requested = ['pinnedNavigation', 'recentNavigation', 'workspaceShortcuts']
    response = await client.get('/api/shell', params={
        'clientId': 'demand-browser', 'navigationResources': json.dumps(requested)})
    assert response.status == 200
    snapshot = (await response.json())['snapshots']['chats']
    assert snapshot['navigationResources'] == sorted(requested)
    assert 'recentNavigation' in snapshot
    assert 'chatNavigation' not in snapshot
    assert 'homeNavigation' not in snapshot
    assert calls == [frozenset(requested)]

    # Compact is only wire deduplication, never permission to omit resources.
    legacy = await client.get('/api/shell', params={'clientId': 'legacy-browser'})
    assert legacy.status == 200
    full = (await legacy.json())['snapshots']['chats']
    assert 'navigationResources' not in full
    assert 'chatNavigation' in full and 'homeNavigation' in full
    compact = await client.get('/api/shell', params={'clientId': 'legacy-browser', 'compact': '1'})
    assert compact.status == 200
    assert calls[-2:] == [None, None]

    before = len(calls)
    for demand in ('not-json', '{}', '["unknown"]', '[1]', '[]' + ' ' * 4096):
        invalid = await client.get('/api/shell', params={
            'clientId': 'invalid-browser', 'navigationResources': demand})
        assert invalid.status == 400
    assert len(calls) == before
    assert service.shell.get('client', 'invalid-browser') is None
