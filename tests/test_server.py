import json
import asyncio
from pathlib import Path
import re

from amplifier_web.server import create_app
from test_service import Runtime


async def test_api_rejects_cross_origin_and_serves_state(aiohttp_client, tmp_path):
    client = await aiohttp_client(await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(), voice=False))
    response = await client.get("/api/health")
    assert response.status == 200
    bad = await client.post("/api/actions", headers={"Origin": "https://untrusted.example"}, json={"action": "session.create", "args": {}})
    assert bad.status == 403
    created = await client.post("/api/actions", json={"action": "session.create", "args": {}, "id": "session"})
    assert created.status == 200
    payload = await created.json()
    assert payload["state"]["selectedSessionId"]
    state = await client.get("/api/state")
    assert (await state.json())["revision"] == payload["revision"]


async def test_frontend_has_real_stylesheet_asset(aiohttp_client, tmp_path):
    client = await aiohttp_client(await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(), voice=False))
    response = await client.get("/")
    assert response.status == 200
    html = await response.text()
    links = re.findall(r'<link[^>]+href="([^"]+\.css)"', html)
    assert links, "Production index must link actual CSS before React runs"
    for link in links:
        css = await client.get(link)
        assert css.status == 200
        assert "text/css" in css.headers["Content-Type"]
        assert len(await css.text()) > 10000


async def test_open_event_stream_does_not_delay_host_shutdown(aiohttp_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(), voice=False)
    client = await aiohttp_client(app)
    response = await client.get("/api/events")
    assert await response.content.readline() == b"event: state\n"
    await asyncio.wait_for(app.shutdown(), 1)
    assert not app["service"].queues
    response.close()


async def test_html_canvas_is_separate_opaque_sandbox(aiohttp_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(), voice=False, background_updates=False)
    client = await aiohttp_client(app)
    await app['service'].dispatch('canvas.show', {'kind':'html','content':'<button onclick="this.textContent=42">Test</button>'})
    identity = app['service'].state['canvas']['id']
    response = await client.get('/api/canvas/'+identity+'/document')
    assert response.status == 200
    csp = response.headers['Content-Security-Policy']
    assert "sandbox allow-scripts;" in csp and 'allow-same-origin' not in csp
    assert "connect-src 'none'" in csp and "form-action 'none'" in csp
    assert "default-src 'none'" in csp
    assert 'canvas-render' in await response.text()
    parent = await client.get('/')
    assert "script-src 'self' 'wasm-unsafe-eval'" in parent.headers['Content-Security-Policy']
    assert "script-src 'unsafe-inline'" not in parent.headers['Content-Security-Policy']
    await app['service'].dispatch('canvas.show', {'kind':'text','content':'Replacement'})
    assert (await client.get('/api/canvas/'+identity+'/document')).status == 404
