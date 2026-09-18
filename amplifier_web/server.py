"""Authenticated HTTP(S) host, event stream and bundled SPA delivery."""
from __future__ import annotations
import asyncio
import hmac
import json
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from . import __version__
from .auth import auth_required, control_token, data_identity, login_page, post_login, session_secret
from .deployment import canonical_host, load_server_config, validate_origin, validate_server
from .service import AppError, AppService
from .tls import ca_bytes


def _set_response_headers(response: web.StreamResponse, path: str) -> web.StreamResponse:
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Chromium sends Origin: null on navigation POSTs from a no-referrer
    # document. Keep the login form same-origin without leaking referrers to
    # other sites; do not weaken the Origin check to accept opaque origins.
    response.headers["Referrer-Policy"] = "same-origin" if path == "/login" else "no-referrer"
    response.headers["Cache-Control"] = "no-store" if path.startswith("/api/") else "no-cache"
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; frame-src 'self' http: https:; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self' https://api.openai.com wss://api.openai.com; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'")
    return response


@web.middleware
async def boundaries(request, handler):
    """Reject rebinding hosts and cross-origin callers before app actions."""
    try:
        host = canonical_host(urlsplit("//" + request.host).hostname or "")
    except ValueError:
        host = ""
    if host not in request.app["permitted_hosts"]:
        return web.json_response({"error": "This Host is not configured."}, status=403)
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        _set_response_headers(exc, request.path)
        raise
    except AppError as exc:
        return _set_response_headers(web.json_response({"error": str(exc), "accepted": False}, status=exc.status), request.path)
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        return _set_response_headers(web.json_response({"error": "Invalid request: " + str(exc), "accepted": False}, status=400), request.path)
    return _set_response_headers(response, request.path)


async def create_app(data_dir, workspace=None, runtime=None, voice=True, background_updates=True,
                     preload_providers=True, server_config=None):
    data_dir = Path(data_dir).expanduser().resolve()
    config = validate_server(server_config) if server_config is not None else load_server_config(data_dir)
    app = web.Application(middlewares=[boundaries, auth_required], client_max_size=13_000_000)
    app["server_config"] = config
    app["permitted_hosts"] = frozenset({"localhost", "127.0.0.1", "::1"} |
                                       {canonical_host(urlsplit(origin).hostname) for origin in config["public_origins"]})
    app["allowed_origins"] = frozenset(config["public_origins"]) | frozenset(
        validate_origin(f"{scheme}://{host}:{config['port']}")
        for scheme in ("http", "https")
        for host in ("localhost", "127.0.0.1", "[::1]")
    )
    app["session_secret"] = session_secret(data_dir)
    app["control_token"] = control_token(data_dir)
    service = AppService(data_dir, runtime=runtime, workspace=workspace)
    service.port = config["port"]
    if runtime is None:
        from .runtime import RuntimeManager
        runtime = RuntimeManager(app_bridge=service.app_bridge)
        service.runtime = runtime
        service.state["runtime"]["available"] = True
    app["service"] = service
    app["runtime"] = runtime
    from .smart_tools import SmartToolsManager
    from .smart_canvas import SmartCanvas
    service.smart_tools = SmartToolsManager(service)
    service.smart_canvas = SmartCanvas(service)
    service._publish()
    from .management import Management
    service.management = Management(service)
    if preload_providers:
        service.management.background(service.management.command("providers.list", {}))
    from .updates import UpdateManager
    service.update_manager = UpdateManager(service)
    if background_updates:
        service.update_manager.task = asyncio.create_task(service.update_manager.loop())
    streams = set()

    async def state(request):
        return web.json_response(service.get_state())

    async def state_detail(request):
        from .agent_state import read_state
        args = {"path": request.query.get("path", "")}
        for key in ("offset", "limit", "revision"):
            if key in request.query:
                args[key] = int(request.query[key])
        return web.json_response(read_state(service.get_state(), args, resolve=service.state_resource))

    async def actions(request):
        if request.method == "GET":
            return web.json_response(service.get_actions())
        payload = await request.json()
        result = await service.dispatch(payload["action"], payload.get("args", {}), origin="ui",
                                        command_id=payload.get("id"), expected_revision=payload.get("expectedRevision"))
        return web.json_response(result)

    async def view(request):
        await service.update_device(await request.json())
        return web.json_response({"accepted": True})

    async def health(request):
        response = {"ok": True, "app": "amplifier-unified", "version": __version__}
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer ") and hmac.compare_digest(authorization[7:], app["control_token"]):
            response["dataIdentity"] = data_identity(service.data_dir)
            response["runtime"] = service.get_state()["runtime"]
        return web.json_response(response)

    async def certificate(request):
        certificate = ca_bytes(data_dir)
        if certificate is None:
            raise web.HTTPNotFound(text="A local CA is not configured.")
        return web.Response(body=certificate, content_type="application/x-x509-ca-cert",
                            headers={"Content-Disposition": 'attachment; filename="amplifier-unified-ca.crt"'})

    async def setup(request):
        available = ca_bytes(data_dir) is not None
        body = "<!doctype html><title>Amplifier Unified setup</title><h1>Amplifier Unified setup</h1>"
        body += '<p><a href="/ca.crt">Download this host’s local CA certificate</a>.</p>' if available else "<p>Run <code>amplifier-unified setup-tls</code> on the host.</p>"
        return web.Response(text=body, content_type="text/html")

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
        try:
            path, row = file_path(service.data_dir, request.match_info["identity"])
        except (ValueError, FileNotFoundError):
            raise AppError("Attachment not found", 404)
        from urllib.parse import quote
        response = web.FileResponse(path)
        response.headers["Content-Type"] = row["mime"]
        response.headers["Content-Disposition"] = ("inline" if row["mime"].startswith("image/") else "attachment") + "; filename*=UTF-8''" + quote(row["name"])
        return response

    from .canvas_documents import canvas_source

    async def canvas_download(request):
        from .state_storage import resource
        identity = request.match_info["identity"]
        row = next((row for row in service.state.get("canvasArtifacts", []) if row["id"] == identity), None)
        if not row:
            raise AppError("Canvas artifact unavailable", 404)
        canvas = {**row, **resource(service.db, row["body"]["$resource"])}
        return web.Response(text=canvas_source(canvas), content_type="text/html",
                            headers={"Content-Disposition": 'attachment; filename="canvas-3d.html"'})

    async def canvas_document(request):
        canvas = service.state.get("canvas", {})
        if canvas.get("id") != request.match_info["identity"] or canvas.get("kind") not in {"html", "babylon", "mcp-app"}:
            raise AppError("Canvas document no longer available", 404)
        if canvas.get("kind") == "mcp-app":
            from .smart_canvas import document_response
            service.smart_canvas.binding(canvas["id"])
            return document_response(canvas)
        identity = json.dumps(canvas["id"])
        bootstrap = "<!doctype html><script data-canvas-bridge>" + (Path(__file__).parent / "canvas_bridge.js").read_text().replace("__CANVAS_ID__", identity) + "</script>"
        return web.Response(text=bootstrap + canvas_source(canvas), content_type="text/html", headers={
            "Content-Security-Policy": "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), clipboard-read=(), clipboard-write=()"})

    async def smart_canvas_tools(request):
        _, binding = service.smart_canvas.binding(request.match_info['identity'])
        server = next(s for s in service.state['smartTools']['servers'] if s['id'] == binding['serverId'])
        tools = [t for t in server.get('tools',[]) if t['name'] in binding['allowedTools']]
        return web.json_response({'tools':tools})

    app.router.add_get('/api/canvas/{identity}/tools', smart_canvas_tools)

    async def smart_operation(request):
        operation = service.smart_tools.operation(request.match_info['identity'])
        if not operation:
            return web.json_response({'status':'pending'}, status=200)
        return web.json_response(operation)

    app.router.add_get('/api/smart-tools/operations/{identity}', smart_operation)
    app.router.add_get("/login", login_page)
    app.router.add_post("/login", post_login)
    app.router.add_get("/setup", setup)
    app.router.add_get("/api/ca", certificate)
    app.router.add_get("/ca.crt", certificate)
    app.router.add_get("/api/canvas/{identity}/download", canvas_download)
    app.router.add_get("/api/canvas/{identity}/document", canvas_document)
    app.router.add_get("/api/attachments/{identity}", attachment)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/state/detail", state_detail)
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
        for task in list(streams):
            task.cancel()
        await asyncio.gather(*list(streams), return_exceptions=True)

    app.on_shutdown.append(shutdown)

    async def cleanup(app):
        await service.close()

    app.on_cleanup.append(cleanup)
    return app