"""Exercise the actual TLS/file path, including cold transfers and validators."""
import asyncio
from contextlib import asynccontextmanager
import gzip
import re
import ssl

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from amplifier_web.deployment import load_server_config
from amplifier_web.static_files import BundledStaticResource, TRANSFER_BYTES
from amplifier_web.tls import setup_local_ca, ssl_context


@asynccontextmanager
async def file_host(tmp_path, *, tls=True, app=None):
    root = tmp_path / 'static'
    root.mkdir(exist_ok=True)
    config = setup_local_ca(tmp_path / 'server', load_server_config(tmp_path / 'server')) if tls else None
    server_ssl = ssl_context(tmp_path / 'server', config) if tls else None
    client_ssl = ssl.create_default_context(cafile=tmp_path / 'server/config/tls/ca.crt') if tls else None
    if app is None:
        app = web.Application()
        app.router.register_resource(BundledStaticResource('', root))
    server = TestServer(app, scheme='https' if tls else 'http')
    await server.start_server(ssl=server_ssl)
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=client_ssl),
                                         auto_decompress=False) as client:
            yield server, client, root
    finally:
        await server.close()


@pytest.mark.parametrize('tls', [False, True])
async def test_cold_asset_uses_bounded_reads_and_preserves_http_semantics(tmp_path, monkeypatch, tls):
    async with file_host(tmp_path, tls=tls) as (server, client, root):
        body = bytes(range(251)) * 12000
        (root / 'client.js').write_bytes(body)
        loop = asyncio.get_running_loop()
        original = loop.run_in_executor
        reads = []

        def record(executor, func, *args):
            if func.__name__ in {'read', 'readinto', '_seek_and_read'}:
                reads.append((func.__name__, args[-1] if func.__name__ != 'readinto' else len(args[-1])))
            return original(executor, func, *args)

        monkeypatch.setattr(loop, 'run_in_executor', record)
        url = server.make_url('/client.js')
        async with client.get(url) as response:
            assert response.status == 200
            assert await response.read() == body
            assert response.content_length == len(body)
            etag = response.headers['ETag']
            modified = response.headers['Last-Modified']
        if tls:
            assert reads
            # Independent workload budget: a 3 MB startup asset must not
            # require dozens of executor handoffs or an unbounded buffer.
            assert len(reads) <= 4
            assert all(size <= 1024 * 1024 for _, size in reads)
        for method, headers, status, expected in [
            ('HEAD', {}, 200, b''),
            ('GET', {'If-None-Match': etag}, 304, b''),
            ('GET', {'If-Modified-Since': modified}, 304, b''),
            ('GET', {'If-Match': '"other"'}, 412, b''),
            ('GET', {'Range': 'bytes=11-29'}, 206, body[11:30]),
            ('GET', {'Range': 'bytes=-17'}, 206, body[-17:]),
            ('GET', {'Range': f'bytes={len(body)}-'}, 416, b''),
        ]:
            async with client.request(method, url, headers=headers) as response:
                assert response.status == status
                assert await response.read() == expected
                if status == 206:
                    assert response.headers['Content-Range'].endswith(f'/{len(body)}')


async def test_static_confinement_and_precompressed_delivery(tmp_path):
    async with file_host(tmp_path) as (server, client, root):
        secret = tmp_path / 'secret.txt'
        secret.write_text('outside static root')
        (root / 'link.txt').symlink_to(secret)
        (root / 'directory').mkdir()
        for path, status in [('/link.txt', 404), ('/%2e%2e/secret.txt', 404),
                             ('/missing.js', 404), ('/directory/', 403)]:
            async with client.get(server.make_url(path)) as response:
                assert response.status == status
                assert 'outside static root' not in await response.text()
        body = b'console.log("synthetic asset");'
        (root / 'app.js').write_bytes(body)
        compressed = gzip.compress(body)
        (root / 'app.js.gz').write_bytes(compressed)
        async with client.get(server.make_url('/app.js'), headers={'Accept-Encoding': 'gzip'}) as response:
            assert response.headers['Content-Encoding'] == 'gzip'
            assert await response.read() == compressed


async def test_tls_disconnect_does_not_break_later_file_request(tmp_path):
    async with file_host(tmp_path) as (server, client, root):
        body = b'x' * (4 * TRANSFER_BYTES + 1)
        (root / 'large.js').write_bytes(body)
        response = await client.get(server.make_url('/large.js'))
        assert await response.content.read(100)
        response.close()
        async with client.get(server.make_url('/large.js')) as next_response:
            assert await next_response.read() == body


async def test_bundled_index_tls_and_auth_boundaries(tmp_path):
    from amplifier_web.server import create_app
    from test_service import Runtime

    app = await create_app(tmp_path / 'app', runtime=Runtime(), voice=False,
                           background_updates=False, background_work=False, preload_providers=False)
    async with file_host(tmp_path, app=app) as (server, client, _):
        async with client.get(server.make_url('/'), allow_redirects=False) as response:
            assert response.status == 307
        headers = {'Authorization': 'Bearer ' + app['control_token']}
        async with client.get(server.make_url('/'), headers=headers) as response:
            assert response.status == 200
            html = await response.text()
            assert '<div id="root">' in html
            assert response.headers['Cache-Control'] == 'no-cache'
            assert response.headers['X-Content-Type-Options'] == 'nosniff'
        asset = re.search(r'src="(/assets/[^"]+\.js)"', html).group(1)
        async with client.get(server.make_url(asset), allow_redirects=False) as response:
            assert response.status == 307
        async with client.get(server.make_url(asset), headers=headers) as response:
            assert response.status == 200
            assert len(await response.read()) > 1000
        async with client.get(server.make_url('/'), headers={**headers, 'Host': 'untrusted.test'}) as response:
            assert response.status == 403
