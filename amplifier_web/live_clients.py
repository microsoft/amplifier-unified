"""Versioned HTTP/SSE session contract shared by browser and terminal clients."""
from __future__ import annotations

import asyncio
import json
from aiohttp import web

from .service import AppError


@web.middleware
async def client_context(request, handler):
    service = request.app["service"]
    header = request.headers.get("X-Amplifier-Client")
    query = request.query.get("clientId")
    if header and query and header != query:
        raise AppError("Conflicting client identities.")
    identity = header or query
    if request.path == "/api/clients/attach":
        identity = None
    with service.clients.bind(identity):
        try:
            return await handler(request)
        except AppError as exc:
            if identity and exc.code == "session_busy":
                exc.client_state = service.browser_state()
            raise


def setup_routes(app, streams):
    service = app["service"]

    def require_client():
        if service.clients.current.get() is None:
            raise AppError("Attach a client and send X-Amplifier-Client.", 400)

    async def attach(request):
        payload = await request.json()
        if not isinstance(payload, dict) or set(payload) - {"clientId", "resumeClientId", "kind", "protocolVersion"}:
            raise AppError("Invalid client registration.")
        if payload.get("protocolVersion", 1) != 1:
            raise AppError("Unsupported client protocol version.", 409)
        identity = payload.get("clientId")
        async with service.lock:
            service.clients.attach(identity, payload.get("resumeClientId"), payload.get("kind", "web"))
            service._save()
            with service.clients.bind(identity):
                return web.json_response({"clientId": identity, "protocolVersion": 1,
                    "hostInstanceId": service.instance_id, "reconnect": "snapshot",
                    "transports": ["http", "sse"], "state": service.browser_state()})

    def session_snapshot(identity, snapshot=None):
        snapshot = snapshot or service.browser_state(session_id=identity)
        session = next((row for row in snapshot["sessions"] if row["id"] == identity), None)
        if session is None:
            return {"protocolVersion": 1, "revision": snapshot["revision"],
                    "hostInstanceId": service.instance_id, "sessionId": identity, "deleted": True}
        return {"protocolVersion": 1, "revision": snapshot["revision"],
                "hostInstanceId": service.instance_id, "session": session}

    async def sessions(request):
        require_client()
        from .browser_state import summary
        offset, limit = int(request.query.get("offset", 0)), int(request.query.get("limit", 100))
        if offset < 0 or not 1 <= limit <= 100:
            raise AppError("Choose a nonnegative offset and a limit from 1 to 100.")
        workspace = request.query.get("workspace")
        rows = [row for row in service.state["sessions"] if workspace is None or row.get("workspace") == workspace]
        return web.json_response({"protocolVersion": 1, "items": [summary(row) for row in rows[offset:offset+limit]],
            "nextOffset": offset+limit if offset+limit < len(rows) else None})

    async def state(request):
        require_client()
        identity = request.match_info["session_id"]
        service._session(identity)
        await service.history.ensure_loaded(identity)
        await service._flush_pending_progress()
        return web.json_response(session_snapshot(identity))

    async def commands(request):
        require_client()
        identity = request.match_info["session_id"]
        service._session(identity)
        payload = await request.json()
        if not isinstance(payload, dict) or set(payload) - {"id", "action", "args", "expectedRevision"}:
            raise AppError("Invalid session command.")
        command_id = payload.get("id")
        if not isinstance(command_id, str) or not 1 <= len(command_id) <= 200:
            raise AppError("Supply a stable command ID for safe retries.")
        action = payload.get("action")
        key = {
            "conversation.send": "sessionId", "conversation.stop": "sessionId",
            "worker.spawn": "sessionId", "worker.stop": "sessionId", "worker.steer": "sessionId",
            "approval.respond": "sessionId", "runtime.control": "sessionId",
            "session.takeover": "id", "session.history": "id", "session.rename": "id",
        }.get(action)
        if key is None:
            raise AppError("This action is not a session command.")
        args = payload.get("args", {})
        if not isinstance(args, dict) or (key in args and args[key] != identity):
            raise AppError("The command targets a different session.")
        task = service._task(service.dispatch(action, {**args, key: identity}, command_id=command_id,
            expected_revision=payload.get("expectedRevision"), include_state=False))
        result = await asyncio.shield(task)
        return web.json_response({**result, **session_snapshot(identity)})

    async def events(request):
        require_client()
        identity = request.match_info["session_id"]
        service._session(identity)
        await service.history.ensure_loaded(identity)
        await service._flush_pending_progress()
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        await response.prepare(request)
        task = asyncio.current_task()
        streams.add(task)
        queue = service.subscribe(session_id=identity)
        snapshot = service.browser_state(session_id=identity)
        try:
            while True:
                data = session_snapshot(identity, snapshot)
                event_id = f"{service.instance_id}:{data['revision']}"
                await response.write(f"event: snapshot\nid: {event_id}\ndata: {json.dumps(data)}\n\n".encode())
                if data.get("deleted"):
                    break
                try:
                    snapshot = await asyncio.wait_for(queue.get(), 20)
                except TimeoutError:
                    await response.write(b": heartbeat\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            service.unsubscribe(queue)
            streams.discard(task)
        return response

    app.router.add_post("/api/clients/attach", attach)
    app.router.add_get("/api/sessions", sessions)
    app.router.add_get("/api/sessions/{session_id}", state)
    app.router.add_post("/api/sessions/{session_id}/commands", commands)
    app.router.add_get("/api/sessions/{session_id}/events", events)
