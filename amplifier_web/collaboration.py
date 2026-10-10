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
PROTOCOL = 2


def definitions(schema, string):
    sid = {"sessionId": string(200)}
    references = {"type": "array", "maxItems": 16, "items": string(2000)}
    message = {**sid, "senderSessionId": string(200), "grantId": string(200),
               "text": {**string(16000), "minLength": 1},
               "mode": {"enum": ["notify", "queue", "steer"]},
               "replyToRequestId": string(200), "references": references}
    return {
        "coordination.grant": ("Retired. Historical grants remain readable; fresh collaboration uses authenticated root generations.", schema({
            **sid, "participants": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True, "items": string(200)},
            "sourceMessageId": string(200),
            "purpose": {**string(4000), "minLength": 1},
            "modes": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"enum": ["notify", "queue", "steer"]}},
            "idleStart": {"type": "boolean"}, "allowCreate": {"type": "boolean"},
        }, ["sessionId", "participants", "purpose", "modes"])),
        "coordination.revoke": ("Retired. Legacy work cannot resume; use ordinary human Stop for current task work.", schema({**sid, "grantId": string(200)}, ["sessionId", "grantId"])),
        "coordination.context": ("Read bounded related request receipts and historical grants without loading peer transcripts.", schema(sid, ["sessionId"])),
        "coordination.decide": ("Retired. Legacy pending proposals cannot authorize new work.", schema({
            **sid, "proposalId": string(200), "decision": {"enum": ["allow", "deny"]},
        }, ["sessionId", "proposalId", "decision"])),
        "coordination.send": ("Commit attributed same-workspace task input and return its receipt promptly. Notify never wakes; queue preparation continues under service ownership. Read results, never replay uncertain input.", schema(message, ["sessionId", "text"])),
        "coordination.create": ("Create an ordinary durable root task chat inheriting source configuration. Returns a committed receipt before runtime preparation; does not select it or request separate approval.", schema({
            "senderSessionId": string(200), "grantId": string(200), "title": {**string(100), "minLength": 1},
            "text": {**string(16000), "minLength": 1}, "references": references,
        }, ["title", "text"])),
        "coordination.read": ("Read a bounded window from one existing native conversation without selecting or warming it.", schema({
            **sid, "messageId": string(200), "offset": {"type": "integer", "minimum": 0},
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
        }, ["sessionId", "requestId"])),
    }


class Collaboration:
    def __init__(self, service):
        self.service = service
        self.draining = set()
        for session in service.state["sessions"]:
            active = session.get("collaborationGeneration")
            if active and not active.get("terminal"):
                active.update(terminal=True, detail="Host restarted; no live generation authority.")
            for approval in session.get("approvals", []):
                if approval.get("id", "").startswith("collaboration:") and approval.get("status") == "pending":
                    approval["status"] = "retired"
        # Index receipts, not conversations or transcripts.
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_target ON commands(
            json_extract(receipt,'$.commandAction'), json_extract(receipt,'$.target.sessionId'))""")
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_sender ON commands(
            json_extract(receipt,'$.commandAction'), json_extract(receipt,'$.senderSessionId'))""")
        for identity, encoded in service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.create'
                AND json_extract(receipt,'$.delivery') IN ('creation_pending','created_initial_pending')""").fetchall():
            value = json.loads(encoded)
            value.update(delivery="unknown", detail="Host restarted during creation; retained effects are not replayed.")
            self.save(identity, value)
        for identity, encoded in service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.delivery')='submitting'""").fetchall():
            value = json.loads(encoded)
            value.update(delivery="unknown", detail="Host restarted during admission; no replay.")
            self.save(identity, value)
        for identity, encoded in service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.response.status')='staged'""").fetchall():
            value = json.loads(encoded)
            value["response"].update(status="unknown", qualified=False,
                detail="Host restarted before terminal proof; declaration retained, no replay.")
            if value.get("subscription"):
                value["subscription"].update(status="needs_attention", detail=value["response"]["detail"])
            self.save(identity, value)
        # Suppress old automatic work before start() or runtime events can drain
        # it. Keep its original evidence; a new protocol is not retroactive consent.
        for identity, encoded in service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction') IN
                    ('coordination.send','coordination.grant.pending')""").fetchall():
            value = json.loads(encoded)
            if value.get("protocol") == PROTOCOL:
                continue
            changed = False
            if value.get("delivery") in {"queued", "pending_steer", "awaiting_approval"}:
                value["legacyDelivery"] = value["delivery"]
                value.update(delivery="suppressed", detail="Legacy coordination is read-only; no automatic admission after upgrade.")
                changed = True
            wait = value.get("subscription")
            if wait and wait.get("status") == "waiting":
                wait.update(status="suppressed", detail="Legacy wait retained without continuation.")
                changed = True
            if changed:
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
            self.error("A child cannot borrow its root's collaboration authority.")
        if source.get("sessionKind", "root") != "root":
            self.error("Collaboration belongs to ordinary root conversations.")
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
        target = self.service._session(args["sessionId"])
        if target["workspace"] != source["workspace"]:
            self.error("The target is outside this caller's workspace.")
        if target.get("sessionKind", "root") != "root":
            self.error("Peer worker admission is unsupported; target its authorized root instead.", 409)
        return target

    def lineage(self, source, origin):
        if origin not in {"ui", "user"}:
            binding, active = self.active_binding(source)
            return {"sourceGenerationId": active["id"],
                    "sourceInterruptionRevision": source.get("interruptionRevision", 0),
                    "sourceInputIds": [row.get("inputId") for row in binding.get("_inputBindings", [])
                                       if row.get("inputId") in active.get("inputIds", [])]}
        return {"sourceGenerationId": None, "sourceInputIds": [],
                "sourceInterruptionRevision": source.get("interruptionRevision", 0)}

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
        proposals = [json.loads(row[0]) for row in self.service.db.execute("""SELECT receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.grant.pending'
            AND EXISTS (SELECT 1 FROM json_each(json_extract(receipt,'$.result.participants')) WHERE value=?)
            ORDER BY rowid DESC LIMIT 32""", (sid,)).fetchall()]
        return {"grants": [row for row in grants if sid in row["participants"]], "proposals": proposals,
                "workspace": self.service._session(sid)["workspace"],
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
            self.error("Collaboration requires the current transport-bound root generation.")
        return binding, active

    def resolve_message(self, session, identity):
        def correlated(row):
            anchor = next((item for item in session.get("collaborationMessageAnchors", [])
                           if item["messageId"] == identity and item["nativeIndex"] == row.get("nativeIndex")), None)
            if (row.get("role") == "assistant" and anchor
                    and row.get("generationId") in {None, anchor["generationId"]}):
                return {**row, "generationId": anchor["generationId"]}
            return row
        message = next((row for row in session.get("messages", []) if row["id"] == identity), None)
        if message or not session.get("nativeProject"):
            return correlated(message) if message else None
        from .automatic_history import read_transcript
        rows = read_transcript(session, limit=None)["messages"]
        native = next((row for row in rows if row["id"] == identity), None)
        if native is None:
            return None
        # User authority comes only from retained host input provenance, never
        # from native role=user or equal prose. Index anchors resolve assistants.
        candidates = [row for row in session.get("messages", []) if (
            native.get("nativeInputId") and row.get("inputId") == native["nativeInputId"]
            or type(native.get("nativeIndex")) is int and row.get("nativeIndex") == native["nativeIndex"])]
        if candidates:
            if len(candidates) != 1:
                self.error("The exact saved message cannot be reconciled unambiguously.", 409)
            retained = candidates[0]
            anchor = next((item for item in session.get("collaborationMessageAnchors", [])
                           if item["messageId"] == identity and item["nativeIndex"] == native.get("nativeIndex")), None)
            if (retained.get("role") != native.get("role")
                    or retained.get("role") == "assistant" and (
                        retained.get("text") != native.get("text")
                        or anchor and retained.get("generationId") not in {None, anchor["generationId"]})):
                self.error("The retained message conflicts with its exact native anchor.", 409)
            # Return linkage only after resolving the real canonical row. Keep
            # the original web identity and history untouched; never alias by prose.
            return correlated({**retained, "nativeMessageId": native["id"],
                               "nativeIndex": native["nativeIndex"]})
        anchor = next((row for row in session.get("collaborationMessageAnchors", [])
                       if row["messageId"] == identity and row["nativeIndex"] == native.get("nativeIndex")), None)
        return {**native, "generationId": anchor["generationId"]} if anchor else None

    def publish_receipt(self, receipt):
        ids = {receipt.get('senderSessionId'), receipt.get('sourceSessionId'),
               receipt.get('sessionId'), (receipt.get('target') or {}).get('sessionId')}
        ids.discard(None)
        self.service._publish_changes(sessions=ids)

    async def decide(self, args, origin):
        self.error("Coordination approval APIs are retired. Historical decisions are read-only.", 410)

    def require_continuation(self):
        if not hasattr(self.service.runtime, "collaboration_input"):
            self.error("This runtime lacks guarded idle peer admission; no task or wait was created.", 409)

    def can_steer(self, target):
        active = target.get("collaborationGeneration") or {}
        return bool(hasattr(self.service.runtime, "collaboration_steer") and active.get("id") and not active.get("terminal"))

    def guard(self, receipt):
        try:
            if receipt.get("protocol") != PROTOCOL:
                return "Legacy coordination is read-only; automatic admission is suppressed."
            from .updates import work_paused
            if work_paused(self.service.state):
                return "Host work is paused for an update; peer admission is suppressed."
            source = self.service._session(receipt["senderSessionId"])
            target = self.authorize(source, {"sessionId": receipt["target"]["sessionId"]})
            if source["workspace"] != receipt.get("workspace"):
                return "The request workspace changed."
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
                        self.publish_receipt(receipt)
                        continue
                    # Durable claim precedes any runtime effect. Unknown never
                    # returns to queued, including cancellation and restart.
                    receipt["delivery"] = "submitting"
                    self.save(identity, receipt)
                    self.service.db.commit()
                    snapshot = copy.deepcopy(target)
                arguments = {"inputId": identity,
                             "taskId": receipt.get("taskId"), "taskRevision": receipt.get("taskRevision")}
                try:
                    result = await self.service.runtime.collaboration_input(
                        snapshot, arguments, lambda: self.guard(self.receipt(identity)), self.service.on_runtime_event)
                    phase = "accepted" if result.get("accepted") else (
                        "not_sent" if result.get("delivery") == "not_sent" else "suppressed")
                except BaseException as exc:
                    result, phase = {"reason": "Admission outcome unknown; no replay."}, "unknown"
                    if isinstance(exc, asyncio.CancelledError):
                        from .runtime import RuntimePreparationCancelled
                        if isinstance(exc, RuntimePreparationCancelled):
                            result = {"accepted": False, "delivery": "not_sent",
                                      "reason": "Preparation cancelled before input admission."}
                            phase = "not_sent"
                        receipt.update(delivery=phase, detail=result["reason"], admission=result)
                        self.save(identity, receipt)
                        self.service.db.commit()
                        raise
                async with self.service.lock:
                    receipt = self.receipt(identity)
                    receipt.update(delivery=phase, admission=result)
                    self.save(identity, receipt)
                    self.publish_receipt(receipt)
                # Native generation events own status. Do not send a second
                # input merely because its acknowledgement arrived first.
                if phase in {"accepted", "unknown"}:
                    break
        finally:
            self.draining.discard(sid)

    def admission(self, sid, args):
        receipt = self.receipt(args["inputId"])
        if (receipt.get("commandAction") != "coordination.send" or receipt.get("delivery") != "submitting"
                or receipt.get("protocol") != PROTOCOL or receipt["target"]["sessionId"] != sid
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
                self.publish_receipt(receipt)
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
            self.publish_receipt(receipt)
            return receipt

    def observe(self, session, kind, payload):
        """Host event linkage, never prose classification or semantic success."""
        changed = set()
        if kind == "runtime.collaboration_checkpoint":
            active = session.get("collaborationGeneration") or {}
            if (payload.get("sessionId") == session["id"] and payload.get("rootSessionId") == session["id"]
                    and active.get("id") == payload.get("generation_id") and not active.get("terminal")):
                previous = {row["messageId"]: row for row in session.get("collaborationMessageAnchors", [])}
                for anchor in payload.get("messageAnchors", [])[:64]:
                    if anchor.get("generationId") == active["id"]:
                        previous[anchor["messageId"]] = anchor
                session["collaborationMessageAnchors"] = list(previous.values())[-128:]
            return changed
        if kind == "runtime.status" and payload.get("event") == "input.delivered":
            active = session.get("collaborationGeneration") or {}
            if active and not active.get("terminal") and payload.get("inputId"):
                active["inputIds"] = list(dict.fromkeys([*active.get("inputIds", []), payload["inputId"]]))[-128:]
        if kind == "runtime.steering" and payload.get("input_id"):
            try:
                receipt = self.receipt(payload["input_id"])
            except Exception:
                return changed
            if (receipt.get("mode") != "steer" or receipt.get("target", {}).get("sessionId") != session["id"]
                    or receipt.get("targetGenerationId") != payload.get("target_generation_id")):
                return changed
            phases = {"steering.applied": "applied", "steering.held": "held", "steering.unknown": "unknown"}
            if payload.get("event") in phases:
                receipt.update(delivery=phases[payload["event"]], steering=copy.deepcopy(payload))
                self.save(receipt["requestId"], receipt)
                changed.update({receipt["senderSessionId"], session["id"]})
            if payload.get("event") == "steering.applied":
                active = session.get("collaborationGeneration") or {}
                if active.get("id") == receipt["targetGenerationId"] and not active.get("terminal"):
                    active["inputIds"] = list(dict.fromkeys([*active.get("inputIds", []), receipt["inputId"]]))
        if (kind != "runtime.generation" or payload.get("rootSessionId", session["id"]) != session["id"]
                or payload.get("sessionId", session["id"]) != session["id"]):
            return changed
        if payload.get("event") == "generation.started":
            session["collaborationGeneration"] = {"id": payload.get("generation_id"), "inputIds": [], "terminal": False}
            return changed
        active = session.get("collaborationGeneration") or {}
        if active.get("id") != payload.get("generation_id") or active.get("terminal"):
            return changed
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
            from .service import AppError
            try:
                linked = [self.resolve_message(session, mid) for mid in response.get("messageIds", [])]
            except AppError:
                # Explicit reads still fail loudly on conflicting identities.
                # In the event reader this is negative linkage evidence, not a
                # worker communication failure; reject via the normal path.
                linked = [None]
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
            changed.update({receipt["senderSessionId"], session["id"]})
            if response.get("qualified") and receipt.get("subscription"):
                self.enqueue_continuation(receipt)
        return changed

    def enqueue_continuation(self, request):
        """Turn one sealed wait into one durable queue claim, using existing drain."""
        wait = request["subscription"]
        if request.get("protocol") != PROTOCOL or wait.get("status") != "waiting":
            return
        identity = wait["continuationId"]
        if self.service.db.execute("SELECT 1 FROM commands WHERE id=?", (identity,)).fetchone():
            return
        sender = self.service._session(request["senderSessionId"])
        sender["historyManaged"] = False
        response = request["response"]
        envelope = {"senderSessionId": request["target"]["sessionId"], "recipientSessionId": sender["id"],
                    "requestId": identity, "inputId": identity, "replyToRequestId": request["requestId"],
                    "mode": "queue", "purpose": "Resume the saved request-specific wait",
                    "references": [response["terminalMessageId"], *response.get("references", [])][:16]}
        text = "A recipient declared a successful result and its root generation terminated. Independently check the referenced artifact before using it.\n" + response["text"]
        message = self.service._message(sender, "user", text, "peer", inputId=identity, inputOrigin="peer",
                                        peerEnvelope=envelope)
        receipt = {"accepted": True, "commandAction": "coordination.send", "requestId": identity, "inputId": identity,
                   "messageId": message["id"], "senderSessionId": request["target"]["sessionId"],
                   "target": {"sessionId": sender["id"]}, "protocol": PROTOCOL, "workspace": request["workspace"],
                   "mode": "queue", "delivery": "queued", "interruptionRevision": wait["interruptionRevision"],
                   "taskId": wait["taskId"], "taskRevision": wait["taskRevision"], "dependencyRequestId": request["requestId"],
                   "origin": "system", "displayBinding": fingerprint([envelope, text])}
        wait["status"] = "claimed"
        self.insert(identity, fingerprint(["continuation", request["requestId"], wait]), receipt)
        self.save(request["requestId"], request)
        self.service._publish_changes(sessions={sender['id'], request['target']['sessionId']})  # Payload and claim commit together.
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
        if action == "conversation.send" and (args.get("grantId") or (
                origin not in {"ui", "user", "scheduler"} and args.get("sessionId") != caller)):
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
                self.source({}, origin, caller)
                return None
            self.error("Use coordination.create for host-owned task inheritance; raw agent session.create is not authorized.")
        from .service import SESSION_ID_ACTIONS, ACTION_POLICIES
        policy = ACTION_POLICIES.get(action, {})
        access = policy.get("access")
        if access in {"peer_read", "presentation", "domain_authoritative"}:
            return None
        target_key = policy.get("target") or ("sessionId" if "sessionId" in args else "id" if action in SESSION_ID_ACTIONS else None)
        target = args.get(target_key) if target_key else None
        if access == "naming":
            if "automatic" in args and not args.get("regenerate"):
                return None
            access = "caller_controlled"
        if access == "caller_controlled":
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
        if target and target != caller and not action.startswith("coordination."):
            self.error("A user must explicitly perform peer mutations outside a collaboration send grant.")
        return None

    async def dispatch(self, action, args, origin, command_id, caller):
        if action in {"coordination.grant", "coordination.decide", "coordination.revoke"}:
            self.error("Coordination approval APIs are retired. Historical records are read-only.", 410)
        source = self.source(args, origin, caller) if action not in {"coordination.result", "coordination.read"} else None
        if action == "coordination.read":
            if args.get("messageId"):
                target = self.service._session(args["sessionId"])
                if origin not in {"ui", "user"}:
                    self.authorize(self.source({}, origin, caller), args)
                await self.service.history.ensure_loaded(target["id"])
                message = self.resolve_message(target, args["messageId"])
                if not message:
                    self.error("The exact original message is unavailable.", 404)
                limit = args.get("textLimit", 4000)
                text = message.get("text", "")
                return {"accepted": True, "result": {"message": {
                    "id": message["id"], "sessionId": target["id"], "role": message.get("role"),
                    **{key: message[key] for key in ("nativeMessageId", "nativeIndex", "generationId")
                       if message.get(key) is not None},
                    "text": text[:limit], "truncated": len(text) > limit}}}
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
            reader = self.source({}, origin, caller) if origin not in {"ui", "user"} else None
            receipt = self.receipt(args["requestId"])
            if receipt.get("commandAction") not in {"coordination.send", "coordination.create"}:
                self.error("This receipt is not a peer request.", 409)
            sid = receipt.get("target", {}).get("sessionId") or receipt["sessionId"]
            if reader:
                sender = receipt.get("senderSessionId") or receipt.get("result", {}).get("creatorSessionId")
                workspace = receipt.get("workspace") or self.service._session(sender)["workspace"]
                if (reader["id"] not in {sender, sid}
                        or reader["workspace"] != workspace
                        or receipt.get("commandAction") == "coordination.send"
                        and self.service._session(sid)["workspace"] != reader["workspace"]):
                    self.error("This result is outside the caller's request and workspace scope.")
            target = next((row for row in self.service.state["sessions"] if row["id"] == sid), None)
            if target:
                await self.service.history.ensure_loaded(sid)
            results = []
            response = receipt.get("response") or {}
            if response.get("qualified") and response.get("status") == "sealed":
                results.append({"messageId": response["terminalMessageId"], "sessionId": sid,
                                "inputId": args["requestId"], "declaration": response})
            return {"accepted": True, "result": {"receipt": receipt, "results": results,
                "preparation": copy.deepcopy((target or {}).get("activity")),
                "qualified": bool(results), "qualificationSupported": True,
                "detail": "Typed agent declaration plus host terminal evidence; not independently verified correctness."}}
        if action == "coordination.send":
            await self.service.history.ensure_loaded(args["sessionId"])
        identity = command_id or str(uuid.uuid4())
        digest = fingerprint([action, args, origin, source["id"]])
        async with self.service.lock:
            duplicate = self.duplicate(identity, digest)
            if duplicate:
                # Re-reading a prior receipt never dispatches work.
                return duplicate
            if args.get("grantId"):
                self.error("Legacy grant-bearing writes are unsupported. Read the original receipt; do not replay it as new work.", 410)
            creation = CREATION.get()
            if (creation and action == "coordination.send" and creation[0] == source["id"]
                    and identity == creation[4]["requestId"] + ":brief"
                    and args["sessionId"] == str(uuid.uuid5(uuid.NAMESPACE_URL,
                        "collaborative-task:" + source["id"] + ":" + creation[4]["requestId"]))):
                lineage = {name: creation[4][name] for name in
                           ("sourceGenerationId", "sourceInputIds", "sourceInterruptionRevision")}
                if source.get("interruptionRevision", 0) != lineage["sourceInterruptionRevision"]:
                    self.error("Source was stopped before initial brief admission.", 409)
            else:
                lineage = self.lineage(source, origin)
            if action == "coordination.subscribe":
                self.require_continuation()
                request = self.receipt(args["requestId"])
                if (request.get("commandAction") != "coordination.send" or request.get("senderSessionId") != source["id"]
                        or args["sessionId"] != source["id"] or request.get("protocol") != PROTOCOL
                        or request.get("workspace") != source["workspace"]):
                    self.error("A wait must bind this sender's exact current request.")
                if request.get("subscription"):
                    return {"accepted": True, "result": request["subscription"], "duplicate": True}
                task = source.get("task") or self.service.coordination.task(source["id"])
                wait = {"requestId": args["requestId"],
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
                if request.get("protocol") != PROTOCOL:
                    self.error("Legacy requests cannot receive fresh response declarations.", 410)
                if request.get("response"):
                    self.error("This request already has a response declaration; retain its exact evidence.", 409)
                response = {**copy.deepcopy(args), "generationId": active["id"], "recipientSessionId": source["id"],
                            "status": "staged", "qualified": False, "declaredAt": time.time()}
                request["response"] = response
                self.save(args["requestId"], request)
                receipt = {"accepted": True, "commandAction": action, "result": response}
            elif action == "coordination.create":
                # Creation is handled outside the service lock below.
                self.require_continuation()
                receipt = None
            else:
                target = self.authorize(source, args)
                mode = args.get("mode", "queue")
                if mode == "steer":
                    active = target.get("collaborationGeneration") or {}
                    if not self.can_steer(target):
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
                            or previous["senderSessionId"] != target["id"] or previous.get("protocol") != PROTOCOL):
                        self.error("Reply linkage must identify the exact incoming request.")
                task = target.get("task") or self.service.coordination.task(target["id"])
                envelope = {"senderSessionId": source["id"], "senderRuntimeSessionId": source.get("runtimeSessionId") or source["id"],
                    "recipientSessionId": target["id"], **lineage,
                    "requestId": identity, "inputId": identity,
                    "mode": mode, "purpose": "Scoped collaboration task input", "references": args.get("references", []),
                    "replyToRequestId": args.get("replyToRequestId")}
                if target.get("collaboration"):
                    envelope["task"] = {key: target["collaboration"][key] for key in
                        ("creatorSessionId", "requestId", "outputNamespace", "configurationHash")}
                target["historyManaged"] = False
                message = self.service._message(target, "user", args["text"], "peer", inputId=identity,
                                               inputOrigin="peer", peerEnvelope=envelope)
                receipt = {"accepted": True, "commandAction": "coordination.send", "requestId": identity,
                    "inputId": identity, "messageId": message["id"], "senderSessionId": source["id"],
                    "target": {"sessionId": target["id"]}, "protocol": PROTOCOL, "workspace": source["workspace"], **lineage,
                    "mode": mode, "delivery": "notified" if mode == "notify" else "queued",
                    "interruptionRevision": target.get("interruptionRevision", 0),
                    "taskId": task.get("id"), "taskRevision": task.get("revision"),
                    # Dispatcher provenance is host-owned. Never copy actor
                    # claims from args, message prose, or the peer envelope.
                    "origin": origin if origin in {"agent", "ui", "user"} else None,
                    "displayBinding": fingerprint([envelope, args["text"]])}
                if mode == "steer":
                    receipt.update(delivery="pending_steer", targetGenerationId=active["id"])
            if receipt is not None:
                self.insert(identity, digest, receipt)
                self.publish_receipt(receipt)
        if action == "coordination.create":
            return await self.create(source, args, origin, identity, digest, lineage)
        if receipt.get("delivery") == "pending_steer":
            return await self.submit_steer(identity)
        if receipt.get("delivery") == "queued":
            # The committed receipt owns the operation, not the request socket.
            self.service._task(self.drain(receipt["target"]["sessionId"]))
        return receipt

    async def create(self, source, args, origin, identity, digest, lineage):
        from .session_creation import template
        try:
            config, summary = template(self.service, source)
        except ValueError as exc:
            self.error(str(exc), 409)
        # Rebind the same host-composed instance/source when the child prepares;
        # never copy expanded credentials or guess a provider-family account.
        from .provider_environment import credential_bindings
        config = credential_bindings(config)
        summary["configurationHash"] = fingerprint(config)
        new_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "collaborative-task:" + source["id"] + ":" + identity))
        metadata = {"creatorSessionId": source["id"], "creatorTitle": source["title"], "requestId": identity,
            "configurationHash": summary["configurationHash"], **lineage,
            "outputNamespace": "working-files/tasks/" + new_id, "brief": args["text"], "references": args.get("references", [])}
        async with self.service.lock:
            duplicate = self.duplicate(identity, digest)
            if duplicate:
                return duplicate
            # A root occupies one slot while executable work is outstanding.
            # Idle history and uncertain historical receipts are not work; their
            # evidence stays unknown and is never resolved/replayed by this cap.
            roots = [row for row in self.service.state["sessions"]
                     if (row.get("collaboration") or {}).get("creatorSessionId") == source["id"]]
            outstanding = set()
            for row in roots:
                active = row.get("collaborationGeneration") or {}
                task = row.get("task") or self.service.coordination.task(row["id"]) or {}
                if (row.get("status") in {"starting", "working", "running", "busy", "stopping"}
                        or active.get("id") and not active.get("terminal")
                        or row["id"] in getattr(self.service.runtime, "_preparations", {})
                        or task.get("id") and task.get("status") in {"active", "running", "working"}):
                    outstanding.add(row["id"])
            pending_inputs = {row[0] for row in self.service.db.execute("""SELECT DISTINCT
                json_extract(receipt,'$.target.sessionId') FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.delivery') IN ('queued','submitting','pending_steer')""").fetchall()}
            outstanding.update(row["id"] for row in roots if row["id"] in pending_inputs)
            pending = self.service.db.execute("""SELECT receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.create'
                AND json_extract(receipt,'$.senderSessionId')=?
                AND json_extract(receipt,'$.protocol')=?
                AND json_extract(receipt,'$.delivery') IN ('creation_pending','created_initial_pending')""",
                (source["id"], PROTOCOL)).fetchall()
            outstanding.update(json.loads(row[0])["sessionId"] for row in pending)
            if len(outstanding) >= 8:
                self.error("At most eight outstanding task chats per source root.", 409)
            receipt = {"accepted": True, "commandAction": "coordination.create",
                "requestId": identity, "senderSessionId": source["id"], "workspace": source["workspace"], "protocol": PROTOCOL,
                "sessionId": new_id, "delivery": "creation_pending", "result": metadata,
                "initialInputId": identity + ":brief", "detail": "Creation committed; preparation is owned by the service."}
            self.insert(identity, digest, receipt)
            self.service.db.commit()
        self.service._task(self.finish_create(source, config, args, origin, identity, new_id, metadata))
        return receipt

    async def finish_create(self, source, config, args, origin, identity, new_id, metadata):
        """Service-owned once-only completion. Reads never restart this task."""
        try:
            await self._finish_create(source, config, args, origin, identity, new_id, metadata)
        except BaseException as exc:
            receipt = self.receipt(identity)
            receipt.update(delivery="unknown", detail="Creation did not confirm all effects; inspect retained root and brief. No replay.",
                           errorType=type(exc).__name__)
            self.save(identity, receipt)
            self.publish_receipt(receipt)
            if isinstance(exc, asyncio.CancelledError):
                raise

    async def _finish_create(self, source, config, args, origin, identity, new_id, metadata):
        values = {"id": new_id, "title": args["title"], "workspace": source["workspace"],
                  "bundle": config["bundle"], "select": False}
        token = CREATION.set((source["id"], fingerprint(values), None, config, metadata))
        try:
            await self.service.dispatch("session.create", values, origin, identity + ":create",
                                        include_state=False, caller_session_id=source["id"])
        finally:
            CREATION.reset(token)
        async with self.service.lock:
            target = self.service._session(new_id)
            receipt = {"accepted": True, "commandAction": "coordination.create",
                       "requestId": identity, "senderSessionId": source["id"], "workspace": source["workspace"], "protocol": PROTOCOL,
                       "sessionId": new_id, "result": target["collaboration"],
                       "delivery": "created_initial_pending", "initialInputId": identity + ":brief",
                       "detail": "Root retained. Initial delivery is separate and has not been confirmed."}
            self.save(identity, receipt)
            self.publish_receipt(receipt)
        # The durable brief is ordinary attributed input, not copied history.
        token = CREATION.set((source["id"], fingerprint(values), None, config, metadata))
        try:
            initial = await self.dispatch("coordination.send", {"sessionId": new_id, "senderSessionId": source["id"],
                "text": args["text"], "references": args.get("references", []),
                "mode": "queue"}, origin, identity + ":brief", source["id"])
        finally:
            CREATION.reset(token)
        async with self.service.lock:
            receipt.update(delivery="created", initialDelivery=initial.get("delivery"),
                           detail="Root retained; initial-input receipt is linked separately.")
            self.save(identity, receipt)
            self.service.db.commit()
        return receipt