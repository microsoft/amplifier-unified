"""Bounded TLS eligibility fixture, not native installation/recovery proof.

Real: AppService, dispatcher, auth, static UI, repair.binding, probe_targets,
TLS listener and pinned authenticated /api/health. Synthetic: uv installation,
service ownership, optional features and release metadata. No Confirm is needed.
The browser stays on a private HTTP UI listener; the positive health listener is
HTTPS on the SAME AppService. This is not browser certificate-trust acceptance.
"""
import asyncio
import copy
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from aiohttp import web
import settings_ui_server as settings
from amplifier_web import app_release_artifacts, app_updates, installation_repair as repair, reset
from amplifier_web.auth import SESSION_COOKIE, data_identity, new_session
from amplifier_web.deployment import DEFAULT_SERVER, save_server_config, write_private
from amplifier_web.tls import ssl_context
from amplifier_web.update_readiness import probe_targets

ROOT = Path(__file__).resolve().parents[2]
TOKEN = 'fixture-browser-control-token'
LIMITS = [
    'Synthetic uv installation/service ownership/extras/release metadata; no native qualification.',
    'No Confirm, artifact acquisition, helper, installer, restart or conversation runtime effects.',
    'HTTP browser UI plus separate pinned HTTPS health on the same private AppService.',
    'No browser certificate-trust, worker reconciliation, published-package or native parity claim.',
]


def identity():
    paths = [
        'components/web-client.source.json', '.build/web-client/.client-source.json',
        'amplifier_web/static/build.json', 'amplifier_web/static/client-component.json',
        'amplifier_web/server.py', 'amplifier_web/service.py',
        'amplifier_web/installation_repair.py', 'amplifier_web/update_readiness.py',
        'tests/fixtures/installation_repair_tls_server.py',
        'tests/web/installation-repair-tls-browser.mjs',
    ]
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}


def loopback_guard(event, args):
    """Process-local fail-closed egress guard; no non-loopback network."""
    if event == 'socket.connect':
        address = args[1]
        if not isinstance(address, tuple) or not ipaddress.ip_address(address[0]).is_loopback:
            raise PermissionError('TLS eligibility fixture permits numeric loopback connections only.')


def certificate(home):
    """Fresh private self-signed leaf; production ssl_context loads these bytes."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Private TLS eligibility fixture')])
    now = datetime.now(timezone.utc)
    leaf = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),
                           critical=False).sign(key, hashes.SHA256()))
    write_private(home / 'config/tls/leaf.crt', leaf.public_bytes(serialization.Encoding.PEM))
    write_private(home / 'config/tls/leaf.key', key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return leaf.fingerprint(hashes.SHA256()).hex()


class IdleRuntime(settings.Runtime):
    def __init__(self):
        super().__init__()
        self.workers, self._preparations, self.calls = {}, {}, []

    def has_pending_operations(self):
        return False

    async def forbidden(self, *args, **kwargs):
        self.calls.append('forbidden-runtime-effect')
        raise AssertionError('TLS preview must not start/control/stop/send runtime work')

    start = control = stop = send = forbidden


def synthetic_installation(stack, parent):
    """Only pre-TLS native ownership seams; NEVER replace binding/probe_targets."""
    prefix = parent / 'synthetic-tools/amplifier-unified'
    metadata = prefix / 'lib/python3.13/site-packages/amplifier_unified-99.0.0.dist-info/METADATA'
    write_private(metadata, 'Name: amplifier-unified\nVersion: 99.0.0\n')
    uv, launcher = parent / 'synthetic-bin/uv', parent / 'synthetic-bin/amplifier-unified'
    base = parent / 'synthetic-bin/base-python-not-executed'
    for path in (uv, launcher, base):
        write_private(path, 'Synthetic identity only; never execute.\n')
        path.chmod(0o700)
    # This fixture already substitutes installation/service ownership. Bind its
    # never-executed base identity to a private fixture file too: a shared uv
    # CPython may correctly fail the product's writable-executable guard before
    # reaching TLS. Do not chmod shared Python or relax executable_identity.
    stack.enter_context(patch.object(repair, 'sys', SimpleNamespace(
        platform=sys.platform, prefix=sys.prefix, _base_executable=str(base))))
    installed = stack.enter_context(patch.object(app_updates, 'installed_target', AsyncMock(
        return_value=(str(uv), str(launcher), str(prefix / 'bin/python'),
                      {'installation': str(prefix)}))))
    stack.enter_context(patch.object(repair, '_service', Mock(return_value={
        'synthetic': True, 'pid': os.getpid(), 'commands': [], 'path': 'not-a-native-service'})))
    stack.enter_context(patch.object(repair, '_declared_extras', Mock(return_value=([], 'a' * 64))))
    stack.enter_context(patch.object(reset, 'environment', Mock(return_value={})))
    release = stack.enter_context(patch.object(reset, 'release_metadata', Mock(
        return_value={'tag_name': 'v99.0.0'})))

    def tag(*args, **kwargs):
        assert args == ('git', 'ls-remote', 'https://github.com/' + reset.REPOSITORY,
                        'refs/tags/v99.0.0', 'refs/tags/v99.0.0^{}')
        assert kwargs['timeout'] == 60
        assert kwargs['env']['UV_TOOL_DIR'] == str(prefix.parent)
        return SimpleNamespace(stdout='a' * 40 + '\trefs/tags/v99.0.0\n')

    tags = stack.enter_context(patch.object(reset, 'download_command', Mock(side_effect=tag)))
    artifacts = stack.enter_context(patch.object(app_release_artifacts, 'prepare', AsyncMock(
        side_effect=AssertionError('Preview must not acquire artifacts'))))
    helpers = stack.enter_context(patch.object(repair, 'launch', Mock(
        side_effect=AssertionError('Preview must not launch a helper'))))
    return installed, release, tags, artifacts, helpers


async def serve():
    sys.addaudithook(loopback_guard)
    bound = identity()
    with tempfile.TemporaryDirectory(prefix='unified-repair-tls-') as directory, ExitStack() as stack:
        parent = Path(directory)
        parent.chmod(0o700)
        home = parent / 'app'
        home.mkdir(mode=0o700)
        # Literal current defaults, including tls.method=none. Only the eventual
        # listener port is replaced with the owned ephemeral port.
        save_server_config(home, copy.deepcopy(DEFAULT_SERVER))
        installed, release, tags, artifacts, helpers = synthetic_installation(stack, parent)
        create_app = settings.create_app

        async def isolated_app(*args, **kwargs):
            kwargs.update(background_updates=False, background_work=False, preload_providers=False)
            return await create_app(*args, **kwargs)

        with patch.object(settings, 'create_app', isolated_app), patch.object(settings, 'Runtime', IdleRuntime):
            app = await settings.main(home)
        service = app['service']
        counters = {'confirmPosts': 0, 'authenticatedHTTPHealth': 0, 'authenticatedHTTPSHealth': 0}
        evidence = {'health': None, 'tlsAttempted': False}
        runner = web.AppRunner(app)
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        signals = (signal.SIGTERM, signal.SIGINT) if os.name == 'posix' else ()

        @web.middleware
        async def observe_wire(request, handler):
            if request.path == '/api/health' and request.headers.get('Authorization') == 'Bearer ' + TOKEN:
                counters['authenticatedHTTPSHealth' if request.secure else 'authenticatedHTTPHealth'] += 1
            if request.path == '/api/actions' and request.method == 'POST':
                body = await request.json()
                command = body.get('args', {}).get('command', body.get('args', {})) if body.get('action') == 'shell.command' else body
                if command.get('action') == 'maintenance.installationRepairConfirm':
                    counters['confirmPosts'] += 1
            return await handler(request)

        app.middlewares.append(observe_wire)

        def authorize(request):
            if request.headers.get('Authorization') != 'Bearer ' + TOKEN:
                raise web.HTTPForbidden()

        async def cookie(request):
            authorize(request)
            if await request.read():
                raise web.HTTPBadRequest()
            response = web.json_response({'ready': True})
            response.set_cookie(SESSION_COOKIE, new_session(app['session_secret']),
                                httponly=True, samesite='Strict')
            return response

        async def observe(request):
            authorize(request)
            urls, pin = await asyncio.to_thread(probe_targets, service.update_manager)
            folder = repair.root(service)
            return web.json_response({
                'identity': bound, 'limits': LIMITS, 'hostInstanceId': service.instance_id,
                'dataDir': str(home), 'tls': service.server_config['tls']['method'],
                'probeTargets': urls, 'pin': pin.fingerprint.hex() if pin else None,
                'health': evidence['health'], 'tlsAttempted': evidence['tlsAttempted'],
                'preview': service.state['maintenance']['installationRepair']['preview'],
                'operation': repair._latest(service), 'admissions': repair._admissions(service),
                'admissionsCreated': (folder / 'admissions.json').exists(),
                'operationFiles': len(list(folder.glob('*/operation.json'))),
                'previewFiles': len(list(folder.glob('preview-*.json'))),
                'counters': {**counters, 'eligibilityReads': installed.await_count,
                             'releaseReads': release.call_count, 'tagReads': tags.call_count,
                             'artifactPreparations': artifacts.await_count, 'helpers': helpers.call_count,
                             'runtimeCalls': service.runtime.calls},
            })

        async def enable_tls(request):
            authorize(request)
            if await request.read() or evidence['tlsAttempted']:
                raise web.HTTPConflict(text='This fixture transition is one-shot; inspect evidence, never replay.')
            evidence['tlsAttempted'] = True
            preview = service.state['maintenance']['installationRepair']['preview']
            assert preview and preview['supported'] is False and 'Configure TLS' in preview['reason']
            expected_pin = certificate(home)
            config = copy.deepcopy(DEFAULT_SERVER)
            config['tls'] = {'method': 'ca', 'cert': 'config/tls/leaf.crt', 'key': 'config/tls/leaf.key'}
            # Same runner/AppService and actual production SSL context. HTTP UI
            # remains available; only configured probe targets switch to HTTPS.
            site = web.TCPSite(runner, '127.0.0.1', 0, ssl_context=ssl_context(home, config))
            await site.start()
            config['port'] = site._server.sockets[0].getsockname()[1]
            config = save_server_config(home, config)
            service.server_config, service.port = config, config['port']
            observed = await repair.binding(service)  # production implementation
            urls, pin = await asyncio.to_thread(probe_targets, service.update_manager)
            assert observed['health'] == {'urls': urls, 'pin': expected_pin}
            assert pin.fingerprint.hex() == expected_pin and len(urls) == 1
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3), trust_env=False) as client:
                # Wrong leaf is rejected before HTTP can send the credential.
                bad_pin = aiohttp.Fingerprint(bytes(value ^ 0xff for value in pin.fingerprint))
                before = counters['authenticatedHTTPSHealth']
                try:
                    async with client.get(urls[0], ssl=bad_pin, headers={'Authorization': 'Bearer ' + TOKEN},
                                          allow_redirects=False):
                        raise AssertionError('Mismatched leaf pin was accepted')
                except aiohttp.ServerFingerprintMismatch:
                    pass
                assert counters['authenticatedHTTPSHealth'] == before
                async with client.get(urls[0], ssl=pin, headers={'Authorization': 'Bearer ' + TOKEN},
                                      allow_redirects=False) as response:
                    assert response.status == 200
                    health = await response.json()
            assert health['ok'] is True and health['hostInstanceId'] == service.instance_id
            assert health['dataIdentity'] == data_identity(home)
            assert health['installationRepair'] == {'protocol': repair.PROTOCOL, 'operationId': None}
            for key in ('revision', 'instanceId'):
                assert health[key] == service.update_manager.running_identity[key]
            evidence['health'] = {'url': urls[0], 'pin': expected_pin, 'wrongPinRejected': True,
                                  'hostInstanceId': health['hostInstanceId'],
                                  'dataIdentity': health['dataIdentity'], 'operationId': None}
            return web.json_response(evidence['health'])

        app.router.add_post('/fixture/repair-tls-cookie', cookie)
        app.router.add_get('/fixture/repair-tls', observe)
        app.router.add_post('/fixture/repair-tls-enable', enable_tls)
        try:
            await runner.setup()
            for signum in signals:
                loop.add_signal_handler(signum, stopped.set)
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            service.port = port
            service.server_config['port'] = port
            assert service.server_config == {**copy.deepcopy(DEFAULT_SERVER), 'port': port}
            base = f'http://127.0.0.1:{port}'
            app['allowed_origins'] |= {base}
            print(json.dumps({'fixture': 'installation-repair-tls', 'pid': os.getpid(), 'base': base}), flush=True)
            await asyncio.wait_for(stopped.wait(), timeout=180)
        finally:
            try:
                await runner.cleanup()
            finally:
                for signum in signals:
                    loop.remove_signal_handler(signum)
                assert counters['confirmPosts'] == helpers.call_count == artifacts.await_count == 0
                assert not (repair.root(service) / 'admissions.json').exists()
                assert identity() == bound, 'TLS qualification inputs changed during execution'
    assert not parent.exists(), 'Private fixture files were not removed'


if __name__ == '__main__':
    asyncio.run(serve())