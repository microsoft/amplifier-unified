"""Synthetic receiving-only provenance fixture. Any model/worker start is a failure."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

ROOT = Path(sys.argv[1]).resolve()
INSTALLED = os.environ.get("AMPLIFIER_TEST_INSTALLED_STATIC") == "1"
if not INSTALLED:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from aiohttp import web
from amplifier_web import server
from amplifier_web.server import create_app
from test_collaborative_workspaces import generation, agent_action


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
    # Receiving-only fixture: replace catalog/default discovery at its boundary
    # and fail any unexpected SDK/provider subprocess, even in a background task.
    probe_attempts = []
    async def forbidden_subprocess(*args, **kwargs):
        probe_attempts.append("subprocess")
        raise AssertionError("Receiving fixture cannot spawn a provider or worker")
    async def forbidden_probe(*args, **kwargs):
        probe_attempts.append("provider-probe")
        raise AssertionError("Receiving fixture cannot probe a provider")
    from amplifier_web.setup import SetupManager
    import amplifier_web.draft_defaults as defaults
    SetupManager.provider_rows = lambda self, workspace: []
    SetupManager.probe = forbidden_probe
    async def fixture_defaults(home, workspace, bundle=None, app_bundle=None, **kwargs):
        return {"bundle": bundle or "work", "effective": {}, "providers": []}
    defaults.resolve_defaults = fixture_defaults
    asyncio.create_subprocess_exec = forbidden_subprocess
    asyncio.create_subprocess_shell = forbidden_subprocess
    if INSTALLED:
        backend = Path(server.__file__).resolve()
        if backend.is_relative_to(ROOT):
            raise RuntimeError("Installed-static trial imported checkout source, not the installed candidate")
        if not (backend.parent / "static" / "index.html").is_file():
            raise RuntimeError("Installed candidate has no packaged static index")
    with tempfile.TemporaryDirectory(prefix="peer-attribution-fixture-", dir=ROOT) as directory:
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
        recipient.update(id="receiving", historyLoaded=True, deferRuntimeUntilInteraction=True,
                         draft="Untouched draft")
        service.state["sessions"] = [sender, recipient]
        service.state["selectedSessionId"] = recipient["id"]
        service.state["selectedWorkspaceId"] = next(row["id"] for row in service.state["workspaces"]
            if row.get("path") == recipient["workspace"])
        service.state["view"].update(navPinned=False, navExpanded=False, draft="Untouched draft")
        service._publish()
        # Synthetic host transport evidence, not a model start or caller-forged
        # generation. Notify retains an input without admitting a runtime turn.
        await generation(service, sender, "fixture-source-generation", ["fixture-source-input"])
        async def send(identity, origin):
            args = {
                "sessionId": recipient["id"], "senderSessionId": sender["id"],
                "mode": "notify", "text": "Exact **α** 🐈\n\n```text\nunchanged  \n```\n"}
            if origin == "agent":
                return await agent_action(service, sender, "coordination.send", args, identity)
            return await service.dispatch("coordination.send", args, origin=origin, command_id=identity)
        older = await send("older-peer", "agent")
        for index in range(65):
            service._message(recipient, "assistant", "Retained answer " + str(index))
        agent = await send("visible-peer", "agent")
        human = await send("human-forward", "ui")
        quoted = service._message(recipient, "user", "Sent by Amplifier from another chat", inputId="literal-quote")
        forged = service._message(recipient, "user", "Unverified peer", "peer", inputId="forged",
                                  peerEnvelope={"senderSessionId": sender["id"]},
                                  attribution={"caption": "Sent by Amplifier from another chat"})
        service.db.execute("INSERT INTO commands VALUES(?,?,?)", (
            "historic-revoked-grant", "fixture-historical",
            json.dumps({"accepted": True, "commandAction": "coordination.grant",
                        "result": {"id": "historic-revoked-grant", "revoked": True}})))
        service._publish()

        async def facts(request):
            return web.json_response({"starts": runtime.starts, "sends": runtime.sends, "probeAttempts": probe_attempts,
                "agent": agent["messageId"], "human": human["messageId"], "older": older["messageId"],
                "quote": quoted["id"], "forged": forged["id"],
                "installedStatic": INSTALLED, "backendFile": str(Path(server.__file__).resolve())})
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