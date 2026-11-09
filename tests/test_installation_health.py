"""Listener proof, local-only control probes, and passive Windows diagnostics."""
import asyncio
import pytest

from amplifier_web import installation_health as health
from amplifier_web import windows_service


def test_windows_service_state_reads_scheduler_without_posix_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(health.sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(windows_service, "task_state",
                        lambda root: calls.append(root) or {"state": "running"})
    monkeypatch.setattr(health.subprocess, "run", lambda *a, **kw: pytest.fail("no POSIX command"))
    assert health.service_state(tmp_path) == "running"
    assert calls == [tmp_path]


@pytest.mark.parametrize("state", ["not configured", "running", "starting", "stopped", "disabled"])
def test_windows_health_cannot_be_true_from_task_state_or_probe_untrusted_listener(tmp_path, monkeypatch, state):
    monkeypatch.setattr(health.sys, "platform", "win32")
    monkeypatch.setattr(health, "service_state", lambda root: state)
    from amplifier_web import deployment, update_readiness
    monkeypatch.setattr(update_readiness, "running_identity", lambda: {"version": "0.0.0", "revision": None})
    monkeypatch.setattr(deployment, "load_server_config", lambda *a, **kw: pytest.fail("no credential/probe path"))
    monkeypatch.setattr(health.asyncio, "sleep", lambda *a: pytest.fail("pending qualification cannot improve by waiting"))
    value = asyncio.run(health.status(tmp_path, wait=60))
    assert not value["ok"] and not value["processVerified"]
    assert value["qualificationPending"] and value["service"] == state
    assert value["responding"] is None and not value["probeAttempted"]
    assert "not readiness" in value["message"]


def test_windows_doctor_does_not_try_pam_or_advise_linux_package_manager(tmp_path, monkeypatch):
    from amplifier_web import deployment, host_dependencies, tls
    import builtins
    import shutil
    monkeypatch.setattr(health.sys, "platform", "win32")
    monkeypatch.setattr(host_dependencies, "require_uv", lambda: "uv.exe")
    monkeypatch.setattr(shutil, "which", lambda *a, **kw: None)
    monkeypatch.setattr(deployment, "load_server_config", lambda root: {"tls": {"method": "none"}})
    monkeypatch.setattr(tls, "ssl_context", lambda *args: None)
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == "pam":
            pytest.fail("PAM is not Windows sign-in")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    result = health.doctor(tmp_path)
    assert not result["ok"]
    messages = " ".join(row["message"] for row in result["checks"])
    assert "Git for Windows" in messages and "qualification remain pending" in messages
    assert "apt-get" not in messages


@pytest.mark.asyncio
@pytest.mark.parametrize('second', ['healthy', 'wrong-identity', 'wrong-version', 'wrong-cert', 'redirect', 'absent', 'timeout'])
async def test_status_reports_every_verified_listener_and_partial_failure(tmp_path, monkeypatch, capsys, second):
    from aiohttp import web
    from amplifier_web import __version__, update_readiness
    from amplifier_web.auth import data_identity
    from amplifier_web.deployment import load_server_config, save_server_config
    from amplifier_web.tls import setup_local_ca, ssl_context

    config = setup_local_ca(tmp_path, load_server_config(tmp_path))
    observed = []
    async def endpoint(request):
        observed.append((request.transport.get_extra_info('sockname')[0],
                         request.headers.get('Authorization'), request.host))
        address = request.transport.get_extra_info('sockname')[0]
        if address == '127.0.0.2' and second == 'timeout':
            await asyncio.sleep(2.1)
        if address == '127.0.0.2' and second == 'redirect':
            raise web.HTTPFound('https://public-untrusted.invalid/api/health')
        return web.json_response({'ok': True, 'app': 'amplifier-unified',
                                  'version': 'wrong' if address == '127.0.0.2' and second == 'wrong-version' else __version__,
                                  'dataIdentity': 'wrong' if address == '127.0.0.2' and second == 'wrong-identity'
                                  else data_identity(tmp_path)})
    app = web.Application()
    app.router.add_get('/api/health', endpoint)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, '127.0.0.1', 0, ssl_context=ssl_context(tmp_path, config)).start()
        port = runner.addresses[0][1]
        if second != 'absent':
            context = ssl_context(tmp_path, config)
            if second == 'wrong-cert':
                other = tmp_path / 'other'
                other_config = setup_local_ca(other, load_server_config(other))
                context = ssl_context(other, other_config)
            await web.TCPSite(runner, '127.0.0.2', port, ssl_context=context).start()
        save_server_config(tmp_path, {**config, 'port': port, 'bind': ['127.0.0.1', '127.0.0.2'],
                                     'public_origins': ['https://public-untrusted.invalid']})
        token = tmp_path / 'config/auth/control-token'
        token.parent.mkdir(parents=True)
        token.write_text('synthetic-local-control')
        monkeypatch.setattr(health, 'service_state', lambda home: 'running')
        monkeypatch.setattr(update_readiness, 'running_identity', lambda: {'version': __version__, 'revision': None})
        result = await health.status(tmp_path)
    finally:
        await runner.cleanup()
    first, other = [f'https://127.0.0.{i}:{port}' for i in (1, 2)]
    assert result['listenerUrls'] == ([first, other] if second == 'healthy' else [first])
    assert result['listenerChecks'][1]['verified'] is (second == 'healthy')
    assert result['ok'] and result['responding']
    assert result['publicUrlsVerified'] is False
    assert 'not all interfaces' in result['listenerScope']
    # A mismatched TLS leaf must fail before any credential reaches that server.
    assert sorted(row[0] for row in observed) == (['127.0.0.1'] if second in {'absent', 'wrong-cert'}
                                                  else ['127.0.0.1', '127.0.0.2'])
    assert all(row[1:] == ('Bearer synthetic-local-control', f'localhost:{port}') for row in observed)
    health.display(result)
    text = capsys.readouterr().out
    assert 'Host listener: ' + first in text
    assert ('Host listener: ' + other in text) is (second == 'healthy')
    assert ('Listener not verified: ' + other in text) is (second != 'healthy')
    assert 'Configured public address (not checked)' in text
    assert 'not all interfaces' in text and 'not enumerated' in text
    assert 'synthetic-local-control' not in text


def test_probe_names_are_resolved_once_and_nonlocal_addresses_excluded(monkeypatch):
    import socket
    from types import SimpleNamespace
    from amplifier_web import update_readiness

    real_socket = socket.socket
    lookups = []
    def resolve(host, port, **kwargs):
        lookups.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('203.0.113.222', 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 0))]
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    binds = []
    class LocalSocket:
        def __init__(self, *args):
            self.socket = real_socket(*args)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.socket.close()
        def bind(self, address):
            binds.append(address[0])
            if address[0] == '203.0.113.222':
                raise OSError('not assigned locally')
            self.socket.bind(address)
    monkeypatch.setattr(socket, 'socket', LocalSocket)
    manager = SimpleNamespace(service=SimpleNamespace(port=8941, server_config={
        'tls': {'method': 'none'}, 'bind': ['untrusted-dns.example'],
        'public_origins': ['https://never-resolve.example']}))
    urls, pin = update_readiness.probe_targets(manager)
    assert urls == ['http://127.0.0.1:8941/api/health'] and pin is None
    assert lookups == ['untrusted-dns.example']  # No public origin or second DNS lookup on connect.
    assert binds == ['127.0.0.1', '203.0.113.222', '127.0.0.1']


@pytest.mark.parametrize('bind,target', [('0.0.0.0', '127.0.0.1'), ('::', '::1'), ('::1', '::1')])
def test_wildcard_and_ipv6_probes_are_numeric_not_wildcard_proof(bind, target):
    import socket
    from types import SimpleNamespace
    from amplifier_web.update_readiness import probe_targets
    if ':' in target:
        try:
            with socket.socket(socket.AF_INET6) as sock:
                sock.bind(('::1', 0))
        except OSError:
            pytest.skip('IPv6 loopback is unavailable on this test host')
    manager = SimpleNamespace(service=SimpleNamespace(port=8941, server_config={
        'tls': {'method': 'none'}, 'bind': [bind]}))
    urls, _ = probe_targets(manager)
    authority = f'[{target}]' if ':' in target else target
    assert urls == [f'http://{authority}:8941/api/health']


def test_windows_certificate_diagnostics_do_not_read_unqualified_paths(tmp_path, monkeypatch):
    from amplifier_web import tls
    monkeypatch.setattr(tls, '_WINDOWS', True)
    monkeypatch.setattr(tls, 'resolve_path', lambda *a: pytest.fail('no pathname TLS diagnostics on Windows'))
    rows = tls.certificate_checks(tmp_path, {'tls': {'method': 'ca'}})
    assert len(rows) == 1 and not rows[0]['ok'] and 'unqualified' in rows[0]['message']


@pytest.mark.asyncio
@pytest.mark.parametrize('bind', ['localhost', '::'])
async def test_named_and_ipv6_wildcard_binds_verify_only_reached_numeric_addresses(tmp_path, monkeypatch, capsys, bind):
    import socket
    from aiohttp import web
    from amplifier_web import __version__
    from amplifier_web.auth import data_identity
    from amplifier_web.deployment import load_server_config, save_server_config
    app = web.Application()
    async def endpoint(request):
        assert request.headers['Authorization'] == 'Bearer synthetic-token'
        return web.json_response({'ok': True, 'app': 'amplifier-unified', 'version': __version__,
                                  'dataIdentity': data_identity(tmp_path)})
    app.router.add_get('/api/health', endpoint)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        try:
            await web.TCPSite(runner, '::1', 0).start()
        except OSError:
            pytest.skip('IPv6 loopback is unavailable on this test host')
        port = runner.addresses[0][1]
        await web.TCPSite(runner, '127.0.0.1', port).start()
        # validate_server requires TLS for wildcard binds; status does not
        # weaken that invariant. This fixture isolates the probe/display path.
        from amplifier_web import deployment
        config = {**load_server_config(tmp_path), 'port': port, 'bind': [bind]}
        if bind == 'localhost':
            save_server_config(tmp_path, config)
        else:
            monkeypatch.setattr(deployment, 'load_server_config', lambda home: config)
        token = tmp_path / 'config/auth/control-token'
        token.parent.mkdir(parents=True)
        token.write_text('synthetic-token')
        monkeypatch.setattr(health, 'service_state', lambda home: 'running')
        if bind == 'localhost':
            # The host's /etc/hosts need not contain both localhost families.
            original = socket.getaddrinfo
            def dual_stack(host, port, *args, **kwargs):
                if host == 'localhost':
                    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 0)),
                            (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::1', 0, 0, 0))]
                return original(host, port, *args, **kwargs)
            monkeypatch.setattr(socket, 'getaddrinfo', dual_stack)
        result = await health.status(tmp_path)
    finally:
        await runner.cleanup()
    assert f'http://[::1]:{port}' in result['listenerUrls']
    assert all('localhost' not in url and '://[::]:' not in url for url in result['listenerUrls'])
    health.display(result)
    assert 'wildcard binds test loopback only' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_unassigned_bind_and_untrusted_dns_cannot_receive_control_token(tmp_path, monkeypatch):
    import socket
    import aiohttp
    from amplifier_web.deployment import load_server_config, save_server_config
    from amplifier_web.tls import setup_local_ca
    config = setup_local_ca(tmp_path, load_server_config(tmp_path))
    save_server_config(tmp_path, {**config, 'bind': ['untrusted.example'],
                                 'public_origins': ['https://never-probe.example']})
    token = tmp_path / 'config/auth/control-token'
    token.parent.mkdir(parents=True)
    token.write_text('synthetic-token')
    monkeypatch.setattr(health, 'service_state', lambda home: 'running')
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw:
        [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('203.0.113.222', 0))])
    monkeypatch.setattr(aiohttp.ClientSession, 'get', lambda *a, **kw: pytest.fail('no remote control probe'))
    result = await health.status(tmp_path)
    assert not result['ok'] and not result['responding'] and result['listenerUrls'] == []
    assert result['configuredBinds'] == ['untrusted.example']


def test_linux_nonlocal_bind_mode_cannot_authorize_a_control_probe(monkeypatch):
    import socket
    from pathlib import Path
    from amplifier_web import update_readiness
    monkeypatch.setattr(update_readiness.sys, 'platform', 'linux')
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw:
        [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('203.0.113.222', 0))])
    original = Path.read_text
    def read(path, *a, **kw):
        if str(path) == '/proc/sys/net/ipv4/ip_nonlocal_bind':
            return '1\n'
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setattr(socket, 'socket', lambda *a: pytest.fail('nonlocal bind mode is not evidence'))
    assert update_readiness.local_probe_hosts('untrusted.example') == []


@pytest.mark.asyncio
async def test_listener_collection_bounds_concurrency_but_does_not_truncate_binds(tmp_path, monkeypatch):
    from aiohttp import web
    from amplifier_web import __version__
    from amplifier_web.auth import data_identity
    from amplifier_web.deployment import load_server_config, save_server_config
    active = peak = 0
    async def endpoint(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.02)
        active -= 1
        return web.json_response({'ok': True, 'app': 'amplifier-unified', 'version': __version__,
                                  'dataIdentity': data_identity(tmp_path)})
    app = web.Application()
    app.router.add_get('/api/health', endpoint)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, '0.0.0.0', 0).start()
        port = runner.addresses[0][1]
        binds = [f'127.0.0.{i}' for i in range(1, 9)]
        save_server_config(tmp_path, {**load_server_config(tmp_path), 'port': port, 'bind': binds})
        token = tmp_path / 'config/auth/control-token'
        token.parent.mkdir(parents=True)
        token.write_text('synthetic-token')
        monkeypatch.setattr(health, 'service_state', lambda home: 'running')
        result = await health.status(tmp_path)
    finally:
        await runner.cleanup()
    assert len(result['listenerUrls']) == 8
    assert peak == 4