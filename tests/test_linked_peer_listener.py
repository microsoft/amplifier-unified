"""Exercise real peer TLS and owner HTTP sockets, only on fixture loopback."""
import asyncio
import ipaddress
import socket
import sys
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import aiohttp
import pytest
from aiohttp import web

from amplifier_web import linked_peer_listener as peer
from amplifier_web.auth import SESSION_COOKIE, new_session
from amplifier_web.cli import _serve_with_peer_cleanup
from amplifier_web.deployment import load_server_config, save_server_config
from amplifier_web.errors import AppError
from amplifier_web.linked_instances import PEER_PREFIX, PREFIX, _encode_offer, _parse_offer, agent_route
from amplifier_web.server import create_app
from test_service import Runtime

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="peer-only listener supports Linux")

ENABLED = {"enabled": True, "peerListener": True, "lan": False, "tailscale": False}


@pytest.fixture
def loopback(monkeypatch):
    # Only the interface inventory is substituted. Bind, TLS/pin, routes,
    # persistence, cleanup, and the browser-origin check are real.
    async def choose(*, tailscale):
        return {peer.Binding("lo", ipaddress.IPv4Interface("127.0.0.1/8"), "lan")}, set()

    monkeypatch.setattr(peer, "select_binding", choose)


@asynccontextmanager
async def host(root):
    root.mkdir(exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        config = save_server_config(root, {**load_server_config(root, create=False), "port": port,
                                           "unrelated": {"keep": True}})
        app = await fixture_app(root, config)
        runner = web.AppRunner(app, access_log=None)
        try:
            await runner.setup()
            await web.SockSite(runner, sock).start()
            yield f"http://127.0.0.1:{port}", app
        finally:
            await runner.cleanup()


async def fixture_app(root, config):
    return await create_app(root, workspace=root, runtime=Runtime(), voice=False,
                            background_updates=False, preload_providers=False, background_work=False,
                            server_config=config)


async def send(client, host, operation, body, *, origin=True, auth=True, session=None):
    base, app = host
    headers = {"Origin": base if origin is True else origin} if origin else {}
    cookies = {SESSION_COOKIE: session or new_session(app["session_secret"])} if auth else {}
    async with client.post(base + PREFIX + "/" + operation, json=body, cookies=cookies, headers=headers) as res:
        return res.status, await res.json()


async def setup(client, host):
    status, packet = await send(client, host, "setup", {})
    assert status == 200
    return packet["result"]


async def save(client, host, choices, revision=None):
    if revision is None:
        revision = (await setup(client, host))["revision"]
    return await send(client, host, "setup-save", {**choices, "expectedRevision": revision})


def options(offer):
    return {"ssl": aiohttp.Fingerprint(bytes.fromhex(offer["fingerprint"])), "allow_redirects": False}


async def test_fresh_two_local_installations_link_disable_restart_and_revoke(tmp_path, loopback):
    root, other = tmp_path / "first", tmp_path / "second"
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), trust_env=False) as client:
        async with host(other) as b:
            async with host(root) as a:
                first = await setup(client, a)
                assert first["listener"] == "none" and first["status"] == "off" and first["peerListener"] is False
                key = root / "config" / "linked-instances" / "peer-identity.pem"
                assert not key.exists()
                status, packet = await send(client, a, "list", {})
                assert status == 200 and packet["result"]["origins"] == [] and packet["result"]["links"] == []
                original = load_server_config(root)
                body = {**ENABLED, "expectedRevision": first["revision"]}
                for origin, auth, expected in [(None, True, 403), (b[0], True, 403), (True, False, 401)]:
                    assert (await send(client, a, "setup-save", body, origin=origin, auth=auth))[0] == expected
                assert not key.exists()
                for instance in [a, b]:
                    status, packet = await save(client, instance, ENABLED)
                    assert status == 200 and packet["result"]["status"] == "active"
                    assert packet["result"]["listener"] == "peer-only"
                    assert packet["result"]["blockers"] == []
                    assert packet["result"]["peerListener"] is True
                    assert await setup(client, instance) == packet["result"]
                assert key.stat().st_mode & 0o777 == 0o600
                identity = key.read_bytes()
                assert not (root / "config" / "tls").exists()
                saved = load_server_config(root)
                for setting in ("bind", "port", "tls", "public_origins", "unrelated"):
                    assert saved[setting] == original[setting]
                listing = (await send(client, a, "list", {}))[1]["result"]
                orig, = listing["origins"]
                assert orig in (await setup(client, a))["origins"]
                dest, = (await setup(client, b))["origins"]
                assert len({a[0], orig, b[0], dest}) == 4
                make = {"origin": orig, "commandId": "make"}
                offer = _parse_offer((await send(client, a, "offer", make))[1]["result"]["code"])
                request = {"instanceId": offer["instanceId"]}

                # Never attach peer operations to a fresh browser router. Neither
                # owner cookies nor owner actions work on the dedicated router.
                assert not a[1].get("linked_instance_peer_paths")
                for path in ("/", "/api/state", "/api/action", "/api/health", PREFIX + "/setup",
                             PREFIX + "/setup-save", PREFIX + "/list", PREFIX + "/revoke"):
                    async with client.post(orig + path, json={}, **options(offer)) as res:
                        assert res.status == 404
                        assert "Set-Cookie" not in res.headers
                async with client.post(a[0] + PEER_PREFIX + "/identity", json=request,
                                       cookies={SESSION_COOKIE: new_session(a[1]["session_secret"])}) as res:
                    assert res.status in {404, 405}
                async with client.get(orig + PEER_PREFIX + "/identity", **options(offer)) as res:
                    assert res.status == 405
                for headers, path, expected in [
                    ({"Cookie": "session=test"}, "/identity", 403),
                    ({"Origin": a[0]}, "/identity", 403),
                    ({"Host": "localhost:" + orig.rsplit(":", 1)[1]}, "/identity", 403),
                    ({"Authorization": "Bearer " + a[1]["control_token"]}, "/identity", 403),
                    ({"Authorization": "Bearer " + a[1]["control_token"]}, "/enroll", 403),
                    ({"Authorization": "Bearer " + a[1]["control_token"]}, "/status", 400),
                    ({}, "/identity?extra=1", 403),
                ]:
                    async with client.post(orig + PEER_PREFIX + path, json=request, headers=headers,
                                           **options(offer)) as res:
                        assert res.status == expected
                        assert "Set-Cookie" not in res.headers
                async with client.post(orig + PEER_PREFIX + "/identity", json=request, **options(offer)) as res:
                    assert res.status == 200 and await res.json() == {"result": request}

                for change in [{"fingerprint": "a" * 64}, {"instanceId": "b" * 32}]:
                    status, packet = await send(client, b, "join", {
                        "commandId": next(iter(change)), "code": _encode_offer({**offer, **change}),
                    })
                    assert status == 502 and packet["code"] == "link_verification_failed"
                assert (await send(client, a, "list", {}))[1]["result"]["links"] == []
                status, packet = await send(client, b, "join", {"commandId": "join", "code": _encode_offer(offer)})
                assert status == 200
                grant = packet["result"]["grantId"]
                status, packet = await send(client, b, "status", request)
                assert status == 200 and packet["result"]["availability"] == "online"
                generation = packet["result"]["generationId"]

                # Explicit consent withdrawal closes the socket without rotating
                # the certificate or deleting grants. Restore it before restart.
                status, packet = await save(client, a, {**ENABLED, "peerListener": False})
                assert status == 200 and packet["result"]["listener"] == "none"
                assert packet["result"]["peerListener"] is False
                with pytest.raises(aiohttp.ClientConnectorError):
                    await client.post(orig + PEER_PREFIX + "/identity", json=request, **options(offer))
                assert (await send(client, b, "status", request))[0] == 503
                assert key.read_bytes() == identity
                assert (await save(client, a, ENABLED))[1]["result"]["origins"] == [orig]

            async with host(root) as restarted:
                assert (await setup(client, restarted))["origins"] == [orig]
                listing = (await send(client, restarted, "list", {}))[1]["result"]
                assert listing["instanceId"] == offer["instanceId"]
                assert listing["links"][0]["grantId"] == grant
                status, packet = await send(client, b, "status", request)
                assert status == 200 and packet["result"]["generationId"] != generation
                assert key.read_bytes() == identity
                assert (await send(client, restarted, "revoke", {"grantId": grant, "commandId": "revoke"}))[0] == 200
                status, packet = await send(client, b, "status", request)
                assert status == 403 and packet["code"] == "link_revoked"

            # An occupied established endpoint never causes silent migration.
            address = urlsplit(orig)
            with socket.socket() as collision:
                collision.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                collision.bind((address.hostname, address.port))
                collision.listen()
                async with host(root) as blocked:
                    state = await setup(client, blocked)
                    assert state["status"] == "unavailable" and state["listener"] == "none"
                    assert state["origins"] == [] and state["blockers"] == ["peer_port_unavailable"]
                    status, packet = await send(client, blocked, "list", {})
                    assert status == 200 and packet["result"]["origins"] == []
                    assert packet["result"]["links"][0]["grantId"] == grant
            async with host(root) as recovered:
                assert (await setup(client, recovered))["origins"] == [orig]
            assert key.read_bytes() == identity


async def test_stale_owner_discovery_cannot_undo_disable(tmp_path, loopback):
    async with host(tmp_path) as a, aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as client:
        assert (await save(client, a, {**ENABLED, "lan": True}))[0] == 200
        # Two independently authenticated clients read the same saved snapshot.
        sessions = [new_session(a[1]["session_secret"]) for _ in range(2)]
        reads = await asyncio.gather(*(send(client, a, "setup", {}, session=session) for session in sessions))
        assert reads[0] == reads[1]
        snapshot = reads[0][1]["result"]
        payload = {"expectedRevision": snapshot["revision"], **ENABLED}
        status, response = await send(client, a, "setup-save", {**payload, "enabled": False}, session=sessions[0])
        assert status == 200 and response["result"]["status"] == "off"
        assert response["result"]["listener"] == "none"
        path = tmp_path / "config" / "server.yaml"
        config = path.read_bytes()
        status, response = await send(client, a, "setup-save", {**payload, "lan": True}, session=sessions[1])
        assert (status, response["code"], response["accepted"]) == (409, "link_setup_conflict", False)
        assert path.read_bytes() == config
        state = await setup(client, a)
        assert not state["enabled"] and not state["discovery"]["lan"] and state["origins"] == []
        port = urlsplit(snapshot["origins"][0]).port
        with pytest.raises(aiohttp.ClientConnectorError):
            await client.post(f"https://127.0.0.1:{port}" + PEER_PREFIX + "/identity", json={})
        # Missing witness cannot bypass the same fence.
        assert (await send(client, a, "setup-save", ENABLED))[0] == 400


async def test_inventory_selects_only_eligible_exact_interfaces(monkeypatch):
    def row(name, ip, prefix=24, flags=("UP", "MULTICAST")):
        return {"ifname": name, "flags": list(flags), "addr_info": [{"local": ip, "prefixlen": prefix}]}

    rows = [row("external", "203.0.113.2"), row("lo", "127.0.0.1", flags=("UP", "LOOPBACK")),
            row("down", "10.0.0.3", flags=("MULTICAST",)), row("wrong-scope", "10.0.0.2", 4),
            row("tailscale0", "100.64.0.2", 32, ("UP", "POINTOPOINT"))]

    async def inventory(*args):
        assert args == ("ip", "-j", "-4", "addr", "show", "up", "scope", "global")
        return rows

    monkeypatch.setattr(peer, "_json", inventory)
    choices = await peer.select_binding(tailscale=False)
    with pytest.raises(peer.Blocked, match="peer_interface_unavailable"):
        peer._select(choices, None)
    assert peer._select(await peer.select_binding(tailscale=True), None).route == "tailscale"
    rows.append(row("lan0", "192.168.1.8"))
    chosen = peer._select(await peer.select_binding(tailscale=True), None)
    assert chosen.interface == "lan0" and str(chosen.local.ip) == "192.168.1.8"
    rows.append(row("lan1", "192.168.2.8"))
    choices = await peer.select_binding(tailscale=False)
    with pytest.raises(peer.Blocked, match="peer_interface_ambiguous"):
        peer._select(choices, None)
    assert peer._select(choices, {"binding": peer._address(chosen)}) == chosen
    rows.remove(rows[-2])
    with pytest.raises(peer.Blocked, match="peer_interface_unavailable"):
        peer._select(await peer.select_binding(tailscale=False), {"binding": peer._address(chosen)})


@pytest.mark.parametrize("damage", ["missing", "malformed", "replaced"])
async def test_established_peer_identity_is_never_recreated_on_failure(tmp_path, loopback, damage):
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as client:
        async with host(tmp_path) as a:
            state = (await save(client, a, ENABLED))[1]["result"]
            assert state["status"] == "active"
            origin, = state["origins"]
            listing = (await send(client, a, "list", {}))[1]["result"]
        key = tmp_path / "config" / "linked-instances" / "peer-identity.pem"
        pem = key.read_bytes()
        if damage == "missing":
            key.unlink()
        else:
            key.write_bytes(b"malformed" if damage == "malformed" else peer._new_identity(listing["instanceId"]))
        damaged_bytes = key.read_bytes() if key.exists() else None
        async with host(tmp_path) as blocked:
            state = await setup(client, blocked)
            assert state["status"] == "unavailable" and state["listener"] == "none"
            assert state["blockers"] == ["peer_identity_unavailable"] and state["origins"] == []
            assert (key.read_bytes() if key.exists() else None) == damaged_bytes
            address = urlsplit(origin)
            with socket.socket() as reusable:
                reusable.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                reusable.bind((address.hostname, address.port))
        if damage == "missing":
            key.touch(mode=0o600)
        key.write_bytes(pem)
        async with host(tmp_path) as recovered:
            assert (await setup(client, recovered))["origins"] == [origin]


async def test_partial_attach_does_not_leak_reserved_socket(tmp_path, loopback, monkeypatch):
    async with host(tmp_path) as a, aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as client:
        reserved = None

        async def fail_site(site):
            nonlocal reserved
            reserved = site._sock.getsockname()
            raise OSError("fixture failed after the socket was reserved")

        with monkeypatch.context() as fixture:
            fixture.setattr(web.SockSite, "start", fail_site)
            status, packet = await save(client, a, ENABLED)
        assert status == 200 and packet["result"]["blockers"] == ["peer_listener_unavailable"]
        assert packet["result"]["status"] == "unavailable" and packet["result"]["origins"] == []
        assert reserved is not None
        with socket.socket() as check:
            check.bind(reserved)
        status, packet = await save(client, a, ENABLED)
        assert status == 200 and packet["result"]["status"] == "active"
        assert packet["result"]["origins"] == [f"https://{reserved[0]}:{reserved[1]}"]


async def test_later_startup_failure_closes_attached_peer_with_event_loop_still_running(tmp_path, loopback):
    config = save_server_config(tmp_path, {"linked_instances": True, "linked_instances_peer_listener": True})
    app = await fixture_app(tmp_path, config)
    links = app["service"].linked_instances
    address = None

    async def fail_after_peer_start(_app):
        nonlocal address
        assert links.listener.runner is not None
        address = urlsplit(links.listener.origin)
        raise RuntimeError("fixture later startup failed")

    # Fail after the real peer cleanup context enters, while the browser
    # AppRunner is still in setup and would skip ordinary shutdown hooks.
    app.on_startup.insert(1, fail_after_peer_start)

    async def assembled():
        return app

    def unexpected_ready(_):
        pytest.fail("browser host was not started")

    try:
        with pytest.raises(RuntimeError, match="fixture later startup failed"):
            await _serve_with_peer_cleanup(assembled(), (), None, unexpected_ready)
        assert links.listener.origin is None and links.listener.runner is None
        assert address is not None
        with socket.socket() as check:
            check.bind((address.hostname, address.port))
    finally:
        await app["service"].close()


async def test_cancelled_activation_drains_partially_attached_peer(tmp_path, loopback, monkeypatch):
    config = save_server_config(tmp_path, {"linked_instances": True, "linked_instances_peer_listener": True})
    app = await fixture_app(tmp_path, config)
    links = app["service"].linked_instances
    runner = web.AppRunner(app)
    original = web.SockSite.start
    attached = asyncio.get_running_loop().create_future()
    continue_attach = asyncio.Event()

    async def pause_after_real_attach(site):
        await original(site)
        attached.set_result((site, site._sock.getsockname()))
        await continue_attach.wait()

    monkeypatch.setattr(web.SockSite, "start", pause_after_real_attach)
    starting = asyncio.create_task(runner.setup())
    try:
        site, address = await asyncio.wait_for(attached, 5)
        assert site._server.is_serving()
        starting.cancel()
        continue_attach.set()
        with pytest.raises(asyncio.CancelledError):
            await starting
        # It was cancelled before yielding the cleanup context, so the
        # startup path itself must already have closed it.
        assert not site._server.is_serving()
        assert links.listener.origin is None and links.listener.runner is None
        with socket.socket() as check:
            check.bind(address)
    finally:
        continue_attach.set()
        if not starting.done():
            starting.cancel()
        await asyncio.gather(starting, return_exceptions=True)
        await runner.cleanup()
        await app["service"].close()


@pytest.mark.parametrize("blocked", [False, True])
async def test_local_owner_revokes_a_retained_grant_without_reopening_network(tmp_path, loopback, blocked):
    root = tmp_path / "issuer"
    async with host(tmp_path / "joiner") as b, aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as client:
        async with host(root) as a:
            for instance in (a, b):
                assert (await save(client, instance, ENABLED))[1]["result"]["status"] == "active"
            origin, = (await setup(client, a))["origins"]
            code = (await send(client, a, "offer", {"origin": origin, "commandId": "offer"}))[1]["result"]["code"]
            status, packet = await send(client, b, "join", {"commandId": "join", "code": code})
            assert status == 200
            grant = packet["result"]["grantId"]
            target = {"instanceId": _parse_offer(code)["instanceId"]}
            assert (await send(client, b, "status", target))[0] == 200
            if not blocked:
                assert (await save(client, a, {**ENABLED, "enabled": False}))[1]["result"]["status"] == "off"

        address = urlsplit(origin)
        with socket.socket() as occupying:
            if blocked:
                occupying.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                occupying.bind((address.hostname, address.port))
                occupying.listen()
            async with host(root) as inactive:
                settings = await setup(client, inactive)
                assert settings["listener"] == "none" and settings["status"] == ("unavailable" if blocked else "off")
                links = inactive[1]["service"].linked_instances
                status, listing = await send(client, inactive, "list", {})
                assert status == 200 and listing["result"]["origins"] == []
                row, = listing["result"]["links"]
                assert row["grantId"] == grant and not row["revoked"]
                assert "credential" not in row and "credentialDigest" not in row
                revoke = {"commandId": "revoke", "grantId": grant}
                for operation, body in (("list", {}), ("revoke", revoke)):
                    assert (await send(client, inactive, operation, body, auth=False))[0] == 401
                    assert (await send(client, inactive, operation, body, origin=False))[0] == 403
                    assert (await send(client, inactive, operation, body, origin=b[0]))[0] == 403
                for operation, body in (("join", {"commandId": "denied", "code": code}), ("status", target),
                                        ("offer", {"origin": origin, "commandId": "denied"}),
                                        ("lan-browse", {}), ("lan-discoverable", {"enabled": True}),
                                        ("tailscale-devices", {})):
                    assert (await send(client, inactive, operation, body))[0] == 403
                with pytest.raises(AppError) as refusal:
                    await agent_route(links, "get_state", {"path": "/linkedInstances"})
                assert refusal.value.code == "link_unsupported"
                if not blocked:
                    with pytest.raises(aiohttp.ClientConnectorError):
                        await client.post(origin + PEER_PREFIX + "/identity", json=target)
                status, packet = await send(client, inactive, "revoke", revoke)
                assert status == 200 and packet["result"]["revoked"] is True
                assert (await send(client, inactive, "revoke", revoke)) == (status, packet)
                assert await setup(client, inactive) == settings
                assert links.listener.runner is None and links.discovery.expires_at() is None
                if not blocked:
                    with pytest.raises(aiohttp.ClientConnectorError):
                        await client.post(origin + PEER_PREFIX + "/status", json=target)
        async with host(root) as again:
            assert (await save(client, again, ENABLED))[1]["result"]["origins"] == [origin]
            status, packet = await send(client, b, "status", target)
            assert status == 403 and packet["code"] == "link_revoked"