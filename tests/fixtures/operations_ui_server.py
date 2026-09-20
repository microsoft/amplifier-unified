"""Isolated operation evidence fixture; no provider or external command calls."""

import asyncio
import json
import tempfile
from pathlib import Path

import settings_ui_server as fixture
from aiohttp import web


def event(sequence, phase, **value):
    return {
        "schemaVersion": 1,
        "source": "tool-bash",
        "kind": "process",
        "operationId": "browser-operation",
        "ownerId": "fixture-mount",
        "eventId": f"browser-operation:{sequence}",
        "sequence": sequence,
        "phase": phase,
        "at": 1,
        **value,
    }


async def main(home):
    app = await fixture.main(home)
    service = app["service"]
    sid = service._session()["id"]
    await service.operations.observe(
        sid, sid, event(1, "started", status={"state": "running"})
    )
    await service.operations.observe(
        sid,
        sid,
        event(
            2,
            "output",
            chunk={
                "cursor": 0,
                "next_cursor": 1,
                "stream": "stdout",
                "text": "Build started\n",
                "source_bytes": 14,
                "binary_output_withheld": False,
            },
        ),
    )
    await service.dispatch(
        "view.update", {"patch": {"draft": "Keep this unsent draft"}}
    )

    async def finish(request):
        await service.operations.observe(
            sid,
            sid,
            event(
                3,
                "output",
                chunk={
                    "cursor": 1,
                    "next_cursor": 2,
                    "stream": "stdout",
                    "text": "Build finished\n",
                    "source_bytes": 15,
                    "binary_output_withheld": False,
                },
            ),
        )
        await service.operations.observe(
            sid,
            sid,
            event(
                4,
                "finished",
                status={
                    "state": "completed",
                    "returncode": 0,
                    "output_complete": True,
                    "total_output_bytes": 29,
                    "ended_at": 2,
                },
            ),
        )
        return web.json_response({"ok": True})

    app.router.add_post("/fixture/finish", finish)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    app["allowed_origins"] = app["allowed_origins"] | {url}
    print(json.dumps({"url": url, "sessionId": sid}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="amplifier-operations-ui-") as temp:
        asyncio.run(main(Path(temp)))
