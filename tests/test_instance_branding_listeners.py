"""Real listeners sharing a data root keep independent PWA presentation."""
import copy

from amplifier_web.deployment import DEFAULT_SERVER
from amplifier_web.server import create_app
from test_service import Runtime


async def test_two_services_share_data_not_branding_and_survive_restart(tmp_path, aiohttp_client):
    async def start(port):
        config = {**copy.deepcopy(DEFAULT_SERVER), 'port': port}
        app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(),
                               server_config=config, voice=False, background_updates=False,
                               preload_providers=False, background_work=False)
        return app, await aiohttp_client(app)

    async def change(app, client, name, color):
        response = await client.post('/api/actions', headers={
            'Authorization': 'Bearer ' + app['control_token']}, json={
                'action': 'instanceBranding.update', 'args': {'patch': {'name': name, 'color': color}}})
        assert response.status == 200
        return (await response.json())['result']

    async def inspect(app, client):
        headers = {'Authorization': 'Bearer ' + app['control_token']}
        read = await client.post('/api/actions', headers=headers,
                                 json={'action': 'instanceBranding.get', 'args': {}})
        snapshot = (await read.json())['result']
        state = await (await client.get('/api/state', headers=headers)).json()
        manifest = await (await client.get('/manifest.webmanifest')).json()
        icon = await (await client.get(snapshot['iconUrl'])).read()
        assert state['instanceBranding'] == snapshot
        assert manifest['name'] == manifest['short_name'] == snapshot['name']
        assert manifest['id'] == manifest['start_url'] == '/'
        assert manifest['icons'][0]['src'] == snapshot['iconUrl']
        return snapshot, icon

    first, first_client = await start(8410)
    second, second_client = await start(8411)
    try:
        first_before = await inspect(first, first_client)
        second_before = await inspect(second, second_client)
        assert first_before[0]['name'].endswith(':8410')
        assert second_before[0]['name'].endswith(':8411')
        await change(first, first_client, 'Studio', '#123456')
        assert await inspect(second, second_client) == second_before
        await change(second, second_client, 'Travel', '#654321')
        first_saved = await inspect(first, first_client)
        second_saved = await inspect(second, second_client)
        assert first_saved[0]['name'] == 'Studio'
        assert second_saved[0]['name'] == 'Travel'
        assert first_saved[1] != second_saved[1]
    finally:
        await first_client.close()
        await second_client.close()

    first, first_client = await start(8410)
    second, second_client = await start(8411)
    try:
        assert await inspect(first, first_client) == first_saved
        assert await inspect(second, second_client) == second_saved
        response = await first_client.post('/api/actions', headers={
            'Authorization': 'Bearer ' + first['control_token']},
            json={'action': 'instanceBranding.reset', 'args': {}})
        assert response.status == 200
        assert (await inspect(first, first_client))[0]['name'] == first_before[0]['name']
        assert await inspect(second, second_client) == second_saved
    finally:
        await first_client.close()
        await second_client.close()