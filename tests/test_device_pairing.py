import re
import secrets
import time
import uuid

import pytest
from aiohttp import web

from amplifier_web.auth import SESSION_COOKIE, new_session
from amplifier_web.device_pairing import Pairings, challenge
from amplifier_web.server import create_app
from amplifier_web.terminal_devices import TerminalDevices
from test_service import Runtime


def test_pairing_proof_expiry_retry_revocation_and_restart(tmp_path):
    devices = TerminalDevices(tmp_path)
    manager = Pairings(devices)
    verifier = secrets.token_urlsafe(32)
    issued = manager.start('My Mac', challenge(verifier))
    identity = issued['id']
    assert manager.poll(identity, verifier) == {'status': 'pending'}
    with pytest.raises(web.HTTPForbidden):
        manager.poll(identity, secrets.token_urlsafe(32))
    manager.decide(identity, True)
    result = manager.poll(identity, verifier)
    assert result['scope'] == 'companion-v1'
    assert manager.poll(identity, verifier) == result
    manager.decide(identity, True)
    assert len(devices.listing()) == 1
    assert result['token'] not in devices.path.read_text()
    assert verifier not in devices.path.read_text()
    with pytest.raises(web.HTTPGone):
        Pairings(devices).poll(identity, verifier)
    devices.revoke(result['deviceID'])
    assert manager.poll(identity, verifier) == {'status': 'revoked'}
    manager.pending[identity]['expires'] = time.time() - 1
    with pytest.raises(web.HTTPGone):
        manager.poll(identity, verifier)


def test_denied_and_bounded_requests(tmp_path):
    manager = Pairings(TerminalDevices(tmp_path))
    verifier = secrets.token_urlsafe(32)
    item = manager.start('Mac', challenge(verifier))
    manager.decide(item['id'], False)
    manager.decide(item['id'], True)
    assert manager.poll(item['id'], verifier) == {'status': 'denied'}
    assert not manager.devices.listing()
    for _ in range(19):
        manager.start('Mac', challenge(verifier))
    with pytest.raises(web.HTTPTooManyRequests):
        manager.start('Mac', challenge(verifier))


async def test_browser_approval_and_scoped_authority(aiohttp_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=Runtime(), voice=False, background_updates=False)
    client = await aiohttp_client(app)
    verifier = secrets.token_urlsafe(32)
    issued = await (await client.post('/api/devices/pair', json={'name': '<My Mac>', 'challenge': challenge(verifier)})).json()
    target = issued['verificationPath']
    assert (await client.get(target, allow_redirects=False)).status == 307
    cookie = new_session(app['session_secret'])
    headers = {'Cookie': SESSION_COOKIE + '=' + cookie}
    page = await client.get(target, headers=headers)
    body = await page.text()
    assert page.status == 200 and '&lt;My Mac&gt;' in body
    assert issued['code'] in body and 'same-origin' == page.headers['Referrer-Policy']
    csrf = re.search(r'name="csrf" value="([^"]+)"', body)[1]
    assert (await client.post(target, headers=headers, data={'decision': 'allow'})).status == 403
    wrong_cookie = {'Cookie': SESSION_COOKIE + '=' + new_session(app['session_secret'])}
    assert (await client.post(target, headers=wrong_cookie, data={'decision': 'allow', 'csrf': csrf})).status == 403
    assert (await client.post(target, headers=headers, data={'decision': 'allow', 'csrf': csrf})).status == 200
    token = await (await client.post('/api/devices/pair/token', json={'id': issued['id'], 'verifier': verifier})).json()
    native = {'Authorization': 'Bearer ' + token['token'], 'X-Amplifier-Client': 'tempo-test', 'X-Amplifier-State-Transport': 'delta-v1'}
    attached = await client.post('/api/clients/attach', headers=native,
                                json={'clientId': 'tempo-test', 'kind': 'native', 'protocolVersion': 1})
    assert attached.status == 200
    assert (await client.get('/api/companion', headers=native)).status in {200, 503}
    assert (await client.get('/api/state', headers=native)).status == 403
    assert (await client.get('/devices', headers=native)).status == 403
    assert (await client.get('/api/conversation/detail?part=tools', headers=native)).status == 403
    for action, args in [('terminal.devices', {}), ('settings.update', {}),
                         ('session.create', {'location': {'kind': 'local', 'path': '/tmp'}, 'select': False})]:
        response = await client.post('/api/actions', headers=native, json={'id': 'blocked', 'action': action, 'args': args})
        assert response.status == 403
    denied = await client.post('/api/sessions/test/commands', headers=native,
                              json={'id': 'blocked', 'action': 'approval.respond', 'args': {}})
    assert denied.status == 403
    sid = str(uuid.uuid4())
    created = await client.post('/api/actions', headers=native, json={'id': str(uuid.uuid4()), 'action': 'session.create',
        'args': {'id': sid, 'title': 'Paired native chat', 'location': {'kind': 'managed'}, 'select': False}})
    assert created.status == 200
    assert (await created.json())['accepted']
    sent = await client.post('/api/sessions/' + sid + '/commands', headers=native,
        json={'id': str(uuid.uuid4()), 'action': 'conversation.send',
              'args': {'text': 'Synthetic paired reply', 'via': 'chat', 'preserveDraft': True, 'attachmentIds': []}})
    assert sent.status == 200
    assert (await sent.json())['accepted']
    # Deny a restricted bearer even alongside a valid owner browser cookie.
    assert (await client.get('/api/state', headers={**native, **headers})).status == 403
    listing = await (await client.get('/devices', headers=headers)).text()
    assert 'Chat companion' in listing and '&lt;My Mac&gt;' in listing
    csrf = re.search(r'name="csrf" value="([^"]+)"', listing)[1]
    response = await client.post('/devices', headers=headers, data={'csrf': csrf, 'id': token['deviceID']}, allow_redirects=False)
    assert response.status == 303
    assert (await client.get('/api/companion', headers={**native, **headers})).status == 401
    # Existing terminal registrations retain their established full access.
    grant, _ = app['terminal_setup'].devices.grant('Terminal', '1' * 32)
    legacy = app['terminal_setup'].devices.redeem(grant)
    assert (await client.get('/api/state', headers={'Authorization': 'Bearer ' + legacy['token']})).status == 200


async def test_pairing_rejects_cross_site_requests(aiohttp_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=Runtime(), voice=False, background_updates=False)
    client = await aiohttp_client(app)
    payload = {'name': 'Mac', 'challenge': challenge(secrets.token_urlsafe(32))}
    assert (await client.post('/api/devices/pair', json=payload, headers={'Origin': 'https://evil.example'})).status == 403
    assert (await client.post('/api/devices/pair', json=payload, headers={'Sec-Fetch-Site': 'cross-site'})).status == 403
    # Cross-site browser navigation may reach sign-in; it never approves anything.
    response = await client.get('/devices/pair?id=missing', allow_redirects=False,
        headers={'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'})
    assert response.status == 307
