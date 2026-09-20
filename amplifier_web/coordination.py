"""Explicit task/worker controls and delivery over existing authoritative stores."""
from __future__ import annotations

import hashlib
import json
import time
import uuid

from amplifier_operations.coordination import ChangeSignal, delivery

ACTIVE = {"working", "running", "starting", "ready", "queued", "pending", "stopping"}
ATTENTION = {"error", "failed", "interrupted", "cancelled", "stopped"}


def definitions():
    from .service import schema, string
    target = {"sessionId": string(200), "workerId": string(200)}
    return {
        "coordination.list": ("List saved conversations and their actual workers without selecting or starting them.", schema({"sessionId": string(200), "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, [])),
        "coordination.wait": ("Wait on up to eight explicit targets for a report or attention. Carry each nextCursor after consuming results; retries retain result IDs. Does not run or replay work.", schema({
            "targets": {"type": "array", "minItems": 1, "maxItems": 8, "items": schema({**target, "afterCursor": string(2048)}, ["sessionId"])},
            "waitMs": {"type": "integer", "minimum": 0, "maximum": 60000},
            "maxBytes": {"type": "integer", "minimum": 4096, "maximum": 65536},
        }, ["targets"])),
        "coordination.followup": ("Send an explicit follow-up to this conversation or an active persistent worker, keeping the selected conversation and draft. Agent writes are limited to the calling conversation's workers.", schema({**target, "text": string(100000)}, ["sessionId", "text"])),
        "coordination.interrupt": ("Request interruption of an explicit conversation or worker. A request does not prove cancellation or undo effects.", schema(target, ["sessionId"])),
    }


def observe_worker(worker, payload):
    """Retain bounded report receipts beside their existing worker lifecycle row."""
    report = payload.get("report")
    if not isinstance(report, str) or not report:
        return
    identity = payload.get("reportId") or "legacy-" + hashlib.sha256(json.dumps(
        [payload.get("runId"), payload.get("callId"), report], separators=(",", ":")
    ).encode()).hexdigest()
    receipts = worker.setdefault("reportReceipts", [])
    if any(row["id"] == identity for row in receipts):
        return
    sequence = worker["reportSequence"] = worker.get("reportSequence", 0) + 1
    receipts.append({"id": identity, "sequence": sequence, "text": report[:20000],
        "runId": payload.get("runId"), "source": "worker", "sourceTruncated": bool(payload.get("reportTruncated") or len(report) > 20000),
        "sourceChars": payload.get("reportSourceChars", len(report)), "window": payload.get("reportWindow", "prefix"), "at": payload.get("updatedAt") or time.time()})
    del receipts[:-32]


class Coordination:
    def __init__(self, service):
        self.service = service
        self.signal = ChangeSignal()

    def notify(self):
        self.signal.notify()

    def task(self, sid):
        return self.service.state.get("runtimeControl", {}).get(sid, {}).get("task.get", {}).get("task") or {}

    def snapshot(self, target, *, include_results=True):
        session = self.service._session(target["sessionId"])
        wid = target.get("workerId")
        worker = next((row for row in session.get("workers", []) if row["id"] == wid), None) if wid else None
        if wid and worker is None:
            raise ValueError("Worker not found in the specified conversation")
        value = worker if wid else session
        status = value.get("status", "interrupted")
        approvals = [row["id"] for row in session.get("approvals", []) if row.get("status") == "pending" and (not wid or row.get("sessionId") in {wid, value.get("sessionId")})]
        # Saved question/task references remain owned by their existing stores.
        task = self.task(session["id"])
        questions = [row["id"] for row in session.get("questions", []) if row.get("status") == "pending"]
        results = (value.get("reportReceipts", []) if wid else [
            {"id": message["id"], "sequence": index + 1, "text": message.get("text", ""),
                "inputId": message.get("inputId"), "source": message.get("source", message.get("via", "conversation"))}
            for index, message in enumerate(m for m in session.get("messages", []) if m.get("role") == "assistant")
        ]) if include_results else []
        latest = value.get("reportSequence", 0) if wid else len(results)
        # Status changes during work do not repeatedly wake waiting callers.
        signal = {"status": status if status not in ACTIVE else "active", "approvals": approvals, "questions": questions,
            "taskStatus": task.get("status"), "taskRevision": task.get("revision"), "interruptionRevision": value.get("interruptionRevision", 0)}
        # Transition to another active phase is progress only. Starting a new
        # turn is also quiet; the next report/attention signal wakes the waiter.
        if status in ACTIVE:
            signal["status"] = "waiting"
        return {"identity": json.dumps([session["id"], wid], separators=(",", ":")),
            "target": {"sessionId": session["id"], **({"workerId": wid} if wid else {})},
            "kind": "worker" if wid else "conversation", "title": value.get("name", session.get("title", "Conversation")),
            "status": status, "parentSessionId": value.get("parentSessionId") if wid else session.get("parentSessionId"),
            "runtimeSessionId": wid if wid and value.get("kind") == "session" else session.get("runtimeSessionId"),
            "runId": value.get("runId") if wid else None, "callId": value.get("callId") if wid else None,
            "taskId": value.get("taskId") if wid else task.get("id"),
            "operationId": "worker:" + wid if wid else None,
            "attention": bool(approvals or questions or status in ATTENTION or task.get("status") == "blocked"),
            "approvalIds": approvals[:64], "questionIds": questions[:64], "taskQuestionIds": task.get("questionIds", [])[:64],
            "canFollowup": bool(self.service.runtime and not session.get("historyReadOnlyReason") and (not wid or value.get("persistent") and status in {"idle", "running"})),
            "canInterrupt": bool(self.service.runtime and (status in ACTIVE or wid and value.get("persistent") and status == "idle")),
            "results": results, "latestSequence": latest, "signal": signal,
            "wakeable": status not in ACTIVE or bool(approvals or questions)}

    def list(self, args):
        sessions = [self.service._session(args["sessionId"])] if args.get("sessionId") else [row for row in self.service.state["sessions"] if row.get("sessionKind") != "worker"]
        items = []
        for session in sessions:
            for target in [{"sessionId": session["id"]}, *({"sessionId": session["id"], "workerId": worker["id"]} for worker in session.get("workers", []))]:
                item = self.snapshot(target, include_results=False)
                items.append({key: value for key, value in item.items() if key not in {"results", "signal", "identity", "wakeable"}})
                if len(items) >= args.get("limit", 100):
                    return {"items": items, "truncated": True}
        return {"items": items, "truncated": False}

    async def wait(self, args):
        targets = args["targets"]
        identities = [(row["sessionId"], row.get("workerId")) for row in targets]
        if len(set(identities)) != len(identities):
            raise ValueError("Wait targets must be distinct")
        max_bytes = args.get("maxBytes", 32768) // len(targets)
        from .service import AppError
        def read():
            results, errors = [], []
            for target in targets:
                try:
                    results.append(delivery(self.snapshot(target), target.get("afterCursor"), max_bytes=max_bytes))
                except (ValueError, LookupError, AppError) as exc:
                    errors.append({"target": {key: target[key] for key in ("sessionId", "workerId") if key in target}, "error": str(exc)})
            return {"targets": results, "errors": errors, "changed": bool(errors or any(row["changed"] for row in results))}
        return await self.signal.wait(read, wait_ms=args.get("waitMs", 0))

    async def dispatch(self, action, args, origin, command_id, include_state, caller_session_id):
        from .service import AppError
        if action in {"coordination.list", "coordination.wait"}:
            await self.service._flush_pending_progress()
            result = self.list(args) if action == "coordination.list" else await self.wait(args)
            return {"accepted": True, "result": result}
        # No model-supplied authorization flag can widen this host-bound scope.
        if origin not in {"ui", "user"} and (not caller_session_id or args["sessionId"] != caller_session_id):
            raise AppError("A user must explicitly send follow-ups or interruptions to another conversation.", 403)
        command_id = command_id or str(uuid.uuid4())
        target = self.snapshot(args)
        retried = bool(command_id and self.service.db.execute("SELECT 1 FROM commands WHERE id=?", (command_id,)).fetchone())
        wid = args.get("workerId")
        if action == "coordination.followup":
            if not args["text"].strip():
                raise AppError("Enter a follow-up message.")
            if not retried and wid and not target["canFollowup"]:
                raise AppError("This worker cannot receive a follow-up. Its saved result remains available.", 409)
            underlying = "worker.message" if wid else "conversation.send"
            values = {"sessionId": args["sessionId"], "text": args["text"], **({"id": wid} if wid else {"preserveDraft": True})}
        else:
            if not retried and not target["canInterrupt"]:
                raise AppError("This target has no live work to interrupt.", 409)
            underlying = "worker.stop" if wid else "conversation.stop"
            values = {"sessionId": args["sessionId"], **({"id": wid} if wid else {})}
        result = await self.service.dispatch(underlying, values, origin, command_id, include_state=include_state, caller_session_id=caller_session_id)
        return {**result, "commandId": command_id}
