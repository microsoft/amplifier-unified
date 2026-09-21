"""Isolated real-process computation UI fixture, no external provider calls."""
import asyncio
import json
import signal
import tempfile
from pathlib import Path

import settings_ui_server as fixture
from aiohttp import web
from computation_runtime import Runtime


async def main(home):
    app = await fixture.main(home)
    service = app["service"]
    service.runtime = Runtime(service, home / "workspace")
    sid = service._session()["id"]
    await service.dispatch("view.update", {"patch": {"draft": "Keep computation draft"}})
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    app["allowed_origins"] = app["allowed_origins"] | {url}
    print(json.dumps({"url": url, "sessionId": sid}), flush=True)
    stopped = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="amplifier-computation-ui-") as temp:
        asyncio.run(main(Path(temp)))
