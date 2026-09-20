"""Real HTTP/SSE clients and worker, with a deterministic credential-free provider."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


async def main():
    root = Path(sys.argv[1]).resolve()
    workspace = root / "workspace"
    provider = root / "provider" / "amplifier_module_provider_fixture"
    workspace.mkdir(parents=True)
    provider.mkdir(parents=True)
    gates = root / "gates"
    gates.mkdir()
    provider.joinpath("__init__.py").write_text(
        f"GATES = {str(gates)!r}\n" + '''import asyncio
from pathlib import Path
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock
class FixtureProvider:
    name = "fixture"
    def get_info(self): return ProviderInfo(id="fixture", display_name="Fixture", defaults={"model":"fixture"})
    async def list_models(self): return []
    def parse_tool_calls(self, response): return []
    async def complete(self, request, **kwargs):
        users = [str(m.content) for m in request.messages if m.role == "user"]
        text = users[-1] if users else ""
        marker = next((key for key in ("terminal-first", "terminal-detach", "terminal-stop") if key in text), "other")
        with (Path(GATES) / (marker + ".calls")).open("a") as stream:
            stream.write("call\\n")
        if marker in {"terminal-detach", "terminal-stop"}:
            try:
                while not (Path(GATES) / (marker + ".release")).exists():
                    await asyncio.sleep(.02)
            except asyncio.CancelledError:
                (Path(GATES) / (marker + ".cancelled")).touch()
                raise
        return ChatResponse(content=[TextBlock(text="completed " + marker)])
async def mount(coordinator, config=None): await coordinator.mount("providers", FixtureProvider(), name="fixture")
''')
    from amplifier_module_context_simple import __file__ as context_module

    bundle = root / "fixture.md"
    bundle.write_text(f"""---
bundle:
  name: terminal-fixture
  version: 0.0.1
session:
  orchestrator:
    module: loop-live
  context:
    module: context-simple
    source: {Path(context_module).parent.parent}
providers:
  - module: provider-fixture
    source: {provider.parent}
---
Reply with the fixture response.
""")
    os.environ.update(
        AMPLIFIER_HOME=str(root / "amplifier-home"),
        AMPLIFIER_WEB_HOME=str(root / "home"),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / "legacy"),
        AMPLIFIER_SESSION_STATE_HOME=str(root / "shared"),
    )
    from aiohttp import web
    from amplifier_web.server import create_app
    from amplifier_web.session_client import SessionClient

    app = await create_app(root / "app", workspace=str(workspace), voice=False,
                           background_updates=False, preload_providers=False)
    service = app["service"]
    service.runtime.command = [sys.executable, str(ROOT / "amplifier_web/runtime_worker.py")]
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    app["allowed_origins"] = app["allowed_origins"] | {url}
    latest = {}
    tasks = []

    async def follow(client, sid):
        async for snapshot in client.snapshots(sid, reconnect=False):
            latest[client.client_id] = snapshot

    async def until(predicate):
        async with asyncio.timeout(45):
            while not predicate():
                for task in tasks:
                    if task.done() and not task.cancelled():
                        task.result()
                await asyncio.sleep(.02)

    def complete(client, marker):
        return any(m.get("text") == "completed " + marker for m in
                   latest.get(client, {}).get("session", {}).get("messages", []))

    try:
        async with SessionClient(url, app["control_token"], "terminal-b") as b:
            async with SessionClient(url, app["control_token"], "terminal-a") as a:
                created = await a.create_session({"title": "Terminal lifecycle", "bundle": str(bundle),
                                                   "workspace": str(workspace)}, command_id="create-terminal")
                sid = created["state"]["selectedSessionId"]
                tasks.extend(asyncio.create_task(follow(client, sid)) for client in (a, b))
                await until(lambda: len(latest) == 2)
                await a.command(sid, "conversation.send", {"text": "terminal-first"}, command_id="first")
                await until(lambda: all(complete(c, "terminal-first") for c in ("terminal-a", "terminal-b")))
                await until(lambda: latest["terminal-b"]["session"]["status"] == "idle")
                # Idle return plus host control is the activation-race regression path.
                await a.command(sid, "runtime.control", {"operation": "configuration.inspect"}, command_id="inspect")
                await a.command(sid, "conversation.send", {"text": "terminal-detach"}, command_id="detach")
                await until(lambda: (gates / "terminal-detach.calls").exists())
                duplicate = await a.command(sid, "conversation.send", {"text": "terminal-detach"}, command_id="detach")
                assert duplicate["duplicate"] is True
                tasks[0].cancel()
                with suppress(asyncio.CancelledError):
                    await tasks[0]
            # A has exited while the actual worker is still waiting on its provider.
            assert sid in service.runtime.workers
            (gates / "terminal-detach.release").touch()
            await until(lambda: complete("terminal-b", "terminal-detach"))
            async with SessionClient(url, app["control_token"], "terminal-a") as a:
                restored = await a.snapshot(sid)
                assert sum(m.get("text") == "terminal-detach" for m in restored["session"]["messages"]) == 1
                assert sum(m.get("text") == "completed terminal-detach" for m in restored["session"]["messages"]) == 1
                assert (gates / "terminal-detach.calls").read_text().splitlines() == ["call"]
                await a.command(sid, "conversation.send", {"text": "terminal-stop"}, command_id="stop-input")
                await until(lambda: (gates / "terminal-stop.calls").exists())
                await b.command(sid, "conversation.stop", {}, command_id="stop-run")
                await until(lambda: sid not in service.runtime.workers)
                await until(lambda: latest["terminal-b"]["session"]["status"] == "stopped")
                assert (gates / "terminal-stop.cancelled").exists()
                assert not complete("terminal-b", "terminal-stop")
                final = await a.snapshot(sid)
                assert final["session"]["status"] == "stopped"
            print(json.dumps({"two_live_clients": True, "idle_resume": True, "duplicate_runs_once": True,
                              "detach_keeps_work": True, "reconnect_restores_history": True,
                              "explicit_stop_cancels_provider": True}))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runner.cleanup()


asyncio.run(main())
