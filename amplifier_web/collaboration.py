"""Scoped peer authority and receipts in existing session/command records.

No transcript or scheduler is owned here. Native adapters must qualify final
results before dependency continuation can be enabled.
"""
from __future__ import annotations

import asyncio
import copy
from contextvars import ContextVar
import json
import time
import uuid

from amplifier_operations.coordination import fingerprint, qualifying_reply

PRINCIPAL = ContextVar("collaboration_principal", default=None)
CREATION = ContextVar("collaboration_creation", default=None)
BUSY = {"working", "starting", "running", "busy", "stopping", "ready"}


def definitions(schema, string):
    sid = {"sessionId": string(200)}
    references = {"type": "array", "maxItems": 16, "items": string(2000)}
    message = {**sid, "senderSessionId": string(200), "grantId": string(200),
               "text": {**string(16000), "minLength": 1},
               "mode": {"enum": ["notify", "queue", "steer"]},
               "replyToRequestId": string(200), "references": references}
    return {
        "coordination.grant": ("Authorize bounded peer exchanges once. Only a real human action can issue a grant; this retains that request without starting a turn.", schema({
            **sid, "participants": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True, "items": string(200)},
            "purpose": {**string(4000), "minLength": 1},
            "modes": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"enum": ["notify", "queue", "steer"]}},
            "idleStart": {"type": "boolean"}, "allowCreate": {"type": "boolean"},
        }, ["sessionId", "participants", "purpose", "modes"])),
        "coordination.revoke": ("Revoke a host grant. Saved messages remain readable; pending automatic admission is suppressed.", schema({**sid, "grantId": string(200)}, ["sessionId", "grantId"])),
        "coordination.context": ("Read this task's bounded current grants and request receipts, without loading peer transcripts.", schema(sid, ["sessionId"])),
        "coordination.send": ("Send host-attributed peer task input under a current grant. Notify never wakes; queue waits for idle; unsupported steer has no effects.", schema(message, ["sessionId", "text", "grantId"])),
        "coordination.create": ("Commission an ordinary durable root task chat with source configuration, creator/request links and its own output namespace. Does not select it.", schema({
            "senderSessionId": string(200), "grantId": string(200), "title": {**string(100), "minLength": 1},
            "text": {**string(16000), "minLength": 1}, "references": references,
        }, ["grantId", "title", "text"])),
        "coordination.read": ("Read a bounded window from one existing native conversation without selecting or warming it.", schema({
            **sid, "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "textLimit": {"type": "integer", "minimum": 1, "maximum": 4000},
        }, ["sessionId"])),
        "coordination.result": ("Inspect admission and exact qualified final result links for one request. Acknowledgements and unrelated turns do not count.", schema({"requestId": string(200)}, ["requestId"])),
        "coordination.subscribe": ("Check durable dependency continuation support. Unsupported adapters return an explicit limitation without waking or replaying work.", schema({
            **sid, "requestId": string(200), "grantId": string(200),
        }, ["sessionId", "requestId", "grantId"])),
    }


class Collaboration:
    def __init__(self, service):
        self.service = service
        self.draining = set()
        # Index receipts, not conversations or transcripts.
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_target ON commands(
            json_extract(receipt,'$.commandAction'), json_extract(receipt,'$.target.sessionId'))""")
        for identity, encoded in service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.delivery')='submitting'""").fetchall():
            value = json.loads(encoded)
            value.update(delivery="unknown", detail="Host restarted during admission; no replay.")
            self.save(identity, value)

    def error(self, text, status=403):
        from .service import AppError
        raise AppError(text, status)

    def source(self, args, origin, caller):
        sid = caller if origin not in {"ui", "user"} else args.get("senderSessionId") or args.get("sessionId")
        if not sid:
            self.error("A user must explicitly authorize a calling conversation.")
        source = self.service._session(sid)
        actor = PRINCIPAL.get()
        native = source.get("runtimeSessionId") or source.get("nativeIdentity") or source["id"]
        if origin not in {"ui", "user"} and actor and actor != native:
            self.error("A child cannot borrow its root's collaboration grant.")
        if source.get("sessionKind", "root") != "root":
            self.error("Collaboration grants belong to ordinary root conversations.")
        return source

    def grant(self, source, identity):
        rows = self.service.db.execute("""SELECT receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.grant'
            AND json_extract(receipt,'$.result.id')=?""", (identity,)).fetchall()
        if not rows:
            self.error("A user must explicitly authorize a current collaboration grant.")
        value = json.loads(rows[0][0])["result"]
        if value.get("revoked") or source["id"] not in value["participants"]:
            self.error("The collaboration grant is revoked or outside this caller's scope.")
        if source["workspace"] != value["workspace"]:
            self.error("The collaboration grant's workspace changed.")
        return value

    def authorize(self, source, args):
        grant = self.grant(source, args["grantId"])
        target = self.service._session(args["sessionId"])
        if target["id"] not in grant["participants"] or target["workspace"] != grant["workspace"]:
            self.error("The target is outside this collaboration grant.")
        if args.get("mode", "queue") not in grant["modes"]:
            self.error("This delivery mode is outside the collaboration grant.")
        if target.get("sessionKind", "root") != "root":
            self.error("Peer worker admission is unsupported; target its authorized root instead.", 409)
        return grant, target

    def receipt(self, identity):
        row = self.service.db.execute("SELECT receipt FROM commands WHERE id=?", (identity,)).fetchone()
        if not row:
            self.error("Request receipt not found.", 404)
        return json.loads(row[0])

    def save(self, identity, receipt):
        self.service.db.execute("UPDATE commands SET receipt=? WHERE id=?", (json.dumps(receipt), identity))

    def duplicate(self, identity, digest):
        row = self.service.db.execute("SELECT fingerprint,receipt FROM commands WHERE id=?", (identity,)).fetchone()
        if row:
            if row[0] != digest:
                self.error("This command ID was already used with different contents.", 409)
            return {**json.loads(row[1]), "duplicate": True}

    def insert(self, identity, digest, receipt):
        self.service.db.execute("INSERT INTO commands(id,fingerprint,receipt) VALUES(?,?,?)",
                                (identity, digest, json.dumps(receipt)))

    def current(self, sid):
        grants = [json.loads(row[0])["result"] for row in self.service.db.execute("""SELECT receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.grant'
            AND EXISTS (SELECT 1 FROM json_each(json_extract(receipt,'$.result.participants')) WHERE value=?)
            ORDER BY rowid DESC LIMIT 32""", (sid,)).fetchall()]
        requests = [json.loads(row[0]) for row in self.service.db.execute("""SELECT receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND (json_extract(receipt,'$.senderSessionId')=? OR json_extract(receipt,'$.target.sessionId')=?)
            ORDER BY rowid DESC LIMIT 32""", (sid, sid)).fetchall()]
        return {"grants": [row for row in grants if sid in row["participants"]],
                "requests": requests, "capturedAt": time.time(),
                "continuation": {"supported": False, "reason": "The current adapter does not attest final-channel result kind; durable dependency continuation is not enabled."}}

    def guard(self, receipt):
        try:
            source = self.service._session(receipt["senderSessionId"])
            grant, target = self.authorize(source, {
                "grantId": receipt["grantId"], "sessionId": receipt["target"]["sessionId"], "mode": receipt["mode"]})
            if grant["revision"] != receipt["grantRevision"]:
                return "The collaboration grant changed."
            if target.get("interruptionRevision", 0) != receipt["interruptionRevision"]:
                return "The user interrupted the recipient after this request."
            if target.get("status") in {"stopped", "stopping", "interrupted", "error"}:
                return "The recipient is stopped or unavailable."
            task = target.get("task") or self.service.coordination.task(target["id"])
            if task.get("status") in {"paused", "blocked", "completed"}:
                return "The recipient task is not active."
            if (task.get("id"), task.get("revision")) != (receipt.get("taskId"), receipt.get("taskRevision")):
                return "The recipient task changed."
            if target.get("ownership", {}).get("status") in {"blocked", "yielding", "yielded", "yield-failed", "taking-over"}:
                return "The native owner is unavailable."
            if not grant.get("idleStart"):
                return "This grant does not authorize idle starts. The message remains readable."
        except Exception as exc:
            return str(exc)

    async def drain(self, sid):
        if sid in self.draining:
            return
        self.draining.add(sid)
        try:
            for identity, encoded in self.service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.target.sessionId')=?
                AND json_extract(receipt,'$.delivery')='queued' ORDER BY rowid LIMIT 32""", (sid,)).fetchall():
                async with self.service.lock:
                    receipt = self.receipt(identity)
                    target = self.service._session(sid)
                    if target.get("status") in BUSY:
                        break
                    reason = self.guard(receipt)
                    if reason:
                        receipt.update(delivery="suppressed", detail=reason)
                        self.save(identity, receipt)
                        self.service._publish()
                        continue
                    # Durable claim precedes any runtime effect. Unknown never
                    # returns to queued, including cancellation and restart.
                    receipt["delivery"] = "submitting"
                    self.save(identity, receipt)
                    snapshot = copy.deepcopy(target)
                arguments = {"inputId": identity, "grantId": receipt["grantId"],
                             "taskId": receipt.get("taskId"), "taskRevision": receipt.get("taskRevision")}
                try:
                    result = await self.service.runtime.collaboration_input(
                        snapshot, arguments, lambda: self.guard(self.receipt(identity)), self.service.on_runtime_event)
                    phase = "accepted" if result.get("accepted") else "suppressed"
                except BaseException as exc:
                    result, phase = {"reason": "Admission outcome unknown; no replay."}, "unknown"
                    if isinstance(exc, asyncio.CancelledError):
                        receipt.update(delivery=phase, detail=result["reason"])
                        self.save(identity, receipt)
                        raise
                async with self.service.lock:
                    receipt = self.receipt(identity)
                    receipt.update(delivery=phase, admission=result)
                    self.save(identity, receipt)
                    self.service._publish()
                # Native generation events own status. Do not send a second
                # input merely because its acknowledgement arrived first.
                if phase in {"accepted", "unknown"}:
                    break
        finally:
            self.draining.discard(sid)

    def admission(self, sid, args):
        receipt = self.receipt(args["inputId"])
        if (receipt.get("commandAction") != "coordination.send" or receipt.get("delivery") != "submitting"
                or receipt["target"]["sessionId"] != sid or args.get("grantId") != receipt["grantId"]
                or args.get("taskId") != receipt.get("taskId") or args.get("taskRevision") != receipt.get("taskRevision")):
            return {"admitted": False, "reason": "The exact host request is no longer submitting."}
        reason = self.guard(receipt)
        message = next((row for row in self.service._session(sid).get("messages", [])
                        if row.get("inputId") == args["inputId"] and row.get("inputOrigin") == "peer"), None)
        if message is None:
            return {"admitted": False, "reason": "The saved host-attributed input is unavailable."}
        return {"admitted": not reason, "reason": reason, "message": copy.deepcopy(message)}

    async def route(self, action, args, origin, command_id, caller):
        """One gate for equivalent old/new host entry points."""
        if origin in {"ui", "user", "scheduler"}:
            return None
        if action == "session.create":
            authorized = CREATION.get()
            if authorized and authorized[:2] == (caller, fingerprint(args)):
                self.grant(self.source({}, origin, caller), authorized[2])
                return None
            self.error("A user must explicitly authorize durable task creation; use coordination.create with a current grant.")
        target = args.get("sessionId") or (args.get("id") if action.startswith("session.") else None)
        if action in {"conversation.send", "conversation.stop", "worker.spawn", "worker.message", "worker.steer", "worker.stop",
                      "conversation.retry", "runtime.control", "configuration.apply", "session.fork", "session.takeover",
                      "bundle.switch", "bundle.fork", "call.start", "message.edit"}:
            if not caller:
                self.error("A user must explicitly authorize a calling conversation.")
            target = target or caller
            args.setdefault("sessionId", target) if not action.startswith("session.") else None
            if target == caller:
                return None
            if action == "conversation.send":
                if not args.get("grantId"):
                    self.error("A user must explicitly authorize a current collaboration grant.")
                if set(args) - {"sessionId", "text", "grantId", "preserveDraft", "mode", "references", "replyToRequestId"}:
                    self.error("Peer input cannot borrow composer attachments or browser references.")
                return await self.service.dispatch("coordination.send",
                    {key: value for key, value in args.items() if key != "preserveDraft"},
                    origin, command_id, include_state=False, caller_session_id=caller)
            self.error("A user must explicitly perform this peer mutation; collaboration does not grant stop, worker or settings authority.")
        passive = {"session.select", "session.inspect", "session.export", "session.shareRead", "session.shareList",
                   "conversation.delivery", "configuration.inspect", "task.get", "capacity.read",
                   "question.list", "question.read"}
        if target and target != caller and not action.startswith("coordination.") and action not in passive:
            # Targeted read APIs have explicit read/list/inspect verbs. Unknown
            # target mutations do not gain authority through a legacy name.
            if action.rsplit(".", 1)[-1] not in {"read", "get", "list", "inspect", "status", "result"}:
                self.error("A user must explicitly perform peer mutations outside a collaboration send grant.")
        return None

    async def dispatch(self, action, args, origin, command_id, caller):
        source = self.source(args, origin, caller) if action not in {"coordination.result", "coordination.read"} else None
        if action == "coordination.read":
            from .history_query import query_history
            return {"accepted": True, "result": await query_history(self.service, {
                "action": "read", "session_id": args["sessionId"], "scope": "all",
                "offset": args.get("offset", 0), "limit": args.get("limit", 16), "text_limit": args.get("textLimit", 1000),
            }, caller or args["sessionId"])}
        if action == "coordination.context":
            if origin not in {"ui", "user"} and args["sessionId"] != caller:
                self.error("Current coordination context belongs to the caller.")
            return {"accepted": True, "result": self.current(args["sessionId"])}
        if action == "coordination.result":
            receipt = self.receipt(args["requestId"])
            if receipt.get("commandAction") != "coordination.send":
                self.error("This receipt is not a peer request.", 409)
            sid = receipt["target"]["sessionId"]
            await self.service.history.ensure_loaded(sid)
            target = self.service._session(sid)
            results = [{"messageId": message["id"], "sessionId": sid, "inputId": args["requestId"]}
                       for message in target.get("messages", [])
                       if any(qualifying_reply(message, event, args["requestId"]) for event in target.get("generations", []))]
            return {"accepted": True, "result": {"receipt": receipt, "results": results,
                "qualified": bool(results), "qualificationSupported": False,
                "detail": "Current loop events do not attest final/result kind. No successful dependency outcome is inferred."}}
        if action == "coordination.send":
            await self.service.history.ensure_loaded(args["sessionId"])
        identity = command_id or str(uuid.uuid4())
        digest = fingerprint([action, args, origin, source["id"]])
        if action in {"coordination.grant", "coordination.revoke"} and origin not in {"ui", "user"}:
            self.error("Only a real human action can issue or revoke collaboration grants.")
        async with self.service.lock:
            duplicate = self.duplicate(identity, digest)
            if duplicate:
                # Re-reading a prior receipt never dispatches work.
                return duplicate
            if action == "coordination.grant":
                participants = list(dict.fromkeys([source["id"], *args["participants"]]))
                if len(participants) > 8:
                    self.error("A grant supports at most eight participant roots.", 400)
                for sid in participants:
                    target = self.service._session(sid)
                    if target["workspace"] != source["workspace"] or target.get("sessionKind", "root") != "root":
                        self.error("Choose ordinary roots in this workspace.")
                message = self.service._message(source, "user", args["purpose"], "collaboration authorization",
                                               inputOrigin=origin, inputId=identity, hostAction=action)
                value = {"id": identity, "sourceMessageId": message["id"], "issuer": origin,
                         "workspace": source["workspace"], "participants": participants,
                         "purpose": args["purpose"], "modes": args["modes"],
                         "idleStart": args.get("idleStart", False), "allowCreate": args.get("allowCreate", False),
                         "revision": 1, "revoked": False, "createdAt": time.time()}
                receipt = {"accepted": True, "commandAction": action, "result": value}
            elif action == "coordination.revoke":
                value = self.grant(source, args["grantId"])
                value.update(revoked=True, revision=value["revision"] + 1, revokedAt=time.time())
                grant_receipt = self.receipt(value["id"])
                grant_receipt["result"] = value
                self.save(value["id"], grant_receipt)
                receipt = {"accepted": True, "commandAction": action, "result": value}
            elif action == "coordination.subscribe":
                self.grant(source, args["grantId"])
                receipt = {"accepted": False, "commandAction": action, "result": {
                    "supported": False, "effect": "none",
                    "reason": "Durable continuation requires adapter-qualified final results and guarded dependency admission; the current loop does not supply final/result qualification."}}
            elif action == "coordination.create":
                # Creation is handled outside the service lock below.
                grant = self.grant(source, args["grantId"])
                if not grant["allowCreate"]:
                    self.error("This grant does not authorize durable task creation.")
                receipt = None
            else:
                grant, target = self.authorize(source, args)
                mode = args.get("mode", "queue")
                if mode == "steer":
                    return {"accepted": False, "result": {"supported": False, "effect": "none",
                        "reason": "This host has no qualified generation-anchored peer steering adapter. Choose queue explicitly."}}
                if mode == "queue" and not hasattr(self.service.runtime, "collaboration_input"):
                    return {"accepted": False, "result": {"supported": False, "effect": "none",
                        "reason": "This runtime lacks guarded idle peer admission."}}
                if not args["text"].strip():
                    self.error("Enter a peer message.", 400)
                if args.get("replyToRequestId"):
                    previous = self.receipt(args["replyToRequestId"])
                    if (previous.get("commandAction") != "coordination.send" or previous["target"]["sessionId"] != source["id"]
                            or previous["senderSessionId"] != target["id"] or previous["grantId"] != grant["id"]):
                        self.error("Reply linkage must identify the exact incoming request.")
                task = target.get("task") or self.service.coordination.task(target["id"])
                envelope = {"senderSessionId": source["id"], "senderRuntimeSessionId": source.get("runtimeSessionId") or source["id"],
                    "recipientSessionId": target["id"], "grantId": grant["id"], "grantRevision": grant["revision"],
                    "sourceMessageId": grant["sourceMessageId"], "requestId": identity, "inputId": identity,
                    "mode": mode, "purpose": grant["purpose"], "references": args.get("references", []),
                    "replyToRequestId": args.get("replyToRequestId")}
                if target.get("collaboration"):
                    envelope["task"] = {key: target["collaboration"][key] for key in
                        ("creatorSessionId", "requestId", "outputNamespace", "configurationHash")}
                message = self.service._message(target, "user", args["text"], "peer", inputId=identity,
                                               inputOrigin="peer", peerEnvelope=envelope)
                receipt = {"accepted": True, "commandAction": "coordination.send", "requestId": identity,
                    "inputId": identity, "messageId": message["id"], "senderSessionId": source["id"],
                    "target": {"sessionId": target["id"]}, "grantId": grant["id"], "grantRevision": grant["revision"],
                    "mode": mode, "delivery": "notified" if mode == "notify" else "queued",
                    "interruptionRevision": target.get("interruptionRevision", 0),
                    "taskId": task.get("id"), "taskRevision": task.get("revision")}
            if receipt is not None:
                self.insert(identity, digest, receipt)
                self.service._publish()
        if action == "coordination.create":
            return await self.create(source, grant, args, origin, identity, digest)
        if receipt.get("delivery") == "queued":
            await self.drain(receipt["target"]["sessionId"])
            return self.receipt(identity)
        return receipt

    async def create(self, source, grant, args, origin, identity, digest):
        from .session_creation import template
        try:
            config, summary = template(self.service, source)
        except ValueError as exc:
            self.error(str(exc), 409)
        if len(grant["participants"]) >= 8:
            self.error("This grant's participant limit is reached.", 409)
        new_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "collaborative-task:" + source["id"] + ":" + identity))
        async with self.service.lock:
            duplicate = self.duplicate(identity, digest)
            if duplicate:
                return duplicate
            self.insert(identity, digest, {"accepted": True, "commandAction": "coordination.create",
                "sessionId": new_id, "delivery": "unknown", "detail": "Creation admitted; inspect this exact chat before any further effect."})
        values = {"id": new_id, "title": args["title"], "workspace": source["workspace"],
                  "bundle": config["bundle"], "select": False}
        token = CREATION.set((source["id"], fingerprint(values), grant["id"]))
        try:
            await self.service.dispatch("session.create", values, origin, identity + ":create",
                                        include_state=False, caller_session_id=source["id"])
        finally:
            CREATION.reset(token)
        async with self.service.lock:
            # Revocation while creation awaited leaves an idle preserved chat,
            # never an implicitly restarted task.
            current = self.grant(source, grant["id"])
            from .session_creation import apply
            target = self.service._session(new_id)
            apply(self.service, target, config)
            target["collaboration"] = {"creatorSessionId": source["id"], "requestId": identity,
                "grantId": grant["id"], "configurationHash": summary["configurationHash"],
                "outputNamespace": "working-files/tasks/" + new_id, "brief": args["text"],
                "references": args.get("references", [])}
            if new_id not in current["participants"]:
                current["participants"].append(new_id)
                saved = self.receipt(current["id"])
                saved["result"] = current
                self.save(current["id"], saved)
            receipt = {"accepted": True, "commandAction": "coordination.create",
                       "sessionId": new_id, "result": target["collaboration"]}
            self.save(identity, receipt)
            self.service._publish()
        # The durable brief is ordinary attributed input, not copied history.
        await self.dispatch("coordination.send", {"sessionId": new_id, "senderSessionId": source["id"],
            "grantId": grant["id"], "text": args["text"], "references": args.get("references", []),
            "mode": "queue" if "queue" in grant["modes"] else "notify"}, origin, identity + ":brief", source["id"])
        return receipt