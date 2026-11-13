"""Direct voice signaling respects installation repair without provider traffic."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from aiohttp import web
import pytest

from amplifier_web import installation_repair as repair
from amplifier_web.voice import VoiceCall, setup_routes
from test_voice import Service


@pytest.fixture
def voice_host(tmp_path, monkeypatch):
    service = Service()
    service.data_dir = tmp_path
    service.default_workspace = tmp_path
    service.state['sessions'][0]['workspace'] = str(tmp_path)
    service._refresh_shared_preferences = Mock()
    app = web.Application()
    app['service'] = service
    manager = setup_routes(app)
    manager.api_key = 'synthetic-no-network'
    manager.http = SimpleNamespace(
        request=Mock(side_effect=AssertionError('Unexpected provider HTTP')),
        ws_connect=AsyncMock(side_effect=AssertionError('Unexpected provider WebSocket')),
    )
    manager.owns_http = False
    manager.request = AsyncMock(side_effect=AssertionError('Unexpected provider request'))
    guard = repair.guard
    admissions = []

    def checked_guard(host):
        assert host.lock.locked()
        admissions.append(repair.held(host))
        return guard(host)

    monkeypatch.setattr('amplifier_web.voice.repair_guard', checked_guard)
    return SimpleNamespace(
        service=service, manager=manager, admissions=admissions,
        handlers={route.resource.canonical: route.handler
                  for route in app.router.routes()},
    )


async def connect(host, provider):
    request = SimpleNamespace(json=AsyncMock(return_value={
        'sdp': 'v=0', 'provider': provider, 'sessionId': 'main',
        'attemptId': 'repair-test',
    }))
    return await host.handlers['/api/voice/connect'](request)


def assert_repair_conflict(response, host):
    assert response.status == 409
    body = json.loads(response.text)
    assert body['code'] == 'installation_repair_held'
    assert 'Installation repair owns this host' in body['error']
    host.manager.request.assert_not_called()
    host.manager.http.request.assert_not_called()
    host.manager.http.ws_connect.assert_not_called()
    assert repair.held(host.service)


@pytest.mark.parametrize('provider', ['live', 'realtime'])
async def test_direct_connect_rejects_initial_repair_hold(voice_host, provider):
    host = voice_host
    host.service._installation_repair_gate = 'a' * 32
    before = copy.deepcopy(host.service.state['voice'])

    response = await connect(host, provider)

    assert_repair_conflict(response, host)
    assert host.admissions == [True]
    assert host.manager.call is None
    assert host.service.state['voice'] == before
    assert not host.service.statuses


@pytest.mark.parametrize('provider', ['live', 'realtime'])
async def test_provider_creation_rechecks_late_repair_hold(voice_host, monkeypatch, provider):
    host = voice_host
    entered, proceed = asyncio.Event(), asyncio.Event()
    create = VoiceCall.create

    async def delayed_create(call, sdp, selected):
        entered.set()
        await proceed.wait()
        return await create(call, sdp, selected)

    monkeypatch.setattr(VoiceCall, 'create', delayed_create)
    pending = asyncio.create_task(connect(host, provider))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert host.service.state['voice']['status'] == 'connecting'
        async with host.service.lock:
            host.service._installation_repair_gate = 'a' * 32
        proceed.set()
        response = await pending

        assert_repair_conflict(response, host)
        assert host.admissions == [False, True]
        assert host.manager.call.closed
        assert host.service.statuses[-1]['status'] == 'error'
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def test_repair_hold_keeps_voice_configuration_and_end_available(voice_host):
    host = voice_host
    host.service._installation_repair_gate = 'a' * 32
    call = SimpleNamespace(
        id='held-call', attempt_id='repair-test',
        close=AsyncMock(return_value={'closed': True, 'workContinues': True}),
    )
    host.manager.call = call

    response = await host.handlers['/api/voice/config'](SimpleNamespace())
    assert response.status == 200
    request = SimpleNamespace(json=AsyncMock(return_value={'attemptId': 'repair-test'}))
    response = await host.handlers['/api/voice/end'](request)
    assert response.status == 200
    assert json.loads(response.text)['closed'] is True
    call.close.assert_awaited_once_with()
    assert host.admissions == []
    host.manager.request.assert_not_called()
    host.manager.http.request.assert_not_called()
    host.manager.http.ws_connect.assert_not_called()
    assert repair.held(host.service)