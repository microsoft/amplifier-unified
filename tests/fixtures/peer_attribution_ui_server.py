"""Synthetic receiving-only provenance fixture. Any model/worker start is a failure."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
from aiohttp import web
from amplifier_web.server import create_app


class NoModel:
    def __init__(self):
        self.starts = self.sends = 0

    async def start(self, *args):
        self.starts += 1
        raise AssertionError("Receiving fixture cannot start a model")

    async def send(self, *args):
        self.sends += 1
        raise AssertionError("Receiving fixture cannot send to a model")

    async def close(self):
        pass


async def main():
    with tempfile.TemporaryDirectory(prefix="peer-attribution-fixture-") as directory:
        temp = Path(directory)
        os.environ.update(AMPLIFIER_HOME=str(temp / "native"), AMPLIFIER_WEB_HOME=str(temp / "app"),
                          AMPLIFIER_UNIFIED_IMPORT_HOME=str(temp / "legacy"),
                          AMPLIFIER_SESSION_STATE_HOME=str(temp / "session-state"))
        runtime = NoModel()
        app = await create_app(temp / "app", workspace=temp, runtime=runtime, voice=False,
                               background_updates=False, preload_providers=False)
        app["control_token"] = "fixture-peer-attribution"
        service = app["service"]
        if service.history.task:
            service.history.task.cancel()
            await asyncio.gather(service.history.task, return_exceptions=True)
            service.history.task = None
        await service.event_log_view.close()
        sender, recipient = [service._new_session({"title": title}) for title in
                             ("PRIVATE SOURCE TITLE", "Receiving fixture")]
        sender.update(id="private-source")
        recipient.update(id="receiving", historyLoaded=True, deferRuntimeUntilInteraction=True)
        service.state["sessions"] = [sender, recipient]
        service.state["selectedSessionId"] = recipient["id"]
        service.state["selectedWorkspaceId"] = recipient["workspaceId"]
        service.state["view"].update(navPinned=False, navExpanded=False, draft="Untouched draft")
        service._publish()
        grant = await service.dispatch("coordination.grant", {
            "sessionId": sender["id"], "participants": [recipient["id"]],
            "purpose": "Synthetic provenance fixture", "modes": ["notify"]}, command_id="fixture-grant")
        gid = grant["result"]["id"]
        async def send(identity, origin):
            return await service.dispatch("coordination.send", {
                "sessionId": recipient["id"], "senderSessionId": sender["id"],
                "grantId": gid, "mode": "notify", "text": "Exact **α** 🐈\n\n```text\nunchanged  \n```\n"},
                origin=origin, caller_session_id=sender["id"] if origin == "agent" else None,
                command_id=identity)
        older = await send("older-peer", "agent")
        for index in range(65):
            service._message(recipient, "assistant", "Retained answer " + str(index))
        agent = await send("visible-peer", "agent")
        human = await send("human-forward", "ui")
        quoted = service._message(recipient, "user", "Sent by Amplifier from another chat", inputId="literal-quote")
        forged = service._message(recipient, "user", "Unverified peer", "peer", inputId="forged",
                                  peerEnvelope={"senderSessionId": sender["id"]},
                                  attribution={"caption": "Sent by Amplifier from another chat"})
        await service.dispatch("coordination.revoke", {
            "sessionId": sender["id"], "grantId": gid}, command_id="fixture-revoke")
        service._publish()

        async def facts(request):
            return web.json_response({"starts": runtime.starts, "sends": runtime.sends,
                "agent": agent["messageId"], "human": human["messageId"], "older": older["messageId"],
                "quote": quoted["id"], "forged": forged["id"]})
        app.router.add_get("/api/fixture/peerFacts", facts)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        app["allowed_origins"] = app["allowed_origins"] | {url}
        print(json.dumps({"url": url}), flush=True)
        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        try:
            await stop.wait()
        finally:
            await runner.cleanup()


asyncio.run(main())