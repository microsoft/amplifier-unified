"""Thin opt-in adapter over hot canonical host observations; no history reads."""
import asyncio
import os
import time
import logging
from itertools import islice

from amplifier_web.status_protocol import Publisher
from amplifier_web.status_protocol.hook import accepted_plan


class StatusEmitter:
    def __init__(self, service, directory):
        from . import __version__
        self.service = service
        self.publisher = Publisher(directory, host="unified", version=__version__, instance_id=service.instance_id)
        self.plans = {}
        self.dirty = set()
        self.timer = None
        self.closed = False
        self.tracked = {}
        self.cursors = {}
        self.omitted_batches = 0
        self.overflow = False

    def execution(self, session, payload):
        if payload.get("sessionId") not in {session['id'], session.get('runtimeSessionId')}:
            return
        if payload.get("kind") == "tool" and payload.get("label") == "todo" and 'statusPlan' in payload:
            if session['id'] in self.plans or len(self.plans) < 256:
                self.plans[session['id']] = accepted_plan({'todos': payload['statusPlan']})

    def publish(self, session_ids):
        if self.closed:
            return
        if session_ids is None:
            self.dirty = None
        elif self.dirty is not None:
            # Bound admission work even if a host batch carries many identities.
            self.dirty.update(islice(session_ids, 257))
            if len(self.dirty) > 256:
                self.omitted_batches += 1
                self.dirty = None
        if self.timer is None:
            try:
                self.timer = asyncio.get_running_loop().call_later(.1, self._flush)
            except RuntimeError:
                pass  # Construction without an event loop does not do IO.

    def _flush(self):
        self.timer = None
        ids, self.dirty = self.dirty, set()
        try:
            self._project(ids)
        except Exception:
            # Optional observations cannot fail a committed host operation.
            self.publisher.failures += 1

    def _batch(self, label, values):
        # Retain only an iterator, not a copied catalog. A stable large active
        # map rotates across ticks; mutation restarts safely and reports overflow.
        if len(values) <= 256:
            self.cursors.pop(label, None)
            return list(values)
        cursor = self.cursors.setdefault(label, iter(values))
        result = []
        for _ in range(256):
            try:
                result.append(next(cursor))
            except (StopIteration, RuntimeError):
                cursor = self.cursors[label] = iter(values)
                try:
                    result.append(next(cursor))
                except StopIteration:
                    break
        return result

    def _project(self, session_ids):
        # The existing resident index does not hydrate old sessions. Reading
        # dict.get explicitly bypasses ColdRecord's lazy detail loader.
        index = self.service.projections.sessions(self.service._state).by_id
        selected = self.service._state.get('selectedSessionId')
        runtime = getattr(self.service, 'runtime', None)
        # A complete publication visits current runtime owners, selected and
        # previously observed sessions. It never enrolls historical residents.
        workers = getattr(runtime, 'workers', {})
        preparations = getattr(runtime, '_preparations', {})
        candidates = self._batch('workers', workers) if session_ids is None else list(islice(session_ids, 256))
        candidates += self._batch('preparations', preparations)
        upper = len(workers) + len(preparations)
        self.overflow = upper > 256
        self.publisher.adapter_coverage = {
            'scope': 'current_runtime_selected_and_tracked',
            'candidateUpperBound': upper, 'candidateOmittedLowerBound': max(0, max(len(workers), len(preparations))-256),
            'oversizedDirtyBatches': self.omitted_batches,
            'complete': not self.overflow and self.omitted_batches == 0,
            'policy': 'rotating_bounded_observations',
        }
        # New candidates first, then existing records for terminal transitions.
        ids = list(dict.fromkeys(([selected] if selected else []) + candidates + list(self.tracked)))
        retired = set()
        pending_admission = 0
        for identity in ids:
            if identity in retired:
                continue
            session = index.get(identity)
            if not session:
                self.publisher.end(identity)
                self.tracked.pop(identity, None)
                self.plans.pop(identity, None)
                continue
            raw = lambda key, default=None: dict.get(session, key, default)
            activity = raw("activity", {}) or {}
            status = raw("status", "unknown")
            approvals = raw("approvals", []) or []
            questions = raw("questions", []) or []
            pending_approvals = sum(row.get("status") == "pending" for row in approvals[:200])
            pending_questions = sum(row.get("status") == "pending" for row in questions[:200])
            tools = activity.get("activeTools", [])
            active = status in {'working', 'running', 'starting', 'stopping', 'ready'} or tools or pending_approvals or pending_questions
            if not active and identity != selected:
                if identity not in self.tracked:
                    continue
                deadline = self.tracked[identity]
                if deadline is None:
                    self.tracked[identity] = time.monotonic() + 30
                elif time.monotonic() >= deadline:
                    self.publisher.end(identity)
                    self.tracked.pop(identity, None)
                    self.plans.pop(identity, None)
                    continue
            elif active and len(self.tracked) + pending_admission >= 256 and identity not in self.tracked:
                # Prefer new work over a settled observation awaiting retirement.
                terminal = next((key for key, deadline in self.tracked.items() if deadline is not None), None)
                terminal = terminal or next((key for key in self.tracked if key != selected), None)
                if terminal:
                    if self.tracked[terminal] is not None:
                        self.publisher.end(terminal)
                    else:
                        self.publisher.suspend(terminal)
                    retired.add(terminal)
                    self.tracked.pop(terminal, None)
                    self.plans.pop(terminal, None)
            elif not active and identity not in self.tracked and identity != selected:
                continue
            state = ("waiting_approval" if pending_approvals else "waiting_person" if pending_questions else
                     "tool" if tools else {"working": "thinking", "starting": "preparing", "ready": "idle",
                                          "stopped": "idle", "running": "thinking"}.get(status, status))
            execution = raw("execution", {}) or {}
            plan_items = self.plans.get(identity)
            if identity not in self.plans and isinstance(execution.get("plan"), dict):
                plan_items = execution["plan"].get("items")
            admitted = self.publisher.submit(identity, {
                "where": {"parentSessionId": raw("parentId"), "workspace": raw("workspace"),
                          "cwd": raw("workingDirectory") or raw("workspace"), "bundle": raw("bundle"),
                          "provider": (raw("selection", {}) or {}).get("instance"),
                          "model": (raw("selection", {}) or {}).get("model")},
                "now": {"status": state, "since": activity.get("startedAt"),
                        "turnId": execution.get("currentTurnId"), "pendingQuestions": pending_questions,
                        "pendingApprovals": pending_approvals,
                        "tools": [{"id": row.get("callId"), "name": row.get("tool"), "startedAt": row.get("startedAt")}
                                  for row in tools[:32]],
                        "workers": [{"sessionId": row.get("id"), "status": row.get("status")}
                                    for row in (raw("workers", []) or [])[:32]]},
                "planItems": plan_items, "error": status == "error",
            })
            if admitted:
                self.tracked.setdefault(identity, None)
                if active or identity == selected:
                    self.tracked[identity] = None
            elif not self.closed:
                if identity not in self.tracked:
                    pending_admission += 1
                if self.dirty is not None and len(self.dirty) < 256:
                    self.dirty.add(identity)
        if self.overflow:
            self.dirty = None
        if not self.closed and (self.overflow or self.dirty or any(deadline is not None for deadline in self.tracked.values())):
            self.timer = asyncio.get_running_loop().call_later(1, self._flush)

    async def close(self):
        self.closed = True
        if self.timer:
            self.timer.cancel()
            self._flush()
        await asyncio.to_thread(self.publisher.close)


def create(service):
    directory = os.environ.get("AMPLIFIER_STATUS_DIR")
    if directory:
        try:
            return StatusEmitter(service, directory)
        except Exception:
            logging.getLogger(__name__).warning('Optional status emitter could not start; check directory and separate package ownership.')
    return None
