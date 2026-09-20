"""Independent client presentation over shared session/runtime state.

These records are disposable UI state, not conversation history. Binding is
task-local: an awaited action cannot borrow another browser's current selection.
"""
from __future__ import annotations

import copy
from collections.abc import MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
import json
import re
import time


LOCAL_KEYS = frozenset({"view", "selectedSessionId", "selectedWorkspaceId", "canvas", "deviceCommands"})


class ClientState(MutableMapping):
    def __init__(self, shared, local):
        self.shared, self.local = shared, local

    def __getitem__(self, key):
        return (self.local if key in LOCAL_KEYS else self.shared)[key]

    def __setitem__(self, key, value):
        (self.local if key in LOCAL_KEYS else self.shared)[key] = value

    def __delitem__(self, key):
        del (self.local if key in LOCAL_KEYS else self.shared)[key]

    def __iter__(self):
        return iter(self.shared.keys() | (self.local.keys() & LOCAL_KEYS))

    def __len__(self):
        return len(set(self))

    def __deepcopy__(self, memo):
        return copy.deepcopy(dict(self), memo)


class ClientViews:
    def __init__(self, service):
        self.service = service
        self.current = ContextVar("amplifier_client", default=None)
        self.records = {}
        self.saved = {}
        service.db.execute("CREATE TABLE IF NOT EXISTS client_views (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        for identity, value in service.db.execute("SELECT id,value FROM client_views"):
            self.records[identity] = json.loads(value)
            self.saved[identity] = value

    @staticmethod
    def validate(identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identity):
            from .service import AppError
            raise AppError("Choose a valid client ID.")
        return identity

    def attach(self, identity, resume=None, kind="web"):
        from .service import AppError
        self.validate(identity)
        if kind not in {"web", "tui", "native", "api"}:
            raise AppError("Unsupported client kind.")
        if resume is not None:
            self.validate(resume)
        if identity not in self.records:
            if len(self.records) >= 256:
                active = set(self.service.queue_clients.values()) | {self.current.get(), resume}
                candidates = [(row.get('updatedAt', 0), key) for key, row in self.records.items() if key not in active]
                if not candidates:
                    raise AppError("Too many attached clients. Close an unused client first.", 409)
                _, oldest = min(candidates)
                self.records.pop(oldest)
                self.saved.pop(oldest, None)
                self.service.db.execute("DELETE FROM client_views WHERE id=?", (oldest,))
            previous = self.records.get(resume)
            if previous:
                record = copy.deepcopy(previous)
            else:
                shared = self.service._state
                record = {key: copy.deepcopy(shared.get(key)) for key in LOCAL_KEYS}
                record["view"] = record.get("view") or {}
                record["canvas"] = record.get("canvas") or {}
                record["drafts"] = {}
                record["attachments"] = {}
                # Preserve pre-client drafts during the first migration only.
                if not shared.get("clientViewsMigrated"):
                    for session in shared.get("sessions", []):
                        if session.get("draft"):
                            record["drafts"][session["id"]] = session["draft"]
                        if session.get("draftAttachments"):
                            record["attachments"][session["id"]] = copy.deepcopy(session["draftAttachments"])
                    if record["selectedSessionId"] and record["view"].get("draft"):
                        record["drafts"][record["selectedSessionId"]] = record["view"]["draft"]
                    shared["clientViewsMigrated"] = True
                else:
                    record["view"]["draft"] = ""
                    record["view"]["panel"] = None
            record.update(kind=kind, updatedAt=time.time(), deviceCommands=[])
            self.records[identity] = record
        self.reconcile(identity)
        return self.records[identity]

    def reconcile(self, identity):
        record = self.records[identity]
        sessions = {row["id"]: row for row in self.service._state.get("sessions", [])}
        sid = record.get("selectedSessionId")
        if sid is not None and sid not in sessions:
            record["selectedSessionId"] = None
            record["canvas"] = {}
        workspaces = {row["id"] for row in self.service._state.get("workspaces", [])}
        if record.get("selectedWorkspaceId") not in workspaces:
            record["selectedWorkspaceId"] = self.service._state.get("selectedWorkspaceId")
        record["view"]["draft"] = record.get("drafts", {}).get(record.get("selectedSessionId"), "")

    @contextmanager
    def bind(self, identity):
        if identity is not None:
            self.validate(identity)
            if identity not in self.records:
                from .service import AppError
                raise AppError("Attach this client before using the session service.", 409, code="client_not_attached")
            self.reconcile(identity)
        token = self.current.set(identity)
        try:
            yield
        finally:
            self.current.reset(token)

    def state(self, shared):
        identity = self.current.get()
        return ClientState(shared, self.records[identity]) if identity is not None else shared

    def record(self):
        identity = self.current.get()
        return self.records.get(identity) if identity is not None else None

    def draft(self, session_id, text):
        record = self.record()
        record.setdefault("drafts", {})[session_id] = text
        if session_id == record.get("selectedSessionId"):
            record["view"]["draft"] = text

    def attachments(self, session):
        record = self.record()
        if record is None:
            return session.setdefault("draftAttachments", [])
        return record.setdefault("attachments", {}).setdefault(session["id"], [])

    def project(self, snapshot):
        record = self.record()
        if record is None:
            return snapshot
        snapshot["client"] = {"id": self.current.get(), "kind": record["kind"], "protocolVersion": 1,
                              "hostInstanceId": self.service.instance_id, "reconnect": "snapshot"}
        snapshot["sessions"] = [dict(row) for row in snapshot.get("sessions", [])]
        for session in snapshot["sessions"]:
            session["draft"] = record.get("drafts", {}).get(session["id"], "")
            session["draftAttachments"] = copy.deepcopy(record.get("attachments", {}).get(session["id"], []))
        return snapshot

    def save(self):
        for identity, record in self.records.items():
            value = copy.deepcopy(record)
            # The artifact store already owns large bodies. A presentation
            # record keeps only the currently selected artifact and controls.
            canvas = value.get("canvas", {})
            artifact = next((a for a in self.service._state.get("canvasArtifacts", []) if a["id"] == canvas.get("id")), None)
            if artifact and artifact.get("body"):
                canvas["contentResource"] = artifact["body"]
                canvas.pop("content", None)
                canvas.pop("surface", None)
            encoded = json.dumps(value)
            if self.saved.get(identity) != encoded:
                self.service.db.execute("INSERT OR REPLACE INTO client_views VALUES (?,?)", (identity, encoded))
                self.saved[identity] = encoded
