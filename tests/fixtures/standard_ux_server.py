"""Private synthetic standard-UI discovery server; no real provider execution."""
import asyncio
import json
import signal
import socket
import sys
import tempfile
from pathlib import Path

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent))
from settings_ui_server import main as settings_app


async def main():
    with tempfile.TemporaryDirectory(prefix="standard-ux-") as directory:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        app = await settings_app(Path(directory))
        app["allowed_origins"] = app["allowed_origins"] | {f"http://127.0.0.1:{port}"}
        runner = web.AppRunner(app)
        await runner.setup()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        try:
            await web.SockSite(runner, sock).start()
            print(json.dumps({"port": port, "fixture": "standard-ux", "realProvider": False}), flush=True)
            await stop.wait()
        finally:
            await runner.cleanup()
            sock.close()


if __name__ == "__main__":
    asyncio.run(main())
