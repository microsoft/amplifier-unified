"""Production service/assets with deterministic worker events, no provider calls."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class Runtime:
    def __init__(self):
        self.messages, self.sent, self.stops = [], [], []

    async def start(self, session, emit):
        await emit("runtime.status", {"sessionId": session["id"], "status": "ready"})

    async def send(self, session, text, input_id, emit):
        self.sent.append({"sessionId": session["id"], "text": text, "inputId": input_id})
        return {"accepted": True}

    async def message_worker(self, sid, wid, text, input_id=None):
        self.messages.append({"sessionId": sid, "workerId": wid, "text": text, "inputId": input_id})
        await self.service.on_runtime_event("worker.updated", {"sessionId": sid, "id": wid, "status": "running"})
        return {"accepted": True, "completed": False, "inputId": input_id}

    async def stop_worker(self, sid, wid):
        self.stops.append({"sessionId": sid, "workerId": wid})
        await self.service.on_runtime_event("worker.updated", {"sessionId": sid, "id": wid, "status": "interrupted"})
        return {"accepted": True, "completed": False}

    async def stop(self, sid):
        self.stops.append({"sessionId": sid})

    async def close(self):
        pass


async def main(home):
    from aiohttp import web
    from amplifier_web.server import create_app
    workspace = home / "workspace"
    workspace.mkdir()
    os.environ["AMPLIFIER_HOME"] = str(home / "shared")
    runtime = Runtime()
    app = await create_app(home / "app", workspace=str(workspace), runtime=runtime, voice=False, background_updates=False)
    app["control_token"] = "fixture-browser-control-token"
    service = runtime.service = app["service"]
    for title in ("Selected conversation", "Other conversation"):
        await service.dispatch("session.create", {"title": title})
    selected = next(row for row in service.state["sessions"] if row["title"] == "Selected conversation")
    other = next(row for row in service.state["sessions"] if row["title"] == "Other conversation")
    await service.dispatch("session.select", {"id": selected["id"]})
    for wid, title in (("worker-a", "First worker"), ("worker-b", "Second worker")):
        await service.on_runtime_event("worker.updated", {"sessionId": other["id"], "id": wid, "name": title, "kind": "session", "parentSessionId": other["id"], "runId": wid + "-run", "status": "running", "persistent": True})

    async def inspect(request):
        return web.json_response({"selected": selected["id"], "other": other["id"], "messages": runtime.messages, "sent": runtime.sent, "stops": runtime.stops})

    async def emit(request):
        data = await request.json()
        await service.on_runtime_event("worker.updated", {"sessionId": other["id"], **data})
        return web.json_response({"ok": True})

    app.router.add_get("/fixture", inspect)
    app.router.add_post("/fixture/emit", emit)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    app["allowed_origins"] = app["allowed_origins"] | {url}
    print(json.dumps({"url": url}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="amplifier-coordination-") as directory:
        asyncio.run(main(Path(directory)))
