"""Two real browsers, real HTTP/SSE, isolated files, synthetic provider output."""
import asyncio
import json
from pathlib import Path
import tempfile

from aiohttp import web
import settings_ui_server as fixture


class Runtime(fixture.Runtime):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.stopped = []
        self.pending = {}

    async def send(self, session, text, input_id, emit):
        self.sent.append({"sessionId": session["id"], "text": text, "id": input_id})
        self.pending[input_id] = (session["id"], text, emit)
        await emit("assistant.delta", {"sessionId": session["id"], "text": "A live partial response"})

    async def finish(self):
        for input_id, (sid, text, emit) in list(self.pending.items()):
            await emit("assistant.message", {"sessionId": sid, "inputId": input_id, "text": "Finished: " + text})
            await emit("runtime.status", {"sessionId": sid, "status": "idle"})
            self.pending.pop(input_id)

    async def stop(self, sid):
        self.stopped.append(sid)


fixture.Runtime = Runtime


async def main(home):
    app = await fixture.main(home)
    service = app["service"]
    first = service.state["selectedSessionId"]
    await service.dispatch("session.rename", {"id": first, "title": "First conversation"})
    await service.dispatch("session.create", {"title": "Second conversation"})
    second = service.state["selectedSessionId"]
    service.state["updates"].update(items=[], available=0)

    async def inspect(request):
        return web.json_response({"first": first, "second": second,
            "sent": service.runtime.sent, "stopped": service.runtime.stopped})

    async def finish(request):
        await service.runtime.finish()
        return web.json_response({"ok": True})

    app.router.add_get("/fixture", inspect)
    app.router.add_post("/fixture/finish", finish)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    app["allowed_origins"] = app["allowed_origins"] | {f"http://127.0.0.1:{port}"}
    print(json.dumps({"url": f"http://127.0.0.1:{port}"}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="amplifier-live-clients-") as directory:
        asyncio.run(main(Path(directory)))
