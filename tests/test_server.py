import json
import asyncio
from pathlib import Path
import re

from amplifier_web.server import create_app
from test_service import Runtime


async def test_runtime_alias_tracks_service_replacement(tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=Runtime(), voice=False, background_updates=False)
    service = app['service']
    replacement = Runtime()
    await service.install_runtime(replacement)
    assert service.runtime is replacement
    assert app['runtime'] is replacement
    await service.close()


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


async def test_canvas_call_returns_original_result_and_preserves_fences(authenticated_client, tmp_path):
    from test_smart_canvas import Tools
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(), voice=False, background_updates=False)
    service = app['service']
    await service.smart_tools.close()
    service.smart_tools = Tools(service)
    tool_calls = []
    original_command = service.smart_tools.command

    async def counted_command(*args, **kwargs):
        tool_calls.append(args)
        return await original_command(*args, **kwargs)

    service.smart_tools.command = counted_command
    client = await authenticated_client(app)
    await service.dispatch('session.create', {'title': 'Interactive tool'})
    await service.smart_canvas.open({'id': 'one', 'tool': 'read'})
    cid = service.state['canvas']['id']
    url = f'/api/canvas/{cid}/tools/call'
    payload = {'id': 'live-input-once', 'name': 'read', 'arguments': {}}
    forbidden = await client.post(url, headers={'Origin': 'https://untrusted.example'}, json=payload)
    assert forbidden.status == 403
    response = await client.post(url, json=payload)
    assert response.status == 200
    operation = await response.json()
    assert operation['status'] == 'completed'
    assert operation['result']['structuredContent'] == {'count': 1}
    assert 'state' not in operation
    repeated = await (await client.post(url, json=payload)).json()
    assert repeated == operation
    assert len(service.state['smartTools']['operations']) == 1
    mismatch = await client.post(url, json={**payload, 'arguments': {'different': True}})
    assert mismatch.status == 409
    denied_payload = {'id': 'not-granted', 'name': 'ungranted'}
    denied_response = await client.post(url, json=denied_payload)
    assert denied_response.status == 200
    denied = await denied_response.json()
    assert denied['status'] == 'failed'
    assert 'not granted' in denied['error']
    assert len(tool_calls) == 1
    # Re-reading prior receipts while disconnected must not execute any tool.
    service.state['smartTools']['servers'][0]['status'] = 'disconnected'
    for prior_payload, receipt in ((payload, operation), (denied_payload, denied)):
        repeated = await client.post(url, json=prior_payload)
        assert repeated.status == 200
        assert await repeated.json() == receipt
    blocked = await client.post(url, json={**payload, 'id': 'disconnected-new'})
    assert blocked.status == 409
    assert 'disconnected' in (await blocked.json())['error'].lower()
    assert len(tool_calls) == 1
    assert len(service.state['smartTools']['operations']) == 2
    assert 'disconnected-new' not in service.smart_tool_requests
    await service.dispatch('canvas.close')
    closed = await client.post(url, json={**payload, 'id': 'closed-view'})
    assert closed.status == 409


async def test_open_event_stream_does_not_delay_host_shutdown(authenticated_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(), voice=False)
    client = await authenticated_client(app)
    response = await client.get("/api/events")
    assert await response.content.readline() == b"event: state\n"
    await asyncio.wait_for(app.shutdown(), 1)
    assert not app["service"].queues
    response.close()


async def test_slow_canvas_call_does_not_delay_host_shutdown(authenticated_client, tmp_path):
    import pytest
    from aiohttp import ClientConnectionError
    from test_smart_canvas import Tools
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(), voice=False, background_updates=False)
    service = app['service']
    await service.smart_tools.close()
    service.smart_tools = Tools(service)
    entered, gate = asyncio.Event(), asyncio.Event()
    original = service.smart_tools.command
    async def delayed(*args, **kwargs):
        entered.set()
        await gate.wait()
        return await original(*args, **kwargs)
    service.smart_tools.command = delayed
    client = await authenticated_client(app)
    await service.dispatch('session.create', {'title': 'Interactive tool'})
    await service.smart_canvas.open({'id': 'one', 'tool': 'read'})
    cid = service.state['canvas']['id']
    request = asyncio.create_task(client.post(f'/api/canvas/{cid}/tools/call', json={'id': 'slow-call', 'name': 'read'}))
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.wait_for(app.shutdown(), 1)
    with pytest.raises(ClientConnectionError):
        await request
    # Cancelling the HTTP waiter must not cancel or replay an admitted write.
    assert not service.smart_tool_requests['slow-call'].done()
    gate.set()
    assert (await service.wait_smart_tool('slow-call'))['status'] == 'completed'


async def test_html_canvas_is_separate_opaque_sandbox(authenticated_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(), voice=False, background_updates=False)
    client = await authenticated_client(app)
    await app['service'].dispatch('session.create', {})
    await app['service'].dispatch('canvas.show', {'kind':'html','content':'<button onclick="this.textContent=42">Test</button>'})
    identity = app['service'].state['canvas']['id']
    response = await client.get('/api/canvas/'+identity+'/document')
    assert response.status == 200
    csp = response.headers['Content-Security-Policy']
    assert "sandbox allow-scripts;" in csp and 'allow-same-origin' not in csp
    assert "connect-src 'none'" in csp and "form-action 'none'" in csp
    assert "default-src 'none'" in csp
    media = next(part.strip() for part in csp.split(";") if part.strip().startswith("media-src "))
    assert media == "media-src data: blob:"
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


async def test_large_html_is_served_separately_with_same_sandbox(authenticated_client,tmp_path):
    body='<h1>Large isolated file</h1><!--'+'x'*3_800_000+'-->'
    (tmp_path/'large.html').write_text(body)
    app=await create_app(tmp_path/'data',preload_providers=False,workspace=tmp_path,runtime=Runtime(),voice=False,background_updates=False)
    client=await authenticated_client(app)
    await app['service'].dispatch('session.create', {})
    baseline=len(await (await client.get('/api/state')).read())
    await app['service'].dispatch('canvas.show',{'kind':'auto','path':str(tmp_path/'large.html')})
    identity=app['service'].state['canvas']['id']
    document=await client.get(f'/api/canvas/{identity}/document')
    assert document.status==200 and body in await document.text()
    assert "sandbox allow-scripts;" in document.headers['Content-Security-Policy']
    assert "connect-src 'none'" in document.headers['Content-Security-Policy']
    source=await client.get(f'/api/canvas/{identity}/source')
    assert source.content_type=='text/plain' and await source.text()==body
    assert source.headers['X-Content-Type-Options']=='nosniff'
    saved=await client.get(f'/api/canvas/{identity}/download')
    assert 'attachment;' in saved.headers['Content-Disposition'] and await saved.text()==body
    assert len(await (await client.get('/api/state')).read())<baseline+20_000


async def test_surface_host_is_trusted_and_limits_child_navigation(authenticated_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=Runtime(), voice=False, background_updates=False)
    service = app['service']
    await service.dispatch('session.create', {})
    service.clients.attach('surface-owner')
    with service.clients.bind('surface-owner'):
        created = await service.dispatch('canvas.apps.create', {
            'title': 'Host boundary', 'content': '<p>Authored HTML stays in the child</p>',
            'manifest': {'version': 1, 'stateSchema': {'type': 'object'}}, 'initialState': {}})
        identity = created['result']['id']
        view = service.canvas_views.summary('primary')
    client = await authenticated_client(app)
    target = {k: str(view[k]) for k in ('viewId', 'resourceId', 'resourceRevision', 'generation')}
    target['clientId'] = 'surface-owner'
    response = await client.get(f'/api/canvas/{identity}/app-host', params=target)
    assert response.status == 200
    policy = response.headers['Content-Security-Policy']
    assert "script-src 'nonce-" in policy
    assert f'/api/canvas/{identity}/document;' in policy
    assert "frame-src 'self'" not in policy
    body = await response.text()
    assert 'Authored HTML stays in the child' not in body
    assert "child.sandbox = 'allow-scripts'" in body
    child = await client.get(f'/api/canvas/{identity}/document', params=target)
    assert "sandbox allow-scripts;" in child.headers['Content-Security-Policy']
    assert 'Authored HTML stays in the child' in await child.text()
    stale = await client.get(f'/api/canvas/{identity}/app-host', params={**target, 'generation': '99999'})
    assert stale.status == 409


async def test_publishing_error_response_preserves_unknown_receipt(authenticated_client, tmp_path, monkeypatch):
    from amplifier_publishing import PublishingError
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(), voice=False, background_updates=False)
    client = await authenticated_client(app)
    service = app['service']
    await service.dispatch('session.create', {'title': 'Publishing uncertainty'})
    sid = service._session()['id']
    receipt = {'requestId': 'unknown-import', 'sessionId': sid, 'state': 'unknown',
               'reconciliationError': {'code': 'invalid_response', 'message': 'Malformed remote receipt'}}
    async def unresolved(*args, **kwargs):
        raise PublishingError('unknown_outcome', 'The admitted operation remains unknown', receipt=receipt)
    monkeypatch.setattr(service.publishing, 'dispatch', unresolved)
    response = await client.post('/api/actions', json={'action': 'publishing.build', 'args': {
        'sessionId': sid, 'siteId': 'site', 'sourcePath': 'dist', 'requestId': 'unknown-import'}})
    payload = await response.json()
    assert response.status == 503 and payload['accepted'] is False
    assert payload['code'] == 'unknown_outcome' and payload['receipt'] == receipt
