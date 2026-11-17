"""Sign-in by typed code for a browser on another device (Windows host).

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

    The sign-out record is not a model. On native Windows it goes through this
    seam to the real private store (`native`); the other systems do not use
    the seam for it.
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
                if name == "session-epoch" and files.native is not None:
                    with files.native(path, owned_root=owned_root) as real:
                        return real.read_private(name, limit=limit)
                if Path(path).parts[-2:] != ("config", "auth") or name not in {"session-secret", "control-token"}:
                    raise FileNotFoundError(name)
                return (SECRET if name == "session-secret" else TOKEN).encode()

            def publish_private(self, name, content, *, replace=False):
                assert name == "session-epoch"              # Nothing else is ever written through this seam.
                with files.native(path, owned_root=owned_root, create=True) as real:
                    real.publish_private(name, content, replace=replace)
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
    app.router.add_post(entry.ISSUE_PATH, entry.issue)
    app.router.add_post(entry.REDEEM_PATH, entry.redeem)
    app.router.add_post(entry.CODE_PATH, entry.code_issue)
    app.router.add_post(entry.CODE_REDEEM_PATH, entry.code_redeem)
    app.router.add_get("/login", auth.login_page)

    async def protected(request):
        return web.json_response({"authenticated": True})
    app.router.add_get("/api/protected", protected)
    return app


async def build(app, path, *, method="POST", at=LOCAL, origin=..., headers=None, body=None,
                secure=True, peer="127.0.0.1"):
    """One real aiohttp request to the address `at`; no socket."""
    headers = {"Host": at.split("://", 1)[1], "Origin": at if origin is ... else origin, **(headers or {})}
    headers = {key: value for key, value in headers.items() if value is not None}
    if isinstance(body, dict):
        raw = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    else:
        raw = (body or "").encode()
    headers["Content-Length"] = str(len(raw))
    content = aiohttp.StreamReader(Mock(_reading_paused=False), 8192)
    content.feed_data(raw)
    content.feed_eof()

    def extra(name):
        if name in {"peername", "sockname"}:
            return (peer if name == "peername" else "127.0.0.1", 8941)
    return make_mocked_request(method, path, headers=headers, app=app, payload=content,
                               transport=SimpleNamespace(get_extra_info=extra),
                               sslcontext=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER) if secure else None)


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
    assert host["browser_entry"] is None and host["browser_codes"] is None
    monkeypatch.setattr(entry, "is_windows", lambda: False)
    other = web.Application()
    entry.initialize(other)
    assert other["browser_entry"] is None and other["browser_codes"] is None


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
    assert refused.text == entry.reopen()
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
    for path in ("/login", "/login?error=1"):
        text = call(host, path, method="GET", at=REMOTE, origin=None).text
        body = text[text.index("<main>"):text.index("</main>")]
        heading, error, form, button, end = (body.index(mark) for mark in (
            "<h1>", '<p class="error" role="alert">', "<form ", '<button type="submit">Sign in</button>', "</form>"))
        assert heading < error < form < button < end
        # Between the heading and the form there is the error text and nothing else.
        between = body[body.index("</h1>") + len("</h1>"):form]
        assert re.fullmatch(r'\s*<p class="error" role="alert">[^<]*</p>\s*', between), between
        # Every other text and the one link come after the button.
        assert all(body.index(mark) > end for mark in ('id="code-help"', "<code>", "<a "))
        assert body.count("<p") == 3
    assert "That code did not work" in text


def test_the_next_step_for_a_browser_that_is_not_signed_in_is_true_on_each_platform(monkeypatch):
    monkeypatch.setattr(entry, "is_windows", lambda: True)
    windows = entry.reopen()
    # A PC from the setup file has no `amplifier-unified` on its PATH: its Start-menu entry comes first.
    assert windows == ("Open Amplifier from the Start menu: Amplifier Unified. "
                       "If the Start menu has no such entry, run this: amplifier-unified open.")
    # The name in the text is the name of the entry that setup makes.
    from amplifier_web import windows_consumer_shell
    assert "Amplifier Unified.lnk" in windows_consumer_shell.LINKS
    monkeypatch.setattr(entry, "is_windows", lambda: False)
    other = entry.reopen()
    assert other == "To open Amplifier, run this: amplifier-unified open."
    assert "Start menu" not in other  # macOS and Linux have no such entry.
    for text in (windows, other):
        assert "launcher" not in text and "candidate" not in text and "host" not in text


@pytest.mark.parametrize("windows,step", [
    (True, "Open Amplifier from the Start menu: Amplifier Unified. "
           "If the Start menu has no such entry, run this: amplifier-unified open."),
    (False, "To open Amplifier, run this: amplifier-unified open.")])
def test_a_browser_that_does_not_open_gets_the_next_step_of_its_platform(tmp_path, monkeypatch, windows, step):
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
    assert str(refused.value) == "The browser did not accept the open request. " + step


def test_a_refused_ticket_names_the_start_menu_first(host):
    # The reply that a browser on the PC shows when its one-use ticket opens nothing.
    refused = open_ticket(host, "a" * 43)
    assert refused.status == 403 and auth.SESSION_COOKIE not in refused.cookies
    assert refused.text == ("Open Amplifier from the Start menu: Amplifier Unified. "
                            "If the Start menu has no such entry, run this: amplifier-unified open.")

def test_remote_address_gets_the_code_form_and_the_local_page_is_unchanged(host):
    for at in (LOCAL, "https://localhost:8941", "https://[::1]:8941"):
        local = call(host, "/login", method="GET", at=at, origin=None).text
        assert '<script src="/browser-entry.js"' in local and 'action="/api/browser-entry/redeem"' in local
        assert "one-time-code" not in local
    page = call(host, "/login", method="GET", at=REMOTE, origin=None)
    text = page.text
    assert page.status == 200 and page.content_type == "text/html"
    assert "<script" not in text.lower()
    assert f'<title>Sign in · {host["service"].instance_branding.snapshot()["name"]}</title>' in text
    assert text.count("<form") == 1 and f'<form method="post" action="{entry.CODE_REDEEM_PATH}">' in text
    assert text.count("<input") == 2 and '<link rel="stylesheet" href="/app-pages.css">' in text
    for attribute in ('name="code"', 'autocomplete="one-time-code"', 'autocapitalize="characters"',
                      'spellcheck="false"', " required>", '<button type="submit">Sign in</button>'):
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


def test_command_line_names_both_operations(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "browsers", "code", "--origin", REMOTE])
    args = cli._parse()
    assert (args.command, args.browsers_command, args.origin) == ("browsers", "code", REMOTE)
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "browsers", "sign-out"])
    assert cli._parse().origin is None
