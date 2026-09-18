from test_service import Runtime

from amplifier_web.auth import new_session
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
    login = await client.get("/login")
    assert login.status == 200 and "csrf" in (await login.text())
    client.session.headers["Authorization"] = "Bearer " + app["control_token"]
    assert (await client.get("/api/state")).status == 200


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
    csrf = login.cookies["amplifier_unified_csrf"].value
    response = await client.post("/login", headers={"Cookie": f"amplifier_unified_csrf={csrf}"},
                                 data={"username": "owner", "password": "correct", "csrf": csrf},
                                 allow_redirects=False)
    assert response.status == 303
    cookie = response.cookies["amplifier_unified_session"]
    assert cookie["domain"] == "" and cookie["httponly"] and cookie["samesite"].lower() == "strict"