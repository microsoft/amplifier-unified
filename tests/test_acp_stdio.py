"""The installed ACP launcher attaches once and preserves uncertain delivery."""
import asyncio
import json
from types import SimpleNamespace

import aiohttp
from aiohttp import web
import pytest

from amplifier_web import acp_stdio, cli


class Writer:
    def __init__(self):
        self.data = bytearray()
        self.changed = asyncio.Event()

    def write(self, value):
        self.data.extend(value)
        self.changed.set()

    async def drain(self):
        pass


async def test_real_websocket_relay_eof_does_not_cancel_or_replay(aiohttp_server):
    received, connected, detached = [], asyncio.Event(), asyncio.Event()
    async def attach(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        connected.set()
        async for msg in ws:
            received.append(json.loads(msg.data))
            await ws.send_json({'id': received[-1]['id'], 'jsonrpc': '2.0', 'result': {}})
        detached.set()
        return ws
    app = web.Application()
    app.router.add_get('/api/acp', attach)
    server = await aiohttp_server(app)
    reader, writer = asyncio.StreamReader(), Writer()
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(server.make_url('/api/acp')) as ws:
            task = asyncio.create_task(acp_stdio.relay(ws, reader, writer))
            request = {'jsonrpc': '2.0', 'id': 1, 'method': 'session/prompt', 'params': {'sessionId': 'saved', 'prompt': []}}
            reader.feed_data((json.dumps(request) + '\n').encode())
            await asyncio.wait_for(writer.changed.wait(), 2)
            reader.feed_eof()
            await asyncio.wait_for(task, 2)
    await asyncio.wait_for(detached.wait(), 2)
    assert received == [request]
    assert json.loads(writer.data) == {'id': 1, 'jsonrpc': '2.0', 'result': {}}


async def test_redirect_is_not_followed_and_stdio_is_not_opened(aiohttp_server, tmp_path, monkeypatch):
    followed = []
    async def redirect(request):
        raise web.HTTPFound('/elsewhere')
    async def elsewhere(request):
        followed.append(True)
        return web.Response()
    app = web.Application()
    app.router.add_get('/api/acp', redirect)
    app.router.add_get('/elsewhere', elsewhere)
    server = await aiohttp_server(app)
    credential = tmp_path/'token'
    credential.write_text('synthetic-owner')
    args = SimpleNamespace(server=str(server.make_url('')).rstrip('/'), token_file=str(credential), ca_file=None,
                           workspace=str(tmp_path), bundle=None)
    async def forbidden(**_):
        pytest.fail('A rejected attachment must not start consuming ACP input')
    monkeypatch.setattr('acp.stdio.stdio_streams', forbidden)
    with pytest.raises(ValueError, match='redirected'):
        await acp_stdio.run(args, tmp_path)
    assert followed == []


def test_remote_plaintext_refuses_before_reading_credentials(tmp_path):
    args = SimpleNamespace(server='http://example.test:8000', token_file=str(tmp_path/'absent'), ca_file=None)
    with pytest.raises(ValueError, match='HTTPS'):
        acp_stdio.connection_options(args, tmp_path)


def test_cli_acp_is_attachment_command(monkeypatch):
    monkeypatch.setattr('sys.argv', ['amplifier-unified', '--workspace', '/project', 'acp',
                                   '--server', 'https://host.test', '--token-file', '/private/token'])
    args = cli._parse()
    assert (args.command, args.workspace, args.server, args.token_file) == (
        'acp', '/project', 'https://host.test', '/private/token')
