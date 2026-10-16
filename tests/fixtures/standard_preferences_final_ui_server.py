"""Owned synthetic preferences acceptance; notification delivery is observed and denied."""
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
    with tempfile.TemporaryDirectory(prefix="standard-preferences-final-") as directory:
        app = await settings_app(Path(directory))
        notifications = app["service"].management.notifications
        attempts = []

        async def denied_send(session, generation):
            attempts.append({"sessionId": session.get("id"), "generationId": generation.get("generation_id")})
            raise RuntimeError("Fixture refuses notification delivery")

        notifications.send = denied_send

        async def observe(request):
            if request.headers.get("Authorization") != "Bearer fixture-browser-control-token":
                raise web.HTTPUnauthorized()
            return web.json_response({"notificationSendAttempts": attempts, "publicPreferences": notifications.public()})

        app.router.add_get("/fixture/preference-observations", observe)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        app["allowed_origins"] |= {f"http://127.0.0.1:{port}"}
        runner = web.AppRunner(app)
        await runner.setup()
        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        try:
            await web.SockSite(runner, sock).start()
            print(json.dumps({"port": port}), flush=True)
            await stop.wait()
        finally:
            await runner.cleanup()
            sock.close()


if __name__ == "__main__":
    asyncio.run(main())
