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

from amplifier_operations.coordination import fingerprint

PRINCIPAL = ContextVar("collaboration_principal", default=None)
BINDING = ContextVar("collaboration_binding", default=None)
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
        "coordination.grant": ("Authorize bounded peer exchanges once. An agent must cite its current retained human sourceMessageId; the host asks once to approve the exact scope.", schema({
            **sid, "participants": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True, "items": string(200)},
            "sourceMessageId": string(200),
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
        "coordination.reply": ("Declare result, ack, defer or decline for one delivered request during your actual generation. The host seals only at matching successful root termination; an agent claim is not independently checked correctness.", schema({
            "requestId": string(200), "kind": {"enum": ["result", "ack", "defer", "decline"]},
            "outcome": {"enum": ["success", "failed", "deferred", "declined"]},
            "text": {**string(4000), "minLength": 1}, "references": references,
            "messageIds": {"type": "array", "maxItems": 16, "items": string(200)},
        }, ["requestId", "kind", "outcome", "text"])),
        "coordination.subscribe": ("Save one request-specific wait and stable continuation. Successful sealed results may wake once through guarded input; cursor reads confer no authority.", schema({
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
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_sender ON commands(
            json_extract(receipt,'$.commandAction'), json_extract(receipt,'$.senderSessionId'))""")
        for identity, encoded in service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.delivery')='submitting'""").fetchall():
            value = json.loads(encoded)
            value.update(delivery="unknown", detail="Host restarted during admission; no replay.")
            self.save(identity, value)
        for identity, encoded in service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.grant.pending'""").fetchall():
            value = json.loads(encoded)
            value.update(accepted=False, delivery="unknown", detail="Approval interrupted by restart; no replay.")
            self.save(identity, value)
        service.db.commit()

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
        row = self.service.db.execute("SELECT receipt FROM commands WHERE id=?", (identity,)).fetchone()
        if not row or json.loads(row[0]).get("commandAction") != "coordination.grant":
            self.error("A user must explicitly authorize a current collaboration grant.")
        value = json.loads(row[0])["result"]
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
        self.refresh_reference(receipt)

    def duplicate(self, identity, digest):
        row = self.service.db.execute("SELECT fingerprint,receipt FROM commands WHERE id=?", (identity,)).fetchone()
        if row:
            if row[0] != digest:
                self.error("This command ID was already used with different contents.", 409)
            return {**json.loads(row[1]), "duplicate": True}

    def insert(self, identity, digest, receipt):
        self.service.db.execute("INSERT INTO commands(id,fingerprint,receipt) VALUES(?,?,?)",
                                (identity, digest, json.dumps(receipt)))
        self.refresh_reference(receipt)

    def refresh_reference(self, receipt):
        """Only affected roots retain a tiny reference projection, not bodies."""
        if receipt.get("commandAction") == "coordination.grant":
            sids = receipt["result"]["participants"]
        else:
            sids = [receipt.get("senderSessionId"), receipt.get("target", {}).get("sessionId")]
        for sid in set(sids):
            if not sid:
                continue
            try:
                session = self.service._session(sid)
            except Exception:
                continue
            value = self.current(sid)
            session["coordinationReference"] = {
                "sessionId": sid, "capturedAt": value["capturedAt"], "bounded": True,
                "grants": [{key: row[key] for key in ("id", "revision", "revoked", "participants", "idleStart", "allowCreate")}
                           for row in value["grants"][:8]],
                "requests": [{key: row.get(key) for key in ("requestId", "senderSessionId", "target", "delivery", "grantId")}
                             for row in value["requests"][:8]], "continuation": value["continuation"]}

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
                "continuation": {"supported": hasattr(self.service.runtime, "collaboration_input"),
                                 "qualification": "recipient declaration plus successful root terminal evidence",
                                 "independentArtifactVerification": False}}

    def active_binding(self, source):
        binding = BINDING.get() or {}
        native = source.get("runtimeSessionId") or source.get("nativeIdentity") or source["id"]
        active = source.get("collaborationGeneration") or {}
        if (PRINCIPAL.get() != native or not binding.get("_generationId")
                or binding["_generationId"] != active.get("id") or active.get("terminal")):
            self.error("A real human source requires the current transport-bound root generation.")
        return binding, active

    def human_source(self, source, args):
        binding, active = self.active_binding(source)
        if args.get("sessionId") != source["id"]:
            self.error("A model cannot propose a grant for a different source root.")
        message = self.resolve_message(source, args.get("sourceMessageId"))
        delivered = {row.get("inputId") for row in binding.get("_inputBindings", [])}
        if (not message or message.get("role") != "user" or message.get("inputOrigin") not in {"ui", "user", "voice"}
                or message.get("hostAction") or message.get("questionId") or message.get("questionReceipt")
                or message.get("scheduledRunId") or message.get("scheduledRun") or message.get("scheduleId") or message.get("peerEnvelope")
                or message.get("inputId") not in delivered or message.get("inputId") not in active.get("inputIds", [])):
            self.error("A real human source must be the retained current delivered input, not peer/generated authority.")
        return message

    def resolve_message(self, session, identity):
        message = next((row for row in session.get("messages", []) if row["id"] == identity), None)
        if message or not session.get("nativeProject"):
            return message
        from .automatic_history import read_transcript
        rows = read_transcript(session, limit=None)["messages"]
        native = next((row for row in rows if row["id"] == identity), None)
        if native is None:
            return None
        # User authority comes only from retained host input provenance, never
        # from native role=user or equal prose. Index anchors resolve assistants.
        retained = next((row for row in session.get("messages", []) if (
            native.get("nativeInputId") and row.get("inputId") == native["nativeInputId"]
            or row.get("nativeIndex") == native.get("nativeIndex"))), None)
        if retained:
            return retained
        anchor = next((row for row in session.get("collaborationMessageAnchors", [])
                       if row["messageId"] == identity and row["nativeIndex"] == native.get("nativeIndex")), None)
        return {**native, "generationId": anchor["generationId"]} if anchor else None

    async def agent_grant(self, source, args, identity, digest):
        """One exact-scope human decision; never await while holding command lock."""
        await self.service.history.ensure_loaded(source["id"])
        async with self.service.lock:
            duplicate = self.duplicate(identity, digest)
            if duplicate:
                return duplicate
            message = self.human_source(source, args)
            participants = list(dict.fromkeys([source["id"], *args["participants"]]))
            self.validate_participants(source, participants)
            reused = self.service.db.execute("""SELECT 1 FROM commands
                WHERE json_extract(receipt,'$.commandAction') IN ('coordination.grant','coordination.grant.pending')
                AND json_extract(receipt,'$.result.sourceMessageId')=? LIMIT 1""", (message["id"],)).fetchone()
            if reused:
                self.error("This human source is already bound to a scoped grant; do not reuse it to widen authority.", 409)
            value = {"id": identity, "sourceMessageId": message["id"], "sourceDigest": fingerprint(message["text"]),
                     "issuer": "human", "mediation": "agent", "workspace": source["workspace"],
                     "participants": participants, "purpose": args["purpose"], "modes": args["modes"],
                     "idleStart": args.get("idleStart", False), "allowCreate": args.get("allowCreate", False),
                     "revision": 1, "revoked": False, "createdAt": time.time()}
            generation = (source.get("collaborationGeneration") or {}).get("id")
            stop = source.get("interruptionRevision", 0)
            receipt = {"accepted": False, "commandAction": "coordination.grant.pending",
                       "delivery": "awaiting_approval", "result": value}
            self.insert(identity, digest, receipt)
            self.service._publish()
        labels = [{"id": sid, "title": self.service._session(sid)["title"]} for sid in participants]
        prompt = "Authorize this task-scoped collaboration once?\nHuman request: " + message["text"] + "\nExact proposed scope:\n" + json.dumps({
            **value, "participants": labels}, sort_keys=True)
        try:
            decision = await self.service.runtime.collaboration_approval(source["id"], prompt)
        except (AttributeError, RuntimeError, TimeoutError, OSError):
            decision = {"allowed": False}
        async with self.service.lock:
            try:
                current = self.human_source(source, args)
                self.validate_participants(source, participants)
                allowed = (decision.get("allowed") is True and fingerprint(current["text"]) == value["sourceDigest"]
                           and generation == source.get("collaborationGeneration", {}).get("id")
                           and stop == source.get("interruptionRevision", 0))
            except Exception:
                allowed = False
            receipt.update(accepted=allowed, commandAction="coordination.grant" if allowed else "coordination.grant.pending",
                           delivery="approved" if allowed else "denied")
            self.save(identity, receipt)
            self.service._publish()
        return receipt

    def validate_participants(self, source, participants):
        if len(participants) > 8:
            self.error("A grant supports at most eight participant roots.", 400)
        for sid in participants:
            target = self.service._session(sid)
            if target["workspace"] != source["workspace"] or target.get("sessionKind", "root") != "root":
                self.error("Choose ordinary roots in this workspace.")

    def guard(self, receipt):
        try:
            from .updates import work_paused
            if work_paused(self.service.state):
                return "Host work is paused for an update; peer admission is suppressed."
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
            if receipt.get("mode") == "steer":
                active = target.get("collaborationGeneration") or {}
                if active.get("terminal") or active.get("id") != receipt.get("targetGenerationId"):
                    return "The anchored recipient generation is no longer active."
            elif not grant.get("idleStart"):
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
                    self.service.db.commit()
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
                        self.service.db.commit()
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
        if receipt.get("mode") == "steer" and args.get("targetGenerationId") != receipt.get("targetGenerationId"):
            return {"admitted": False, "reason": "The steering generation anchor changed."}
        reason = self.guard(receipt)
        message = next((row for row in self.service._session(sid).get("messages", [])
                        if row.get("inputId") == args["inputId"] and row.get("inputOrigin") == "peer"), None)
        if message is None:
            return {"admitted": False, "reason": "The saved host-attributed input is unavailable."}
        return {"admitted": not reason, "reason": reason, "message": copy.deepcopy(message)}

    async def submit_steer(self, identity):
        async with self.service.lock:
            receipt = self.receipt(identity)
            reason = self.guard(receipt)
            if reason:
                receipt.update(delivery="suppressed", detail=reason)
                self.save(identity, receipt)
                self.service._publish()
                return receipt
            receipt["delivery"] = "submitting"
            self.save(identity, receipt)
            self.service.db.commit()
            target = copy.deepcopy(self.service._session(receipt["target"]["sessionId"]))
        args = {key: receipt.get(key) for key in ("inputId", "grantId", "taskId", "taskRevision", "targetGenerationId")}
        try:
            result = await self.service.runtime.collaboration_steer(
                target, args, lambda: self.guard(self.receipt(identity)), self.service.on_runtime_event)
            phase = "accepted" if result.get("accepted") else "suppressed"
        except BaseException as exc:
            result, phase = {"reason": "Steering outcome unknown; no replay."}, "unknown"
            if isinstance(exc, asyncio.CancelledError):
                receipt.update(delivery=phase, admission=result)
                self.save(identity, receipt)
                self.service.db.commit()
                raise
        async with self.service.lock:
            receipt = self.receipt(identity)
            # Applied/held events can precede the transport acknowledgement.
            receipt.update(delivery=receipt.get("delivery") if receipt.get("delivery") in {"applied", "held", "unknown"} else phase,
                           admission=result)
            self.save(identity, receipt)
            self.service._publish()
            return receipt

    def observe(self, session, kind, payload):
        """Host event linkage, never prose classification or semantic success."""
        if kind == "runtime.collaboration_checkpoint":
            active = session.get("collaborationGeneration") or {}
            if (payload.get("sessionId") == session["id"] and payload.get("rootSessionId") == session["id"]
                    and active.get("id") == payload.get("generation_id") and not active.get("terminal")):
                previous = {row["messageId"]: row for row in session.get("collaborationMessageAnchors", [])}
                for anchor in payload.get("messageAnchors", [])[:64]:
                    if anchor.get("generationId") == active["id"]:
                        previous[anchor["messageId"]] = anchor
                session["collaborationMessageAnchors"] = list(previous.values())[-128:]
            return
        if kind == "runtime.status" and payload.get("event") == "input.delivered":
            active = session.get("collaborationGeneration") or {}
            if active and not active.get("terminal") and payload.get("inputId"):
                active["inputIds"] = list(dict.fromkeys([*active.get("inputIds", []), payload["inputId"]]))[-128:]
        if kind == "runtime.steering" and payload.get("input_id"):
            try:
                receipt = self.receipt(payload["input_id"])
            except Exception:
                return
            if (receipt.get("mode") != "steer" or receipt.get("target", {}).get("sessionId") != session["id"]
                    or receipt.get("targetGenerationId") != payload.get("target_generation_id")):
                return
            phases = {"steering.applied": "applied", "steering.held": "held", "steering.unknown": "unknown"}
            if payload.get("event") in phases:
                receipt.update(delivery=phases[payload["event"]], steering=copy.deepcopy(payload))
                self.save(receipt["requestId"], receipt)
            if payload.get("event") == "steering.applied":
                active = session.get("collaborationGeneration") or {}
                if active.get("id") == receipt["targetGenerationId"] and not active.get("terminal"):
                    active["inputIds"] = list(dict.fromkeys([*active.get("inputIds", []), receipt["inputId"]]))
        if (kind != "runtime.generation" or payload.get("rootSessionId", session["id"]) != session["id"]
                or payload.get("sessionId", session["id"]) != session["id"]):
            return
        if payload.get("event") == "generation.started":
            session["collaborationGeneration"] = {"id": payload.get("generation_id"), "inputIds": [], "terminal": False}
            return
        active = session.get("collaborationGeneration") or {}
        if active.get("id") != payload.get("generation_id") or active.get("terminal"):
            return
        active["terminal"] = True
        # Only declarations staged in this actual generation can be sealed.
        for identity, encoded in self.service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.target.sessionId')=?
                AND json_extract(receipt,'$.response.status')='staged'""", (session["id"],)).fetchall():
            receipt = json.loads(encoded)
            response = receipt["response"]
            if response["generationId"] != payload.get("generation_id"):
                continue
            valid = (payload.get("event") == "generation.finished" and identity in payload.get("input_ids", [])
                     and not payload.get("active_job_ids") and bool(payload.get("text", "").strip())
                     and payload.get("disposition") == "manager_turn_finished"
                     and receipt.get("delivery") in {"accepted", "applied", "submitting"})
            linked = [self.resolve_message(session, mid) for mid in response.get("messageIds", [])]
            valid = valid and all(m and m.get("role") == "assistant" and m.get("generationId") == response["generationId"] for m in linked)
            anchor = payload.get("nativeTerminal") or {}
            native_id = session.get("runtimeSessionId") or session.get("nativeIdentity") or session["id"]
            from .automatic_history import display_identity
            valid = (valid and anchor.get("rootSessionId") == native_id
                     and anchor.get("generationId") == response["generationId"]
                     and type(anchor.get("nativeIndex")) is int and anchor["nativeIndex"] >= 0
                     and anchor.get("textDigest") == fingerprint(payload.get("text", ""))
                     and isinstance(anchor.get("nativeText"), str)
                     and anchor.get("messageId") == display_identity(session, anchor["nativeIndex"], "assistant", anchor["nativeText"]))
            if valid:
                # This ID names the exact checkpointed native row, not a prose
                # match or newly synthesized web assistant message.
                response.update(status="sealed", terminalMessageId=anchor["messageId"], nativeTerminal=anchor, sealedAt=time.time(),
                    qualified=(response["kind"] == "result" and response["outcome"] == "success"
                               and bool(response.get("references") or response.get("messageIds"))),
                    independentArtifactVerification=False)
            else:
                response.update(status="rejected", qualified=False, detail="Terminal evidence or exact linkage failed.")
            self.save(identity, receipt)
            if response.get("qualified") and receipt.get("subscription"):
                self.enqueue_continuation(receipt)

    def enqueue_continuation(self, request):
        """Turn one sealed wait into one durable queue claim, using existing drain."""
        wait = request["subscription"]
        identity = wait["continuationId"]
        if self.service.db.execute("SELECT 1 FROM commands WHERE id=?", (identity,)).fetchone():
            return
        sender = self.service._session(request["senderSessionId"])
        sender["historyManaged"] = False
        response = request["response"]
        envelope = {"senderSessionId": request["target"]["sessionId"], "recipientSessionId": sender["id"],
                    "grantId": wait["grantId"], "grantRevision": wait["grantRevision"],
                    "requestId": identity, "inputId": identity, "replyToRequestId": request["requestId"],
                    "mode": "queue", "purpose": "Resume the saved request-specific wait",
                    "references": [response["terminalMessageId"], *response.get("references", [])][:16]}
        text = "A recipient declared a successful result and its root generation terminated. Independently check the referenced artifact before using it.\n" + response["text"]
        message = self.service._message(sender, "user", text, "peer", inputId=identity, inputOrigin="peer",
                                        peerEnvelope=envelope)
        receipt = {"accepted": True, "commandAction": "coordination.send", "requestId": identity, "inputId": identity,
                   "messageId": message["id"], "senderSessionId": request["target"]["sessionId"],
                   "target": {"sessionId": sender["id"]}, "grantId": wait["grantId"], "grantRevision": wait["grantRevision"],
                   "mode": "queue", "delivery": "queued", "interruptionRevision": wait["interruptionRevision"],
                   "taskId": wait["taskId"], "taskRevision": wait["taskRevision"], "dependencyRequestId": request["requestId"]}
        wait["status"] = "claimed"
        self.insert(identity, fingerprint(["continuation", request["requestId"], wait]), receipt)
        self.save(request["requestId"], request)
        self.service._publish()  # Persist payload, seal and stable claim together.
        self.service._task(self.drain(sender["id"]))

    def start(self):
        """One recovery pass over known unsubmitted queues, not a scheduler."""
        targets = self.service.db.execute("""SELECT DISTINCT json_extract(receipt,'$.target.sessionId')
            FROM commands WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.delivery')='queued'""").fetchall()
        for (sid,) in targets:
            self.service._task(self.drain(sid))

    async def route(self, action, args, origin, command_id, caller):
        """One gate for equivalent old/new host entry points."""
        if action == "conversation.send" and args.get("grantId"):
            values = {key: value for key, value in args.items() if key != "preserveDraft"}
            if origin in {"ui", "user"}:
                values.setdefault("senderSessionId", self.service.state.get("selectedSessionId"))
            elif args.get("senderSessionId") not in {None, caller}:
                self.error("A model cannot impersonate a different peer sender.")
            return await self.service.dispatch("coordination.send", values, origin, command_id,
                include_state=False, caller_session_id=caller)
        if origin in {"ui", "user", "scheduler"}:
            return None
        if action == "session.create":
            authorized = CREATION.get()
            if authorized and authorized[:2] == (caller, fingerprint(args)):
                self.grant(self.source({}, origin, caller), authorized[2])
                return None
            self.error("A user must explicitly authorize durable task creation; use coordination.create with a current grant.")
        from .service import SESSION_ID_ACTIONS
        target_key = "sessionId" if "sessionId" in args else "id" if action in SESSION_ID_ACTIONS else None
        target = args.get(target_key) if target_key else None
        if action in {"conversation.send", "conversation.stop", "worker.spawn", "worker.message", "worker.steer", "worker.stop",
                      "conversation.retry", "runtime.control", "configuration.apply", "session.fork", "session.takeover",
                      "bundle.switch", "bundle.fork", "call.start", "message.edit"}:
            if not caller:
                self.error("A user must explicitly authorize a calling conversation.")
            target = target or caller
            if target_key:
                args[target_key] = target
            elif not action.startswith("session.") and action != "configuration.apply":
                args["sessionId"] = target
            if target == caller:
                return None
            if action == "conversation.send":
                self.error("A user must explicitly authorize a current collaboration grant.")
            self.error("A user must explicitly perform this peer mutation; collaboration does not grant stop, worker or settings authority.")
        passive = {"session.select", "session.inspect", "session.export", "session.shareRead", "session.shareList",
                   "session.pin",
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
            results = []
            response = receipt.get("response") or {}
            if response.get("qualified") and response.get("status") == "sealed":
                results.append({"messageId": response["terminalMessageId"], "sessionId": sid,
                                "inputId": args["requestId"], "declaration": response})
            return {"accepted": True, "result": {"receipt": receipt, "results": results,
                "qualified": bool(results), "qualificationSupported": True,
                "detail": "Typed agent declaration plus host terminal evidence; not independently verified correctness."}}
        if action == "coordination.send":
            await self.service.history.ensure_loaded(args["sessionId"])
        identity = command_id or str(uuid.uuid4())
        digest = fingerprint([action, args, origin, source["id"]])
        if action == "coordination.grant" and origin not in {"ui", "user"}:
            return await self.agent_grant(source, args, identity, digest)
        if action == "coordination.revoke" and origin not in {"ui", "user"}:
            self.error("Only a real human action can revoke collaboration grants.")
        async with self.service.lock:
            duplicate = self.duplicate(identity, digest)
            if duplicate:
                # Re-reading a prior receipt never dispatches work.
                return duplicate
            if action == "coordination.grant":
                participants = list(dict.fromkeys([source["id"], *args["participants"]]))
                self.validate_participants(source, participants)
                source["historyManaged"] = False
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
                current = self.grant(source, args["grantId"])
                request = self.receipt(args["requestId"])
                if (request.get("commandAction") != "coordination.send" or request.get("senderSessionId") != source["id"]
                        or args["sessionId"] != source["id"] or request.get("grantId") != current["id"]
                        or request.get("grantRevision") != current["revision"]):
                    self.error("A wait must bind this sender's exact current granted request.")
                if request.get("subscription"):
                    return {"accepted": True, "result": request["subscription"], "duplicate": True}
                task = source.get("task") or self.service.coordination.task(source["id"])
                wait = {"requestId": args["requestId"], "grantId": current["id"], "grantRevision": current["revision"],
                        "taskId": task.get("id"), "taskRevision": task.get("revision"),
                        "interruptionRevision": source.get("interruptionRevision", 0), "status": "waiting",
                        "continuationId": str(uuid.uuid5(uuid.NAMESPACE_URL, "collaboration-wait:" + source["id"] + ":" + args["requestId"])),
                        "supported": hasattr(self.service.runtime, "collaboration_input")}
                request["subscription"] = wait
                self.save(args["requestId"], request)
                receipt = {"accepted": wait["supported"], "commandAction": action, "result": wait}
                if (request.get("response") or {}).get("qualified"):
                    self.enqueue_continuation(request)
            elif action == "coordination.reply":
                if origin != "agent":
                    self.error("Response declarations require the actual recipient agent generation.")
                binding, active = self.active_binding(source)
                request = self.receipt(args["requestId"])
                if (request.get("commandAction") != "coordination.send" or request.get("target", {}).get("sessionId") != source["id"]
                        or args["requestId"] not in active.get("inputIds", [])
                        or args["requestId"] not in {row.get("inputId") for row in binding.get("_inputBindings", [])}
                        or request.get("delivery") not in {"accepted", "applied", "submitting"}):
                    self.error("Declare only the exact delivered request in your current root generation.")
                self.grant(source, request["grantId"])
                if request.get("response"):
                    self.error("This request already has a response declaration; retain its exact evidence.", 409)
                response = {**copy.deepcopy(args), "generationId": active["id"], "recipientSessionId": source["id"],
                            "status": "staged", "qualified": False, "declaredAt": time.time()}
                request["response"] = response
                self.save(args["requestId"], request)
                receipt = {"accepted": True, "commandAction": action, "result": response}
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
                    active = target.get("collaborationGeneration") or {}
                    if not hasattr(self.service.runtime, "collaboration_steer") or not active.get("id") or active.get("terminal"):
                        return {"accepted": False, "result": {"supported": False, "effect": "none",
                            "reason": "No supported active generation-anchored recipient adapter."}}
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
                target["historyManaged"] = False
                message = self.service._message(target, "user", args["text"], "peer", inputId=identity,
                                               inputOrigin="peer", peerEnvelope=envelope)
                receipt = {"accepted": True, "commandAction": "coordination.send", "requestId": identity,
                    "inputId": identity, "messageId": message["id"], "senderSessionId": source["id"],
                    "target": {"sessionId": target["id"]}, "grantId": grant["id"], "grantRevision": grant["revision"],
                    "mode": mode, "delivery": "notified" if mode == "notify" else "queued",
                    "interruptionRevision": target.get("interruptionRevision", 0),
                    "taskId": task.get("id"), "taskRevision": task.get("revision")}
                if mode == "steer":
                    receipt.update(delivery="pending_steer", targetGenerationId=active["id"])
            if receipt is not None:
                self.insert(identity, digest, receipt)
                self.service._publish()
        if action == "coordination.create":
            return await self.create(source, grant, args, origin, identity, digest)
        if receipt.get("delivery") == "pending_steer":
            return await self.submit_steer(identity)
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
        # Effective plans can contain locally configured literal credentials.
        # New roots resolve credentials from the normal host environment, never
        # a copied credential value or opaque native state.
        from .runtime_controls import public_config, REDACTED
        def without_credentials(value):
            if isinstance(value, dict):
                return {key: without_credentials(item) for key, item in value.items()
                        if public_config({key: item})[key] != REDACTED}
            if isinstance(value, list):
                return [without_credentials(item) for item in value]
            return value
        config = without_credentials(config)
        summary["configurationHash"] = fingerprint(config)
        if len(grant["participants"]) >= 8:
            self.error("This grant's participant limit is reached.", 409)
        new_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "collaborative-task:" + source["id"] + ":" + identity))
        metadata = {"creatorSessionId": source["id"], "requestId": identity,
            "grantId": grant["id"], "configurationHash": summary["configurationHash"],
            "outputNamespace": "working-files/tasks/" + new_id, "brief": args["text"], "references": args.get("references", [])}
        async with self.service.lock:
            duplicate = self.duplicate(identity, digest)
            if duplicate:
                return duplicate
            self.insert(identity, digest, {"accepted": True, "commandAction": "coordination.create",
                "sessionId": new_id, "delivery": "unknown", "result": metadata,
                "initialInputId": identity + ":brief", "detail": "Creation admitted; inspect this exact chat before any further effect."})
            self.service.db.commit()
        values = {"id": new_id, "title": args["title"], "workspace": source["workspace"],
                  "bundle": config["bundle"], "select": False}
        token = CREATION.set((source["id"], fingerprint(values), grant["id"], config, metadata))
        try:
            await self.service.dispatch("session.create", values, origin, identity + ":create",
                                        include_state=False, caller_session_id=source["id"])
        finally:
            CREATION.reset(token)
        async with self.service.lock:
            # Revocation while creation awaited leaves an idle preserved chat,
            # never an implicitly restarted task.
            target = self.service._session(new_id)
            try:
                current = self.grant(source, grant["id"])
            except Exception:
                receipt = {"accepted": True, "commandAction": "coordination.create", "sessionId": new_id,
                    "delivery": "created_brief_suppressed", "result": metadata,
                    "detail": "Chat and configuration retained; grant revoked before initial delivery."}
                self.save(identity, receipt)
                self.service._publish()
                return receipt
            if new_id not in current["participants"]:
                current["participants"].append(new_id)
                saved = self.receipt(current["id"])
                saved["result"] = current
                self.save(current["id"], saved)
            receipt = {"accepted": True, "commandAction": "coordination.create",
                       "sessionId": new_id, "result": target["collaboration"],
                       "delivery": "created_initial_pending", "initialInputId": identity + ":brief",
                       "detail": "Root retained. Initial delivery is separate and has not been confirmed."}
            self.save(identity, receipt)
            self.service._publish()
        # The durable brief is ordinary attributed input, not copied history.
        initial = await self.dispatch("coordination.send", {"sessionId": new_id, "senderSessionId": source["id"],
            "grantId": grant["id"], "text": args["text"], "references": args.get("references", []),
            "mode": "queue" if "queue" in grant["modes"] else "notify"}, origin, identity + ":brief", source["id"])
        async with self.service.lock:
            receipt.update(delivery="created", initialDelivery=initial.get("delivery"),
                           detail="Root retained; initial-input receipt is linked separately.")
            self.save(identity, receipt)
            self.service.db.commit()
        return receipt