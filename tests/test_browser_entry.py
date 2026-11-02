"""No-listener protocol checks; native-only tests exercise real HTTPS separately."""
from argparse import Namespace
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlencode

import aiohttp
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
import pytest

from amplifier_web import __version__, auth, browser_entry as entry, cli, server
from amplifier_web.deployment import load_server_config

SID = "S-1-5-21-100-200-300-1001"
REVISION = "a" * 40
ORIGIN = "https://127.0.0.1:8941"


@pytest.fixture
def tmp_path(tmp_path, request):
    # Windows tests select a new private profile child, not the runner TEMP ACL.
    return request.getfixturevalue("native_root") if os.name == "nt" else tmp_path


def binding():
    return entry.Binding("b" * 32, "c" * 64, ORIGIN, SID)


def test_hash_only_binding_expiry_capacity_and_atomic_replay():
    now = [10]
    store = entry.GrantStore(capacity=1, clock=lambda: now[0])
    ticket = store.issue(binding(), "/safe?tab=one")
    assert list(store._grants) == [hashlib.sha256(ticket.encode()).digest()]
    assert ticket not in repr(store._grants)
    with pytest.raises(entry.BrowserEntryUnavailable, match="busy"):
        store.issue(binding())
    for key in ("instance_id", "data_identity", "owner_sid", "origin"):
        assert store.consume(ticket, replace(binding(), **{key: "wrong"})) is None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.consume(ticket, binding()), range(32)))
    assert results.count("/safe?tab=one") == 1 and results.count(None) == 31
    expired = store.issue(binding())
    assert entry.GrantStore().consume(expired, binding()) is None
    now[0] += 60
    assert store.consume(expired, binding()) is None
    assert store.consume(store.issue(binding(), "//attacker.example"), binding()) == "/"


@pytest.mark.parametrize("value", [None, "", "x" * 42, "x" * 44, "?" * 43, "é" * 43, ["x"]])
def test_malformed_tickets(value):
    assert entry.GrantStore().consume(value, binding()) is None


@pytest.mark.parametrize("options", [{"ttl": 61}, {"ttl": 0}, {"capacity": 0}, {"capacity": 129}])
def test_invalid_store_bounds(options):
    with pytest.raises(ValueError):
        entry.GrantStore(**options)


async def request(app, path, *, method="POST", headers=None, body=None, secure=True, remote=None):
    """Real aiohttp Request/body/parser, real middleware/handlers, no socket."""
    headers = {"Host": "127.0.0.1:8941", "Origin": ORIGIN, "Sec-Fetch-Site": "same-origin", **(headers or {})}
    headers = {key: value for key, value in headers.items() if value is not None}
    if isinstance(body, dict):
        raw = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    else:
        raw = (body or "").encode()
    headers["Content-Length"] = str(len(raw))
    protocol = Mock(_reading_paused=False)
    payload = aiohttp.StreamReader(protocol, 8192)
    payload.feed_data(raw)
    payload.feed_eof()
    def extra(name):
        if name in {"peername", "sockname"}:
            return ("192.0.2.20" if name == remote else "127.0.0.1", 8941)
    req = make_mocked_request(method, path, headers=headers, app=app, payload=payload,
                             transport=SimpleNamespace(get_extra_info=extra),
                             sslcontext=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER) if secure else None)
    match = await app.router.resolve(req)
    match.add_app(app)
    req._match_info = match
    async def authorized(req):
        return await auth.auth_required(req, match.handler)
    try:
        return await server.boundaries(req, authorized)
    except web.HTTPException as response:
        return response


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(entry, "is_windows", lambda: True)
    monkeypatch.setattr(entry, "current_owner_sid", lambda: SID)
    # Explicit candidate-only fixture selection, never a production gate change.
    monkeypatch.setattr(entry, "read_owner_control_token", lambda *a: "c" * 64)
    app = web.Application()
    app["control_token"], app["session_secret"] = "c" * 64, "s" * 64
    app["server_config"] = {"session_ttl_seconds": 120}
    app["allowed_origins"] = frozenset({ORIGIN, ORIGIN.replace("https:", "http:")})
    app["permitted_hosts"] = frozenset({"127.0.0.1", "localhost", "::1"})
    app["service"] = SimpleNamespace(data_dir=tmp_path, update_manager=SimpleNamespace(
        running_identity={"instanceId": "b" * 32, "version": __version__, "revision": REVISION}))
    entry.initialize(app)
    app.router.add_post(entry.ISSUE_PATH, entry.issue)
    app.router.add_post(entry.REDEEM_PATH, entry.redeem)
    app.router.add_get("/login", auth.login_page)
    app.router.add_post("/login", auth.post_login)
    async def protected(req):
        return web.json_response({"authenticated": True})
    app.router.add_get("/api/protected", protected)
    return app


def issue_body(app, **changes):
    return {"instanceId": "b" * 32, "version": __version__, "revision": REVISION,
            "dataIdentity": auth.data_identity(app["service"].data_dir), "ownerSid": SID, **changes}


async def issue(app, **changes):
    response = await request(app, entry.ISSUE_PATH, headers={"Authorization": "Bearer " + app["control_token"]},
                             body=issue_body(app, **changes))
    assert response.status == 200
    assert response.headers["Cache-Control"] == "no-store"
    return json.loads(response.body)["ticket"]


async def redeem(app, ticket, *, csrf=None, headers=None, body=None, **kwargs):
    csrf = csrf or auth.new_csrf(app["session_secret"])
    return await request(app, entry.REDEEM_PATH, headers={
        "Content-Type": "application/x-www-form-urlencoded", "Cookie": f"{auth.CSRF_COOKIE}={csrf}",
        **(headers or {})}, body=body or urlencode({"csrf": csrf, "ticket": ticket}), **kwargs)


async def test_grant_cookie_protects_actual_auth_middleware_and_replay(owner):
    assert (await request(owner, "/api/protected", method="GET")).status == 401
    ticket = await issue(owner, next="/safe?tab=one")
    results = await asyncio.gather(*(redeem(owner, ticket) for _ in range(8)))
    assert sorted(r.status for r in results) == [303] + [403] * 7
    response = next(r for r in results if r.status == 303)
    assert response.headers["Location"] == "/safe?tab=one"
    cookie = response.cookies[auth.SESSION_COOKIE]
    assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Strict" and cookie["path"] == "/"
    assert (await request(owner, "/api/protected", method="GET", headers={
        "Cookie": f"{auth.SESSION_COOKIE}={cookie.value}"})).status == 200
    assert (await redeem(owner, ticket)).status == 403


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"},
    {"Authorization": "Bearer amt_" + "x" * 64}, {"Authorization": "Bearer amc_" + "x" * 64},
    {"Cookie": f"{auth.SESSION_COOKIE}=" + auth.new_session("s" * 64)}])
async def test_issue_requires_explicit_control_not_session_or_device(owner, headers):
    assert (await request(owner, entry.ISSUE_PATH, headers=headers, body=issue_body(owner))).status == 403
    assert not owner["browser_entry"]._grants


@pytest.mark.parametrize("key", ["instanceId", "version", "revision", "dataIdentity", "ownerSid"])
async def test_wrong_health_identity_never_issues(owner, key):
    response = await request(owner, entry.ISSUE_PATH, headers={"Authorization": "Bearer " + owner["control_token"]},
                             body=issue_body(owner, **{key: "wrong"}))
    assert response.status == 409 and not owner["browser_entry"]._grants


@pytest.mark.parametrize("local,submitted,drop,expected", [
    (None, None, None, 200),
    (None, REVISION, None, 409),
    (REVISION, None, None, 409),
    (REVISION, "f" * 40, None, 409),
    (None, None, "revision", 409),
    (None, None, "version", 409),
    ("", "", None, 503),
])
async def test_issue_wheel_identity_is_explicit_not_a_provenance_fallback(owner, local, submitted, drop, expected):
    owner["service"].update_manager.running_identity["revision"] = local
    body = issue_body(owner, revision=submitted)
    if drop:
        del body[drop]
    response = await request(owner, entry.ISSUE_PATH,
                             headers={"Authorization": "Bearer " + owner["control_token"]}, body=body)
    assert response.status == expected
    assert len(owner["browser_entry"]._grants) == (1 if expected == 200 else 0)


@pytest.mark.parametrize("identity", [
    None, {}, {"version": __version__}, {"version": "wrong", "revision": None},
    *({"version": __version__, "revision": bad} for bad in ("", False, 0, [], "x" * 40, "a" * 39, "A" * 40)),
])
def test_package_identity_does_not_invent_or_normalize_source(identity):
    assert entry._package_identity(identity) is None


@pytest.mark.parametrize("headers", [{"Origin": "https://attacker.example"}, {"Origin": "null"},
    {"Origin": None}, {"Host": "attacker.example"}, {"Sec-Fetch-Site": "cross-site"}])
async def test_cross_origin_host_csrf_rejection_preserves_ticket(owner, headers):
    ticket = await issue(owner)
    assert (await redeem(owner, ticket, headers=headers)).status == 403
    assert (await redeem(owner, ticket)).status == 303


@pytest.mark.parametrize("remote", ["peername", "sockname"])
async def test_both_real_socket_ends_must_be_local(owner, remote):
    ticket = await issue(owner)
    assert (await redeem(owner, ticket, remote=remote)).status == 403
    assert (await redeem(owner, ticket)).status == 303


async def test_http_bad_csrf_duplicate_form_and_oversize(owner):
    ticket = await issue(owner)
    assert (await redeem(owner, ticket, secure=False, headers={"Origin": ORIGIN.replace("https:", "http:")})).status == 403
    assert (await redeem(owner, ticket, body=urlencode({"csrf": "wrong", "ticket": ticket}))).status == 403
    csrf = auth.new_csrf(owner["session_secret"])
    body = urlencode({"csrf": csrf, "ticket": ticket}) + "&ticket=" + ticket
    assert (await redeem(owner, ticket, csrf=csrf, body=body)).status == 403
    assert (await redeem(owner, ticket, body="csrf=" + "x" * 4096 + "&ticket=" + ticket)).status == 413
    response = await request(owner, entry.ISSUE_PATH, headers={"Authorization": "Bearer " + owner["control_token"]},
                             body=issue_body(owner, next="/" + "x" * 4096))
    assert response.status == 413
    assert (await redeem(owner, ticket)).status == 303


async def test_expiry_capacity_and_restart_owner_root_binding(owner, monkeypatch, tmp_path):
    now = [0]
    owner["browser_entry"].clock = lambda: now[0]
    owner["browser_entry"].capacity = 1
    ticket = await issue(owner)
    assert (await request(owner, entry.ISSUE_PATH, headers={"Authorization": "Bearer " + owner["control_token"]},
                          body=issue_body(owner))).status == 503
    monkeypatch.setattr(entry, "current_owner_sid", lambda: "S-1-5-21-999")
    assert (await redeem(owner, ticket)).status == 403
    monkeypatch.setattr(entry, "current_owner_sid", lambda: SID)
    original = owner["service"].data_dir
    owner["service"].data_dir = tmp_path / "other"
    assert (await redeem(owner, ticket)).status == 403
    owner["service"].data_dir = original
    owner["service"].update_manager.running_identity["instanceId"] = "e" * 32
    assert (await redeem(owner, ticket)).status == 403
    owner["service"].update_manager.running_identity["instanceId"] = "b" * 32
    now[0] = 60
    assert (await redeem(owner, ticket)).status == 403


async def test_windows_page_no_password_and_posix_login_preserved(owner, monkeypatch):
    response = await request(owner, "/login", method="GET")
    assert 'src="/browser-entry.js"' in response.text and 'name="ticket"' in response.text
    assert "unsafe-inline" not in response.headers["Content-Security-Policy"]
    assert "form-action 'self'" in response.headers["Content-Security-Policy"]
    monkeypatch.setattr(auth, "authenticate_pam", lambda *a: pytest.fail("Windows PAM"))
    csrf = response.cookies[auth.CSRF_COOKIE].value
    assert (await request(owner, "/login", body=urlencode({"csrf": csrf}), headers={
        "Content-Type": "application/x-www-form-urlencoded", "Cookie": f"{auth.CSRF_COOKIE}={csrf}"})).status == 403
    monkeypatch.setattr(entry, "is_windows", lambda: False)
    monkeypatch.setattr(auth, "authenticate_pam", lambda u, p: (u, p) == ("owner", "synthetic"))
    response = await request(owner, "/login", body=urlencode({"csrf": csrf, "username": "owner", "password": "synthetic"}),
                             headers={"Content-Type": "application/x-www-form-urlencoded", "Cookie": f"{auth.CSRF_COOKIE}={csrf}"})
    assert response.status == 303 and response.cookies[auth.SESSION_COOKIE]["httponly"]


async def test_real_app_routes_and_static_parity_without_listener(tmp_path, monkeypatch):
    from test_service import Runtime
    app = await server.create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                                  background_updates=False, background_work=False, preload_providers=False)
    try:
        assert app["browser_entry"] is None
        assert (await request(app, entry.ISSUE_PATH, body={})).status == 403
        assert (await request(app, entry.REDEEM_PATH, body={})).status == 403
        monkeypatch.setattr(entry, "is_windows", lambda: True)
        assert 'src="/browser-entry.js"' in (await request(app, "/login", method="GET")).text
        response = await request(app, "/browser-entry.js", method="GET")
        assert response.status == 200
        root = Path(entry.__file__).parent.parent
        for name in ("browser-entry.js", "windows-login.html"):
            assert (root / "frontend/public" / name).read_bytes() == (root / "amplifier_web/static" / name).read_bytes()
    finally:
        await app["service"].close()


async def test_http_no_pin_or_unsafe_pin_precedes_credential_read(tmp_path, monkeypatch):
    monkeypatch.setattr(entry, "read_owner_control_token", lambda *a: pytest.fail("unverified credential read"))
    with pytest.raises(entry.BrowserEntryUnavailable, match="leaf-pinned HTTPS"):
        await entry.owner_browser_url(tmp_path, load_server_config(tmp_path))
    for pin in (None, False, True):
        monkeypatch.setattr(entry, "_owner_probe_targets", lambda *a: ([ORIGIN + "/api/health"], pin))
        with pytest.raises(entry.BrowserEntryUnavailable, match="exact SHA-256"):
            await entry.owner_browser_url(tmp_path, {})


async def test_launcher_unknown_post_is_not_replayed(tmp_path, monkeypatch):
    from amplifier_web import __version__, update_readiness
    pin = aiohttp.Fingerprint(bytes(range(32)))
    monkeypatch.setattr(entry, "_owner_probe_targets", lambda *a: ([ORIGIN + "/api/health", "https://[::1]:8941/api/health"], pin))
    monkeypatch.setattr(entry, "current_owner_sid", lambda: SID)
    monkeypatch.setattr(entry, "read_owner_control_token", lambda *a: "c" * 64)
    monkeypatch.setattr(update_readiness, "running_identity", lambda: {"version": __version__, "revision": REVISION})
    calls = []
    class Response:
        status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def json(self):
            return {"ok": True, "app": "amplifier-unified", "version": __version__, "revision": REVISION,
                    "instanceId": "b" * 32, "dataIdentity": auth.data_identity(tmp_path)}
    class Client(Response):
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
        def get(self, url, **kwargs):
            calls.append(("get", url))
            assert kwargs["ssl"] is pin and kwargs["allow_redirects"] is False
            assert kwargs["headers"]["Authorization"] == "Bearer " + "c" * 64
            return Response()
        def post(self, url, **kwargs):
            calls.append(("post", url))
            assert kwargs["ssl"] is pin and kwargs["allow_redirects"] is False
            assert kwargs["headers"]["Origin"] == ORIGIN
            raise TimeoutError("Unknown POST; never retry")
    monkeypatch.setattr(aiohttp, "ClientSession", Client)
    with pytest.raises(entry.BrowserEntryUnavailable, match="no retry"):
        await entry.owner_browser_url(tmp_path, {})
    assert calls == [("get", ORIGIN + "/api/health"), ("post", ORIGIN + entry.ISSUE_PATH)]


@pytest.mark.parametrize("local,remote,change", [
    (None, REVISION, {}),
    (REVISION, None, {}),
    (REVISION, "f" * 40, {}),
    (None, None, {"version": "0.0.0"}),
    (None, None, {"drop": "revision"}),
    (None, None, {"drop": "version"}),
    (None, None, {"revision": ""}),
    (None, None, {"revision": False}),
    (None, None, {"instanceId": "not-an-instance"}),
    (None, None, {"dataIdentity": "wrong"}),
])
async def test_launcher_package_mismatch_never_issues(tmp_path, monkeypatch, local, remote, change):
    from amplifier_web import update_readiness
    pin = aiohttp.Fingerprint(bytes(range(32)))
    monkeypatch.setattr(entry, "_owner_probe_targets", lambda *a: ([ORIGIN + "/api/health"], pin))
    monkeypatch.setattr(entry, "current_owner_sid", lambda: SID)
    monkeypatch.setattr(entry, "read_owner_control_token", lambda *a: "c" * 64)
    monkeypatch.setattr(update_readiness, "running_identity", lambda: {"version": __version__, "revision": local})
    health = {"ok": True, "app": "amplifier-unified", "version": __version__, "revision": remote,
              "instanceId": "b" * 32, "dataIdentity": auth.data_identity(tmp_path)}
    health.update({key: value for key, value in change.items() if key != "drop"})
    if "drop" in change:
        del health[change["drop"]]
    calls = []
    class Response:
        status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def json(self):
            return health
    class Client(Response):
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
        def get(self, url, **kwargs):
            calls.append("get")
            assert kwargs["ssl"] is pin and kwargs["allow_redirects"] is False
            return Response()
        def post(self, *a, **kw):
            pytest.fail("Mismatched package/host identity must never issue")
    monkeypatch.setattr(aiohttp, "ClientSession", Client)
    with pytest.raises(entry.BrowserEntryUnavailable, match="identity"):
        await entry.owner_browser_url(tmp_path, {})
    assert calls == ["get"]


@pytest.mark.parametrize("source", [
    {"url": "https://example.test/source.git", "vcs_info": {"vcs": "git", "commit_id": REVISION}},
    {"url": "file:///candidate.whl", "archive_info": {}},
    {"url": "file:///candidate.whl", "archive_info": {"hashes": {"sha256": "c" * 64}}},
])
async def test_launcher_success_uses_real_app_health_issue_cookie_in_memory(tmp_path, monkeypatch, source):
    from amplifier_web import __version__, update_readiness
    from test_service import Runtime
    monkeypatch.setattr(entry, "is_windows", lambda: True)
    monkeypatch.setattr(entry, "current_owner_sid", lambda: SID)
    monkeypatch.setattr(entry, "read_owner_control_token",
                        lambda home, sid: (home / "config/auth/control-token").read_text().strip())
    # Exercise the real canonical metadata reader. A wheel has no VCS revision;
    # do not fabricate one in either the host or launcher to make this pass.
    monkeypatch.setattr(update_readiness.metadata, "distribution", lambda name: SimpleNamespace(
        locate_file=lambda path: Path(entry.__file__).parent,
        read_text=lambda name: json.dumps(source)))
    app = await server.create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                                  background_updates=False, background_work=False, preload_providers=False)
    identity = app["service"].update_manager.running_identity
    assert identity["revision"] == source.get("vcs_info", {}).get("commit_id")
    assert identity["version"] == __version__
    pin = aiohttp.Fingerprint(bytes(range(32)))
    monkeypatch.setattr(entry, "_owner_probe_targets", lambda *a: ([ORIGIN + "/api/health"], pin))
    calls = []
    class Response:
        async def __aenter__(self):
            self.response = await self.operation()
            self.status = self.response.status
            return self
        async def __aexit__(self, *a):
            pass
        async def json(self):
            return json.loads(self.response.body)
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        def operation(self, method, url, kwargs):
            assert kwargs["ssl"] is pin and kwargs["allow_redirects"] is False
            calls.append((method, url))
            response = Response()
            response.operation = lambda: request(app, url.removeprefix(ORIGIN), method=method,
                                                  headers=kwargs["headers"], body=kwargs.get("json"))
            return response
        def get(self, url, **kwargs):
            return self.operation("GET", url, kwargs)
        def post(self, url, **kwargs):
            assert kwargs["json"]["revision"] == identity["revision"]
            assert kwargs["json"]["version"] == __version__
            return self.operation("POST", url, kwargs)
    monkeypatch.setattr(aiohttp, "ClientSession", Client)
    try:
        url = await entry.owner_browser_url(tmp_path, {})
        assert url.startswith(ORIGIN + "/login#ticket=") and app["control_token"] not in url
        response = await redeem(app, url.split("#ticket=")[1])
        assert response.status == 303 and response.cookies[auth.SESSION_COOKIE]["secure"]
        assert calls == [("GET", ORIGIN + "/api/health"), ("POST", ORIGIN + entry.ISSUE_PATH)]
    finally:
        await app["service"].close()


@pytest.mark.parametrize("no_open", [True, False])
def test_foreground_opens_only_after_all_sockets_bound(tmp_path, monkeypatch, no_open):
    monkeypatch.setattr(entry, "is_windows", lambda: True)
    monkeypatch.setattr("amplifier_web.settings_migration.migrate_settings", lambda *a: None)
    monkeypatch.setattr("amplifier_web.host.config.load_config", lambda *a: None)
    monkeypatch.setattr(server, "create_app", lambda *a, **kw: object())
    calls = []
    monkeypatch.setattr(cli, "_open_browser_safely", lambda *a: calls.append("open"))
    class Timer:
        def __init__(self, delay, callback):
            assert calls == ["listening"]
            self.callback = callback
        def start(self):
            self.callback()
        def cancel(self):
            pass
    monkeypatch.setattr(cli.threading, "Timer", Timer)
    def run(app, **kwargs):
        assert calls == []
        calls.append("listening")
        kwargs["print"]("sockets bound")
    monkeypatch.setattr(web, "run_app", run)
    args = Namespace(port=None, public_origin=None, session_ttl_seconds=None, bind=None, host=None,
                     tls_cert=None, tls_key=None, workspace=str(tmp_path), no_open=no_open)
    cli._serve_unlocked(args, tmp_path)
    assert calls == (["listening"] if no_open else ["listening", "open"])


async def test_expired_csrf_preserves_grant(owner):
    ticket = await issue(owner)
    csrf = auth._signed(owner["session_secret"], {"kind": "csrf", "issued": 1})
    assert (await redeem(owner, ticket, csrf=csrf)).status == 403
    assert (await redeem(owner, ticket)).status == 303


def test_private_tls_read_failure_precedes_control_and_generic_probe(tmp_path, monkeypatch):
    from amplifier_web import windows_files
    def refuse(*a, **kw):
        raise PermissionError("unsafe private TLS storage")
    monkeypatch.setattr(windows_files, "directory", refuse)
    config = {"tls": {"method": "ca", "cert": "config/tls/leaf.crt", "key": "config/tls/leaf.key"},
              "bind": ["127.0.0.1"], "port": 8941}
    with pytest.raises(entry.BrowserEntryUnavailable, match="private SHA-256 TLS leaf pin"):
        entry._owner_probe_targets(tmp_path, config)


@pytest.mark.parametrize("no_open", [True, False])
def test_explicit_cli_open_no_secret_output(tmp_path, monkeypatch, capsys, no_open):
    monkeypatch.setattr(entry, "is_windows", lambda: True)
    calls = []
    url = ORIGIN + "/login#ticket=" + "a" * 43
    async def grant(*a):
        calls.append("issue")
        return url
    monkeypatch.setattr(entry, "owner_browser_url", grant)
    monkeypatch.setattr(cli.webbrowser, "open", lambda value: calls.append(value) or True)
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "--data-dir", str(tmp_path),
                                    *(["--no-open"] if no_open else []), "open"])
    cli.main()
    assert calls == ([] if no_open else ["issue", url])
    captured = capsys.readouterr()
    assert "ticket=" not in captured.out + captured.err


def test_backend_exception_never_displays_grant(tmp_path, monkeypatch):
    monkeypatch.setattr(entry, "is_windows", lambda: True)
    async def grant(*a):
        return ORIGIN + "/login#ticket=" + "a" * 43
    monkeypatch.setattr(entry, "owner_browser_url", grant)
    def broken(value):
        raise OSError(value)
    monkeypatch.setattr(cli.webbrowser, "open", broken)
    with pytest.raises(entry.BrowserEntryUnavailable) as error:
        cli._open_browser(Namespace(no_open=False), tmp_path, {})
    assert "ticket=" not in str(error.value)


def test_shipped_script_strips_fragment_first_and_requires_https():
    source = Path(entry.__file__).parent / "static/browser-entry.js"
    harness = r'''
const fs = require("node:fs"), vm = require("node:vm");
const source = fs.readFileSync(process.argv[1], "utf8");
for (const [origin, action, valid] of [
  ["https://127.0.0.1:8941", "https://127.0.0.1:8941/api/browser-entry/redeem", true],
  ["http://127.0.0.1:8941", "http://127.0.0.1:8941/api/browser-entry/redeem", false],
  ["https://127.0.0.1:8941", "https://attacker.example/api/browser-entry/redeem", false],
  ["https://remote.example", "https://remote.example/api/browser-entry/redeem", false],
]) {
  const effects = [], url = new URL(origin + "/login#ticket=" + "a".repeat(43));
  const form = {action, elements:{ticket:{value:""}}, submit:()=>effects.push("submit")};
  vm.runInNewContext(source, {URL, URLSearchParams,
    window:{location:url, history:{replaceState:(_, __, value)=>{
      if(value.includes("ticket")) throw Error("fragment retained"); effects.push("strip");
    }}}, document:{getElementById:id=>id==="browser-entry"?form:{textContent:""}},
  });
  if(JSON.stringify(effects)!==JSON.stringify(valid?["strip","submit"]:["strip"])) throw Error(effects);
}
'''
    subprocess.run(["node", "-e", harness, str(source)], check=True, capture_output=True, timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows HTTPS/private-storage combined boundary")
async def test_native_actual_app_tls_grant_cookie_and_replay(native_root, monkeypatch, aiohttp_client, unused_tcp_port):
    """Candidate-only selection. This does not remove the production activation gate."""
    from aiohttp.test_utils import TestServer
    from amplifier_web import tls, windows_files
    from test_service import Runtime
    home = native_root / "host"
    for name in ("host", "workspace", "shared", "amplifier", "terminal"):
        with windows_files.directory(native_root / name, owned_root=native_root, create=True):
            pass
    for name, folder in (("AMPLIFIER_WEB_HOME", "host"), ("AMPLIFIER_HOME", "amplifier"),
                         ("AMPLIFIER_SESSION_STATE_HOME", "shared"), ("AMPLIFIER_TERMINAL_HOME", "terminal")):
        monkeypatch.setenv(name, str(native_root / folder))
    config = load_server_config(home, overrides={"port": unused_tcp_port, "bind": ["127.0.0.1"]})
    config = tls.setup_local_ca(home, config)
    monkeypatch.setattr(entry, "read_owner_control_token", entry._read_private_owner_control_token)
    app = await server.create_app(home, workspace=native_root / "workspace", runtime=Runtime(), voice=False,
                                  background_updates=False, background_work=False, preload_providers=False, server_config=config)
    listener = TestServer(app, port=unused_tcp_port)
    await listener.start_server(ssl=tls.ssl_context(home, config))
    _, pin = entry._owner_probe_targets(home, config)
    client = await aiohttp_client(listener, connector=aiohttp.TCPConnector(ssl=pin))
    url = await entry.owner_browser_url(home, config)
    assert app["control_token"] not in url
    response = await client.get("/login")
    csrf = response.cookies[auth.CSRF_COOKIE].value
    ticket = url.split("#ticket=")[1]
    headers = {"Origin": f"https://127.0.0.1:{unused_tcp_port}", "Cookie": f"{auth.CSRF_COOKIE}={csrf}"}
    response = await client.post(entry.REDEEM_PATH, data={"csrf": csrf, "ticket": ticket}, headers=headers, allow_redirects=False)
    assert response.status == 303 and response.cookies[auth.SESSION_COOKIE]["secure"]
    assert response.cookies[auth.SESSION_COOKIE]["httponly"]
    cookie = response.cookies[auth.SESSION_COOKIE].value
    response = await client.get("/", headers={"Cookie": f"{auth.SESSION_COOKIE}={cookie}"}, allow_redirects=False)
    assert response.status == 200
    assert (await client.post(entry.REDEEM_PATH, data={"csrf": csrf, "ticket": ticket}, headers=headers, allow_redirects=False)).status == 403


@pytest.mark.skipif(os.name != "nt", reason="Native Windows TLS listener-confusion controls")
@pytest.mark.parametrize("mode", ["wrong-leaf", "redirect"])
async def test_native_unrelated_listener_and_proxy_never_receive_bearer(
        native_root, monkeypatch, aiohttp_client, aiohttp_server, unused_tcp_port, mode):
    from aiohttp.test_utils import TestServer
    from amplifier_web import tls, update_readiness, windows_files
    home, other = native_root / "owner", native_root / "other"
    for path in (home, other):
        with windows_files.directory(path, owned_root=native_root, create=True):
            pass
    config = load_server_config(home, overrides={"port": unused_tcp_port, "bind": ["127.0.0.1"]})
    config = tls.setup_local_ca(home, config)
    auth.session_secret(home)
    token = auth.control_token(home)
    monkeypatch.setattr(entry, "read_owner_control_token", entry._read_private_owner_control_token)
    monkeypatch.setattr(update_readiness, "running_identity", lambda: {"version": __version__, "revision": REVISION})
    received, trusted_received = [], []
    async def catcher(req):
        received.append(req.headers.get("Authorization"))
        return web.json_response({"ok": True, "app": "unrelated"})
    sink = web.Application()
    sink.router.add_route("*", "/{path:.*}", catcher)
    target = await aiohttp_server(sink)
    async def redirect(req):
        trusted_received.append(req.headers.get("Authorization"))
        raise web.HTTPFound(str(target.make_url("/api/health")))
    listener_app = web.Application()
    listener_app.router.add_get("/api/health", catcher if mode == "wrong-leaf" else redirect)
    if mode == "wrong-leaf":
        other_config = tls.setup_local_ca(other, load_server_config(other))
        context = tls.ssl_context(other, other_config)
    else:
        context = tls.ssl_context(home, config)
    listener = TestServer(listener_app, port=unused_tcp_port)
    await listener.start_server(ssl=context)
    await aiohttp_client(listener)  # cleanup only, never send an unpinned request
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, str(target.make_url("/")))
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    with pytest.raises(entry.BrowserEntryUnavailable, match="identity"):
        await entry.owner_browser_url(home, config)
    assert received == []
    assert trusted_received == (["Bearer " + token] if mode == "redirect" else [])


# Share only the native fixture definition, not any test's production overrides.
from test_windows_auth import native_root  # noqa: E402,F401
