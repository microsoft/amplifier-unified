"""Listener branding is presentation, never app identity or another write route."""
import asyncio
import base64
import copy
import html
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path

from PIL import Image, PngImagePlugin
import pytest

from amplifier_web import instance_branding as branding
from amplifier_web.deployment import DEFAULT_SERVER
from amplifier_web.errors import AppError
from amplifier_web.server import create_app
from test_configuration_authority import app, agent, grant
from test_service import Runtime


def owner(tmp_path, port=8941, **changes):
    return branding.InstanceBranding(tmp_path, {**DEFAULT_SERVER, 'port': port, **changes})


def raster(size=(64, 48), *, format='PNG', metadata=False):
    image = Image.new('RGBA' if format != 'JPEG' else 'RGB', size, '#bada55')
    output = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text('secret', 'must not survive')
    image.save(output, format, **({'pnginfo': info} if metadata else {}))
    return base64.b64encode(output.getvalue()).decode('ascii')


def test_defaults_are_stable_virtual_and_listener_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(branding.socket, 'gethostname', lambda: 'friendly.example.test')
    first = owner(tmp_path)
    assert first.snapshot()['name'] == 'Amplifier · friendly:8941'
    assert first.snapshot() == owner(tmp_path).snapshot()
    assert not list(tmp_path.iterdir()), 'Reading defaults must not create configuration'
    for port in (80, 443):
        assert owner(tmp_path, port).snapshot()['name'] == 'Amplifier · friendly'
    assert owner(tmp_path, 8942).snapshot()['name'] != first.snapshot()['name']
    assert owner(tmp_path, 8942).path != first.path
    assert owner(tmp_path, bind=['::1']).path != first.path
    assert owner(tmp_path, bind=['::1', '127.0.0.1']).path == owner(
        tmp_path, bind=['127.0.0.1', '::1']).path
    # Aliases for accessing a configured listener cannot change its branding.
    assert owner(tmp_path, public_origins=['https://alias.example:8941']).snapshot() == first.snapshot()


def test_persistence_reset_and_other_listener_are_independent(tmp_path, monkeypatch):
    monkeypatch.setattr(branding.socket, 'gethostname', lambda: 'friendly')
    first, second = owner(tmp_path), owner(tmp_path, 8942)
    default, other_default = first.snapshot(), second.snapshot()
    updated = first.update(first.prepare({'name': '  Work station  ', 'color': '#AABBCC', 'image': raster()}))
    assert updated['name'] == 'Work station' and updated['color'] == '#aabbcc'
    assert updated['customIcon']
    assert updated['revision'] != default['revision']
    assert first.path.parent == tmp_path / 'config' / 'services'
    if os.name != 'nt':
        assert first.path.stat().st_mode & 0o777 == 0o600
    assert owner(tmp_path).snapshot() == updated
    assert second.snapshot() == other_default
    assert not second.path.exists()
    assert first.update(first.prepare({'name': ''}))['name'] == default['name']
    assert first.update(reset=True) == default
    assert owner(tmp_path).snapshot() == default
    assert second.snapshot() == other_default
    assert first.update(first.prepare({'image': raster()}))['customIcon']
    assert not first.update(first.prepare({'image': ''}))['customIcon']


@pytest.mark.parametrize('patch', [
    {'name': 'x' * 121}, {'name': 'line\nbreak'}, {'name': 'spoof\u202e'}, {'name': 1},
    {'name': 'line\u2028break'},
    {'color': '#fff'}, {'color': 'red'}, {'color': '#12345g'}, {'image': None},
    {'image': 'data:image/png;base64,AAAA'}, {'image': 'not base64'},
    {'image': base64.b64encode(b'<svg onload="alert(1)"/>').decode()},
    {'image': base64.b64encode(b'<' * (branding.MAX_IMAGE_BYTES + 1)).decode()},
    {'workspaceId': 'not a workspace feature'},
])
def test_invalid_input_does_not_write(tmp_path, patch):
    store = owner(tmp_path)
    before = store.snapshot()
    with pytest.raises(ValueError):
        store.prepare(patch)
    assert store.snapshot() == before
    assert not store.path.exists()


@pytest.mark.parametrize('format', ['PNG', 'JPEG', 'WEBP', 'GIF', 'ICO'])
def test_upload_is_reencoded_to_bounded_metadata_free_png(tmp_path, format):
    store = owner(tmp_path)
    store.update(store.prepare({'image': raster((1024, 512) if format != 'ICO' else (64, 64),
                                               format=format, metadata=format == 'PNG')}))
    record = json.loads(store.path.read_text())
    pixels = base64.b64decode(record['image'])
    assert pixels.startswith(b'\x89PNG\r\n\x1a\n')
    assert b'must not survive' not in pixels
    with Image.open(io.BytesIO(pixels)) as image:
        assert max(image.size) <= 512 and not image.info
    for size in branding.SIZES:
        with Image.open(io.BytesIO(store.icon(size))) as image:
            assert image.size == (size, size) and image.format == 'PNG'
            assert not image.info
    assert len(store._icons) == len(branding.SIZES)


@pytest.mark.parametrize('size', [(4097, 1), (1, 4097), (4097, 4097)])
def test_oversized_dimensions_are_rejected_before_decode(tmp_path, size, monkeypatch):
    encoded = raster(size)
    def unexpected_decode(self, *args, **kwargs):
        raise AssertionError('Oversized image must not be decoded')
    monkeypatch.setattr(Image.Image, 'load', unexpected_decode)
    with pytest.raises(ValueError, match='4096'):
        owner(tmp_path).prepare({'image': encoded})


def test_badge_is_raster_deterministic_and_recolored(tmp_path):
    first, second = owner(tmp_path), owner(tmp_path)
    assert first.icon(192) == second.icon(192)
    original = first.icon(192)
    first.update(first.prepare({'color': '#aabbcc'}))
    assert first.icon(192) != original
    assert first.icon(192) is first.icon(192), 'Encoded icons are cached by revision/size'
    with pytest.raises(ValueError):
        first.icon(2048)


def test_private_write_has_an_owned_root_and_failure_keeps_previous_state(tmp_path, monkeypatch):
    store = owner(tmp_path)
    initial = store.snapshot()
    calls = []
    def refused(path, content, *, owned_root):
        calls.append((path, owned_root))
        raise OSError('private writer refused')
    monkeypatch.setattr(branding, 'write_private', refused)
    with pytest.raises(OSError):
        store.update(store.prepare({'name': 'Not saved'}))
    assert calls == [(store.path, tmp_path)]
    assert store.snapshot() == initial
    assert not store.path.exists()


def test_invalid_saved_record_is_preserved_not_silently_replaced(tmp_path):
    store = owner(tmp_path)
    store.path.parent.mkdir(parents=True)
    broken = '{"version": 19}'
    store.path.write_text(broken)
    with pytest.raises(ValueError, match='unsupported format'):
        owner(tmp_path)
    assert store.path.read_text() == broken


def test_encoding_old_revision_does_not_poison_new_cache(tmp_path, monkeypatch):
    store = owner(tmp_path)
    encode = branding._png
    def changed_during_encode(image):
        store.update(store.prepare({'color': '#abcdef'}))
        return encode(image)
    with monkeypatch.context() as patch:
        patch.setattr(branding, '_png', changed_during_encode)
        old_pixels = store.icon(32)
    assert store.icon(32) != old_pixels
    assert store.icon(32) == store.icon(32)


def test_manifest_changes_presentation_not_identity_or_other_properties(tmp_path):
    template = json.loads((Path(branding.__file__).parent / 'static' / 'manifest.webmanifest').read_text())
    store = owner(tmp_path)
    for name in ('Lab', 'Other machine'):
        store.update(store.prepare({'name': name, 'color': '#aabbcc'}))
        result = store.manifest(template)
        assert result['name'] == result['short_name'] == name
        assert result['id'] == result['start_url'] == result['scope'] == '/'
        assert {k: v for k, v in result.items() if k not in {'name', 'short_name', 'icons'}} == {
            k: v for k, v in template.items() if k not in {'name', 'short_name', 'icons'}}
        for original, icon in zip(template['icons'], result['icons']):
            assert icon == {**original, 'src': store.icon_url(int(icon['sizes'].split('x')[0]))}
    assert template['name'] == 'Amplifier'


class Head(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.nodes = []
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


@pytest.fixture
async def server_app(tmp_path):
    application = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(),
                                   voice=False, background_updates=False,
                                   preload_providers=False, background_work=False)
    yield application
    await application['service'].close()


async def test_public_branding_caches_login_metadata_and_mutation_auth(server_app, aiohttp_client):
    client = await aiohttp_client(server_app)
    store = server_app['service'].instance_branding
    before = store.snapshot()
    for action in ('instanceBranding.update', 'instanceBranding.reset'):
        args = {'patch': {'name': 'Unauthorized'}} if action.endswith('update') else {}
        response = await client.post('/api/actions', json={'action': action, 'args': args})
        assert response.status == 401
    assert store.snapshot() == before and not store.path.exists()
    headers = {'Authorization': 'Bearer ' + server_app['control_token']}
    name = 'Lab <script>& "name" __CSRF__'
    changed = await client.post('/api/actions', headers=headers, json={
        'action': 'instanceBranding.update', 'args': {'patch': {'name': name, 'color': '#123456'}},
        'id': 'brand-server'})
    assert changed.status == 200
    payload = await changed.json()
    assert payload['result'] == payload['state']['instanceBranding'] == store.snapshot()
    for host in ('localhost:12345', '127.0.0.1:54321'):
        response = await client.get('/manifest.webmanifest', headers={
            'Host': host, 'X-Forwarded-Host': 'attacker.example:9999'})
        assert json.loads(await response.text())['name'] == name
    for path in ['/manifest.webmanifest', *(f'/instance-icon/{size}.png?v=old' for size in branding.SIZES)]:
        response = await client.get(path, headers={'If-None-Match': '*'}, allow_redirects=False)
        assert response.status == 200 and response.headers['Cache-Control'] == 'no-store'
        assert 'Set-Cookie' not in response.headers
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        if path.startswith('/instance-icon'):
            assert response.content_type == 'image/png'
            assert (await response.read()).startswith(b'\x89PNG')
        else:
            manifest = json.loads(await response.text())
            assert manifest['name'] == name and manifest['id'] == '/'
    for path in ('/login', '/', '/index.html'):
        response = await client.get(path, headers=headers if path != '/login' else {})
        assert response.status == 200
        page = await response.text()
        assert html.escape(name, quote=True) in page and name not in page
        nodes = Head(page).nodes
        assert ('link', {'rel': 'icon', 'type': 'image/png', 'sizes': '32x32',
                         'href': store.icon_url(32)}) in nodes
        assert ('link', {'rel': 'apple-touch-icon', 'sizes': '180x180',
                         'href': store.icon_url(180)}) in nodes
        assert ('meta', {'name': 'apple-mobile-web-app-title', 'content': name}) in nodes
        assert sum(tag == 'title' for tag, _ in nodes) == 1
    forbidden = await client.post('/api/actions', headers={**headers, 'Origin': 'https://evil.example'},
                                  json={'action': 'instanceBranding.reset', 'args': {}})
    assert forbidden.status == 403
    assert store.snapshot()['name'] == name
    assert (await client.get('/instance-icon/4096.png', allow_redirects=False)).status == 307
    assert (await client.get('/api/state', allow_redirects=False)).status == 401
    assert (await client.get('/', allow_redirects=False)).status == 307


async def test_final_serialized_record_overflow_is_input_error_and_keeps_saved_state(server_app, aiohttp_client, monkeypatch):
    client = await aiohttp_client(server_app)
    headers = {'Authorization': 'Bearer ' + server_app['control_token']}
    store = server_app['service'].instance_branding
    store.update(store.prepare({'name': 'Saved', 'color': '#123456'}))
    before, snapshot = store.path.read_bytes(), store.snapshot()
    server_app['service'].state['instanceBranding'] = snapshot
    monkeypatch.setattr(branding, '_RECORD_LIMIT', len(before))
    response = await client.post('/api/actions', headers=headers, json={
        'action': 'instanceBranding.update', 'args': {'patch': {'name': '\U0001f600' * 120}}})
    assert response.status == 400
    assert 'too large' in (await response.json())['error']
    assert store.path.read_bytes() == before
    assert store.snapshot() == snapshot
    assert server_app['service'].state['instanceBranding'] == snapshot


async def test_actions_broadcast_and_invalidate_all_browser_snapshots_without_visiting_chats(server_app, monkeypatch):
    service = server_app['service']
    queues = []
    for client in ('branding-a', 'branding-b'):
        service.clients.attach(client)
        with service.clients.bind(client):
            service.browser_state()
            queues.append(service.subscribe())
    save = service._save
    scopes = []
    def saved():
        scopes.append((service._publish_save_scope, service._publish_global_keys))
        assert service._publish_save_scope == set()
        assert 'instanceBranding' in service._publish_global_keys
        save()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(service, '_save', saved)
            result = await service.dispatch('instanceBranding.update',
                                            {'patch': {'name': 'Broadcast'}}, command_id='broadcast')
        assert scopes
        expected = result['result']
        for queue in queues:
            assert (await asyncio.wait_for(queue.get(), 1))['instanceBranding'] == expected
        for client in ('branding-a', 'branding-b'):
            with service.clients.bind(client):
                assert service.browser_state()['instanceBranding'] == expected
        duplicate = await service.dispatch('instanceBranding.update',
                                           {'patch': {'name': 'Broadcast'}}, command_id='broadcast')
        assert duplicate['duplicate'] and duplicate['result'] == expected
        reset = await service.dispatch('instanceBranding.reset', {}, command_id='reset')
        assert reset['result']['name'] == expected['defaultName']
        assert reset['state']['instanceBranding'] == reset['result']
    finally:
        for queue in queues:
            service.unsubscribe(queue)


async def test_get_is_passive_and_agent_mutations_require_global_authority(app):
    source = app.state['sessions'][0]
    before = copy.deepcopy(app.state)
    read = await agent(app, source, 'instanceBranding.get', {}, 'read-branding')
    assert read['result'] == app.instance_branding.snapshot()
    assert app.state == before and not app.instance_branding.path.exists()
    for action, args in (('instanceBranding.update', {'patch': {'name': 'Agent'}}),
                         ('instanceBranding.reset', {})):
        with pytest.raises(AppError, match='Configuration authority is required'):
            await agent(app, source, action, args, action + '-denied')
    await grant(app, ['host_defaults'], ['global'])
    updated = await agent(app, source, 'instanceBranding.update',
                          {'patch': {'name': 'Authorized'}}, 'brand-authorized')
    assert updated['result']['name'] == 'Authorized'
    reset = await agent(app, source, 'instanceBranding.reset', {}, 'brand-reset')
    assert reset['result']['name'] == reset['result']['defaultName']