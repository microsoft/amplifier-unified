"""Real HTTP/SSE and packaged assets; synthetic chats, no provider execution.

Run only inside the task's owned test instance. This source fixture does not
claim installed-wheel isolation. No reference records or rendered cards are
injected: the browser must use the real typed attachment action.
"""
import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import tempfile

from aiohttp import web
import settings_ui_server as fixture


class Runtime(fixture.Runtime):
    def __init__(self):
        super().__init__()
        self.effects = []

    async def start(self, session, emit, **kwargs):
        self.effects.append(["start", session["id"]])
        raise AssertionError("Chat references must not start a runtime")

    async def send(self, session, text, input_id, emit):
        self.effects.append(["send", session["id"]])
        raise AssertionError("Chat references must not send input")

    async def control(self, sid, operation, args):
        self.effects.append(["control", sid, operation])
        raise AssertionError("Chat references must not execute a runtime control")

    async def stop(self, sid, **kwargs):
        self.effects.append(["stop", sid])


fixture.Runtime = Runtime


async def main(home):
    app = await fixture.main(home)
    service = app["service"]
    source = service.state["selectedSessionId"]
    await service.dispatch("session.rename", {"id": source, "title": "Reference source"})
    await service.on_runtime_event("assistant.message", {
        "sessionId": source, "text": "Open the investigation chat to review its findings.",
        "generationId": "reference-fixture-generation",
    })
    message = service._session(source)["messages"][-1]["id"]
    await service.dispatch("session.create", {"title": "Investigation destination"})
    target = service.state["selectedSessionId"]
    await service.dispatch("session.create", {"title": "Unrelated conversation"})
    other = service.state["selectedSessionId"]
    await service.dispatch("session.select", {"id": source})
    service.state["updates"].update(items=[], available=0)
    service._publish()

    async def inspect(request):
        if request.headers.get("Authorization") != "Bearer fixture-browser-control-token":
            raise web.HTTPForbidden()
        return web.json_response({
            "source": source, "message": message, "target": target, "other": other,
            "effects": copy.deepcopy(service.runtime.effects),
            "messages": {sid: len(service._session(sid)["messages"])
                         for sid in (source, target, other)},
            "approvals": {sid: copy.deepcopy(service._session(sid).get("approvals", []))
                          for sid in (source, target, other)},
        })

    app.router.add_get("/fixture/chat-references", inspect)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    app["allowed_origins"] = app["allowed_origins"] | {url}
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM) if os.name == "posix" else ()
    for signum in signals:
        loop.add_signal_handler(signum, stopped.set)
    print(json.dumps({"url": url}), flush=True)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()
        for signum in signals:
            loop.remove_signal_handler(signum)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="chat-reference-fixture-") as folder:
        asyncio.run(main(Path(folder)))