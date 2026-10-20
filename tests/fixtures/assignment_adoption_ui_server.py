"""Production service and built UI with no provider or real work execution."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from coordination_ui_server import Runtime


async def main(home):
    from aiohttp import web
    from amplifier_web.server import create_app
    from amplifier_web.runtime import RuntimeManager

    workspace = home / "workspace"
    workspace.mkdir()
    shared = home / "shared"
    shared.mkdir()
    os.environ["AMPLIFIER_HOME"] = str(shared)
    (shared / "settings.yaml").write_text(json.dumps({"config": {"providers": [
        {"module": "provider-fixture", "id": "fixture",
         "config": {"default_model": "fixture-model"}}]}}))
    runtime = Runtime()
    runtime.stopped_boundary = RuntimeManager().stopped_boundary
    app = await create_app(home / "app", workspace=str(workspace), runtime=runtime,
                           voice=False, background_updates=False)
    app["control_token"] = "fixture-browser-control-token"
    service = runtime.service = app["service"]
    for title in ("Review manager", "Existing navigation work", "Independent work"):
        await service.dispatch("session.create", {"title": title})
    sessions = {row["title"]: row for row in service.state["sessions"]}
    source, target = sessions["Review manager"], sessions["Existing navigation work"]
    runtime.source_id, runtime.other_id = source["id"], target["id"]
    await service.dispatch("session.select", {"id": source["id"]})
    await service.dispatch("assignment.start", {"sessionId": source["id"]})
    service._message(target, "assistant", "Earlier result remains available.")
    service._publish_changes(sessions={target["id"]})

    async def inspect(request):
        return web.json_response({
            "source": source["id"], "target": target["id"],
            "assignment": service.assignments.current(source),
            "targetAssignment": service.assignments.current(target),
            "targetMessages": target["messages"],
            "sent": runtime.sent, "stops": runtime.stops,
            "targetStatus": target["status"],
            "deterministicRuntime": True, "providerCalls": False,
        })

    async def change(request):
        # A competing user's Stop invalidates the old UI observation.
        target["interruptionRevision"] = target.get("interruptionRevision", 0) + 1
        service._publish_changes(sessions={target["id"]})
        return web.json_response({"ok": True})

    app.router.add_get("/fixture", inspect)
    app.router.add_post("/fixture/change-target", change)
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
    output = Path(os.environ.get("AMPLIFIER_TEST_OUTPUT_DIR",
                                Path(__file__).resolve().parents[2] / "working-files"))
    output.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(Path(tempfile.mkdtemp(prefix="adoption-fixture-", dir=output))))
