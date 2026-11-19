"""Forwarding headers are read only from configured reverse-proxy peers."""
import copy

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from amplifier_web import browser_entry
from amplifier_web.deployment import DEFAULT_SERVER, validate_server, validate_trusted_proxies
from amplifier_web.forwarding import VIA_PROXY, forwarded_request
from amplifier_web.server import create_app
from test_service import Runtime


class Transport:
    def __init__(self, peer, sock='127.0.0.1'):
        self.ends = {'peername': (peer, 50000), 'sockname': (sock, 8941)}

    def get_extra_info(self, name, default=None):
        return self.ends.get(name, default)


def app_with(*proxies):
    app = web.Application()
    app['trusted_proxies'] = tuple(__import__('ipaddress').ip_network(p) for p in proxies)
    return app


async def forward(app, peer, headers):
    seen = []

    async def handler(request):
        seen.append(request)
        return web.Response()

    request = make_mocked_request('GET', '/', headers={'Host': '127.0.0.1:8941', **headers},
                                  app=app, transport=Transport(peer))
    response = await forwarded_request(request, handler)
    return response, (seen[0] if seen else None)


PROXIED = {'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'unified.example',
           'X-Forwarded-For': '203.0.113.7'}


def test_trusted_proxies_accepts_only_exact_addresses_and_networks():
    assert validate_trusted_proxies([]) == []
    assert validate_trusted_proxies(['127.0.0.1', '10.0.0.0/8', '::1', '127.0.0.1/32', '::1']) == [
        '127.0.0.1', '10.0.0.0/8', '::1']
    assert validate_server(copy.deepcopy(DEFAULT_SERVER))['trusted_proxies'] == []
    for bad in ['localhost', '10.0.0.1/8', ' 127.0.0.1', '', 1, None]:
        with pytest.raises(ValueError, match='trusted_proxies'):
            validate_trusted_proxies([bad])
    for everyone in ['0.0.0.0/0', '::/0']:
        with pytest.raises(ValueError, match='every address'):
            validate_trusted_proxies([everyone])
    with pytest.raises(ValueError, match='list'):
        validate_trusted_proxies('127.0.0.1')


async def test_headers_are_ignored_without_configured_proxies():
    response, request = await forward(app_with(), '127.0.0.1', PROXIED)
    assert response.status == 200 and request.scheme == 'http'
    assert request.host == '127.0.0.1:8941' and request.remote == '127.0.0.1'
    assert not request.get(VIA_PROXY)


async def test_headers_are_ignored_from_an_untrusted_peer():
    response, request = await forward(app_with('10.0.0.5'), '127.0.0.1', PROXIED)
    assert response.status == 200 and request.scheme == 'http'
    assert request.host == '127.0.0.1:8941' and not request.get(VIA_PROXY)


@pytest.mark.parametrize('peer', ['127.0.0.1', '::ffff:127.0.0.1'])
async def test_trusted_peer_supplies_scheme_host_and_client(peer):
    response, request = await forward(app_with('127.0.0.1'), peer, PROXIED)
    assert response.status == 200
    assert request.scheme == 'https' and request.secure
    assert request.host == 'unified.example' and request.remote == '203.0.113.7'
    assert request[VIA_PROXY]
    # The proxy connects from this computer for a browser that may be anywhere.
    assert not browser_entry._local_sockets(request)


async def test_client_is_nearest_untrusted_hop_read_right_to_left():
    headers = {**PROXIED, 'X-Forwarded-For': '198.51.100.1, 203.0.113.7, 10.0.0.9'}
    _, request = await forward(app_with('127.0.0.1', '10.0.0.0/8'), '127.0.0.1', headers)
    assert request.remote == '203.0.113.7'


@pytest.mark.parametrize('headers', [
    {'X-Forwarded-Proto': 'https, http'},
    {'X-Forwarded-Proto': 'ftp'},
    {'X-Forwarded-Host': 'unified.example/path'},
    {'X-Forwarded-Host': 'a.example, b.example'},
    {'X-Forwarded-For': 'not-an-address'},
])
async def test_malformed_headers_from_a_trusted_peer_are_rejected(headers):
    response, request = await forward(app_with('127.0.0.1'), '127.0.0.1', headers)
    assert response.status == 400 and request is None


async def server(aiohttp_client, root, **settings):
    config = validate_server({**copy.deepcopy(DEFAULT_SERVER),
                              'public_origins': ['https://unified.example'], **settings})
    app = await create_app(root, workspace=root, runtime=Runtime(), server_config=config,
                           voice=False, background_updates=False, preload_providers=False,
                           background_work=False)
    return await aiohttp_client(app)


async def test_behind_a_trusted_proxy_the_public_origin_passes_every_boundary(tmp_path, aiohttp_client):
    client = await server(aiohttp_client, tmp_path, trusted_proxies=['127.0.0.1', '::1'])
    headers = {**PROXIED, 'Origin': 'https://unified.example'}
    assert (await client.get('/api/health', headers=headers)).status == 200
    # Exact Origin and Host checks still run against the forwarded address.
    for changed in ({'Origin': 'https://attacker.example'}, {'Origin': 'http://unified.example'},
                    {'X-Forwarded-Host': 'attacker.example'}):
        response = await client.get('/api/health', headers={**headers, **changed})
        assert response.status == 403


async def test_without_trusted_proxies_forwarded_headers_change_nothing(tmp_path, aiohttp_client):
    client = await server(aiohttp_client, tmp_path)
    response = await client.get('/api/health', headers={**PROXIED, 'Origin': 'https://unified.example'})
    assert response.status == 403
