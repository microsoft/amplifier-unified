"""Restricted companion authority regressions; all state is synthetic."""
import asyncio
import importlib.util
import json
from pathlib import Path
import secrets
import uuid

import pytest
from aiohttp import ClientConnectionError

from amplifier_web.device_pairing import challenge
from amplifier_web.server import create_app
from amplifier_web.runtime import SessionInUseError
from test_service import Runtime


async def fixture(aiohttp_client, tmp_path, runtime=None):
    app = await create_app(tmp_path, workspace=tmp_path, runtime=runtime or Runtime(),
                           voice=False, background_updates=False, preload_providers=False)
    client = await aiohttp_client(app)
    manager = app['device_pairing']
    verifier = secrets.token_urlsafe(32)
    request = manager.start('Synthetic restricted companion', challenge(verifier))
    manager.decide(request['id'], True)
    device = manager.poll(request['id'], verifier)
    headers = {'Authorization': 'Bearer ' + device['token']}
    return app, client, headers, device


async def attach(client, headers, identity='native-review', **extra):
    return await client.post('/api/clients/attach', headers=headers,
        json={'clientId': identity, 'kind': 'native', 'protocolVersion': 1, **extra})


def create_payload(sid):
    return {'id': str(uuid.uuid4()), 'action': 'session.create',
        'args': {'id': sid, 'title': 'Synthetic review chat', 'location': {'kind': 'managed'}, 'select': False}}


async def create_chat(client, headers):
    sid = str(uuid.uuid4())
    response = await client.post('/api/actions', headers=headers, json=create_payload(sid))
    assert response.status == 200, await response.text()
    return sid, await response.json()


async def send(client, headers, sid):
    return await client.post('/api/sessions/' + sid + '/commands', headers=headers,
        json={'id': str(uuid.uuid4()), 'action': 'conversation.send',
              'args': {'text': 'Synthetic security probe', 'via': 'chat', 'preserveDraft': True, 'attachmentIds': []}})


async def test_attach_cannot_return_full_state(aiohttp_client, tmp_path):
    app, client, headers, _ = await fixture(aiohttp_client, tmp_path)
    assert (await client.get('/api/state', headers=headers)).status == 403
    response = await attach(client, headers)
    payload = await response.json()
    print('attach', response.status, 'state keys', sorted(payload.get('state', {})))
    assert 'state' not in payload


async def test_create_cannot_return_full_state(aiohttp_client, tmp_path):
    app, client, headers, _ = await fixture(aiohttp_client, tmp_path)
    assert (await attach(client, headers, includeState=False)).status == 200
    _, payload = await create_chat(client, {**headers, 'X-Amplifier-Client': 'native-review'})
    print('create state keys', sorted(payload.get('state', {})))
    assert 'state' not in payload


@pytest.mark.parametrize('mode', ['existing-id', 'resume-id'])
async def test_companion_cannot_copy_private_browser_draft(aiohttp_client, tmp_path, mode):
    app, client, headers, _ = await fixture(aiohttp_client, tmp_path)
    service = app['service']
    owner = {'Authorization': 'Bearer ' + app['control_token'], 'X-Amplifier-Client': 'browser-private',
             'X-Amplifier-State-Transport': 'delta-v1'}
    response = await client.post('/api/clients/attach', headers=owner,
        json={'clientId': 'browser-private', 'kind': 'web', 'protocolVersion': 1})
    assert response.status == 200
    sid, _ = await create_chat(client, owner)
    with service.clients.bind('browser-private'):
        await service.dispatch('session.select', {'id': sid}, include_state=False)
        await service.dispatch('view.update', {'patch': {'draft': 'PRIVATE UNSENT BROWSER DRAFT'}}, include_state=False)
    target = 'browser-private' if mode == 'existing-id' else 'native-copy'
    extra = {} if mode == 'existing-id' else {'resumeClientId': 'browser-private'}
    native = {**headers, 'X-Amplifier-Client': target, 'X-Amplifier-State-Transport': 'delta-v1'}
    attached = await attach(client, native, target, **extra)
    if attached.status in (401, 403):
        return
    assert attached.status == 200
    result = await send(client, native, sid)
    data = await result.json()
    print(mode, 'attach', attached.status, 'send', result.status,
          'private draft returned', 'PRIVATE UNSENT BROWSER DRAFT' in json.dumps(data))
    assert 'PRIVATE UNSENT BROWSER DRAFT' not in json.dumps(data)


async def test_busy_error_cannot_return_full_state(aiohttp_client, tmp_path):
    class LockedRuntime(Runtime):
        async def send(self, *args):
            raise SessionInUseError({'app': 'synthetic-cli'})
    app, client, headers, _ = await fixture(aiohttp_client, tmp_path, LockedRuntime())
    native = {**headers, 'X-Amplifier-Client': 'native-review', 'X-Amplifier-State-Transport': 'delta-v1'}
    assert (await attach(client, native)).status == 200
    sid, _ = await create_chat(client, native)
    response = await send(client, native, sid)
    payload = await response.json()
    print('busy', response.status, payload.get('code'), 'state keys', sorted(payload.get('state', {})))
    assert response.status == 409 and payload.get('code') == 'session_busy'
    assert 'state' not in payload


async def test_revoking_while_reading_body_prevents_new_command(aiohttp_client, tmp_path, monkeypatch):
    import amplifier_web.device_pairing as pairing
    app, client, headers, device = await fixture(aiohttp_client, tmp_path)
    native = {**headers, 'X-Amplifier-Client': 'native-review', 'X-Amplifier-State-Transport': 'delta-v1'}
    assert (await attach(client, native)).status == 200
    reading, release = asyncio.Event(), asyncio.Event()
    original = pairing.permits_companion
    async def observed(request):
        reading.set()  # Observe admission only; real request.json awaits the real HTTP body.
        return await original(request)
    monkeypatch.setattr(pairing, 'permits_companion', observed)
    sid = str(uuid.uuid4())
    encoded = json.dumps(create_payload(sid)).encode()
    async def chunks():
        yield encoded[:1]
        await release.wait()
        yield encoded[1:]
    request = asyncio.create_task(client.post('/api/actions', headers={**native, 'Content-Type': 'application/json'}, data=chunks()))
    try:
        await asyncio.wait_for(reading.wait(), 3)
        devices = app['terminal_setup'].devices
        devices.revoke(device['deviceID'])
        assert devices.identify(device['token']) is None
        release.set()
        try:
            response = await asyncio.wait_for(request, 3)
            result = await response.json()
            print('after revoke', response.status, 'accepted', result.get('accepted'))
            assert response.status in (401, 403) and not result.get('accepted')
        except ClientConnectionError:
            assert all(row['id'] != sid for row in app['service'].state['sessions'])
    finally:
        release.set()
        if not request.done():
            request.cancel()
        await asyncio.gather(request, return_exceptions=True)


async def test_companion_token_rejected_by_previous_host_auth(aiohttp_client, tmp_path, monkeypatch):
    # Exact baseline auth middleware, imported under its own package-relative name.
    import amplifier_web.server as server
    source = Path(__file__).parent / 'fixtures' / 'pre_pairing_auth.py'
    spec = importlib.util.spec_from_file_location('amplifier_web.review813_baseline_auth', source)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    monkeypatch.setattr(server, 'auth_required', baseline.auth_required)
    app, client, headers, _ = await fixture(aiohttp_client, tmp_path)
    response = await client.get('/api/state', headers=headers)
    data = await response.json()
    print('baseline auth', response.status, 'response keys', sorted(data))
    assert response.status in (401, 403)


@pytest.mark.parametrize('selector', ['header', 'query'])
@pytest.mark.parametrize('foreign_kind', ['web', 'device'])
async def test_foreign_client_selection_rejected(aiohttp_client, tmp_path, selector, foreign_kind):
    app, client, headers, device = await fixture(aiohttp_client, tmp_path)
    other_headers = {'Authorization': 'Bearer ' + app['control_token']}
    if foreign_kind == 'device':
        manager = app['device_pairing']
        verifier = secrets.token_urlsafe(32)
        pending = manager.start('Other device', challenge(verifier))
        manager.decide(pending['id'], True)
        other = manager.poll(pending['id'], verifier)
        other_headers = {'Authorization': 'Bearer ' + other['token']}
    response = await client.post('/api/clients/attach', headers=other_headers,
        json={'clientId': 'foreign', 'kind': 'native' if foreign_kind == 'device' else 'web', 'protocolVersion': 1})
    assert response.status == 200
    assert (await attach(client, headers)).status == 200
    target = '/api/companion'
    if selector == 'header':
        headers = {**headers, 'X-Amplifier-Client': 'foreign'}
    else:
        target += '?clientId=foreign'
    assert (await client.get(target, headers=headers)).status == 403
    assert (await attach(client, headers, 'foreign')).status == 403
    assert (await attach(client, headers, 'copy', resumeClientId='foreign')).status == 403


async def test_device_binding_persists_and_same_device_can_resume(aiohttp_client, tmp_path):
    from amplifier_web.client_views import ClientViews
    app, client, headers, device = await fixture(aiohttp_client, tmp_path)
    assert (await attach(client, headers)).status == 200
    service = app['service']
    restored = ClientViews(service)
    assert restored.records['native-review']['deviceOwner'] == device['deviceID']
    assert (await attach(client, headers, 'native-resumed', resumeClientId='native-review')).status == 200
    assert service.clients.records['native-resumed']['deviceOwner'] == device['deviceID']
    assert (await client.get('/api/activity', headers={**headers, 'X-Amplifier-Client': 'native-resumed'})).status in (200, 503)
    assert (await client.get('/api/activity', headers=headers)).status == 403


async def test_successful_send_only_returns_receipt(aiohttp_client, tmp_path):
    app, client, headers, _ = await fixture(aiohttp_client, tmp_path)
    assert (await attach(client, headers)).status == 200
    native = {**headers, 'X-Amplifier-Client': 'native-review'}
    sid, created = await create_chat(client, native)
    assert created['sessionId'] == sid and created['accepted'] is True
    result = await send(client, native, sid)
    payload = await result.json()
    assert result.status == 200 and payload['accepted'] is True
    assert set(payload) <= {'accepted', 'revision', 'sessionId', 'hostInstanceId', 'stateRevision', 'delivery', 'code'}
