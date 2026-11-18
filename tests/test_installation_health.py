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
    monkeypatch.setattr(windows_service, "_read",
                        lambda root: ({"state": 4, "instances": 1}, None))
    from amplifier_web import deployment, update_readiness
    monkeypatch.setattr(update_readiness, "running_identity", lambda: {"version": "0.0.0", "revision": None})
    monkeypatch.setattr(deployment, "load_server_config", lambda *a, **kw: pytest.fail("no credential/probe path"))
    value = asyncio.run(health.status(tmp_path))
    assert not value["ok"] and not value["processVerified"]
    assert value["service"] == state
    assert value["responding"] is False and not value["probeAttempted"]
    assert "not ready" in value["message"]


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
    assert result['listenerScopeComplete']
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
    assert 'Only matching authenticated responses are verified' in text
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
    assert 'Probe scope:' in capsys.readouterr().out


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


def test_ipv4_mapped_ipv6_cannot_bypass_nonlocal_bind_policy(monkeypatch):
    import socket
    from amplifier_web import update_readiness
    monkeypatch.setattr(update_readiness.sys, 'platform', 'linux')
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw:
        [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::ffff:203.0.113.222', 0, 0, 0))])
    monkeypatch.setattr(socket, 'socket', lambda *a: pytest.fail('mapped candidate must not reach ownership bind'))
    assert update_readiness.local_probe_hosts('::ffff:203.0.113.222') == []


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


@pytest.mark.asyncio
@pytest.mark.parametrize('bind,version', [('0.0.0.0', 4), ('::', 6)])
async def test_real_wildcard_listener_enumerates_nonloopback_interfaces_without_dns(tmp_path, monkeypatch, bind, version):
    import ipaddress
    import socket
    from aiohttp import web
    from amplifier_web import __version__, update_readiness
    from amplifier_web.auth import data_identity
    from amplifier_web.deployment import load_server_config, save_server_config
    from amplifier_web.tls import setup_local_ca, ssl_context

    if health.sys.platform not in {'linux', 'darwin'}:
        pytest.skip('getifaddrs enumeration is qualified only on Linux/macOS')
    assigned = [host for host in update_readiness.local_interface_addresses()
                if ipaddress.ip_address(host).version == version]
    if not any(not ipaddress.ip_address(host).is_loopback for host in assigned):
        pytest.skip(f'No assigned nonloopback IPv{version} interface on this host')
    config = setup_local_ca(tmp_path, load_server_config(tmp_path))
    seen = []
    async def endpoint(request):
        assert request.headers['Authorization'] == 'Bearer synthetic-wildcard-control'
        seen.append(request.transport.get_extra_info('sockname')[0])
        return web.json_response({'ok': True, 'app': 'amplifier-unified', 'version': __version__,
                                  'dataIdentity': data_identity(tmp_path)})
    app = web.Application()
    app.router.add_get('/api/health', endpoint)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, bind, 0, ssl_context=ssl_context(tmp_path, config)).start()
        port = runner.addresses[0][1]
        save_server_config(tmp_path, {**config, 'port': port, 'bind': [bind],
                                     'public_origins': ['https://public-never-resolve.invalid']})
        token = tmp_path / 'config/auth/control-token'
        token.parent.mkdir(parents=True)
        token.write_text('synthetic-wildcard-control')
        monkeypatch.setattr(health, 'service_state', lambda home: 'running')
        original = socket.getaddrinfo
        def numeric_only(host, port, *args, **kwargs):
            ipaddress.ip_address(host)
            if port == 0:
                assert kwargs['flags'] & socket.AI_NUMERICHOST
            return original(host, port, *args, **kwargs)
        monkeypatch.setattr(socket, 'getaddrinfo', numeric_only)
        result = await health.status(tmp_path)
    finally:
        await runner.cleanup()
    expected = {f'https://[{host}]:{port}' if version == 6 else f'https://{host}:{port}'
                for host in assigned}
    assert set(result['listenerUrls']) == expected
    assert any(not ipaddress.ip_address(host).is_loopback for host in seen)
    assert result['listenerScopeComplete'] and 'getifaddrs snapshot' in result['listenerScope']
    assert result['publicUrlsVerified'] is False


def test_unavailable_wildcard_enumeration_is_explicitly_partial(monkeypatch):
    from types import SimpleNamespace
    from amplifier_web import update_readiness
    def unavailable():
        raise NotImplementedError('unsupported local interface source')
    monkeypatch.setattr(update_readiness, 'local_interface_addresses', unavailable)
    manager = SimpleNamespace(service=SimpleNamespace(port=8941, server_config={
        'tls': {'method': 'none'}, 'bind': ['0.0.0.0']}))
    diagnostics = {}
    urls, _ = update_readiness.probe_targets(manager, diagnostics=diagnostics)
    assert urls == ['http://127.0.0.1:8941/api/health']
    assert not diagnostics['listenerScopeComplete']
    assert 'PARTIAL' in diagnostics['listenerScope'] and 'not all interfaces' in diagnostics['listenerScope']


@pytest.mark.parametrize('platform', ['linux', 'darwin'])
def test_getifaddrs_layouts_ipv4_ipv6_scope_and_release(monkeypatch, platform):
    import ctypes as c
    import socket
    from amplifier_web import update_readiness
    monkeypatch.setattr(update_readiness.sys, 'platform', platform)
    buffers, entries, calls = [], [], []
    class Function:
        def __init__(self, call):
            self.call = call
        def __call__(self, *args):
            return self.call(*args)
    def get(output):
        # The helper's declared ABI supplies the actual ifaddrs structure type.
        node_type = output._obj._type_
        for family, host, scope in [(socket.AF_INET, '192.0.2.10', 0),
                                    (socket.AF_INET6, 'fe80::123', 7),
                                    (socket.AF_INET, '192.0.2.10', 0)]:
            data = bytearray(28 if family == socket.AF_INET6 else 16)
            data[:2] = bytes([len(data), family]) if platform == 'darwin' else family.to_bytes(2, update_readiness.sys.byteorder)
            offset = 8 if family == socket.AF_INET6 else 4
            packed = socket.inet_pton(family, host)
            if platform == 'darwin' and family == socket.AF_INET6:
                packed = packed[:2] + scope.to_bytes(2, 'big') + packed[4:]
                scope = 0  # Embedded Darwin scope instead of sin6_scope_id.
            data[offset:offset + len(packed)] = packed
            if family == socket.AF_INET6:
                data[24:28] = scope.to_bytes(4, update_readiness.sys.byteorder)
            buffer = c.create_string_buffer(bytes(data))
            buffers.append(buffer)
            entries.append(node_type(name=b'test0', addr=c.addressof(buffer)))
        for first, second in zip(entries, entries[1:]):
            first.next = c.pointer(second)
        c.cast(output, c.POINTER(c.POINTER(node_type)))[0] = c.pointer(entries[0])
        return 0
    from types import SimpleNamespace
    libc = SimpleNamespace(getifaddrs=Function(get), freeifaddrs=Function(lambda head: calls.append('free')))
    monkeypatch.setattr(c, 'CDLL', lambda *a, **kw: libc)
    assert update_readiness.local_interface_addresses() == ['192.0.2.10', 'fe80::123%7']
    assert calls == ['free']


def test_windows_interface_enumeration_is_unqualified(monkeypatch):
    from amplifier_web import update_readiness
    monkeypatch.setattr(update_readiness.sys, 'platform', 'win32')
    with pytest.raises(NotImplementedError, match='Linux and macOS only'):
        update_readiness.local_interface_addresses()


def test_wildcard_snapshot_is_taken_once_and_unowned_candidates_remain_unverified(tmp_path, monkeypatch):
    import socket
    from types import SimpleNamespace
    from amplifier_web import update_readiness
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import setup_local_ca
    calls = []
    def interfaces():
        calls.append('enumerate')
        return ['127.0.0.1', '203.0.113.222', '::1']
    monkeypatch.setattr(update_readiness, 'local_interface_addresses', interfaces)
    real_socket = socket.socket
    class OwnedOnly:
        def __init__(self, *args):
            self.socket = real_socket(*args)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.socket.close()
        def bind(self, address):
            if address[0] in {'203.0.113.222', '::1'}:
                raise OSError('unowned/unavailable')
            self.socket.bind(address)
    monkeypatch.setattr(socket, 'socket', OwnedOnly)
    config = {**setup_local_ca(tmp_path, load_server_config(tmp_path)), 'bind': ['0.0.0.0', '::']}
    manager = SimpleNamespace(home=tmp_path, service=SimpleNamespace(port=8941, server_config=config))
    diagnostics = {}
    urls, _ = update_readiness.probe_targets(manager, diagnostics=diagnostics)
    assert calls == ['enumerate']
    assert urls == ['https://127.0.0.1:8941/api/health']
    assert not diagnostics['listenerScopeComplete'] and 'PARTIAL' in diagnostics['listenerScope']
@pytest.fixture
def windows_health_chain(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import aiohttp
    from amplifier_web import auth, browser_entry, deployment, windows_task_runtime
    spec = SimpleNamespace(sid="S-1-5-21-100-200-300-1001")
    task = {"state": 4, "instances": 1}
    record = {"pid": 123, "created": "134000000000000001", "phase": "listening", "run": "a" * 32}
    state = {"reads": 0, "listener": True, "record": record, "requests": [],
             "credentialReads": 0, "pin": aiohttp.Fingerprint(b"\x11" * 32), "ready": True}
    monkeypatch.setattr(windows_service, "_read", lambda home: (dict(task), spec))
    monkeypatch.setattr(windows_task_runtime, "observe", lambda value: state["record"])
    monkeypatch.setattr(windows_task_runtime, "listener_owned", lambda *a: state["listener"])
    monkeypatch.setattr(deployment, "load_server_config", lambda home: {"public_origins": []})
    monkeypatch.setattr(auth, "data_identity", lambda home: "owned-data")
    monkeypatch.setattr(browser_entry, "_owner_probe_targets",
                        lambda *a: (["https://127.0.0.1:8942/api/health"], state["pin"]))
    def token(home, sid):
        state["credentialReads"] += 1
        assert state["listener"] and state["record"]
        return "synthetic-control"
    monkeypatch.setattr(browser_entry, "_control_token", token)

    class Response:
        status = 200
        async def json(self):
            return {"ok": state["ready"], "app": "amplifier-unified",
                    "version": "1.2.3", "revision": None, "dataIdentity": "owned-data"}
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def get(self, url, **kwargs):
            assert kwargs["ssl"] is state["pin"] and kwargs["allow_redirects"] is False
            state["requests"].append((url, kwargs))
            return Response()
    monkeypatch.setattr(aiohttp, "ClientSession", Client)
    state.update(home=tmp_path, spec=spec, task=task)
    return state


def test_windows_health_requires_full_receipt_listener_and_pinned_authenticated_match(windows_health_chain):
    state = windows_health_chain
    result = asyncio.run(health._windows_status(state["home"],
                         {"version": "1.2.3", "revision": None}, "running"))
    assert result["ok"] and result["processVerified"] and result["responding"]
    assert result["pid"] == 123 and result["created"] == "134000000000000001"
    assert state["credentialReads"] == 1 and len(state["requests"]) == 1


def test_windows_status_says_why_the_background_app_stopped(windows_health_chain, monkeypatch):
    from datetime import datetime
    from amplifier_web import windows_task_runtime
    state = windows_health_chain
    state["task"].update(state=3, instances=0)  # The task is not running.
    failure = {"source": "host", "at": datetime(2026, 11, 14, 9, 5).timestamp(),
               "errorType": "WindowsServiceError", "message": "The saved reason."}
    monkeypatch.setattr(windows_task_runtime, "last_failure", lambda spec: dict(failure))
    result = asyncio.run(health._windows_status(state["home"],
                         {"version": "1.2.3", "revision": None}, "stopped"))
    assert not result["ok"] and not result["processVerified"]
    assert result["message"] == ("Windows background host is not ready. Amplifier's background app "
                                 "stopped on November 14 at 9:05 AM: The saved reason.")
    assert result["lastFailure"] == failure
    assert state["credentialReads"] == 0 and state["requests"] == []
    # An error that the product did not word keeps its class name only.
    failure.update(message=None, errorType="OSError", at=datetime(2026, 11, 14, 15, 30).timestamp())
    result = asyncio.run(health._windows_status(state["home"],
                         {"version": "1.2.3", "revision": None}, "stopped"))
    assert result["message"].endswith("stopped on November 14 at 3:30 PM with an unexpected error (OSError).")
    # No saved failure: the message is unchanged.
    monkeypatch.setattr(windows_task_runtime, "last_failure", lambda spec: None)
    result = asyncio.run(health._windows_status(state["home"],
                         {"version": "1.2.3", "revision": None}, "stopped"))
    assert result["message"] == "Windows background host is not ready." and "lastFailure" not in result


def test_windows_status_of_a_running_or_starting_host_does_not_read_an_old_failure(windows_health_chain, monkeypatch):
    from amplifier_web import windows_task_runtime
    monkeypatch.setattr(windows_task_runtime, "last_failure",
                        lambda spec: pytest.fail("a running task has no earlier failure to show"))
    state = windows_health_chain
    result = asyncio.run(health._windows_status(state["home"],
                         {"version": "1.2.3", "revision": None}, "running"))
    assert result["ok"] and "lastFailure" not in result
    # The task runs but no host listens yet: it is starting.
    for record in (None, {**state["record"], "phase": "starting"}):
        state["record"] = record
        result = asyncio.run(health._windows_status(state["home"],
                             {"version": "1.2.3", "revision": None}, "running"))
        assert not result["ok"] and result["message"] == "Windows background host is not ready."
        assert "lastFailure" not in result


@pytest.mark.parametrize("missing", ["record", "listener", "task"])
def test_windows_health_rejects_unverified_process_before_credential_read(windows_health_chain, missing):
    state = windows_health_chain
    if missing == "task":
        state["task"]["instances"] = 2
    else:
        state[missing] = None if missing == "record" else False
    result = asyncio.run(health._windows_status(state["home"],
                         {"version": "1.2.3", "revision": None}, "running"))
    assert not result["ok"] and not result["processVerified"]
    assert state["credentialReads"] == 0 and state["requests"] == []


def test_windows_health_does_not_accept_success_response_after_run_changes(windows_health_chain, monkeypatch):
    from amplifier_web import windows_task_runtime
    state = windows_health_chain
    calls = []
    def observe(spec):
        calls.append(None)
        return {**state["record"], "run": ("a" if len(calls) == 1 else "b") * 32}
    monkeypatch.setattr(windows_task_runtime, "observe", observe)
    result = asyncio.run(health._windows_status(state["home"],
                         {"version": "1.2.3", "revision": None}, "running"))
    assert not result["ok"] and not result["processVerified"]
    assert len(state["requests"]) == 1


def test_windows_health_bad_pin_does_not_read_or_send_bearer(windows_health_chain, monkeypatch):
    from amplifier_web import browser_entry
    state = windows_health_chain
    def invalid(*args):
        raise browser_entry.BrowserEntryUnavailable("private pin unavailable")
    monkeypatch.setattr(browser_entry, "_owner_probe_targets", invalid)
    result = asyncio.run(health._windows_status(state["home"],
                         {"version": "1.2.3", "revision": None}, "running"))
    assert not result["ok"] and not result["probeAttempted"]
    assert state["credentialReads"] == 0 and state["requests"] == []


@pytest.mark.parametrize("pin", [False, True, None, object()])
def test_windows_health_refuses_nonfingerprint_before_credential(windows_health_chain, pin):
    state = windows_health_chain
    state["pin"] = pin
    result = asyncio.run(health._windows_status(state["home"],
                         {"version": "1.2.3", "revision": None}, "running"))
    assert not result["ok"] and not result["probeAttempted"]
    assert state["credentialReads"] == 0 and state["requests"] == []


def test_windows_health_mismatched_version_is_not_ready(windows_health_chain):
    state = windows_health_chain
    result = asyncio.run(health._windows_status(state["home"],
                         {"version": "9.9.9", "revision": None}, "running"))
    assert not result["ok"] and not result["processVerified"]


def test_a_launcher_expects_the_app_version_that_it_starts_not_its_own(tmp_path, monkeypatch):
    """After an in-app update the Windows launcher is older than the running app."""
    from amplifier_web import application_generations as generations, installation_health as health
    own = {"version": "0.94.1", "revision": "a" * 40, "instanceId": "caller"}
    launcher = {"prefix": str(tmp_path / "tools/amplifier-unified"), "digest": "d" * 64}
    pointer = {"bootstrap": launcher, "version": "0.95.0", "revision": "b" * 40, "generation": "c" * 32}
    monkeypatch.setattr(generations, "bootstrap_identity", lambda prefix: launcher)
    # No in-app update yet: the launcher is the app.
    monkeypatch.setattr(generations, "active", lambda home: None)
    assert health.started_identity(tmp_path, own) == own
    # The launcher starts the active generation, so that is the app it must find.
    monkeypatch.setattr(generations, "active", lambda home: pointer)
    assert health.started_identity(tmp_path, own) == {**own, "version": "0.95.0", "revision": "b" * 40}
    # A caller that is not this installation's launcher keeps its own identity:
    # the app generation itself, a development checkout, another installation.
    monkeypatch.setattr(generations, "bootstrap_identity", lambda prefix: {**launcher, "digest": "e" * 64})
    assert health.started_identity(tmp_path, own) == own

    def unreadable(prefix):
        raise ValueError("no installed distribution")
    monkeypatch.setattr(generations, "bootstrap_identity", unreadable)
    assert health.started_identity(tmp_path, own) == own
    # The Windows status path uses it; the identity check itself is unchanged.
    import inspect
    assert ("_windows_status(home, started_identity(home, expected), initial_state)"
            in inspect.getsource(health._status_once))
    assert health.matches_health.__code__.co_argcount == 3


@pytest.fixture
def working_chain(windows_health_chain, monkeypatch):
    """The owner's activity question over the same pinned private boundary."""
    import aiohttp
    from amplifier_web import browser_entry
    state = windows_health_chain
    state["body"] = {"ok": True, "app": "amplifier-unified", "version": "1.2.3",
                     "dataIdentity": "owned-data", "activity": {"working": 2}}
    monkeypatch.setattr(browser_entry, "current_owner_sid", lambda: state["spec"].sid)
    def unused(*args):
        raise AssertionError("The activity question must not wait for Task Scheduler.")
    monkeypatch.setattr(windows_service, "_read", unused)

    class Response:
        async def json(self):
            return state["body"]
        async def __aenter__(self):
            self.status = state.get("status", 200)
            return self
        async def __aexit__(self, *args):
            pass
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def get(self, url, **kwargs):
            assert kwargs["ssl"] is state["pin"] and kwargs["allow_redirects"] is False
            state["requests"].append((url, kwargs))
            if state.get("offline"):
                raise aiohttp.ClientConnectionError("refused")
            return Response()
    monkeypatch.setattr(aiohttp, "ClientSession", Client)
    return state


def test_windows_working_reads_the_count_from_this_installations_pinned_host(working_chain):
    state = working_chain
    assert asyncio.run(health.windows_working(state["home"])) == 2
    (url, request), = state["requests"]
    assert url == "https://127.0.0.1:8942/api/health" and request["params"] == {"activity": "1"}
    assert request["headers"] == {"Authorization": "Bearer synthetic-control"}
    state["body"]["activity"] = {"working": 0}
    assert asyncio.run(health.windows_working(state["home"])) == 0


@pytest.mark.parametrize("change", [
    {"dataIdentity": "another-installation"}, {"app": "something-else"}, {"activity": None},
    {"activity": {}}, {"activity": {"working": True}}, {"activity": {"working": -1}},
    {"activity": {"working": "2"}}, {"activity": [2]}])
def test_windows_working_is_unknown_for_another_host_or_an_answer_without_a_count(working_chain, change):
    state = working_chain
    state["body"].update(change)
    assert asyncio.run(health.windows_working(state["home"])) is None


@pytest.mark.parametrize("fault", ["offline", "status", "pin", "targets", "owner", "credential"])
def test_windows_working_is_unknown_when_no_owned_host_answers(working_chain, monkeypatch, fault):
    from amplifier_web import browser_entry
    state = working_chain
    def unavailable(*args):
        raise browser_entry.BrowserEntryUnavailable("private boundary unavailable")
    if fault == "offline":
        state["offline"] = True
    elif fault == "status":
        state["status"] = 503
    elif fault == "pin":
        state["pin"] = object()
    elif fault == "targets":
        monkeypatch.setattr(browser_entry, "_owner_probe_targets", unavailable)
    elif fault == "owner":
        monkeypatch.setattr(browser_entry, "current_owner_sid", unavailable)
    else:
        monkeypatch.setattr(browser_entry, "_control_token", unavailable)
    assert asyncio.run(health.windows_working(state["home"])) is None
    if fault in {"pin", "targets", "owner", "credential"}:
        # No request, and no credential read before the private pin is valid.
        assert state["requests"] == []
        assert state["credentialReads"] == 0


def reads_end(state, monkeypatch, ends, seconds=3.0):
    """Each read of the activity question ends as the next entry of `ends` says.

    An entry is the error that the read ends with, or None for the host's
    answer. `windows_working` gets a stand-in for its clock: a read that ends
    by a time limit took `seconds`, every other read took no time. A read
    after the last entry fails the test, so no case can read without end.
    """
    import aiohttp
    from types import SimpleNamespace
    clock, left = [0.0], list(ends)
    chain = state.setdefault("client", aiohttp.ClientSession)  # The stand-in of `working_chain`.

    class Client(chain):
        def get(self, url, **kwargs):
            assert left, "A read that this case does not expect"
            end = left.pop(0)
            if isinstance(end, TimeoutError):
                clock[0] += seconds
            response = super().get(url, **kwargs)  # It keeps the request.
            if end is not None:
                raise end
            return response
    monkeypatch.setattr(aiohttp, "ClientSession", Client)
    monkeypatch.setattr(health, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    return clock, left


def test_windows_working_asks_again_when_a_read_ends_by_its_time_limit(working_chain, monkeypatch):
    import aiohttp
    state = working_chain
    # The time limit of the whole request, and aiohttp's own error for a limit while it connects.
    clock, left = reads_end(state, monkeypatch, [
        TimeoutError(), aiohttp.ConnectionTimeoutError("Connection timeout to host"), None])
    assert asyncio.run(health.windows_working(state["home"])) == 2
    assert len(state["requests"]) == 3 and left == [] and clock == [6.0]
    # Each read is the same request, and the credential is read once.
    assert all(request == state["requests"][0] for request in state["requests"])
    assert state["credentialReads"] == 1


def test_windows_working_asks_again_only_the_address_whose_read_ended_by_its_time_limit(working_chain, monkeypatch):
    import aiohttp
    from amplifier_web import browser_entry
    state = working_chain
    first, second = urls = ["https://127.0.0.1:8942/api/health", "https://[::1]:8942/api/health"]
    monkeypatch.setattr(browser_entry, "_owner_probe_targets", lambda *a: (list(urls), state["pin"]))
    # The first address is silent and the second refuses: only the first is asked again.
    clock, left = reads_end(state, monkeypatch, [
        TimeoutError(), aiohttp.ClientConnectionError("refused"), TimeoutError(), None])
    assert asyncio.run(health.windows_working(state["home"])) == 2
    assert [url for url, _ in state["requests"]] == [first, second, first, first] and left == [] and clock == [6.0]
    # The other order: the first address refuses and is not asked again.
    state["requests"].clear()
    clock, left = reads_end(state, monkeypatch, [
        ConnectionRefusedError(10061, "No connection could be made"), TimeoutError(), None])
    assert asyncio.run(health.windows_working(state["home"])) == 2
    assert [url for url, _ in state["requests"]] == [first, second, second] and left == [] and clock == [3.0]
    # Both addresses stay silent: both are asked again. A round that has started is completed, so the third
    # round (it starts at 12 seconds) ends at 18 seconds.
    state["requests"].clear()
    clock, left = reads_end(state, monkeypatch, [TimeoutError()] * 6)
    assert asyncio.run(health.windows_working(state["home"])) is None
    assert [url for url, _ in state["requests"]] == urls * 3 and left == [] and clock == [18.0]


def test_windows_working_is_unknown_when_the_host_stays_silent_for_about_fifteen_seconds(working_chain, monkeypatch):
    state = working_chain
    # Five reads of 3 seconds each. Then the result is the one that a single read gave before.
    clock, left = reads_end(state, monkeypatch, [TimeoutError()] * 5)
    assert asyncio.run(health.windows_working(state["home"])) is None
    assert len(state["requests"]) == 5 and left == [] and clock == [15.0]
    # The bound is the caller's. With 0 it is one read.
    for wait, reads in ((7, 3), (0, 1)):
        state["requests"].clear()
        clock, left = reads_end(state, monkeypatch, [TimeoutError()] * reads)
        assert asyncio.run(health.windows_working(state["home"], wait=wait)) is None
        assert len(state["requests"]) == reads and left == [] and clock == [3.0 * reads]


@pytest.mark.parametrize("end", [
    "refused", "no connection", "closed", "no JSON", "status", "another installation", "no count"])
def test_windows_working_does_not_ask_again_when_a_read_ends_in_another_way(working_chain, monkeypatch, end):
    import aiohttp
    state = working_chain
    error = {"refused": ConnectionRefusedError(10061, "No connection could be made"),
             "no connection": aiohttp.ClientConnectionError("refused"),
             "closed": aiohttp.ServerDisconnectedError(),
             "no JSON": ValueError("The answer is not JSON")}.get(end)
    if end == "status":
        state["status"] = 503
    elif end == "another installation":
        state["body"]["dataIdentity"] = "another-installation"
    elif end == "no count":
        state["body"]["activity"] = {}
    clock, left = reads_end(state, monkeypatch, [error])
    # One read and no wait: no host is there, or the answer is final. A second read would fail in `reads_end`.
    assert asyncio.run(health.windows_working(state["home"])) is None
    assert len(state["requests"]) == 1 and left == [] and clock == [0.0]


def test_windows_working_with_aiohttp_itself_asks_a_silent_listener_again_and_ends_at_a_port_without_one(
        tmp_path, monkeypatch):
    """Real sockets and aiohttp's own errors. The cases above give `windows_working` errors that they chose.

    A socket that listens and never accepts: the system completes the
    connection, and nothing answers the request. A host whose loop does not
    run looks like that. A socket that is bound and does not listen refuses
    the connection, as a port with no host does. Windows refuses only after
    about 2 seconds, its own tries; the native lane prints the time.
    """
    import socket
    import time
    from types import SimpleNamespace
    import aiohttp
    from amplifier_web import auth, browser_entry, deployment
    monkeypatch.setattr(deployment, "load_server_config", lambda home: {})
    monkeypatch.setattr(auth, "data_identity", lambda home: "owned-data")
    monkeypatch.setattr(browser_entry, "current_owner_sid", lambda: "S-1-5-21-100-200-300-1001")
    monkeypatch.setattr(browser_entry, "_control_token", lambda home, sid: "synthetic-control")
    asked, ends, clock, seconds = [], [], [], [0.3]
    request, limit = aiohttp.ClientSession.get, aiohttp.ClientTimeout

    class Kept:
        """The request of aiohttp itself. It keeps how each read ended."""

        def __init__(self, read):
            self.read = read

        async def __aenter__(self):
            try:
                return await self.read.__aenter__()
            except BaseException as error:
                ends.append("limit" if isinstance(error, TimeoutError) else
                            "refused" if isinstance(error, aiohttp.ClientConnectorError) else type(error).__name__)
                raise

        async def __aexit__(self, *error):
            return await self.read.__aexit__(*error)

    def get(self, url, **kwargs):
        asked.append(url)
        return Kept(request(self, url, **kwargs))

    def limited(total):
        assert total == 3  # The time limit of one read. The silent listener gets a tenth of it here.
        return limit(total=seconds[0])

    def now():  # A stand-in for the clock of the bound: 3 seconds go by with each question to it.
        clock.append(3.0 * len(clock))
        return clock[-1]
    monkeypatch.setattr(aiohttp.ClientSession, "get", get)
    monkeypatch.setattr(aiohttp, "ClientTimeout", limited)
    monkeypatch.setattr(health, "time", SimpleNamespace(monotonic=now))
    silent, refusing = socket.socket(), socket.socket()
    try:
        for end in (silent, refusing):
            end.bind(("127.0.0.1", 0))
        silent.listen(8)
        urls = [f"https://127.0.0.1:{end.getsockname()[1]}/api/health" for end in (silent, refusing)]
        pin = aiohttp.Fingerprint(b"\x11" * 32)
        # The silent listener, with a bound of 5 seconds: asked at 0, again at 3, and not at 6.
        monkeypatch.setattr(browser_entry, "_owner_probe_targets", lambda *a: (urls[:1], pin))
        assert asyncio.run(health.windows_working(tmp_path, wait=5)) is None
        assert asked == urls[:1] * 2 and ends == ["limit", "limit"] and clock == [0.0, 3.0, 6.0]
        # No listener, with the real limit of 3 seconds and the bound of 15 seconds.
        asked.clear(), ends.clear(), clock.clear()
        seconds[0] = 3
        monkeypatch.setattr(browser_entry, "_owner_probe_targets", lambda *a: (urls[1:], pin))
        began = time.perf_counter()
        assert asyncio.run(health.windows_working(tmp_path)) is None
        print(f"A port with no listener: {len(asked)} read(s), {time.perf_counter() - began:.2f} s in all; "
              f"the reads ended: {', '.join(ends)}.")
        # A refusal ends the wait: no read comes after it. Nothing here requires that the first read is the
        # refusal: a machine that gives this program no time can end a read by the limit, and only such a read
        # is made again, at most to the bound of 15 seconds (five reads).
        assert asked == urls[1:] * len(ends) and 1 <= len(ends) <= 5
        assert ends[:-1] == ["limit"] * (len(ends) - 1)
        assert ends[-1] == "refused" or ends == ["limit"] * 5
        assert clock == [3.0 * number for number in range(len(ends) + (ends[-1] == "limit"))]
    finally:
        silent.close()
        refusing.close()


@pytest.mark.parametrize("retry", [False, True])
def test_status_names_a_task_that_was_not_confirmed_instead_of_an_unreachable_service_manager(
        tmp_path, monkeypatch, retry):
    # Measured after a computer rename: Task Scheduler answered, the app refused
    # its task, and status said that the service manager could not be reached.
    from amplifier_web import windows_service
    entry = "AmplifierUnified-S-1-5-21-100-200-300-1001"

    def state(home):
        raise windows_service.TaskNotConfirmed(entry, retry=retry)
    monkeypatch.setattr(health, "service_state", state)
    result = asyncio.run(health.status(tmp_path, wait=30))  # Returns at once: nothing to wait for.
    assert result["ok"] is False and result["service"] == "unavailable"
    assert result["entry"] == {"name": entry, "retry": retry}
    assert "could not be reached" not in result["message"] and "Sign in" not in result["nextStep"]
    if retry:
        assert "could not check the account" in result["message"] and "Restart Windows" in result["nextStep"]
    else:
        assert "could not confirm" in result["message"] and entry in result["nextStep"]
        assert "uses only a task that it can confirm" in result["nextStep"]
    assert "changes only" not in result["nextStep"] + result["message"]
    # Any other failure keeps the earlier answer.
    monkeypatch.setattr(health, "service_state", lambda home: (_ for _ in ()).throw(
        windows_service.WindowsServiceError("Task Scheduler refused the operation.")))
    other = asyncio.run(health.status(tmp_path))
    assert other["message"] == "The background service manager could not be reached." and "entry" not in other

