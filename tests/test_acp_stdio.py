"""The installed ACP launcher attaches once and preserves uncertain delivery."""
import asyncio
import json
import sys
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


@pytest.mark.skipif(sys.platform == 'win32', reason='Windows owner attachment requires its fixed private TLS layout')
async def test_local_custom_certificate_uses_existing_leaf_pin(aiohttp_server, tmp_path):
    """A DNS-only private certificate must work on the configured local bind."""
    import datetime
    import ssl
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from amplifier_web.deployment import save_server_config

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'backend.example')])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('backend.example')]), critical=False)
        .sign(key, hashes.SHA256()))
    cert_file, key_file = tmp_path/'leaf.crt', tmp_path/'leaf.key'
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    received = []
    async def endpoint(request):
        received.append((request.headers['Authorization'], request.headers['Host']))
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        await socket.send_json({'connected': True})
        await socket.close()
        return socket
    app = web.Application()
    app.router.add_get('/api/acp', endpoint)
    server = await aiohttp_server(app, ssl=context)
    home = tmp_path/'app'
    save_server_config(home, {'bind': ['127.0.0.1'], 'port': server.port,
        'tls': {'method': 'ca', 'cert': str(cert_file), 'key': str(key_file)}})
    token = home/'config/auth/control-token'
    token.parent.mkdir()
    token.write_text('synthetic-local-owner')
    args = SimpleNamespace(server=None, token_file=None, ca_file=None)
    url, secret, pin, host = acp_stdio.connection_options(args, home)
    assert isinstance(pin, aiohttp.Fingerprint)
    async with aiohttp.ClientSession() as client:
        async with client.ws_connect(url+'/api/acp', ssl=pin,
                headers={'Authorization': 'Bearer '+secret, 'Host': host}) as socket:
            assert await socket.receive_json() == {'connected': True}
    assert received == [('Bearer synthetic-local-owner', f'localhost:{server.port}')]


@pytest.mark.skipif(sys.platform == 'win32', reason='Windows owner attachment requires private HTTPS')
def test_local_options_follow_ipv6_only_bind(tmp_path):
    from amplifier_web.deployment import save_server_config
    save_server_config(tmp_path, {'bind': ['::1'], 'port': 23456})
    token = tmp_path/'config/auth/control-token'
    token.parent.mkdir()
    token.write_text('synthetic-owner')
    args = SimpleNamespace(server=None, token_file=None, ca_file=None)
    url, _, _, host = acp_stdio.connection_options(args, tmp_path)
    assert url == 'http://[::1]:23456'
    assert host == 'localhost:23456'
