"""Production host/assets, empty storage, synthetic runtime; no provider calls."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class Runtime:
    def __init__(self):
        self.sent = []
        self.stopped = []
        self.started = []

    async def start(self, session, emit):
        self.started.append(session["id"])
        await emit("runtime.status", {"sessionId": session["id"], "status": "ready"})

    async def send(self, session, text, input_id, emit):
        self.sent.append({"sessionId": session["id"], "text": text})
        if '--chat-controls' in sys.argv:
            self.sent[-1].update(workspace=session['workspace'], bundle=session['bundle'],
                                 selection=session.get('selection'), attachments=session['messages'][-1].get('attachments', []))
        if hasattr(self, 'service'):
            binding = session.get('surfaceInputs', {}).get(input_id, {})
            context = self.service.surface_context.manifest(session['id'], [binding])
            if context['surfaces']:
                self.sent[-1]['surfaceContext'] = context
        await emit("assistant.message", {"sessionId": session["id"],
                   "inputId": input_id, "text": text if "--canvas-versions" in sys.argv else "Synthetic first response"})
        await emit("runtime.status", {"sessionId": session["id"], "status": "idle"})

    async def control(self, *args):
        return {}

    async def stop(self, sid):
        self.stopped.append(sid)
        await self.service.on_runtime_event("runtime.status", {"sessionId": sid, "status": "idle"})

    async def close(self):
        pass


async def main(home):
    os.environ.update(
        AMPLIFIER_HOME=str(home / "shared-settings"),
        AMPLIFIER_WEB_HOME=str(home / "app"),
        AMPLIFIER_SESSION_STATE_HOME=str(home / "sessions"),
    )
    from aiohttp import web
    from amplifier_web.server import create_app

    workspace = home / "workspace"
    workspace.mkdir()
    runtime = Runtime()
    if '--chat-controls' in sys.argv:
        from amplifier_web.setup import SetupManager
        SetupManager.provider_rows=lambda self,workspace:[{'id':'test-provider','module':'provider-test','config':{'model':'first'},'enabled':True}]
        async def catalog(self,action,args,workspace):
            return {'modelsProviderId':'test-provider','models':[{'id':'first'},{'id':'chosen-model'}],
                    'providerMetadata':{'module':'provider-test','info':{'display_name':'Test provider'},
                    'configSchema':{'fields':[{'id':'reasoning_effort','choices':['low','high']}]}}}
        SetupManager.cached_probe=catalog
        import amplifier_web.draft_defaults as draft_defaults
        async def resolve_defaults(home,workspace,bundle=None,app_bundle=None,**kwargs):
            return {'bundle':bundle or 'work','effective':{'instance':'test-provider','model':'first'},
                    'providers':[{'id':'test-provider','info':{'display_name':'Test provider','defaults':{'model':'first'}},
                    'configSchema':{'fields':[{'id':'reasoning_effort','choices':['low','high']}]}}]}
        draft_defaults.resolve_defaults=resolve_defaults
    if '--retention' in sys.argv:
        from amplifier_web.runtime import RuntimeManager
        # Settings exercise the actual manager policy without starting a model.
        runtime = RuntimeManager(command=[sys.executable, '-c', 'raise RuntimeError("Unexpected worker start in settings fixture")'])
    app = await create_app(home / "app", workspace=str(workspace), runtime=runtime,
                           voice=False, background_updates=False)
    app["control_token"] = "fixture-browser-control-token"
    runtime.service = app['service']

    async def inspect(request):
        return web.json_response({"registeredWorkspaces": app["service"]._state["workspaces"], "sent": getattr(runtime, 'sent', []),
            "retention": getattr(getattr(runtime, 'retention', None), 'settings', None),
            "workerCount": len(getattr(runtime, 'workers', {})), "started": getattr(runtime, "started", []), "stopped": getattr(runtime, "stopped", [])})

    app.router.add_get("/fixture", inspect)
    if '--chat-controls' in sys.argv:
        async def activity(request):
            data = await request.json()
            service = app['service']
            session = service._session(data['sessionId'])
            session['status'] = data.get('status', 'working')
            session['workers'] = data.get('workers', [])
            service._publish()
            return web.json_response({'ok': True})

        async def damage(request):
            from amplifier_web.resource_files import root
            service = app['service']
            aid = (await request.json())['artifactId']
            row = next(row for row in service.state['canvasArtifacts'] if row['id'] == aid)
            identity = row['body']['$resource']
            (root(service.db) / (identity + '.json')).unlink()
            service.db.execute('DELETE FROM state_resources WHERE id=?', (identity,))
            for record in service.clients.records.values():
                if record.get('canvas', {}).get('id') == aid:
                    record['canvas'] = {'id': 'stale', 'sessionId': 'different-chat', 'open': False}
            service._last_storage_sweep = 0
            service._publish()
            return web.json_response({'ok': True})

        app.router.add_post('/fixture/activity', activity)
        app.router.add_post('/fixture/damage', damage)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    app["allowed_origins"] = app["allowed_origins"] | {url}
    print(json.dumps({"url": url}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="amplifier-empty-host-") as directory:
        asyncio.run(main(Path(directory)))
