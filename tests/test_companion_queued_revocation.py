"""No newly admitted companion work after revocation while dispatch is queued."""
import asyncio
import uuid

import pytest
from aiohttp import ClientConnectionError

from test_companion_authority import fixture, attach, create_chat, create_payload


async def test_device_client_binding_survives_app_restart(aiohttp_client, tmp_path):
    from amplifier_web.server import create_app
    from test_service import Runtime
    from amplifier_web.device_pairing import challenge
    import secrets

    app, first, headers, device = await fixture(aiohttp_client, tmp_path)
    assert (await attach(first, headers)).status == 200
    await first.close()
    restarted = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                                 background_updates=False, preload_providers=False)
    second = await aiohttp_client(restarted)
    assert restarted['service'].clients.records['native-review']['deviceOwner'] == device['deviceID']
    assert (await attach(second, headers)).status == 200
    assert (await attach(second, headers, 'native-resumed', resumeClientId='native-review')).status == 200
    manager = restarted['device_pairing']
    verifier = secrets.token_urlsafe(32)
    pending = manager.start('Second device after restart', challenge(verifier))
    manager.decide(pending['id'], True)
    other = manager.poll(pending['id'], verifier)
    foreign = {'Authorization': 'Bearer ' + other['token']}
    assert (await attach(second, foreign, 'native-review')).status == 403
    assert (await second.get('/api/companion', headers={**foreign, 'X-Amplifier-Client': 'native-resumed'})).status == 403


@pytest.mark.parametrize('operation', ['create', 'send'])
async def test_revoke_before_service_admission(aiohttp_client, tmp_path, monkeypatch, operation):
    app, client, headers, device = await fixture(aiohttp_client, tmp_path)
    service = app['service']
    assert (await attach(client, headers)).status == 200
    native = {**headers, 'X-Amplifier-Client': 'native-review'}
    sid = str(uuid.uuid4())
    if operation == 'send':
        sid, _ = await create_chat(client, native)
    cid = str(uuid.uuid4())
    if operation == 'create':
        target = '/api/actions'
        payload = create_payload(sid)
        payload['id'] = cid
    else:
        target = '/api/sessions/' + sid + '/commands'
        payload = {'id': cid, 'action': 'conversation.send',
                   'args': {'text': 'QUEUED AFTER REVOKE PROBE', 'via': 'chat',
                            'preserveDraft': True, 'attachmentIds': []}}
    entered = asyncio.Event()
    tasks = []
    original = service.dispatch

    async def observe(*args, **kwargs):
        if kwargs.get('command_id') == cid:
            tasks.append(asyncio.current_task())
            entered.set()
        return await original(*args, **kwargs)

    monkeypatch.setattr(service, 'dispatch', observe)
    request = None
    try:
        async with service.lock:
            request = asyncio.create_task(client.post(target, headers=native, json=payload))
            await asyncio.wait_for(entered.wait(), 3)
            assert tasks and not tasks[0].done()
            # No durable receipt exists and the admission lock is still held.
            assert service.db.execute('SELECT receipt FROM commands WHERE id=?', (cid,)).fetchone() is None
            app['terminal_setup'].devices.revoke(device['deviceID'])
            assert app['terminal_setup'].devices.identify(device['token']) is None
            try:
                response = await asyncio.wait_for(request, 3)
                print(operation, 'HTTP response after revoke', response.status)
            except ClientConnectionError:
                print(operation, 'HTTP disconnected after revoke')
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
        receipt = service.db.execute('SELECT receipt FROM commands WHERE id=?', (cid,)).fetchone()
        print(operation, 'dispatch results', [str(r) if isinstance(r, BaseException) else r for r in results],
              'durable receipt created after revoke', receipt is not None)
        assert receipt is None, 'Revoked, unadmitted command produced a durable receipt'
        if operation == 'create':
            assert all(row['id'] != sid for row in service.state['sessions'])
        else:
            row = service._session(sid)
            assert all(m.get('text') != 'QUEUED AFTER REVOKE PROBE' for m in row.get('messages', []))
    finally:
        for task in [request, *tasks]:
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in [request, *tasks] if t is not None), return_exceptions=True)


async def test_revocation_preserves_accepted_send_and_receipt(aiohttp_client, tmp_path):
    import json
    from test_service import Runtime

    class HeldRuntime(Runtime):
        def __init__(self):
            super().__init__()
            self.started, self.release, self.finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def send(self, *args):
            self.started.set()
            await self.release.wait()
            await super().send(*args)
            self.finished.set()

    runtime = HeldRuntime()
    app, client, headers, device = await fixture(aiohttp_client, tmp_path, runtime)
    service = app['service']
    assert (await attach(client, headers)).status == 200
    native = {**headers, 'X-Amplifier-Client': 'native-review'}
    sid, _ = await create_chat(client, native)
    cid = str(uuid.uuid4())
    payload = {'id': cid, 'action': 'conversation.send', 'args': {
        'text': 'Already accepted', 'via': 'chat', 'preserveDraft': True, 'attachmentIds': []}}
    request = asyncio.create_task(client.post('/api/sessions/' + sid + '/commands', headers=native, json=payload))
    try:
        await asyncio.wait_for(runtime.started.wait(), 5)
        receipt = service.db.execute('SELECT receipt FROM commands WHERE id=?', (cid,)).fetchone()[0]
        assert json.loads(receipt)['accepted'] is True
        app['terminal_setup'].devices.revoke(device['deviceID'])
        runtime.release.set()
        await asyncio.wait_for(runtime.finished.wait(), 5)
        assert len(runtime.sent) == 1
        saved = json.loads(service.db.execute('SELECT receipt FROM commands WHERE id=?', (cid,)).fetchone()[0])
        assert saved['accepted'] is True and saved['inputId'] == cid
        assert saved['delivery'] == 'accepted'
        def must_not_readmit():
            pytest.fail('An existing accepted receipt must not attempt new admission')
        with service.clients.bind('native-review'):
            replay = await service.dispatch('conversation.send', {**payload['args'], 'sessionId': sid},
                command_id=cid, include_state=False, admission_check=must_not_readmit)
        assert replay['duplicate'] is True and replay['inputId'] == cid
        assert len(runtime.sent) == 1
        assert (await client.post('/api/sessions/' + sid + '/commands', headers=native, json=payload)).status == 401
    finally:
        runtime.release.set()
        await asyncio.gather(request, return_exceptions=True)
