"""Process-isolated, configured Amplifier sessions and their public event bridge.

The web server never changes cwd or imports provider SDKs. Each worker owns the
app-owned Foundation lifecycle and standalone Amplifier configuration.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time
import uuid
from typing import Any, Awaitable, Callable

from .runtime_protocol import MAX_MESSAGE_BYTES, encode_message

Emitter = Callable[[str, dict], Awaitable[None]]

class SessionInUseError(RuntimeError):
    """A definite rejected admission, not an uncertain execution failure."""

    def __init__(self, owner=None):
        self.owner = owner if isinstance(owner, dict) else {}
        from .session_ownership import owner_label
        super().__init__(
            f"This conversation is in use by {owner_label(self.owner)}. "
            "Use the conversation's Continue here action. Your draft has been kept."
        )


def _worker_error(data):
    if data.get("code") == "session_busy":
        return SessionInUseError(data.get("owner"))
    return RuntimeError(data.get("error", "Amplifier runtime failed"))


def normalize_event(event: dict, session_id: str, input_id: str | None = None):
    """Only publish useful runtime events; keep analysis/provider payloads private."""
    kind = event.get("type", "")
    base = {"sessionId": session_id}
    if kind == 'runtime.ownership':
        return kind, {**base, **{key: event[key] for key in ('status', 'source', 'detail') if key in event}}
    if kind in {'session.naming','session.naming.progress'}:
        return kind, {**base,**{key:event[key] for key in ('name','description','completedInputs','nameRevision') if key in event}}
    if kind == "execution.event":
        event = event.get("event", {})
        allowed = ("id", "parentId", "turnId", "sessionId", "rootSessionId", "kind", "phase", "label",
            "toolCallId", "provider", "model", "startedAt", "endedAt", "usage", "summary", "input", "output", "error", "lifecycle", "failure", "liveObservation")
        return "execution.event", {key:event[key] for key in allowed if key in event and (key not in {"input", "output", "error"} or event.get("kind") == "tool")}
    if kind == "runtime.activity":
        allowed = {"model", "processing", "waiting-workers", "tools", "retrying", "compacting"}
        phase = event.get("phase")
        if phase not in allowed:
            return None
        return "runtime.status", {**base, "status": "working", "activityOnly": True, "phase": phase,
            "detail": event.get("detail", "Working"), "activeWorkers": event.get("activeWorkers", 0),
            **{key:event[key] for key in ("retryAttempt", "retryMax") if key in event}}
    if kind == "assistant.delta":
        return "assistant.delta", {**base, "text": event.get("text", ""),
            "requestId": event.get("request_id"), "blockIndex": event.get("block_index")}
    if kind == "assistant.message":
        return "assistant.message", {**base, "text": event.get("text", ""),
            "inputId": (event.get("input_ids") or [input_id])[-1],
            "generationId": event.get("generation_id"), "inputIds": event.get("input_ids", [])}
    if kind in {"generation.started", "generation.finished", "generation.failed", "generation.detached"}:
        return "runtime.generation", {**base, "event": kind,
            **{key: event[key] for key in ("generation_id", "input_ids", "initial_input_id",
                "text", "active_job_ids", "disposition", "error_type", "accepted_input_ids") if key in event}}
    if kind.startswith("job."):
        statuses = {"queued": "queued", "returned": "completed", "failed": "error",
                    "cancelled": "cancelled", "cancel_requested": "stopping", "recovered": "interrupted"}
        return "worker.updated", {**base, "id": event.get("job_id"),
            "status": (statuses.get(event.get("status"), event.get("status")) or "interrupted") if kind == "job.recovered" else statuses.get(kind[4:]) or event.get("status") or "running",
            "callId": event.get("call_id"), "name": event.get("agent") or "Delegated work",
            "kind": "job", "event": kind, "updatedAt": event.get("time")}
    if kind == "worker.activity":
        return "worker.updated", {**base, "id": event.get("workerId"), "status": "running",
            "phase": event.get("phase"), "detail": event.get("detail"),
            "callId": event.get("callId"), "name": event.get("name", "Worker"),
            "kind": "session", "updatedAt": event.get("time"),
            **{key:event[key] for key in ("retryAttempt", "retryMax") if key in event}}
    if kind == "child.updated":
        return "worker.updated", {**base, "id": event.get("sessionId"),
            "status": event.get("status", "running"), "name": event.get("agent", "Worker"),
            "report": event.get("report", ""), "persistent": event.get("persistent", False),
            "callId": event.get("callId"), "kind": "session", "updatedAt": event.get("time")}
    statuses = {"session.ready": "ready", "session.idle": "idle", "session.closed": "stopped",
                "input.delivered": "working"}
    if kind in statuses:
        return "runtime.status", {**base, "status": statuses[kind], "event": kind,
            **({"inputId": event["input_id"]} if "input_id" in event else {})}
    if kind in {"provider.error", "persistence.failed", "command.rejected", "native.error"}:
        return "runtime.error", {**base, "error": event.get("reason") or event.get("error_type") or kind,
                                  "event": kind, "errorType": event.get("error_type")}
    if kind in {"tool.pre", "tool.post", "tool.error"}:
        return "runtime.tool", {**base, "tool": event.get("tool"), "callId": event.get("call_id"), "phase": kind[5:]}
    return None


class RuntimeManager:
    def __init__(self, app_bridge=None, *, command=None, startup_timeout=600, progress_interval=5, retention=None):
        self.app_bridge = app_bridge
        self.command = command
        self.startup_timeout = startup_timeout
        self.progress_interval = progress_interval
        self.workers: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._closed = False
        self._retired = {}
        from .runtime_retention import WorkerRetention
        self.retention = WorkerRetention(self, retention)

    def has_pending_operations(self):
        """Preparing a worker or waiting for its reply must defer host updates."""
        return any(lock.locked() for lock in self._locks.values()) or any(
            row.get("inflight") and row["process"].returncode is None
            for row in self.workers.values()
        )

    def configure_retention(self, settings):
        from .runtime_retention import validate_retention
        updated = validate_retention(settings)
        if updated['max_background_starts'] != self.retention.settings['max_background_starts']:
            raise ValueError('Changing background startup concurrency requires a host restart.')
        self.retention.settings = updated
        self.retention.wake()

    def _command(self, release=None, *, home=None):
        if self.command:
            return list(self.command)
        worker = Path(__file__).with_name("runtime_worker.py")
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError("Install uv to prepare the Amplifier runtime.")
        # The packaged manifest is copied to a writable cache: installed package
        # directories may be read-only and uv creates its lock and .venv there.
        from .updates import active_release
        from .runtime_environment import prepare_project, receipt_directory
        home = Path(home or os.environ.get("AMPLIFIER_WEB_HOME", Path.home() / ".amplifier-unified"))
        generation = release if release is not None else active_release(home).get("current")
        cache = prepare_project(home, generation)
        recorded = (receipt_directory(home, generation) / 'runtime.lock').exists()
        return [uv, "run", *(["--locked"] if recorded else []), "--project", str(cache), "--python", "3.13", "python", str(worker)]

    async def start(self, session: dict, emit: Emitter):
        sid = session["id"]
        async with self._admission(sid):
            await self._start_locked(session, emit)

    @asynccontextmanager
    async def _admission(self, sid):
        while True:
            async with self._locks.setdefault(sid, asyncio.Lock()):
                row = self.workers.get(sid)
                retirement = row.get('retirement_task') if row else None
                if retirement is None or retirement.done():
                    yield
                    return
            # Never send a new command to an owner that might still exit after
            # a delayed retirement acknowledgement. Waiting does not cancel it.
            try:
                await asyncio.wait_for(asyncio.shield(retirement), self.retention.reply_timeout)
            except TimeoutError as exc:
                raise RuntimeError('Idle worker shutdown is still being confirmed. No new command was sent; retry after it settles.') from exc

    async def _start_locked(self, session, emit):
        if self._closed:
            raise RuntimeError("The runtime host is closing.")
        sid = session["id"]
        self._retired.pop(sid, None)
        current = self.workers.get(sid)
        if current and current["process"].returncode is None:
            current["emit"] = emit
            current['start_session'] = {key: session[key] for key in (
                'id', 'workspace', 'workingDirectory', 'bundle', 'selection',
                'runtimeSessionId', 'nativeIdentity', 'forkContext') if key in session}
            await asyncio.wait_for(asyncio.shield(current["ready"]), self.startup_timeout)
            return
        await emit("runtime.status", {"sessionId": sid, "status": "starting", "phase": "runtime-setup",
            "detail": "Preparing the pinned Amplifier runtime. First use may install dependencies.", "elapsedSeconds": 0})
        from .host.config import worker_environment
        proc = await asyncio.create_subprocess_exec(*self._command(), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=MAX_MESSAGE_BYTES,
            start_new_session=os.name != "nt", env=worker_environment())
        row = {"process": proc, "emit": emit, "ready": asyncio.get_running_loop().create_future(),
               "pending": {}, "inflight": set(), "inputId": None, "closing": False, "stderr": [], "bridge_tasks": set(),
               "started_at": time.monotonic(), "phase": "runtime-setup",
               "detail": "Preparing the pinned Amplifier runtime. First use may install dependencies."}
        self.workers[sid] = row
        row["reader"] = asyncio.create_task(self._read(sid, row))
        row["stderr_task"] = asyncio.create_task(self._drain_stderr(row))
        row["heartbeat"] = asyncio.create_task(self._progress(sid, row))
        # The worker restores normal history from its checkpoint. Sending the
        # browser's execution logs, catalogs and attachment history is redundant.
        config = {key:session[key] for key in ('id','workspace','workingDirectory','bundle','selection','forkContext') if key in session}
        config['id'] = session.get('runtimeSessionId') or session.get('nativeIdentity') or sid
        row['runtime_id'] = config['id']
        row['start_session'] = {**config, 'id': sid, 'runtimeSessionId': config['id']}
        row['parked'] = False
        self.retention.wake()
        if session.get('forkContext'):
            config['messages'] = session.get('messages', [])
        try:
            await self._write(row, {"op": "start", "session": config})
            await asyncio.wait_for(asyncio.shield(row["ready"]), self.startup_timeout)
        except TimeoutError as exc:
            await self.stop(sid)
            raise RuntimeError(f"Amplifier preparation exceeded {self.startup_timeout:g} seconds and was stopped before accepting your message. Check the configured bundle, then retry.") from exc
        except BaseException:
            await self.stop(sid)
            raise

    async def _progress(self, sid, row):
        while not row["ready"].done() and not row["closing"]:
            await asyncio.sleep(self.progress_interval)
            if not row["ready"].done() and not row["closing"]:
                await self._emit_progress(sid, row)

    async def _emit_progress(self, sid, row):
        await row["emit"]("runtime.status", {"sessionId": sid, "status": "starting",
            "phase": row["phase"], "detail": row["detail"],
            "elapsedSeconds": int(time.monotonic() - row["started_at"])})

    async def _drain_stderr(self, row):
        while line := await row["process"].stderr.readline():
            # Keep diagnostics local and bounded; never stream arbitrary SDK logs
            # (which can contain prompts/credentials) into browser state.
            row["stderr"].append(line.decode(errors="replace")[-2000:])
            row["stderr"] = row["stderr"][-30:]

    async def _write(self, row, data):
        row["process"].stdin.write(encode_message(data))
        await row["process"].stdin.drain()

    async def _bridge(self, sid, row, data):
        try:
            if not self.app_bridge:
                raise RuntimeError("App controls are not connected")
            result = await self.app_bridge(data["operation"], data.get("args", {}), sid)
            reply = {"op": "bridge.result", "id": data["id"], "result": result}
        except Exception as exc:
            reply = {"op": "bridge.result", "id": data["id"], "error": str(exc)}
        if row["process"].returncode is None:
            try:
                await self._write(row, reply)
            except ValueError as exc:
                await self._write(row, {"op":"bridge.result","id":data["id"],"error":str(exc)})

    async def _read(self, sid, row):
        reported_error = None
        try:
            while line := await row["process"].stdout.readline():
                try:
                    data = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("op") == "bridge":
                    task = asyncio.create_task(self._bridge(sid, row, data))
                    row["bridge_tasks"].add(task)
                    task.add_done_callback(row["bridge_tasks"].discard)
                elif data.get("op") == "reply":
                    row["inflight"].discard(data.get("id"))
                    future = row["pending"].get(data.get("id"))
                    if future and not future.done():
                        if data.get("error"):
                            future.set_exception(_worker_error(data))
                        else:
                            future.set_result(data.get("result"))
                elif data.get("type") == "runtime.parked":
                    row["parked"] = True
                    row["parked_at"] = self.retention.clock()
                    self.retention.wake()
                    await row['emit']('runtime.warmth', {'sessionId': sid, 'status': 'warm'})
                elif data.get("type") == "runtime.reactivated":
                    row["parked"] = False
                    await row['emit']('runtime.warmth', {'sessionId': sid, 'status': 'active'})
                elif data.get("type") == "runtime.progress":
                    if not row["ready"].done() and not row["closing"]:
                        row["phase"] = data.get("phase", row["phase"])
                        row["detail"] = data.get("detail", row["detail"])
                        await self._emit_progress(sid, row)
                elif data.get("type") == "runtime.ready":
                    if not row["ready"].done():
                        row["ready"].set_result(data.get("report", {}))
                    await row["emit"]("runtime.status", {"sessionId": sid, "status": "ready", "phase": "ready",
                        "detail": "Amplifier is ready.", "elapsedSeconds": int(time.monotonic() - row["started_at"]),
                        "report": data.get("report", {})})
                elif data.get("type") == "runtime.ownership":
                    row['yielded'] = data.get('status') == 'yielded'
                    row['yielding'] = data.get('status') in {'yielding', 'yield-failed'}
                    await row['emit']('runtime.ownership', {'sessionId': sid,
                        **{key: data[key] for key in ('status', 'source', 'detail') if key in data}})
                elif data.get("type") == "runtime.error":
                    failure = _worker_error(data)
                    error = str(failure)
                    reported_error = error
                    if not row["ready"].done():
                        row["ready"].set_exception(failure)
                    if isinstance(failure, SessionInUseError):
                        await row["emit"]("runtime.ownership", {"sessionId": sid, "status": "blocked", "owner": failure.owner})
                    else:
                        await row["emit"]("runtime.error", {"sessionId": sid, "error": error})
                elif data.get("type") == "history.revised":
                    await row["emit"]("history.revised", {**data, "sessionId": sid})
                elif data.get("type") in {"approval.requested", "approval.resolved"} and data.get("id"):
                    await row["emit"](data["type"], {**data, "sessionId": sid})
                else:
                    if data.get('type') == 'execution.event' and isinstance(data.get('event'), dict):
                        data = {**data, 'event': dict(data['event'])}
                        if data['event'].get('lifecycle') == 'background' and data['event'].get('id'):
                            row.setdefault('backgroundCalls', set()).add(data['event']['id'])
                        for key in ('sessionId', 'rootSessionId'):
                            if data['event'].get(key) == row.get('runtime_id'):
                                data['event'][key] = sid
                    if data.get("type") == "input.delivered":
                        row["inputId"] = data.get("input_id")
                    normalized = normalize_event(data, sid, row["inputId"])
                    if normalized:
                        await row["emit"](*normalized)
            code = await row["process"].wait()
            if not row["closing"] and not reported_error:
                error = f"Amplifier worker exited (code {code}). Work was not replayed."
                await row["emit"]("runtime.error", {"sessionId": sid, "error": error})
                if not row["ready"].done():
                    row["ready"].set_exception(RuntimeError(error))
            await self._execution_ended(sid, row, "stopped" if row["closing"] else "interrupted")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = f'Amplifier worker communication failed ({type(exc).__name__}). Work was not replayed.'
            if not row['closing']:
                await row['emit']('runtime.error', {'sessionId':sid,'error':error})
            if not row["ready"].done():
                row["ready"].set_exception(RuntimeError(error))
        finally:
            for future in row["pending"].values():
                if not future.done():
                    future.set_exception(RuntimeError("Amplifier worker disconnected"))

    async def _execution_ended(self, sid, row, status):
        """Only a confirmed process exit settles independent background calls."""
        if row.get("executionEnded"): return
        await row["emit"]("runtime.ended", {"sessionId": sid, "status": status, "backgroundCallIds": sorted(row.get("backgroundCalls", ()))})
        # The reader can be cancelled while delivery waits for the service
        # lock. Only successful delivery suppresses the stop-path retry.
        row["executionEnded"] = True

    async def _request(self, sid, op, **args):
        row = self.workers.get(sid)
        if op == 'approval' and row and not row['ready'].done():
            # A module can ask for approval during initialization. start() owns
            # the admission lock until ready, so its answer must bypass that
            # lock. A starting worker is never eligible for idle retirement.
            return await self._request_unlocked(sid, op, **args)
        # The admission portion holds the same lock as retirement. Waiting for
        # replies does not: a tool control may itself await an approval/bridge.
        async with self._admission(sid):
            if sid not in self.workers and sid in self._retired and op in {"send", "control", "resume"}:
                session, emit = self._retired[sid]
                await self._start_locked(session, emit)
            pending = await self._admit(sid, op, args)
        return await self._reply(*pending, op=op, args=args)

    async def _request_unlocked(self, sid, op, **args):
        return await self._reply(*await self._admit(sid, op, args), op=op, args=args)

    async def _admit(self, sid, op, args):
        row = self.workers.get(sid)
        if not row or row["process"].returncode is not None:
            raise RuntimeError("Session is not running")
        identity = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        row["pending"][identity] = future
        # A caller timing out or disconnecting does not cancel work already
        # handed to the worker. Keep it busy until the reply or process exit.
        row["inflight"].add(identity)
        if op not in {"park", "retire"}:
            row["parked"] = False
        try:
            await self._write(row, {"op": op, "id": identity, **args})
        except (ValueError, TypeError):
            row["inflight"].discard(identity)
            row["pending"].pop(identity, None)
            raise
        except BaseException as exc:
            # A partial transport write has an unknown outcome. Keep the
            # in-flight fence until its reply or process exit, just like timeout.
            if op == 'retire' and isinstance(exc, Exception):
                # A partial retirement write can still get a late reply. Its
                # reconciler keeps the original future until reply or exit.
                return row, identity, future
            row["pending"].pop(identity, None)
            raise
        return row, identity, future

    async def _reply(self, row, identity, future, *, op, args):
        try:
            timeout = None if op == "retire" or op == "control" and args.get("operation") in {"bundle.switch", "history.edit"} else 180 if op == "control" and args.get("operation") == "bundle.preview" else 600 if op == "control" and args.get("operation") == "tool.invoke" else 150 if op == "control" and args.get("operation") in {"configuration.providerModels","configuration.providerTest"} else 30
            try:
                return await asyncio.wait_for(future, timeout)
            except TimeoutError as exc:
                raise RuntimeError("The runtime operation has not returned yet. It may still be running; do not automatically repeat it.") from exc
        finally:
            row["pending"].pop(identity, None)

    async def prewarm(self, session, emit):
        if self._closed or not self.retention.settings["prewarm_on_select"] or not self.retention.settings["max_warm_workers"] or not self.retention.settings["idle_timeout_hours"]:
            return
        # Browsing an already loaded chat neither reacquires its writer lock nor
        # extends its idle lifetime. Actual input still validates state on wake.
        if self.workers.get(session["id"], {}).get("process") is not None:
            row = self.workers[session["id"]]
            if row["process"].returncode is None:
                return
        async with self.retention.background:
            # Policy or another admission may have changed while this startup
            # waited behind other background preparations.
            if self._closed or not self.retention.settings["prewarm_on_select"] or not self.retention.settings["max_warm_workers"] or not self.retention.settings["idle_timeout_hours"]:
                return
            row = self.workers.get(session["id"])
            if row and row["process"].returncode is None:
                return
            await self.start(session, emit)
            await self._request(session["id"], "park")

    async def send(self, session, text, input_id, emit):
        await self.start(session, emit)
        return await self._request(session["id"], "send", text=text, input_id=input_id,
            context_binding=session.get('surfaceInputs', {}).get(input_id, {'clientId': None, 'targets': []}),
            attachments=next((m.get("attachments",[]) for m in session.get("messages",[]) if m.get("inputId")==input_id),[]))

    async def takeover(self, session, emit, expected_owner=None, timeout=30):
        """One deliberate request; a competing successor is never asked to yield."""
        from amplifier_foundation.session import SharedSessionStore, request_release
        row = self.workers.get(session['id'])
        if row and row.get('yielding'):
            raise RuntimeError('The owner has not finished safe shutdown. Ownership is retained.')
        if row and row.get('yielded'):
            await self.stop(session['id'])
        try:
            await self.start(session, emit)
            await self._request(session['id'], 'resume')
            return
        except SessionInUseError as busy:
            if expected_owner and expected_owner.get('acquisition_id') != busy.owner.get('acquisition_id'):
                raise
            store = SharedSessionStore(session['workspace'],
                session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id'])
            result = await request_release(store, expected_owner=busy.owner, request_id=uuid.uuid4().hex,
                requester_app='Amplifier Unified', timeout=timeout)
            # Attempt the real acquisition even if the final reply was lost.
            try:
                await self.start(session, emit)
                await self._request(session['id'], 'resume')
            except SessionInUseError as current:
                current.args = (f'Takeover did not complete ({result.status}). {result.message} {current}',)
                raise current from None

    async def approval(self, session_id, approval_id, decision):
        return await self._request(session_id, "approval", approval_id=approval_id, decision=decision)

    async def control(self, session_id, operation, arguments=None):
        return await self._request(session_id, "control", operation=operation, arguments=arguments or {})

    async def shared_state_probe(self, request):
        """Run one read-only shared-state request inside the isolated runtime."""
        if not isinstance(request, dict):
            raise ValueError("Shared-state request must be an object.")
        command = self._command()
        command[-1] = str(Path(__file__).with_name("shared_state_probe.py"))
        proc = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=MAX_MESSAGE_BYTES,
        )
        try:
            proc.stdin.write(encode_message(request))
            await proc.stdin.drain()
            proc.stdin.close()
            line = await asyncio.wait_for(proc.stdout.readline(), 30)
            await asyncio.wait_for(proc.wait(), 30)
            response = json.loads(line) if line else None
            if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
                raise RuntimeError("Shared-state probe returned an invalid response.")
            if not response["ok"]:
                raise RuntimeError(response.get("error", {}).get("message") or "Shared-state probe failed.")
            return response["result"]
        except TimeoutError as exc:
            raise RuntimeError("The shared-state probe timed out without changing session ownership.") from exc
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def steer_worker(self, session_id, worker_id, text):
        return await self._request(session_id, "worker.steer", worker_id=worker_id, text=text)

    async def stop_worker(self, session_id, worker_id):
        return await self._request(session_id, "worker.stop", worker_id=worker_id)

    async def stop(self, session_id):
        self._retired.pop(session_id, None)
        row = self.workers.get(session_id)
        if not row:
            return
        retirement = row.get('retirement_task')
        if retirement and retirement is not asyncio.current_task() and not retirement.done():
            retirement.cancel()
            await asyncio.gather(retirement, return_exceptions=True)
        if "stop_task" not in row:
            row["stop_task"] = asyncio.create_task(self._stop_row(session_id, row))
        await asyncio.shield(row["stop_task"])

    async def _stop_row(self, session_id, row):
        row["closing"] = True
        if not row["ready"].done():
            row["ready"].cancel()
        proc = row["process"]
        if proc.returncode is None:
            try:
                await self._write(row, {"op": "stop"})
                await asyncio.wait_for(proc.wait(), 15)
            except (TimeoutError, BrokenPipeError, ConnectionResetError):
                if proc.returncode is None:
                    if os.name != "nt":
                        os.killpg(proc.pid, signal.SIGTERM)
                    else:
                        proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), 5)
                    except TimeoutError:
                        if os.name != "nt":
                            os.killpg(proc.pid, signal.SIGKILL)
                        else:
                            proc.kill()
                        await proc.wait()
        for task in [row["reader"], row["stderr_task"], row["heartbeat"], *row["bridge_tasks"]]:
            if not task.done():
                task.cancel()
        await asyncio.gather(row["reader"], row["stderr_task"], row["heartbeat"], *row["bridge_tasks"], return_exceptions=True)
        await self._execution_ended(session_id, row, "stopped")
        await row["emit"]("runtime.warmth" if row.get("retiring") else "runtime.status",
                          {"sessionId": session_id, "status": "cold" if row.get("retiring") else "stopped"})
        self.workers.pop(session_id, None)

    async def reset(self):
        """Retire workers at an idle update boundary, retaining this host."""
        await self.close()
        self._closed = False

    async def close(self):
        self._closed = True
        await self.retention.close()
        await asyncio.gather(*(self.stop(sid) for sid in list(self.workers)), return_exceptions=True)
        self._retired.clear()

    async def spawn_worker(self, session, instruction, input_id, emit):
        """Ask the configured root to delegate through its normal tool hooks."""
        return await self.send(session,
            "Please delegate this request to a suitable configured agent as a persistent worker lane. "
            "Use the normal delegate tool and preserve all approval checks. Report the actual worker identity "
            "once created; if delegation is unavailable, explain that honestly. Request:\n\n" + instruction,
            input_id, emit)
