"""Exact configured sockets, phase-bound failures and deterministic ownership."""
import asyncio
from contextlib import nullcontext
import errno
import os
import socket
from types import SimpleNamespace

from aiohttp import web
import pytest

from amplifier_web import cli
from amplifier_web.deployment import load_server_config, save_server_config
from test_onboarding_listener_readiness import _minimal_cli


def _info(host="127.0.0.1", port=8941, *, family=socket.AF_INET, scope=0):
    address = (host, port, 0, scope) if family == socket.AF_INET6 else (host, port)
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", address)


@pytest.fixture
def network(monkeypatch):
    net = SimpleNamespace(infos={"fixture": [_info()]}, errors={}, sockets=[], resolutions=[])

    def resolve(host, port, **kwargs):
        net.resolutions.append((host, port, kwargs))
        value = net.infos[host]
        if isinstance(value, BaseException):
            raise value
        return value

    class Socket:
        def __init__(self, family, kind, protocol):
            error = net.errors.get(("create", family))
            if error:
                raise error
            self.family, self.options, self.address = family, [], None
            self.closed, self.listening, self.blocking = False, False, True
            net.sockets.append(self)

        def close(self):
            self.closed = True

        def setsockopt(self, *option):
            self.options.append(option)

        def bind(self, address):
            self.address = address
            error = net.errors.get(("bind", address))
            if error:
                raise error

        def listen(self, backlog):
            assert backlog == 128
            error = net.errors.get(("listen", self.address))
            if error:
                raise error
            self.listening = True

        def setblocking(self, value):
            self.blocking = value

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "socket", Socket)
    return net


def test_passive_resolution_once_dedup_options_and_scope(network):
    v4, v6 = _info(), _info("fe80::1", family=socket.AF_INET6, scope=7)
    network.infos["fixture"] = [v4, v4, v6]
    with cli._listener_sockets(["fixture"], 8941) as sockets:
        assert len(sockets) == 2 and sockets == network.sockets
        assert all(sock.listening and not sock.blocking and not sock.closed for sock in sockets)
        assert sockets[1].address == ("fe80::1", 8941, 0, 7)
        assert (socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, True) in sockets[1].options
        if os.name == "posix":
            assert (socket.SOL_SOCKET, socket.SO_REUSEADDR, True) in sockets[0].options
        assert all(option[1] != getattr(socket, "SO_REUSEPORT", None)
                   for sock in sockets for option in sock.options)
    assert all(sock.closed for sock in network.sockets)
    assert network.resolutions == [("fixture", 8941, {"type": socket.SOCK_STREAM, "flags": socket.AI_PASSIVE})]


def test_windows_never_enables_posix_reuse(network, monkeypatch):
    # Restore os.name before pytest/pathlib observes the synthetic platform.
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt")
        with cli._listener_sockets(["fixture"], 8941) as sockets:
            assert not sockets[0].options


@pytest.mark.parametrize("code", [errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL])
@pytest.mark.parametrize("missing_first", [True, False])
def test_only_unavailable_family_can_yield_within_same_name(network, code, missing_first):
    v4, v6 = _info(), _info("::1", family=socket.AF_INET6)
    network.infos["fixture"] = [v6, v4] if missing_first else [v4, v6]
    phase, key = ("bind", v6[4]) if code == errno.EADDRNOTAVAIL else ("create", socket.AF_INET6)
    network.errors[(phase, key)] = OSError(code, "unavailable family")
    with cli._listener_sockets(["fixture"], 8941) as sockets:
        assert [sock.address for sock in sockets] == [v4[4]]
    assert all(sock.closed for sock in network.sockets)


@pytest.mark.parametrize("phase", ["create", "bind", "listen"])
@pytest.mark.parametrize("code", [errno.EADDRINUSE, errno.EACCES, errno.EMFILE, errno.EIO])
def test_conflict_permission_and_resource_errors_never_degrade(network, phase, code):
    v4, v6 = _info(), _info("::1", family=socket.AF_INET6)
    network.infos["fixture"] = [v4, v6]
    error = OSError(code, "specific OS failure")
    network.errors[(phase, socket.AF_INET6 if phase == "create" else v6[4])] = error
    expected = SystemExit if code in {errno.EADDRINUSE, errno.EACCES} else OSError
    with pytest.raises(expected) as captured:
        with cli._listener_sockets(["fixture"], 8941):
            pytest.fail("silently degraded")
    if expected is OSError:
        assert captured.value is error
    assert all(sock.closed for sock in network.sockets)


@pytest.mark.parametrize("code", [None, errno.EIO, errno.EAFNOSUPPORT])
def test_listen_failures_are_not_skipped_as_bind_unavailability(network, code):
    error = OSError("untyped failure") if code is None else OSError(code, "listen failure")
    network.infos["fixture"] += [_info("127.0.0.2")]
    network.errors[("listen", _info()[4])] = error
    with pytest.raises(SystemExit if code == errno.EAFNOSUPPORT else OSError):
        with cli._listener_sockets(["fixture"], 8941):
            pytest.fail("listen failure ignored")
    assert len(network.sockets) == 1 and network.sockets[0].closed


@pytest.mark.parametrize("infos", [[], socket.gaierror(errno.EACCES, "synthetic EAI collision")])
def test_dns_has_own_guidance_and_closes_prior_entry(network, infos):
    network.infos["unresolved"] = infos
    with pytest.raises(SystemExit, match="resolve configured bind address unresolved") as captured:
        with cli._listener_sockets(["fixture", "unresolved"], 8941):
            pytest.fail("unresolved bind admitted")
    assert "Permission was denied" not in str(captured.value)
    assert all(sock.closed for sock in network.sockets)


@pytest.mark.parametrize("phase", ["create", "bind"])
def test_no_listener_in_one_entry_fails_despite_other_entry_success(network, phase):
    network.infos["missing"] = [_info("2001:db8::254", family=socket.AF_INET6)]
    key = socket.AF_INET6 if phase == "create" else network.infos["missing"][0][4]
    code = errno.EAFNOSUPPORT if phase == "create" else errno.EADDRNOTAVAIL
    network.errors[(phase, key)] = OSError(code, "no listener")
    with pytest.raises(SystemExit, match="missing, port"):
        with cli._listener_sockets(["fixture", "missing"], 8941):
            pytest.fail("one configured entry substituted for another")
    assert all(sock.closed for sock in network.sockets)


def test_separate_alias_entries_are_not_silently_deduplicated(network):
    network.infos["alias"] = network.infos["fixture"]
    with cli._listener_sockets(["fixture", "alias"], 8941) as sockets:
        # OS owns overlap policy, not a cross-entry identity registry.
        assert len(sockets) == 2 and len(network.resolutions) == 2


@pytest.mark.parametrize("failure", [None, RuntimeError("factory"), asyncio.CancelledError()])
def test_real_sockets_remain_held_and_close_on_body_exit(failure):
    with pytest.raises(type(failure)) if failure else nullcontext():
        with cli._listener_sockets(["127.0.0.1", "127.0.0.2"], 0) as sockets:
            held = list(sockets)
            assert len(held) == 2
            assert all(sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) for sock in held)
            for sock in held:
                with socket.socket() as competitor:
                    with pytest.raises(OSError) as conflict:
                        competitor.bind(sock.getsockname())
                    assert conflict.value.errno == errno.EADDRINUSE
            if failure:
                raise failure
    assert all(sock.fileno() == -1 for sock in held)


async def test_socksite_receives_same_real_socket_and_later_site_failure_closes_every_fd(monkeypatch):
    original = web.SockSite.start
    for fail_second in (False, True):
        attached = []
        runner = web.AppRunner(web.Application())
        with cli._listener_sockets(["127.0.0.1", "127.0.0.2"], 0) as sockets:
            held = list(sockets)
            addresses = [sock.getsockname() for sock in held]
            await runner.setup()

            async def start(site):
                attached.append(site._sock)
                if fail_second and len(attached) == 2:
                    raise OSError(errno.EIO, "second attach failed")
                await original(site)

            with monkeypatch.context() as patch:
                patch.setattr(web.SockSite, "start", start)
                try:
                    for sock in sockets:
                        await web.SockSite(runner, sock).start()
                    assert not fail_second
                    assert runner.addresses == addresses
                    assert attached == held
                except OSError:
                    assert fail_second
                finally:
                    await runner.cleanup()
            assert attached == held
        assert all(sock.fileno() == -1 for sock in held)


@pytest.mark.parametrize("error", [RuntimeError("factory"), asyncio.CancelledError(),
                                 OSError(errno.EADDRINUSE, "application failure")])
def test_cli_releases_exact_handoff_sockets_without_classifying_body_failure(tmp_path, monkeypatch, error):
    from amplifier_web import server
    _minimal_cli(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.threading, "Timer", lambda *a, **kw: pytest.fail("Browser scheduled"))
    held = []

    async def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(server, "create_app", fail)
    run_app = web.run_app

    def run(app, **kwargs):
        assert "host" not in kwargs and "port" not in kwargs
        held.extend(kwargs["sock"])
        assert all(sock.fileno() != -1 for sock in held)
        return run_app(app, **kwargs)

    monkeypatch.setattr(web, "run_app", run)
    with pytest.raises(type(error)) as captured:
        cli._serve_unlocked(cli._parse(), tmp_path)
    assert captured.value is error
    assert held and all(sock.fileno() == -1 for sock in held)


def test_tls_failure_precedes_socket_acquisition_and_migration(tmp_path, monkeypatch):
    from amplifier_web import tls
    _minimal_cli(monkeypatch, tmp_path)
    error = PermissionError(errno.EACCES, "TLS key failure")
    def fail(*a, **kw):
        raise error
    monkeypatch.setattr(tls, "ssl_context", fail)
    monkeypatch.setattr(cli, "_listener_sockets", lambda *a: pytest.fail("TLS failed before bind"))
    monkeypatch.setattr("amplifier_web.settings_migration.migrate_settings", lambda *a: pytest.fail("migration before TLS"))
    with pytest.raises(PermissionError) as captured:
        cli._serve_unlocked(cli._parse(), tmp_path)
    assert captured.value is error


def test_literal_duplicates_stay_canonical_and_real_ipv6_stays_v6_only(tmp_path):
    config = save_server_config(tmp_path, {**load_server_config(tmp_path), "bind": ["127.0.0.1", "127.0.0.1"]})
    assert config["bind"] == ["127.0.0.1"]
    if not socket.has_ipv6:
        pytest.skip("IPv6 unavailable")
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except OSError as exc:
        if exc.errno not in {errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT}:
            raise
        pytest.skip("IPv6 loopback not enabled on this test host")
    with cli._listener_sockets(["::1"], 0) as sockets:
        assert len(sockets) == 1
        assert sockets[0].getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) == 1
        assert sockets[0].getsockname()[0] == "::1"


@pytest.mark.parametrize("binds", [["0.0.0.0", "127.0.0.1"], ["127.0.0.1", "0.0.0.0"]])
async def test_real_wildcard_overlap_matches_platform_tcpsite_policy(binds):
    # macOS and Linux need not agree about overlaps. Compare on this OS to the
    # previous TCPSite path, rather than asserting Linux policy for every host.
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    runner = web.AppRunner(web.Application())
    await runner.setup()
    try:
        for host in binds:
            await web.TCPSite(runner, host, port).start()
        old_conflict = False
    except OSError as exc:
        assert exc.errno == errno.EADDRINUSE
        old_conflict = True
    finally:
        await runner.cleanup()
    try:
        with cli._listener_sockets(binds, port):
            assert not old_conflict
    except SystemExit as exc:
        assert old_conflict and "already in use" in str(exc)


def test_real_localhost_resolution_uses_only_its_resolved_addresses():
    infos = socket.getaddrinfo("localhost", 0, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)
    candidates = {info[4][0] for info in infos}
    with cli._listener_sockets(["localhost"], 0) as sockets:
        assert sockets
        assert all(sock.getsockname()[0] in candidates for sock in sockets)