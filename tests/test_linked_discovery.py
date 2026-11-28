"""Synthetic discovery and real loopback TLS; no LAN or tailnet is exercised."""
import asyncio
import socket
import time

import pytest
from aiohttp import web

from amplifier_web import linked_discovery, tls
from amplifier_web.deployment import load_server_config, save_server_config
from amplifier_web.errors import AppError
from amplifier_web.linked_discovery import Discovery, Endpoint, LocalDiscovery, Node
from amplifier_web.linked_instances import (
    LOCAL_TLS, PEER_PREFIX, DirectRoute, DirectTransport, LinkedInstances,
    _encode_offer, _leaf_fingerprint, _new_link,
)

PEER = "a" * 32
GRANT = "b" * 32
GENERATION = "c" * 32
LAN = Endpoint(PEER, "Private Amplifier", "192.168.1.12", 8941)
OVERLAY = Node("selected", "Selected device", True, ("100.64.1.11",))


class Lease:
    def __init__(self):
        self.returncode = None

    def terminate(self):
        self.returncode = 0

    async def wait(self):
        return self.returncode


class Inventory:
    lan_up = True

    async def lan(self):
        return [LAN, LAN, Endpoint("d" * 32, "Public hint", "8.8.8.8", 443)]

    async def lan_address(self, address):
        assert address == LAN.address
        return self.lan_up

    async def roster(self):
        return [OVERLAY, Node("other", "Other device", True, ("100.64.1.12",))]

    async def tailscale_interface(self, address):
        assert address == OVERLAY.addresses[0]
        return "tailscale0"

    async def publish(self, name, port, label):
        assert name.startswith("Amplifier-") and port == LAN.port and label
        self.lease = Lease()
        return self.lease


def config(origin="https://localhost"):
    return {
        "linked_instances": True, "public_origins": [origin],
        "tls": LOCAL_TLS, "port": 8941, "bind": ["0.0.0.0"],
        "linked_instances_discovery": {
            "lan": True, "tailscale": True,
            "tailscale_endpoints": {OVERLAY.device_id: [
                {"instanceId": PEER, "port": LAN.port, "label": LAN.label},
            ]},
        },
    }


async def test_selected_lookup_and_advertisement_with_no_endpoint_calls(monkeypatch):
    inventory = Inventory()
    discovery = Discovery(config(), backend=inventory)
    own = "e" * 32
    assert (await discovery.find(OVERLAY.device_id, own))["status"] == "unavailable"
    assert await discovery.devices() == {"status": "ok", "devices": [
        {"deviceId": "selected", "label": "Selected device", "online": True},
        {"deviceId": "other", "label": "Other device", "online": True},
    ]}
    assert (await discovery.find("other", own)) == {
        "status": "unavailable", "reason": "tailscale_profile_required", "instances": [],
    }
    selected, = (await discovery.find(OVERLAY.device_id, own))["instances"]
    assert set(selected) == {"candidateId", "instanceId", "label", "route"}
    assert selected["route"] == "tailscale" and selected["instanceId"] == PEER
    nearby, = (await discovery.browse(own))["instances"]
    assert nearby["route"] == "lan" and nearby["instanceId"] == PEER
    assert discovery.expires_at() is None
    monkeypatch.setattr(linked_discovery, "LEASE_SECONDS", 1)
    lease = await discovery.discoverable(True, own)
    first_process = inventory.lease
    assert await discovery.discoverable(True, own) == lease and inventory.lease is first_process
    assert lease["status"] == "ok" and lease["expiresAt"] == discovery.expires_at()
    assert lease["expiresAt"] <= time.time() + 1
    async with asyncio.timeout(2):
        await discovery.lease_task
    assert inventory.lease.returncode == 0 and discovery.expires_at() is None
    await discovery.discoverable(True, own)
    await discovery.close(None)
    assert inventory.lease.returncode == 0 and discovery.expires_at() is None


async def test_system_adapter_only_reads_known_inventory_and_routes(monkeypatch):
    calls = []

    async def local_json(*args):
        calls.append(args)
        if args == ("tailscale", "status", "--json"):
            return {"BackendState": "Running", "Peer": {
                "known": {"ID": "selected", "HostName": "Known host", "TailscaleIPs": ["100.64.1.11"]},
                "shared": {"ID": "shared", "ShareeNode": True, "TailscaleIPs": ["100.64.1.12"]},
            }}
        if args == ("ip", "-j", "-4", "route", "get", "100.64.1.11"):
            return [{"dev": "tailscale0", "prefsrc": "100.64.1.5"}]
        raise AssertionError(args)

    monkeypatch.setattr(linked_discovery.sys, "platform", "linux")
    monkeypatch.setattr(linked_discovery, "_json", local_json)
    backend = LocalDiscovery(())
    assert await backend.roster() == [Node("selected", "Known host", None, ("100.64.1.11",))]
    assert len(calls) == 1
    assert await backend.tailscale_interface("100.64.1.11") == "tailscale0"


async def test_lan_publisher_requires_one_proven_private_interface(monkeypatch):
    calls = []
    first = {"ifname": "eth1", "flags": ["MULTICAST"], "addr_info": [{"local": "192.168.1.5", "prefixlen": 24}]}
    second = {"ifname": "eth2", "flags": ["MULTICAST"], "addr_info": [{"local": "172.16.0.5", "prefixlen": 24}]}

    async def local_json(*_args):
        return [first, second]

    async def spawn(*args):
        calls.append(args)
        process = Lease()
        process.stdout = asyncio.StreamReader()
        process.stdout.feed_data(b"established\n")
        process.stdout.feed_eof()
        return process

    monkeypatch.setattr(linked_discovery.sys, "platform", "linux")
    monkeypatch.setattr(linked_discovery, "_json", local_json)
    monkeypatch.setattr(linked_discovery, "_pipe", spawn)
    for binds in (("127.0.0.1",), ("0.0.0.0",)):
        with pytest.raises(linked_discovery.Unavailable):
            await LocalDiscovery(binds).publish("Amplifier-" + PEER, 8941, "Private")
    assert not calls
    await LocalDiscovery(("192.168.1.5",)).publish("Amplifier-" + PEER, 8941, "Private")
    assert len(calls) == 1
    assert calls[0][-3:] == ("eth1", "192.168.1.5", "120")


async def test_verified_route_preference_keeps_the_same_grant(tmp_path):
    inventory = Inventory()
    traffic = []

    class Transport:
        async def discovered_status(self, route, saved, payload, credential):
            assert route == DirectRoute("https://localhost", "1" * 64)
            assert payload["instanceId"] == PEER and payload["grantId"] == GRANT
            assert credential() == "2" * 43
            traffic.append("lan" if saved["deviceId"] is None else "tailscale")
            return {"instanceId": PEER, "generationId": GENERATION, "availability": "online"}

        async def status(self, *_args):
            pytest.fail("Configured route was not needed")

    links = LinkedInstances(tmp_path, config(), transport=Transport(), discovery=inventory)
    with links.store.transaction() as state:
        state["links"][GRANT] = {**_new_link(GRANT, PEER, "outgoing", "https://localhost"),
                                 "fingerprint": "1" * 64, "credential": "2" * 43}
    own = links.list()["instanceId"]
    lan, = (await links.discovery.browse(own))["instances"]
    await links.discovery.devices()
    overlay, = (await links.discovery.find(OVERLAY.device_id, own))["instances"]
    for candidate in (lan, overlay):
        assert (await links.remember({"candidateId": candidate["candidateId"], "grantId": GRANT}))["grantId"] == GRANT
    for up, route in ((True, "lan"), (False, "tailscale"), (True, "lan")):
        inventory.lan_up = up
        assert await links.status({"instanceId": PEER}, owner=True) == {
            "instanceId": PEER, "generationId": GENERATION, "availability": "online", "route": route,
        }
    assert traffic == ["lan", "tailscale", "lan", "tailscale", "lan"]
    restored = LinkedInstances(tmp_path, config(), transport=Transport(), discovery=inventory)
    assert (await restored.remember({"candidateId": lan["candidateId"], "grantId": GRANT}))["route"] == "lan"
    assert len(traffic) == 5  # exact response recovered without a new socket
    assert len(links.list()["links"]) == 1
    assert "discovery" not in links.list(outgoing_only=True)


async def test_setup_passive_status_and_narrow_saved_choices(tmp_path):
    off = LinkedInstances(tmp_path, {"public_origins": []})
    assert off.setup()["status"] == "off"
    assert not (tmp_path / "config").exists()
    before = save_server_config(tmp_path, {**config(), "linked_instances": False,
                                          "extension": {"keep": "value"}})
    off = LinkedInstances(tmp_path, before)
    # The configured layout is not proof of usable TLS.
    res = await off.save_setup({"enabled": True, "lan": False, "tailscale": True,
                                "expectedRevision": off.setup()["revision"]})
    assert res["status"] == "unavailable" and res["blockers"] == ["app_tls_unavailable"]
    saved = load_server_config(tmp_path, create=False)
    assert saved["linked_instances_setup_mark"]
    assert {key: value for key, value in saved.items() if key != "linked_instances_setup_mark"} == {
        **before, "linked_instances": True, "linked_instances_discovery": {
        **before["linked_instances_discovery"], "lan": False,
    }}
    assert not off.enabled and not off.store.path.exists()


async def test_setup_reports_pending_until_runtime_uses_saved_preferences(tmp_path, monkeypatch):
    monkeypatch.setattr(tls, "tls_ready", lambda *_: True)
    initial = save_server_config(tmp_path, {**config(), "linked_instances": False})
    off = LinkedInstances(tmp_path, initial)
    assert (await off.save_setup({"enabled": True, "lan": False, "tailscale": False,
                                  "expectedRevision": off.setup()["revision"]}))["status"] == "pending"
    ready = LinkedInstances(tmp_path, load_server_config(tmp_path))
    assert ready.setup()["status"] == "active" and ready.enabled
    assert ready.list()["discovery"] == {"lan": False, "tailscale": False}
    assert (await ready.save_setup({"enabled": False, "lan": False, "tailscale": False,
                                    "expectedRevision": ready.setup()["revision"]}))["status"] == "pending"
    assert ready.enabled


async def test_discovered_invite_mismatch_does_not_dispatch_or_reserve(tmp_path):
    class Transport:
        async def enroll(self, *_args):
            pytest.fail("A mismatched discovered invite must not dispatch")

    links = LinkedInstances(tmp_path, config(), transport=Transport())
    code = _encode_offer({"origin": "https://localhost", "instanceId": PEER,
                          "fingerprint": "1" * 64, "secret": "2" * 43})
    with pytest.raises(AppError) as refused:
        await links.join({"commandId": "mismatch", "code": code, "expectedInstanceId": "e" * 32})
    assert refused.value.code == "link_verification_failed" and refused.value.status == 502
    with links.store.transaction() as state:
        assert not state["commands"] and not state["links"]


async def test_owner_setup_over_real_loopback_and_only_narrow_body(tmp_path):
    import aiohttp
    from amplifier_web.auth import SESSION_COOKIE, new_session
    from amplifier_web.linked_instances import PREFIX
    from amplifier_web.server import create_app
    from test_service import Runtime

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    initial = save_server_config(tmp_path, {"port": port, "note": "keep"})
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                           background_updates=False, preload_providers=False, background_work=False,
                           server_config=initial)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.SockSite(runner, sock).start()
        async with aiohttp.ClientSession() as client:
            session = new_session(app["session_secret"])

            async def send(operation, args, *, auth=True, origin=base):
                headers = {"Origin": origin} if origin else {}
                cookies = {SESSION_COOKIE: session} if auth else None
                async with client.post(base + PREFIX + "/" + operation, headers=headers, cookies=cookies,
                                       json=args) as response:
                    return response.status, await response.json()

            snapshot = (await send("setup", {}))[1]["result"]
            choice = {"enabled": True, "lan": True, "tailscale": False, "expectedRevision": snapshot["revision"]}
            assert (await send("setup", {}, auth=False))[0] == 401
            assert (await send("setup-save", choice, origin=None))[0] == 403
            assert (await send("setup-save", choice, origin="http://127.0.0.1:1234"))[0] == 403
            assert (await send("setup-save", {**choice, "bind": ["0.0.0.0"]}))[0] == 400
            assert (await send("setup-save", {**choice, "enabled": 1}))[0] == 400
            assert snapshot["status"] == "off"
            status, data = await send("setup-save", choice)
            assert status == 200 and data["result"] == {
                "status": "unavailable", "enabled": True, "discovery": {"lan": True, "tailscale": False},
                "origins": [], "blockers": ["https_origin_required", "app_tls_required"], "lanExpiresAt": None,
                "peerListener": False, "listener": "none",
                "revision": data["result"]["revision"],
            }
            assert data["result"]["revision"] != snapshot["revision"]
            assert (await send("setup", {}))[1] == data
            status, data = await send("list", {})
            assert status == 200 and data["result"]["links"] == [] and data["result"]["origins"] == []
            assert load_server_config(tmp_path)["note"] == "keep"
            assert load_server_config(tmp_path)["bind"] == ["127.0.0.1"]
    finally:
        await runner.cleanup()
        sock.close()


@pytest.mark.skipif(not hasattr(socket, "SO_DONTROUTE"), reason="Linux direct-route socket guard")
@pytest.mark.parametrize("identity_result, expected", [
    ("match", None), ("wrong", "link_verification_failed"),
    ("close", "link_unavailable"), ("wrong-pin", "link_verification_failed"),
])
async def test_pinned_tls_alternate_sends_bearer_only_on_proven_socket(tmp_path, identity_result, expected):
    packets = []
    root = tmp_path / "tls"
    root.mkdir()
    server_config = tls.setup_local_ca(root, {"public_origins": ["https://localhost"], "bind": ["127.0.0.1"]})
    pin = _leaf_fingerprint(root, server_config)

    async def receive(request):
        operation = request.match_info["operation"]
        packets.append((operation, request.headers.get("Authorization"), request.transport))
        assert request.host == "localhost"
        if operation == "identity":
            assert await request.json() == {"instanceId": PEER}
            response = web.json_response({"result": {"instanceId": "f" * 32 if identity_result == "wrong" else PEER}})
            if identity_result == "close":
                response.force_close()
            return response
        assert await request.json() == payload
        return web.json_response({"result": {"instanceId": PEER, "generationId": GENERATION, "availability": "online"}})

    app = web.Application()
    app.router.add_post(PEER_PREFIX + "/{operation}", receive)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    runner = web.AppRunner(app)
    await runner.setup()
    payload = {"instanceId": PEER, "requesterId": "e" * 32, "grantId": GRANT}
    try:
        await web.SockSite(runner, sock, ssl_context=tls.ssl_context(root, server_config)).start()
        transport = DirectTransport()
        route = DirectRoute("https://localhost", "1" * 64 if identity_result == "wrong-pin" else pin)

        async def check():
            return await transport.discovered_status(route, {"address": "127.0.0.1", "port": port, "deviceId": None},
                                                     payload, lambda: "2" * 43)

        if expected:
            with pytest.raises(AppError) as refused:
                await check()
            assert refused.value.code == expected
            assert [(path, bearer) for path, bearer, _ in packets] == (
                [] if identity_result == "wrong-pin" else [("identity", None)])
        else:
            assert (await check())["instanceId"] == PEER
            assert [(path, bearer) for path, bearer, _ in packets] == [("identity", None), ("status", "Bearer ali_" + "2" * 43)]
            assert packets[0][2] is packets[1][2]
    finally:
        await runner.cleanup()
        sock.close()