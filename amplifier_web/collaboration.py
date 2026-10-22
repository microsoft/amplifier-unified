"""Scoped peer authority and receipts in existing session/command records.

No transcript or scheduler is owned here. Native adapters must qualify final
results before dependency continuation can be enabled.
"""
from __future__ import annotations

import asyncio
import copy
from contextlib import AsyncExitStack
from contextvars import ContextVar
import json
import time
import uuid

from amplifier_operations.coordination import fingerprint
from .task_binding import continuation_epoch, matches as task_matches

PRINCIPAL = ContextVar("collaboration_principal", default=None)
BINDING = ContextVar("collaboration_binding", default=None)
CREATION = ContextVar("collaboration_creation", default=None)
BUSY = {"working", "starting", "running", "busy", "stopping", "ready"}
PROTOCOL = 2
MAX_PENDING_INPUTS = 64
MAX_PAIR_MESSAGES_PER_MINUTE = 32
MAX_SENDER_INPUTS = 16
MAX_ASSIGNMENT_INPUTS = 32
MAX_RESPONSE_REVISIONS = 32
MAX_CAUSAL_HOPS = 16
DEFAULT_QUEUE_SECONDS = 86400
REPORT_OUTCOMES = {'failed', 'deferred', 'declined', 'question', 'conflict', 'cancelled'}


def response_ready(response):
    """A verified negative report can wake a waiter without satisfying its work."""
    return (response.get("kind") != "ack" and (
        response.get("status") in {"rejected", "unknown"}
        or response.get("status") in {"staged", "sealed"} and (
            response.get("qualified") is True or response.get("outcome") in REPORT_OUTCOMES
            or response.get('status') == 'sealed' and response.get('kind') == 'result')))



def continuation_outcome(request):
    response = request.get('response') or {}
    terminal = request.get('terminalOutcome') or {}
    candidates = ([response] if response_ready(response) else []) + (
        [terminal] if terminal.get('status') == 'observed' else [])
    return max(candidates, key=lambda row: row.get('outcomeRevision', 0)) if candidates else None


def outcome_sender(request):
    # Preparation failures are host observations about the caller's operation;
    # an uncreated peer must never be impersonated as their author.
    return (request['senderSessionId'] if request.get('commandAction') == 'coordination.create'
            else request['target']['sessionId'])


def definitions(schema, string):
    from .result_artifacts import REFERENCE_HELP
    sid = {"sessionId": string(200)}
    references = {"type": "array", "maxItems": 16, "items": string(2000)}
    message = {**sid, "senderSessionId": string(200), "grantId": string(200),
               "text": {**string(16000), "minLength": 1},
               "mode": {"enum": ["notify", "queue", "steer"]},
               "replyToRequestId": string(200), "references": references,
               "expiresInSeconds": {"type": "integer", "minimum": 1, "maximum": 86400},
               "resume": schema({"sourceMessageId": string(200),
                   "expectedInterruptionRevision": {"type": "integer", "minimum": 0}},
                   ["sourceMessageId", "expectedInterruptionRevision"]),
               "scheduleRepair": schema({"sourceMessageId": string(200),
                   "schedules": {"type": "array", "minItems": 1, "maxItems": 16,
                       "items": schema({"id": string(200), "expectedRevision": {"type": "integer", "minimum": 0}}, ["id", "expectedRevision"])}},
                   ["sourceMessageId", "schedules"])}
    from .coordination_watches import definitions as watch_definitions
    return {
        **watch_definitions(schema, string),
        "coordination.grant": ("Retired. Historical grants remain readable; fresh collaboration uses authenticated root generations.", schema({
            **sid, "participants": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True, "items": string(200)},
            "sourceMessageId": string(200),
            "purpose": {**string(4000), "minLength": 1},
            "modes": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"enum": ["notify", "queue", "steer"]}},
            "idleStart": {"type": "boolean"}, "allowCreate": {"type": "boolean"},
        }, ["sessionId", "participants", "purpose", "modes"])),
        "coordination.revoke": ("Retired. Legacy work cannot resume; use ordinary human Stop for current task work.", schema({**sid, "grantId": string(200)}, ["sessionId", "grantId"])),
        "coordination.context": ("Read bounded related receipts, current admitted humanInput identity and historical grants without loading peer transcripts. humanInput is null outside the current human-backed root generation; it is not a permission grant.", schema(sid, ["sessionId"])),
        "coordination.recap": ("Compare bounded saved work with an optional private caller cursor. Passive; no model, history hydration or shared read-state write. nextCursor advances only returned changes; retain it only after presenting those changes.", schema({**sid, "cursor": string(16000)}, ["sessionId"])),
        "coordination.diagnostics": ("Read portable bounded phase/timing metadata for this root. Hashed correlation IDs, no prompts, drafts, paths or tool payloads; unavailable timing remains explicit. Does not start work.", schema(sid, ["sessionId"])),
        "coordination.decide": ("Retired. Legacy pending proposals cannot authorize new work.", schema({
            **sid, "proposalId": string(200), "decision": {"enum": ["allow", "deny"]},
        }, ["sessionId", "proposalId", "decision"])),
        "coordination.send": ("Commit attributed same-workspace task input and return its receipt promptly. Notify never wakes; queue preparation continues under service ownership. Read results, never replay uncertain input. To resume a commissioned member after an explicit human instruction, use queue with resume={sourceMessageId, expectedInterruptionRevision}; the host binds the current human input and exact Stop/task/assignment boundaries. This admits one fresh input without resetting budgets, permissions or replaying old work. For explicit human repair of existing schedules, scheduleRepair scopes the sourceMessageId and exact target IDs/revisions; the owner may resume those schedules unchanged.", schema(message, ["sessionId", "text"])),
        "coordination.create": ("Commission an authorized durable task chat inheriting the caller's configuration, workspace and actual execution placement, with a saved return notification before its brief can run. This does not create an independent user chat in a different folder. Inspect this operation's requestId for its initial result. Returns before preparation and does not select the peer. Set returnNotification:false only when no automatic return is wanted.", schema({
            "senderSessionId": string(200), "grantId": string(200), "title": {**string(100), "minLength": 1},
            "text": {**string(16000), "minLength": 1}, "references": references,
            "returnNotification": {"type": "boolean", "default": True},
        }, ["title", "text"])),
        "coordination.read": ("Read a bounded window from one existing native conversation without selecting or warming it.", schema({
            **sid, "scope": {"enum": ["workspace", "all"]}, "messageId": string(200), "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "textLimit": {"type": "integer", "minimum": 1, "maximum": 4000},
        }, ["sessionId"])),
        "coordination.cancel": ("Withdraw your exact queued request, or request cancellation of already admitted work. Does not stop the recipient chat, undo effects, or prove cancellation. Active recipients see the request at their next coordination boundary and report outcome=cancelled when settled. Inspect coordination.result; never replay an unknown admission.", schema({
            **sid, "requestId": string(200), "reason": string(1000)}, ["sessionId", "requestId"])),
        "coordination.result": ("Inspect admission and exact qualified final result links for one request. scope=all never widens request-participant/workspace authority. Acknowledgements and unrelated turns do not count.", schema({"requestId": string(200), "scope": {"enum": ["workspace", "all"]}}, ["requestId"])),
        "coordination.artifact": ("Read the exact retained file declared in your request relationship, even after its source disappears. Bounded text and download identity; no peer output-library access or work admission. Retained bytes are not proof of correctness.", schema({**sid,
            "requestId": string(200), "reference": string(2000), "scope": {"enum": ["workspace", "all"]}, "responseRevision": {"type": "integer", "minimum": 1}, "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 4000}}, ["sessionId", "requestId", "reference"])),
        "coordination.reply": ("Record a typed reply to your exact delivered request. Optional messageIds name your current-generation assistant output, never the incoming request messageId; omit them when unknown. Retain files using path@sha256:<64 lowercase hex digits> in references. Before ending the turn, check artifactVerification and each artifacts[].status: bytes are saved before this reply is acknowledged; later terminal sealing only qualifies the report. Later current generations may reply with expectedResponseRevision from coordination.result. Questions/deferrals stay open; reports are unqualified until separately proven terminal. Exact command IDs deduplicate retries. At most 32 revisions per request.", schema({
            "requestId": string(200), "kind": {"enum": ["result", "ack", "defer", "decline", "question", "conflict"]},
            "expectedResponseRevision": {"type": "integer", "minimum": 0, "maximum": MAX_RESPONSE_REVISIONS},
            "outcome": {"enum": ["success", "failed", "deferred", "declined", "question", "conflict", "cancelled"]},
            "text": {**string(4000), "minLength": 1}, "references": {**references,
                "description": REFERENCE_HELP,
                "items": {**string(2000), "description": "Exact retained local file reference or opaque non-file citation."}},
            "messageIds": {"type": "array", "maxItems": 16, "items": string(200),
                           "description": "Optional exact assistant output IDs from your current generation. Never use the incoming request's messageId, a user/peer input ID, another conversation's output, or an earlier generation. Omit messageIds if no current assistant output ID is known; requestId already correlates the request. Supply evidence in references. Invalid IDs are refused before a reply revision is recorded."},
        }, ["requestId", "kind", "outcome", "text"])),
        "coordination.subscribe": ("Subscribe to meaningful revisions of one exact request, at most 32 automatic notifications. Questions, deferrals and negative outcomes never satisfy a dependency; only qualified success does. Every continuation rechecks the saved task/Stop boundary. Cursor reads confer no authority.", schema({
            **sid, "requestId": string(200), "grantId": string(200),
        }, ["sessionId", "requestId"])),
    }


def restore_owner(session, host_instance):
    """Retire prior-host authority and native waits without replaying work."""
    if session.get('_collaborationHostInstanceId') == host_instance:
        return
    active = session.get('collaborationGeneration')
    if active and not active.get('terminal'):
        active.update(terminal=True, detail='Host restarted; no live generation authority.')
    for approval in session.get('approvals', []):
        if approval.get('status', 'pending') not in {None, 'pending'}:
            continue
        identity = approval.get('id', '')
        if identity.startswith('collaboration:'):
            approval['status'] = 'retired'
        elif identity.startswith('capability:'):
            # Its owner determines whether this was a live native wait or a
            # durable raw-API request after configuration authority is loaded.
            approval['_hostRestarted'] = True
        else:
            # Ordinary approvals resolve an in-memory native waiter. It cannot
            # survive host replacement. Configuration grants instead retain a
            # durable decision and their own authority checks across restarts.
            approval.update(status='expired', resolutionReason='host_restarted',
                detail='The app restarted before this permission was answered. Review the saved work and ask again if needed; nothing was replayed.')
    # The marker travels with this one durable owner, not a growing process-wide
    # set of every chat visited. Same-host eviction is not another restart.
    session['_collaborationHostInstanceId'] = host_instance


class Collaboration:
    def __init__(self, service):
        self.service = service
        self.draining = set()
        self.creating = set()
        self.expiry_handles = {}
        # Private preparation configuration, never returned by receipt APIs.
        # Native conversation history and existing command receipts stay authoritative.
        service.db.execute('''CREATE TABLE IF NOT EXISTS collaboration_preparations(
            request_id TEXT PRIMARY KEY, configuration TEXT NOT NULL)''')
        for session in service.state["sessions"]:
            restore_owner(session, service.instance_id)
        # Index receipts, not conversations or transcripts.
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_target ON commands(
            json_extract(receipt,'$.commandAction'), json_extract(receipt,'$.target.sessionId'))""")
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_sender ON commands(
            json_extract(receipt,'$.commandAction'), json_extract(receipt,'$.senderSessionId'))""")
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_admission_order ON commands(
            json_extract(receipt,'$.target.sessionId'), json_extract(receipt,'$.admissionOrder'))
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'""")
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_pending_outcomes ON commands(
            json_extract(receipt,'$.subscription.status'), json_extract(receipt,'$.senderSessionId'))
            WHERE json_extract(receipt,'$.commandAction') IN ('coordination.send','coordination.watch')
            AND (coalesce(json_extract(receipt,'$.outcomeRevision'),0)>coalesce(json_extract(receipt,'$.subscription.lastClaimedRevision'),0)
                OR json_extract(receipt,'$.subscription.status')='waiting')""")
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_replies ON commands(
            json_extract(receipt,'$.commandAction'), json_extract(receipt,'$.result.requestId'))""")
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_pair_window ON commands(
            json_extract(receipt,'$.commandAction'), json_extract(receipt,'$.senderSessionId'),
            json_extract(receipt,'$.target.sessionId'), json_extract(receipt,'$.acceptedAt'))""")
        for identity, encoded in service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.create'
                AND json_extract(receipt,'$.delivery') IN ('creation_pending','created_initial_pending')""").fetchall():
            value = json.loads(encoded)
            if value.get('creationVersion') == 1:
                continue
            value.update(delivery="unknown", detail="Host restarted during creation; retained effects are not replayed.")
            self.save(identity, value)
        for identity, encoded in service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.delivery')='submitting'""").fetchall():
            value = json.loads(encoded)
            value.update(delivery="unknown", detail="Host restarted during admission; no replay.")
            self.observe_admission(value, notify=False)
        for identity, encoded in service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.response.status')='staged'""").fetchall():
            value = json.loads(encoded)
            value["response"].update(status="unknown", qualified=False,
                detail="Host restarted before terminal proof; declaration retained, no replay.")
            value['response'].pop('outcomeRevision', None)
            self.record_outcome(value, value['response'])
            self.save_response(value)
            if value.get("subscription") and value['subscription'].get('notificationVersion') != 2:
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
        service.db.execute("""CREATE INDEX IF NOT EXISTS peer_request_cancellations ON commands(
            json_extract(receipt,'$.target.sessionId'), json_extract(receipt,'$.cancellation.status'))
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'""")
        from .coordination_watches import Watches
        self.watches = Watches(self)
        service.db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS peer_resume_authorization
            ON commands(json_extract(receipt,'$.humanResume.authorizationKey'))
            WHERE json_extract(receipt,'$.humanResume.authorizationKey') IS NOT NULL""")
        service.db.commit()

    def error(self, text, status=403):
        from .service import AppError
        raise AppError(text, status)

    def source(self, args, origin, caller, *, hydrate=True):
        sid = caller if origin not in {"ui", "user"} else args.get("senderSessionId") or args.get("sessionId")
        if not sid:
            self.error("A user must explicitly authorize a calling conversation.")
        source = self.service._session(sid, hydrate=hydrate)
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

    def authorize(self, source, args, *, hydrate=True):
        target = self.service._session(args["sessionId"], hydrate=hydrate)
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
        receipt.setdefault('acceptedAt', time.time())
        self.service.db.execute("INSERT INTO commands(id,fingerprint,receipt) VALUES(?,?,?)",
                                (identity, digest, json.dumps(receipt)))
        self.refresh_reference(receipt)

    def require_inbox_slot(self, sid, sender=None, assignment=None):
        # A saved wait reserves one slot for its eventual outcome. Interim
        # notifications retain a slot for the next revision as well as their
        # queued input, and both count against this bound.
        pending = self.service.db.execute("""SELECT count(*) FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.target.sessionId')=?
            AND json_extract(receipt,'$.delivery') IN ('queued','submitting','pending_steer')""", (sid,)).fetchone()[0]
        session = self.service._session(sid)
        task = session.get('task') or self.service.coordination.task(sid)
        waiting = self.service.db.execute("""SELECT count(*) FROM commands
            WHERE json_extract(receipt,'$.commandAction') IN ('coordination.send','coordination.create','coordination.watch','workspace.tests.submit','worktree.handoff')
            AND json_extract(receipt,'$.senderSessionId')=?
            AND (json_extract(receipt,'$.subscription.status') IN ('waiting','reserved','backpressured')
                OR json_extract(receipt,'$.subscription.open')=1)
            AND json_extract(receipt,'$.subscription.interruptionRevision')=?
            AND json_extract(receipt,'$.subscription.taskId') IS ?
            AND ((json_type(receipt,'$.subscription.taskContinuationEpoch') IS NOT NULL
                  AND json_extract(receipt,'$.subscription.taskContinuationEpoch') IS ?)
                 OR (json_type(receipt,'$.subscription.taskContinuationEpoch') IS NULL
                     AND json_extract(receipt,'$.subscription.taskRevision') IS ?))""",
            (sid, session.get('interruptionRevision', 0), task.get('id'),
             continuation_epoch(task), task.get('revision'))).fetchone()[0]
        if pending + waiting >= MAX_PENDING_INPUTS:
            self.error('This conversation inbox is full, including reserved replies. Wait for pending work to settle; no input was added.', 429)
        for key, value, limit, label in [('senderSessionId', sender, MAX_SENDER_INPUTS, 'sender'),
                                        ('assignmentId', assignment, MAX_ASSIGNMENT_INPUTS, 'assignment')]:
            if not value:
                continue
            count = self.service.db.execute(f"""SELECT count(*) FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.target.sessionId')=?
                AND json_extract(receipt,'$.{key}')=?
                AND json_extract(receipt,'$.delivery') IN ('queued','submitting','pending_steer')""", (sid, value)).fetchone()[0]
            if count >= limit:
                self.error(f'This {label} has {limit} pending inputs in the conversation inbox. Wait for admission before adding more work.', 429)

    def causal_depth(self, source, lineage, origin):
        if origin in {'ui', 'user'}:
            return 0
        inputs = set(lineage.get('sourceInputIds', []))
        # Only retained host provenance can reset a causal chain. Equal prose,
        # a role=user native row, or a model's claimed human approval cannot.
        if any(row.get('inputId') in inputs and row.get('inputOrigin') in {'ui', 'user'}
               for row in source.get('messages', [])):
            return 0
        depth = 0
        for identity in inputs:
            row = self.service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
            if row:
                receipt = json.loads(row[0])
                if receipt.get('commandAction') == 'coordination.send' and receipt.get('target', {}).get('sessionId') == source['id']:
                    depth = max(depth, receipt.get('causalDepth', 0))
        return depth + 1

    def require_causal_depth(self, depth):
        if depth > MAX_CAUSAL_HOPS:
            self.error('This causal peer-input chain reached 16 hops. Inspect the retained work and wait for explicit human direction; changing IDs or wording does not reset the bound.', 429)

    def require_message_rate(self, source, target):
        count = self.service.db.execute("""SELECT count(*) FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.senderSessionId')=?
            AND json_extract(receipt,'$.target.sessionId')=?
            AND json_extract(receipt,'$.acceptedAt')>?""",
            (source, target, time.time() - 60)).fetchone()[0]
        if count >= MAX_PAIR_MESSAGES_PER_MINUTE:
            self.error('Too many messages between these conversations in one minute. Pause and combine updates; no input was added.', 429)

    def refresh_reference(self, receipt):
        """Only affected roots retain a tiny reference projection, not bodies."""
        if receipt.get('commandAction') == 'coordination.watch':
            sids = [receipt.get('senderSessionId')]
        elif receipt.get("commandAction") == "coordination.grant":
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
            from .request_cancellation import references
            session["coordinationReference"] = {
                "sessionId": sid, "capturedAt": value["capturedAt"], "bounded": True,
                "grants": [{key: row[key] for key in ("id", "revision", "revoked", "participants", "idleStart", "allowCreate")}
                           for row in value["grants"][:8]],
                "cancellations": references(self, sid),
                "requests": [{key: row.get(key) for key in ("requestId", "senderSessionId", "target", "delivery", "grantId", "cancellation")}
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
        from .request_cancellation import status as cancellation_status
        for request in requests:
            if request.get('cancellation'):
                request['cancellation'] = cancellation_status(request)
        proposals = [json.loads(row[0]) for row in self.service.db.execute("""SELECT receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.grant.pending'
            AND EXISTS (SELECT 1 FROM json_each(json_extract(receipt,'$.result.participants')) WHERE value=?)
            ORDER BY rowid DESC LIMIT 32""", (sid,)).fetchall()]
        watches = [json.loads(row[0]) for row in self.service.db.execute("""SELECT receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.watch'
            AND json_extract(receipt,'$.senderSessionId')=? ORDER BY rowid DESC LIMIT 16""", (sid,)).fetchall()]
        return {"grants": [row for row in grants if sid in row["participants"]], "proposals": proposals, 'watches': watches,
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

    def current_human_input(self, source):
        """Expose a bounded current-input identity, never a permission grant."""
        from .service import AppError
        from .schedule_handoff import latest_human
        try:
            binding, active = self.active_binding(source)
        except AppError:
            return None
        human = latest_human(source)
        admitted = set(active.get('inputIds', [])) & {
            row.get('inputId') for row in binding.get('_inputBindings', [])}
        if (not human or human.get('questionId') or human.get('scheduledRunId')
                or human.get('inputId') not in admitted):
            return None
        return {'sourceMessageId': human['id'], 'sourceInputId': human['inputId'],
                'createdAt': human.get('createdAt'), 'generationId': active['id']}

    def validate_reply(self, source, args):
        binding, active = self.active_binding(source)
        request = self.receipt(args["requestId"])
        delivered = set(active.get('inputIds', [])) & {row.get('inputId') for row in binding.get('_inputBindings', [])}
        if (request.get('commandAction') != 'coordination.send'
                or request.get('target', {}).get('sessionId') != source['id']
                or not delivered or request.get('delivery') not in {'accepted', 'applied', 'submitting'}):
            self.error('Declare only your exact delivered request from a current root generation.')
        if request.get('protocol') != PROTOCOL:
            self.error('Legacy requests cannot receive fresh response declarations.', 410)
        revision = request.get('responseRevision', 1 if request.get('response') else 0)
        if args.get('expectedResponseRevision', 0) != revision:
            self.error('The response revision changed. Read the retained result and use its expectedResponseRevision.', 409)
        if revision >= MAX_RESPONSE_REVISIONS:
            self.error('This request reached its 32 reply revisions. Start a new intentional request; retained reports were not replaced.', 429)
        if request.get('response', {}).get('status') == 'staged':
            self.error('The previous report is awaiting terminal evidence. Inspect it before submitting another revision.', 409)
        if args['requestId'] not in delivered:
            if request.get('delivery') not in {'accepted', 'applied'}:
                self.error('A later generation needs confirmed prior input admission.', 409)
            reason = self.guard(request, for_reply=True)
            if reason:
                self.error('The original request boundary no longer permits a later reply: ' + reason, 409)
        self.validate_reply_message_ids(source, args.get('messageIds', []), active['id'])
        return active, request, revision, delivered

    def validate_reply_message_ids(self, source, identities, generation):
        if not identities:
            return
        # This preflight uses current retained metadata only. A checkpoint can
        # name a canonical native row outside the display window; the terminal
        # path still resolves that exact row and rejects conflicting evidence.
        messages = {row['id']: row for row in source.get('messages', [])}
        anchors = {row['messageId']: row for row in source.get('collaborationMessageAnchors', [])}
        for identity in identities:
            message, anchor = messages.get(identity), anchors.get(identity)
            if message is not None:
                linked_generation = message.get('generationId')
                if (linked_generation is None and anchor
                        and anchor.get('nativeIndex') == message.get('nativeIndex')):
                    linked_generation = anchor.get('generationId')
                valid = message.get('role') == 'assistant' and linked_generation == generation
            else:
                valid = (anchor is not None and anchor.get('generationId') == generation
                         and type(anchor.get('nativeIndex')) is int and anchor['nativeIndex'] >= 0)
            if not valid:
                self.error('Invalid messageIds: each ID must name your assistant output from this current generation, '
                           'never the incoming request messageId, a user/peer input, or another conversation or generation. '
                           'Omit messageIds if no current assistant output ID is known; requestId already correlates the request. '
                           'Keep any evidence in references. No reply revision was recorded; correct the arguments and retry.', 400)

    def require_result_reader(self, receipt, origin, caller):
        if origin in {'ui', 'user'}:
            return
        reader = self.source({}, origin, caller)
        sid = receipt.get('target', {}).get('sessionId') or receipt['sessionId']
        sender = receipt.get('senderSessionId') or receipt.get('result', {}).get('creatorSessionId')
        workspace = receipt.get('workspace') or self.service._session(sender, hydrate=False)['workspace']
        if (reader['id'] not in {sender, sid}
                or reader['workspace'] != workspace
                or receipt.get('commandAction') == 'coordination.send'
                and self.service._session(sid, hydrate=False)['workspace'] != reader['workspace']):
            self.error("This result is outside the caller's request and workspace scope.")

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
        if receipt.get('commandAction') == 'coordination.reply':
            request = self.receipt(receipt['result']['requestId'])
            ids = {request['senderSessionId'], request['target']['sessionId']}
        if receipt.get('commandAction') == 'coordination.watch':
            ids = {receipt['senderSessionId']}
        self.service._publish_changes(sessions=ids)

    async def decide(self, args, origin):
        self.error("Coordination approval APIs are retired. Historical decisions are read-only.", 410)

    def require_continuation(self):
        if not hasattr(self.service.runtime, "collaboration_input"):
            self.error("This runtime lacks guarded idle peer admission; no task or wait was created.", 409)

    def saved_wait(self, source, request_id, *, status='waiting'):
        task = source.get('task') or self.service.coordination.task(source['id'])
        return {'requestId': request_id, 'taskId': task.get('id'), 'taskRevision': task.get('revision'),
                'taskContinuationEpoch': continuation_epoch(task),
                'interruptionRevision': source.get('interruptionRevision', 0), 'status': status,
                'continuationId': str(uuid.uuid5(uuid.NAMESPACE_URL, 'collaboration-wait:' + source['id'] + ':' + request_id)),
                'notificationVersion': 2, 'lastClaimedRevision': 0, 'open': True,
                'supported': hasattr(self.service.runtime, 'collaboration_input')}

    def record_outcome(self, request, outcome):
        if outcome.get('outcomeRevision') is None:
            request['outcomeRevision'] = request.get('outcomeRevision', 0) + 1
            outcome['outcomeRevision'] = request['outcomeRevision']
        return outcome['outcomeRevision']

    def save_response(self, request):
        response = request.get('response') or {}
        operation = response.get('operationId')
        if operation:
            row = self.service.db.execute('SELECT receipt FROM commands WHERE id=?', (operation,)).fetchone()
            if row:
                receipt = json.loads(row[0])
                receipt['result'] = copy.deepcopy(response)
                self.save(operation, receipt)

    def expire_later(self, receipt):
        identity, deadline = receipt.get('requestId'), receipt.get('expiresAt')
        if not identity or not deadline or identity in self.expiry_handles:
            return
        def wake():
            self.expiry_handles.pop(identity, None)
            if not self.service.closed:
                self.service._task(self.expire(identity))
        self.expiry_handles[identity] = asyncio.get_running_loop().call_later(max(0, deadline-time.time()), wake)

    def cancel_expiry(self, identity):
        handle = self.expiry_handles.pop(identity, None)
        if handle:
            handle.cancel()

    async def expire(self, identity):
        async with self.service.lock:
            receipt = self.receipt(identity)
            if receipt.get('delivery') != 'queued' or receipt.get('expiresAt', float('inf')) > time.time():
                return
            receipt.update(delivery='expired', detail='The queued request expired before runtime admission; its input was not executed.')
            self.observe_admission(receipt)
            self.publish_receipt(receipt)

    def close(self):
        self.watches.close()
        for handle in self.expiry_handles.values():
            handle.cancel()
        self.expiry_handles.clear()

    def observe_admission(self, receipt, *, notify=True):
        phase = receipt.get('delivery')
        if phase in {'unknown', 'not_sent', 'suppressed', 'expired'}:
            detail = (receipt.get('admission') or {}).get('reason') or receipt.get('detail') or 'Inspect the retained admission receipt.'
            receipt['terminalOutcome'] = {'status': 'observed', 'kind': 'admission',
                'outcome': 'unknown' if phase == 'unknown' else 'failed', 'qualified': False,
                'requestId': receipt['requestId'], 'observedAt': time.time(),
                'text': f'The host recorded input admission as {phase}. {detail}'}
            self.record_outcome(receipt, receipt['terminalOutcome'])
        self.save(receipt['requestId'], receipt)
        if notify and receipt.get('subscription') and continuation_outcome(receipt):
            self.enqueue_continuation(receipt)

    def creation_guard(self, receipt):
        if receipt.get('assignmentId'):
            reason = self.service.assignments.blocked(identity=receipt['assignmentId'])
            if reason:
                return reason
        try:
            source = self.service._session(receipt['senderSessionId'])
        except Exception:
            return 'The requester is no longer available.'
        task = source.get('task') or self.service.coordination.task(source['id'])
        from .updates import work_paused
        if work_paused(self.service.state):
            return 'Host work is paused for an update.'
        if (source.get('interruptionRevision', 0) != receipt['sourceInterruptionRevision']
                or task.get('status') in {'paused', 'blocked', 'completed'}):
            return 'The requester or its assignment was stopped.'
        if (task.get('id'), task.get('revision')) != (receipt.get('sourceTaskId'), receipt.get('sourceTaskRevision')):
            return 'The requesting assignment changed.'
        if (source['workspace'] != receipt['workspace']
                or (source.get('workingDirectory') or source['workspace']) != receipt['result']['workingDirectory']):
            return 'The requester execution placement changed.'

    def can_steer(self, target):
        active = target.get("collaborationGeneration") or {}
        return bool(hasattr(self.service.runtime, "collaboration_steer") and active.get("id") and not active.get("terminal"))

    def guard(self, receipt, *, for_reply=False):
        if not for_reply and receipt.get('cancellation'):
            return 'The requester cancelled this exact request before further admission.'
        try:
            if receipt.get('delivery') == 'queued' and receipt.get('expiresAt', float('inf')) <= time.time():
                return 'The queued request expired before runtime admission.'
            if receipt.get("protocol") != PROTOCOL:
                return "Legacy coordination is read-only; automatic admission is suppressed."
            from .updates import work_paused
            if work_paused(self.service.state):
                return "Host work is paused for an update; peer admission is suppressed."
            source = self.service._session(receipt["senderSessionId"])
            reason = self.service.assignments.blocked(source, receipt.get('assignmentId'))
            if reason:
                return reason
            if (receipt.get('assignmentId') and not receipt.get('dependencyRequestId')
                    and receipt.get('sourceInterruptionRevision') != source.get('interruptionRevision', 0)):
                return 'The assignment requester was interrupted after this request; old input is not replayed.'
            if receipt.get('creationOperationId'):
                creation = self.receipt(receipt['creationOperationId'])
                reason = self.creation_guard(creation)
                if reason:
                    return reason
            if receipt.get('dependencyRequestId'):
                dependency = self.receipt(receipt['dependencyRequestId'])
                if dependency.get('cancellation'):
                    return 'The requester cancelled the originating request; no new automatic return is admitted.'
                if dependency.get('commandAction') == 'workspace.tests.submit':
                    if reason := self.service.test_queue.continuation_guard(dependency):
                        return reason
                if dependency.get('commandAction') == 'worktree.handoff':
                    reason = self.service.worktrees.continuation_guard(dependency)
                    if reason:
                        return reason
                if dependency.get('commandAction') == 'coordination.watch' and dependency.get('watch', {}).get('status') == 'cancelled':
                    return 'The one-shot watch was cancelled.'
                if receipt.get('dependencyOutcomeRevision') and not self.claim_matches(dependency, receipt):
                    return 'This outcome notification was superseded or its qualification changed; inspect the current result.'
            elif 'taskContinuationEpoch' in receipt:
                return 'Only retained outcome notifications can follow a continuing objective.'
            target = self.authorize(source, {"sessionId": receipt["target"]["sessionId"]})
            if target.get('fileAccessPending'):
                return 'The recipient has pending file-access settings; existing work must settle before new input.'
            reason = self.service.assignments.blocked(target)
            if reason:
                return reason
            if source["workspace"] != receipt.get("workspace"):
                return "The request workspace changed."
            if target.get("interruptionRevision", 0) != receipt["interruptionRevision"]:
                return "The user interrupted the recipient after this request."
            from . import peer_resume
            resuming = peer_resume.pending(receipt)
            if resuming and (reason := peer_resume.guard(self.service, receipt, source, target)):
                return reason
            if target.get("status") in {"stopping", "interrupted"} or (
                    target.get("status") in {"stopped", "error"} and not resuming):
                return "The recipient is stopped or unavailable."
            task = target.get("task") or self.service.coordination.task(target["id"])
            if task.get("status") in {"paused", "blocked", "completed"} and not resuming:
                return "The recipient task is not active."
            task_binding = receipt
            if for_reply and 'replyTaskContinuationEpoch' in receipt:
                task_binding = {**receipt, 'taskContinuationEpoch': receipt['replyTaskContinuationEpoch']}
            if not task_matches(task, task_binding):
                return "The recipient task changed."
            if target.get("ownership", {}).get("status") in {"blocked", "yielding", "yielded", "yield-failed", "taking-over"}:
                return "The native owner is unavailable."
            if receipt.get("mode") == "steer":
                active = target.get("collaborationGeneration") or {}
                if active.get("terminal") or active.get("id") != receipt.get("targetGenerationId"):
                    return "The anchored recipient generation is no longer active."
        except Exception as exc:
            return str(exc)

    def pending_continuation(self, sid):
        """Read an outcome-backed, unsubmitted exact wait; never claim or publish."""
        for (encoded,) in self.service.db.execute("""SELECT receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.target.sessionId')=?
                AND json_extract(receipt,'$.delivery')='queued'
                AND json_extract(receipt,'$.mode')='queue'
                AND json_extract(receipt,'$.dependencyRequestId') IS NOT NULL
                ORDER BY rowid""", (sid,)):
            receipt = json.loads(encoded)
            if self.guard(receipt):
                continue
            row = self.service.db.execute("SELECT receipt FROM commands WHERE id=?",
                                          (receipt["dependencyRequestId"],)).fetchone()
            request = json.loads(row[0]) if row else {}
            wait, response = request.get("subscription") or {}, request.get("response") or {}
            if (request.get("protocol") == PROTOCOL and request.get("senderSessionId") == sid
                    and outcome_sender(request) == receipt.get("senderSessionId")
                    and receipt.get('inputId') == receipt.get('requestId')
                    and self.claim_matches(request, receipt)
                    and (receipt.get('outcomeSnapshot') is not None or continuation_outcome(request) is not None)
                    and any(m.get("id") == receipt.get("messageId") and m.get("inputId") == receipt["inputId"]
                            and m.get("inputOrigin") == "peer"
                            for m in self.service._session(sid).get("messages", []))):
                return receipt

    def continuation_ready(self, sid, args):
        source = self.source({}, "agent", sid)
        self.active_binding(source)  # Missing/foreign root or stale generation fails closed.
        task = source.get("task") or self.service.coordination.task(sid)
        if (task.get("id"), task.get("revision")) != (args.get("taskId"), args.get("taskRevision")):
            return {"ready": False}
        from .capacity import saved_policy, usage_snapshot, evaluate
        if not evaluate(saved_policy(self.service, sid), usage_snapshot(source))["allowed"]:
            return {"ready": False}
        return {"ready": self.pending_continuation(sid) is not None}

    def claim_matches(self, request, receipt):
        wait = request.get('subscription') or {}
        revision = receipt.get('dependencyOutcomeRevision')
        if wait.get('notificationVersion') == 2 and revision:
            identity = self.continuation_identity(wait, revision)
            snapshot = receipt.get('outcomeSnapshot') or {}
            # A staged report may later seal, but changed qualification of the
            # very same outcome cannot retain an earlier success claim.
            current = next((row for row in (request.get('response') or {}, request.get('terminalOutcome') or {})
                            if row.get('outcomeRevision') == revision), None)
            if current is not None and current.get('qualified') is not snapshot.get('qualified'):
                return False
            if receipt.get('dependencySatisfied') and request.get('outcomeRevision', revision) > revision:
                return False  # A later report superseded the old successful dependency.
            return revision <= wait.get('lastClaimedRevision', 0) and identity == receipt.get('requestId')
        return wait.get('status') == 'claimed' and wait.get('continuationId') == receipt.get('requestId')

    def fair_rows(self, sid):
        rows = self.service.db.execute("""SELECT id,receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.target.sessionId')=?
            AND json_extract(receipt,'$.delivery')='queued' ORDER BY rowid LIMIT 64""", (sid,)).fetchall()
        groups = {}
        for row in rows:
            groups.setdefault(json.loads(row[1]).get('senderSessionId'), []).append(row)
        last = self.service.db.execute("""SELECT json_extract(receipt,'$.senderSessionId') FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.target.sessionId')=?
            AND json_extract(receipt,'$.admissionOrder') IS NOT NULL
            ORDER BY json_extract(receipt,'$.admissionOrder') DESC LIMIT 1""", (sid,)).fetchone()
        senders = list(groups)
        if last and last[0] in senders:
            offset = senders.index(last[0])+1
            senders = senders[offset:]+senders[:offset]
        result = []
        while any(groups.values()):
            for sender in senders:
                if groups[sender]:
                    result.append(groups[sender].pop(0))
        return result

    def resume_outcomes(self, sid):
        for (encoded,) in self.service.db.execute("""SELECT receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction') IN ('coordination.send','coordination.watch','worktree.handoff')
            AND json_extract(receipt,'$.senderSessionId')=?
            AND json_extract(receipt,'$.subscription.status')='backpressured' LIMIT 64""", (sid,)).fetchall():
            self.enqueue_continuation(json.loads(encoded))

    async def drain(self, sid):
        if sid in self.draining:
            return
        self.draining.add(sid)
        try:
            self.resume_outcomes(sid)
            rows = self.fair_rows(sid)
            for identity, encoded in rows:
                async with self.service.lock:
                    receipt = self.receipt(identity)
                    if receipt.get("delivery") != "queued":
                        self.cancel_expiry(identity)
                        continue
                    target = self.service._session(sid)
                    if target.get('fileAccessPending'):
                        break  # Keep queued input until the existing refresh owner retires the old mount.
                    reason = self.guard(receipt)
                    if reason:
                        receipt.update(delivery='expired' if receipt.get('expiresAt', float('inf')) <= time.time() else 'suppressed', detail=reason)
                        self.observe_admission(receipt)
                        self.publish_receipt(receipt)
                        self.cancel_expiry(identity)
                        continue
                    if (target.get("status") in BUSY or target.get('configurationBusy')
                            or target.get('pendingConfiguration', {}).get('phase') in {'queued', 'applying'}
                            or target.get('pendingConfiguration', {}).get('recoveryRequired')
                            or target.get('pendingConfiguration', {}).get('phase') == 'error'
                            and target.get('pendingConfiguration', {}).get('effect', 'none') != 'none'):
                        break
                    # Durable claim precedes any runtime effect. Unknown never
                    # returns to queued, including cancellation and restart.
                    receipt['admissionOrder'] = self.service.db.execute("""SELECT coalesce(max(json_extract(receipt,'$.admissionOrder')),0)+1
                        FROM commands WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                        AND json_extract(receipt,'$.target.sessionId')=?""", (sid,)).fetchone()[0]
                    receipt['admittedAt'] = time.time()
                    receipt["delivery"] = "submitting"
                    self.save(identity, receipt)
                    self.service.db.commit()
                    self.cancel_expiry(identity)
                    snapshot = copy.deepcopy(target)
                arguments = {"inputId": identity,
                             "taskId": receipt.get("taskId"), "taskRevision": receipt.get("taskRevision")}
                if 'taskContinuationEpoch' in receipt:
                    arguments['taskContinuationEpoch'] = receipt['taskContinuationEpoch']
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
                        async with self.service.lock:
                            # Cancellation, replies and subscriptions can commit
                            # while transport is awaited. Reconcile only admission
                            # against the durable owner, never the pre-await copy.
                            receipt = self.receipt(identity)
                            receipt.update(delivery=phase, detail=result["reason"], admission=result)
                            self.observe_admission(receipt)
                            self.service.db.commit()
                        raise
                async with self.service.lock:
                    receipt = self.receipt(identity)
                    receipt.update(delivery=phase, admission=result)
                    self.observe_admission(receipt)
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
                or args.get("taskId") != receipt.get("taskId") or args.get("taskRevision") != receipt.get("taskRevision")
                or ('taskContinuationEpoch' in args) != ('taskContinuationEpoch' in receipt)
                or args.get('taskContinuationEpoch') != receipt.get('taskContinuationEpoch')):
            return {"admitted": False, "reason": "The exact host request is no longer submitting."}
        if receipt.get("mode") == "steer" and args.get("targetGenerationId") != receipt.get("targetGenerationId"):
            return {"admitted": False, "reason": "The steering generation anchor changed."}
        reason = self.guard(receipt)
        message = next((row for row in self.service._session(sid).get("messages", [])
                        if row.get("inputId") == args["inputId"] and row.get("inputOrigin") == "peer"), None)
        if message is None:
            return {"admitted": False, "reason": "The saved host-attributed input is unavailable."}
        from .peer_resume import pending
        return {"admitted": not reason, "reason": reason, "message": copy.deepcopy(message),
                "humanResume": not reason and pending(receipt)}

    async def submit_steer(self, identity):
        async with self.service.lock:
            receipt = self.receipt(identity)
            if receipt.get("delivery") != "pending_steer":
                return receipt
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
                async with self.service.lock:
                    receipt = self.receipt(identity)
                    # Native applied/held evidence can beat the acknowledgement.
                    receipt.update(delivery=receipt.get("delivery") if receipt.get("delivery") in {"applied", "held", "unknown"} else phase,
                                   admission=result)
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
        changed = self.watches.observe(session, kind, payload)
        if (kind == 'runtime.ended' and not payload.get('backgroundOnly')
                and payload.get('sessionId') is not None
                and payload.get('sessionId') in {session['id'], session.get('runtimeSessionId')}
                and payload.get('rootSessionId', session['id']) == session['id']):
            # Confirmed process exit ends routing authority even when the worker
            # could not flush its terminal generation event. It proves neither
            # successful completion nor a staged reply: keep those outcomes
            # unqualified and never replay any previously accepted input.
            active = session.get('collaborationGeneration') or {}
            if active and not active.get('terminal'):
                active.update(terminal=True, completed=False,
                              detail='Worker exited before terminal generation evidence was received.')
            return changed
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
            session['_collaborationHostInstanceId'] = self.service.instance_id
            return changed
        active = session.get("collaborationGeneration") or {}
        if active.get("id") != payload.get("generation_id") or active.get("terminal"):
            return changed
        active["terminal"] = True
        active["completed"] = (payload.get("event") == "generation.finished"
            and payload.get("disposition") == "manager_turn_finished"
            and not payload.get("active_job_ids"))
        # Only declarations staged in this actual generation can be sealed.
        for identity, encoded in self.service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.target.sessionId')=?
                AND json_extract(receipt,'$.response.status')='staged'""", (session["id"],)).fetchall():
            receipt = json.loads(encoded)
            response = receipt["response"]
            if response["generationId"] != payload.get("generation_id"):
                continue
            valid = (payload.get("event") == "generation.finished"
                     and bool(set(response.get('admittedInputIds') or [identity]) & set(payload.get('input_ids', [])))
                     and bool(payload.get("text", "").strip())
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
                               and response.get('artifactVerification') != 'incomplete'
                               and bool(response.get("references") or response.get("messageIds"))),
                    independentArtifactVerification=False)
            else:
                response.update(status="rejected", qualified=False, detail="Terminal evidence or exact linkage failed.")
            if response.get('status') == 'rejected':
                response.pop('outcomeRevision', None)
            if response_ready(response) and payload.get('event') != 'generation.failed':
                self.record_outcome(receipt, response)
            self.save_response(receipt)
            self.save(identity, receipt)
            changed.update({receipt["senderSessionId"], session["id"]})
            if response_ready(response) and payload.get('event') != 'generation.failed' and receipt.get("subscription"):
                self.enqueue_continuation(receipt)
        if payload.get('event') == 'generation.failed':
            # A host-observed failure is useful even if the model never managed
            # to declare a reply. It supplies no successful artifact evidence.
            delivered = set(payload.get('input_ids', [])) & set(active.get('inputIds', []))
            for identity, encoded in self.service.db.execute("""SELECT id,receipt FROM commands
                    WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                    AND json_extract(receipt,'$.target.sessionId')=?
                    AND json_extract(receipt,'$.protocol')=?""", (session['id'], PROTOCOL)).fetchall():
                if identity not in delivered:
                    continue
                receipt = json.loads(encoded)
                if receipt.get('delivery') not in {'accepted', 'applied', 'submitting'}:
                    continue
                receipt['terminalOutcome'] = {'status': 'observed', 'kind': 'result', 'outcome': 'failed',
                    'qualified': False, 'requestId': identity, 'generationId': active['id'],
                    'recipientSessionId': session['id'], 'observedAt': time.time(),
                    'text': 'The recipient runtime reported a failed generation for this delivered request. Inspect its retained history before deciding whether to retry.'}
                self.record_outcome(receipt, receipt['terminalOutcome'])
                self.save(identity, receipt)
                changed.update({receipt['senderSessionId'], session['id']})
                if receipt.get('subscription'):
                    self.enqueue_continuation(receipt)
        return changed

    def committed(self, identity):
        # Only the failure path needs a second connection: reading our own
        # transaction cannot establish whether a lost acknowledgement committed.
        import sqlite3
        path = self.service.db.execute('PRAGMA database_list').fetchone()[2]
        with sqlite3.connect(path) as database:
            return database.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone() is not None

    def continuation_identity(self, wait, revision):
        return wait['continuationId'] if revision == 1 else str(uuid.uuid5(
            uuid.NAMESPACE_URL, wait['continuationId'] + ':' + str(revision)))

    def enqueue_continuation(self, request):
        """Commit at most one guarded input for a meaningful outcome revision."""
        wait = request['subscription']
        versioned = wait.get('notificationVersion') == 2
        if request.get('protocol') != PROTOCOL or wait.get('status') not in (
                {'waiting', 'claimed', 'backpressured'} if versioned else {'waiting'}):
            return
        response = continuation_outcome(request)
        if response is None:
            return
        revision = self.record_outcome(request, response)
        if versioned and revision <= wait.get('lastClaimedRevision', 0):
            return
        if revision > MAX_RESPONSE_REVISIONS:
            wait.update(status='limit_reached', open=False, detail='Automatic outcome notification limit reached; inspect the retained result.')
            self.save(request['requestId'], request)
            return
        identity = self.continuation_identity(wait, revision) if versioned else wait['continuationId']
        if self.service.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone():
            return
        sender = self.service._session(request['senderSessionId'])
        if (self.service.assignments.blocked(sender, request.get('assignmentId'))
                or wait.get('interruptionRevision') != sender.get('interruptionRevision', 0)):
            wait.update(status='suppressed', open=False, detail='The saved wait was interrupted; late evidence remains inspectable.')
            self.save(request['requestId'], request)
            self.service.db.commit()
            return
        producer = outcome_sender(request)
        depth = max(request.get('causalDepth', 0), response.get('causalDepth', 0))+1
        if depth > MAX_CAUSAL_HOPS:
            wait.update(status='limit_reached', open=False, detail='The causal peer-input chain reached 16 hops; its report remains inspectable.')
            self.save(request['requestId'], request)
            self.service.db.commit()
            return
        # A report remains durable even if its independent notification cannot
        # commit. The payload and claim below are one transaction.
        self.save(request['requestId'], request)
        self.service.db.commit()
        prior_wait = copy.deepcopy(wait)
        prior_messages = list(sender.get('messages', []))
        prior_history = sender.get('historyManaged')
        try:
            if versioned:
                from .service import AppError
                # Temporarily release this wait's reserved slot before exchanging it
                # for a queued notification. Preserve a slot for further interim reports.
                wait['open'] = response.get('outcome') in {'deferred', 'question', 'conflict'}
                wait['status'] = 'claiming'
                self.save(request['requestId'], request)
                try:
                    self.require_inbox_slot(sender['id'], producer, request.get('assignmentId'))
                except AppError as exc:
                    wait.update(status='backpressured', open=True, detail=str(exc))
                    self.save(request['requestId'], request)
                    self.service.db.commit()
                    return
            sender['historyManaged'] = False
            envelope = {"senderSessionId": producer, "recipientSessionId": sender["id"],
                        "requestId": identity, "inputId": identity, "replyToRequestId": request["requestId"],
                        "mode": "queue", "purpose": "Resume the saved request-specific wait",
                        "references": ([response['terminalMessageId']] if response.get('terminalMessageId') else []) + response.get('references', [])[:15]}
            if response.get("qualified"):
                text = "A recipient declared a successful result and its root generation terminated. Independently check the referenced artifact before using it.\n"
            elif request.get('commandAction') == 'coordination.create':
                text = 'The host reported a preparation outcome for your commissioned task. This does not authorize replaying uncertain effects.\n'
            elif response.get('status') == 'observed':
                text = f"The host observed {response['outcome']} for the saved request. This does not satisfy the dependency or authorize a retry.\n"
            else:
                text = (f"A recipient reported {response['outcome']} for the saved request. "
                        "This does not satisfy the dependency or authorize a retry. Review the report and decide how to proceed within the original task.\n")
            if response.get('status') in {'rejected', 'unknown'}:
                text = f"The request report has {response['status']} terminal evidence. This does not satisfy the dependency or authorize replay.\n"
            text += response["text"]
            text += ('\nThis is a retained result for the same objective, not new execution authority. '
                     'Apply the current task constraints and permissions; do not repeat completed or uncertain effects.')
            message = self.service._message(sender, "user", text, "peer", inputId=identity, inputOrigin="peer",
                                            peerEnvelope=envelope)
            receipt = {"accepted": True, "commandAction": "coordination.send", "requestId": identity, "inputId": identity,
                       "messageId": message["id"], "senderSessionId": producer,
                       "target": {"sessionId": sender["id"]}, "protocol": PROTOCOL, "workspace": request["workspace"],
                       "mode": "queue", "delivery": "queued", "interruptionRevision": wait["interruptionRevision"],
                       "taskId": wait["taskId"], "taskRevision": wait["taskRevision"], "dependencyRequestId": request["requestId"],
                       **({'taskContinuationEpoch': wait['taskContinuationEpoch']}
                          if 'taskContinuationEpoch' in wait else {}),
                       "dependencySatisfied": response.get("qualified") is True, "continuationReason": response.get('notificationOutcome') or response["outcome"],
                       'dependencyOutcomeRevision': revision, 'outcomeSnapshot': {key: copy.deepcopy(response[key]) for key in ('outcomeRevision', 'responseRevision', 'operationId', 'status', 'outcome', 'qualified', 'text') if key in response},
                       'expiresAt': time.time()+DEFAULT_QUEUE_SECONDS, 'causalDepth': depth,
                       "origin": "system", "displayBinding": fingerprint([envelope, text])}
            receipt['assignmentId'] = request.get('assignmentId')
            wait.update(status='claimed', lastClaimedRevision=revision,
                        open=versioned and response.get('outcome') in {'deferred', 'question', 'conflict'})
            wait.pop('detail', None)
            self.insert(identity, fingerprint(["continuation", request["requestId"], wait]), receipt)
            self.save(request["requestId"], request)
            self.service._publish_changes(sessions={sender['id'], producer})  # Payload and claim commit together.
            self.expire_later(receipt)
            self.service._task(self.drain(sender["id"]))
        except BaseException:
            if not self.committed(identity):
                self.service.db.rollback()
                sender['messages'] = prior_messages
                if prior_history is None:
                    sender.pop('historyManaged', None)
                else:
                    sender['historyManaged'] = prior_history
                wait.clear()
                wait.update(prior_wait)
                self.refresh_reference(request)
            else:
                # Lost response after durable commit: the known queue still
                # belongs to the service and must not wait for caller retry.
                retained = self.receipt(identity)
                self.expire_later(retained)
                self.service._task(self.drain(sender['id']))
            raise

    def start(self):
        """One recovery pass over known unsubmitted queues, not a scheduler."""
        self.watches.start()
        for (encoded,) in self.service.db.execute('''SELECT receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.subscription.status') IN ('waiting','claimed','backpressured')
                AND (coalesce(json_extract(receipt,'$.outcomeRevision'),0)>coalesce(json_extract(receipt,'$.subscription.lastClaimedRevision'),0)
                    OR json_extract(receipt,'$.subscription.status')='waiting') ''').fetchall():
            self.enqueue_continuation(json.loads(encoded))
        for (encoded,) in self.service.db.execute('''SELECT receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.create'
                AND json_extract(receipt,'$.creationVersion')=1
                AND json_extract(receipt,'$.delivery') IN ('creation_pending','created_initial_pending')'''):
            self.service._task(self.finish_create(json.loads(encoded)['requestId']))
        targets = self.service.db.execute("""SELECT DISTINCT json_extract(receipt,'$.target.sessionId')
            FROM commands WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.delivery')='queued'""").fetchall()
        for (sid,) in targets:
            self.service._task(self.drain(sid))
        for (encoded,) in self.service.db.execute("""SELECT receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.delivery')='queued'
                AND json_extract(receipt,'$.expiresAt') IS NOT NULL""").fetchall():
            self.expire_later(json.loads(encoded))

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
            self.error("Use coordination.create for a task inheriting this conversation's workspace and configuration; raw agent session.create is not authorized. For an independent chat in another folder that the user explicitly requested, use session.handoff.start directly. Use session.handoff.propose for an unapproved suggestion, or session.draft for editable setup. No conversation was created or work started.")
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
            # A child's transport still belongs to the root worker process;
            # that does not make root controls or configuration its authority.
            self.source({}, origin, caller)
            target = target or caller
            if target_key and not args.get("configurationTarget"):
                args[target_key] = target
            elif not args.get("configurationTarget") and not action.startswith("session.") and action != "configuration.apply":
                args["sessionId"] = target
            if target == caller:
                if action == "permissions.save" and "scope" not in args:
                    self.error("Choose an explicit file-access scope: local, project, or global. No settings were changed.", 400)
                return None
            if action == "conversation.send":
                self.error("A user must explicitly authorize a current collaboration grant.")
            self.error("A user must explicitly perform this peer mutation; collaboration does not grant stop, worker or settings authority.")
        if target and target != caller and not action.startswith("coordination."):
            self.error("A user must explicitly perform peer mutations outside a collaboration send grant.")
        return None

    async def dispatch(self, action, args, origin, command_id, caller):
        if action in {'coordination.recap', 'coordination.diagnostics'}:
            from .collaboration_observations import read
            return {'accepted': True, 'result': await read(self, action, args, origin, caller)}
        if action.startswith('coordination.watch'):
            return await self.watches.dispatch(action, args, origin, command_id, caller)
        if action in {"coordination.grant", "coordination.decide", "coordination.revoke"}:
            self.error("Coordination approval APIs are retired. Historical records are read-only.", 410)
        source = self.source(args, origin, caller) if action not in {"coordination.result", "coordination.read"} else None
        if action == 'coordination.cancel':
            from .request_cancellation import cancel
            return await cancel(self, source, args, origin, command_id)
        if action == 'coordination.artifact':
            if source['id'] != args['sessionId']:
                self.error('Read a retained result from the calling conversation.')
            from .result_artifacts import read
            return {'accepted': True, 'result': await read(self, source['id'], args)}
        if action in {'coordination.awareness', 'coordination.intent.read', 'coordination.intent'}:
            from .workspace_awareness import packet, intent_for, declare
            if action == 'coordination.intent':
                return await declare(self, source, args, origin, command_id)
            target = self.service._session(args['sessionId'], hydrate=False)
            if origin not in {'ui', 'user'} and target['workspace'] != source['workspace']:
                self.error('Awareness and shared declarations belong to the calling workspace.')
            if action == 'coordination.intent.read':
                return {'accepted': True, 'result': intent_for(self.service, target)}
            if origin not in {'ui', 'user'} and target['id'] != source['id']:
                self.error('Read workspace awareness from the calling conversation.')
            return {'accepted': True, 'result': packet(self.service, target['id'],
                principal=PRINCIPAL.get(), offset=args.get('offset', 0), limit=args.get('limit', 8))}
        if action == "coordination.read":
            from .owned_history import require, selected
            if origin not in {'ui','user'} and not caller:
                self.error('History reads require a registered calling conversation.')
            scope = selected(args, 'workspace' if caller else 'all')
            reader = caller or (self.service.state.get('selectedSessionId') if 'scope' in args else None) or args['sessionId']
            require(self.service,reader,args['sessionId'],scope)
            if args.get("messageId"):
                target = self.service._session(args["sessionId"])
                await self.service.history.ensure_loaded(target["id"])
                require(self.service,reader,target['id'],scope)
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
                "action": "read", "session_id": args["sessionId"], "scope": scope,
                "offset": args.get("offset", 0), "limit": args.get("limit", 16), "text_limit": args.get("textLimit", 1000),
            }, caller or args["sessionId"])}
        if action == "coordination.context":
            if origin not in {"ui", "user"} and args["sessionId"] != caller:
                self.error("Current coordination context belongs to the caller.")
            result = self.current(args["sessionId"])
            source = self.service._session(args['sessionId'])
            result['humanInput'] = self.current_human_input(source)
            from .workspace_awareness import packet, observed_intent
            result['awareness'] = packet(self.service, source['id'], principal=PRINCIPAL.get())
            result['writeIntent'] = observed_intent(self.service, source)
            workspace = next((w for w in self.service.state['workspaces'] if w['path'] == source['workspace']), None)
            if workspace:
                from .workspace_resources import dispatch_reservation
                resources = await dispatch_reservation(self.service, 'workspace.resources.reservations',
                    {'workspaceId': workspace['id'], 'limit': 16}, origin, None, caller)
                # Explicit reads only: never load transcripts or duplicate this
                # inventory in per-session persistent guidance.
                result['resources'] = {**resources['result'], 'workspaceId': workspace['id']}
            return {"accepted": True, "result": result}
        if action == "coordination.result":
            receipt = self.receipt(args["requestId"])
            if receipt.get("commandAction") not in {"coordination.send", "coordination.create"}:
                self.error("This receipt is not a peer request.", 409)
            sid = receipt.get("target", {}).get("sessionId") or receipt["sessionId"]
            self.require_result_reader(receipt, origin, caller)
            target = next((row for row in self.service.state["sessions"] if row["id"] == sid), None)
            if target:
                await self.service.history.ensure_loaded(sid)
                # Hydration yields to workspace moves and session replacement.
                # Resolve the actual reader and target again before exposing data.
                self.require_result_reader(receipt, origin, caller)
                target = next((row for row in self.service.state['sessions'] if row['id'] == sid), None)
            initial = None
            if receipt.get('commandAction') == 'coordination.create' and receipt.get('initialInputId'):
                row = self.service.db.execute('SELECT receipt FROM commands WHERE id=?', (receipt['initialInputId'],)).fetchone()
                candidate = json.loads(row[0]) if row else {}
                if (candidate.get('commandAction') == 'coordination.send'
                        and candidate.get('senderSessionId') == receipt['senderSessionId']
                        and candidate.get('target', {}).get('sessionId') == sid):
                    initial = candidate
            results = []
            report = initial or receipt
            revisions = [json.loads(row[0])['result'] for row in self.service.db.execute("""SELECT receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.reply'
                AND json_extract(receipt,'$.result.requestId')=? ORDER BY rowid DESC LIMIT 32""", (report['requestId'],)).fetchall()]
            response = report.get("response") or {}
            if response.get("qualified") and response.get("status") == "sealed":
                results.append({"messageId": response["terminalMessageId"], "sessionId": sid,
                                "inputId": (initial or receipt)['requestId'], "declaration": response})
            from .request_cancellation import status as cancellation_status
            return {"accepted": True, "result": {"receipt": receipt, "results": results,
                'cancellation': cancellation_status(report),
                'responseRevision': report.get('responseRevision', 1 if response else 0), 'revisions': revisions,
                **({'initialRequest': initial} if initial is not None else {}),
                "preparation": copy.deepcopy((target or {}).get("activity")),
                "qualified": bool(results), "qualificationSupported": True,
                "detail": "Typed agent declaration plus host terminal evidence; not independently verified correctness."}}
        if action == "coordination.send":
            await self.service.history.ensure_loaded(args["sessionId"])
        identity = command_id or str(uuid.uuid4())
        digest = fingerprint([action, args, origin, source["id"]])
        prepared_artifacts = None
        if action == 'coordination.reply':
            from .result_artifacts import prepare
            prepared_artifacts = await prepare(self, source, args, origin, identity, digest)
            if prepared_artifacts.get('duplicate'):
                return prepared_artifacts['duplicate']
        async with self.service.lock, AsyncExitStack() as recovery_boundary:
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
                operation = self.receipt(creation[4]['requestId'])
                if operation.get('creationVersion') == 1:
                    reason = self.creation_guard(operation)
                    if reason:
                        self.error(reason, 409)
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
                self.require_inbox_slot(source['id'])
                wait = self.saved_wait(source, args['requestId'])
                request["subscription"] = wait
                self.save(args["requestId"], request)
                receipt = {"accepted": wait["supported"], "commandAction": action, "result": wait}
            elif action == "coordination.reply":
                if origin != "agent":
                    self.error("Response declarations require the actual recipient agent generation.")
                active, request, revision, delivered = self.validate_reply(source, args)
                response = {**copy.deepcopy(args), 'generationId': active['id'], 'recipientSessionId': source['id'],
                            'operationId': identity, 'responseRevision': revision+1,
                            'causalDepth': self.causal_depth(source, lineage, origin),
                            'admittedInputIds': sorted(delivered),
                            'executionDirectory': source.get('workingDirectory') or source['workspace'],
                            'status': 'staged', 'qualified': False, 'declaredAt': time.time()}
                from .result_artifacts import store
                # Keep the reply, output relationships and resource roots in one
                # owner transaction. Prior unrelated pending receipts survive a
                # failed artifact save; a lost post-commit acknowledgment does
                # not discard retained bytes or repeat their capture.
                self.service.db.commit()
                original = copy.deepcopy(request)
                try:
                    await store(self, source, request, response, prepared_artifacts, identity)
                    request['responseRevision'] = revision+1
                    request['response'] = response
                    if response_ready(response):
                        self.record_outcome(request, response)
                    self.save(args['requestId'], request)
                    receipt = {'accepted': True, 'commandAction': action, 'result': response}
                    self.insert(identity, digest, receipt)
                    self.publish_receipt(receipt)
                except BaseException:
                    if self.service.db.in_transaction:
                        self.service.db.rollback()
                        self.refresh_reference(original)
                    raise
                if continuation_outcome(request) is not None and request.get('subscription'):
                    self.enqueue_continuation(request)
                return receipt
            elif action == "coordination.create":
                if source.get('fileAccessPending'):
                    self.error('Finish applying the source chat’s file settings before creating another task.', 409)
                # Creation is handled outside the service lock below.
                self.require_continuation()
                receipt = None
            else:
                target = self.authorize(source, args)
                reason = self.service.assignments.blocked(source)
                if reason:
                    self.error(reason, 409)
                mode = args.get("mode", "queue")
                if args.get('resume') and (origin != 'agent' or mode != 'queue'):
                    self.error('Resume requires a current human-rooted agent request using queue.')
                self.service.assignments.require_request(source, target, mode)
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
                self.require_message_rate(source['id'], target['id'])
                depth = self.causal_depth(source, lineage, origin)
                if mode != 'notify':
                    self.require_causal_depth(depth)
                    self.require_inbox_slot(target['id'], source['id'], source.get('assignmentId') or (source.get('collaboration') or {}).get('assignmentId'))
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
                repair = None
                resume = None
                if args.get('resume'):
                    from .peer_resume import prepare
                    resume = prepare(self.service, source, target, args['resume'], lineage, recovery_boundary)
                    envelope['humanResume'] = {
                        'sourceMessageId': resume['sourceMessageId'],
                        'instruction': 'The host verified a fresh human resume request in the commissioning owner. '
                            'Reconcile completed effects first. You may resume your own paused task if its blocker '
                            'is resolved. Keep completed objectives completed; only establish follow-on work '
                            'within the human request. This does not answer questions, grant permissions or reset budgets.'}
                if args.get('scheduleRepair'):
                    if origin != 'agent' or mode == 'notify':
                        self.error('Schedule repair requires a current human-rooted agent handoff using queue or steer.')
                    from .schedule_handoff import prepare
                    repair = prepare(self.service, source, target, args['scheduleRepair'], lineage)
                target["historyManaged"] = False
                message = self.service._message(target, "user", args["text"], "peer", inputId=identity,
                                               inputOrigin="peer", peerEnvelope=envelope)
                receipt = {"accepted": True, "commandAction": "coordination.send", "requestId": identity,
                    "inputId": identity, "messageId": message["id"], "senderSessionId": source["id"],
                    "target": {"sessionId": target["id"]}, "protocol": PROTOCOL, "workspace": source["workspace"], **lineage,
                    "mode": mode, 'causalDepth': depth, "delivery": "notified" if mode == "notify" else "queued",
                    "interruptionRevision": target.get("interruptionRevision", 0),
                    "taskId": task.get("id"), "taskRevision": task.get("revision"),
                    'replyTaskContinuationEpoch': continuation_epoch(task),
                    # Dispatcher provenance is host-owned. Never copy actor
                    # claims from args, message prose, or the peer envelope.
                    **({'expiresAt': time.time()+args.get('expiresInSeconds', DEFAULT_QUEUE_SECONDS)} if mode != 'notify' else {}),
                    "origin": origin if origin in {"agent", "ui", "user"} else None,
                    "displayBinding": fingerprint([envelope, args["text"]])}
                receipt['assignmentId'] = (self.service.assignments.current(source) or {}).get('id')
                if repair:
                    receipt['scheduleRepair'] = repair
                if resume:
                    receipt['humanResume'] = resume
                if mode == "steer":
                    receipt.update(delivery="pending_steer", targetGenerationId=active["id"])
                if creation and identity == creation[4]['requestId'] + ':brief':
                    operation = self.receipt(creation[4]['requestId'])
                    if operation.get('creationVersion') == 1:
                        receipt['creationOperationId'] = operation['requestId']
                        wait = operation.get('subscription')
                        if wait:
                            receipt['subscription'] = {**wait, 'requestId': identity, 'status': 'waiting'}
                            operation['subscription'] = {**wait, 'requestId': identity, 'status': 'linked', 'open': False}
                        operation.update(delivery='created', phase='input_committed', initialDelivery=receipt['delivery'],
                                         detail='Peer, initial request and return policy committed before admission.')
                        self.save(operation['requestId'], operation)
                        self.service.db.execute('DELETE FROM collaboration_preparations WHERE request_id=?', (operation['requestId'],))
            if receipt is not None:
                self.insert(identity, digest, receipt)
                self.publish_receipt(receipt)
                if action in {'coordination.reply', 'coordination.subscribe'} and continuation_outcome(request) is not None and request.get('subscription'):
                    self.enqueue_continuation(request)
        if action == "coordination.create":
            return await self.create(source, args, origin, identity, digest, lineage)
        if receipt.get("delivery") == "pending_steer":
            return await self.submit_steer(identity)
        if receipt.get("delivery") == "queued":
            # The committed receipt owns the operation, not the request socket.
            self.expire_later(receipt)
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
            "workingDirectory": source.get("workingDirectory") or source["workspace"],
            "outputNamespace": "working-files/tasks/" + new_id, "brief": args["text"], "references": args.get("references", [])}
        async with self.service.lock, self.service.assignments.mutation(source, identity):
            duplicate = self.duplicate(identity, digest)
            if duplicate:
                return duplicate
            from .test_execution_settings import concurrency
            maximum = concurrency(self.service)['maxTaskChatsPerSource']
            if maximum:
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
                # Idle presentation can be nonresident while a durable task or an
                # unsubmitted input still occupies a slot. Count exact directory
                # owners without admitting or reading unrelated saved bodies.
                sql, bound = self.service.directory.effective()
                outstanding.update(row[0] for row in self.service.db.execute(sql + """
                    SELECT d.id FROM directory_rows d
                    WHERE json_extract(d.summary,'$.collaboration.creatorSessionId')=?
                    AND (json_extract(d.summary,'$._coordinationTaskActive')=1 OR EXISTS(
                        SELECT 1 FROM commands c
                        WHERE json_extract(c.receipt,'$.commandAction')='coordination.send'
                        AND json_extract(c.receipt,'$.target.sessionId')=d.id
                        AND json_extract(c.receipt,'$.delivery') IN ('queued','submitting','pending_steer')))
                    LIMIT ?""", (*bound, source['id'], maximum)))
                pending = self.service.db.execute("""SELECT receipt FROM commands
                    WHERE json_extract(receipt,'$.commandAction')='coordination.create'
                    AND json_extract(receipt,'$.senderSessionId')=?
                    AND json_extract(receipt,'$.protocol')=?
                    AND json_extract(receipt,'$.delivery') IN ('creation_pending','created_initial_pending')""",
                    (source["id"], PROTOCOL)).fetchall()
                outstanding.update(json.loads(row[0])["sessionId"] for row in pending)
                if len(outstanding) >= maximum:
                    self.error(f"The limit of {maximum} outstanding task chats per source has been reached. Adjust Advanced execution limits in Settings to change it.", 409)
            if not args['title'].strip() or not args['text'].strip():
                self.error('Enter a task title and brief.', 400)
            self.require_causal_depth(self.causal_depth(source, lineage, origin))
            notify = args.get('returnNotification', True)
            if notify:
                self.require_inbox_slot(source['id'])
            assignment_id = self.service.assignments.reserve_member(source, new_id, identity)
            metadata['assignmentId'] = assignment_id
            task = source.get('task') or self.service.coordination.task(source['id'])
            receipt = {"accepted": True, "commandAction": "coordination.create",
                "requestId": identity, "senderSessionId": source["id"], "workspace": source["workspace"], "protocol": PROTOCOL,
                "sessionId": new_id, "delivery": "creation_pending", "result": metadata,
                'creationVersion': 1, 'phase': 'planned', 'title': args['title'], 'origin': origin,
                **lineage,
                'sourceTaskId': task.get('id'), 'sourceTaskRevision': task.get('revision'),
                "initialInputId": identity + ":brief", "detail": "Creation committed; preparation is owned by the service."}
            receipt['assignmentId'] = assignment_id
            if notify:
                receipt['subscription'] = self.saved_wait(source, identity, status='reserved')
            self.service.db.execute('INSERT INTO collaboration_preparations(request_id,configuration) VALUES(?,?)',
                                    (identity, json.dumps(config)))
            self.insert(identity, digest, receipt)
            self.service._save_changes(sessions={source['id']})
        self.service._task(self.finish_create(identity))
        return receipt

    def end_creation(self, receipt, delivery, detail, *, notify=False):
        if receipt.get('assignmentId') and delivery == 'suppressed' and receipt.get('phase') == 'planned':
            assignment = self.service.assignments.load(receipt['assignmentId'])
            assignment['members'][receipt['sessionId']]['status'] = 'not_created'
            assignment['revision'] += 1
            self.service.assignments.save(assignment)
        receipt.update(delivery=delivery, detail=detail)
        wait = receipt.get('subscription')
        if wait and wait.get('status') == 'reserved':
            wait['status'] = 'waiting' if notify else 'suppressed'
        if notify:
            receipt['terminalOutcome'] = {'status': 'observed', 'outcome': 'unknown' if delivery == 'unknown' else 'failed',
                'qualified': False, 'requestId': receipt['requestId'], 'text': detail, 'observedAt': time.time()}
        self.save(receipt['requestId'], receipt)
        if notify and wait:
            self.enqueue_continuation(receipt)
        self.publish_receipt(receipt)

    async def finish_create(self, identity):
        """Resume only steps backed by the private plan and durable effect receipts."""
        if identity in self.creating:
            return
        self.creating.add(identity)
        try:
            receipt = self.receipt(identity)
            if receipt.get('delivery') not in {'creation_pending', 'created_initial_pending'}:
                return
            reason = self.creation_guard(receipt)
            if reason:
                self.end_creation(receipt, 'suppressed', reason)
                return
            if receipt.get('phase') not in {'planned', 'creating_root', 'root_retained'}:
                self.end_creation(receipt, 'unknown', 'The preparation phase is unsupported. Retained effects were not replayed.', notify=True)
                return
            row = self.service.db.execute('SELECT configuration FROM collaboration_preparations WHERE request_id=?', (identity,)).fetchone()
            if not row:
                self.end_creation(receipt, 'unknown', 'The captured preparation plan is unavailable. Retained effects were not replayed.', notify=True)
                return
            config = json.loads(row[0])
            if fingerprint(config) != receipt['result']['configurationHash']:
                self.end_creation(receipt, 'unknown', 'The captured preparation plan changed. Retained effects were not replayed.', notify=True)
                return
            source = self.service._session(receipt['senderSessionId'])
            args = {'title': receipt['title'], 'text': receipt['result']['brief'], 'references': receipt['result']['references']}
            await self._finish_create(source, config, args, receipt['origin'], identity, receipt['sessionId'], receipt['result'])
        except asyncio.CancelledError:
            # Phases already precede effects; orderly shutdown need not destroy
            # the evidence required to resume a still-unperformed step.
            raise
        except BaseException as exc:
            receipt = self.receipt(identity)
            # A lost acknowledgment after the input transaction is not a lost
            # operation. Keep its exact admitted/queued receipt untouched.
            if receipt.get('phase') != 'input_committed':
                receipt['errorType'] = type(exc).__name__
                reason = self.creation_guard(receipt)
                if reason:
                    self.end_creation(receipt, 'suppressed', reason)
                else:
                    self.end_creation(receipt, 'unknown', 'Preparation did not confirm all effects. Inspect the retained operation; no effects were replayed.', notify=True)
        finally:
            self.creating.discard(identity)

    async def _finish_create(self, source, config, args, origin, identity, new_id, metadata):
        values = {"id": new_id, "title": args["title"], "workspace": source["workspace"],
                  "bundle": config["bundle"], "select": False}
        receipt = self.receipt(identity)
        if receipt['phase'] == 'creating_root':
            # Only a committed subordinate receipt establishes root creation;
            # partial files or a matching-looking title do not prove completion.
            row = self.service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity + ':create',)).fetchone()
            created = json.loads(row[0]) if row else {}
            if created.get('sessionId') != new_id or not created.get('accepted'):
                self.end_creation(receipt, 'unknown', 'Root creation has no committed acknowledgment. Existing files and history were retained without replay.', notify=True)
                return
        elif receipt['phase'] == 'planned':
            receipt['phase'] = 'creating_root'
            self.save(identity, receipt)
            self.service.db.commit()
            token = CREATION.set((source["id"], fingerprint(values), None, config, metadata))
            try:
                await self.service.dispatch("session.create", values, origin, identity + ":create",
                                            include_state=False, caller_session_id=source["id"])
            finally:
                CREATION.reset(token)
        async with self.service.lock:
            target = self.service._session(new_id)
            if (target.get('collaboration') != metadata or target['workspace'] != receipt['workspace']
                    or (target.get('workingDirectory') or target['workspace']) != metadata['workingDirectory']):
                self.error('The retained root does not match this creation operation.', 409)
            directory = self.service.data_dir / 'sessions' / (target.get('runtimeSessionId') or target['id'])
            for filename, expected in [('configuration.json', config['plan']), ('control-state.json', config['controls'])]:
                if json.loads((directory / filename).read_text()) != expected:
                    self.error('The retained root configuration changed before initial input.', 409)
            receipt.update(delivery='created_initial_pending', phase='root_retained',
                           detail='Root retained. Initial input and return policy are being committed.')
            if receipt.get('assignmentId'):
                assignment = self.service.assignments.load(receipt['assignmentId'])
                assignment['members'][new_id]['status'] = 'bound'
                self.service.assignments.save(assignment)
            self.save(identity, receipt)
            self.publish_receipt(receipt)
        # The durable brief is ordinary attributed input, not copied history.
        token = CREATION.set((source["id"], fingerprint(values), None, config, metadata))
        try:
            await self.dispatch("coordination.send", {"sessionId": new_id, "senderSessionId": source["id"],
                "text": args["text"], "references": args.get("references", []),
                "mode": "queue"}, origin, identity + ":brief", source["id"])
        finally:
            CREATION.reset(token)
        return self.receipt(identity)
