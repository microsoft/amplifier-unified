import json
import asyncio
from pathlib import Path
import re

from amplifier_web.server import create_app
from test_service import Runtime


async def test_api_rejects_cross_origin_and_serves_state(authenticated_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(), voice=False)
    client = await authenticated_client(app)
    response = await client.get("/api/health")
    assert response.status == 200
    bad = await client.post("/api/actions", headers={"Origin": "https://untrusted.example"}, json={"action": "session.create", "args": {}})
    assert bad.status == 403
    created = await client.post("/api/actions", json={"action": "session.create", "args": {}, "id": "session"})
    assert created.status == 200
    payload = await created.json()
    assert payload["state"]["selectedSessionId"]
    state = await client.get("/api/state")
    assert (await state.json())["revision"] >= payload["revision"]  # Background diagnostics may publish.


async def test_frontend_has_real_stylesheet_asset(authenticated_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(), voice=False)
    client = await authenticated_client(app)
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


async def test_open_event_stream_does_not_delay_host_shutdown(authenticated_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(), voice=False)
    client = await authenticated_client(app)
    response = await client.get("/api/events")
    assert await response.content.readline() == b"event: state\n"
    await asyncio.wait_for(app.shutdown(), 1)
    assert not app["service"].queues
    response.close()


async def test_html_canvas_is_separate_opaque_sandbox(authenticated_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(), voice=False, background_updates=False)
    client = await authenticated_client(app)
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


async def test_state_details_load_provenance_without_repeating_it_in_snapshots(authenticated_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(), voice=False, background_updates=False)
    client = await authenticated_client(app)
    await app['service'].dispatch('session.create',{})
    details={'agents':[{'name':'test','include_paths':['a'*20000]}]}
    app['service'].state['sessions'][0]['configuration']={'plan':{},'provenance':details}
    app['service']._save()
    snapshot=await (await client.get('/api/state')).json()
    assert '$resource' in snapshot['sessions'][0]['configuration']['provenance']
    page=await (await client.get('/api/state/detail',params={'path':'/sessions/0/configuration/provenance/agents/0/include_paths/0','offset':'16000'})).json()
    assert page['value']=='a'*4000 and page['nextOffset'] is None


async def test_diagnostics_rejects_unknown_test_ids_without_growing_state(authenticated_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(), voice=False, background_updates=False)
    client = await authenticated_client(app)
    for identity in ['absent-1', 'absent-2', 'absent-3']:
        response = await client.post('/api/actions', json={'action':'diagnostics.test','args':{'id':identity}})
        assert response.status == 400
    assert app['service'].diagnostics.results == {}
