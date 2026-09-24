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
from .runtime import RuntimeOperationPending
from .live_clients import client_context
from .setup_page import detect_platform, render_setup_page
from .tls import ca_bytes


def _set_response_headers(response: web.StreamResponse, path: str) -> web.StreamResponse:
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Chromium sends Origin: null on navigation POSTs from a no-referrer
    # document. Keep the login form same-origin without leaking referrers to
    # other sites; do not weaken the Origin check to accept opaque origins.
    response.headers["Referrer-Policy"] = "same-origin" if path == "/login" else "no-referrer"
    response.headers["Cache-Control"] = "no-store" if path.startswith(("/api/", "/share/")) or path in {"/login", "/oauth/mcp/callback", "/oauth/mcp/complete"} else "no-cache"
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
    except RuntimeOperationPending as exc:
        payload = {'error': str(exc), 'code': 'runtime_pending'}
        if exc.operation == 'send':
            payload['delivery'] = 'unknown'
        return _set_response_headers(web.json_response(payload, status=504), request.path)
    except AppError as exc:
        payload = {"error": str(exc), "accepted": False}
        if exc.code:
            payload['code'] = exc.code
        if exc.receipt is not None:
            payload['receipt'] = exc.receipt
        if exc.code == 'session_busy':
            payload['state'] = getattr(exc, 'client_state', None) or request.app['service'].browser_state()
        return _set_response_headers(web.json_response(payload, status=exc.status), request.path)
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        return _set_response_headers(web.json_response({"error": "Invalid request: " + str(exc), "accepted": False}, status=400), request.path)
    return _set_response_headers(response, request.path)


async def create_app(data_dir, workspace=None, runtime=None, voice=True, background_updates=True,
                     preload_providers=True, server_config=None):
    data_dir = Path(data_dir).expanduser().resolve()
    config = validate_server(server_config) if server_config is not None else load_server_config(data_dir)
    app = web.Application(middlewares=[boundaries, auth_required, client_context], client_max_size=13_000_000)
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
    service.server_config = config
    if runtime is None:
        from .runtime import RuntimeManager
        runtime = RuntimeManager(app_bridge=service.app_bridge, retention=config["runtime"])
        await service.install_runtime(runtime)
        service.state["runtime"]["available"] = True
    if hasattr(runtime, 'retention'):
        service.state['runtime']['retention'] = dict(runtime.retention.settings)
    app["service"] = service

    async def mcp_oauth_callback(request):
        await service.smart_tools.oauth.callback(request.query, validate_origin(f"{request.scheme}://{request.host}"))
        raise web.HTTPSeeOther("/oauth/mcp/complete", headers={"Cache-Control":"no-store", "Referrer-Policy":"no-referrer"})

    async def mcp_oauth_complete(request):
        return web.Response(text="Authorization response received. Return to Amplifier Unified to check the connection.")

    app.router.add_get("/oauth/mcp/callback", mcp_oauth_callback, allow_head=False)
    app.router.add_get("/oauth/mcp/complete", mcp_oauth_complete)
    service.bind_runtime_alias(lambda current: app.__setitem__("runtime", current))
    from .voice_visual import setup_routes as visual_routes
    visual_routes(app)
    from .computer_visual import setup_routes as computer_visual_routes
    computer_visual_routes(app)
    from .terminal_setup import setup_routes as setup_terminal
    setup_terminal(app)
    from .smart_tools import SmartToolsManager
    from .smart_canvas import SmartCanvas
    service.smart_tools = SmartToolsManager(service)
    service.smart_canvas = SmartCanvas(service)
    service.diagnostics.start()
    service._publish()
    from .management import Management
    service.management = Management(service)
    service.schedules.start()
    service.worktrees.start()
    service.history.start()
    service.event_log_view.start()
    if preload_providers:
        service.management.background(service.management.command("providers.list", {}))
    from .updates import UpdateManager
    service.update_manager = UpdateManager(service)
    if background_updates:
        service.update_manager.task = asyncio.create_task(service.update_manager.loop())
    streams = set()
    call_waiters = set()

    async def state(request):
        session_id = request.query.get('sessionId')
        if session_id is not None and (not session_id or len(session_id) > 200):
            raise AppError("Choose a valid conversation ID.")
        await service._flush_pending_progress()
        return web.json_response(service.browser_state(session_id=session_id))

    async def state_detail(request):
        from .agent_state import read_state
        args = {"path": request.query.get("path", "")}
        for key in ("offset", "limit", "revision"):
            if key in request.query:
                args[key] = int(request.query[key])
        await service._flush_pending_progress()
        return web.json_response(read_state(service.state_context(), args, resolve=service.state_resource))

    async def conversation_detail(request):
        from .browser_detail import page, read_text
        session = service._session(request.query.get('sessionId'))
        if 'field' in request.query:
            return web.json_response(await asyncio.to_thread(read_text, session, dict(request.query)))
        return web.json_response(page(session, request.query.get('part'), request.query.get('before')))

    async def conversation_export(request):
        snapshot = service.state.get('conversationExports', {}).get(request.match_info['identity'])
        if snapshot is None:
            raise AppError('This conversation export is unavailable.', 404)
        return web.json_response({'content': service.state_resource(snapshot['content']['$resource']),
                                  'filename': snapshot['filename'], 'mimeType': snapshot['mimeType']},
                                 headers={'Cache-Control': 'no-store'})

    async def output_content(request):
        from urllib.parse import quote
        record=service.outputs.store.read(request.match_info['identity'])
        data=service.outputs.content(record)
        if data is None:
            raise AppError('This output is an external reference without saved content.',404)
        filename=Path(record.get('filename','output')).name
        return web.Response(body=data,content_type='application/octet-stream',headers={
            'Content-Disposition': "attachment; filename*=UTF-8''"+quote(filename,safe=''),
            'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'; sandbox"})

    async def output_image(request):
        import base64
        record=service.outputs.store.read(request.match_info['identity'])
        try:
            value=service.outputs.image(record['sessionId'],record['id'],record.get('sha256'))
        except ValueError as exc:
            raise AppError(str(exc),409) from None
        return web.Response(body=base64.b64decode(value['_image']),content_type='image/png',headers={
            'Content-Security-Policy': "default-src 'none'; frame-ancestors 'self'; sandbox"})

    async def actions(request):
        if request.method == "GET":
            return web.json_response(service.get_actions())
        payload = await request.json()
        if isinstance(payload, dict) and payload.get('action') == 'terminal.prepare':
            from .terminal_setup import require_safe_transport
            require_safe_transport(request)
        compact = request.headers.get('X-Amplifier-State-Transport') == 'delta-v1'
        task = service._task(service.dispatch(payload["action"], payload.get("args", {}), origin="ui",
                                        command_id=payload.get("id"), expected_revision=payload.get("expectedRevision"),
                                        include_state=not compact))
        result = await asyncio.shield(task)
        if compact:
            # Some delegated action families return their own snapshot. Keep
            # their receipt/effects, but use the same stream synchronization.
            result = {key: value for key, value in result.items() if key != 'state'}
            # Shell-only receipts have their own revision domain and notify
            # /api/shell. They must not wait for an unrelated app-state frame.
            if not payload['action'].startswith('shell.') or payload['action'] == 'shell.command':
                result['stateRevision'] = result.get('revision', service._state['revision'])
            result['hostInstanceId'] = service.instance_id
        return web.json_response(result)

    async def view(request):
        await service.update_device(await request.json())
        return web.json_response({"accepted": True})

    async def health(request):
        response = {"ok": True, "app": "amplifier-unified", "version": __version__}
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer ") and hmac.compare_digest(authorization[7:], app["control_token"]):
            response["dataIdentity"] = data_identity(service.data_dir)
            response.update({key: service.update_manager.running_identity[key] for key in ('revision', 'instanceId')})
            response["runtime"] = dict(service.state["runtime"])
        return web.json_response(response)

    async def certificate(request):
        certificate = ca_bytes(data_dir)
        if certificate is None:
            raise web.HTTPNotFound(text="A local CA is not configured.")
        return web.Response(body=certificate, content_type="application/x-x509-ca-cert",
                            headers={"Content-Disposition": 'attachment; filename="amplifier-unified-ca.crt"'})

    async def setup(request):
        certificate = ca_bytes(data_dir)
        from .tls import ca_fingerprint
        return web.Response(
            text=render_setup_page(
                detect_platform(request.headers.get("User-Agent")),
                certificate is not None,
                ca_fingerprint(data_dir),
            ),
            content_type="text/html",
            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"},
        )

    async def events(request):
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        await response.prepare(request)
        queue = None
        task = asyncio.current_task()
        streams.add(task)
        try:
            await service._flush_pending_progress()
            queue = service.subscribe()
            snapshot = service.browser_state()
            selected = next((row for row in snapshot['sessions'] if row['id'] == snapshot.get('selectedSessionId')), None)
            if selected and selected.get('nativeProject'):
                # Reconnecting restores a client's selection without dispatching
                # session.select. Load its display history without warming or
                # starting a runtime, while the initial snapshot paints promptly.
                service._task(service.history.refresh_session(selected['id']))
            previous = None
            compact = request.query.get('transport') == 'delta-v1'
            while True:
                if "shellClientId" in snapshot:
                    await response.write(("event: shell\ndata: " + json.dumps(snapshot) + "\n\n").encode())
                else:
                    from .state_transport import delta
                    event = 'state-delta' if compact and previous is not None else 'state'
                    payload = delta(previous, snapshot) if event == 'state-delta' else snapshot
                    await response.write(("event: " + event + "\nid: " + str(snapshot["revision"]) + "\ndata: " + json.dumps(payload) + "\n\n").encode())
                    previous = snapshot
                try:
                    snapshot = await asyncio.wait_for(queue.get(), 20)
                except TimeoutError:
                    await response.write(b": heartbeat\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            streams.discard(task)
            if queue is not None:
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

    from .canvas_documents import canvas_source, raw_source

    def canvas_view_target(request, view_id):
        try:
            args = {'viewId': view_id, 'resourceId': request.query['resourceId'],
                    'resourceRevision': request.query['resourceRevision'], 'generation': int(request.query['generation'])}
        except (KeyError, ValueError):
            raise AppError('Supply the artifact view identity and revision.') from None
        service.canvas_views.target(args)
        return service.canvas_views.canvas(view_id)

    async def canvas_view_resource(request):
        canvas = canvas_view_target(request, request.match_info['view_id'])
        return web.json_response(canvas, headers={'Cache-Control': 'no-store'})

    async def canvas_view_source(request):
        canvas = canvas_view_target(request, request.match_info['view_id'])
        return web.json_response({'content': raw_source(canvas, service.db)}, headers={'Cache-Control': 'no-store'})

    app.router.add_get('/api/canvas/views/{view_id}/resource', canvas_view_resource)
    app.router.add_get('/api/canvas/views/{view_id}/source', canvas_view_source)

    async def canvas_download(request):
        from .state_storage import resource
        from urllib.parse import quote
        identity = request.match_info["identity"]
        row = next((row for row in service.state.get("canvasArtifacts", []) if row["id"] == identity), None)
        if not row:
            raise AppError("Canvas artifact unavailable", 404)
        canvas = {**row, **resource(service.db, row["body"]["$resource"])}
        from .canvas_downloads import filename
        return web.Response(text=canvas_source(canvas,service.db), content_type="text/html",
                            headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename(canvas), safe='')})

    async def canvas_source_text(request):
        from .state_storage import resource
        row=next((r for r in service.state.get('canvasArtifacts',[]) if r['id']==request.match_info['identity']),None)
        if not row or row.get('kind') not in {'html','babylon','canvas-app'}:
            raise AppError('Canvas source unavailable',404)
        canvas={**row, **({} if row.get('contentResource') else resource(service.db,row['body']['$resource']))}
        return web.Response(text=raw_source(canvas,service.db),content_type='text/plain',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    async def canvas_app_host(request):
        canvas = canvas_view_target(request, request.query.get('viewId', 'primary'))
        if canvas.get('id') != request.match_info['identity'] or not canvas.get('app'):
            raise AppError('Interactive surface no longer available.', 404)
        from .canvas_app_host import document_response
        return document_response(canvas, request)

    app.router.add_get('/api/canvas/{identity}/app-host', canvas_app_host)

    async def canvas_document(request):
        canvas = (canvas_view_target(request, request.query['viewId']) if request.query.get('viewId')
                  else service.state.get("canvas", {}))
        if canvas.get("id") != request.match_info["identity"] or canvas.get("kind") not in {"html", "babylon", "mcp-app", "canvas-app"}:
            raise AppError("Canvas document no longer available", 404)
        if canvas.get("kind") == "mcp-app":
            from .smart_canvas import document_response
            from .mcp_view_recovery import source
            return document_response({**canvas, 'content': source(service, canvas['id'])})
        identity = json.dumps(canvas["id"])
        bridge = 'canvas_app_bridge.js' if canvas.get('app') else 'canvas_bridge.js'
        bootstrap = "<!doctype html><script data-canvas-bridge>" + (Path(__file__).parent / bridge).read_text().replace("__CANVAS_ID__", identity) + "</script>"
        return web.Response(text=bootstrap + canvas_source(canvas,service.db), content_type="text/html", headers={
            "Content-Security-Policy": "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), clipboard-read=(), clipboard-write=()"})

    async def smart_canvas_status(request):
        from .mcp_view_recovery import inspect
        return web.json_response(inspect(service, request.match_info['identity']), headers={'Cache-Control': 'no-store'})

    app.router.add_get('/api/canvas/{identity}/status', smart_canvas_status)

    async def smart_canvas_tools(request):
        _, binding = service.smart_canvas.binding(request.match_info['identity'])
        server = next(s for s in service.state['smartTools']['servers'] if s['id'] == binding['serverId'])
        tools = [t for t in await service.smart_tools.list_tools(server['id'], origin='app') if t['name'] in binding['allowedTools']]
        return web.json_response({'tools':tools})

    app.router.add_get('/api/canvas/{identity}/tools', smart_canvas_tools)

    async def smart_canvas_call(request):
        # The same admitted action, visibility checks and durable receipt as
        # app_control, returned immediately on completion instead of browser polling.
        payload = await request.json()
        if not isinstance(payload, dict):
            raise AppError('A tool request must be a JSON object.')
        identity = payload.get('id')
        if not isinstance(identity, str) or not 1 <= len(identity) <= 100:
            raise AppError('A stable request ID is required.')
        waiter = asyncio.current_task()
        call_waiters.add(waiter)
        try:
            accepted = await service.dispatch('smartTools.appCall', {
                'canvasId': request.match_info['identity'],
                'name': payload.get('name'), 'arguments': payload.get('arguments', {}),
            }, command_id=identity, origin='ui', include_state=False)
            operation = await service.wait_smart_tool(accepted['operationId'])
            return web.json_response(operation, headers={'Cache-Control': 'no-store'})
        finally:
            call_waiters.discard(waiter)

    app.router.add_post('/api/canvas/{identity}/tools/call', smart_canvas_call)

    async def smart_canvas_resource(request):
        try:
            result = await service.smart_canvas.resource(
                request.match_info['identity'], request.query.get('kind', 'read'),
                uri=request.query.get('uri'), cursor=request.query.get('cursor'))
        except ValueError as exc:
            raise AppError(str(exc), 400) from None
        return web.json_response(result, headers={'Cache-Control': 'no-store'})

    app.router.add_get('/api/canvas/{identity}/resources', smart_canvas_resource)

    async def smart_operation(request):
        operation = service.smart_tools.operation(request.match_info['identity'])
        if not operation:
            return web.json_response({'status':'pending'}, status=200)
        return web.json_response(operation)

    app.router.add_get('/api/smart-tools/operations/{identity}', smart_operation)
    async def shared_conversation(request):
        async with service.lock:
            content = service.conversation_library.public_snapshot(request.match_info['token'])
        if content is None:
            raise web.HTTPNotFound(text='This snapshot link is unavailable.')
        return web.Response(text=content, content_type='text/html', headers={
            'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            'X-Robots-Tag': 'noindex, nofollow, noarchive'})
    app.router.add_get('/share/{token}', shared_conversation)
    app.router.add_get("/login", login_page)
    app.router.add_post("/login", post_login)
    app.router.add_get("/setup", setup)
    app.router.add_get("/api/ca", certificate)
    app.router.add_get("/ca.crt", certificate)
    app.router.add_get("/api/canvas/{identity}/download", canvas_download)
    app.router.add_get("/api/canvas/{identity}/document", canvas_document)
    app.router.add_get("/api/canvas/{identity}/source", canvas_source_text)
    app.router.add_get("/api/attachments/{identity}", attachment)
    app.router.add_get("/api/outputs/{identity}/content", output_content)
    app.router.add_get("/api/outputs/{identity}/image", output_image)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/state/detail", state_detail)
    app.router.add_get("/api/conversation/detail", conversation_detail)
    app.router.add_get('/api/conversation/exports/{identity}', conversation_export)
    app.router.add_get("/api/actions", actions)
    app.router.add_post("/api/actions", actions)
    app.router.add_post("/api/view", view)
    app.router.add_get("/api/events", events)
    from .live_clients import setup_routes as setup_clients
    setup_clients(app, streams)

    async def shell_state(request):
        from .shell_modules import IDENTITY
        from jsonschema import validate, ValidationError
        client_id = request.query.get('clientId', '')
        try:
            validate(client_id, IDENTITY)
        except ValidationError:
            raise AppError('A valid shell clientId is required.') from None
        return web.json_response(service.shell.inspect(client_id, snapshots=True, recovery=request.query.get('recovery') == '1'))

    async def shell_package(request):
        digest = request.match_info['digest']
        service.shell.manifest(digest)
        return web.Response(text=service.shell.source(digest), content_type='text/javascript', headers={'Cache-Control': 'no-store'})

    app.router.add_get('/api/shell', shell_state)
    app.router.add_get('/api/shell/packages/{digest}.mjs', shell_package)

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
        waiting = list(streams | call_waiters)
        for task in waiting:
            task.cancel()
        await asyncio.gather(*waiting, return_exceptions=True)

    app.on_shutdown.append(shutdown)

    async def confirm_started(app):
        from .update_readiness import wait_for_readiness, recovery_candidate
        if recovery_candidate(service.update_manager):
            service.update_manager.readiness_task = asyncio.create_task(
                wait_for_readiness(service.update_manager, app['control_token']))

    app.on_startup.append(confirm_started)

    async def cleanup(app):
        await service.close()

    app.on_cleanup.append(cleanup)
    return app
