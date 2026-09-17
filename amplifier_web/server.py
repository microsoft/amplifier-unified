"""Local-only HTTP host, event stream and bundled SPA delivery."""
from __future__ import annotations
import asyncio
import contextlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from .service import AppError, AppService
from . import __version__


@web.middleware
async def boundaries(request, handler):
    # A localhost app can run tools. Reject cross-origin websites and DNS rebinding.
    host = request.host.split(":")[0].strip("[]")
    if host not in {"localhost", "127.0.0.1", "::1"}:
        return web.json_response({"error": "This host only accepts loopback connections."}, status=403)
    origin = request.headers.get("Origin")
    if origin and urlsplit(origin).netloc != request.host:
        return web.json_response({"error": "Cross-origin requests are not permitted."}, status=403)
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return web.json_response({"error": "Cross-site requests are not permitted."}, status=403)
    try:
        response = await handler(request)
    except AppError as exc:
        return web.json_response({"error": str(exc), "accepted": False}, status=exc.status)
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        return web.json_response({"error": "Invalid request: " + str(exc), "accepted": False}, status=400)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store" if request.path.startswith("/api/") else "no-cache"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self' https://api.openai.com wss://api.openai.com; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'"
    return response


async def create_app(data_dir, workspace=None, runtime=None, voice=True, background_updates=True, preload_providers=True):
    app = web.Application(middlewares=[boundaries], client_max_size=13_000_000)
    service = AppService(Path(data_dir), runtime=runtime, workspace=workspace)
    import os
    service.port = int(os.environ.get("AMPLIFIER_WEB_PORT","8941"))
    if runtime is None:
        from .runtime import RuntimeManager
        runtime = RuntimeManager(app_bridge=service.app_bridge)
        service.runtime = runtime
        service.state["runtime"]["available"] = True
    app["service"] = service
    app["runtime"] = runtime
    from .management import Management
    service.management = Management(service)
    if preload_providers:
        service.management.background(service.management.command("providers.list",{}))
    from .updates import UpdateManager
    service.update_manager = UpdateManager(service)
    if background_updates:
        service.update_manager.task = asyncio.create_task(service.update_manager.loop())
    streams = set()

    async def state(request):
        return web.json_response(service.get_state())

    async def actions(request):
        if request.method == "GET":
            return web.json_response(service.get_actions())
        payload = await request.json()
        result = await service.dispatch(payload["action"], payload.get("args", {}), origin="ui", command_id=payload.get("id"), expected_revision=payload.get("expectedRevision"))
        return web.json_response(result)

    async def view(request):
        await service.update_device(await request.json())
        return web.json_response({"accepted": True})

    async def health(request):
        import hashlib
        return web.json_response({"ok": True, "app": "amplifier-unified", "version": __version__, "dataIdentity":hashlib.sha256(str(service.data_dir.resolve()).encode()).hexdigest(), "runtime": service.get_state()["runtime"]})

    async def events(request):
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        await response.prepare(request)
        queue = service.subscribe()
        task = asyncio.current_task()
        streams.add(task)
        try:
            snapshot = service.get_state()
            while True:
                await response.write(("event: state\nid: " + str(snapshot["revision"]) + "\ndata: " + json.dumps(snapshot) + "\n\n").encode())
                try:
                    snapshot = await asyncio.wait_for(queue.get(), 20)
                except TimeoutError:
                    await response.write(b": heartbeat\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            streams.discard(task)
            service.unsubscribe(queue)
        return response

    async def attachment(request):
        from .attachments import file_path
        try:path,row=file_path(service.data_dir,request.match_info['identity'])
        except (ValueError,FileNotFoundError):raise AppError('Attachment not found',404)
        from urllib.parse import quote
        response=web.FileResponse(path)
        response.headers['Content-Type']=row['mime']
        response.headers['Content-Disposition']=('inline' if row['mime'].startswith('image/') else 'attachment')+"; filename*=UTF-8''"+quote(row['name'])
        return response
    app.router.add_get('/api/attachments/{identity}',attachment)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/actions", actions)
    app.router.add_post("/api/actions", actions)
    app.router.add_post("/api/view", view)
    app.router.add_get("/api/events", events)
    if voice:
        from .voice import setup_routes
        service.voice_service = setup_routes(app)

    static = Path(__file__).parent / "static"
    async def index(request):
        file = static / "index.html"
        if not file.exists():
            raise AppError("Frontend assets are missing. Build frontend before packaging.", 503)
        return web.FileResponse(file)
    app.router.add_get("/", index)
    if static.exists():
        app.router.add_static("/", static, show_index=False)

    async def shutdown(app):
        # Event streams intentionally stay open; release them before aiohttp's
        # request-drain grace period so a restart does not wait a full minute.
        for task in list(streams):
            task.cancel()
        await asyncio.gather(*list(streams), return_exceptions=True)
    app.on_shutdown.append(shutdown)

    async def cleanup(app):
        await service.close()
    app.on_cleanup.append(cleanup)
    return app
