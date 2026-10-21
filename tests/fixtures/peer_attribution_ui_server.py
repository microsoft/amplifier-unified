"""Synthetic receiving-only fixture. Model/provider starts are forbidden."""
import asyncio
import json
import hashlib
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

    async def collaboration_input(self, *args):
        self.sends += 1
        raise AssertionError("Receiving fixture cannot admit a notification")

    async def close(self):
        pass


async def main():
    # Receiving-only fixture: replace catalog/default discovery at its boundary
    # and fail any unexpected SDK/provider subprocess, even in a background task.
    probe_attempts = []
    query_workers = []
    spawn_exec = asyncio.create_subprocess_exec
    async def query_only_subprocess(*args, **kwargs):
        # Bounded shell reads use a real isolated query helper. It neither
        # discovers providers nor admits chat work; retain this production path.
        if args[:3] == (sys.executable, "-m", "amplifier_web.shell_query_worker"):
            query_workers.append("shell-query")
            return await spawn_exec(*args, **kwargs)
        return await forbidden_subprocess(*args, **kwargs)
    async def forbidden_subprocess(*args, **kwargs):
        probe_attempts.append([str(arg)[:200] for arg in args[:4]])
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
    asyncio.create_subprocess_exec = query_only_subprocess
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
        peer_text = "Exact **α** 🐈\n\n```text\nunchanged  \n```\n" + "\nLong peer detail retained exactly. " * 150
        async def send(identity, origin):
            args = {
                "sessionId": recipient["id"], "senderSessionId": sender["id"],
                "mode": "notify", "text": peer_text if identity == "visible-peer" else "Exact **α** 🐈\n\n```text\nunchanged  \n```\n"}
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
        # A real host-generated watch return stays queued behind a deliberately
        # busy fixture root; no provider or runtime work is admitted.
        recipient['status'] = 'working'
        await generation(service, recipient, 'fixture-observer-generation', ['fixture-observer-input'])
        watch = await agent_action(service, recipient, 'coordination.watch', {
            'sessionId': recipient['id'], 'targetSessionId': sender['id'],
            'condition': 'status_changed', 'expiresInSeconds': 60}, 'visible-watch')
        await service.on_runtime_event('runtime.status', {'sessionId': sender['id'], 'status': 'idle'})
        notification = service.collaboration.receipt(watch['subscription']['continuationId'])
        service._publish()

        async def facts(request):
            return web.json_response({"starts": runtime.starts, "sends": runtime.sends, "probeAttempts": probe_attempts,
                "queryWorkers": len(query_workers),
                "peerTextDigest": hashlib.sha256(peer_text.encode()).hexdigest(), "peerTextLength": len(peer_text.encode()),
                "agent": agent["messageId"], "human": human["messageId"], "older": older["messageId"],
                "quote": quoted["id"], "forged": forged["id"], "notification": notification['messageId'],
                "installedStatic": INSTALLED, "backendFile": str(Path(server.__file__).resolve())})
        app.router.add_get("/api/fixture/peerFacts", facts)
        async def react(request):
            args = await request.json()
            result = await service.app_bridge('dispatch', {'action': 'message.reaction', 'args': {
                'sessionId': recipient['id'], 'messageId': quoted['id'], 'emoji': '👍',
                'present': args['present']}}, recipient['id'])
            return web.json_response(result)
        app.router.add_post('/api/fixture/reaction', react)
        async def extend_history(request):
            # Exercise the actual >100-row cursor boundary only after proving
            # full-set receipt attribution. Partial windows cannot prove global
            # input uniqueness and deliberately keep neutral attribution.
            additions = [service._message(recipient, 'assistant', 'Earlier filler ' + str(i)) for i in range(65)]
            del recipient['messages'][-len(additions):]
            recipient['messages'][1:1] = additions
            service._publish()
            return web.json_response({'extended': True})
        app.router.add_post('/api/fixture/extendHistory', extend_history)


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
