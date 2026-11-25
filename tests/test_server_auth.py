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
    linux_setup = await client.get("/setup", headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Edg/150"})
    assert linux_setup.status == 200
    linux_body = await linux_setup.text()
    assert 'data-platform="linux" open' in linux_body
    assert 'ca_file="$HOME/Downloads/amplifier-unified-ca (1).crt"' in linux_body
    assert "ca_dest=/usr/local/share/ca-certificates/amplifier-unified-ca.crt" in linux_body
    assert "OS success is not proof of browser success" in linux_body
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
        assert "runtime" not in body and "activity" not in body
        assert "revision" not in body and "instanceId" not in body
        # The activity count is the owner's, with or without the request for it.
        assert "activity" not in await (await client.get("/api/health?activity=1", headers=headers)).json()
    owner = {"Authorization": "Bearer " + app["control_token"]}
    response = await client.get("/api/health", headers=owner)
    assert response.status == 200
    body = await response.json()
    assert body["dataIdentity"] == data_identity(tmp_path)
    assert body["instanceId"] == app['service'].update_manager.running_identity['instanceId']
    assert body['revision'] == app['service'].update_manager.running_identity['revision']
    # An ordinary owner health probe does not scan conversations.
    assert "activity" not in body
    # The owner's uninstall asks before a normal stop interrupts running work.
    asked = await (await client.get("/api/health?activity=1", headers=owner)).json()
    assert asked["activity"] == {"working": 0} and asked["dataIdentity"] == body["dataIdentity"]
    sessions = app['service'].state['sessions']
    saved = list(sessions)
    # Counted with the update gate's own predicates: a, c, f, j and k are working.
    sessions.extend({'id': name, 'status': status, **extra} for name, status, extra in (
        ('a', 'working', {}), ('b', 'idle', {}), ('c', 'stopping', {}), ('d', 'interrupted', {}),
        ('e', 'error', {}), ('f', 'starting', {}), ('g', 'ready', {}), ('h', 'stopped', {}),
        ('i', 'read-only', {}), ('j', 'ready', {'execution': {'turns': [{'phase': 'running'}]}}),
        ('k', 'idle', {'workers': [{'status': 'idle'}, {'status': 'queued'}]}),
        ('l', 'idle', {'workers': [{'status': 'idle', 'persistent': True}]})))
    try:
        response = await client.get("/api/health?activity=1", headers=owner)
        assert (await response.json())["activity"] == {"working": 5}
    finally:
        sessions[:] = saved  # Synthetic rows only for this read; the app closes its real ones.


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


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("path", ["/", "/login"])
async def test_service_worker_forwarded_entry_navigation(aiohttp_client, tmp_path, method, path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    # Chromium's fetch(event.request) preserves navigate/cross-site but clears
    # destination and user activation on the forwarded document request.
    headers = {**NAVIGATION_HEADERS, "Sec-Fetch-Dest": "empty"}
    headers.pop("Sec-Fetch-User")
    response = await client.request(method, path, headers=headers, allow_redirects=False)
    if path == "/":
        assert response.status == 307 and response.headers["Location"] == "/login"
        response = await client.request(method, "/login", headers=headers, allow_redirects=False)
    assert response.status == 200
    assert (await client.get("/api/state")).status == 401
    headers["Cookie"] = "amplifier_unified_session=" + new_session(app["session_secret"])
    assert (await client.request(method, "/", headers=headers)).status == 200


@pytest.mark.parametrize("path", ["/api/state", "/api/actions", "/api/events", "/api/health",
                                 "/api/ca", "/api/canvas/private/document", "/api/attachments/private",
                                 "/index.html", "/setup", "/oauth/mcp/callback", "/oauth/mcp/complete"])
async def test_forwarded_navigation_does_not_exempt_other_routes(aiohttp_client, tmp_path, path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    headers = {**NAVIGATION_HEADERS, "Sec-Fetch-Dest": "empty",
               "Cookie": "amplifier_unified_session=" + new_session(app["session_secret"])}
    assert (await client.get(path, headers=headers, allow_redirects=False)).status == 403


@pytest.mark.parametrize("origin", ["https://attacker.example", "null",
                                   "android-app://com.microsoft.emmx", "", "http://127.0.0.1:8941"])
async def test_forwarded_navigation_with_origin_stays_blocked(aiohttp_client, tmp_path, origin):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    headers = {**NAVIGATION_HEADERS, "Sec-Fetch-Dest": "empty",
               "Host": "127.0.0.1:8941", "Origin": origin}
    assert (await client.get("/login", headers=headers)).status == 403


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def test_forwarded_entry_metadata_does_not_allow_mutations(aiohttp_client, tmp_path, monkeypatch, method):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    monkeypatch.setattr("amplifier_web.auth.authenticate_pam",
                        lambda *args: pytest.fail("cross-site mutation must not invoke PAM"))
    csrf = new_csrf(app["session_secret"])
    headers = {**NAVIGATION_HEADERS, "Sec-Fetch-Dest": "empty", "Cookie": f"{CSRF_COOKIE}={csrf}"}
    assert (await client.request(method, "/login", headers=headers, data={"csrf": csrf})).status == 403


async def test_forwarded_entry_requires_destination_and_known_host(aiohttp_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    headers = {**NAVIGATION_HEADERS}
    headers.pop("Sec-Fetch-Dest")
    assert (await client.get("/login", headers=headers)).status == 403
    headers.update({"Sec-Fetch-Dest": "empty", "Host": "attacker.example"})
    response = await client.get("/login", headers=headers)
    assert response.status == 403
    assert (await response.json())["error"] == "This Host is not configured."


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
                                            ("no-cors", "empty"), ("same-origin", "empty"),
                                            ("navigate", "iframe"), ("websocket", "empty"),
                                            ("", ""), ("", "empty"), ("navigate", ""),
                                            ("navigate", "frame"), ("navigate", "object"), ("navigate", "image")])
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


async def test_real_host_applies_sign_out_all_browsers_to_requests_and_open_streams(aiohttp_client, tmp_path, monkeypatch, recwarn):
    import asyncio
    from amplifier_web import auth
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    assert app["sessions"].epoch == 0 and not app["session_watch"].done()
    cookie = {"Cookie": f"amplifier_unified_session={new_session(app['session_secret'])}"}
    assert (await client.get("/api/state", headers=cookie)).status == 200
    stream = await client.get("/api/events", headers=cookie)
    assert stream.status == 200
    assert await stream.content.readany()  # The stream is open and delivering.
    # The owner's command writes the private record; it sends the host nothing.
    real = time.time
    monkeypatch.setattr(auth.time, "time", lambda: real() + 5)
    saved = auth.sign_out_all(tmp_path)
    # Until the host has applied it, a sign-in ticket or code opens no session.
    assert app["sessions"].epoch == 0 and auth.sign_in(app) is None and auth.sign_in(app, remote=True) is None
    # The running host notices by itself (its watcher looks every two seconds).
    for _ in range(100):
        if app["sessions"].epoch == saved:
            break
        await asyncio.sleep(0.1)
    assert app["sessions"].epoch == saved
    # The running application's own keys are not changed (a later aiohttp refuses that).
    assert not [str(row.message) for row in recwarn.list if "Changing state" in str(row.message)]
    # The same cookie is refused, for the API and for a page.
    assert (await client.get("/api/state", headers=cookie)).status == 401
    page = await client.get("/", headers=cookie, allow_redirects=False)
    assert page.status == 307 and page.headers["Location"].startswith("/login")
    # The open stream was ended by the host.
    await asyncio.wait_for(stream.content.read(), 5)
    assert stream.content.at_eof()
    # A sign-in after the sign-out works.
    monkeypatch.setattr(auth.time, "time", lambda: real() + 10)
    fresh = {"Cookie": f"amplifier_unified_session={new_session(app['session_secret'])}"}
    assert (await client.get("/api/state", headers=fresh)).status == 200
    opened = {"Cookie": f"amplifier_unified_session={auth.sign_in(app)}"}
    assert (await client.get("/api/state", headers=opened)).status == 200


async def test_real_host_serves_the_password_page_and_refuses_it_where_the_owner_is_not_proven(
        aiohttp_client, tmp_path, monkeypatch):
    """The real routes and the real middleware. This client speaks plain HTTP, so no form may show."""
    from amplifier_web import browser_entry
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    assert app["browser_key"] is None                       # Not a Windows host.
    app["browser_key"] = browser_entry.SavedKey()           # As a Windows host has it, for the second half.
    client = await aiohttp_client(app)
    cookie = {"Cookie": f"amplifier_unified_session={new_session(app['session_secret'])}"}
    # Not signed in: the sign-in page, as for every page of the app.
    anonymous = await client.get(browser_entry.PASSWORD_PATH, allow_redirects=False)
    assert anonymous.status == 307 and anonymous.headers["Location"].startswith("/login")
    # A host that is not a Windows host has no such password.
    page = await client.get(browser_entry.PASSWORD_PATH, headers=cookie)
    assert page.status == 200 and "<form" not in await page.text()
    assert "the password of the host account" in await page.text()
    assert page.headers["Cache-Control"] == "no-store" and page.headers["Referrer-Policy"] == "same-origin"
    assert (await client.post(browser_entry.PASSWORD_PATH, headers=cookie, data={"action": "remove"})).status == 403
    connect = await client.get("/connect", headers=cookie)
    assert connect.status == 200 and browser_entry.PASSWORD_PATH not in await connect.text()
    # A Windows host over plain HTTP: the owner is not proven, so there is no form and no card.
    monkeypatch.setattr(browser_entry, "is_windows", lambda: True)
    monkeypatch.setattr(browser_entry, "set_password", lambda *a: pytest.fail("A password was set"))
    refused = await client.get(browser_entry.PASSWORD_PATH, headers=cookie)
    assert refused.status == 403 and "<form" not in await refused.text()
    posted = await client.post(browser_entry.PASSWORD_PATH, headers={**cookie, "Origin": str(client.make_url("")).rstrip("/")},
                               data={"action": "set", "password": "correct horse battery", "confirm": "correct horse battery"})
    assert posted.status == 403
    assert browser_entry.PASSWORD_PATH not in await (await client.get("/connect", headers=cookie)).text()


async def test_page_devices_and_apps_has_the_password_card_first_on_the_pc_and_reads_as_utf_8(tmp_path, monkeypatch):
    """The real handler of the real app, for a browser of the PC over HTTPS, on a host whose text encoding is cp1252."""
    import ssl
    from pathlib import Path
    from types import SimpleNamespace
    from aiohttp.test_utils import make_mocked_request
    from amplifier_web import auth, browser_entry
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    app["browser_key"] = browser_entry.SavedKey()
    monkeypatch.setattr(browser_entry, "is_windows", lambda: True)
    read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, encoding=None, errors=None, newline=None: (
        read(self, encoding or "cp1252", errors, newline)))                 # As a Windows host reads a file.

    async def page(path, session, host="127.0.0.1:8941", peer="127.0.0.1"):
        request = make_mocked_request(
            "GET", path, headers={"Host": host, "Cookie": f"amplifier_unified_session={session}"}, app=app,
            transport=SimpleNamespace(get_extra_info=lambda name: (
                (peer if name == "peername" else "127.0.0.1", 8941) if name in {"peername", "sockname"} else None)),
            sslcontext=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER))
        match = await app.router.resolve(request)
        match.add_app(app)
        request._match_info = match
        return await auth.auth_required(request, match.handler)
    local = new_session(app["session_secret"])
    text = (await page("/connect", local)).text
    card = text.index("<h2>Sign in from a browser on another device</h2>")
    assert card < text.index("<h2>Use a browser on this computer</h2>") and text.count(browser_entry.PASSWORD_PATH) == 1
    assert f'<a class="button" href="{browser_entry.PASSWORD_PATH}">Set a password</a>' in text
    # The characters of the page are the page's own, not a second reading of their bytes.
    assert "extension\u2019s" in text and "Loading connections\u2026" in text and "\u00e2\u20ac" not in text
    script = (await page("/connect/assets/connect.js", local)).text
    assert "\u00e2\u20ac" not in script and "\u00c2\u00b7" not in script and "\u2026" in script
    # A browser that signed in with a password, and a browser at another address of the PC, get the page with no card.
    remote = auth.new_session(app["session_secret"], remote=True)
    for other in (await page("/connect", remote), await page("/connect", local, peer="192.168.1.9")):
        assert other.status == 200 and browser_entry.PASSWORD_PATH not in other.text
        assert "<h2>Use a browser on this computer</h2>" in other.text


@pytest.mark.parametrize("method,path", [
    ("GET", "/login"), ("POST", "/api/browser-entry/code"), ("POST", "/api/browser-entry/code/redeem"),
    ("POST", "/api/browser-entry/key/redeem")])
async def test_default_host_serves_no_sign_in_by_code_at_an_address_for_other_devices(aiohttp_client, tmp_path, method, path):
    """Remote access is off by default: the host answers only at this computer's own names."""
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False)
    assert app["server_config"]["public_origins"] == [] and app["server_config"]["bind"] == ["127.0.0.1"]
    assert app["permitted_hosts"] == frozenset({"127.0.0.1", "localhost", "::1"})
    client = await aiohttp_client(app)
    other = "pc.tail1234.ts.net:8941"
    response = await client.request(method, path, allow_redirects=False, data="csrf=x&code=ABCDEFGHJK", headers={
        "Host": other, "Origin": "https://" + other, "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": "Bearer " + app["control_token"]})
    # Refused at the host boundary, before the sign-in page or either code endpoint runs.
    assert response.status == 403 and (await response.json())["error"] == "This Host is not configured."
    assert "Set-Cookie" not in response.headers
