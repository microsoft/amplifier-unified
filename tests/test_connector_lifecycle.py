import asyncio
import json
from pathlib import Path
import stat
import sys
from urllib.parse import parse_qs, urlsplit, urlencode, urlunsplit

import httpx
from multidict import MultiDict
import pytest

from test_smart_tools import manager, register

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from mcp_oauth_server import Fixture


@pytest.fixture
async def remote():
    fixture = await Fixture().start()
    yield fixture
    await fixture.close()


async def wait_for(check):
    for _ in range(500):
        result = check()
        if result:
            return result
        await asyncio.sleep(.01)
    raise AssertionError("Expected condition did not arrive")


async def configure_remote(manager, remote, **values):
    return await manager.configure({"id":"remote", "name":"Fixture account", "transport":"streamable-http", "url":remote.origin+"/mcp", **values})


async def authorize(manager, remote, decision="approve", *, bad_pkce=False, bad_issuer=False):
    await manager.execute("smartTools.authStart", {"id":"remote"})
    login = await wait_for(lambda: manager.oauth.status("remote") if manager.oauth.status("remote").get("phase") == "waiting" else None)
    async with httpx.AsyncClient(follow_redirects=False) as browser:
        url = login["url"]
        if bad_pkce:
            parts = urlsplit(url)
            query = parse_qs(parts.query)
            query["code_challenge"] = ["A" * 43]
            url = urlunsplit(parts._replace(query=urlencode(query, doseq=True)))
        first = await browser.get(url)
        assert first.status_code in (302, 303, 307)
        consent_url = first.headers["location"]
        consent = await browser.get(consent_url)
        assert "records:read" in consent.text
        reply = await browser.post(consent_url, data={"decision":decision})
    callback = urlsplit(reply.headers["location"])
    query = MultiDict((key, v) for key, values in parse_qs(callback.query).items() for v in values)
    if bad_issuer:
        query["iss"] = remote.origin + "/wrong-issuer"
    await manager.oauth.callback(query, f"{callback.scheme}://{callback.netloc}")
    return query


async def test_oauth_real_protocol_private_store_refresh_and_forget(manager, remote):
    await configure_remote(manager, remote, auth="oauth")
    with pytest.raises(ValueError, match="Sign in explicitly"):
        await manager.connect("remote")
    assert manager._server("remote")["connectionState"] == "auth-required"
    assert not remote.provider.clients  # no registration or browser flow on read/connect
    query = await authorize(manager, remote)
    await wait_for(lambda: manager.oauth.status("remote")["phase"] == "ready")
    server = manager._server("remote")
    assert server["connectionState"] == "ready"
    assert server["account"] == {"status":"unknown"}
    assert server["authorization"]["grantedScopes"] == ["records:read"]
    assert all("inputSchema" not in t for t in server["tools"])
    result = await manager.call_tool("remote", "read_record", {"key":"first"})
    assert result["structuredContent"]["key"] == "first"
    with pytest.raises(ValueError, match="invalid or expired"):
        await manager.oauth.callback(query, "http://127.0.0.1:8941")
    store = next((manager.root / "credentials").glob("*.json"))
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    stored = json.loads(store.read_text())
    for token in (stored["tokens"]["access_token"], stored["tokens"]["refresh_token"]):
        assert token not in json.dumps(manager.service.state)
        assert manager._redact(token) == "[redacted]"
    stored["expiresAt"] = 1
    store.write_text(json.dumps(stored))
    await manager.connect("remote", reconnect=True)
    assert remote.provider.exchanges == 1 and remote.provider.refreshes == 1
    assert remote.calls == 1
    await manager.disconnect("remote")
    assert store.exists()  # disconnect is not account revocation
    result = await manager.execute("smartTools.authForget", {"id":"remote"})
    assert result["remoteRevocation"] == "not-attempted" and not store.exists()


async def test_oauth_denial_cancel_and_callback_origin(manager, remote):
    await configure_remote(manager, remote, auth="oauth")
    with pytest.raises(ValueError, match="configured origin"):
        await manager.oauth.start("remote", "https://attacker.example")
    await authorize(manager, remote, "deny")
    await wait_for(lambda: manager.oauth.status("remote")["phase"] == "error")
    assert remote.provider.exchanges == 0
    await manager.oauth.start("remote")
    login = await wait_for(lambda: manager.oauth.status("remote") if manager.oauth.status("remote").get("url") else None)
    state = parse_qs(urlsplit(login["url"]).query)["state"][0]
    with pytest.raises(ValueError, match="invalid or expired"):
        await manager.oauth.callback(MultiDict(state=state, code="fixture"), "https://wrong.example")
    await manager.oauth.cancel("remote")
    assert manager.oauth.status("remote")["phase"] == "cancelled"
    assert manager._server("remote")["connectionState"] == "disconnected"
    with pytest.raises(ValueError, match="invalid or expired"):
        await manager.oauth.callback(MultiDict(state=state, code="fixture"), "http://127.0.0.1:8941")
    assert remote.provider.exchanges == 0 and not manager.connections


async def test_remote_environment_challenge_and_no_secret_in_state(manager, remote, monkeypatch):
    await configure_remote(manager, remote, headers={"Authorization":"FIXTURE_HEADER"})
    monkeypatch.delenv("FIXTURE_HEADER", raising=False)
    with pytest.raises(ValueError, match="not set"):
        await manager.connect("remote")
    assert manager._server("remote")["connectionState"] == "auth-required"
    monkeypatch.setenv("FIXTURE_HEADER", "Bearer invalid-fixture-token")
    with pytest.raises(ValueError, match="Authorization is required"):
        await manager.connect("remote")
    assert manager._server("remote")["connectionState"] == "auth-required"
    assert "invalid-fixture-token" not in json.dumps(manager.service.state)
    assert not remote.provider.clients


async def test_progressive_discovery_stale_notification_and_revision(manager, remote):
    await configure_remote(manager, remote, auth="oauth")
    await authorize(manager, remote)
    await wait_for(lambda: manager.oauth.status("remote")["phase"] == "ready")
    result = await manager.execute("smartTools.discover", {"id":"remote", "query":"record", "limit":1}, origin="agent")
    revision = result["catalogRevision"]
    assert [t["name"] for t in result["tools"]] == ["read_record"]
    assert "inputSchema" not in json.dumps(result)
    loaded = await manager.execute("smartTools.schemas", {"id":"remote", "names":["read_record"], "catalogRevision":revision}, origin="agent")
    assert loaded["tools"][0]["inputSchema"]["required"] == ["key"]
    await manager.call_tool("remote", "change_tools", {})
    await wait_for(lambda: manager._server("remote")["catalogState"] == "stale")
    assert manager._server("remote")["loadedSchemas"] == {}
    with pytest.raises(ValueError, match="stale"):
        await manager.call_tool("remote", "read_record", {"key":"bad"}, expected_catalog=revision)
    await manager.execute("smartTools.discover", {"id":"remote", "refresh":True})
    with pytest.raises(ValueError, match="stale"):
        await manager.call_tool("remote", "read_record", {"key":"bad"}, expected_catalog=revision)
    assert remote.calls == 0


async def test_schema_bounds_visibility_and_disconnect(manager, tmp_path):
    await register(manager, tmp_path)
    row = await manager.connect("board")
    args = {"id":"board", "catalogRevision":row["catalogRevision"]}
    with pytest.raises(ValueError, match="not available"):
        await manager.load_schemas({**args,"names":["view_only"]}, "agent")
    with pytest.raises(ValueError, match="one and five"):
        await manager.load_schemas({**args,"names":["board_set"] * 6}, "agent")
    manager.schemas["board"][0]["inputSchema"] = {"description":"x"*64_000}
    with pytest.raises(ValueError, match="too large"):
        await manager.load_schemas({**args,"names":[manager.schemas["board"][0]["name"]]}, "agent")
    await manager.disconnect("board")
    with pytest.raises(ValueError, match="Connect"):
        await manager.discover(args, "agent")


async def test_uninstall_refuses_declared_and_inferred_dependencies(manager, tmp_path):
    identity = "a"*16
    folder = manager.root / "installs" / identity
    folder.mkdir(parents=True)
    (folder / "owned").write_text("package")
    owned_work = tmp_path / "user-work"
    owned_work.write_text("preserve")
    manager.state["installations"] = [{"id":identity}]
    await register(manager, tmp_path, installationId=identity)
    with pytest.raises(ValueError, match="Remove these connection"):
        await manager.uninstall(identity)
    await manager.execute("smartTools.remove", {"id":"board"})
    await register(manager, tmp_path, args=[str(folder / "owned")])
    with pytest.raises(ValueError, match="Remove these connection"):
        await manager.uninstall(identity)
    await manager.execute("smartTools.remove", {"id":"board"})
    await manager.uninstall(identity)
    assert not folder.exists() and owned_work.read_text() == "preserve"


async def test_queued_call_is_rejected_when_catalog_changes_before_dispatch(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect("board")
    connection = manager.connections["board"]
    slow = asyncio.create_task(manager.call_tool("board", "slow", {}))
    await wait_for(lambda: (tmp_path / "board-value").exists())
    queued = asyncio.create_task(manager.call_tool("board", "board_set", {"value":"must not run"}))
    await wait_for(lambda: not connection.queue.empty())
    await connection._catalog_changed()
    await slow
    with pytest.raises(ValueError, match="catalog changed before execution"):
        await queued
    assert (tmp_path / "board-value").read_text() == "finished"


async def test_cancel_real_running_tool_records_interruption_without_replay(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect("board")
    task = asyncio.create_task(manager.command("smartTools.call", {"id":"board", "name":"slow"}, "cancel-real"))
    await wait_for(lambda: (tmp_path / "board-value").exists())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert manager.operation("cancel-real")["status"] == "interrupted"
    assert "not replayed" in manager.operation("cancel-real")["error"]
    assert manager._server("board")["connectionState"] == "disconnected"


async def test_callback_endpoint_is_state_bound_and_does_not_leak_codes(aiohttp_client, tmp_path):
    from amplifier_web.server import create_app
    from amplifier_web.mcp_oauth import SafeAccessLogger
    from test_service import Runtime
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(), voice=False, background_updates=False)
    client = await aiohttp_client(app)
    callback = await client.get("/oauth/mcp/callback?state=wrong&code=private-code", headers={
        "Sec-Fetch-Site":"cross-site", "Sec-Fetch-Mode":"navigate", "Sec-Fetch-Dest":"document"}, allow_redirects=False)
    assert callback.status == 400
    assert "private-code" not in await callback.text()
    assert callback.headers["Cache-Control"] == "no-store"
    assert callback.headers["Referrer-Policy"] == "no-referrer"
    assert (await client.get("/api/state")).status == 401
    request = type("Request", (), {"method":"GET", "path":"/oauth/mcp/callback", "version":type("Version", (), {"major":1,"minor":1})()})()
    assert SafeAccessLogger._format_r(request, None, 0) == "GET /oauth/mcp/callback HTTP/1.1"


async def test_reconfigure_cancels_authorization_and_removes_old_credentials(manager, remote):
    await configure_remote(manager, remote, auth="oauth")
    await authorize(manager, remote)
    await wait_for(lambda: manager.oauth.status("remote")["phase"] == "ready")
    stored = next((manager.root / "credentials").glob("*.json"))
    await configure_remote(manager, remote, name="Renamed fixture", auth="oauth")
    assert stored.exists()  # a display-name change is not an account switch
    assert manager._server("remote")["authorization"]["consent"] == "granted"
    await configure_remote(manager, remote, url=remote.origin + "/different", auth="oauth")
    assert not stored.exists()
    assert manager._server("remote")["connectionState"] == "disconnected"
    assert manager._server("remote")["loadedSchemas"] == {}
    assert manager.oauth.status("remote")["phase"] == "idle"


async def test_abrupt_stdio_exit_is_not_still_reported_ready(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect("board")
    with pytest.raises(Exception):
        await manager.call_tool("board", "exit_fixture", {})
    await wait_for(lambda: manager._server("board")["connectionState"] == "disconnected")
    assert manager._server("board")["catalogState"] == "stale"


@pytest.mark.parametrize("attack", ["bad_pkce", "bad_issuer"])
async def test_sdk_rejects_pkce_or_issuer_substitution(manager, remote, attack):
    await configure_remote(manager, remote, auth="oauth")
    await authorize(manager, remote, **{attack:True})
    await wait_for(lambda: manager.oauth.status("remote")["phase"] == "error")
    assert remote.provider.exchanges == 0
    assert manager._server("remote")["connectionState"] != "ready"
    assert not any("tokens" in json.loads(p.read_text()) for p in (manager.root / "credentials").glob("*.json"))


async def test_sdk_timeout_closes_connection_and_keeps_outcome_unknown(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect("board")
    with pytest.raises(ValueError, match="unconfirmed"):
        await manager.call_tool("board", "slow", {}, timeout_seconds=.03)
    await wait_for(lambda: manager._server("board")["connectionState"] == "disconnected")
    with pytest.raises(ValueError, match="Connect"):
        await manager.call_tool("board", "board_set", {"value":"no automatic retry"})


def test_sdk_sensitive_diagnostics_are_scoped_and_do_not_render_exception_values():
    import logging
    from amplifier_web.mcp_connection import PrivateDiagnostics, _private_diagnostics
    record = logging.LogRecord("mcp.client.auth.oauth2", logging.ERROR, __file__, 1,
        "Invalid token %s", ("private-token",), (ValueError, ValueError("private-token"), None))
    scope = _private_diagnostics.set(True)
    try:
        assert PrivateDiagnostics().filter(record)
    finally:
        _private_diagnostics.reset(scope)
    assert "private-token" not in logging.Formatter().format(record)
    assert record.exc_info is None


async def test_sdk_discovered_auth_endpoints_cannot_downgrade_credentials_to_remote_http():
    import httpx2
    from amplifier_web.mcp_connection import Connection
    connection = object.__new__(Connection)
    for url in ("http://remote.example/token", "https://user:password@remote.example/token"):
        with pytest.raises(ValueError, match="HTTPS"):
            await connection._request(httpx2.Request("POST", url))
    await connection._request(httpx2.Request("POST", "https://remote.example/token"))
    await connection._request(httpx2.Request("POST", "http://127.0.0.1:8989/token"))
