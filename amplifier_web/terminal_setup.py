"""Compiler-free terminal installation prepared by the authenticated host."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
import re
import time
import uuid
from urllib.parse import urlsplit

from aiohttp import web

from .deployment import is_loopback, validate_origin, write_private
from .terminal_devices import TerminalDevices
from .terminal_release import latest_release
from .tls import ca_bytes

UV_VERSION = '0.12.17'
PLATFORMS = {
    'macos-arm64': {
        'label': 'Mac with Apple silicon · macOS 26 or later', 'system': 'Darwin',
        'tag': 'macosx_26_0_arm64', 'architectures': ('arm64',),
        'uv': 'uv-aarch64-apple-darwin',
        'uvSha256': '85f00cbdc6dd3e97eba4c31b4d014375a9fdfe8f570023b84e5102fc3456896b',
    },
    'linux-arm64': {
        'label': 'Linux ARM64 · glibc (including Spark)', 'system': 'Linux',
        'tag': 'linux_aarch64', 'architectures': ('aarch64', 'arm64'),
        'uv': 'uv-aarch64-unknown-linux-gnu',
        'uvSha256': 'd636d1b678e9e7f367ecb22b46bd1cabbed234d6bc3b4d96365d2b507f72f86c',
    },
}


def require_safe_transport(request):
    host = urlsplit('//' + request.host).hostname or ''
    if not request.secure and not (is_loopback(host) and is_loopback(request.remote or '')):
        raise web.HTTPForbidden(text='Open this setup through trusted HTTPS before connecting a terminal.')


class TerminalSetup:
    def __init__(self, data_dir, allowed_origins, tls_method='none'):
        self.data_dir = Path(data_dir)
        self.directory = self.data_dir / 'config/auth/terminal-installers'
        self.devices = TerminalDevices(data_dir)
        self.allowed_origins = frozenset(allowed_origins)
        self.tls_method = tls_method
        self.lock = asyncio.Lock()

    async def release(self, platform):
        return await latest_release(PLATFORMS[platform], self.data_dir / 'cache/terminal-releases/artifacts')

    def clean(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for path in self.directory.glob('*.json'):
            row = json.loads(path.read_text())
            if row['expiresAt'] <= time.time():
                path.with_suffix('.sh').unlink(missing_ok=True)
                path.unlink(missing_ok=True)

    async def perform(self, action, args, command_id=None):
        if action == 'terminal.devices':
            return {'accepted': True, 'devices': self.devices.listing()}
        if action == 'terminal.revoke':
            self.devices.revoke(args['id'])
            return {'accepted': True, 'devices': self.devices.listing()}
        if action != 'terminal.prepare':
            raise ValueError('Unknown terminal operation')
        server = validate_origin(args['server'])
        if server not in self.allowed_origins or (urlsplit(server).scheme != 'https' and not is_loopback(urlsplit(server).hostname)):
            raise ValueError('Choose this service through a configured, trusted HTTPS address.')
        platform = args['platform']
        if platform not in PLATFORMS:
            raise ValueError('This platform does not have a qualified connected-client release yet.')
        name = args.get('name', '').strip()
        if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise ValueError('Give this terminal connection a name, up to 80 characters.')
        # Persist only download identity/receipt, never enrollment or device
        # credentials in the service's conversation/action state.
        identity = hashlib.sha256(str(command_id).encode()).hexdigest()[:32] if command_id else uuid.uuid4().hex
        fingerprint = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
        async with self.lock:
            self.clean()
            receipt_path = self.directory / (identity + '.json')
            if receipt_path.exists():
                row = json.loads(receipt_path.read_text())
                if row['fingerprint'] != fingerprint:
                    raise ValueError('This setup request was already used with different settings.')
                return row['receipt']
            release = await self.release(platform)
            certificate = ca_bytes(self.data_dir) if self.tls_method == 'ca' else None
            if self.tls_method == 'ca' and certificate is None:
                raise ValueError('The service local CA is unavailable. Restore its certificate before preparing terminal setup.')
            grant, expires = self.devices.grant(name, identity)
            profile = {'server': server, 'grant': grant, 'name': name,
                       'ca': (certificate or b'').decode(), 'expiresAt': expires,
                       'clientVersion': release.version}
            script = self.render(platform, profile, release)
            filename = 'Amplifier-Terminal-' + identity[:8] + '.sh'
            receipt = {'accepted': True, 'installer': {'id': identity, 'filename': filename,
                'downloadUrl': '/api/terminal/installers/' + identity, 'platform': platform,
                'server': server, 'expiresAt': expires, 'version': release.version}}
            write_private(self.directory / (identity + '.sh'), script)
            write_private(receipt_path, json.dumps({'expiresAt': expires, 'fingerprint': fingerprint, 'receipt': receipt}))
            return receipt

    def render(self, platform, profile, release):
        template = (Path(__file__).parent / 'terminal_install.sh').read_text()
        fields = {'PROFILE': json.dumps(profile).encode(), 'WHEEL': release.wheel,
                  'INSTALLER': (Path(__file__).parent / 'terminal_install_client.py').read_bytes()}
        for key, value in fields.items():
            template = template.replace('__' + key + '_BASE64__', base64.b64encode(value).decode())
        spec = PLATFORMS[platform]
        for key, value in {'SYSTEM': spec['system'], 'WHEEL_NAME': release.filename,
                           'WHEEL_SHA256': release.sha256, 'UV_NAME': spec['uv'],
                           'UV_SHA256': spec['uvSha256'], 'UV_VERSION': UV_VERSION, 'VERSION': release.version}.items():
            template = template.replace('__' + key + '__', value)
        return template


def setup_routes(app):
    manager = TerminalSetup(app['service'].data_dir, app['allowed_origins'], app['server_config']['tls']['method'])
    app['terminal_setup'] = manager
    app['service'].terminal_setup = manager

    async def page(request):
        require_safe_transport(request)
        return web.Response(text=(Path(__file__).parent / 'terminal_setup.html').read_text(), content_type='text/html')

    async def script(request):
        return web.Response(text=(Path(__file__).parent / 'terminal_setup.js').read_text(), content_type='application/javascript')

    async def download(request):
        require_safe_transport(request)
        identity = request.match_info['identity']
        if not re.fullmatch('[a-f0-9]{32}', identity):
            raise web.HTTPNotFound()
        path = manager.directory / (identity + '.json')
        if not path.is_file() or json.loads(path.read_text())['expiresAt'] <= time.time():
            raise web.HTTPGone(text='Setup expired. Prepare a new installer.')
        return web.Response(body=path.with_suffix('.sh').read_bytes(), content_type='application/octet-stream',
            headers={'Content-Disposition': f'attachment; filename="Amplifier-Terminal-{identity[:8]}.sh"', 'Cache-Control': 'no-store'})

    async def redeem(request):
        require_safe_transport(request)
        # This route authenticates the one-use grant itself, after the ordinary
        # Host/Origin checks; it never accepts a grant for other API operations.
        payload = await request.json()
        if not isinstance(payload, dict) or set(payload) != {'grant'}:
            raise web.HTTPBadRequest(text='Supply the setup grant.')
        try:
            result = manager.devices.redeem(payload['grant'])
        except ValueError as exc:
            raise web.HTTPUnauthorized(text=str(exc)) from None
        return web.json_response(result, headers={'Cache-Control': 'no-store'})

    app.router.add_get('/setup/terminal', page)
    app.router.add_get('/terminal-setup.js', script)
    app.router.add_get('/api/terminal/installers/{identity}', download)
    app.router.add_post('/api/terminal/redeem', redeem)
