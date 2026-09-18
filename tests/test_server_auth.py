import time

import pytest

from test_service import Runtime

from amplifier_web.auth import CSRF_COOKIE, _signed, _verified, data_identity, new_csrf, new_session
from amplifier_web.server import create_app


async def test_only_bootstrap_paths_are_anonymous_and_control_bearer_protects_api(aiohttp_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    assert (await client.get("/api/health")).status == 200
    assert (await client.get("/api/state")).status == 401
    page = await client.get("/", allow_redirects=False)
    assert page.status == 307 and page.headers["Location"].startswith("/login")
    assert page.headers["X-Content-Type-Options"] == "nosniff"
    valid_cookie = new_session(app["session_secret"])
    assert (await client.get("/api/state", headers={"Cookie": f"amplifier_unified_session={valid_cookie}"})).status == 200
    assert (await client.get("/api/state", headers={"Cookie": "muxplex_session=not-a-unified-session"})).status == 401
    assert (await client.post("/login", data={"username": "owner", "password": "correct"})).status == 403
    setup = await client.get("/setup", headers={"User-Agent": "Mozilla/5.0 (Linux; Android 14; attacker.example)"})
    assert setup.status == 200
    assert setup.headers["Referrer-Policy"] == "no-referrer"
    assert setup.headers["Content-Security-Policy"] == (
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    )
    setup_body = await setup.text()
    assert 'data-platform="android" open' in setup_body
    assert "Mozilla/5.0" not in setup_body
    assert "attacker.example" not in setup_body
    assert not (tmp_path / "config" / "tls").exists()
    login = await client.get("/login")
    assert login.status == 200 and "csrf" in (await login.text())
    client.session.headers["Authorization"] = "Bearer " + app["control_token"]
    assert (await client.get("/api/state")).status == 200


@pytest.mark.parametrize("path,expected_status", [
    ("/login", 200),
    ("/setup", 200),
    ("/api/ca", 404),
    ("/ca.crt", 404),
    ("/api/health", 200),
])
async def test_anonymous_access_is_limited_to_the_bootstrap_routes(aiohttp_client, tmp_path, path, expected_status):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)

    response = await client.get(path, allow_redirects=False)
    assert response.status == expected_status
    assert (await client.get("/api/state", allow_redirects=False)).status == 401
    root = await client.get("/", allow_redirects=False)
    assert root.status == 307 and root.headers["Location"] == "/login"


async def test_health_identity_is_only_disclosed_to_control_bearer(aiohttp_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    cookie = new_session(app["session_secret"])
    for headers in ({}, {"Authorization": "Bearer invalid"},
                    {"Cookie": "amplifier_unified_session=" + cookie}):
        response = await client.get("/api/health", headers=headers)
        assert response.status == 200
        body = await response.json()
        assert "dataIdentity" not in body
        assert "runtime" not in body
    response = await client.get("/api/health", headers={"Authorization": "Bearer " + app["control_token"]})
    assert response.status == 200
    assert (await response.json())["dataIdentity"] == data_identity(tmp_path)


async def test_login_rejects_cross_origin_before_pam(aiohttp_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    csrf = (await client.get("/login")).cookies["amplifier_unified_csrf"].value
    response = await client.post("/login", headers={"Origin": "https://attacker.example"},
                                 data={"username": "nobody", "password": "no", "csrf": csrf})
    assert response.status == 403


async def test_exact_loopback_origin_is_allowed(aiohttp_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    response = await client.get("/api/state", headers={
        "Authorization": "Bearer " + app["control_token"],
        "Host": "127.0.0.1:8941",
        "Origin": "http://127.0.0.1:8941",
    })
    assert response.status == 200


async def test_pam_login_issues_only_unified_host_cookie(aiohttp_client, tmp_path, monkeypatch):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    monkeypatch.setattr("amplifier_web.auth.authenticate_pam", lambda username, password: username == "owner" and password == "correct")
    login = await client.get("/login")
    assert login.headers["Referrer-Policy"] == "same-origin"
    csrf = login.cookies["amplifier_unified_csrf"].value
    response = await client.post("/login", headers={"Cookie": f"amplifier_unified_csrf={csrf}",
                                                  "Host": "127.0.0.1:8941",
                                                  "Origin": "http://127.0.0.1:8941",
                                                  "Sec-Fetch-Site": "same-origin"},
                                 data={"username": "owner", "password": "correct", "csrf": csrf},
                                 allow_redirects=False)
    assert response.status == 303
    cookie = response.cookies["amplifier_unified_session"]
    assert cookie["domain"] == "" and cookie["httponly"] and cookie["samesite"].lower() == "strict"


async def test_login_reuses_verified_csrf_across_public_favicon_and_sequential_forms(
        aiohttp_client, tmp_path, monkeypatch):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    pam_calls = []
    monkeypatch.setattr("amplifier_web.auth.authenticate_pam",
                        lambda username, password: pam_calls.append((username, password)) or True)

    for _ in range(2):
        client.session.cookie_jar.clear()
        login = await client.get("/login?next=/")
        csrf = login.cookies[CSRF_COOKIE].value
        assert csrf in await login.text()

        favicon = await client.get("/favicon.ico", headers={"Cookie": f"{CSRF_COOKIE}={csrf}"},
                                   allow_redirects=False)
        assert favicon.status == 200
        assert CSRF_COOKIE not in favicon.cookies
        replacement = await client.get("/login?next=/",
                                       headers={"Cookie": f"{CSRF_COOKIE}={csrf}"})
        assert replacement.cookies[CSRF_COOKIE].value == csrf
        assert csrf in await replacement.text()

        response = await client.post("/login?next=/", headers={"Cookie": f"{CSRF_COOKIE}={csrf}"},
                                     data={"username": "owner", "password": "correct", "csrf": csrf},
                                     allow_redirects=False)
        assert response.status == 303
        assert response.headers["Location"] == "/"

    assert pam_calls == [("owner", "correct"), ("owner", "correct")]


@pytest.mark.parametrize("invalid", [
    lambda secret: None,
    lambda secret: new_csrf(secret) + "x",
    lambda secret: _signed(secret, {"kind": "csrf", "issued": 1, "nonce": "expired"}),
    lambda secret: _signed(secret, {"kind": "session", "issued": int(time.time()), "nonce": "wrong-kind"}),
    lambda secret: _signed(secret, {"kind": "csrf", "issued": int(time.time()) + 60, "nonce": "future"}),
])
async def test_login_replaces_missing_or_invalid_csrf_cookie(aiohttp_client, tmp_path, invalid):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    old_csrf = invalid(app["session_secret"])
    headers = {} if old_csrf is None else {"Cookie": f"{CSRF_COOKIE}={old_csrf}"}

    response = await client.get("/login", headers=headers)
    csrf = response.cookies[CSRF_COOKIE].value
    assert csrf != old_csrf
    assert _verified(app["session_secret"], csrf, 900, "csrf")
    assert csrf in await response.text()


async def test_expired_and_mismatched_login_csrf_remain_rejected(aiohttp_client, tmp_path, monkeypatch):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    monkeypatch.setattr("amplifier_web.auth.authenticate_pam",
                        lambda *args: pytest.fail("invalid CSRF must not invoke PAM"))
    secret = app["session_secret"]
    expired = _signed(secret, {"kind": "csrf", "issued": 1, "nonce": "expired"})
    valid = new_csrf(secret)

    expired_response = await client.post("/login", headers={"Cookie": f"{CSRF_COOKIE}={expired}"},
                                         data={"csrf": expired})
    mismatch_response = await client.post("/login", headers={"Cookie": f"{CSRF_COOKIE}={valid}"},
                                          data={"csrf": new_csrf(secret)})
    assert expired_response.status == 403
    assert mismatch_response.status == 403


@pytest.mark.parametrize(("path", "location"), [
    ("/login?next=/", "/login?error=1"),
    ("/login?next=/projects%3Ftab%3Done", "/login?next=/projects?tab%3Done&error=1"),
])
async def test_failed_pam_login_redirects_to_well_formed_login_url(
        aiohttp_client, tmp_path, monkeypatch, path, location):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    monkeypatch.setattr("amplifier_web.auth.authenticate_pam", lambda *args: False)
    csrf = new_csrf(app["session_secret"])

    response = await client.post(path, headers={"Cookie": f"{CSRF_COOKIE}={csrf}"},
                                 data={"csrf": csrf}, allow_redirects=False)
    assert response.status == 303
    assert response.headers["Location"] == location


async def test_opaque_origin_login_stays_blocked_even_with_valid_csrf(aiohttp_client, tmp_path, monkeypatch):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    monkeypatch.setattr("amplifier_web.auth.authenticate_pam",
                        lambda *args: pytest.fail("opaque-origin form must not invoke PAM"))
    csrf = (await client.get("/login")).cookies["amplifier_unified_csrf"].value
    response = await client.post("/login", headers={
        "Origin": "null", "Sec-Fetch-Site": "same-origin",
        "Cookie": f"amplifier_unified_csrf={csrf}",
    }, data={"username": "owner", "password": "synthetic", "csrf": csrf})
    assert response.status == 403
    assert (await client.get("/setup")).headers["Referrer-Policy"] == "no-referrer"


# External links send cross-site Fetch Metadata even though they are ordinary
# document navigations, not cross-origin API calls. Test the redirect too.
NAVIGATION_HEADERS = {
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-User": "?1",
}


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("path", ["/", "/login", "/setup"])
async def test_external_document_navigation_reaches_login_or_setup(aiohttp_client, tmp_path, method, path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    response = await client.request(method, path, headers=NAVIGATION_HEADERS, allow_redirects=False)
    if path == "/":
        assert response.status == 307 and response.headers["Location"] == "/login"
        # A redirect can retain the original cross-site navigation metadata.
        response = await client.request(method, response.headers["Location"],
                                        headers=NAVIGATION_HEADERS, allow_redirects=False)
    assert response.status == 200
    if method == "GET":
        assert "Amplifier" in await response.text()
    assert (await client.get("/api/state")).status == 401


@pytest.mark.parametrize("path", ["/api/state", "/api/actions", "/api/events", "/api/health",
                                 "/api/ca", "/ca.crt", "/index.html"])
async def test_navigation_metadata_does_not_exempt_other_routes(aiohttp_client, tmp_path, path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    headers = {**NAVIGATION_HEADERS, "Cookie": "amplifier_unified_session=" + new_session(app["session_secret"])}
    response = await client.get(path, headers=headers, allow_redirects=False)
    assert response.status == 403


@pytest.mark.parametrize("mode,destination", [("cors", "empty"), ("no-cors", "image"),
                                            ("navigate", "iframe"), ("websocket", "empty"),
                                            ("", ""), ("navigate", "")])
async def test_cross_site_fetches_and_frames_stay_blocked(aiohttp_client, tmp_path, mode, destination):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    headers = {**NAVIGATION_HEADERS, "Sec-Fetch-Mode": mode, "Sec-Fetch-Dest": destination}
    assert (await client.get("/login", headers=headers)).status == 403


@pytest.mark.parametrize("origin", ["https://attacker.example", "null", "http://127.0.0.1:8941"])
async def test_cross_site_navigation_with_origin_stays_blocked(aiohttp_client, tmp_path, origin):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    headers = {**NAVIGATION_HEADERS, "Host": "127.0.0.1:8941", "Origin": origin}
    assert (await client.get("/login", headers=headers)).status == 403


async def test_cross_site_form_with_valid_csrf_is_rejected_before_pam(aiohttp_client, tmp_path, monkeypatch):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    monkeypatch.setattr("amplifier_web.auth.authenticate_pam",
                        lambda *args: pytest.fail("cross-site form must not invoke PAM"))
    csrf = new_csrf(app["session_secret"])
    response = await client.post("/login", headers={
        **NAVIGATION_HEADERS,
        "Cookie": f"amplifier_unified_csrf={csrf}",
    }, data={"username": "owner", "password": "synthetic", "csrf": csrf}, allow_redirects=False)
    assert response.status == 403


async def test_external_navigation_does_not_allow_unknown_host(aiohttp_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    response = await client.get("/login", headers={**NAVIGATION_HEADERS, "Host": "attacker.example"})
    assert response.status == 403
    assert (await response.json())["error"] == "This Host is not configured."

async def test_pwa_metadata_is_public_but_conversations_and_canvas_stay_private(aiohttp_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    for path in ['/manifest.webmanifest','/sw.js','/pwa.js','/offline.html','/app-pages.css',
                 '/favicon.ico','/branding/pwa/pwa-192.png','/branding/pwa/pwa-512.png']:
        response = await client.get(path, allow_redirects=False)
        assert response.status == 200, path
        assert 'Set-Cookie' not in response.headers
    manifest = await (await client.get('/manifest.webmanifest')).json()
    assert manifest['start_url'] == '/' and manifest['display'] == 'standalone'
    for path in ['/api/state','/api/canvas/private/document','/api/attachments/private']:
        assert (await client.get(path, allow_redirects=False)).status == 401
    assert (await client.get('/', allow_redirects=False)).status == 307
    assert (await client.get('/index.html', allow_redirects=False)).status == 307
    assert (await client.post('/sw.js', allow_redirects=False)).status == 307
    login = await client.get('/login?error=1')
    assert login.headers['Cache-Control'] == 'no-store'
    assert 'Could not sign in.' in await login.text()
