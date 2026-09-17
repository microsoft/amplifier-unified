"""Process-isolated, configured Amplifier sessions and their public event bridge.

The web server never changes cwd or imports provider SDKs. Each worker owns the
app-owned Foundation lifecycle and standalone Amplifier configuration.
"""
from __future__ import annotations

import asyncio
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


def normalize_event(event: dict, session_id: str, input_id: str | None = None):
    """Only publish useful runtime events; keep analysis/provider payloads private."""
    kind = event.get("type", "")
    base = {"sessionId": session_id}
    if kind in {'session.naming','session.naming.progress'}:
        return kind, {**base,**{key:event[key] for key in ('name','description','completedInputs') if key in event}}
    if kind == "execution.event":
        event = event.get("event", {})
        allowed = ("id", "parentId", "turnId", "sessionId", "rootSessionId", "kind", "phase", "label",
            "toolCallId", "provider", "model", "startedAt", "endedAt", "usage", "summary")
        return "execution.event", {key:event[key] for key in allowed if key in event}
    if kind == "runtime.activity":
        allowed = {"model", "processing", "waiting-workers", "tools", "retrying"}
        phase = event.get("phase")
        if phase not in allowed:
            return None
        return "runtime.status", {**base, "status": "working", "phase": phase,
            "detail": event.get("detail", "Working"), "activeWorkers": event.get("activeWorkers", 0),
            **{key:event[key] for key in ("retryAttempt", "retryMax") if key in event}}
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
            "status": event.get("status") or statuses.get(kind[4:], "running"),
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
                                  "event": kind}
    if kind in {"tool.pre", "tool.post", "tool.error"}:
        return "runtime.tool", {**base, "tool": event.get("tool"), "callId": event.get("call_id"), "phase": kind[5:]}
    return None


class RuntimeManager:
    def __init__(self, app_bridge=None, *, command=None, startup_timeout=600, progress_interval=5):
        self.app_bridge = app_bridge
        self.command = command
        self.startup_timeout = startup_timeout
        self.progress_interval = progress_interval
        self.workers: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _command(self, release=None):
        if self.command:
            return list(self.command)
        worker = Path(__file__).with_name("runtime_worker.py")
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError("Install uv to prepare the pinned Amplifier runtime.")
        # The packaged manifest is copied to a writable cache: installed package
        # directories may be read-only and uv creates its lock and .venv there.
        import hashlib
        manifest = Path(__file__).with_name("runtime_deps") / "pyproject.toml"
        content = manifest.read_bytes()
        digest = hashlib.sha256(content)
        from .updates import active_release
        home = Path(os.environ.get("AMPLIFIER_WEB_HOME", Path.home() / ".amplifier-unified"))
        generation = release if release is not None else active_release(home).get("current")
        if generation:
            digest.update(generation.encode())
        vendor = manifest.parent / "vendor"
        files = sorted(path for path in vendor.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
        for path in files:
            digest.update(str(path.relative_to(vendor)).encode())
            digest.update(path.read_bytes())
        cache = Path(os.environ.get("AMPLIFIER_WEB_HOME", Path.home() / ".amplifier-unified")) / "runtime" / digest.hexdigest()[:16]
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "pyproject.toml").write_bytes(content)
        for source in files:
            target = cache / "vendor" / source.relative_to(vendor)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.read_bytes() != source.read_bytes():
                shutil.copy2(source, target)
        return [uv, "run", "--project", str(cache), "--python", "3.13", "python", str(worker)]

    async def start(self, session: dict, emit: Emitter):
        sid = session["id"]
        async with self._locks.setdefault(sid, asyncio.Lock()):
            current = self.workers.get(sid)
            if current and current["process"].returncode is None:
                current["emit"] = emit
                await asyncio.wait_for(asyncio.shield(current["ready"]), self.startup_timeout)
                return
            await emit("runtime.status", {"sessionId": sid, "status": "starting", "phase": "runtime-setup",
                "detail": "Preparing the pinned Amplifier runtime. First use may install dependencies.", "elapsedSeconds": 0})
            proc = await asyncio.create_subprocess_exec(*self._command(), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=MAX_MESSAGE_BYTES,
                start_new_session=os.name != "nt")
            row = {"process": proc, "emit": emit, "ready": asyncio.get_running_loop().create_future(),
                   "pending": {}, "inputId": None, "closing": False, "stderr": [], "bridge_tasks": set(),
                   "started_at": time.monotonic(), "phase": "runtime-setup",
                   "detail": "Preparing the pinned Amplifier runtime. First use may install dependencies."}
            self.workers[sid] = row
            row["reader"] = asyncio.create_task(self._read(sid, row))
            row["stderr_task"] = asyncio.create_task(self._drain_stderr(row))
            row["heartbeat"] = asyncio.create_task(self._progress(sid, row))
            # The worker restores normal history from its checkpoint. Sending the
            # browser's execution logs, catalogs and attachment history is redundant.
            config = {key:session[key] for key in ('id','workspace','workingDirectory','bundle','selection','forkContext') if key in session}
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
                    future = row["pending"].get(data.get("id"))
                    if future and not future.done():
                        if data.get("error"):
                            future.set_exception(RuntimeError(data["error"]))
                        else:
                            future.set_result(data.get("result"))
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
                elif data.get("type") == "runtime.error":
                    error = data.get("error", "Amplifier runtime failed")
                    reported_error = error
                    if not row["ready"].done():
                        row["ready"].set_exception(RuntimeError(error))
                    await row["emit"]("runtime.error", {"sessionId": sid, "error": error})
                elif data.get("type") in {"approval.requested", "approval.resolved"} and data.get("id"):
                    await row["emit"](data["type"], {**data, "sessionId": sid})
                else:
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

    async def _request(self, sid, op, **args):
        row = self.workers.get(sid)
        if not row or row["process"].returncode is not None:
            raise RuntimeError("Session is not running")
        identity = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        row["pending"][identity] = future
        try:
            await self._write(row, {"op": op, "id": identity, **args})
            timeout = 600 if op == "control" and args.get("operation") == "tool.invoke" else 150 if op == "control" and args.get("operation") in {"configuration.providerModels","configuration.providerTest"} else 30
            try:
                return await asyncio.wait_for(future, timeout)
            except TimeoutError as exc:
                raise RuntimeError("The runtime operation has not returned yet. It may still be running; do not automatically repeat it.") from exc
        finally:
            row["pending"].pop(identity, None)

    async def send(self, session, text, input_id, emit):
        await self.start(session, emit)
        return await self._request(session["id"], "send", text=text, input_id=input_id, attachments=next((m.get("attachments",[]) for m in session.get("messages",[]) if m.get("inputId")==input_id),[]))

    async def approval(self, session_id, approval_id, decision):
        return await self._request(session_id, "approval", approval_id=approval_id, decision=decision)

    async def control(self, session_id, operation, arguments=None):
        return await self._request(session_id, "control", operation=operation, arguments=arguments or {})

    async def steer_worker(self, session_id, worker_id, text):
        return await self._request(session_id, "worker.steer", worker_id=worker_id, text=text)

    async def stop_worker(self, session_id, worker_id):
        return await self._request(session_id, "worker.stop", worker_id=worker_id)

    async def stop(self, session_id):
        row = self.workers.get(session_id)
        if not row:
            return
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
        await row["emit"]("runtime.status", {"sessionId": session_id, "status": "stopped"})
        self.workers.pop(session_id, None)

    async def close(self):
        await asyncio.gather(*(self.stop(sid) for sid in list(self.workers)), return_exceptions=True)

    async def spawn_worker(self, session, instruction, input_id, emit):
        """Ask the configured root to delegate through its normal tool hooks."""
        return await self.send(session,
            "Please delegate this request to a suitable configured agent as a persistent worker lane. "
            "Use the normal delegate tool and preserve all approval checks. Report the actual worker identity "
            "once created; if delegation is unavailable, explain that honestly. Request:\n\n" + instruction,
            input_id, emit)
