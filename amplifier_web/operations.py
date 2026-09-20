"""Host ownership/actions for portable durable operation evidence."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json

from amplifier_operations import OperationJournal
from amplifier_operations.journal import ACTIVE


def definitions():
    from .service import schema, string

    common = {"sessionId": string(200), "id": string(200)}
    read = {
        **common,
        "cursor": {"type": "integer", "minimum": 0},
        "maxBytes": {"type": "integer", "minimum": 4096, "maximum": 100000},
    }
    return {
        "operations.list": (
            "Read observed operations for a conversation without selecting or running it. Existing Smart Tool receipts and workers share this view.",
            schema(
                {
                    "sessionId": string(200),
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                ["sessionId"],
            ),
        ),
        "operations.status": (
            "Read durable operation status and actual completion evidence; unknown outcomes are never inferred as success.",
            schema(common, ["sessionId", "id"]),
        ),
        "operations.read": (
            "Read bounded durable output by cursor. A gap explicitly identifies missing or expired evidence.",
            schema(read, ["sessionId", "id"]),
        ),
        "operations.wait": (
            "Wait for an actual change to the observed operation. Does not start, resume, cancel or replay work.",
            schema(
                {
                    **read,
                    "afterRevision": string(200),
                    "waitMs": {"type": "integer", "minimum": 0, "maximum": 60000},
                },
                ["sessionId", "id"],
            ),
        ),
        "operations.cancel": (
            "Request cancellation of an owned live process through existing shell hooks/approvals. A request is not proof of cancellation; read the observed final state.",
            schema(common, ["sessionId", "id"]),
        ),
    }


class Operations:
    def __init__(self, service):
        self.service = service
        self.journal = OperationJournal(service.data_dir / "operations.sqlite3")
        self.journal.recover()
        self.changed = asyncio.Event()
        self.closed = False
        self.pending = set()

    def notify(self):
        previous = self.changed
        self.changed = asyncio.Event()
        previous.set()

    async def observe(self, session_id, runtime_session_id, event):
        # session_id comes from the authenticated worker bridge, never the tool.
        self.service._session(session_id)
        if event.get("source") != "tool-bash" or event.get("kind") != "process":
            raise ValueError(
                "No host adapter is registered for this operation producer"
            )
        if self.closed:
            raise ValueError("Operation journal is closing")
        task = asyncio.create_task(
            asyncio.to_thread(
                self.journal.ingest, session_id, runtime_session_id, event
            )
        )
        self.pending.add(task)

        def settled(done):
            self.pending.discard(done)
            # A timed-out producer may have stopped awaiting this shielded write.
            # Retrieve its exception without logging command output or secrets.
            if not done.cancelled():
                done.exception()
            self.notify()

        task.add_done_callback(settled)
        value, changed = await asyncio.shield(task)
        return {
            "id": value["id"],
            "revision": value["revision"],
            "duplicate": not changed,
        }

    @staticmethod
    def projection(source, session_id, value):
        state = value.get("status", "outcome_unknown")
        state = {
            "interrupted": "outcome_unknown",
            "stopped": "cancelled",
            "error": "failed",
            "starting": "queued",
            "working": "running",
            "idle": "waiting_input",
            "done": "completed",
        }.get(state, state)
        if state not in ACTIVE | {
            "completed",
            "failed",
            "cancelled",
            "outcome_unknown",
            "waiting_input",
        }:
            state = "outcome_unknown"
        result = {
            "id": source + ":" + value["id"],
            "sessionId": session_id,
            "source": source,
            "sourceId": value["id"],
            "kind": source,
            "state": state,
            "createdAt": value.get("createdAt", value.get("startedAt", 0)),
            "updatedAt": value.get("updatedAt", value.get("endedAt", 0)),
            "outputComplete": None,
            "captureComplete": None,
            "controlAvailable": False,
            "returncode": None,
            "evidence": {
                key: copy.deepcopy(value[key])
                for key in ("result", "error", "report", "action", "name")
                if key in value
            },
        }
        result["revision"] = hashlib.sha256(
            json.dumps(result, sort_keys=True, default=str).encode()
        ).hexdigest()
        return result

    def sources(self, session_id):
        service = self.service
        session = service._session(session_id)
        values = []
        if service.smart_tools:
            for (data,) in service.db.execute(
                "SELECT value FROM smart_tool_operations WHERE json_extract(value,'$.target.sessionId')=? ORDER BY rowid DESC LIMIT 100",
                (session_id,),
            ):
                row = json.loads(data)
                values.append(self.projection("smart-tool", session_id, row))
        values.extend(
            self.projection("worker", session_id, row)
            for row in session.get("workers", [])
        )
        return values

    def status(self, session_id, identity):
        self.service._session(session_id)
        if identity.startswith("smart-tool:"):
            if not self.service.smart_tools:
                raise ValueError("Operation not found in this conversation")
            row = self.service.db.execute(
                "SELECT value FROM smart_tool_operations WHERE id=?",
                (identity.removeprefix("smart-tool:"),),
            ).fetchone()
            value = json.loads(row[0]) if row else None
            if value is None or value.get("target", {}).get("sessionId") != session_id:
                raise ValueError("Operation not found in this conversation")
            return self.projection("smart-tool", session_id, value)
        if identity.startswith("worker:"):
            value = next(
                (row for row in self.sources(session_id) if row["id"] == identity), None
            )
            if value is None:
                raise ValueError("Operation not found in this conversation")
            return value
        value = self.journal.status(session_id, identity)
        value["revision"] = str(value["revision"])
        return value

    def read(self, args):
        value = self.status(args["sessionId"], args["id"])
        if value["source"] != "process":
            # Existing receipts stay authoritative; inspect their evidence rather
            # than synthesizing a second streaming-output log.
            return {
                **value,
                "chunks": [],
                "nextCursor": 0,
                "hasMore": False,
                "cursorGap": False,
            }
        result = self.journal.read(
            args["sessionId"],
            args["id"],
            args.get("cursor", 0),
            args.get("maxBytes", 16384),
        )
        result["revision"] = str(result["revision"])
        return result

    async def dispatch(self, action, args, origin):
        from .service import AppError

        sid = args["sessionId"]
        self.service._session(sid)
        try:
            if action == "operations.list":
                values = self.journal.list(sid, args.get("limit", 50)) + self.sources(
                    sid
                )
                values.sort(key=lambda row: row.get("createdAt") or 0, reverse=True)
                result = {
                    "operations": values[: args.get("limit", 50)],
                    "capabilities": {
                        "durableProcessOutput": True,
                        "pty": False,
                        "nativeWindows": False,
                    },
                }
            elif action == "operations.cancel":
                result = await self.cancel(sid, args["id"], origin)
            elif action == "operations.wait":
                # Capture the event before reading the revision to avoid a
                # change slipping between snapshot and subscription.
                deadline = (
                    asyncio.get_running_loop().time() + args.get("waitMs", 1000) / 1000
                )
                while True:
                    changed = self.changed
                    result = self.read(args)
                    if (
                        str(result["revision"]) != args.get("afterRevision")
                        or self.closed
                    ):
                        break
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        await asyncio.wait_for(changed.wait(), remaining)
                    except TimeoutError:
                        break
            elif action == "operations.status":
                result = self.status(sid, args["id"])
            else:
                result = self.read(args)
            return {"accepted": True, "result": result, "effects": []}
        except (TypeError, ValueError) as exc:
            raise AppError(str(exc), 409) from exc

    async def cancel(self, session_id, identity, origin):
        value = self.status(session_id, identity)
        if value["source"] != "process":
            raise ValueError(
                "Cancellation is unavailable for this receipt adapter; use the existing worker or Smart Tool controls"
            )
        if value["state"] not in ACTIVE:
            return value
        session = self.service._session(session_id)
        if session.get("ownership", {}).get("status") in {
            "blocked",
            "yielding",
            "yielded",
            "taking-over",
        }:
            raise ValueError("This conversation is read-only on this host")
        runtime = self.service.runtime
        if runtime is None:
            raise ValueError(
                "The owning runtime is unavailable; cancellation was not attempted"
            )
        # Never mount/resume a missing runtime just to cancel historical work.
        response = await runtime.control(
            session_id,
            "operations.cancel",
            {
                "processId": value["sourceId"],
                "runtimeSessionId": value["runtimeSessionId"],
                "ownerId": value["ownerId"],
                "operationId": identity,
                "actor": origin,
            },
        )
        result = response.get("result", response) if isinstance(response, dict) else {}
        if result.get("success") is False:
            raise ValueError(
                result.get("error", {}).get("message", "Cancellation denied")
            )
        return {**self.status(session_id, identity), "request": response}

    def interrupted(self, session_id):
        self.journal.recover(
            session_id,
            "The owning runtime ended; outcome was not observed and work was not replayed.",
        )
        self.notify()

    async def close(self):
        self.closed = True
        self.notify()
        await asyncio.gather(*self.pending, return_exceptions=True)
        self.journal.close()
