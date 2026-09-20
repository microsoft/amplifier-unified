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

    async def start(self, session, emit):
        await emit("runtime.status", {"sessionId": session["id"], "status": "ready"})

    async def send(self, session, text, input_id, emit):
        self.sent.append({"sessionId": session["id"], "text": text})
        await emit("assistant.message", {"sessionId": session["id"],
                   "inputId": input_id, "text": "Synthetic first response"})
        await emit("runtime.status", {"sessionId": session["id"], "status": "idle"})

    async def control(self, *args):
        return {}

    async def stop(self, *args):
        pass

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
    if '--retention' in sys.argv:
        from amplifier_web.runtime import RuntimeManager
        # Settings exercise the actual manager policy without starting a model.
        runtime = RuntimeManager(command=[sys.executable, '-c', 'raise RuntimeError("Unexpected worker start in settings fixture")'])
    app = await create_app(home / "app", workspace=str(workspace), runtime=runtime,
                           voice=False, background_updates=False)
    app["control_token"] = "fixture-browser-control-token"

    async def inspect(request):
        return web.json_response({"sent": getattr(runtime, 'sent', []),
            "retention": getattr(getattr(runtime, 'retention', None), 'settings', None),
            "workerCount": len(getattr(runtime, 'workers', {}))})

    app.router.add_get("/fixture", inspect)
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
