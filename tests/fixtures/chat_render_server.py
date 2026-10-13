"""Real service and synthetic long chat for client render checks; no models."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from empty_host_ui_server import main
from amplifier_web import server
from aiohttp import web

original_create = server.create_app


async def create(*args, **kwargs):
    app = await original_create(*args, **kwargs)
    service = app["service"]
    await service.history.close()
    await service.event_log_view.close()
    await service.dispatch("session.create", {})
    session = service._session()
    now = time.time() - 120
    session.update(historyManaged=False, historyLoaded=True, historyLoading=False,
                   status="working", messages=[
        {"id": f"message-{i}", "role": "user" if i % 2 == 0 else "assistant",
         "createdAt": now + i, "text": f"Saved message {i}\n\n**Stable text**\n\n| Name | Value |\n| --- | --- |\n| sample | {i} |\n"}
        for i in range(80)])
    session["activity"] = {"phase": "composing", "label": "Synthetic work", "startedAt": now}
    service._publish()

    async def change(request):
        data = await request.json()
        async with service.lock:
            if "text" in data:
                session["messages"][-1]["text"] = data["text"]
            if "status" in data:
                session["status"] = data["status"]
            if "streaming" in data:
                session["streaming"] = data["streaming"]
            if "workers" in data:
                session["workers"] = data["workers"]
                session["execution"] = {"nodes": [{"id": "worker-test", "kind": "tool",
                    "turnId": "worker-turn", "phase": "completed", "anchorMessageId": "message-78"}],
                    "turns": [{"id": "worker-turn", "phase": "completed"}]}
            service._publish_changes(sessions={session["id"]}, detail_only=True)
        return web.json_response({"id": session["id"], "messages": len(session["messages"])})

    app.router.add_post("/fixture/render", change)
    return app


server.create_app = create
if __name__ == "__main__":
    sys.argv.append("--chat-controls")
    with tempfile.TemporaryDirectory(prefix="amplifier-chat-render-") as directory:
        asyncio.run(main(Path(directory)))