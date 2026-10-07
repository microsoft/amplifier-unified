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


LOCAL_KEYS = frozenset({"view", "selectedSessionId", "selectedWorkspaceId", "canvas", "canvasTabs", "deviceCommands"})


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

    def copy(self):
        return dict(self)

    def __deepcopy__(self, memo):
        return copy.deepcopy(dict(self), memo)


class ClientViews:
    def __init__(self, service):
        self.service = service
        self.current = ContextVar("amplifier_client", default=None)
        self.records = {}
        self.saved = {}
        self.dirty = set()
        self.startup_selections = {}
        service.db.execute("CREATE TABLE IF NOT EXISTS client_views (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        for identity, value in service.db.execute("SELECT id,value FROM client_views"):
            self.records[identity] = json.loads(value)
            self.records[identity]["canvasTabs"] = self.records[identity].get("canvasTabs") or {}
            self.saved[identity] = value
            self.startup_selections[identity] = (
                self.records[identity].get('selectedSessionId'),
                self.records[identity].get('selectedWorkspaceId'))

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
                    if record["view"].get("draft"):
                        record["drafts"][record["selectedSessionId"] or ""] = record["view"]["draft"]
                    shared["clientViewsMigrated"] = True
                else:
                    record["view"]["draft"] = ""
                    record["view"]["panel"] = None
            record.update(kind=kind, updatedAt=time.time(), deviceCommands=[])
            record["canvasTabs"] = record.get("canvasTabs") or {}
            self.records[identity] = record
            self.dirty.add(identity)
            if resume:
                shell = self.service.shell.get("client", resume)
                if shell:
                    shell.update(preview=None, reported=None)
                    self.service.shell.put("client", identity, shell)
        self.reconcile(identity)
        self.selection_revision(identity)
        return self.records[identity]

    def reconcile(self, identity):
        from .canvas_apps import sync
        sync(self.service)
        record = self.records[identity]
        self.dirty.add(identity)
        self.service.computer_visual.reconcile(identity)
        sid = record.get("selectedSessionId")
        projections = getattr(self.service, '_projections', None)
        index = projections.values.get(('session-index',)) if projections is not None else None
        rows = self.service._state.get('sessions', [])
        position = index.positions.get(sid) if index is not None else None
        if index is not None and (len(rows) != len(index.positions) or sid is not None and
                (position is None or position >= len(rows) or rows[position] is not index.by_id.get(sid))):
            # A command can alter membership before publication invalidates its
            # saved index. Reconcile against live membership, never stale cache.
            index = None
        selected = (index.by_id.get(sid) if index is not None else
                    next((row for row in rows if row['id'] == sid), None))
        catalog_loading = self.service._state.get('sharedHistory', {}).get('loading', False)
        startup = self.startup_selections.get(identity)
        awaiting_catalog = bool(catalog_loading and startup and startup[0] == sid)
        if not catalog_loading or selected is not None:
            self.startup_selections.pop(identity, None)
        if sid is not None and selected is None and not awaiting_catalog:
            record["selectedSessionId"] = None
            record["canvas"] = {}
        self.service.computer_visual.reconcile(identity)
        from .managed_chats import is_managed
        selected = selected or {}
        managed = is_managed(selected) or (sid is None and is_managed(record.get("view", {}).get("newSessionDraft", {})))
        workspace = record.get("selectedWorkspaceId")
        if managed and workspace is None:
            pass
        else:
            workspaces = self.service._state.get('workspaces', [])
            position = index.workspace_positions.get(workspace) if index is not None else None
            indexed_workspace = (index.workspaces.get(workspace) if index is not None
                                 and len(workspaces) == len(index.workspaces) and position is not None
                                 and position < len(workspaces) and workspaces[position] is index.workspaces.get(workspace)
                                 else None)
            available = indexed_workspace is not None or any(row['id'] == workspace for row in workspaces)
            if not available and not (awaiting_catalog and startup[1] == workspace):
                record["selectedWorkspaceId"] = self.service._state.get("selectedWorkspaceId")
        if record.get('selectedSessionId') is None:
            # Older clients could persist an open Canvas without a chat. Hide
            # that presentation on reconnect; never discard its saved content.
            if record.get('canvas', {}).get('open'):
                record['canvas']['open'] = False
            if record['view'].get('canvasFocused'):
                record['view']['canvasFocused'] = False
        from .canvas_library import restore_body
        restore_body(record.get("canvas", {}), self.service.db)
        # The empty key is the client's pre-conversation draft. Unlike None,
        # it keeps its identity through JSON persistence and reload.
        record["view"]["messageReply"] = copy.deepcopy(record.get("messageReplies", {}).get(record.get("selectedSessionId")))
        draft = record.get("drafts", {}).get(record.get("selectedSessionId") or "", "")
        if record["view"].get("draft") != draft:
            record["view"]["draft"] = draft
            self.dirty.add(identity)

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
        if identity is not None:
            self.dirty.add(identity)
            return ClientState(shared, self.records[identity])
        return shared

    def record(self):
        identity = self.current.get()
        if identity is not None:
            self.dirty.add(identity)
        return self.records.get(identity) if identity is not None else None

    def draft(self, session_id, text):
        record = self.record()
        record.setdefault("drafts", {})[session_id or ""] = text
        if session_id == record.get("selectedSessionId"):
            record["view"]["draft"] = text

    def attachments(self, session):
        record = self.record()
        if record is None:
            if session is None:
                from .service import AppError
                raise AppError('Attach a client to add files before starting a chat.')
            return session.setdefault("draftAttachments", [])
        return record.setdefault("attachments", {}).setdefault(session["id"] if session else "", [])

    def project(self, snapshot):
        record = self.record()
        if record is None:
            return snapshot
        if feedback := getattr(self.service, "feedback", None):
            snapshot["feedback"] = feedback.followups.project(snapshot.get("feedback", {}))
        snapshot.pop("canvasTabs", None)
        snapshot['canvasWorkspace'] = self.service.canvas_views.project()
        from .canvas_library import presentation
        snapshot["canvasArtifacts"] = [{**row, **copy.deepcopy(presentation(self.service.state, row))}
                                       for row in snapshot.get("canvasArtifacts", [])]
        snapshot["client"] = {"id": self.current.get(), "kind": record["kind"], "protocolVersion": 1,
                              "hostInstanceId": self.service.instance_id, "reconnect": "snapshot"}
        snapshot["computerVisual"] = self.service.computer_visual.project(self.current.get())
        snapshot["draftAttachments"] = copy.deepcopy(record.get("attachments", {}).get("", []))
        from .cold_display import detached
        snapshot["sessions"] = [detached(row) for row in snapshot.get("sessions", [])]
        for session in snapshot["sessions"]:
            session["draft"] = record.get("drafts", {}).get(session["id"], "")
            session["draftAttachments"] = copy.deepcopy(record.get("attachments", {}).get(session["id"], []))
        return snapshot

    def remember_canvas(self, record=None):
        """Keep only presentation, never another copy of an artifact body."""
        record = record if record is not None else self.record()
        if record is None or not record.get('selectedSessionId'):
            return
        canvas = record.get('canvas', {})
        record.setdefault('chatCanvas', {})[record['selectedSessionId']] = {
            'canvas': {key: copy.deepcopy(canvas[key]) for key in
                       ('id', 'open', 'selectedVersion', 'view') if key in canvas},
            'view': {key: copy.deepcopy(value) for key, value in record['view'].items()
                     if key.startswith('canvas')},
        }

    def restore_canvas(self):
        from .canvas_library import empty, load, restore
        record = self.record()
        if record is None:
            restore(self.service.state, self.service.db)
            return
        saved = record.get('chatCanvas', {}).get(record.get('selectedSessionId'), {})
        for key in list(record['view']):
            if key.startswith('canvas'):
                record['view'].pop(key)
        record['view'].update(copy.deepcopy(saved.get('view', {})))
        record['view'].setdefault('canvasFocused', False)
        views = record.get('canvasViews', {})
        views.pop('retained', None)
        views.pop('primaryBinding', None)
        canvas = saved.get('canvas', {})
        # Deleted artifacts must not make the chat itself impossible to open.
        from .canvas_library import scope
        available = any(row['id'] == canvas.get('id') and scope(self.service.state, row)
                        for row in self.service._state.get('canvasArtifacts', []))
        if available:
            load(self.service.state, self.service.db, canvas['id'],
                 open_panel=bool(canvas.get('open')), version=canvas.get('selectedVersion'))
            record['canvas']['view'] = copy.deepcopy(canvas.get('view', {}))
        else:
            empty(self.service.state, open_panel=bool(canvas.get('open')) and bool(record.get('selectedSessionId')))

    def selection_revision(self, identity):
        record = self.records[identity]
        canvas = record.get('canvas') or {}
        binding = [record.get('selectedSessionId'), record.get('selectedWorkspaceId'),
                   canvas.get('id'), canvas.get('selectedVersion'), bool(canvas.get('open'))]
        if record.get('_selectionBinding') != binding:
            record['_selectionBinding'] = copy.deepcopy(binding)
            record['selectionRevision'] = record.get('selectionRevision', 0) + 1
            self.dirty.add(identity)
        return record.get('selectionRevision', 0)

    def save(self, identity=None, *, defer_ack=False):
        pending = set(self.dirty) if identity is None else self.dirty.intersection({identity})
        written = {}
        if pending and self.service._state.get('clientViewsMigrated'):
            # Commit the one-time draft migration with its private client rows,
            # including navigation/preference paths that don't save app globals.
            from .state_records import UPSERT
            self.service.db.execute(UPSERT, ('global', 'clientViewsMigrated',
                json.dumps({'present': True, 'value': True})))
        for identity in pending:
            self.remember_canvas(self.records[identity])
            self.selection_revision(identity)
            value = copy.deepcopy(self.records[identity])
            # The artifact store already owns large bodies. A presentation
            # record keeps only the currently selected artifact and controls.
            canvas = value.get("canvas", {})
            artifact = next((a for a in self.service._state.get("canvasArtifacts", []) if a["id"] == canvas.get("id")), None)
            if artifact and artifact.get("body"):
                from .canvas_versions import definition
                canvas["contentResource"] = definition(artifact, canvas.get('selectedVersion'))["body"]
                canvas.pop("content", None)
                canvas.pop("surface", None)
            encoded = json.dumps(value)
            if self.saved.get(identity) != encoded:
                self.service.db.execute("INSERT OR REPLACE INTO client_views VALUES (?,?)", (identity, encoded))
            written[identity] = encoded
        if not defer_ack:
            self.acknowledge(written)
        return written

    def acknowledge(self, written):
        self.saved.update(written)
        self.dirty.difference_update(written)
