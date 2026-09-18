"""Optional real-browser guard: fetch-metadata and form Origin come from Chromium."""
import pytest
from aiohttp import web

from test_service import Runtime
from amplifier_web.server import create_app
from amplifier_web.tls import setup_local_ca, ssl_context

playwright = pytest.importorskip("playwright.async_api", reason="Run with --with playwright for the browser auth smoke")


async def test_external_link_then_same_origin_login(aiohttp_server, unused_tcp_port, tmp_path, monkeypatch):
    port = unused_tcp_port
    origin = f"https://127.0.0.1:{port}"
    config = setup_local_ca(tmp_path, {"bind": ["127.0.0.1"], "port": port,
                                      "public_origins": [origin], "session_ttl_seconds": 60})
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                           preload_providers=False, background_updates=False, server_config=config)
    requests = []

    @web.middleware
    async def observe(request, handler):
        # Deliberately never record passwords, cookies, bodies, or CSRF values.
        requests.append((request.method, request.path, request.headers.get("Sec-Fetch-Site"),
                         request.headers.get("Origin")))
        return await handler(request)

    app.middlewares.insert(0, observe)
    await aiohttp_server(app, port=port, scheme="https", ssl=ssl_context(tmp_path, config))
    source_app = web.Application()

    async def links(_):
        return web.Response(text=f'<a id="entry" href="{origin}/">Enter</a>'
                            f'<a id="api" href="{origin}/api/state">API</a>', content_type="text/html")

    source_app.router.add_get("/", links)
    source = await aiohttp_server(source_app)
    external = f"http://localhost:{source.port}"
    pam_calls = []

    def fixture_pam(username, password):
        pam_calls.append(username)
        return username == "fixture-user" and password == "fixture-only"

    monkeypatch.setattr("amplifier_web.auth.authenticate_pam", fixture_pam)
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            # Only this disposable test browser trusts the freshly generated
            # fixture certificate. No OS trust store or live host is touched.
            context = await browser.new_context(ignore_https_errors=True)
            page = await context.new_page()
            await page.goto(external)
            await page.locator("#entry").click()
            await page.wait_for_url(origin + "/login")
            await page.get_by_label("Username").wait_for()
            assert ("GET", "/", "cross-site", None) in requests
            assert ("GET", "/login", "cross-site", None) in requests
            assert await page.evaluate("fetch('/api/state').then(r=>r.status)") == 401
            await page.get_by_label("Username").fill("fixture-user")
            await page.get_by_label("Password").fill("fixture-only")
            async with page.expect_response(lambda r: r.request.method == "POST" and "/login" in r.url) as result:
                await page.get_by_role("button", name="Sign in").click()
            assert (await result.value).status == 303
            await page.wait_for_url(origin + "/")
            assert ("POST", "/login", "same-origin", origin) in requests
            assert pam_calls == ["fixture-user"]
            assert await page.evaluate("fetch('/api/state').then(r=>r.status)") == 200
            await page.goto(external)
            async with page.expect_response(origin + "/api/state") as result:
                await page.locator("#api").click()
            assert (await result.value).status == 403
        finally:
            await browser.close()