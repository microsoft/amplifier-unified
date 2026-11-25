"""Sign-in by typed code or saved key for a browser on another device (Windows host).

No listener: real aiohttp requests through the real authentication middleware
and the real handlers. Synchronous on purpose: the focused Windows job installs
no async pytest plugin, and it must not need the full server module.
The private store is a small model here, not native storage qualification.
"""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlencode
import uuid

import aiohttp
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
import pytest

from amplifier_web import __version__, auth, browser_entry as entry, cli

SID = "S-1-5-21-100-200-300-1001"
REVISION = "a" * 40
TOKEN, SECRET = "c" * 64, "s" * 64
LOCAL = "https://127.0.0.1:8941"
REMOTE = "https://pc.tail1234.ts.net:8941"   # The Tailscale name of the PC.
OTHER = "https://192.168.1.20:8941"          # A second configured address.
PASSWORD = "correct horse battery"           # The owner's own text, set in the app on the PC.
PLAIN = "http://plain.example:8941"          # Configured, but not HTTPS.
UNLISTED = "https://rebind.example:8941"     # Passes the middleware; not a configured address.
BEARER = {"Authorization": "Bearer " + TOKEN}
FORM = "application/x-www-form-urlencoded"


@pytest.fixture
def home(tmp_path_factory):
    # Native Windows refuses a runner temp folder with a broad inherited ACL;
    # use a private profile child there instead of repairing or skipping.
    if os.name != "nt":
        yield tmp_path_factory.mktemp("home")
        return
    from amplifier_web.windows_files import address, directory
    root = address(Path.home() / (".unified-code-test-" + uuid.uuid4().hex))
    with directory(root, owned_root=root, create=True):
        pass
    try:
        yield root
    finally:
        shutil.rmtree(root)


class PrivateFiles:
    """The two existing owner credentials behind the private-directory seam.

    The sign-out record and the record of the saved sign-in key are not
    models. On native Windows they go through this seam to the real private
    store (`native`); the other systems do not use the seam for them.
    """

    def __init__(self, native=None):
        self.events, self.native = [], native

    @contextmanager
    def directory(self, path, *, owned_root, create=False):
        assert Path(path).is_relative_to(owned_root)
        files = self

        class Pinned:
            def read_private(self, name, *, limit):
                files.events.append(name)
                if name in {"session-epoch", "sign-in-key"} and files.native is not None:
                    with files.native(path, owned_root=owned_root) as real:
                        return real.read_private(name, limit=limit)
                if Path(path).parts[-2:] != ("config", "auth") or name not in {"session-secret", "control-token"}:
                    raise FileNotFoundError(name)
                return (SECRET if name == "session-secret" else TOKEN).encode()

            def publish_private(self, name, content, *, replace=False):
                assert name in {"session-epoch", "sign-in-key"}     # Nothing else is ever written through this seam.
                with files.native(path, owned_root=owned_root, create=True) as real:
                    real.publish_private(name, content, replace=replace)

            def remove_private(self, name, expected_sha256):
                assert name == "sign-in-key"                # The page's swap, and nothing else.
                with files.native(path, owned_root=owned_root) as real:
                    real.remove_private(name, expected_sha256)
        yield Pinned()


@pytest.fixture
def files(monkeypatch):
    from amplifier_web import windows_files
    model = PrivateFiles(windows_files.directory)           # The real store, before the seam replaces it.
    monkeypatch.setattr(entry, "is_windows", lambda: True)
    monkeypatch.setattr(entry, "current_owner_sid", lambda: SID)
    monkeypatch.setattr(windows_files, "directory", model.directory)
    return model


@pytest.fixture
def host(home, files):
    app = web.Application()
    app["control_token"], app["session_secret"] = TOKEN, SECRET
    app["server_config"] = {"session_ttl_seconds": 600,
                            "public_origins": [REMOTE, OTHER, PLAIN, LOCAL, "https://localhost:8941"]}
    app["allowed_origins"] = frozenset({LOCAL, LOCAL.replace("https:", "http:"), REMOTE,
                                        REMOTE.replace("https:", "http:"), OTHER, PLAIN, UNLISTED})
    app["service"] = SimpleNamespace(data_dir=home, update_manager=SimpleNamespace(
        running_identity={"instanceId": "b" * 32, "version": __version__, "revision": REVISION}))
    from amplifier_web.deployment import DEFAULT_SERVER
    from amplifier_web.instance_branding import InstanceBranding
    app["service"].instance_branding = InstanceBranding(home, DEFAULT_SERVER)
    app["sessions"] = auth.Sessions()                       # No sign-out yet, as on a new installation.
    entry.initialize(app)
    assert isinstance(app["browser_entry"], entry.GrantStore) and isinstance(app["browser_codes"], entry.CodeStore)
    assert isinstance(app["browser_key"], entry.SavedKey)
    app.router.add_post(entry.ISSUE_PATH, entry.issue)
    app.router.add_post(entry.REDEEM_PATH, entry.redeem)
    app.router.add_post(entry.CODE_PATH, entry.code_issue)
    app.router.add_post(entry.CODE_REDEEM_PATH, entry.code_redeem)
    app.router.add_post(entry.KEY_REDEEM_PATH, entry.key_redeem)
    app.router.add_get(entry.PASSWORD_PATH, entry.password_page)
    app.router.add_post(entry.PASSWORD_PATH, entry.password_page)
    app.router.add_get("/login", auth.login_page)

    async def protected(request):
        return web.json_response({"authenticated": True})
    app.router.add_get("/api/protected", protected)
    return app


async def build(app, path, *, method="POST", at=LOCAL, origin=..., headers=None, body=None,
                secure=True, peer="127.0.0.1", sock="127.0.0.1", connected=True, held=False):
    """One real aiohttp request to the address `at`; no socket. With `held`, the body is not there yet."""
    headers = {"Host": at.split("://", 1)[1], "Origin": at if origin is ... else origin, **(headers or {})}
    headers = {key: value for key, value in headers.items() if value is not None}
    if isinstance(body, dict):
        raw = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    else:
        raw = (body or "").encode()
    headers["Content-Length"] = str(len(raw))
    content = aiohttp.StreamReader(Mock(_reading_paused=False), 8192)
    if not held:
        content.feed_data(raw)
        content.feed_eof()

    def extra(name):
        if name in {"peername", "sockname"}:
            return (peer if name == "peername" else sock, 8941)
    request = make_mocked_request(method, path, headers=headers, app=app, payload=content,
                               transport=SimpleNamespace(get_extra_info=extra),
                               sslcontext=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER) if secure else None)
    if not connected:
        request._protocol.transport = None                  # The connection is gone.
    return request


async def send(app, path, **options):
    """That request through the real authentication middleware to the real handler."""
    request = await build(app, path, **options)
    match = await app.router.resolve(request)
    match.add_app(app)
    request._match_info = match
    try:
        return await auth.auth_required(request, match.handler)
    except web.HTTPException as response:
        return response


def call(*args, **options):
    return asyncio.run(send(*args, **options))


def issue_body(app, **changes):
    return {"instanceId": "b" * 32, "version": __version__, "revision": REVISION,
            "dataIdentity": auth.data_identity(app["service"].data_dir), "ownerSid": SID,
            "origin": REMOTE, **changes}


def make_code(app, origin=REMOTE):
    response = call(app, entry.CODE_PATH, headers=BEARER, body=issue_body(app, origin=origin))
    assert response.status == 200
    return json.loads(response.body)["code"]


def local_ticket(app):
    """A ticket, as the launcher on the PC gets it."""
    body = issue_body(app)
    del body["origin"]
    response = call(app, entry.ISSUE_PATH, headers=BEARER, body=body)
    assert response.status == 200
    return json.loads(response.body)["ticket"]


def open_ticket(app, ticket):
    """Post the ticket form at the local address, as the local sign-in page does."""
    csrf = auth.new_csrf(app["session_secret"])
    return call(app, entry.REDEEM_PATH, headers={"Content-Type": FORM, "Cookie": f"{auth.CSRF_COOKIE}={csrf}"},
                body=urlencode({"csrf": csrf, "ticket": ticket}))


async def typed(app, code, *, at=REMOTE, csrf=None, headers=None, body=None, **options):
    """Post the code form at the remote address, as the page on the other device does."""
    csrf = csrf or auth.new_csrf(app["session_secret"])
    return await send(app, entry.CODE_REDEEM_PATH, at=at, headers={
        "Content-Type": FORM, "Cookie": f"{auth.CSRF_COOKIE}={csrf}", **(headers or {})},
        body=body or urlencode({"csrf": csrf, "code": code}), **options)


def redeem(*args, **options):
    return asyncio.run(typed(*args, **options))


def signed_in(response):
    return (response.status == 303 and response.headers["Location"] == "/"
            and auth.SESSION_COOKIE in response.cookies)


def wrong(response):
    return (response.status == 303 and response.headers["Location"] == "/login?error=1"
            and auth.SESSION_COOKIE not in response.cookies)


def session_payload(value):
    encoded = value.rsplit(".", 1)[0]
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


def binding():
    return entry.Binding("b" * 32, "d" * 64, REMOTE, SID)


# The store.

def test_store_is_hash_only_bounded_one_use_and_bound_to_its_address():
    now = [0]
    store = entry.CodeStore(clock=lambda: now[0])
    assert (store.ttl, store.capacity) == (120, 8)
    assert len(set(entry.CODE_ALPHABET)) == 32 and not set("01IO") & set(entry.CODE_ALPHABET)
    assert entry.CodeStore.display("ABCDEFGHJK") == "ABCDE-FGHJK"
    code = store.issue(binding())
    assert re.fullmatch(f"[{entry.CODE_ALPHABET}]{{10}}", code)
    # Only the SHA-256 is kept; the code text is nowhere in the store.
    assert list(store._codes) == [hashlib.sha256(code.encode()).digest()]
    assert code not in repr(vars(store))
    # Another address, host, data folder or owner is refused and removes nothing.
    for key in ("instance_id", "data_identity", "owner_sid", "origin"):
        assert store.consume(code, replace(binding(), **{key: "wrong"})) is False
    assert store.consume("2" * 10, binding()) is False and len(store._codes) == 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.consume(code, binding()), range(32)))
    assert results.count(True) == 1 and results.count(False) == 31
    assert store.failures == 5 + 31
    codes = [store.issue(binding()) for _ in range(8)]
    with pytest.raises(entry.BrowserEntryUnavailable, match="Too many codes"):
        store.issue(binding())
    now[0] = 119
    assert store.consume(codes[0], binding()) is True
    now[0] = 120
    assert store.consume(codes[1], binding()) is False      # Expired at 120 seconds.
    assert len(store._codes) == 6                           # Only that expired code was removed.
    store.issue(binding())                                  # The expired ones no longer fill the store.
    store.clear()
    assert not store._codes


@pytest.mark.parametrize("options", [{"ttl": 121}, {"ttl": 0}, {"capacity": 9}, {"capacity": 0}])
def test_store_bounds_cannot_weaken_the_numbers(options):
    with pytest.raises(ValueError):
        entry.CodeStore(**options)


def test_typed_code_accepts_case_spaces_and_a_hyphen_but_never_a_look_alike():
    store = entry.CodeStore()
    for write in (str.lower, entry.CodeStore.display, lambda text: " " + " ".join(text) + " ",
                  lambda text: entry.CodeStore.display(text).lower()):
        code = store.issue(binding())
        assert store.consume(write(code), binding()) is True
    code = store.issue(binding())
    for look_alike in "01IOio":
        assert store.consume(look_alike + code[1:], binding()) is False
    for malformed in (None, "", code[:9], code + "2", code + "\n", "\t" + code, "_" + code[1:], [code], code.encode()):
        assert store.consume(malformed, binding()) is False
    assert store.consume(code, binding()) is True           # No wrong text removed the real code.
    # A letter outside ASCII that upper-cases to a code symbol is not that symbol.
    store._codes[hashlib.sha256(b"S" * 10).digest()] = (store.clock() + 120, binding())
    assert "\u017f".upper() == "S" and store.consume("\u017f" * 10, binding()) is False
    assert store.consume("s" * 10, binding()) is True


# The owner's request for a code: this computer only, control credential only.

@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer amt_" + "x" * 64},
    {"Authorization": "Bearer amc_" + "x" * 64}, {"Authorization": "Bearer amd_" + "x" * 64},
    {"Cookie": f"{auth.SESSION_COOKIE}=" + auth.new_session(SECRET)},
    {"Cookie": f"{auth.SESSION_COOKIE}=" + auth.new_session(SECRET, remote=True)}])
def test_issue_needs_the_control_bearer_not_a_session_or_a_device_token(host, headers):
    assert call(host, entry.CODE_PATH, headers=headers, body=issue_body(host)).status == 403
    assert not host["browser_codes"]._codes


@pytest.mark.parametrize("options", [
    {"peer": "192.0.2.20"},                                                 # Not from this computer.
    {"at": REMOTE},                                                         # Not the local address.
    {"origin": None}, {"origin": REMOTE}, {"origin": "null"},
    {"secure": False, "origin": LOCAL.replace("https:", "http:")},
    {"method": "GET"}])
def test_issue_needs_a_loopback_peer_and_the_exact_local_https_origin(host, options):
    assert call(host, entry.CODE_PATH, headers=BEARER, body=issue_body(host), **options).status == 403
    assert not host["browser_codes"]._codes


@pytest.mark.parametrize("origin", [
    "https://unknown.example:8941", UNLISTED,               # Not configured.
    PLAIN,                                                  # Configured, not HTTPS.
    LOCAL, "https://localhost:8941",                        # Configured, but this computer itself.
    REMOTE + "/", REMOTE.upper(), None, 5, [REMOTE], "absent"])
def test_issue_is_only_for_an_exact_configured_https_address_of_another_device(host, origin):
    body = issue_body(host, origin=origin)
    if origin == "absent":
        del body["origin"]
    assert call(host, entry.CODE_PATH, headers=BEARER, body=body).status == 400
    assert not host["browser_codes"]._codes


@pytest.mark.parametrize("change", [{key: "wrong"} for key in (
    "instanceId", "version", "revision", "dataIdentity", "ownerSid")] + [{"next": "/"}])
def test_issue_refuses_another_host_identity(host, change):
    assert call(host, entry.CODE_PATH, headers=BEARER, body=issue_body(host, **change)).status == 409
    assert not host["browser_codes"]._codes


def test_issue_reply_capacity_and_unavailable_host(host, monkeypatch):
    response = call(host, entry.CODE_PATH, headers=BEARER, body=issue_body(host))
    reply = json.loads(response.body)
    assert response.status == 200 and set(reply) == {"code", "expiresIn"} and reply["expiresIn"] == 120
    assert re.fullmatch(f"[{entry.CODE_ALPHABET}]{{10}}", reply["code"])
    assert response.headers["Cache-Control"] == "no-store" and response.headers["Referrer-Policy"] == "no-referrer"
    assert call(host, entry.CODE_PATH, headers={**BEARER, "Content-Type": "text/plain"},
                body=json.dumps(issue_body(host))).status == 400
    for _ in range(7):
        make_code(host, OTHER)
    assert call(host, entry.CODE_PATH, headers=BEARER, body=issue_body(host)).status == 503
    assert len(host["browser_codes"]._codes) == 8
    # A full store never blocks the form: the first code still signs in.
    assert signed_in(redeem(host, reply["code"]))
    read = entry.read_owner_control_token
    monkeypatch.setattr(entry, "read_owner_control_token", lambda *a: "d" * 64)    # The private credential changed.
    assert call(host, entry.CODE_PATH, headers=BEARER, body=issue_body(host)).status == 503
    monkeypatch.setattr(entry, "read_owner_control_token", read)
    host["browser_codes"] = None
    assert call(host, entry.CODE_PATH, headers=BEARER, body=issue_body(host)).status == 503


def test_stores_exist_together_or_not_at_all(host, monkeypatch):
    host["control_token"] = "d" * 64                        # Not the private owner credential.
    entry.initialize(host)
    assert host["browser_entry"] is None and host["browser_codes"] is None and host["browser_key"] is None
    monkeypatch.setattr(entry, "is_windows", lambda: False)
    other = web.Application()
    entry.initialize(other)
    assert other["browser_entry"] is None and other["browser_codes"] is None and other["browser_key"] is None


# The typed code at the remote address.

@pytest.mark.parametrize("peer", ["127.0.0.1", "100.101.102.103"])
def test_code_signs_in_at_the_remote_address_whatever_the_peer_address_is(host, peer):
    """A Tailscale TCP forward connects from 127.0.0.1; a LAN browser does not."""
    assert call(host, "/api/protected", method="GET", at=REMOTE, origin=None).status == 401
    response = redeem(host, make_code(host), peer=peer)
    assert response.status == 303 and response.headers["Location"] == "/"
    assert response.headers["Cache-Control"] == "no-store" and response.headers["Referrer-Policy"] == "no-referrer"
    cookie = response.cookies[auth.SESSION_COOKIE]
    assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Strict" and cookie["path"] == "/"
    assert int(cookie["max-age"]) == 600
    deleted = response.cookies[auth.CSRF_COOKIE]
    assert deleted.value == "" and int(deleted["max-age"]) == 0
    assert auth.valid_session(host, cookie.value)
    assert session_payload(cookie.value)["via"] == "remote"
    assert call(host, "/api/protected", method="GET", at=REMOTE, origin=None,
                headers={"Cookie": f"{auth.SESSION_COOKIE}={cookie.value}"}).status == 200
    # A local session is exactly what it was: no mark.
    assert list(session_payload(auth.new_session(SECRET))) == ["kind", "issued", "nonce"]


def test_address_check_stands_alone_and_does_not_depend_on_the_peer_address(host):
    """The handler's own check, without the middleware in front of it."""
    async def origin(**options):
        request = await build(host, entry.CODE_REDEEM_PATH, **{"at": REMOTE, **options})
        request.transport.get_extra_info = lambda name: pytest.fail("The peer address was read")
        return entry.remote_origin(request)

    async def check():
        assert await origin() == REMOTE and await origin(at=OTHER) == OTHER
        assert await origin(peer="203.0.113.9") == REMOTE
        for refused in ({"origin": OTHER}, {"origin": None}, {"origin": "null"}, {"origin": REMOTE + "/"},
                        {"at": LOCAL}, {"at": "https://localhost:8941"}, {"at": UNLISTED},
                        {"at": PLAIN, "secure": False}, {"secure": False},
                        {"secure": False, "origin": REMOTE.replace("https:", "http:")}):
            assert await origin(**refused) is None, refused
    asyncio.run(check())


def fields(app, **values):
    """A form body and the CSRF cookie that belongs to it."""
    csrf = auth.new_csrf(app["session_secret"])
    return {"csrf": csrf, "body": urlencode({"csrf": csrf, **values})}


REFUSED = {
    "loopback Host": lambda app, code: {"at": LOCAL},
    "localhost Host": lambda app, code: {"at": "https://localhost:8941"},
    "Host that is not a configured address": lambda app, code: {"at": UNLISTED},
    "configured address that is not HTTPS": lambda app, code: {"at": PLAIN, "secure": False},
    "no Origin": lambda app, code: {"origin": None},
    "another Origin": lambda app, code: {"origin": OTHER},
    "opaque Origin": lambda app, code: {"origin": "null"},
    "plain HTTP": lambda app, code: {"secure": False, "origin": REMOTE.replace("https:", "http:")},
    "plain HTTP without Origin": lambda app, code: {"secure": False, "origin": None},
    "no CSRF cookie": lambda app, code: {"headers": {"Cookie": None}},
    "unsigned CSRF": lambda app, code: {"csrf": "wrong"},
    "expired CSRF": lambda app, code: {"csrf": auth._signed(SECRET, {"kind": "csrf", "issued": 1})},
    "a session used as CSRF": lambda app, code: {"csrf": auth.new_session(SECRET)},
    "form CSRF is not the cookie's": lambda app, code: {"body": fields(app, code=code)["body"]},
    "no code field": lambda app, code: fields(app),
    "another field in place of the code": lambda app, code: fields(app, ticket=code),
    "extra field": lambda app, code: fields(app, code=code, next="/"),
    "duplicate field": lambda app, code: {
        key: value + ("&code=" + code if key == "body" else "") for key, value in fields(app, code=code).items()},
    "JSON content type": lambda app, code: {"headers": {"Content-Type": "application/json"}},
    "multipart content type": lambda app, code: {"headers": {"Content-Type": "multipart/form-data; boundary=x"}},
}


@pytest.mark.parametrize("options", REFUSED.values(), ids=[name.replace(" ", "-") for name in REFUSED])
def test_failed_precondition_is_refused_and_keeps_the_code(host, options):
    code = make_code(host)
    response = redeem(host, code, **options(host, code))
    assert response.status == 403 and auth.SESSION_COOKIE not in response.cookies
    assert host["browser_codes"].failures == 0              # Not counted as a wrong code.
    assert signed_in(redeem(host, code))


def test_oversize_form_and_changed_host_identity_are_refused(host, monkeypatch):
    code = make_code(host)
    assert redeem(host, code, body="csrf=" + "x" * 4096 + "&code=" + code).status == 413
    read = entry.read_owner_control_token
    monkeypatch.setattr(entry, "read_owner_control_token", lambda *a: "d" * 64)    # The private credential changed.
    # The host identity cannot be confirmed: no session, and the code is kept.
    assert wrong(redeem(host, code))
    monkeypatch.setattr(entry, "read_owner_control_token", read)
    monkeypatch.setattr(entry, "current_owner_sid", lambda: "S-1-5-21-999")        # Another Windows account.
    assert wrong(redeem(host, code))
    monkeypatch.setattr(entry, "current_owner_sid", lambda: SID)
    host["service"].update_manager.running_identity["instanceId"] = "e" * 32
    assert wrong(redeem(host, code))                        # A code from before a restart.
    host["service"].update_manager.running_identity["instanceId"] = "b" * 32
    assert signed_in(redeem(host, code))


def test_real_https_sign_in_at_the_remote_address_with_the_product_certificate(host, tmp_path):
    """Real sockets, real TLS and the real middleware; only the private store is a model.

    The listener is on loopback, as on a Windows host behind a Tailscale TCP
    forward: the client reaches it by the remote name, and the peer is local.
    """
    from cryptography.hazmat.primitives import serialization
    from amplifier_web import deployment, windows_tls
    config = deployment.validate_server({"public_origins": [LOCAL, REMOTE]})
    root, root_key = windows_tls._new_root(config)          # The key-less identity's shape.
    leaf, leaf_key = windows_tls._new_local_leaf(root, root_key, config)
    cert, key = windows_tls._encoded_pair(leaf, leaf_key)
    (tmp_path / "leaf.crt").write_bytes(cert)
    (tmp_path / "leaf.key").write_bytes(key)
    serving = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    serving.load_cert_chain(tmp_path / "leaf.crt", tmp_path / "leaf.key")
    trusting = ssl.create_default_context(cadata=root.public_bytes(serialization.Encoding.PEM).decode())
    served = web.Application(middlewares=[auth.auth_required])
    for name in ("control_token", "session_secret", "server_config", "allowed_origins", "service",
                 "browser_entry", "browser_codes", "sessions"):
        served[name] = host[name]
    served.router.add_post(entry.CODE_PATH, entry.code_issue)
    served.router.add_post(entry.CODE_REDEEM_PATH, entry.code_redeem)
    served.router.add_get("/login", auth.login_page)

    async def protected(request):
        return web.json_response({"authenticated": True})
    served.router.add_get("/api/protected", protected)
    remote_name, remote_host = "pc.tail1234.ts.net", REMOTE.split("://", 1)[1]

    async def run():
        runner = web.AppRunner(served)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=serving)
        await site.start()
        base = f"https://127.0.0.1:{runner.addresses[0][1]}"
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as client:
                def at_remote(method, path, **options):
                    headers = {"Host": remote_host, **options.pop("headers", {})}
                    return client.request(method, base + path, ssl=trusting, server_hostname=remote_name,
                                          allow_redirects=False, headers=headers, **options)
                # The certificate is valid for the remote name and for no other name.
                with pytest.raises(aiohttp.ClientConnectorCertificateError):
                    await client.get(base + "/login", ssl=trusting, server_hostname="other.example")
                async with at_remote("GET", "/login") as page:
                    text = await page.text()
                    csrf = page.cookies[auth.CSRF_COOKIE].value
                assert page.status == 200 and "<script" not in text and f'value="{csrf}"' in text
                form = {"Origin": REMOTE, "Content-Type": FORM, "Cookie": f"{auth.CSRF_COOKIE}={csrf}"}
                # The owner makes a code at the PC, over the local address.
                async with client.post(base + entry.CODE_PATH, ssl=trusting, json=issue_body(host), headers={
                        **BEARER, "Host": LOCAL.split("://", 1)[1], "Origin": LOCAL}) as issued:
                    code = (await issued.json())["code"]
                assert issued.status == 200
                guess = next(symbol for symbol in entry.CODE_ALPHABET if symbol != code[0]) + code[1:]
                async with at_remote("POST", entry.CODE_REDEEM_PATH, headers=form,
                                     data=urlencode({"csrf": csrf, "code": guess})) as refused:
                    assert refused.status == 303 and refused.headers["Location"] == "/login?error=1"
                    assert auth.SESSION_COOKIE not in refused.cookies
                # The same form at the local address is not a remote sign-in.
                async with client.post(base + entry.CODE_REDEEM_PATH, ssl=trusting, allow_redirects=False,
                                       data=urlencode({"csrf": csrf, "code": code}),
                                       headers={**form, "Host": LOCAL.split("://", 1)[1], "Origin": LOCAL}) as local:
                    assert local.status == 403
                async with at_remote("POST", entry.CODE_REDEEM_PATH, headers=form,
                                     data=urlencode({"csrf": csrf, "code": code})) as accepted:
                    assert accepted.status == 303 and accepted.headers["Location"] == "/"
                    cookie = accepted.cookies[auth.SESSION_COOKIE]
                assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Strict"
                async with at_remote("GET", "/api/protected") as anonymous:
                    assert anonymous.status == 401
                async with at_remote("GET", "/api/protected", headers={
                        "Cookie": f"{auth.SESSION_COOKIE}={cookie.value}"}) as signed:
                    assert signed.status == 200 and (await signed.json()) == {"authenticated": True}
                # The code worked once.
                async with at_remote("POST", entry.CODE_REDEEM_PATH, headers=form,
                                     data=urlencode({"csrf": csrf, "code": code})) as again:
                    assert again.status == 303 and again.headers["Location"] == "/login?error=1"
                # The owner makes one more code and then signs out all browsers at the PC.
                # The host has not looked at the saved sign-out yet: that code opens nothing.
                async with client.post(base + entry.CODE_PATH, ssl=trusting, json=issue_body(host), headers={
                        **BEARER, "Host": LOCAL.split("://", 1)[1], "Origin": LOCAL}) as issued:
                    earlier = (await issued.json())["code"]
                auth.sign_out_all(host["service"].data_dir)
                async with at_remote("POST", entry.CODE_REDEEM_PATH, headers=form,
                                     data=urlencode({"csrf": csrf, "code": earlier})) as waiting:
                    assert waiting.status == 303 and waiting.headers["Location"] == "/login?error=1"
                    assert auth.SESSION_COOKIE not in waiting.cookies
                # The host applies it: the browser that signed in above is signed out.
                assert auth.refresh_sessions(served) is True and not host["browser_codes"]._codes
                async with at_remote("GET", "/api/protected", headers={
                        "Cookie": f"{auth.SESSION_COOKIE}={cookie.value}"}) as signed_out:
                    assert signed_out.status == 401
        finally:
            await runner.cleanup()
    asyncio.run(run())


def test_wrong_code_reads_no_private_file_and_a_right_code_confirms_the_host(host, files):
    code = make_code(host)
    guess = next(symbol for symbol in entry.CODE_ALPHABET if symbol != code[0]) + code[1:]
    files.events.clear()
    # Any device that reaches the address can send wrong codes; each stays cheap.
    for attempt in (guess, "not a code", "", guess.lower()):
        assert wrong(redeem(host, attempt))
    assert files.events == []
    # A live code confirms the owner credential and host identity before it signs in.
    assert signed_in(redeem(host, code))
    assert "control-token" in files.events


def test_wrong_code_shows_the_error_page_without_lockout_and_logs_only_a_count(host, capsys):
    code = make_code(host)
    guess = next(symbol for symbol in entry.CODE_ALPHABET if symbol != code[0]) + code[1:]
    response = redeem(host, guess)
    assert wrong(response) and auth.CSRF_COOKIE not in response.cookies
    assert response.headers["Cache-Control"] == "no-store" and response.headers["Referrer-Policy"] == "no-referrer"
    assert capsys.readouterr().out == "Remote sign-in: 1 wrong codes since start.\n"
    host["browser_codes"].failures = 98
    assert wrong(redeem(host, guess)) and capsys.readouterr().out == ""
    assert wrong(redeem(host, "O" + code[1:]))              # A look-alike is a wrong code.
    assert capsys.readouterr().out == "Remote sign-in: 100 wrong codes since start.\n"
    # No lockout and no delay: after 100 wrong codes the real one still works, once.
    assert signed_in(redeem(host, entry.CodeStore.display(code).lower()))
    assert wrong(redeem(host, code))
    printed = capsys.readouterr()
    assert code not in printed.out + printed.err and guess not in printed.out + printed.err


def test_code_expires_at_120_seconds_and_works_only_at_its_own_address(host):
    now = [0]
    host["browser_codes"].clock = lambda: now[0]
    late, in_time, elsewhere = make_code(host), make_code(host), make_code(host)
    assert wrong(redeem(host, elsewhere, at=OTHER))         # Made for REMOTE, typed at OTHER.
    assert signed_in(redeem(host, elsewhere))
    now[0] = 119
    assert signed_in(redeem(host, " ".join(in_time)))
    now[0] = 120
    assert wrong(redeem(host, late))
    assert signed_in(redeem(host, make_code(host, OTHER), at=OTHER))


def test_eight_parallel_redeems_give_exactly_one_session(host):
    code = make_code(host)

    async def together():
        return await asyncio.gather(*(typed(host, code) for _ in range(8)))
    results = asyncio.run(together())
    assert sorted(response.headers["Location"] for response in results) == ["/"] + ["/login?error=1"] * 7
    assert sum(auth.SESSION_COOKIE in response.cookies for response in results) == 1


def test_sign_out_all_browsers_drops_pending_codes_and_remote_sessions(home, monkeypatch):
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_100)
    store = entry.CodeStore()
    code = store.issue(binding())
    app = {"session_secret": SECRET, "server_config": {"session_ttl_seconds": 600},
           "service": SimpleNamespace(data_dir=home), "sessions": auth.Sessions.load(home),
           "browser_codes": store}
    before = auth._signed(SECRET, {"kind": "session", "issued": 1_900_000_050, "nonce": "n", "via": "remote"})
    assert auth.valid_session(app, before)
    assert auth.refresh_sessions(app) is False and len(store._codes) == 1
    auth.sign_out_all(home)                                 # The owner's command, in another process.
    assert auth.refresh_sessions(app) is True
    assert not store._codes and store.consume(code, binding()) is False
    assert not auth.valid_session(app, before)


def test_code_or_ticket_opens_no_session_while_a_saved_sign_out_waits_for_the_host(host, monkeypatch):
    """The owner signed out; the running host looks at the saved record only every two seconds."""
    code, ticket, later_code, later_ticket = make_code(host), local_ticket(host), make_code(host), local_ticket(host)
    applied = host["sessions"].hint
    monkeypatch.setattr(auth, "_epoch_hint", lambda data_dir: "a saved sign-out that the host has not applied")
    assert wrong(redeem(host, code))
    assert host["browser_codes"].failures == 0              # Refused, and not counted as a wrong code.
    refused = open_ticket(host, ticket)
    assert refused.status == 403 and auth.SESSION_COOKIE not in refused.cookies
    assert refused.text == entry.ON_THE_PC
    # Both are used up: they open nothing later either.
    monkeypatch.setattr(auth, "_epoch_hint", lambda data_dir: applied)
    assert wrong(redeem(host, code)) and open_ticket(host, ticket).status == 403
    # With nothing waiting, a code and a ticket sign in as before.
    assert signed_in(redeem(host, later_code))
    opened = open_ticket(host, later_ticket)
    assert opened.status == 303 and auth.valid_session(host, opened.cookies[auth.SESSION_COOKIE].value)
    assert "via" not in session_payload(opened.cookies[auth.SESSION_COOKIE].value)


def test_failed_look_at_the_sign_out_record_is_not_taken_for_no_sign_out(host, monkeypatch):
    """The first sign-out of an installation: the host has seen no record. Then its look at the record fails."""
    code, ticket = make_code(host), local_ticket(host)
    data_dir = host["service"].data_dir
    assert host["sessions"].hint is None                    # No record when the host started.
    auth.sign_out_all(data_dir)                             # The owner's command. The host has not looked yet.
    record, real = auth._epoch_path(data_dir), os.stat

    def in_use(path, *args, **kwargs):
        if isinstance(path, (str, os.PathLike)) and Path(path) == record:
            raise PermissionError("in use")                 # Not: the record is missing.
        return real(path, *args, **kwargs)
    monkeypatch.setattr(auth.os, "stat", in_use)
    assert wrong(redeem(host, code)) and host["browser_codes"].failures == 0
    refused = open_ticket(host, ticket)
    assert refused.status == 403 and auth.SESSION_COOKIE not in refused.cookies
    monkeypatch.setattr(auth.os, "stat", real)
    assert auth.refresh_sessions(host) is True              # The host applies the sign-out.
    assert signed_in(redeem(host, make_code(host))) and open_ticket(host, local_ticket(host)).status == 303


def test_with_remote_access_off_no_code_can_be_made_or_used(host):
    """The default: only this computer's own addresses are configured."""
    host["server_config"]["public_origins"] = [LOCAL, "https://localhost:8941"]
    for origin in (REMOTE, OTHER, UNLISTED, LOCAL, "https://localhost:8941"):
        assert call(host, entry.CODE_PATH, headers=BEARER, body=issue_body(host, origin=origin)).status == 400
    assert not host["browser_codes"]._codes
    # A code that exists all the same opens nothing: no address is a remote one.
    code = host["browser_codes"].issue(entry.Binding("b" * 32, auth.data_identity(host["service"].data_dir), REMOTE, SID))
    for at in (REMOTE, OTHER, UNLISTED, LOCAL, "https://localhost:8941"):
        response = redeem(host, code, at=at)
        assert response.status == 403 and auth.SESSION_COOKIE not in response.cookies
    host["server_config"]["public_origins"] = []
    assert call(host, entry.CODE_PATH, headers=BEARER, body=issue_body(host)).status == 400
    assert redeem(host, code).status == 403 and len(host["browser_codes"]._codes) == 1


def test_form_is_refused_when_the_host_has_no_code_store(host):
    code = make_code(host)
    host["browser_codes"] = None
    response = redeem(host, code)
    assert response.status == 403 and response.text == entry.RELOAD and auth.SESSION_COOKIE not in response.cookies


def test_issue_handler_checks_the_credential_itself_and_again_after_it_read_the_request(host, monkeypatch):
    """Without the middleware in front: the handler's own two checks."""
    async def direct(headers):
        return await entry.code_issue(await build(host, entry.CODE_PATH, headers=headers, body=issue_body(host)))
    read = web.Request.clone
    # Refused before the handler reads anything that the caller sent.
    monkeypatch.setattr(web.Request, "clone", lambda *a, **k: pytest.fail("The request was read without the credential"))
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}):
        assert asyncio.run(direct(headers)).status == 403
    monkeypatch.setattr(web.Request, "clone", read)
    assert asyncio.run(direct(BEARER)).status == 200
    host["browser_codes"].clear()

    def changed_while_reading(request, **options):
        host["control_token"] = "d" * 64                    # The credential changed during the body read.
        return read(request, **options)
    monkeypatch.setattr(web.Request, "clone", changed_while_reading)
    response = asyncio.run(direct(BEARER))
    assert response.status == 403 and json.loads(response.body) == {"error": "The owner control credential changed."}
    assert not host["browser_codes"]._codes


# The sign-in page.


def test_the_field_and_sign_in_come_directly_after_the_heading_and_the_error(host):
    """"Sign in" must not depend on how long the help is.

    Run G: at 300 % the error text moved the button below the window edge,
    because all the help stood between the heading and the field.
    """
    def parts(path):
        text = call(host, path, method="GET", at=REMOTE, origin=None).text
        body = text[text.index("<main>"):text.index("</main>")]
        heading, error, form, button, end = (body.index(mark) for mark in (
            "<h1>", '<p class="error" role="alert">', "<form ", '<button type="submit">Sign in</button>', "</form>"))
        assert heading < error < form < button < end
        # Between the heading and the first form there is the error text and nothing else.
        between = body[body.index("</h1>") + len("</h1>"):form]
        assert re.fullmatch(r'\s*<p class="error" role="alert">[^<]*</p>\s*', between), between
        return text, body, form, end
    # No password is saved: the code form is the one form.
    for path in ("/login", "/login?error=1"):
        text, body, form, end = parts(path)
        assert body.count("<form ") == 1 and f'action="{entry.CODE_REDEEM_PATH}"' in body[form:end]
        # Every other text and the one link come after the button.
        assert all(body.index(mark) > end for mark in ('id="code-help"', 'id="password-help"', "<code>", "<a "))
        assert body.count("<p") == 4
    assert "That code did not work" in text
    # A password is saved: its form is the first one, and the code form comes after one short line.
    entry.set_password(host["service"].data_dir, PASSWORD)
    for path in ("/login", "/login?error=password", "/login?error=wait", "/login?error=1"):
        text, body, form, end = parts(path)
        assert f'action="{entry.KEY_REDEEM_PATH}"' in body[form:end]
        code_form = body.index("<form ", end)
        assert (body[end + len("</form>"):code_form].strip()
                == '<p class="note">Or sign in with a one-use code from the PC.</p>')
        assert f'action="{entry.CODE_REDEEM_PATH}"' in body[code_form:] and body.count("<form ") == 2
        assert all(body.index(mark) > body.index("</form>", code_form)
                   for mark in ('id="code-help"', 'id="password-help"', "<code>", "<a "))
        assert body.count("<p") == 5


# The next step for a browser on the PC that is not signed in.

def from_setup(app, monkeypatch, *, entry_there=True):
    """Make this host the one of an installation from the setup file.

    Its data folder is `<root>/app`, and the task of that installation's
    launcher and workspace started it. The Start-menu record is a stand-in;
    `test_the_host_reads_the_real_start_menu_record` has the real one.
    """
    from amplifier_web import windows_consumer_shell
    root = Path(app["service"].data_dir)
    app["service"].data_dir = root / "app"
    app["owner_task"] = SimpleNamespace(
        python=str(root / "tools" / "amplifier-unified" / "Scripts" / "pythonw.exe"),
        workspace=str(root / "workspace"), data_dir=str(root / "app"))
    looked = []
    monkeypatch.setattr(windows_consumer_shell, "listed", lambda folder: looked.append(Path(folder)) or entry_there)
    return root, looked


def step(app, **options):
    """The text that a browser gets for a ticket that opens nothing."""
    csrf = auth.new_csrf(app["session_secret"])
    refused = call(app, entry.REDEEM_PATH, headers={"Content-Type": FORM, "Cookie": f"{auth.CSRF_COOKIE}={csrf}"},
                   body=urlencode({"csrf": csrf, "ticket": "a" * 43}), **options)
    assert refused.status == 403 and auth.SESSION_COOKIE not in refused.cookies
    return refused.text


def test_a_pc_from_the_setup_file_is_sent_to_its_start_menu_entry(host, monkeypatch):
    root, looked = from_setup(host, monkeypatch)
    assert step(host) == ("Open Amplifier from the Start menu: Amplifier Unified. "
                          "Amplifier then opens in your browser.")
    assert looked == [root]                                 # The record of this installation, and no other.
    # The name in the text is the name of the entry that setup makes.
    from amplifier_web import windows_consumer_shell
    assert "Amplifier Unified.lnk" in windows_consumer_shell.LINKS
    # Every local name of this computer gets the same text. The fixture's host accepts an
    # Origin of its first local address only, so these requests carry none.
    for at in ("https://localhost:8941", "https://[::1]:8941", "https://LOCALHOST.:8941", "https://127.0.0.2:8941"):
        assert step(host, at=at, origin=None) == entry.START_MENU
    # A form that this host did not sign gets the same text, and no session.
    unsigned = call(host, entry.REDEEM_PATH, headers={"Content-Type": FORM},
                    body=urlencode({"csrf": "not signed", "ticket": "a" * 43}))
    assert unsigned.status == 403 and unsigned.text == entry.START_MENU and auth.SESSION_COOKIE not in unsigned.cookies


@pytest.mark.parametrize("change", [
    "no-ticket-store", "started-by-a-person", "another-data-folder", "another-launcher", "another-workspace",
    "no-entry", "entry-test-fails"])
def test_every_other_windows_host_names_no_way(host, monkeypatch, change):
    """One sentence that is true everywhere. No command: this host knows none that works."""
    root, looked = from_setup(host, monkeypatch, entry_there=change != "no-entry")
    task = host["owner_task"]
    if change == "no-ticket-store":
        # This host gives no ticket: the entry's `open` would get a refusal from it.
        host["browser_entry"] = None
    elif change == "started-by-a-person":
        del host["owner_task"]                              # No task started this host.
    elif change == "another-data-folder":
        # The Start-menu entry opens `<root>/app` only.
        host["service"].data_dir = task.data_dir = root / "data"
    elif change == "another-launcher":
        task.python = str(root / "other" / "Scripts" / "pythonw.exe")
    elif change == "another-workspace":
        task.workspace = str(root / "elsewhere")
    elif change == "entry-test-fails":
        from amplifier_web import windows_consumer_shell

        def unreadable(folder):
            raise PermissionError("The record cannot be read")
        monkeypatch.setattr(windows_consumer_shell, "listed", unreadable)
    assert step(host) == "Open Amplifier again on the Windows PC that runs it."
    # The record is read only when this host is the one that the entry opens.
    assert looked == ([root] if change == "no-entry" else [])


@pytest.mark.parametrize("options", [
    {"at": REMOTE},                                         # Another name than this computer's.
    {"peer": "192.0.2.20"},                                 # The other end is not this computer.
    {"sock": "192.0.2.5"},                                  # This end is not a local address.
    {"connected": False}])
def test_a_request_that_is_not_from_this_computer_reads_no_file(host, monkeypatch, options):
    root, looked = from_setup(host, monkeypatch)
    assert step(host, **options) == entry.ON_THE_PC
    assert looked == []


def test_the_texts_name_no_command_on_windows_and_other_systems_keep_theirs(host, monkeypatch):
    for text in (entry.START_MENU, entry.ON_THE_PC):
        assert "amplifier-unified" not in text and "\\" not in text
        assert "launcher" not in text and "candidate" not in text and "host" not in text
    from_setup(host, monkeypatch)
    monkeypatch.setattr(entry, "is_windows", lambda: False)
    # macOS and Linux have no Start-menu entry; their text is the earlier one.
    assert step(host) == "To open Amplifier, run this: amplifier-unified open."


def test_the_password_form_on_windows_gives_the_same_next_step(host, monkeypatch):
    host.router.add_post("/login", auth.post_login)
    csrf = auth.new_csrf(host["session_secret"])
    form = dict(method="POST", headers={"Content-Type": FORM, "Cookie": f"{auth.CSRF_COOKIE}={csrf}"},
                body=urlencode({"csrf": csrf}))
    refused = call(host, "/login", **form)
    assert refused.status == 403 and refused.text == entry.ON_THE_PC
    from_setup(host, monkeypatch)
    assert call(host, "/login", **form).text == entry.START_MENU


def with_marks(monkeypatch):
    """The sign-in page as the client's change makes it: marks for the step and for the wanted address."""
    read = Path.read_text

    def page(self, *args, **options):
        text = read(self, *args, **options)
        if self.name != "windows-login.html":
            return text
        marked, count = re.subn(r'(<p id="entry-status" role="status">).*?(</p>)', r"\1__REOPEN__\2", text, flags=re.S)
        assert count == 1
        return marked.replace("<main>", '<main data-next="__NEXT__">')
    monkeypatch.setattr(Path, "read_text", page)


def test_the_sign_in_page_shows_the_step_in_its_own_place(host, monkeypatch):
    with_marks(monkeypatch)
    page = call(host, "/login", method="GET", origin=None).text
    assert f'<p id="entry-status" role="status">{entry.ON_THE_PC}</p>' in page and "__REOPEN__" not in page
    root, looked = from_setup(host, monkeypatch)
    page = call(host, "/login", method="GET", origin=None).text
    assert f'<p id="entry-status" role="status">{entry.START_MENU}</p>' in page and looked == [root]
    # The text is data in the page, never markup.
    monkeypatch.setattr(entry, "next_step", lambda request: 'a <b> & "c"')
    page = call(host, "/login", method="GET", origin=None).text
    assert '<p id="entry-status" role="status">a &lt;b&gt; &amp; &quot;c&quot;</p>' in page and "<b>" not in page
    # The step is in the page before any value of the request: a wanted address cannot name the mark.
    monkeypatch.setattr(entry, "next_step", lambda request: "the step")
    page = call(host, "/login?next=%2F__REOPEN__", method="GET", origin=None).text
    assert '<main data-next="/__REOPEN__">' in page and page.count("the step") == 1


def test_a_sign_in_page_without_the_mark_is_not_changed_and_reads_nothing(host, monkeypatch):
    """A page of a client that has no place for the step, whatever the selected client's page has."""
    read = Path.read_text
    plain = read(Path(auth.__file__).parent / "static" / "windows-login.html", encoding="utf-8").replace(
        "__REOPEN__", "The client's own sentence.")
    monkeypatch.setattr(Path, "read_text", lambda self, *args, **options: (
        plain if self.name == "windows-login.html" else read(self, *args, **options)))
    from_setup(host, monkeypatch)
    monkeypatch.setattr(entry, "next_step", lambda request: pytest.fail("The step was worked out for nothing"))
    page = call(host, "/login", method="GET", origin=None)
    csrf = page.cookies[auth.CSRF_COOKIE].value
    # The three marks that the host filled before this change, and nothing else.
    earlier = plain.replace("__CSRF__", csrf).replace("__NEXT__", "/").replace("__ERROR__", "")
    assert page.text == host["service"].instance_branding.document(earlier, login=True)


@pytest.mark.skipif(os.name != "nt", reason="The real private store of Windows and the real record of the entries")
def test_the_host_reads_the_real_start_menu_record(home):
    """No stand-in: the host's own look at `<root>\\consumer-shell.json` through the private folder check.

    The entry itself is a plain file here. Real shortcuts are the uninstall
    cases' part; `listed` reads only that the file is there.
    """
    from amplifier_web import windows_consumer_shell as shell
    from amplifier_web.deployment import write_private
    from amplifier_web.windows_files import address, current_process_sid
    from amplifier_web.windows_service import TaskSpec
    root = home
    folder = root / "entries"
    folder.mkdir()
    app = web.Application()
    app["browser_entry"] = entry.GrantStore()
    app["service"] = SimpleNamespace(data_dir=address(root / "app"))
    # The real definition type, with paths in the form that a registration gives them.
    app["owner_task"] = TaskSpec(
        sid=current_process_sid(), registration_id=uuid.uuid4().hex, tool_path=str(root),
        python=str(address(root / "tools" / "amplifier-unified" / "Scripts" / "pythonw.exe")),
        data_dir=str(address(root / "app")), workspace=str(address(root / "workspace")),
        state_home=str(address(root / "state")))

    def now():
        return entry.next_step(asyncio.run(build(app, "/login", method="GET", origin=None)))
    assert now() == entry.ON_THE_PC                         # No record: setup made no entry.
    write_private(root / shell.FILE, json.dumps({"schema": 1, "root": str(root), "folder": str(folder),
                                                 "status": "installed", "links": {}, "registry": {}}),
                  owned_root=root)
    assert now() == entry.ON_THE_PC                         # The record alone is not the entry.
    (folder / next(iter(shell.LINKS))).write_bytes(b"")
    assert now() == entry.START_MENU
    (folder / next(iter(shell.LINKS))).unlink()             # A person deleted the entry while the host runs.
    assert now() == entry.ON_THE_PC


# What `amplifier-unified open` says when it cannot open the browser.

@pytest.mark.parametrize("windows,again", [(True, ""), (False, " To open Amplifier, run this: amplifier-unified open.")])
def test_a_browser_that_does_not_open_gets_the_cause_and_on_windows_no_command(tmp_path, monkeypatch, windows, again):
    from argparse import Namespace
    from amplifier_web import cli, update_readiness
    monkeypatch.setattr(entry, "is_windows", lambda: windows)

    async def grant(*a, **kw):
        return LOCAL + "/login#ticket=" + "a" * 43
    monkeypatch.setattr(entry, "owner_browser_url", grant)
    monkeypatch.setattr(update_readiness, "probe_targets", lambda host: ([LOCAL + "/api/health"], None))
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: False)
    with pytest.raises(entry.BrowserEntryUnavailable) as refused:
        cli._open_browser(Namespace(no_open=False), tmp_path, {"port": 8941, "public_origins": []})
    assert str(refused.value) == "The browser did not accept the open request." + again

    def broken(url):
        raise OSError(url)
    monkeypatch.setattr(cli.webbrowser, "open", broken)
    with pytest.raises(entry.BrowserEntryUnavailable) as failed:
        cli._open_browser(Namespace(no_open=False), tmp_path, {"port": 8941, "public_origins": []})
    assert str(failed.value) == "Could not open the browser." + again


@pytest.mark.parametrize("windows,again", [(True, ""), (False, " To open Amplifier, run this: amplifier-unified open.")])
def test_an_open_command_that_cannot_start_names_no_command_on_windows(home, monkeypatch, windows, again):
    from amplifier_web import application_generations, deployment, host_dependencies
    monkeypatch.setattr(host_dependencies, "prepare_environment", lambda: None)
    monkeypatch.setattr(application_generations, "delegate", lambda: None)
    monkeypatch.setattr(entry, "is_windows", lambda: windows)
    monkeypatch.setattr(deployment, "load_server_config", lambda data_dir, overrides=None: {"port": 8941})

    def unreadable(*a, **kw):
        raise OSError("private storage")
    monkeypatch.setattr(cli, "_open_browser", unreadable)
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "--data-dir", str(home), "open"])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert str(stopped.value) == "Cannot open the owned host." + again


def owner_request(home, monkeypatch, answer, *, health=True):
    """The pinned owner request of `open` and of `browsers code` against a host that gives `answer`."""
    from amplifier_web import update_readiness
    pin = aiohttp.Fingerprint(bytes(range(32)))
    monkeypatch.setattr(entry, "_owner_probe_targets", lambda *a: ([LOCAL + "/api/health"], pin))
    monkeypatch.setattr(entry, "current_owner_sid", lambda: SID)
    monkeypatch.setattr(entry, "read_owner_control_token", lambda *a: TOKEN)
    monkeypatch.setattr(update_readiness, "running_identity", lambda: {"version": __version__, "revision": REVISION})
    body = {**HEALTH, "dataIdentity": auth.data_identity(home)}

    class Client:
        def __init__(self, **options):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *error):
            pass

        def get(self, url, **options):
            async def reply():
                return web.json_response(body if health else {"ok": True, "app": "amplifier-unified"})
            return Reply(reply)

        def post(self, url, **options):
            async def reply():
                if isinstance(answer, Exception):
                    raise answer
                return answer
            return Reply(reply)
    monkeypatch.setattr(aiohttp, "ClientSession", Client)


def said(operation):
    with pytest.raises(entry.BrowserEntryUnavailable) as stopped:
        asyncio.run(operation)
    return str(stopped.value)


OPEN_ANSWERS = [
    # The host's own reason, as the host wrote it.
    (web.json_response({"error": entry.BUSY}, status=503), entry.BUSY),
    (web.json_response({"error": "The owner host identity changed."}, status=409), "The owner host identity changed."),
    (web.json_response({"error": "Invalid owner entry request."}, status=400), "Invalid owner entry request."),
    # An answer with no reason that can be shown.
    (web.json_response({}, status=503), "Amplifier refused the request. Try again."),
    (web.json_response({"error": ""}, status=503), entry.REFUSED),
    (web.json_response({"error": 5}, status=503), entry.REFUSED),
    (web.json_response(["refused"], status=503), entry.REFUSED),
    (web.json_response({"error": "x" * 301}, status=503), entry.REFUSED),
    (web.json_response({"error": "two\nlines"}, status=503), entry.REFUSED),
    (web.Response(text="not JSON", status=502), entry.REFUSED),
    # No confirmed answer.
    (TimeoutError("unknown"), "Amplifier's answer was not confirmed. The request was not repeated. Try again."),
    (web.json_response({"ticket": "short"}), entry.UNCONFIRMED)]


@pytest.mark.parametrize("answer,message", OPEN_ANSWERS)
def test_open_says_what_it_knows_and_never_names_itself(home, monkeypatch, answer, message):
    owner_request(home, monkeypatch, answer)
    text = said(entry.owner_browser_url(home, {}))
    assert text == message
    assert "amplifier-unified open" not in text and "Start menu" not in text and TOKEN not in text


def test_open_with_no_host_of_its_version_says_so(home, monkeypatch):
    owner_request(home, monkeypatch, pytest.fail, health=False)
    text = said(entry.owner_browser_url(home, {}))
    assert text == ("No Amplifier of this version and this data folder answered on this PC. "
                    "Make sure that Amplifier is running. If it is updating, wait one minute. Then try again.")
    assert "amplifier-unified open" not in text and "Start menu" not in text


def test_a_wrong_owner_credential_file_gives_its_cause_and_no_step(home, monkeypatch):
    owner_request(home, monkeypatch, pytest.fail)
    monkeypatch.setattr(entry, "read_owner_control_token", lambda *a: "not the stored form")
    for operation in (entry.owner_browser_url(home, {}), entry.owner_browser_code(home, {}, REMOTE)):
        assert said(operation) == "Cannot read the verified private owner credential."


@pytest.mark.parametrize("answer,message", [
    (web.json_response({"error": entry.BUSY}, status=503), "The owner host refused browser entry. "),
    (web.json_response({}, status=409), "The owner host refused browser entry. "),
    (TimeoutError("unknown"), "Browser entry response was not confirmed; no retry was made. "),
    (web.json_response({"code": "short"}), "Browser entry response was not confirmed; no retry was made. ")])
def test_the_typed_code_command_keeps_its_texts(home, monkeypatch, answer, message):
    step = "Make sure that Amplifier is running on this PC. Then run this again: amplifier-unified browsers code."
    owner_request(home, monkeypatch, answer)
    assert said(entry.owner_browser_code(home, {}, REMOTE)) == message + step
    owner_request(home, monkeypatch, pytest.fail, health=False)
    assert said(entry.owner_browser_code(home, {}, REMOTE)) == (
        "The owned local host is not reachable or its identity does not match. " + step)
    owner_request(home, monkeypatch, web.json_response({"error": "Invalid owner entry request. " + step}, status=400))
    assert said(entry.owner_browser_code(home, {}, REMOTE)) == (
        "The running Amplifier does not use this address yet. Stop Amplifier and start it again. "
        "Then run this again: amplifier-unified browsers code.")


def ticket_answer(app, **changes):
    body = issue_body(app, **changes)
    del body["origin"]
    response = call(app, entry.ISSUE_PATH, headers=BEARER, body=body)
    return response.status, json.loads(response.body)["error"]


def code_answer(app, **changes):
    response = call(app, entry.CODE_PATH, headers=BEARER, body=issue_body(app, **changes))
    return response.status, json.loads(response.body)["error"]


def test_the_host_answers_a_ticket_request_with_the_cause_only_and_a_code_request_as_before(host, monkeypatch):
    step = " " + entry.NEW_CODE
    assert ticket_answer(host, version="wrong") == (409, "The owner host identity changed.")
    assert code_answer(host, version="wrong") == (409, "The owner host identity changed." + step)
    broken = call(host, entry.ISSUE_PATH, headers={**BEARER, "Content-Type": "application/json"}, body="{")
    assert (broken.status, json.loads(broken.body)["error"]) == (400, "Invalid owner entry request.")
    assert code_answer(host, origin=LOCAL) == (400, "Invalid owner entry request." + step)
    read, identity = entry.read_owner_control_token, auth.data_identity
    monkeypatch.setattr(entry, "read_owner_control_token", lambda *a: "d" * 64)    # The private credential changed.
    assert ticket_answer(host) == code_answer(host) == (503, "The owner control credential changed.")
    monkeypatch.setattr(entry, "read_owner_control_token", read)

    body = issue_body(host)
    del body["origin"]

    def gone(data_dir):
        raise OSError("The data folder cannot be read")
    monkeypatch.setattr(auth, "data_identity", gone)
    unread = call(host, entry.ISSUE_PATH, headers=BEARER, body=body)
    assert (unread.status, json.loads(unread.body)["error"]) == (
        503, "The private owner data identity is unavailable.")
    monkeypatch.setattr(auth, "data_identity", identity)
    for _ in range(host["browser_entry"].capacity):
        local_ticket(host)
    assert ticket_answer(host) == (
        503, "Amplifier has too many browser openings in progress. Wait one minute. Then try again.")
    host["browser_entry"] = host["browser_codes"] = None
    assert ticket_answer(host) == (503, "Windows owner browser entry is not available.")
    assert code_answer(host) == (503, "Windows owner browser entry is not available." + step)


def test_open_prints_the_real_reason_of_the_real_host(host, monkeypatch):
    """The whole way: the real handler refuses, and `open` says the handler's sentence."""
    for _ in range(host["browser_entry"].capacity):
        local_ticket(host)                                  # Every place for a ticket is taken.
    home = host["service"].data_dir
    owner_request(home, monkeypatch, None)

    def post(self, url, **options):
        return Reply(lambda: send(host, entry.ISSUE_PATH, headers=options["headers"], body=options["json"]))
    monkeypatch.setattr(aiohttp.ClientSession, "post", post)
    assert said(entry.owner_browser_url(home, {})) == entry.BUSY



def test_remote_address_gets_the_code_form_and_the_local_page_is_unchanged(host):
    for at in (LOCAL, "https://localhost:8941", "https://[::1]:8941"):
        local = call(host, "/login", method="GET", at=at, origin=None).text
        assert '<script src="/browser-entry.js"' in local and 'action="/api/browser-entry/redeem"' in local
        assert "one-time-code" not in local and entry.KEY_REDEEM_PATH not in local
    page = call(host, "/login", method="GET", at=REMOTE, origin=None)
    text = page.text
    assert page.status == 200 and page.content_type == "text/html"
    assert "<script" not in text.lower()
    assert f'<title>Sign in \u00b7 {host["service"].instance_branding.snapshot()["name"]}</title>' in text
    # No password is saved: the page has the code form only.
    assert text.count("<form") == 1 and f'<form method="post" action="{entry.CODE_REDEEM_PATH}">' in text
    assert text.count("<input") == 2 and '<link rel="stylesheet" href="/app-pages.css">' in text
    for attribute in ('name="code"', 'autocomplete="one-time-code"', 'autocapitalize="characters"',
                      'spellcheck="false"', " autofocus required>", '<button type="submit">Sign in</button>'):
        assert attribute in text
    assert ("To get a code, run this on the Windows PC that runs Amplifier: "
            "<code>amplifier-unified browsers code</code>.") in text
    # A PC from the setup file has no short command: the page links to the guide's full one.
    assert ("If Windows does not find that command, use the full command from the\n"
            f'<a href="{entry.GUIDE}" target="_blank" rel="noopener noreferrer">'
            "Windows steps of the deployment guide</a>.") in text
    guide = (Path(__file__).resolve().parents[1] / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "\n## Windows host: reach Unified from another device\n" in guide     # The link's target exists.
    assert entry.GUIDE.endswith("/docs/DEPLOYMENT.md#windows-host-reach-unified-from-another-device")
    assert text.count("<a ") == 1 and text.count("http") == 1                   # No other address on the page.
    assert "Type the code here within two minutes." in text and "That code did not work" not in text
    # The page says where a password is set: in the app on the PC, with no command.
    assert ('<p id="password-help">To sign in with a password when you are not at the PC, set the password on the PC '
            'first. In Amplifier on the PC, open Settings, then "Install app", then "Devices &amp; apps". '
            'There, select "Set a password".</p>') in text
    # The field's help is the text that says where the code comes from.
    assert 'aria-describedby="code-help"' in text and text.count('id="code-help"') == 1
    csrf = page.cookies[auth.CSRF_COOKIE]
    assert f'<input type="hidden" name="csrf" value="{csrf.value}">' in text
    assert csrf["httponly"] and csrf["samesite"] == "Strict" and csrf["secure"] and csrf["path"] == "/"
    assert page.headers["Cache-Control"] == "no-store"
    # Not no-referrer: a browser would then post the form with "Origin: null".
    assert page.headers["Referrer-Policy"] == "same-origin"
    again = call(host, "/login?error=%3Cb%3Ereflected&next=%2Freflected", method="GET", at=REMOTE, origin=None,
                 headers={"Cookie": f"{auth.CSRF_COOKIE}={csrf.value}"})
    # A typing mistake needs no new code: the real one is still there.
    assert ("That code did not work. Check it and type it again. A code works for two minutes "
            "and only once. After that, make a new code on the PC.") in again.text
    assert "reflected" not in again.text and "<script" not in again.text.lower()
    assert again.cookies[auth.CSRF_COOKIE].value == csrf.value     # A valid form stays valid.
    # The page's own form signs in.
    assert signed_in(redeem(host, make_code(host), csrf=csrf.value))
    # The host boundary reads "localhost." as this computer; so does the page.
    for at in ("https://localhost.:8941", "https://LOCALHOST:8941"):
        assert "one-time-code" not in call(host, "/login", method="GET", at=at, origin=None).text


def test_other_platforms_keep_the_password_page_and_refuse_both_code_endpoints(host, monkeypatch):
    code = make_code(host)
    monkeypatch.setattr(entry, "is_windows", lambda: False)
    for at in (LOCAL, REMOTE):
        text = call(host, "/login", method="GET", at=at, origin=None).text
        assert 'name="password"' in text and "one-time-code" not in text
    assert call(host, entry.CODE_PATH, headers=BEARER, body=issue_body(host)).status == 503
    assert redeem(host, code).status == 403
    assert len(host["browser_codes"]._codes) == 1
    refused = with_key(host, new_key(host))
    assert refused.status == 403 and refused.text == entry.RELOAD_KEY and host["browser_key"].failures == 0


# The owner's command.

HEALTH = {"ok": True, "app": "amplifier-unified", "version": __version__, "revision": REVISION,
          "instanceId": "b" * 32}


class Reply:
    """A fake pinned HTTPS reply whose body comes from the real handler or a fixed value."""

    def __init__(self, operation):
        self.operation = operation

    async def __aenter__(self):
        self.response = await self.operation()
        self.status = self.response.status
        return self

    async def __aexit__(self, *error):
        pass

    async def json(self):
        return json.loads(self.response.body)


def test_pin_precedes_the_credential_read_for_a_code(home, monkeypatch):
    monkeypatch.setattr(entry, "read_owner_control_token", lambda *a: pytest.fail("Credential read without a pin"))
    with pytest.raises(entry.BrowserEntryUnavailable, match="leaf-pinned HTTPS"):
        asyncio.run(entry.owner_browser_code(home, {"tls": {}}, REMOTE))
    for pin in (None, False, True):
        monkeypatch.setattr(entry, "_owner_probe_targets", lambda *a: ([LOCAL + "/api/health"], pin))
        with pytest.raises(entry.BrowserEntryUnavailable, match="exact SHA-256"):
            asyncio.run(entry.owner_browser_code(home, {}, REMOTE))


def test_owner_code_uses_the_pinned_local_request_and_the_real_handler(host, files, monkeypatch):
    from amplifier_web import update_readiness
    pin = aiohttp.Fingerprint(bytes(range(32)))
    monkeypatch.setattr(entry, "_owner_probe_targets",
                        lambda *a: files.events.append("pin") or ([LOCAL + "/api/health"], pin))
    identity = host["service"].update_manager.running_identity
    monkeypatch.setattr(update_readiness, "running_identity", lambda: identity)
    health = {**HEALTH, "dataIdentity": auth.data_identity(host["service"].data_dir)}
    files.events.clear()

    class Client:
        def __init__(self, **options):
            assert options["trust_env"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *error):
            pass

        def check(self, options):
            # The leaf was pinned before the control credential was read.
            assert files.events[:3] == ["pin", "session-secret", "control-token"]
            assert options["ssl"] is pin and options["allow_redirects"] is False
            assert options["headers"]["Authorization"] == "Bearer " + TOKEN

        def get(self, url, **options):
            self.check(options)
            assert url == LOCAL + "/api/health"
            files.events.append("health")

            async def reply():
                return web.json_response(health)
            return Reply(reply)

        def post(self, url, **options):
            self.check(options)
            assert url == LOCAL + entry.CODE_PATH and options["headers"]["Origin"] == LOCAL
            assert options["json"]["origin"] == REMOTE
            files.events.append("post")
            return Reply(lambda: send(host, entry.CODE_PATH, headers=options["headers"], body=options["json"]))
    monkeypatch.setattr(aiohttp, "ClientSession", Client)
    code = asyncio.run(entry.owner_browser_code(host["service"].data_dir, {}, REMOTE))
    assert files.events.index("health") < files.events.index("post") and files.events.count("post") == 1
    assert signed_in(redeem(host, code))


@pytest.mark.parametrize("reply,message", [
    (TimeoutError("unknown"), "no retry"), (503, "refused"), (409, "refused"),
    (400, "does not use this address yet. Stop Amplifier and start it again"),
    ({"code": "abcde-fghjk"}, "no retry"), ({"ticket": "x" * 43}, "no retry"), (["ABCDEFGHJK"], "no retry")])
def test_owner_code_never_repeats_a_refused_or_unconfirmed_request(home, monkeypatch, reply, message):
    from amplifier_web import update_readiness
    pin = aiohttp.Fingerprint(bytes(range(32)))
    monkeypatch.setattr(entry, "_owner_probe_targets",
                        lambda *a: ([LOCAL + "/api/health", "https://[::1]:8941/api/health"], pin))
    monkeypatch.setattr(entry, "current_owner_sid", lambda: SID)
    monkeypatch.setattr(entry, "read_owner_control_token", lambda *a: TOKEN)
    monkeypatch.setattr(update_readiness, "running_identity", lambda: {"version": __version__, "revision": REVISION})
    health = {**HEALTH, "dataIdentity": auth.data_identity(home)}
    calls = []

    class Client:
        def __init__(self, **options):
            assert options["trust_env"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *error):
            pass

        def get(self, url, **options):
            calls.append("get")

            async def answer():
                return web.json_response(health)
            return Reply(answer)

        def post(self, url, **options):
            calls.append("post")
            assert options["ssl"] is pin and options["allow_redirects"] is False

            async def answer():
                if isinstance(reply, Exception):
                    raise reply
                return web.json_response({}, status=reply) if isinstance(reply, int) else web.json_response(reply)
            return Reply(answer)
    monkeypatch.setattr(aiohttp, "ClientSession", Client)
    with pytest.raises(entry.BrowserEntryUnavailable, match=message) as error:
        asyncio.run(entry.owner_browser_code(home, {}, REMOTE))
    assert calls == ["get", "post"]
    assert "amplifier-unified browsers code" in str(error.value) and TOKEN not in str(error.value)


@pytest.fixture
def command(home, monkeypatch):
    """The real CLI entry; only the saved configuration and the pinned helper are replaced."""
    from amplifier_web import application_generations, deployment, host_dependencies
    monkeypatch.setattr(host_dependencies, "prepare_environment", lambda: None)
    monkeypatch.setattr(application_generations, "delegate", lambda: None)
    # The command itself must never read a credential: only the pinned helper may.
    for owner, name in ((auth, "control_token"), (auth, "session_secret"), (entry, "read_owner_control_token")):
        monkeypatch.setattr(owner, name, lambda *a: pytest.fail("The command read a credential itself"))
    calls = []

    def run(*arguments, origins=(REMOTE,), windows=True, result="ABCDEFGHJK"):
        monkeypatch.setattr(entry, "is_windows", lambda: windows)
        monkeypatch.setattr(deployment, "load_server_config",
                            lambda data_dir, overrides=None: {"public_origins": list(origins)})

        async def helper(data_dir, config, origin):
            calls.append((config["public_origins"], origin))
            if isinstance(result, Exception):
                raise result
            return result
        monkeypatch.setattr(entry, "owner_browser_code", helper)
        monkeypatch.setattr(sys, "argv", ["amplifier-unified", "--data-dir", str(home), "browsers", "code", *arguments])
        return cli.main()
    run.calls = calls
    return run


def test_command_prints_the_address_and_the_code_through_the_pinned_helper_only(command, capsys):
    command()
    assert command.calls == [([REMOTE], REMOTE)]
    assert capsys.readouterr().out == (
        f"Open {REMOTE} on the other device and type this code within two minutes:\n\n"
        "    ABCDE-FGHJK\n\nThe code works once.\n")


def test_command_selects_the_address(command, capsys):
    command(origins=(LOCAL, "https://localhost:8941", PLAIN, OTHER))       # Only one is for other devices.
    command("--origin", OTHER, origins=(REMOTE, OTHER))
    command("--origin", REMOTE.replace("pc.", "PC.") + "/", origins=(REMOTE, OTHER))     # As copied from a browser.
    assert [origin for _, origin in command.calls] == [OTHER, OTHER, REMOTE]
    capsys.readouterr()
    del command.calls[:]
    with pytest.raises(SystemExit) as none:
        command(origins=(LOCAL,))
    assert "amplifier-unified config set public_origins" in str(none.value)
    with pytest.raises(SystemExit) as several:
        command(origins=(REMOTE, OTHER))
    assert REMOTE in str(several.value) and OTHER in str(several.value) and "--origin" in str(several.value)
    for given in (UNLISTED, LOCAL, REMOTE + "/login", "not an address"):
        with pytest.raises(SystemExit) as unknown:
            command("--origin", given, origins=(REMOTE, LOCAL))
        assert REMOTE in str(unknown.value) and "--origin" in str(unknown.value)
    assert command.calls == [] and capsys.readouterr().out == ""


def test_command_refusals_are_plain_and_print_no_code(command, capsys):
    with pytest.raises(SystemExit) as stopped:
        command(windows=False)
    assert str(stopped.value) == "On this computer, sign in with the host account password on the sign-in page."
    assert command.calls == []
    with pytest.raises(SystemExit) as stopped:
        command(result=entry.BrowserEntryUnavailable("The host did not answer."))
    assert str(stopped.value) == "The host did not answer."
    with pytest.raises(SystemExit) as stopped:
        command(result=OSError("ABCDEFGHJK"))
    assert "ABCDEFGHJK" not in str(stopped.value) and "amplifier-unified browsers code" in str(stopped.value)
    assert capsys.readouterr().out == ""


def test_host_serves_both_code_endpoints():
    # This file must not import the server module; its two routes are checked as text.
    source = (Path(entry.__file__).parent / "server.py").read_text(encoding="utf-8")
    assert "app.router.add_post(browser_entry.CODE_PATH, browser_entry.code_issue)" in source
    assert "app.router.add_post(browser_entry.CODE_REDEEM_PATH, browser_entry.code_redeem)" in source
    assert "app.router.add_post(browser_entry.KEY_REDEEM_PATH, browser_entry.key_redeem)" in source


def test_command_line_names_both_operations(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "browsers", "code", "--origin", REMOTE])
    args = cli._parse()
    assert (args.command, args.browsers_command, args.origin) == ("browsers", "code", REMOTE)
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "browsers", "sign-out"])
    assert cli._parse().origin is None and cli._parse().remove is False
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "browsers", "key", "--remove"])
    args = cli._parse()
    assert (args.command, args.browsers_command, args.remove) == ("browsers", "key", True)


# The saved sign-in key.

def new_key(app):
    """A key, as the owner's command makes it on the PC."""
    return entry.make_key(app["service"].data_dir)[0]


async def typed_key(app, key, *, at=REMOTE, csrf=None, headers=None, body=None, **options):
    """Post the key form at a remote address, as the page on the other device does."""
    csrf = csrf or auth.new_csrf(app["session_secret"])
    return await send(app, entry.KEY_REDEEM_PATH, at=at, headers={
        "Content-Type": FORM, "Cookie": f"{auth.CSRF_COOKIE}={csrf}", **(headers or {})},
        body=body or urlencode({"csrf": csrf, "password": key}), **options)


def with_key(*args, **options):
    return asyncio.run(typed_key(*args, **options))


def wrong_key(response):
    return (response.status == 303 and response.headers["Location"] == "/login?error=password"
            and auth.SESSION_COOKIE not in response.cookies)


def other_key(key):
    """The key with another first symbol."""
    return next(symbol for symbol in entry.CODE_ALPHABET if symbol != key[0]) + key[1:]


def test_key_has_125_bits_and_its_record_holds_only_the_hash(host):
    home = host["service"].data_dir
    assert auth.read_sign_in_key(home) is None              # A new installation has no key.
    key, replaced = entry.make_key(home)
    assert re.fullmatch(f"[{entry.CODE_ALPHABET}]{{25}}", key) and replaced is False
    assert len(entry.CODE_ALPHABET) ** entry.KEY_LENGTH == 2 ** 125
    assert entry.SavedKey.display("ABCDEFGHJKLMNPQRSTUVWXYZ2") == "ABCDE-FGHJK-LMNPQ-RSTUV-WXYZ2"
    record = auth._sign_in_key_path(home).read_bytes()
    made, digest = record.decode("ascii").split()
    assert record.endswith(b"\n") and digest == hashlib.sha256(key.encode()).hexdigest()
    assert key.encode() not in record and abs(int(made) - auth.time.time()) < 5
    assert auth.read_sign_in_key(home) == (int(made), digest)
    if os.name != "nt":
        assert os.stat(auth._sign_in_key_path(home)).st_mode & 0o777 == 0o600
    assert len({entry.make_key(home)[0] for _ in range(20)}) == 20


@pytest.mark.parametrize("peer", ["127.0.0.1", "100.101.102.103"])
def test_key_signs_in_at_every_remote_address_again_and_again(host, peer):
    """A Tailscale TCP forward connects from 127.0.0.1; a LAN browser does not."""
    key = new_key(host)
    assert call(host, "/api/protected", method="GET", at=REMOTE, origin=None).status == 401
    for at, typed_text in ((REMOTE, key), (OTHER, entry.SavedKey.display(key).lower()), (REMOTE, " ".join(key))):
        response = with_key(host, typed_text, at=at, peer=peer)
        assert signed_in(response), at
        assert response.headers["Cache-Control"] == "no-store" and response.headers["Referrer-Policy"] == "no-referrer"
        cookie = response.cookies[auth.SESSION_COOKIE]
        assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Strict" and cookie["path"] == "/"
        assert int(cookie["max-age"]) == 600 and response.cookies[auth.CSRF_COOKIE].value == ""
        assert auth.valid_session(host, cookie.value) and session_payload(cookie.value)["via"] == "remote"
        assert call(host, "/api/protected", method="GET", at=at, origin=None,
                    headers={"Cookie": f"{auth.SESSION_COOKIE}={cookie.value}"}).status == 200
    assert host["browser_key"].failures == 0


def test_wrong_key_costs_no_private_read_shows_its_error_and_logs_only_a_count(host, monkeypatch, capsys):
    key = new_key(host)
    reads, read = [], auth.read_sign_in_key
    monkeypatch.setattr(auth, "read_sign_in_key", lambda data_dir: reads.append(1) or read(data_dir))
    guess = other_key(key)
    response = with_key(host, guess)
    assert wrong_key(response) and auth.CSRF_COOKIE not in response.cookies
    assert response.headers["Cache-Control"] == "no-store" and response.headers["Referrer-Policy"] == "no-referrer"
    assert capsys.readouterr().out == "Remote sign-in: 1 wrong passwords or keys since start.\n"
    assert len(reads) == 1                                  # The host's first look at the record.
    host["browser_key"].failures = 95
    code = make_code(host)
    # Any device that reaches the address can send wrong keys; each stays cheap.
    for attempt in (guess, guess.lower(), "O" + key[1:], key[:-1], key + "2", code, "not a key", "", "é" * 25):
        assert wrong_key(with_key(host, attempt)), attempt
    assert capsys.readouterr().out == "Remote sign-in: 100 wrong passwords or keys since start.\n"
    assert len(reads) == 1 and host["browser_key"].failures == 104
    # No lockout and no delay: after all these the saved key still works, and a code is no key.
    assert signed_in(with_key(host, key)) and signed_in(redeem(host, code))
    printed = capsys.readouterr()
    assert printed.out == "Remote sign-in: a browser signed in with the saved password or key.\n"
    assert key not in printed.out + printed.err and guess not in printed.out + printed.err
    assert wrong(redeem(host, key))                         # And the key is no code.


def test_no_key_and_a_removed_key_open_nothing(host):
    home = host["service"].data_dir
    assert wrong_key(with_key(host, "ABCDEFGHJKLMNPQRSTUVWXYZ2"))      # No key is saved.
    assert entry.remove_key(home) is False and not auth._sign_in_key_path(home).exists()
    key = new_key(host)
    assert signed_in(with_key(host, key))
    assert entry.remove_key(home) is True
    assert auth._sign_in_key_path(home).read_bytes() == b"none\n" and auth.read_sign_in_key(home) is None
    assert wrong_key(with_key(host, key)) and entry.remove_key(home) is False


def test_a_new_key_ends_the_earlier_one_at_the_next_request(host):
    home = host["service"].data_dir
    first = new_key(host)
    session = with_key(host, first).cookies[auth.SESSION_COOKIE].value
    second, replaced = entry.make_key(home)
    assert replaced is True and second != first
    assert wrong_key(with_key(host, first)) and signed_in(with_key(host, second))
    assert auth.valid_session(host, session)                # A browser that is signed in stays signed in.


def clock(monkeypatch, start):
    """One clock for the host and the owner's commands; a pause moves it."""
    now = [start]
    monkeypatch.setattr(auth.time, "time", lambda: now[0])
    monkeypatch.setattr(auth.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    return now


def test_sign_out_of_all_browsers_ends_the_saved_key(host, monkeypatch):
    home = host["service"].data_dir
    now = clock(monkeypatch, 1_900_000_100)
    key = new_key(host)
    session = with_key(host, key).cookies[auth.SESSION_COOKIE].value
    now[0] += 10
    assert auth.valid_session(host, session)
    auth.sign_out_all(home)                                 # The owner's command, in another process.
    # The running host has not applied it yet: the key opens nothing, and that is not a wrong key.
    assert wrong_key(with_key(host, key)) and host["browser_key"].failures == 0
    assert auth.refresh_sessions(host) is True and not auth.valid_session(host, session)
    now[0] += 30
    assert wrong_key(with_key(host, key)) and host["browser_key"].failures == 1     # The key ended with the sign-out.
    assert auth.read_sign_in_key(home) is not None          # Its record is still there, and of no use.
    later, replaced = entry.make_key(home)
    assert replaced is False and signed_in(with_key(host, later))


def test_key_is_made_in_a_second_after_the_saved_sign_out(host, monkeypatch):
    home = host["service"].data_dir
    now = clock(monkeypatch, 1_900_000_200)
    auth.sign_out_all(home)
    assert auth.refresh_sessions(host) is True and host["sessions"].epoch == 1_900_000_200
    key = new_key(host)                                     # In the second of the sign-out: the command waits.
    assert auth.read_sign_in_key(home)[0] == 1_900_000_201 and signed_in(with_key(host, key))
    now[0] = 1_900_000_150                                  # The clock of the PC was set back.
    monkeypatch.setattr(auth.time, "sleep", lambda seconds: None)
    with pytest.raises(entry.BrowserEntryUnavailable, match="clock"):
        entry.make_key(home)
    assert auth.read_sign_in_key(home)[0] == 1_900_000_201  # Nothing was saved.
    assert wrong_key(with_key(host, key))                   # A key from a later second than the present opens nothing.


@pytest.mark.parametrize("made,works", [(1_900_000_300, False), (1_900_000_301, True),
                                        (1_900_000_400, True), (1_900_000_401, False)])
def test_key_counts_from_the_second_after_the_applied_sign_out_to_the_present_second(host, monkeypatch, made, works):
    """The rule of a session, for the second in which the key was made."""
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_400)
    host["sessions"].epoch = 1_900_000_300
    key = "ABCDEFGHJKLMNPQRSTUVWXYZ2"
    auth.write_sign_in_key(host["service"].data_dir, (made, hashlib.sha256(key.encode()).hexdigest()))
    assert signed_in(with_key(host, key)) is works


def test_record_that_cannot_be_read_is_no_key_and_is_read_again(host, monkeypatch):
    home = host["service"].data_dir
    key = new_key(host)
    read, look, reads = auth.read_sign_in_key, auth.sign_in_key_hint, []

    def fails(data_dir):
        reads.append(1)
        raise OSError("in use")
    monkeypatch.setattr(auth, "read_sign_in_key", fails)
    assert wrong_key(with_key(host, key)) and wrong_key(with_key(host, other_key(key))) and len(reads) == 2
    monkeypatch.setattr(auth, "read_sign_in_key", read)
    assert signed_in(with_key(host, key))                   # Nothing was kept from the reads that failed.
    monkeypatch.setattr(auth, "sign_in_key_hint", lambda data_dir: auth._UNSEEN)
    assert wrong_key(with_key(host, key))                   # A look that failed finds no key.
    monkeypatch.setattr(auth, "sign_in_key_hint", look)
    assert signed_in(with_key(host, key))
    # A malformed record is an error for its reader. For the host it is no key, and the host
    # does not read it again until the file changes.
    auth.write_sign_in_key(home, ("soon", "0" * 64))
    with pytest.raises(ValueError):
        auth.read_sign_in_key(home)
    del reads[:]
    monkeypatch.setattr(auth, "read_sign_in_key", lambda data_dir: reads.append(1) or read(data_dir))
    assert wrong_key(with_key(host, key)) and wrong_key(with_key(host, other_key(key))) and len(reads) == 1
    monkeypatch.setattr(auth, "read_sign_in_key", read)
    for content in (b"12 " + b"a" * 63 + b"\n", b"12 " + b"A" * 64 + b"\n", b"1" * 13 + b" " + b"a" * 64 + b"\n",
                    b"12 " + b"a" * 64 + b"\n\n", b"none", b""):
        assert not auth._SIGN_IN_KEY_RECORD.fullmatch(content), content
    # The longest record that is read has 125 bytes: a password's. A longer file is malformed, whatever it begins with.
    digest = hashlib.sha256(key.encode()).hexdigest()
    longest = entry._SCRYPT + "a" * 32 + " " + digest
    auth.write_sign_in_key(home, (999_999_999_999, longest))
    assert len(auth._sign_in_key_path(home).read_bytes()) == 125 and auth.read_sign_in_key(home)[1] == longest
    auth.write_sign_in_key(home, (auth.read_sign_in_key(home)[0], digest + "\n" + "a" * 100))
    with pytest.raises(ValueError):
        auth.read_sign_in_key(home)
    assert wrong_key(with_key(host, key))
    assert entry.remove_key(home) is True and auth.read_sign_in_key(home) is None      # Such a record is replaced too.


def test_record_that_is_replaced_between_the_look_and_the_read_leaves_no_earlier_key(host, monkeypatch):
    """The host looks at the file, then reads it. A key that is made in between counts for that request."""
    home = host["service"].data_dir
    first = new_key(host)
    assert signed_in(with_key(host, first))                 # The host has read the record of the first key.
    second = new_key(host)
    read, made = auth.read_sign_in_key, {}

    def replaced_before_the_read(data_dir):
        if not made:
            made["started"] = True
            made["third"] = entry.make_key(data_dir)[0]     # The owner's command, after the host's look.
        return read(data_dir)
    monkeypatch.setattr(auth, "read_sign_in_key", replaced_before_the_read)
    assert wrong_key(with_key(host, second))                # The record that was read is the third key's.
    monkeypatch.setattr(auth, "read_sign_in_key", read)
    assert wrong_key(with_key(host, first)) and wrong_key(with_key(host, second))
    assert signed_in(with_key(host, made["third"]))


KEY_REFUSED = {name: REFUSED[name] for name in (
    "loopback Host", "localhost Host", "Host that is not a configured address", "configured address that is not HTTPS",
    "no Origin", "another Origin", "opaque Origin", "plain HTTP", "plain HTTP without Origin",
    "no CSRF cookie", "unsigned CSRF", "expired CSRF", "a session used as CSRF",
    "JSON content type", "multipart content type")}
KEY_REFUSED.update({
    "form CSRF is not the cookie's": lambda app, key: {"body": fields(app, password=key)["body"]},
    "no password field": lambda app, key: fields(app),
    "a code field in place of the password": lambda app, key: fields(app, code=key),
    "the field name of an earlier version": lambda app, key: fields(app, key=key),
    "extra field": lambda app, key: fields(app, password=key, next="/"),
    "duplicate field": lambda app, key: {
        name: value + ("&password=" + key if name == "body" else "")
        for name, value in fields(app, password=key).items()},
})


@pytest.mark.parametrize("options", KEY_REFUSED.values(), ids=[name.replace(" ", "-") for name in KEY_REFUSED])
def test_failed_precondition_is_refused_before_the_key_record_is_looked_at(host, monkeypatch, options):
    key = new_key(host)
    monkeypatch.setattr(auth, "sign_in_key_hint", lambda data_dir: pytest.fail("The record was looked at"))
    response = with_key(host, key, **options(host, key))
    assert response.status == 403 and auth.SESSION_COOKIE not in response.cookies
    assert host["browser_key"].failures == 0                # Not counted as a wrong key.


def test_oversize_key_form_and_a_host_that_cannot_be_confirmed_open_nothing(host, monkeypatch):
    key = new_key(host)
    assert with_key(host, key, body="csrf=" + "x" * 4096 + "&password=" + key).status == 413
    read = entry.read_owner_control_token
    monkeypatch.setattr(entry, "read_owner_control_token", lambda *a: "d" * 64)    # The private credential changed.
    assert wrong_key(with_key(host, key)) and host["browser_key"].failures == 0
    monkeypatch.setattr(entry, "read_owner_control_token", read)
    host["service"].update_manager.running_identity["instanceId"] = "e" * 32
    assert signed_in(with_key(host, key))                   # Unlike a code, the key outlasts a restart of the host.


def test_with_remote_access_off_the_key_opens_nothing(host):
    """The default: only this computer's own addresses are configured."""
    key = new_key(host)
    for origins in ([LOCAL, "https://localhost:8941"], []):
        host["server_config"]["public_origins"] = origins
        for at in (REMOTE, OTHER, UNLISTED, LOCAL, "https://localhost:8941"):
            response = with_key(host, key, at=at)
            assert response.status == 403 and auth.SESSION_COOKIE not in response.cookies
    assert host["browser_key"].failures == 0


def test_key_form_is_refused_when_the_host_has_no_key_store(host):
    key = new_key(host)
    host["browser_key"] = None
    response = with_key(host, key)
    assert response.status == 403 and response.text == entry.RELOAD_KEY and auth.SESSION_COOKIE not in response.cookies



def test_remote_page_has_the_password_form_only_while_a_password_or_key_is_saved(host):
    home = host["service"].data_dir

    def remote(path="/login"):
        return call(host, path, method="GET", at=REMOTE, origin=None)
    assert entry.KEY_REDEEM_PATH not in remote().text and 'type="password"' not in remote().text
    entry.set_password(home, PASSWORD)
    page = remote()
    text = page.text
    assert text.count("<form") == 2 and f'<form method="post" action="{entry.KEY_REDEEM_PATH}">' in text
    for attribute in ('<label for="password">Password</label>', 'id="password" name="password" type="password"',
                      'autocomplete="current-password"', 'aria-describedby="password-help"',
                      '<button type="submit">Sign in</button>', '<button type="submit">Sign in with code</button>'):
        assert attribute in text
    # The password field has the focus, and a password manager finds one password field.
    assert text.count("autofocus") == 1 and text.index('id="password"') < text.index("autofocus") < text.index('id="code"')
    assert text.count('type="password"') == 1 and "<script" not in text.lower()
    csrf = page.cookies[auth.CSRF_COOKIE].value
    assert text.count(f'<input type="hidden" name="csrf" value="{csrf}">') == 2      # One in each form.
    assert ('<p id="password-help">Type the password that you set in Amplifier on the PC, or the sign-in key that a '
            'command on the PC showed. To change the password: In Amplifier on the PC, open Settings, then '
            '"Install app", then "Devices &amp; apps". There, select "Set a password".</p>') in text
    assert text.count('id="password-help"') == 1 and "did not work" not in text and PASSWORD not in text
    again = remote("/login?error=password").text
    assert ("That password did not work. Check it and type it again. A password stops working when you change it "
            "or remove it on the PC, or sign out all browsers there.") in again and "That code did not work" not in again
    # A password that was not checked has its own texts; nothing of the request is on the page.
    assert "The password was not checked. Type it again." in remote("/login?error=wait").text
    host["browser_key"]._until = host["browser_key"].clock() + 119.5
    assert ("Too many wrong passwords were typed. No password is checked for 2 minutes. Then type the password "
            "again. A code from the PC works now.") in remote("/login?error=wait").text
    host["browser_key"]._until = host["browser_key"].clock() + 0.5
    assert "No password is checked for 1 second." in remote("/login?error=wait").text
    host["browser_key"]._until = 0.0
    # The page's own form signs in.
    assert signed_in(with_key(host, PASSWORD, csrf=csrf))
    # A key from the command has the same form. With nothing saved, the form is gone.
    key = new_key(host)
    assert f'action="{entry.KEY_REDEEM_PATH}"' in remote().text and signed_in(with_key(host, key))
    assert entry.remove_key(home) is True and entry.KEY_REDEEM_PATH not in remote().text
    # The page of this computer never has the form.
    entry.set_password(home, PASSWORD)
    assert entry.KEY_REDEEM_PATH not in call(host, "/login", method="GET", at=LOCAL, origin=None).text


def test_real_https_sign_in_with_the_saved_key_at_the_remote_address(host, tmp_path):
    """Real sockets, real TLS and the real middleware; the listener is on loopback, as behind a Tailscale forward."""
    from cryptography.hazmat.primitives import serialization
    from amplifier_web import deployment, windows_tls
    config = deployment.validate_server({"public_origins": [LOCAL, REMOTE]})
    root, root_key = windows_tls._new_root(config)
    cert, key_file = windows_tls._encoded_pair(*windows_tls._new_local_leaf(root, root_key, config))
    (tmp_path / "leaf.crt").write_bytes(cert)
    (tmp_path / "leaf.key").write_bytes(key_file)
    serving = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    serving.load_cert_chain(tmp_path / "leaf.crt", tmp_path / "leaf.key")
    trusting = ssl.create_default_context(cadata=root.public_bytes(serialization.Encoding.PEM).decode())
    served = web.Application(middlewares=[auth.auth_required])
    for name in ("control_token", "session_secret", "server_config", "allowed_origins", "service",
                 "browser_entry", "browser_codes", "browser_key", "sessions"):
        served[name] = host[name]
    served.router.add_post(entry.KEY_REDEEM_PATH, entry.key_redeem)
    served.router.add_get("/login", auth.login_page)

    async def protected(request):
        return web.json_response({"authenticated": True})
    served.router.add_get("/api/protected", protected)
    home, remote_host = host["service"].data_dir, REMOTE.split("://", 1)[1]

    async def run():
        runner = web.AppRunner(served)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=serving)
        await site.start()
        base = f"https://127.0.0.1:{runner.addresses[0][1]}"
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as client:
                def at_remote(method, path, **options):
                    headers = {"Host": remote_host, **options.pop("headers", {})}
                    return client.request(method, base + path, ssl=trusting, server_hostname="pc.tail1234.ts.net",
                                          allow_redirects=False, headers=headers, **options)

                async def sign_in(typed_text, form=True):
                    async with at_remote("GET", "/login") as page:
                        csrf = page.cookies[auth.CSRF_COOKIE].value
                        assert (f'action="{entry.KEY_REDEEM_PATH}"' in await page.text()) is form
                    form = {"Origin": REMOTE, "Content-Type": FORM, "Cookie": f"{auth.CSRF_COOKIE}={csrf}"}
                    async with at_remote("POST", entry.KEY_REDEEM_PATH, headers=form,
                                         data=urlencode({"csrf": csrf, "password": typed_text})) as response:
                        return response.status, response.headers["Location"], response.cookies.get(auth.SESSION_COOKIE)

                async def signed(cookie):
                    async with at_remote("GET", "/api/protected", headers={
                            "Cookie": f"{auth.SESSION_COOKIE}={cookie.value}"}) as response:
                        return response.status
                key = entry.make_key(home)[0]                # The owner's command at the PC.
                assert await sign_in(other_key(key)) == (303, "/login?error=password", None)
                # The same form at the local address is not a remote sign-in.
                csrf = auth.new_csrf(SECRET)
                async with client.post(base + entry.KEY_REDEEM_PATH, ssl=trusting, allow_redirects=False,
                                       data=urlencode({"csrf": csrf, "password": key}), headers={
                                           "Host": LOCAL.split("://", 1)[1], "Origin": LOCAL, "Content-Type": FORM,
                                           "Cookie": f"{auth.CSRF_COOKIE}={csrf}"}) as local:
                    assert local.status == 403
                status, location, cookie = await sign_in(entry.SavedKey.display(key))
                assert (status, location) == (303, "/") and cookie["secure"] and cookie["httponly"]
                assert await signed(cookie) == 200
                # The key is not used up: a second browser signs in with it.
                status, location, second = await sign_in(key)
                assert (status, location) == (303, "/") and second.value != cookie.value and await signed(second) == 200
                # The owner signs out all browsers at the PC. The host applies it: both browsers are signed
                # out, and the key opens nothing.
                auth.sign_out_all(home)
                assert await sign_in(key) == (303, "/login?error=password", None)
                assert auth.refresh_sessions(served) is True
                assert await signed(cookie) == 401 and await signed(second) == 401
                # The page no longer offers the form; a form from before opens nothing.
                assert await sign_in(key, form=False) == (303, "/login?error=password", None)
        finally:
            await runner.cleanup()
    asyncio.run(run())


# The owner's command for the key.

@pytest.fixture
def key_command(home, monkeypatch):
    """The real CLI entry and the real record; only the saved configuration is replaced."""
    from amplifier_web import application_generations, deployment, host_dependencies
    monkeypatch.setattr(host_dependencies, "prepare_environment", lambda: None)
    monkeypatch.setattr(application_generations, "delegate", lambda: None)
    # The command needs no credential: the owner's private folder is its authority.
    for owner, name in ((auth, "control_token"), (auth, "session_secret"), (entry, "read_owner_control_token")):
        monkeypatch.setattr(owner, name, lambda *a: pytest.fail("The command read a credential"))

    def run(*arguments, origins=(REMOTE, OTHER), windows=True, command="key"):
        monkeypatch.setattr(entry, "is_windows", lambda: windows)
        monkeypatch.setattr(deployment, "load_server_config",
                            lambda data_dir, overrides=None: {"public_origins": list(origins)})
        monkeypatch.setattr(sys, "argv", ["amplifier-unified", "--data-dir", str(home), "browsers", command, *arguments])
        return cli.main()
    return run


def test_key_command_shows_a_new_key_once_and_saves_only_its_record(key_command, home, capsys):
    key_command()
    out = capsys.readouterr().out
    shown = re.search(f"^    ((?:[{entry.CODE_ALPHABET}]{{5}}-){{4}}[{entry.CODE_ALPHABET}]{{5}})$", out, re.M)[1]
    assert out == (
        "Save this sign-in key now, for example in a password manager. Amplifier does not show it again.\n\n"
        f"    {shown}\n\n"
        'On the other device, open Amplifier and type the key in the field "Sign-in key". '
        f"Addresses for other devices:\n  {REMOTE}\n  {OTHER}\n"
        'Amplifier must be running. It uses a new address only after the steps "Turn remote access on" '
        "of the deployment guide.\n"
        "To set a password of your own instead, use the app on the PC: Open Settings, then \"Install app\", "
        "then \"Devices & apps\". There, select \"Set a password\".\n"
        "The key works until you make a new key, set a password, remove it (amplifier-unified browsers key --remove) "
        "or sign out all browsers.\n")
    digest = auth.read_sign_in_key(home)[1]
    assert digest == hashlib.sha256(shown.replace("-", "").encode()).hexdigest()
    key_command(origins=(LOCAL, PLAIN, OTHER))              # Only one address is for other devices.
    again = capsys.readouterr().out
    assert again.endswith("The earlier key or password no longer works.\n") and shown not in again and f"  {OTHER}\n" in again
    assert REMOTE not in again and LOCAL not in again and PLAIN not in again
    assert auth.read_sign_in_key(home)[1] != digest


def test_key_command_removes_the_key_and_the_sign_out_command_ends_it(key_command, home, monkeypatch, capsys):
    key_command("--remove")
    assert capsys.readouterr().out == "No sign-in key or password was saved.\n"
    key_command()
    capsys.readouterr()
    key_command("--remove")
    assert capsys.readouterr().out == (
        "The saved sign-in key or password is removed. Browsers that are signed in stay signed in.\n")
    assert auth.read_sign_in_key(home) is None and auth.session_epoch(home) == 0       # No browser was signed out.
    key_command()
    capsys.readouterr()
    signed_out = ("All browsers on all devices are signed out. A running Amplifier applies this within a few seconds.\n"
                  "To use Amplifier in a browser again, open it from this computer.\n")
    key_command(command="sign-out")
    assert capsys.readouterr().out == signed_out + (
        "The saved sign-in key or password no longer works. Set a new password in the app on the PC "
        "when you need one.\n")
    # The saved second ends the key, and the record is gone as well: the key cannot come back with the clock.
    assert auth.read_sign_in_key(home) is None and auth.session_epoch(home) > 0
    key_command(command="sign-out")
    assert capsys.readouterr().out == signed_out            # No key: no line about one.
    key_command()                                           # In a second after the sign-out.
    assert "no longer works." not in capsys.readouterr().out
    later = auth.read_sign_in_key(home)
    assert later[0] > auth.session_epoch(home)
    # A removal that is not confirmed is said, with the command that removes the key.
    write = auth.write_sign_in_key

    def fails(*arguments):
        raise OSError("ABCDE")

    def fails_after_the_save(*arguments):
        write(*arguments)
        raise OSError("ABCDE")
    # A host that is not a Windows host has no key and no key command: its sign-out names neither.
    remove = entry.remove_key
    monkeypatch.setattr(entry, "remove_key", lambda data_dir: pytest.fail("The key's record was looked at"))
    key_command(command="sign-out", windows=False)
    assert capsys.readouterr().out == signed_out and auth.read_sign_in_key(home) == later
    monkeypatch.setattr(entry, "remove_key", remove)
    for writer, record in ((fails, later), (fails_after_the_save, None)):
        monkeypatch.setattr(auth, "write_sign_in_key", writer)
        key_command(command="sign-out")                     # The sign-out itself is done: no error exit.
        assert capsys.readouterr().out == signed_out + (
            "Amplifier could not confirm that the saved sign-in key or password is removed. Run this: "
            "amplifier-unified browsers key --remove.\n")
        assert auth.read_sign_in_key(home) == record


def test_sign_out_command_ends_a_key_from_a_second_that_the_clock_has_not_reached_again(
        key_command, home, monkeypatch, capsys):
    """The clock of the PC was ahead when the key was made, or it was set back later."""
    now = clock(monkeypatch, 1_900_000_500)
    key_command()
    key = re.search(f"^    ((?:[{entry.CODE_ALPHABET}]{{5}}-){{4}}[{entry.CODE_ALPHABET}]{{5}})$",
                    capsys.readouterr().out, re.M)[1]
    record = auth.read_sign_in_key(home)
    app = {"service": SimpleNamespace(data_dir=home), "sessions": auth.Sessions.load(home)}
    saved = entry.SavedKey()
    def accepts():
        return asyncio.run(saved.accepts(app, key))
    assert record[0] == 1_900_000_500 and accepts() is True
    now[0] = 1_900_000_100                                  # The clock was set back.
    assert accepts() is False                               # A key from a later second opens nothing now.
    key_command(command="sign-out")
    assert "The saved sign-in key or password no longer works." in capsys.readouterr().out
    assert auth.refresh_sessions(app) is True and app["sessions"].epoch < record[0]    # An earlier second than the key's.
    now[0] = 1_900_000_600                                  # The clock reaches the second of the key again.
    assert auth.read_sign_in_key(home) is None and accepts() is False
    # The rule alone reads the clock: with the record still there, the key would be back.
    auth.write_sign_in_key(home, record)
    assert accepts() is True


def test_key_command_refusals_are_plain_and_change_nothing(key_command, home, monkeypatch, capsys):
    with pytest.raises(SystemExit) as stopped:
        key_command(windows=False)
    assert str(stopped.value) == "On this computer, sign in with the host account password on the sign-in page."
    with pytest.raises(SystemExit) as stopped:
        key_command(origins=(LOCAL, PLAIN))
    assert "amplifier-unified config set public_origins" in str(stopped.value)
    for command in ("code", "sign-out"):
        with pytest.raises(SystemExit) as stopped:
            key_command("--remove", command=command)
        assert str(stopped.value) == "--remove is only valid with browsers key."
    assert auth.read_sign_in_key(home) is None and auth.session_epoch(home) == 0
    key_command()
    record = auth.read_sign_in_key(home)
    capsys.readouterr()

    write = auth.write_sign_in_key

    def fails(*arguments):
        raise OSError("ABCDE")

    def fails_after_the_save(*arguments):
        write(*arguments)                                   # As a save that fails when the record is in place.
        raise OSError("ABCDE")
    texts = {(): "Amplifier could not confirm that a new sign-in key is saved",
             ("--remove",): "Amplifier could not confirm that the sign-in key or password is removed"}
    for writer in (fails, fails_after_the_save):
        monkeypatch.setattr(auth, "write_sign_in_key", writer)
        for arguments, phrase in texts.items():
            with pytest.raises(SystemExit) as stopped:
                key_command(*arguments)
            said = str(stopped.value)
            assert said.startswith(phrase) and str(home) in said and "Run the command again." in said
            # No sentence says that nothing changed, and none says what works: neither is known.
            said = said.replace(str(home), "")
            assert "ABCDE" not in said and "NOT" not in said and "Nothing was changed" not in said
        assert capsys.readouterr().out == ""                # No key was shown.
        changed = auth.read_sign_in_key(home) != record
        assert changed is (writer is fails_after_the_save)
    # A save that leaves another record than the one that was written is not confirmed either.
    for arguments, phrase in texts.items():
        monkeypatch.setattr(auth, "write_sign_in_key", lambda *a: None)
        before = auth.read_sign_in_key(home)
        if arguments and before is None:
            write(home, record)                             # A removal needs a record to remove.
            before = record
        with pytest.raises(SystemExit) as stopped:
            key_command(*arguments)
        assert str(stopped.value).startswith(phrase) and capsys.readouterr().out == ""
        assert auth.read_sign_in_key(home) == before
    # A save that cannot be read back is not confirmed either.
    monkeypatch.setattr(auth, "write_sign_in_key", write)
    key_command()
    capsys.readouterr()
    read = auth.read_sign_in_key
    for arguments, phrase in texts.items():
        looks = []

        def unread_after_the_save(data_dir):
            looks.append(1)
            if len(looks) > 1:
                raise OSError("ABCDE")
            return read(data_dir)
        monkeypatch.setattr(auth, "read_sign_in_key", unread_after_the_save)
        with pytest.raises(SystemExit) as stopped:
            key_command(*arguments)
        assert str(stopped.value).startswith(phrase) and capsys.readouterr().out == ""
        monkeypatch.setattr(auth, "read_sign_in_key", read)


# The password that the owner sets in the app on the PC.

def waits(response):
    """The password was not checked: a wait, or another check that runs."""
    return (response.status == 303 and response.headers["Location"] == "/login?error=wait"
            and auth.SESSION_COOKIE not in response.cookies)


def test_password_record_holds_a_salt_and_a_scrypt_hash_never_the_password(host):
    home = host["service"].data_dir
    assert host["browser_key"].current(host) is None
    assert entry.set_password(home, PASSWORD) is False      # It replaced nothing.
    record = auth._sign_in_key_path(home).read_bytes()
    made, name, n, r, p, salt, digest = record.decode("ascii").split()
    assert re.fullmatch(rb"[0-9]{10} scrypt 15 8 1 [0-9a-f]{32} [0-9a-f]{64}\n", record) and len(record) <= 125
    assert PASSWORD.encode() not in record and abs(int(made) - auth.time.time()) < 5
    assert digest == hashlib.scrypt(PASSWORD.encode(), salt=bytes.fromhex(salt), n=2 ** 15, r=8, p=1,
                                    maxmem=64 * 1024 * 1024, dklen=32).hex()
    assert host["browser_key"].current(host) == (int(made), "password")
    if os.name != "nt":
        assert os.stat(auth._sign_in_key_path(home)).st_mode & 0o777 == 0o600
    # The same password again has another salt and replaces the first record.
    assert entry.set_password(home, PASSWORD) is True and salt.encode() not in auth._sign_in_key_path(home).read_bytes()
    saved = auth.read_sign_in_key(home)
    for refused, message in (("eleven char", "Use at least 12 characters."), ("", "Use at least 12 characters."),
                             ("x" * 257, "Use at most 256 characters."), (None, "Use at least 12 characters."),
                             (PASSWORD.encode(), "Use at least 12 characters.")):
        with pytest.raises(entry.PasswordRefused, match=re.escape(message)):
            entry.set_password(home, refused)
    with pytest.raises(entry.PasswordRefused, match="a character that cannot be saved"):
        entry.set_password(home, "half of a pair \ud800")
    assert auth.read_sign_in_key(home) == saved             # A refused text changes nothing.
    # Twelve characters are enough, and 256 are not too many.
    assert entry.set_password(home, "twelve chars") is True and signed_in(with_key(host, "twelve chars"))
    assert entry.set_password(home, "x" * 256) is True and signed_in(with_key(host, "x" * 256))
    # A record with other parameters is malformed: the host makes no hash at a cost that a file names.
    made = auth.read_sign_in_key(home)[0]
    for value in ("scrypt 20 8 1 " + "0" * 32 + " " + "0" * 64, "scrypt 15 8 2 " + "0" * 32 + " " + "0" * 64,
                  "scrypt 15 8 1 " + "0" * 30 + " " + "0" * 64, "scrypt 15 8 1 " + "0" * 32 + " " + "0" * 62,
                  "argon 15 8 1 " + "0" * 32 + " " + "0" * 64, "scrypt 15 8 1 " + "0" * 32 + "  " + "0" * 64):
        auth.write_sign_in_key(home, (made, value))
        with pytest.raises(ValueError):
            auth.read_sign_in_key(home)
        assert wrong_key(with_key(host, "x" * 256)) and host["browser_key"].current(host) is None
    host["browser_key"].failures = 0
    # A key from the command replaces the password, and a password replaces the key.
    entry.set_password(home, "x" * 256)
    key, replaced = entry.make_key(home)
    assert replaced is True and host["browser_key"].current(host)[1] == "key"
    assert wrong_key(with_key(host, "x" * 256)) and signed_in(with_key(host, key))
    assert entry.set_password(home, PASSWORD) is True and wrong_key(with_key(host, key))


@pytest.mark.parametrize("peer", ["127.0.0.1", "100.101.102.103"])
def test_password_signs_in_at_every_remote_address_again_and_again(host, peer):
    """A Tailscale TCP forward connects from 127.0.0.1; a LAN browser does not."""
    entry.set_password(host["service"].data_dir, PASSWORD)
    for at in (REMOTE, OTHER, REMOTE):
        response = with_key(host, PASSWORD, at=at, peer=peer)
        assert signed_in(response), at
        assert response.headers["Cache-Control"] == "no-store" and response.headers["Referrer-Policy"] == "no-referrer"
        cookie = response.cookies[auth.SESSION_COOKIE]
        assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Strict" and cookie["path"] == "/"
        assert auth.valid_session(host, cookie.value) and session_payload(cookie.value)["via"] == "remote"
        assert call(host, "/api/protected", method="GET", at=at, origin=None,
                    headers={"Cookie": f"{auth.SESSION_COOKIE}={cookie.value}"}).status == 200
    assert host["browser_key"].failures == 0


def test_password_is_exact_except_for_the_form_of_its_characters(host, capsys):
    """Unlike a key: case, spaces and hyphens count. Two forms of the same characters are one password."""
    home = host["service"].data_dir
    entry.set_password(home, "\ufb01sh and Chips-12")          # With the single character "fi".
    for attempt in ("fish and chips-12", "fish and Chips 12", "fishandChips-12", " fish and Chips-12"):
        assert wrong_key(with_key(host, attempt)), attempt
    # A form in another character set can send half of a character pair: a wrong password, with no hash.
    csrf = auth.new_csrf(SECRET)
    odd = with_key(host, "", csrf=csrf, headers={"Content-Type": FORM + "; charset=utf-7"},
                   body=f"csrf={csrf}&password=%2B2AA-")
    assert wrong_key(odd) and host["browser_key"].failures == 5 and host["browser_key"].wait() == 2
    host["browser_key"]._streak, host["browser_key"]._until = 0, 0.0
    assert signed_in(with_key(host, "fish and Chips-12"))    # Typed as two letters on another keyboard.
    printed = capsys.readouterr()
    assert "Chips" not in printed.out + printed.err          # Neither the password nor a wrong one is logged.
    assert printed.out == ("Remote sign-in: 1 wrong passwords or keys since start.\n"
                           "Remote sign-in: a browser signed in with the saved password or key.\n")


def test_wait_is_for_the_password_that_the_wrong_ones_were_sent_for(host):
    """The owner changes the password on the PC during a wait: the new password is checked at once."""
    home, saved = host["service"].data_dir, host["browser_key"]
    entry.set_password(home, PASSWORD)
    now = [1000.0]
    saved.clock = lambda: now[0]
    for _ in range(8):
        now[0] += saved.wait()
        assert wrong_key(with_key(host, "wrong horse battery"))
    assert saved.wait() == 16 and waits(with_key(host, PASSWORD))
    entry.set_password(home, "another horse battery")       # A new record.
    assert signed_in(with_key(host, "another horse battery")) and saved.wait() == 0
    # The same record keeps its wait: a look at an unchanged file changes nothing.
    for _ in range(5):
        assert wrong_key(with_key(host, PASSWORD))
    assert saved.wait() == 2 and saved.current(host)[1] == "password" and saved.wait() == 2
    assert waits(with_key(host, "another horse battery"))


def test_wrong_passwords_are_slowed_down_and_during_a_wait_no_password_is_checked(host, monkeypatch):
    home, saved = host["service"].data_dir, host["browser_key"]
    entry.set_password(home, PASSWORD)
    now, checks, real = [1000.0], [], entry._scrypt
    saved.clock = lambda: now[0]
    monkeypatch.setattr(entry, "_scrypt", lambda password, salt: checks.append(1) or real(password, salt))
    wrong = "wrong horse battery"
    # Four wrong passwords in a row have no wait. The fifth one starts it.
    for _ in range(4):
        assert wrong_key(with_key(host, wrong)) and saved.wait() == 0
    assert wrong_key(with_key(host, wrong)) and saved.wait() == 2 and len(checks) == 5
    # During the wait nothing is checked, also not the right password, and nothing is counted.
    assert waits(with_key(host, PASSWORD)) and waits(with_key(host, wrong))
    assert len(checks) == 5 and saved.failures == 5
    # A code is not slowed down, and a wait for passwords does not stop it.
    assert signed_in(redeem(host, make_code(host)))
    # Each further wrong password doubles the wait, to five minutes at most.
    for expected in (4, 8, 16, 32, 64, 128, 256, 300, 300):
        now[0] += saved.wait()
        assert wrong_key(with_key(host, wrong)) and saved.wait() == expected
    now[0] += 299
    assert waits(with_key(host, PASSWORD))
    # After the wait the right password signs in, and the count starts again.
    now[0] += 1
    assert signed_in(with_key(host, PASSWORD)) and saved.wait() == 0
    for _ in range(4):
        assert wrong_key(with_key(host, wrong)) and saved.wait() == 0
    # One hour with no wrong password starts the count again as well; less than that does not.
    now[0] += 3600
    assert wrong_key(with_key(host, wrong)) and saved.wait() == 2
    now[0] += 3601
    for _ in range(4):
        assert wrong_key(with_key(host, wrong)) and saved.wait() == 0
    assert wrong_key(with_key(host, wrong)) and saved.wait() == 2
    # A text that cannot be a password is a wrong password, and it needs no hash.
    now[0] += 2
    before = len(checks)
    assert wrong_key(with_key(host, "x" * 257)) and len(checks) == before and saved.wait() == 4
    # A sign-in key has no wait: 125 bits need none.
    key = new_key(host)
    for _ in range(20):
        assert wrong_key(with_key(host, other_key(key)))
    assert signed_in(with_key(host, key))


def test_a_sender_of_wrong_passwords_has_at_most_300_guesses_in_a_day(host, monkeypatch):
    """The real count against the ways of sending: without a pause, and in bursts with the quiet hour between them."""
    entry.set_password(host["service"].data_dir, PASSWORD)
    saved = host["browser_key"]
    monkeypatch.setattr(entry, "_scrypt", lambda password, salt: "0" * 64)      # A hash that costs the sender no time.

    async def day(burst):
        now = [0.0]
        saved.clock = lambda: now[0]
        saved._streak, saved._last, saved._until = 0, 0.0, 0.0
        guesses = sent = 0
        while now[0] < 86400:
            answer = await saved.accepts(host, "wrong horse battery")
            assert answer is False                          # The sender never sends during a wait.
            guesses, sent = guesses + 1, sent + 1
            if burst and sent == burst:
                now[0] += saved.QUIET + 1                   # Start a new count.
                sent = 0
            else:
                now[0] += saved.wait()
        return guesses
    counts = {burst: asyncio.run(day(burst)) for burst in (0, 4, 5, 13, 14, 20, 40, 100)}
    assert counts[0] == 299 and max(counts.values()) <= 300, counts
    assert counts[4] == 96                                  # Four free guesses in each hour.


def test_one_password_is_checked_at_a_time(host, monkeypatch):
    import threading
    entry.set_password(host["service"].data_dir, PASSWORD)
    started, release, real, checks = threading.Event(), threading.Event(), entry._scrypt, []

    def slow(password, salt):
        checks.append(1)
        started.set()
        assert release.wait(20)
        return real(password, salt)
    monkeypatch.setattr(entry, "_scrypt", slow)

    async def together():
        first = asyncio.create_task(typed_key(host, PASSWORD))
        await asyncio.to_thread(started.wait, 20)           # The first check runs in its thread.
        second = await typed_key(host, PASSWORD)            # The event loop is free meanwhile.
        release.set()
        return await first, second
    first, second = asyncio.run(together())
    assert signed_in(first) and waits(second) and len(checks) == 1
    assert host["browser_key"].failures == 0                # A password that was not checked is not a wrong one.


def test_password_that_is_changed_or_removed_during_its_check_opens_nothing(host, monkeypatch):
    home, real = host["service"].data_dir, entry._scrypt
    for change in (lambda: entry.remove_key(home), lambda: auth.write_sign_in_key(home, (
            auth.read_sign_in_key(home)[0], entry._SCRYPT + "0" * 32 + " " + "0" * 64))):
        entry.set_password(home, PASSWORD)
        assert signed_in(with_key(host, PASSWORD))
        done = []

        def changed_meanwhile(password, salt):
            digest = real(password, salt)
            if not done:
                done.append(change())                       # The owner's page or command, during the check.
            return digest
        monkeypatch.setattr(entry, "_scrypt", changed_meanwhile)
        assert wrong_key(with_key(host, PASSWORD))
        monkeypatch.setattr(entry, "_scrypt", real)
        host["browser_key"]._streak = 0


def test_sign_out_of_all_browsers_ends_the_password(host, monkeypatch):
    home = host["service"].data_dir
    now = clock(monkeypatch, 1_900_000_100)
    entry.set_password(home, PASSWORD)
    session = with_key(host, PASSWORD).cookies[auth.SESSION_COOKIE].value
    now[0] += 10
    auth.sign_out_all(home)                                 # The owner's command, in another process.
    # The running host has not applied it yet: the password opens nothing.
    assert wrong_key(with_key(host, PASSWORD))
    assert auth.refresh_sessions(host) is True and not auth.valid_session(host, session)
    now[0] += 30
    assert wrong_key(with_key(host, PASSWORD)) and host["browser_key"].current(host) is None
    # The owner sets it again, in a second after the sign-out.
    assert entry.set_password(home, PASSWORD) is False and signed_in(with_key(host, PASSWORD))
    # In the second of a sign-out the save waits for the next second.
    auth.sign_out_all(home)
    assert auth.refresh_sessions(host) is True
    entry.set_password(home, PASSWORD)
    assert auth.read_sign_in_key(home)[0] == host["sessions"].epoch + 1 and signed_in(with_key(host, PASSWORD))
    now[0] -= 500                                           # The clock of the PC was set back.
    monkeypatch.setattr(auth.time, "sleep", lambda seconds: None)
    with pytest.raises(entry.BrowserEntryUnavailable, match="Set the clock. Then set the password again."):
        entry.set_password(home, PASSWORD)


# The owner's page on the PC.

def owner_page(app, *, method="GET", session=..., form=None, token=None, headers=None, **options):
    """The owner's page as a browser on the PC asks for it; returns the response and the session."""
    if session is ...:
        session = auth.new_session(app["session_secret"])   # As the launcher's ticket opens it: not marked remote.
    cookie = {"Cookie": f"{auth.SESSION_COOKIE}={session}"} if session else {}
    if method == "GET":
        options.setdefault("origin", None)                  # A navigation has no Origin.
        return call(app, entry.PASSWORD_PATH + options.pop("query", ""), method="GET",
                    headers={**cookie, **(headers or {})}, **options), session
    token = token or entry._owner_csrf(SimpleNamespace(app=app), session or "")
    body = options.pop("body", None) or urlencode({"csrf": token, **(form or {})})
    return call(app, entry.PASSWORD_PATH, headers={"Content-Type": FORM, **cookie, **(headers or {})},
                body=body, **options), session


def set_form(password=PASSWORD, confirm=None):
    return {"action": "set", "password": password, "confirm": password if confirm is None else confirm}


def test_owner_sets_changes_and_removes_the_password_on_the_page_of_the_pc(host, capsys):
    home = host["service"].data_dir
    page, session = owner_page(host)
    text = page.text
    assert page.status == 200 and page.content_type == "text/html" and "<script" not in text.lower()
    assert page.headers["Cache-Control"] == "no-store" and page.headers["Referrer-Policy"] == "same-origin"
    assert "<h1>Password for other devices</h1>" in text and "<p>No password is set.</p>" in text
    assert '<link rel="stylesheet" href="/connect/assets/connect.css">' in text
    for part in ('<label for="password">New password</label>', 'autocomplete="new-password" minlength="12"',
                 '<label for="confirm">New password again</label>',
                 '<button class="primary" name="action" value="set">Set password</button>',
                 "Use at least 12 characters.", "A sign-out of all browsers also removes the password."):
        assert part in text
    assert text.count('type="password"') == 2 and "Remove password" not in text and text.count("<form") == 1
    # The addresses for other devices, and no other configured address.
    assert f"<li><code>{REMOTE}</code></li><li><code>{OTHER}</code></li>" in text
    assert PLAIN not in text and LOCAL not in text and "localhost" not in text
    token = entry._owner_csrf(SimpleNamespace(app=host), session)
    assert text.count(f'<input type="hidden" name="csrf" value="{token}">') == 1
    # The owner sets the password. The page answers with a redirect, so that a reload sends nothing again.
    done, _ = owner_page(host, method="POST", session=session, form=set_form())
    assert done.status == 303 and done.headers["Location"] == entry.PASSWORD_PATH + "?done=set"
    assert done.headers["Cache-Control"] == "no-store" and auth.SESSION_COOKIE not in done.cookies
    assert host["browser_key"].current(host)[1] == "password" and signed_in(with_key(host, PASSWORD))
    text = owner_page(host, session=session, query="?done=set")[0].text
    assert '<div id="notice" role="status">The password is set.</div>' in text
    assert "<p>A password is set.</p>" in text
    # The record has the second of the session that set it, not the present second (see `_swap`).
    assert auth.read_sign_in_key(home)[0] == session_payload(session)["issued"]
    # A notice shows only while the saved record agrees with it: a reload after a later change says nothing.
    assert '<div id="notice" role="status"></div>' in owner_page(host, session=session, query="?done=removed")[0].text
    assert ">Change password</button>" in text and text.count("<form") == 2
    assert '<button name="action" value="remove">Remove password</button>' in text
    # A change replaces it at once.
    changed, _ = owner_page(host, method="POST", session=session, form=set_form("another horse battery"))
    assert changed.status == 303
    assert wrong_key(with_key(host, PASSWORD)) and signed_in(with_key(host, "another horse battery"))
    # The owner removes it. Browsers that are signed in stay signed in.
    signed = with_key(host, "another horse battery").cookies[auth.SESSION_COOKIE].value
    removed, _ = owner_page(host, method="POST", session=session, form={"action": "remove"})
    assert removed.status == 303 and removed.headers["Location"] == entry.PASSWORD_PATH + "?done=removed"
    assert auth.read_sign_in_key(home) is None and wrong_key(with_key(host, "another horse battery"))
    assert auth.valid_session(host, signed) and auth.valid_session(host, session)
    text = owner_page(host, session=session, query="?done=removed")[0].text
    assert '<div id="notice" role="status">No password or key is set now.</div>' in text and "Remove password" not in text
    assert '<div id="notice" role="status"></div>' in owner_page(host, session=session, query="?done=set")[0].text
    # A key from the command shows as one, and the page can replace or remove it.
    key = new_key(host)
    text = owner_page(host, session=session)[0].text
    assert "<p>A sign-in key from a command is set. A password replaces it.</p>" in text
    # The page names the key as a key.
    assert "<h2>Remove the sign-in key</h2>" in text and "Remove password" not in text
    assert '<button name="action" value="remove">Remove sign-in key</button>' in text
    assert owner_page(host, method="POST", session=session, form=set_form())[0].status == 303
    assert wrong_key(with_key(host, key)) and signed_in(with_key(host, PASSWORD))
    # Nothing of the request is on the page, and no password is logged or shown.
    text = owner_page(host, session=session, query="?done=%3Cb%3Ereflected")[0].text
    assert "reflected" not in text and '<div id="notice" role="status"></div>' in text
    printed = capsys.readouterr()
    assert "horse" not in printed.out + printed.err


def test_owner_page_says_what_is_wrong_and_never_shows_the_password(host, monkeypatch):
    home = host["service"].data_dir
    session = auth.new_session(SECRET)
    cases = ((set_form(confirm="correct horse batterx"), 400, "The two passwords are not the same. Type them again."),
             (set_form("eleven char"), 400, "Use at least 12 characters."),
             (set_form("x" * 257), 400, "Use at most 256 characters."))
    for form, status, message in cases:
        page, _ = owner_page(host, method="POST", session=session, form=form)
        assert page.status == status and f'<div id="notice" role="alert">{message}</div>' in page.text
        assert form["password"] not in page.text and form["confirm"] not in page.text
        assert auth.read_sign_in_key(home) is None
    # A save that is not confirmed is said as such: the record can be in place.
    entry.set_password(home, "the earlier password")
    swap_record = auth.swap_sign_in_key

    def fails_after_the_swap(*arguments):
        swap_record(*arguments)
        raise OSError("ABCDE")
    monkeypatch.setattr(auth, "swap_sign_in_key", fails_after_the_swap)
    page, _ = owner_page(host, method="POST", session=session, form=set_form())
    assert page.status == 500 and ("Amplifier could not confirm that the password is saved. A password or key that you "
                                   "had before may no longer work. Set the password again.") in page.text
    assert "ABCDE" not in page.text and PASSWORD not in page.text
    page, _ = owner_page(host, method="POST", session=session, form={"action": "remove"})
    assert page.status == 500 and "Amplifier could not confirm the removal. Remove it again." in page.text
    # Another change of the record came first: this one is not made, and the page says so.
    entry.set_password(home, "the earlier password")
    earlier = auth.read_sign_in_key(home)
    monkeypatch.setattr(auth, "swap_sign_in_key", lambda *arguments: False)
    for form in (set_form(), {"action": "remove"}):
        page, _ = owner_page(host, method="POST", session=session, form=form)
        assert page.status == 409 and "<form" not in page.text and PASSWORD not in page.text
        assert ("Another change of the password or key came first, so this change was not made. "
                f'<a href="{entry.PASSWORD_PATH}">Open this page again</a>.') in page.text
    monkeypatch.setattr(auth, "swap_sign_in_key", swap_record)
    assert auth.read_sign_in_key(home) == earlier
    # A save that the clock stops (see `_save`) has its own text. A session cannot get there: it must be
    # from a second after the saved sign-out, and the present second is not before the session's.
    def stopped(*arguments):
        raise entry.BrowserEntryUnavailable("The clock of this PC is earlier than the last sign-out of all browsers. "
                                            "Set the clock. Then set the password again.")
    monkeypatch.setattr(entry, "set_password", stopped)
    page, _ = owner_page(host, method="POST", session=session, form=set_form())
    assert page.status == 409 and "Set the clock. Then set the password again." in page.text


def held_form(app, session, form):
    """The owner's form with its body held back; the request, and the bytes of the body."""
    raw = urlencode({"csrf": entry._owner_csrf(SimpleNamespace(app=app), session), **form})

    async def request():
        built = await build(app, entry.PASSWORD_PATH, held=True, body=raw, headers={
            "Content-Type": FORM, "Cookie": f"{auth.SESSION_COOKIE}={session}"})
        match = await app.router.resolve(built)
        match.add_app(app)
        built._match_info = match
        return built, match.handler
    return request, raw.encode()


@pytest.mark.parametrize("applied", [True, False], ids=["the-host-applied-the-sign-out", "the-host-did-not-apply-it-yet"])
@pytest.mark.parametrize("form", [set_form(), {"action": "remove"}], ids=["set", "remove"])
def test_session_that_a_sign_out_ended_during_its_request_changes_nothing(host, monkeypatch, applied, form):
    """The sender of a form decides when its body arrives. The owner signs out all browsers meanwhile.

    Two layers refuse it. The page looks at the session again after the form: a host that applied the
    sign-out starts no save. The save reads the saved sign-out itself: that holds when the host did not
    apply the sign-out yet.
    """
    home = host["service"].data_dir
    entry.set_password(home, "the earlier password")
    earlier = auth.read_sign_in_key(home)
    session = auth.new_session(SECRET)
    make, raw = held_form(host, session, form)
    if applied:
        monkeypatch.setattr(entry, "set_password", lambda *a: pytest.fail("A save was started"))
        monkeypatch.setattr(entry, "remove_key", lambda *a: pytest.fail("A removal was started"))

    async def late():
        request, handler = await make()
        answer = asyncio.create_task(auth.auth_required(request, handler))
        await asyncio.sleep(0.05)                           # The page waits for the form: the session counted so far.
        assert not answer.done()
        auth.sign_out_all(home)                             # The owner's command, in another process.
        if applied:
            assert auth.refresh_sessions(host) is True and not auth.valid_session(host, session)
        request.content.feed_data(raw)
        request.content.feed_eof()
        try:
            return await answer
        except web.HTTPException as response:
            return response
    page = asyncio.run(late())
    assert page.status == 403 and "<form" not in page.text and PASSWORD not in page.text
    assert auth.read_sign_in_key(home) == earlier           # Nothing was saved and nothing was removed.
    if applied:
        assert ("Open this page in Amplifier on the PC that runs it, in a browser that signed in there. A browser "
                "that signed in with a password, a key or a code cannot set the password.") in page.text
    else:
        assert "A sign-out of all browsers ended this sign-in. This change does not count." in page.text
        assert auth.refresh_sessions(host) is True
    assert wrong_key(with_key(host, PASSWORD)) and wrong_key(with_key(host, "the earlier password"))


def test_second_of_a_session_is_only_that_of_a_session_that_counts_now(host, monkeypatch):
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_100)
    host["sessions"].epoch = 1_900_000_050                  # The sign-out that this host applied.

    def session(issued, **more):
        return auth._signed(SECRET, {"kind": "session", "issued": issued, "nonce": "n", **more})
    assert auth.session_issued(host, session(1_900_000_051)) == 1_900_000_051
    assert auth.session_issued(host, session(1_900_000_100, via="remote")) == 1_900_000_100
    for value in (session(1_900_000_050), session(1_900_000_049), session(1_900_000_101), session(1_899_999_000),
                  auth._signed(SECRET, {"kind": "csrf", "issued": 1_900_000_060, "nonce": "n"}),
                  auth._signed("t" * 64, {"kind": "session", "issued": 1_900_000_060, "nonce": "n"}),
                  "not a session", "", None):
        assert auth.session_issued(host, value) is None, value
    # The mark of a session that a code, a key or a password opened; a value that cannot be read counts as remote.
    assert auth.session_is_remote(session(1_900_000_060, via="remote")) is True
    assert auth.session_is_remote(session(1_900_000_060)) is False
    assert auth.session_is_remote(session(1_900_000_060, via="local")) is False
    for value in ("not a session", "", None, "####.0", 7):
        assert auth.session_is_remote(value) is True, value


def during_the_swap(monkeypatch, *, before=None, after=None):
    """Run the owner's other doings at an exact point of the page's change: before or after its record goes in place."""
    real, done = auth.swap_sign_in_key, []

    def swap_record(data_dir, expected, record):
        first = not done
        done.append(record)
        if first and before:
            before()
        result = real(data_dir, expected, record)
        if first and after:
            after()
        return result
    monkeypatch.setattr(auth, "swap_sign_in_key", swap_record)
    return done


def signs_in(host, password):
    """In a thread of the page's change: does the password open a session at this moment?"""
    return signed_in(with_key(host, password))


def test_record_that_the_page_saves_has_the_second_of_its_session(host, monkeypatch):
    home = host["service"].data_dir
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_500)
    session = auth._signed(SECRET, {"kind": "session", "issued": 1_900_000_100, "nonce": "n"})
    assert owner_page(host, method="POST", session=session, form=set_form())[0].status == 303
    assert auth.read_sign_in_key(home)[0] == 1_900_000_100 and signed_in(with_key(host, PASSWORD))
    # So the sign-out that ends that session ends the record, at whatever point of the change it is saved.
    host["sessions"].epoch = 1_900_000_100
    assert not auth.valid_session(host, session) and wrong_key(with_key(host, PASSWORD))
    host["sessions"].epoch = 1_900_000_099
    assert auth.valid_session(host, session) and signed_in(with_key(host, PASSWORD))
    # The owner's command keeps the present second: its authority is the private folder.
    assert entry.set_password(home, PASSWORD) is True and auth.read_sign_in_key(home)[0] == 1_900_000_500
    key, _ = entry.make_key(home)
    assert auth.read_sign_in_key(home)[0] == 1_900_000_500


@pytest.mark.parametrize("applied", [True, False], ids=["the-host-applied-the-sign-out", "the-host-did-not-apply-it-yet"])
@pytest.mark.parametrize("earlier", [True, False], ids=["a-password-was-set-before", "nothing-was-set-before"])
def test_sign_out_after_the_page_looked_leaves_no_moment_with_a_password_that_counts(host, monkeypatch, applied, earlier):
    """The page read the saved sign-out; then the owner signs out all browsers; then the page's record goes in place."""
    home = host["service"].data_dir
    if earlier:
        entry.set_password(home, "the earlier password")
    session = auth.new_session(SECRET)
    moments = []

    def owner_signs_out():
        auth.sign_out_all(home)                             # The owner's command, with its "no key" write.
        entry.remove_key(home)
        if applied:
            assert auth.refresh_sessions(host) is True

    def at_this_moment():
        moments.append((auth.read_sign_in_key(home), signs_in(host, PASSWORD)))
    during_the_swap(monkeypatch, before=owner_signs_out, after=at_this_moment)
    page, _ = owner_page(host, method="POST", session=session, form=set_form())
    assert "<form" not in page.text and PASSWORD not in page.text
    if earlier:
        # The record that the page looked at is gone: the page's change is not made at all.
        assert page.status == 409 and moments == [(None, False)]
    else:
        # The page's record was in place for a moment. It did not count, and the page removed it again.
        assert page.status == 403 and "A sign-out of all browsers ended this sign-in." in page.text
        assert moments[0][0][0] == session_payload(session)["issued"] and moments[0][1] is False
    assert auth.read_sign_in_key(home) is None
    auth.refresh_sessions(host)
    assert wrong_key(with_key(host, PASSWORD)) and not auth.valid_session(host, session)


@pytest.mark.parametrize("form", [set_form(), {"action": "remove"}], ids=["set", "remove"])
def test_change_of_an_ended_session_never_touches_what_the_owner_did_after_the_sign_out(host, monkeypatch, form):
    """The page looked; the owner signs out all browsers, signs in again and sets a new password; the page goes on."""
    home = host["service"].data_dir
    entry.set_password(home, "the earlier password")
    session = auth.new_session(SECRET)
    real_time = auth.time.time

    def owner_goes_on():
        auth.sign_out_all(home)
        entry.remove_key(home)
        assert auth.refresh_sessions(host) is True
        monkeypatch.setattr(auth.time, "time", lambda: real_time() + 5)     # In a later second.
        fresh = auth.new_session(SECRET)
        assert owner_page(host, method="POST", session=fresh, form=set_form("the replacement password"))[0].status == 303
    done = during_the_swap(monkeypatch, before=owner_goes_on)
    page, _ = owner_page(host, method="POST", session=session, form=form)
    assert page.status in {403, 409} and "<form" not in page.text
    # The replacement is there, untouched, and it signs in. Neither password of the ended session does.
    assert host["browser_key"].current(host)[1] == "password"
    assert signed_in(with_key(host, "the replacement password"))
    assert wrong_key(with_key(host, PASSWORD)) and wrong_key(with_key(host, "the earlier password"))
    assert len(done) >= 2                                   # The replacement's own swap, and the ended session's.


def test_cleanup_of_a_refused_change_removes_its_own_record_and_no_other(host, monkeypatch):
    """The page's record is in place; then a sign-out is saved and the owner's command makes a key; then the look."""
    home = host["service"].data_dir
    session = auth.new_session(SECRET)
    made = []

    def owner_goes_on():
        auth.sign_out_all(home)
        monkeypatch.setattr(auth.time, "time", lambda now=auth.time.time() + 5: now)
        made.append(entry.make_key(home)[0])                # The owner's command: a write that replaces.
    during_the_swap(monkeypatch, after=owner_goes_on)
    page, _ = owner_page(host, method="POST", session=session, form=set_form())
    assert page.status == 403 and "A sign-out of all browsers ended this sign-in." in page.text
    # The record in the file is the owner's key, untouched. Until the host applies the sign-out, nothing signs in.
    assert host["browser_key"].current(host)[1] == "key" and wrong_key(with_key(host, made[0]))
    assert auth.refresh_sessions(host) is True
    assert signed_in(with_key(host, made[0])) and wrong_key(with_key(host, PASSWORD))


@pytest.mark.parametrize("replacement", [True, False], ids=["the-owner-set-a-new-password", "the-owner-set-nothing"])
def test_owner_doings_during_the_hash_refuse_the_change_also_with_a_clock_that_was_set_back(host, monkeypatch, replacement):
    """The page checked its session; during the hash the clock goes back and the owner signs out all browsers.

    The second of that sign-out is before the session's second, so no second shows the order. The page is
    bound to the sign-out record that the host had applied when it checked the session, and reads no clock.
    """
    home = host["service"].data_dir
    now = clock(monkeypatch, 1_900_000_500)
    session = auth.new_session(SECRET)
    real = entry._scrypt

    def during_the_hash(password, salt):
        if password == PASSWORD.encode():                   # The hash of the page's change, in its thread.
            now[0] -= 100                                   # The clock of the PC is set back.
            assert auth.sign_out_all(home) == now[0]        # Sign-out, its "no key" write, and the host applies it.
            entry.remove_key(home)
            assert auth.refresh_sessions(host) is True
            if replacement:
                now[0] += 2
                assert entry.set_password(home, "the replacement password") is False       # The owner's own save.
        return real(password, salt)
    monkeypatch.setattr(entry, "_scrypt", during_the_hash)
    page, _ = owner_page(host, method="POST", session=session, form=set_form())
    assert page.status == 403 and "A sign-out of all browsers ended this sign-in." in page.text
    monkeypatch.setattr(entry, "_scrypt", real)
    now[0] = 1_900_000_600                                  # The clock reaches the session's second again.
    assert wrong_key(with_key(host, PASSWORD))
    if replacement:
        assert signed_in(with_key(host, "the replacement password"))
    else:
        assert auth.read_sign_in_key(home) is None


@pytest.mark.parametrize("form", [set_form(), {"action": "remove"}], ids=["set", "remove"])
def test_page_reads_the_record_before_it_looks_at_the_sign_out_record(host, monkeypatch, form):
    """The owner signs out and sets a new password just before the page's change reads the record.

    The change then reads that new record. It must look at the sign-out record after that read: a look
    before it would pass, and the change would then swap out the owner's new record as its own.
    """
    home = host["service"].data_dir
    entry.set_password(home, "the earlier password")
    session = auth.new_session(SECRET)
    read, done = auth.sign_in_key_bytes, []

    def owner_goes_on_first(data_dir):
        if not done:
            done.append(True)
            auth.sign_out_all(home)
            entry.remove_key(home)
            monkeypatch.setattr(auth.time, "time", lambda later=auth.time.time() + 5: later)
            entry.set_password(home, "the replacement password")
        return read(data_dir)
    monkeypatch.setattr(auth, "sign_in_key_bytes", owner_goes_on_first)
    page, _ = owner_page(host, method="POST", session=session, form=form)
    assert done and page.status == 403 and "A sign-out of all browsers ended this sign-in." in page.text
    assert auth.refresh_sessions(host) is True
    assert signed_in(with_key(host, "the replacement password")) and wrong_key(with_key(host, PASSWORD))


def test_look_at_the_sign_out_record_that_fails_is_not_a_sign_out(host, monkeypatch):
    home = host["service"].data_dir
    entry.set_password(home, "the earlier password")
    earlier = auth.read_sign_in_key(home)
    session = auth.new_session(SECRET)
    look = auth._epoch_hint
    unconfirmed = "Amplifier could not confirm that the password is saved."
    # The first look fails: nothing is changed, and the page does not speak of a sign-out.
    monkeypatch.setattr(auth, "_epoch_hint", lambda data_dir: auth._UNSEEN)
    for form, text in ((set_form(), unconfirmed), ({"action": "remove"}, "Amplifier could not confirm the removal.")):
        page, _ = owner_page(host, method="POST", session=session, form=form)
        assert page.status == 500 and text in page.text and "sign-out of all browsers ended" not in page.text
        assert auth.read_sign_in_key(home) == earlier
    # The look after the record is in place fails: it is not known that no sign-out came, so that record goes.
    looks = []
    monkeypatch.setattr(auth, "_epoch_hint", lambda data_dir: look(data_dir) if not looks.append(1) and len(looks) < 2
                        else auth._UNSEEN)
    page, _ = owner_page(host, method="POST", session=session, form=set_form())
    assert page.status == 500 and unconfirmed in page.text and "may no longer work" in page.text
    assert auth.read_sign_in_key(home) is None and len(looks) == 2
    # A host that could not look at the record at its start gives no change either.
    monkeypatch.setattr(auth, "_epoch_hint", look)
    host["sessions"].hint = auth._UNSEEN
    page, _ = owner_page(host, method="POST", session=session, form=set_form())
    assert page.status == 500 and unconfirmed in page.text and auth.read_sign_in_key(home) is None


def test_swap_puts_a_record_in_place_only_where_the_expected_bytes_are(host):
    """On native Windows through the real private store: remove the exact bytes, then publish without replacement."""
    home = host["service"].data_dir
    first, second = (1_900_000_001, "a" * 64), (1_900_000_002, "b" * 64)
    assert auth.sign_in_key_bytes(home) is None
    assert auth.swap_sign_in_key(home, b"something", first) is False and auth.sign_in_key_bytes(home) is None
    assert auth.swap_sign_in_key(home, None, first) is True
    one = auth.sign_in_key_bytes(home)
    assert one == b"1900000001 " + b"a" * 64 + b"\n" and auth.read_sign_in_key(home) == first
    assert auth.swap_sign_in_key(home, None, second) is False        # A record is there; none was expected.
    assert auth.swap_sign_in_key(home, one + b" ", second) is False and auth.sign_in_key_bytes(home) == one
    assert auth.swap_sign_in_key(home, one, second) is True and auth.read_sign_in_key(home) == second
    assert auth.swap_sign_in_key(home, one, None) is False and auth.read_sign_in_key(home) == second
    assert auth.swap_sign_in_key(home, auth.sign_in_key_bytes(home), None) is True
    assert auth.sign_in_key_bytes(home) == b"none\n" and auth.read_sign_in_key(home) is None
    assert auth.swap_sign_in_key(home, b"none\n", first) is True and auth.read_sign_in_key(home) == first


OWNER_REFUSED = {
    "no session": lambda app: {"session": None},
    "a session that a code, a key or a password opened": lambda app: {"session": auth.new_session(SECRET, remote=True)},
    "a session of another secret": lambda app: {"session": auth.new_session("t" * 64)},
    "the control credential in place of a session": lambda app: {"session": None, "headers": BEARER},
    "an address for other devices": lambda app: {"at": REMOTE},
    "a peer that is not this computer": lambda app: {"peer": "192.168.1.9"},
    "a socket that is not this computer's": lambda app: {"sock": "192.168.1.20"},
    "plain HTTP": lambda app: {"secure": False, "origin": LOCAL.replace("https:", "http:")},
}
OWNER_POST_REFUSED = {
    "no Origin": lambda app, session: {"origin": None},
    "the Origin of another address": lambda app, session: {"origin": REMOTE},
    "an opaque Origin": lambda app, session: {"origin": "null"},
    "the token of another session": lambda app, session: {
        "token": entry._owner_csrf(SimpleNamespace(app=app), auth.new_session(SECRET))},
    "a sign-in page token": lambda app, session: {"token": auth.new_csrf(SECRET)},
    "no token": lambda app, session: {"body": urlencode(set_form())},
    "an extra field": lambda app, session: {"form": {**set_form(), "next": "/"}},
    "a field twice": lambda app, session: {"body": urlencode({
        "csrf": entry._owner_csrf(SimpleNamespace(app=app), session), **set_form()}) + "&password=" + PASSWORD},
    "no confirmation": lambda app, session: {"form": {"action": "set", "password": PASSWORD}},
    "another action": lambda app, session: {"form": {**set_form(), "action": "show"}},
    "a removal with a password": lambda app, session: {"form": {**set_form(), "action": "remove"}},
    "JSON": lambda app, session: {"headers": {"Content-Type": "application/json"}},
    "a multipart form": lambda app, session: {
        "headers": {"Content-Type": "multipart/form-data; boundary=x"},
        "body": "".join(f'--x\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n' for name, value in {
            "csrf": entry._owner_csrf(SimpleNamespace(app=app), session), **set_form()}.items()) + "--x--\r\n"},
    "half of a character pair": None,                      # Its own case below: the form is the page's own.
}
del OWNER_POST_REFUSED["half of a character pair"]


def test_owner_page_refuses_a_text_that_cannot_be_saved(host):
    home = host["service"].data_dir
    session = auth.new_session(SECRET)
    token = entry._owner_csrf(SimpleNamespace(app=host), session)
    odd = "half%20of%20a%20pair%20%2B2AA-"                  # In UTF-7: a text of 15 characters; the last is half of a pair.
    page, _ = owner_page(host, method="POST", session=session, headers={"Content-Type": FORM + "; charset=utf-7"},
                         body=f"csrf={token}&action=set&password={odd}&confirm={odd}")
    assert page.status == 400 and auth.read_sign_in_key(home) is None
    assert "This text has a character that cannot be saved. Use another password." in page.text


@pytest.mark.parametrize("options", OWNER_REFUSED.values(), ids=[name.replace(" ", "-") for name in OWNER_REFUSED])
def test_owner_page_is_only_for_a_browser_that_signed_in_on_this_pc(host, monkeypatch, options):
    home = host["service"].data_dir
    entry.set_password(home, "the earlier password")
    record = auth.read_sign_in_key(home)
    monkeypatch.setattr(entry, "set_password", lambda *a: pytest.fail("A password was set"))
    monkeypatch.setattr(entry, "remove_key", lambda *a: pytest.fail("The password was removed"))
    for method, form in (("GET", None), ("POST", set_form()), ("POST", {"action": "remove"})):
        page, _ = owner_page(host, method=method, form=form, **options(host))
        # A browser that is not signed in is sent to the sign-in page; every other one is refused.
        assert page.status in {307, 403}, (method, page.status)
        text = page.text or ""
        assert "<form" not in text and "csrf" not in text and PASSWORD not in text
    assert auth.read_sign_in_key(home) == record


@pytest.mark.parametrize("options", OWNER_POST_REFUSED.values(),
                         ids=[name.replace(" ", "-") for name in OWNER_POST_REFUSED])
def test_owner_page_refuses_a_form_that_is_not_its_own(host, options):
    home = host["service"].data_dir
    session = auth.new_session(SECRET)
    page, _ = owner_page(host, method="POST", session=session, **{"form": set_form(), **options(host, session)})
    assert page.status == 403 and auth.read_sign_in_key(home) is None
    assert PASSWORD not in (page.text or "")


def test_owner_page_on_a_host_that_is_not_a_windows_host_has_no_form(host, monkeypatch):
    home = host["service"].data_dir
    monkeypatch.setattr(entry, "is_windows", lambda: False)
    page, session = owner_page(host)
    assert page.status == 200 and "<form" not in page.text
    assert "a browser on another device signs in with the password of the host account" in page.text
    assert owner_page(host, method="POST", session=session, form=set_form())[0].status == 403
    assert auth.read_sign_in_key(home) is None
    monkeypatch.setattr(entry, "is_windows", lambda: True)
    host["browser_key"] = None                              # A Windows host without its stores.
    page, _ = owner_page(host, session=session)
    assert page.status == 503 and "<form" not in page.text and "cannot set a password for other devices now" in page.text
    assert "host account" not in page.text
    assert owner_page(host, method="POST", session=session, form=set_form())[0].status == 503
    assert auth.read_sign_in_key(home) is None


def test_page_devices_and_apps_gets_the_card_only_where_the_owner_page_works(host, monkeypatch):
    def card(session=..., **options):
        session = auth.new_session(SECRET) if session is ... else session
        request = asyncio.run(build(host, "/connect", method="GET", origin=None, headers={
            "Cookie": f"{auth.SESSION_COOKIE}={session}"}, **options))
        return entry.password_card(request)
    assert card() == entry.PASSWORD_CARD and f'href="{entry.PASSWORD_PATH}"' in entry.PASSWORD_CARD
    assert ">Set a password</a>" in entry.PASSWORD_CARD and "<script" not in entry.PASSWORD_CARD
    for options in ({"session": auth.new_session(SECRET, remote=True)}, {"at": REMOTE}, {"peer": "192.168.1.9"},
                    {"secure": False}, {"session": "not a session"}):
        assert card(**options) == "", options
    monkeypatch.setattr(entry, "is_windows", lambda: False)
    assert card() == ""
    # The page puts the card before its first card, and only there.
    source = (Path(entry.__file__).parent / "device_connection_pages.py").read_text(encoding="utf-8")
    assert ("content.replace('<section class=\"card\">', password_card(request) + '<section class=\"card\">', 1)"
            in source)
    page = (Path(entry.__file__).parent / "device_client" / "web" / "connect.html").read_text(encoding="utf-8")
    assert page.count('<section class="card">') >= 1 and entry.PASSWORD_PATH not in page


def test_host_serves_the_owner_page_and_the_command_keeps_its_one_job(home, monkeypatch, capsys):
    # This file must not import the server module; its routes are checked as text.
    source = (Path(entry.__file__).parent / "server.py").read_text(encoding="utf-8")
    assert "app.router.add_get(browser_entry.PASSWORD_PATH, browser_entry.password_page, allow_head=False)" in source
    assert "app.router.add_post(browser_entry.PASSWORD_PATH, browser_entry.password_page)" in source
    # The command line has no way to set a password: a password on a command line stays in its history.
    for words in (["browsers", "password"], ["browsers", "key", "--password", PASSWORD]):
        monkeypatch.setattr(sys, "argv", ["amplifier-unified", *words])
        with pytest.raises(SystemExit):
            cli._parse()
    capsys.readouterr()
