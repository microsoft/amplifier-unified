"""Single-owner host login, with a disposable peer for the existing bridge protocol."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

from aiohttp import web
import pytest

from amplifier_web import auth, host_pam
from amplifier_web.deployment import validate_server, setting_set

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Unix socket login is opt-in on Unix only")


@pytest.fixture
async def bridge():
    # Linux Unix socket paths are limited to 108 bytes; pytest's case directory
    # names can exceed that before the socket filename is appended.
    with tempfile.TemporaryDirectory(prefix="unified-pam-") as root:
        path = Path(root) / "owner.sock"
        state = SimpleNamespace(requests=[], reply=b"OK\n", delay=0, tasks=set())

        async def connection(reader, writer):
            state.tasks.add(asyncio.current_task())
            try:
                state.requests.append(json.loads(await reader.readline()))
                await asyncio.sleep(state.delay)
                writer.write(state.reply)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                state.tasks.discard(asyncio.current_task())

        server = await asyncio.start_unix_server(connection, path)
        path.chmod(0o600)
        try:
            yield {"owner": "host-owner", "socket": str(path)}, state
        finally:
            server.close()
            await server.wait_closed()
            for task in tuple(state.tasks):
                task.cancel()
            await asyncio.gather(*state.tasks, return_exceptions=True)


async def test_one_host_owner_and_exact_password_reach_the_bridge(bridge):
    config, state = bridge
    password = "synthetic café\n\"password\""
    assert await host_pam.authenticate(config, "host-owner", password)
    assert state.requests == [{"username": "host-owner", "password": password}]
    assert not await host_pam.authenticate(config, "guest-service", password)
    assert not await host_pam.authenticate(config, "host-owner", "")
    assert not await host_pam.authenticate(config, "host-owner", "x" * 1025)
    assert len(state.requests) == 1


@pytest.mark.parametrize("reply", [b"NO\n", b"", b"OK", b"OK\nextra", b"x" * 80 + b"\n"])
async def test_rejection_and_malformed_responses_fail_closed(bridge, reply):
    config, state = bridge
    state.reply = reply
    assert not await host_pam.authenticate(config, "host-owner", "synthetic")


async def test_slow_bridge_is_bounded_and_does_not_block_the_event_loop(bridge, monkeypatch):
    config, state = bridge
    state.delay = 1
    monkeypatch.setattr(host_pam, "TIMEOUT_SECONDS", 0.02)
    attempt = asyncio.create_task(host_pam.authenticate(config, "host-owner", "synthetic"))
    await asyncio.sleep(0.005)
    assert not attempt.done()
    assert not await attempt


async def test_missing_unshared_and_symlink_sockets_fail_closed(bridge, tmp_path):
    config, state = bridge
    assert not await host_pam.authenticate({**config, "socket": str(tmp_path / "absent")}, "host-owner", "synthetic")
    path = Path(config["socket"])
    path.chmod(0o666)
    assert not await host_pam.authenticate(config, "host-owner", "synthetic")
    path.chmod(0o600)
    alias = path.with_name("alias")
    alias.symlink_to(path)
    assert not await host_pam.authenticate({**config, "socket": str(alias)}, "host-owner", "synthetic")
    assert state.requests == []


def test_opt_in_configuration_is_atomic_and_defaults_remain_local():
    config = validate_server({})
    assert config["host_pam"] is None
    selected = setting_set(config, "host_pam", {"owner": "host-owner", "socket": "/run/private/pam.sock"})
    assert selected["host_pam"]["owner"] == "host-owner"
    assert config["host_pam"] is None
    assert setting_set(selected, "host_pam", None)["host_pam"] is None
    for invalid in ({}, {"owner": "", "socket": "/socket"},
                    {"owner": "user", "socket": "relative"},
                    {"owner": "user", "socket": "/socket", "url": "https://peer"},
                    {"owner": "user\n", "socket": "/socket"}):
        with pytest.raises(ValueError, match="host_pam"):
            setting_set(config, "host_pam", invalid)


async def test_normal_login_keeps_csrf_cookie_and_private_route_boundary(aiohttp_client, bridge, monkeypatch):
    config, state = bridge
    app = web.Application(middlewares=[auth.auth_required])
    app["server_config"] = validate_server({"host_pam": config})
    app["session_secret"] = "synthetic-session-key"
    app["allowed_origins"] = set()
    app["control_token"] = "synthetic-control"
    app["service"] = SimpleNamespace(data_dir=Path("/synthetic"))
    app.router.add_post("/login", auth.post_login)
    async def private(_):
        return web.Response(text="signed in")
    app.router.add_get("/", private)
    def no_guest_fallback(*_):
        pytest.fail("Selected host login must not fall back to guest PAM")
    monkeypatch.setattr(auth, "authenticate_pam", no_guest_fallback)
    client = await aiohttp_client(app)
    form = {"username": "host-owner", "password": "synthetic"}
    assert (await client.post("/login", data=form)).status == 403
    csrf = auth.new_csrf(app["session_secret"])
    headers = {"Cookie": f"{auth.CSRF_COOKIE}={csrf}"}
    form["csrf"] = csrf
    rejected = await client.post("/login", data=form, headers={**headers, "Origin": "https://wrong.example"})
    assert rejected.status == 403 and state.requests == []
    state.reply = b"NO\n"
    rejected = await client.post("/login", data=form, headers=headers, allow_redirects=False)
    assert rejected.status == 303 and "error=1" in rejected.headers["Location"]
    assert auth.SESSION_COOKIE not in rejected.cookies
    state.reply = b"OK\n"
    accepted = await client.post("/login", data=form, headers=headers, allow_redirects=False)
    assert accepted.status == 303 and accepted.headers["Location"] == "/"
    cookie = accepted.cookies[auth.SESSION_COOKIE]
    assert cookie["httponly"] and cookie["samesite"] == "Strict"
    assert auth._verified(app["session_secret"], cookie.value, 60, "session")
    assert (await client.get("/", headers={"Cookie": f"{auth.SESSION_COOKIE}={cookie.value}"})).status == 200
