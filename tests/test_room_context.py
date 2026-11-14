"""Real SDK/HTTPS reads of synthetic source-owned rooms, without a model."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import ssl
import sys

import aiohttp
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import pytest

from amplifier_web.acp_participant import ACPParticipant
from amplifier_web.deployment import validate_server
from amplifier_web.room_context import PATH, REQUEST_LIMIT
from amplifier_web.server import create_app
from amplifier_web.tls import setup_local_ca, ssl_context
from test_service import Runtime


@pytest.fixture
async def host(tmp_path, aiohttp_server, monkeypatch):
    config = setup_local_ca(tmp_path, validate_server({'public_origins': ['https://localhost']}))
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), server_config=config,
        voice=False, background_work=False, background_updates=False, preload_providers=False)
    server = await aiohttp_server(app, ssl=ssl_context(tmp_path, config))
    service = app['service']
    service.server_config['public_origins'] = [str(server.make_url('')).rstrip('/')]
    ca = tmp_path / 'config/tls/ca.crt'
    monkeypatch.setenv('SSL_CERT_FILE', str(ca))
    service.server_config['externalAgents'] = [
        {'id': identity, 'label': identity.title(), 'command': sys.executable,
         'args': [str(Path(__file__).parent / 'fixtures/room_context_acp_agent.py'), str(tmp_path / identity), str(ca)]}
        for identity in ('alpha', 'beta')]
    receipt = await service.dispatch('session.create', {'workspace': str(tmp_path),
        'externalAgentIds': ['alpha', 'beta']}, command_id='create-room')
    session = service._session(receipt['result']['sessionId'])
    session['messages'] = [{'id': f'public-{i}', 'role': 'user', 'roomSequence': i + 1,
        'createdAt': i, 'text': 'older-source-only' if i == 0 else f'public {i}',
        'inputActor': {'kind': 'human', 'id': 'test-person'}, 'inputOrigin': 'ui'} for i in range(45)]
    session['_externalRoom'] = {'sequence': 45, 'deliveries': []}
    yield app, service, session, server, ssl.create_default_context(cafile=str(ca))


@asynccontextmanager
async def reader(host, *, room=None, participant='alpha'):
    _, service, session, _, trust = host
    descriptor = service.room_context.descriptor(room or session['id'], participant)
    headers = {row['name']: row['value'] for row in descriptor['headers']}
    async with httpx2.AsyncClient(headers=headers, verify=trust) as http:
        async with streamable_http_client(descriptor['url'], http_client=http) as streams:
            async with ClientSession(*streams) as client:
                await client.initialize()
                yield client


async def call(client, name='conversation_read', **args):
    return (await client.call_tool(name, args)).model_dump(by_alias=True)


async def test_sdk_pages_actual_public_source_and_continues_text_without_browser_grants(host):
    _, service, session, _, _ = host
    long_text = '🌱' * 6000
    session['messages'][-1].update(role='assistant', agentId='beta', agentLabel='Beta', text=long_text,
        permission={'secret': 'not-public'}, inputStatus={'detail': 'private'}, attachments=[{'secret': 'private'}])
    session['messages'].extend([
        {'id': 'tool', 'role': 'tool', 'text': 'private tool', 'roomSequence': 46},
        {'id': 'draft', 'role': 'assistant', 'text': 'private draft', 'partial': True, 'roomSequence': 47}])
    original = deepcopy(session)
    async with reader(host) as client:
        names = [tool.name for tool in (await client.list_tools()).tools]
        assert names == ['conversation_read', 'conversation_read_text']
        page = (await call(client))['structuredContent']
        assert 'older-source-only' not in json.dumps(page) and page['hasEarlier']
        final = page['items'][-1]
        assert final['author'] == {'id': 'beta', 'isAgent': True}
        assert final['text'] == long_text[:2048] and final['textTruncated']
        assert not any(key in json.dumps(page) for key in ('permission', 'inputStatus', 'private tool', 'private draft', 'attachments', 'textDetail'))
        text = (await call(client, 'conversation_read_text', **final['continuation']))['structuredContent']
        assert final['text'] + text['value'] == long_text and text['nextOffset'] is None
        assert text['messageId'] == final['id'] and text['conversationId'] == session['id']
        # Guessing a non-public row from the same retained page is still refused.
        denied = await call(client, 'conversation_read_text', message_id='draft',
            window_key=final['continuation']['window_key'], digest=hashlib.sha256(b'private draft').hexdigest())
        assert denied['isError'] and 'private draft' not in json.dumps(denied)
        while page['before'] is not None:
            page = (await call(client, before=page['before']))['structuredContent']
        assert page['items'][0]['text'] == 'older-source-only'
        assert page['items'][0]['author'] == {'id': 'test-person', 'isAgent': False}
    assert session == original and service.runtime.sent == []


async def test_real_acp_http_tool_forwarding_resume_revocation_and_failed_mount_keep_identity(host, tmp_path):
    _, service, session, _, _ = host
    selected = service.server_config['externalAgents'][0]
    updates, binding = [], {}
    async def update(value): updates.append(value)
    async def permission(_): pytest.fail('Source membership is not a per-read permission request')
    async def save(value): binding.update(value)
    peer = ACPParticipant(selected, str(tmp_path), update=update, permission=permission)
    source = lambda: service.room_context.descriptor(session['id'], 'alpha')
    initial = source()
    try:
        await peer.connect(binding, save, room_context=source)
        await peer.prompt('Retrieve the oldest public source item.')
        first_id = binding['sessionId']
        assert binding['capabilities']['roomContext']
        assert updates[-1]['content']['text'] == 'older-source-only'
        await peer.close()
        await peer.connect(binding, save, room_context=source)
        assert binding['sessionId'] == first_id and source() == initial
        session['externalAgent']['participants'] = [row for row in session['externalAgent']['participants'] if row['id'] != 'alpha']
        await peer.prompt('Read the current source again.')
        assert updates[-1]['content']['text'] == 'Source unavailable'
        assert service.room_context.tokens == {}
        await peer.close()
        # Attachment rejection may not silently create a new remote session.
        (tmp_path / 'alpha/reject-mount').touch()
        from acp import RequestError
        with pytest.raises(RequestError, match='attachment refused'):
            await peer.connect(binding, save, room_context=lambda: initial)
        assert binding['sessionId'] == first_id
    finally:
        await peer.close()
    operations = [json.loads(line) for line in (tmp_path / 'alpha/operations.jsonl').read_text().splitlines()]
    assert [row['kind'] for row in operations] == ['new', 'prompt', 'read', 'resume', 'prompt', 'read-refused']
    assert all(row.get('sessionId') == first_id for row in operations)
    assert 'older-source-only' not in json.dumps([row for row in operations if row['kind'] == 'prompt'])


@pytest.mark.parametrize('change', ['membership', 'endpoint', 'origin'])
async def test_revocation_during_source_read_discards_body_and_later_requests_before_read(host, monkeypatch, change):
    _, service, session, server, trust = host
    descriptor = service.room_context.descriptor(session['id'], 'alpha')
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.history.windows.read_room
    reads = []
    async def held(*args, **kwargs):
        reads.append(True)
        result = await original(*args, **kwargs)
        entered.set()
        await release.wait()
        return result
    monkeypatch.setattr(service.history.windows, 'read_room', held)
    async with reader(host) as client:
        task = asyncio.create_task(call(client))
        await asyncio.wait_for(entered.wait(), 5)
        if change == 'membership': session['externalAgent']['participants'].pop(0)
        elif change == 'endpoint': service.server_config['externalAgents'][0]['args'].append('changed-source')
        else: service.server_config['public_origins'] = ['https://new.example']
        release.set()
        result = await task
        assert result['isError'] and 'public 44' not in json.dumps(result)
    async with aiohttp.ClientSession() as http:
        response = await http.post(server.make_url(PATH), ssl=trust, json={}, headers={
            row['name']: row['value'] for row in descriptor['headers']})
        assert response.status == 403 and reads == [True]
    assert not service.closed and not service.room_context.tokens


async def test_tokens_cannot_cross_rooms_or_control_host_and_source_errors_are_sanitized(host, monkeypatch):
    app, service, session, server, trust = host
    other = await service.dispatch('session.create', {'workspace': session['workspace'],
        'externalAgentIds': ['alpha', 'beta']}, command_id='other-room')
    other_id = other['result']['sessionId']
    service._session(other_id)['messages'] = [{'id': 'other', 'role': 'user', 'roomSequence': 1, 'text': 'other-room-only'}]
    async with reader(host) as one, reader(host, room=other_id) as two:
        first, second = await asyncio.gather(call(one), call(two))
        assert first['structuredContent']['conversationId'] == session['id']
        assert second['structuredContent']['items'][0]['text'] == 'other-room-only'
        rejected = await call(one, room=other_id)
        assert rejected['structuredContent']['conversationId'] == session['id']
        assert 'other-room-only' not in json.dumps(rejected)
        async def fail(*args, **kwargs): raise OSError('/private/source/secret-path')
        monkeypatch.setattr(service.history.windows, 'read_room', fail)
        result = await call(one)
        assert result['isError'] and 'secret-path' not in json.dumps(result)
    headers = {row['name']: row['value'] for row in service.room_context.descriptor(session['id'], 'alpha')['headers']}
    async with aiohttp.ClientSession() as http:
        assert (await http.get(server.make_url('/api/state'), ssl=trust, headers=headers)).status == 401
        assert (await http.post(server.make_url(PATH), ssl=trust, json={}, headers={'Authorization': 'Bearer ' + app['control_token']})).status == 403
        assert (await http.post(server.make_url(PATH), ssl=trust, data=b'x' * (REQUEST_LIMIT + 1), headers=headers)).status == 413
        assert (await http.post(server.make_url(PATH), ssl=trust, data=b'{', headers={**headers, 'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})).status == 400


async def test_actual_http_disconnect_has_bounded_reader_lifetime_and_keeps_host_alive(host, monkeypatch):
    _, service, session, server, trust = host
    from amplifier_web import room_context
    monkeypatch.setattr(room_context, 'REQUEST_TIMEOUT', .2)
    entered, finished = asyncio.Event(), asyncio.Event()
    original = service.history.windows.read_room
    async def held(*args, **kwargs):
        entered.set()
        try: await asyncio.Event().wait()
        finally: finished.set()
    monkeypatch.setattr(service.history.windows, 'read_room', held)
    descriptor = service.room_context.descriptor(session['id'], 'alpha')
    headers = {row['name']: row['value'] for row in descriptor['headers']}
    headers.update({'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
                    'MCP-Protocol-Version': '2025-11-25'})
    async with aiohttp.ClientSession() as http:
        task = asyncio.create_task(http.post(server.make_url(PATH), ssl=trust, headers=headers,
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'conversation_read', 'arguments': {}}}))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    await asyncio.wait_for(finished.wait(), 3)
    monkeypatch.setattr(service.history.windows, 'read_room', original)
    assert not service.closed and service.runtime.stopped == []
    assert service.room_context.descriptor(session['id'], 'alpha') == descriptor
    async with reader(host) as client:
        assert not (await call(client)).get('isError')
    await server.close()
    assert service.room_context.closed and not service.room_context.tokens


async def test_optional_origin_and_http_support_do_not_change_plain_peer_chat(host, tmp_path):
    _, service, session, _, _ = host
    service.server_config['public_origins'] = []
    assert service.room_context.descriptor(session['id'], 'alpha') is None
    service.server_config['public_origins'] = ['http://127.0.0.1:8000', 'https://not-the-primary.example']
    assert service.room_context.descriptor(session['id'], 'alpha') is None
    from test_acp_participation import participant
    peer, save, binding, updates, _ = participant(tmp_path)
    def forbidden(): pytest.fail('A peer without HTTP support must not mint a credential')
    try:
        await peer.connect({}, save, room_context=forbidden)
        assert binding['capabilities']['roomContext'] is False
        await peer.prompt('ordinary chat')
        assert updates[-1]['content']['text'] == 'ordinary chat'
    finally:
        await peer.close()
