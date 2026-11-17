"""Listener presentation respects the host's existing single-owner data roots."""
import copy
from contextlib import asynccontextmanager

from amplifier_web.deployment import DEFAULT_SERVER
from amplifier_web.server import create_app
from test_service import Runtime


@asynccontextmanager
async def listener(root, port, aiohttp_client):
    config = {**copy.deepcopy(DEFAULT_SERVER), 'port': port}
    app = await create_app(root, workspace=root, runtime=Runtime(),
                           server_config=config, voice=False, background_updates=False,
                           preload_providers=False, background_work=False)
    client = await aiohttp_client(app)
    try:
        yield app, client
    finally:
        await client.close()


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
    assert read.status == 200
    snapshot = (await read.json())['result']
    state = await (await client.get('/api/state', headers=headers)).json()
    manifest = await (await client.get('/manifest.webmanifest')).json()
    icon = await (await client.get(snapshot['iconUrl'])).read()
    assert state['instanceBranding'] == snapshot
    assert manifest['name'] == manifest['short_name'] == snapshot['name']
    assert manifest['id'] == manifest['start_url'] == '/'
    assert manifest['icons'][0]['src'] == snapshot['iconUrl']
    return snapshot, icon


async def reset(app, client):
    response = await client.post('/api/actions', headers={
        'Authorization': 'Bearer ' + app['control_token']},
        json={'action': 'instanceBranding.reset', 'args': {}})
    assert response.status == 200


async def test_two_listeners_with_independent_data_roots_survive_restart(tmp_path, aiohttp_client):
    first_root, second_root = tmp_path / 'studio', tmp_path / 'travel'
    async with (listener(first_root, 8410, aiohttp_client) as (first, first_client),
                listener(second_root, 8411, aiohttp_client) as (second, second_client)):
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
    async with (listener(first_root, 8410, aiohttp_client) as (first, first_client),
                listener(second_root, 8411, aiohttp_client) as (second, second_client)):
        assert await inspect(first, first_client) == first_saved
        assert await inspect(second, second_client) == second_saved
        await reset(first, first_client)
        assert (await inspect(first, first_client))[0]['name'] == first_before[0]['name']
        assert await inspect(second, second_client) == second_saved


async def test_data_root_reopened_on_other_port_keeps_separate_branding(tmp_path, aiohttp_client):
    # Publishing requires one active owner per data root. Switch ports only
    # after cleanup; branding must retain both namespaces without weakening it.
    async with listener(tmp_path, 8410, aiohttp_client) as (app, client):
        first_default = await inspect(app, client)
        assert first_default[0]['name'].endswith(':8410')
        await change(app, client, 'Studio', '#123456')
        first_saved = await inspect(app, client)

    async with listener(tmp_path, 8411, aiohttp_client) as (app, client):
        second_default = await inspect(app, client)
        assert second_default[0]['name'].endswith(':8411')
        assert second_default[0]['name'] != first_saved[0]['name']
        await change(app, client, 'Travel', '#654321')
        second_saved = await inspect(app, client)
        assert first_saved[1] != second_saved[1]

    async with listener(tmp_path, 8410, aiohttp_client) as (app, client):
        assert await inspect(app, client) == first_saved
        await reset(app, client)
        assert await inspect(app, client) == first_default

    async with listener(tmp_path, 8411, aiohttp_client) as (app, client):
        assert await inspect(app, client) == second_saved

    async with listener(tmp_path, 8410, aiohttp_client) as (app, client):
        assert await inspect(app, client) == first_default