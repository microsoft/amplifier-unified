"""Durable app state and the shared UI/agent command surface."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from jsonschema import validate, ValidationError
import tinycss2
from .execution import ensure_turn, ingest as ingest_execution, finish as finish_execution


def string(limit=16000):
    return {"type": "string", "maxLength": limit}


def schema(properties=None, required=None):
    properties = properties or {}
    return {"type": "object", "properties": properties, "required": list(properties) if required is None else required, "additionalProperties": False}


ACTION_DEFINITIONS = {
    "workspace.add": ("Register an existing workspace folder and use it for new chats", schema({"path":string(4000),"name":string(200)},["path"])),
    "workspace.select": ("Select the workspace used for new chats and canvas files", schema({"id":string(100)})),
    "workspace.rename": ("Rename a workspace registration", schema({"id":string(100),"name":string(200)})),
    "workspace.remove": ("Remove a workspace registration without deleting folders or chats", schema({"id":string(100)})),
    "canvas.show": ("Save a durable artifact in this chat and open a new canvas tab (browser kind takes an http/https url): sandboxed interactive HTML, Markdown with diagram fences, Mermaid, Graphviz DOT, structured data, text, code, image, auto-detected workspace file, Babylon.js 3D HTML (kind babylon, global BABYLON preloaded), or A2UI snapshot. Surface supports Text, Row, Column, Card, Button and Divider only.", schema({"kind":{"enum":["auto","text","markdown","code","html","mermaid","dot","json","jsonl","image","a2ui","browser","babylon"]},"title":string(200),"content":string(7000000),"path":string(4000),"url":string(4000),"sessionId":string(200),"surface":{"type":"object"}},["kind"])),
    "canvas.view": ("Adjust shared canvas viewer controls", schema({"id":string(100),"patch":schema({"source":{"type":"boolean"},"reload":{"type":"number","minimum":0},"zoom":{"type":"number","minimum":0.2,"maximum":4},"panX":{"type":"number","minimum":-10000,"maximum":10000},"panY":{"type":"number","minimum":-10000,"maximum":10000},"engine":{"enum":["dot","neato","fdp","sfdp","circo","twopi"]},"node":string(500),"query":string(500)}, [])})),
    "canvas.report": ("Report browser rendering success or failure for a canvas part; this is display evidence only", schema({"id":string(100),"part":string(100),"status":{"enum":["pending","ready","error"]},"message":string(2000)},["id","part","status"])),
    "canvas.snapshot": ("Report visible HTML preview text and standard controls as untrusted display data", schema({"id":string(100),"document":schema({"text":string(16000),"controls":{"type":"array","maxItems":100,"items":schema({"id":string(100),"tag":string(30),"type":string(30),"label":string(200),"value":string(4000),"disabled":{"type":"boolean"}},["id","tag","type","label","value","disabled"])}})})),
    "canvas.interact": ("Operate a standard HTML preview control from the current canvas.document snapshot. Never executes arbitrary JavaScript.", schema({"id":string(100),"controlId":string(100),"event":{"enum":["click","input"]},"value":string(4000)},["id","controlId","event"])),
    "canvas.copy": ("Copy the current canvas source to the browser clipboard", schema({"id":string(100)})),
    "canvas.download": ("Download the current canvas source", schema({"id":string(100)})),
    "canvas.openExternal": ("Open the active browser preview URL in a browser tab; popup permissions may apply", schema({"id":string(100)})),
    "canvas.select": ("Reopen a saved artifact by ID from canvasArtifacts", schema({"id":string(100)})),
    "canvas.reopen": ("Show this chat's canvas and saved artifacts", schema()),
    "canvas.tabClose": ("Close a canvas tab; keep the artifact in chat history", schema({"id":string(100)})),
    "canvas.close": ("Close the canvas without losing its content", schema()),
    "canvas.event": ("Record an A2UI button interaction in shared agent-visible state", schema({"surfaceId":string(100),"componentId":string(100),"name":string(200),"value":{}},["surfaceId","componentId","name"])),
    "session.create": ("Start a conversation with a community bundle", schema({"title": string(200), "bundle": string(2000), "workspace": string(4000)}, [])),
    "session.select": ("Select a conversation", schema({"id": string(100)})),
    "session.rename": ("Rename a conversation", schema({"id": string(100), "title": string(200)})),
    "session.delete": ("Delete a conversation and stop its work", schema({"id": string(100)})),
    "session.export": ("Export a conversation", schema({"id": string(100)})),
    "session.fork": ("Fork conversation history through an optional user turn", schema({"id": string(100),"turn":{"type":"integer","minimum":1}},["id"])),
    "message.copy": ("Copy the entire message text as Markdown on the connected browser",schema({"sessionId":string(200),"messageId":string(200)})),
    "message.copyResult": ("Report clipboard success or failure",schema({"requestId":string(100),"status":{"enum":["ready","error"]},"message":string(2000)},["requestId","status"])),
    "message.edit": ("Fork before a user message and generate a new reply from its edited text. Original history remains available; external tool effects are not undone.",schema({"sessionId":string(200),"messageId":string(200),"text":string(100000)})),
    "conversation.send": ("Send to the main Amplifier session", schema({"sessionId":string(200),"text": string(100000), "attachmentIds":{"type":"array","maxItems":8,"uniqueItems":True,"items":string(32)}, "via": {"enum": ["chat", "text", "call"]}}, ["text"])),
    "attachment.add": ("Attach a file or image to a conversation draft", schema({"sessionId":string(200),"name":string(200),"base64":string(12000000)},["name","base64"])),
    "attachment.remove": ("Remove an attachment from a conversation draft", schema({"sessionId":string(200),"id":string(32)},["id"])),
    "conversation.stop": ("Stop the current session execution", schema()),
    "worker.spawn": ("Start a worker lane for heavier work", schema({"instruction": string(100000), "bundle": string(2000)}, ["instruction"])),
    "worker.stop": ("Stop one worker lane", schema({"id": string(100)})),
    "worker.steer": ("Send a correction to a worker", schema({"id": string(100), "text": string(100000)})),
    "approval.respond": ("Respond to an Amplifier permission request", schema({"id": string(100), "decision": {"enum": ["allow", "deny", "approve", "reject"]}})),
    "attention.read": ("Mark reviewed attention items as read without resolving the underlying condition", schema({"ids":{"type":"array","items":string(300),"maxItems":500}},["ids"])),
    "view.update": ("Change panels, modality, draft, appearance or layout", schema({"patch": {"type": "object"}})),
    "providers.credentials": ("Check provider credential environment availability without revealing values",schema({"sessionId":string(200),"module":string(200),"envVar":string(200)},["module"])),
    "providers.move": ("Reorder saved provider connections",schema({"id":string(200),"beforeId":{"type":["string","null"]},"scope":{"enum":["global","project","local"]},"sessionId":string(200)},["id"])),
    "locations.list": ("Browse local folders and files for a location control",schema({"path":string(4000),"directoriesOnly":{"type":"boolean"},"controlId":string(200)},["controlId"])),
    "providers.schema": ("Read a provider module’s configuration fields and choices",schema({"module":string(200),"id":string(200),"sessionId":string(200)},["module"])),
    "providers.list": ("List provider connections and setup status",schema({"sessionId":string(200)},[])),
    "providers.save": ("Add or edit a provider connection",schema({"sessionId":string(200),"id":string(200),"module":string(200),"source":string(4000),"config":{"type":"object"},"apiKey":string(16000),"apiKeyEnv":string(200),"scope":{"enum":["global","project","local"]}},["module","config"])),
    "providers.remove": ("Remove a provider connection",schema({"sessionId":string(200),"id":string(200),"scope":{"enum":["global","project","local"]}},["id"])),
    "providers.test": ("Test a configured provider",schema({"id":string(200),"sessionId":string(200)},["id"])),
    "providers.models": ("Browse cached provider models; refresh only this provider when requested",schema({"id":string(200),"sessionId":string(200),"refresh":{"type":"boolean"}},["id"])),
    "providers.login": ("Sign in to a provider",schema({"id":string(200),"sessionId":string(200)},["id"])),
    "providers.loginStatus": ("Check provider sign-in progress",schema({"id":string(200)},["id"])),
    "providers.loginCancel": ("Cancel provider sign-in",schema({"id":string(200)},["id"])),
    "routing.list": ("List model routing presets",schema()),
    "routing.show": ("Inspect a routing preset",schema({"name":string(200)})),
    "routing.use": ("Use a model routing preset",schema({"name":string(200),"scope":{"enum":["global","project","local"]}},["name"])),
    "routing.save": ("Save a custom routing preset",schema({"activate":{"type":"boolean"},"name":string(200),"matrix":{"type":"object"},"scope":{"enum":["global","project","local"]}},["name","matrix"])),
    "bundle.discover": ("Browse bundles and behaviors in a Git repository", schema({"url":string(4000)})),
    "bundles.list": ("List app behaviors and standalone bundles",schema()),
    "bundles.add": ("Add a behavior or standalone bundle",schema({"uri":string(4000),"name":string(200),"role":{"enum":["behavior","standalone"]}},["uri","role"])),
    "bundles.toggle": ("Enable or disable an app behavior",schema({"id":string(200),"enabled":{"type":"boolean"}})),
    "bundles.remove": ("Remove a registered bundle",schema({"id":string(200)})),
    "bundles.move": ("Reorder app behaviors",schema({"id":string(200),"direction":{"enum":["up","down"]},"beforeId":{"type":["string","null"]}},["id"])),
    "bundle.export": ("Export the conversation's customized bundle",schema({"sessionId":string(200),"name":string(200),"description":string(1000)},["sessionId"])),
    "bundle.save": ("Save and use the conversation's customized bundle",schema({"sessionId":string(200),"name":string(200),"description":string(1000)},["sessionId","name"])),
    "configuration.cancel": ("Cancel queued mount-plan changes",schema({"id":string(200)})),
    "configuration.inspect": ("Inspect resolved modules and configuration",schema({"id":string(200)})),
    "configuration.apply": ("Apply edits to the loaded session mount plan, optionally waiting for idle",schema({"id":string(200),"config":{"type":"object"},"whenIdle":{"type":"boolean"}},["id","config"])),
    "runtime.control": ("Manage runtime modes, goals, provider, budgets, skills and tools",schema({"sessionId":string(200),"operation":string(100),"args":{"type":"object"}},["sessionId","operation"])),
    "modules.list": ("List scoped module configuration",schema({"scope":{"enum":["global","project","local"]}},[])),
    "modules.save": ("Add or override a scoped module",schema({"section":{"enum":["tools","hooks","providers","orchestrator","context"]},"module":string(200),"id":string(200),"source":string(4000),"config":{"type":"object"},"enabled":{"type":"boolean"},"scope":{"enum":["global","project","local"]}},["section","module"])),
    "modules.remove": ("Remove a scoped module override",schema({"section":string(100),"id":string(200),"scope":{"enum":["global","project","local"]}},["section","id"])),
    "modules.validate": ("Validate a module using its public Core contract",schema({"behavioral":{"type":"boolean"},"section":string(100),"id":string(200),"scope":{"enum":["global","project","local"]}},["section","id"])),
    "sources.list": ("List scoped source overrides",schema({"scope":{"enum":["global","project","local"]}},[])),
    "sources.save": ("Set a scoped bundle or module source",schema({"kind":{"enum":["module","bundle"]},"name":string(200),"source":string(4000),"scope":{"enum":["global","project","local"]}},["kind","name","source"])),
    "sources.remove": ("Remove a scoped source override",schema({"kind":{"enum":["module","bundle"]},"name":string(200),"scope":{"enum":["global","project","local"]}},["kind","name"])),
    "history.importFile": ("Import a transcript as an independent conversation",schema({"content":string(1000000),"format":{"enum":["json","jsonl"]},"title":string(200),"bundle":string(2000)},["content","format"])),
    "notifications.get": ("Inspect notification delivery preferences",schema()),
    "notifications.save": ("Configure browser and ntfy notifications",schema({"patch":{"type":"object"}})),
    "permissions.get": ("Inspect scoped file-access settings",schema({"sessionId":string(200),"scope":{"enum":["global","project","local"]}},[])),
    "permissions.save": ("Save scoped file-access settings",schema({"sessionId":string(200),"scope":{"enum":["global","project","local"]},"allowed":{"type":"array","items":string(4000)},"denied":{"type":"array","items":string(4000)}},["allowed","denied"])),
    "history.list": ("Browse persisted and optionally legacy sessions",schema({"legacy":{"type":"boolean"}},[])),
    "history.import": ("Open a persisted or legacy session",schema({"id":string(200)})),
    "history.export": ("Export runtime transcript and metadata",schema({"sessionId":string(200),"format":{"enum":["json","jsonl"]}},["sessionId"])),
    "history.cleanup": ("Preview or clean old conversation entries",schema({"days":{"type":"integer","minimum":1},"apply":{"type":"boolean"},"purge":{"type":"boolean"}},[])),
    "maintenance.backup": ("Back up the conversation database",schema()),
    "maintenance.reset": ("Preview or reset selected app data with a retained private backup",schema({"parts":{"type":"array","items":{"enum":["runtime","cache","settings","conversations"]}},"apply":{"type":"boolean"},"confirmation":string(20)},["parts"])),
    "maintenance.repair": ("Repair runtime dependency installation while idle",schema()),
    "updates.app": ("Stage a published application release and restart when idle",schema()),
    "updates.check": ("Check ecosystem sources for updates", schema()),
    "updates.install": ("Stage and validate available ecosystem updates; activate when idle", schema()),
    "updates.rollback": ("Restore the previous ecosystem version when idle", schema()),
    "settings.update": ("Change voice or workspace defaults", schema({"patch": {"type": "object"}})),
    "theme.apply": ("Apply a complete single-file CSS skin", schema({"name": string(100), "css": string(1000000)})),
    "theme.reset": ("Restore the Converge skin", schema()),
    "theme.export": ("Export the applied skin", schema()),
    "state.export": ("Export app state and attached device views", schema()),
    "notification.request": ("Request notification permission on this device", schema()),
    "call.start": ("Start a realtime voice call on the connected browser", schema()),
    "call.mute": ("Mute or unmute the call microphone", schema({"muted": {"type": "boolean"}})),
    "call.end": ("End audio while leaving the work running", schema()),
}


class AppError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def validate_theme(css):
    """Reject network-bearing or malformed skins; assets must travel with the file."""
    if not css.strip():
        raise AppError("The skin is empty.")
    if "</style" in css.lower():
        raise AppError("A skin must contain CSS only.")
    rules = tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True)
    def visit(nodes):
        for node in nodes:
            if node.type == "error":
                raise AppError("The stylesheet contains invalid CSS: " + node.message)
            if node.type == "at-rule" and node.lower_at_keyword in {"import", "namespace", "document"}:
                raise AppError("Skins must be self-contained; external imports are unsupported.")
            if node.type == "url":
                check_url(node.value)
            if node.type == "function" and node.lower_name == "url":
                value = tinycss2.serialize(node.arguments).strip().strip('\"\'')
                check_url(value)
            for attr in ("prelude", "content", "arguments"):
                child = getattr(node, attr, None)
                if child:
                    visit(child)
    def check_url(value):
        if not value.startswith(("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,", "data:font/woff;base64,", "data:font/woff2;base64,")):
            raise AppError("Embed images and fonts in the skin instead of loading external URLs.")
    visit(rules)


from .smart_canvas import definitions as smart_tool_definitions
ACTION_DEFINITIONS.update(smart_tool_definitions(schema, string))


class AppService:
    def __init__(self, data_dir: Path, runtime=None, workspace=None):
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.data_dir / "app.sqlite3")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.commit()
        self.default_workspace = str(Path(workspace or os.getcwd()).resolve())
        self.runtime = runtime
        self.voice_service = None
        self.update_manager = None
        self.management = None
        self.smart_tools = None
        self.smart_canvas = None
        self.queues = set()
        self.tasks = set()
        self.lock = asyncio.Lock()
        self.closed = False
        row = self.db.execute("SELECT value FROM state WHERE id=1").fetchone()
        self.state = json.loads(row[0]) if row else {
            "schemaVersion": 1, "revision": 0, "sessions": [], "selectedSessionId": None,
            "settings": {"preferredVoice": "gpt-live-1", "fallbackVoice": "gpt-realtime-2.1", "bundle": "anchors", "workspace": self.default_workspace},
            "theme": {"name": "Converge", "css": self.default_theme()},
            "view": {"mode": "chat", "panel": None, "draft": "", "scheme": "light", "layout": "balanced"},
            "voice": {"status": "disconnected"}, "runtime": {"available": runtime is not None}, "devices": {}, "events": [],
        }
        self.state["voice"] = {"status": "disconnected"}
        self.state["runtime"] = {"available": runtime is not None, "description": "Isolated Amplifier sessions; runtime is prepared on first use."}
        for session in self.state["sessions"]:
            session["configurationBusy"]=False
            if session["status"] in {"working", "starting", "ready", "stopping"}:
                session["status"] = "interrupted"
                session["activity"] = {"phase": "interrupted", "label": "Previous work was interrupted; it has not been replayed.", "activeTools": [], "updatedAt": time.time()}
            for worker in session.get("workers", []):
                if worker.get("status") in {"working", "running", "starting", "queued", "pending"}:
                    worker["status"] = "interrupted"
        if not self.state.get("defaultBundleMigration"):
            if self.state["settings"].get("bundle") == "foundation":
                self.state["settings"]["bundle"] = "anchors"
            self.state["defaultBundleMigration"] = True
        self.state["settings"].setdefault("updates", {"autoCheck": True, "autoInstall": False, "intervalHours": 24})
        self.state["devices"] = {}
        from .workspace_canvas import initialize
        initialize(self.state)
        from .canvas_library import recover_legacy
        recover_legacy(self.state,self.db,self.data_dir)
        from .naming import automatic,persist
        for session in self.state['sessions']:
            session.setdefault('titleSource','automatic' if automatic(session) else 'manual')
            persist(self.data_dir,session)
        self._save()

    def default_theme(self):
        for name in ("converge.amplifier.css", "default-theme.css"):
            path = Path(__file__).parent / "static" / name
            if path.exists():
                return path.read_text()
        return "/* Converge uses the app's bundled default styling. */"

    def get_state(self):
        from .attention import snapshot
        result = copy.deepcopy(self.state)
        result["attention"] = snapshot(self.state)
        result.pop("attentionRead", None)
        return result

    def get_actions(self):
        return [{"name": name, "description": desc, "inputSchema": copy.deepcopy(spec)} for name, (desc, spec) in ACTION_DEFINITIONS.items()]

    def _save(self):
        from .state_storage import normalize_state
        normalize_state(self.state, self.db)
        self.db.execute("INSERT OR REPLACE INTO state VALUES (1,?)", (json.dumps(self.state),))
        self.db.commit()

    def _publish(self):
        self.state["revision"] += 1
        self._save()
        snapshot = self.get_state()
        for queue in self.queues:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(snapshot)

    def subscribe(self):
        queue = asyncio.Queue(maxsize=4)
        self.queues.add(queue)
        return queue

    def unsubscribe(self, queue):
        self.queues.discard(queue)

    def _session(self, sid=None):
        sid = sid or self.state["selectedSessionId"]
        for session in self.state["sessions"]:
            if session["id"] == sid:
                return session
        raise AppError("Select or create a conversation first.", 404)

    def _new_session(self, args):
        workspace = str(Path(args.get("workspace") or self.state["settings"]["workspace"]).expanduser().resolve())
        if not Path(workspace).is_dir():
            raise AppError("The workspace folder does not exist.")
        return {"id": str(uuid.uuid4()), "title": args.get("title") or "New conversation", "titleSource":"manual" if args.get("title") and args["title"] not in {"New conversation","A new conversation","Untitled conversation"} else "automatic", "bundle": args.get("bundle") or self.state["settings"]["bundle"], "workspace": workspace, "status": "idle", "createdAt": time.time(), "messages": [], "workers": [], "approvals": []}

    def _message(self, session, role, text, via="chat", **extra):
        message = {"id": str(uuid.uuid4()), "role": role, "text": text, "via": via, "createdAt": time.time(), **extra}
        session["messages"].append(message)
        return message

    def _activity(self, session, phase, label, *, reset=False):
        now = time.time()
        previous = session.get("activity", {})
        session["activity"] = {"phase": phase, "label": label,
            "startedAt": now if reset else previous.get("startedAt", now), "updatedAt": now,
            "activeTools": previous.get("activeTools", []), "lastEvent": previous.get("lastEvent")}
        return session["activity"]

    def _task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def dispatch(self, action, args=None, origin="ui", command_id=None, expected_revision=None):
        args = args or {}
        if action.startswith("smartTools."):
            command_id = command_id or str(uuid.uuid4())
        if action not in ACTION_DEFINITIONS:
            raise AppError("Unknown action: " + action, 404)
        try:
            validate(args, ACTION_DEFINITIONS[action][1])
        except ValidationError as exc:
            raise AppError(exc.message) from exc
        fingerprint = hashlib.sha256(json.dumps([action, args, origin], sort_keys=True).encode()).hexdigest()
        pending = []
        async with self.lock:
            if command_id:
                previous = self.db.execute("SELECT fingerprint,receipt FROM commands WHERE id=?", (command_id,)).fetchone()
                if previous:
                    if previous[0] != fingerprint:
                        raise AppError("This command ID was already used with different contents.", 409)
                    return {**json.loads(previous[1]), "state": self.get_state(), "duplicate": True}
            if expected_revision is not None and expected_revision != self.state["revision"]:
                raise AppError("The app changed. Refresh its state and retry.", 409)
            if self.state.get("updates",{}).get("phase") == "activating" and action in {"conversation.send","worker.spawn","worker.steer","call.start"}:
                raise AppError("An ecosystem update is activating. Please retry in a moment.", 409)
            if action in {"conversation.send","worker.spawn","worker.steer","call.start"}:
                current=next((s for s in self.state['sessions'] if s['id']==args.get('sessionId',self.state['selectedSessionId'])),{})
                if current.get('configurationBusy'):raise AppError('Applying conversation settings; retry shortly.',409)
            from .canvas_library import remember, restore, fork_artifacts
            remember(self.state,self.db)
            previous_scope=(self.state.get('selectedSessionId'),self.state.get('selectedWorkspaceId'))
            previous_open=self.state.get('canvas',{}).get('open',False)
            effects = []
            if action.startswith("workspace."):
                from .workspace_canvas import workspace_command
                workspace_command(self.state, action, args)
            elif action.startswith("canvas."):
                from .workspace_canvas import canvas_command
                if action in {'canvas.select','canvas.reopen','canvas.tabClose'}:
                    from .canvas_library import command
                    command(self.state,self.db,action,args)
                    if action=='canvas.select':
                        self.state['view'].setdefault('canvasDraft',{}).update(library=False,open=False,browser=False)
                elif action=='canvas.openExternal':
                    canvas=self.state['canvas']
                    if canvas.get('id')!=args['id'] or canvas.get('kind')!='browser':raise AppError('Select a browser artifact first.')
                    effects.append({'type':'browser.open','url':canvas['url']})
                elif action in {'canvas.copy', 'canvas.download'}:
                    canvas = self.state['canvas']
                    if args['id'] != canvas.get('id'):
                        raise AppError('This canvas has been replaced.')
                    content = canvas.get('url') if canvas.get('kind')=='browser' else canvas.get('content', json.dumps(canvas.get('surface', {}), indent=2))
                    extension = {'markdown':'md','html':'html','mermaid':'mmd','dot':'dot','json':'json','jsonl':'jsonl','a2ui':'json'}.get(canvas.get('kind'), 'txt')
                    if action=='canvas.download' and canvas.get('kind')=='babylon':
                        effects.append({'type':'download.url','url':'/api/canvas/'+canvas['id']+'/download','filename':'canvas-3d.html'})
                    else:
                        effects.append({'type':'clipboard.write' if action == 'canvas.copy' else 'download',
                        'content':content,'filename':'canvas.'+extension,'mime':'text/plain','canvasId':args['id']})
                elif action=='canvas.show':
                    sid=args.get('sessionId',self.state.get('selectedSessionId'))
                    owner=self._session(sid) if sid else None
                    workspace=next((w for w in self.state['workspaces'] if owner and w['path']==owner['workspace']),None)
                    scoped={**self.state,'selectedSessionId':sid,'selectedWorkspaceId':workspace['id'] if workspace else self.state['selectedWorkspaceId']}
                    canvas_command(scoped,action,args,origin)
                    remember(scoped,self.db)
                    if sid==self.state.get('selectedSessionId') and scoped['selectedWorkspaceId']==self.state['selectedWorkspaceId']:
                        self.state['canvas']=scoped['canvas']
                        self.state['view'].setdefault('canvasDraft',{}).update(library=False,open=False,browser=False)
                else:
                    canvas_command(self.state, action, args, origin)
            elif action == "session.create":
                session = self._new_session(args)
                from .workspace_canvas import select_session_workspace
                select_session_workspace(self.state, session)
                self.state["sessions"].insert(0, session)
                self.state["selectedSessionId"] = session["id"]
                self.state["view"]["draft"] = ""
            elif action == "session.select":
                session = self._session(args["id"])
                from .workspace_canvas import select_session_workspace
                select_session_workspace(self.state, session)
                self.state["selectedSessionId"] = session["id"]
                self.state["view"]["draft"] = ""
            elif action == "session.rename":
                if not args["title"].strip():
                    raise AppError("Enter a title.")
                session=self._session(args['id'])
                session.update(title=args['title'].strip(),titleSource='manual')
                from .naming import persist
                persist(self.data_dir,session)
            elif action == "session.delete":
                session = self._session(args["id"])
                if self.runtime:
                    pending.append((self.runtime.stop, (session["id"],)))
                self.state["sessions"].remove(session)
                if self.state["selectedSessionId"] == session["id"]:
                    self.state["selectedSessionId"] = self.state["sessions"][0]["id"] if self.state["sessions"] else None
            elif action == 'message.copy':
                source = self._session(args['sessionId'])
                message = next((m for m in source['messages'] if m['id']==args['messageId']), None)
                if not message:
                    raise AppError('This message is no longer available.',404)
                identity = str(uuid.uuid4())
                self.state['view']['messageCopy'] = {**args,'requestId':identity,'status':'pending'}
                effects.append({'type':'message.copy',**args,'requestId':identity})
            elif action == 'message.copyResult':
                result = self.state['view'].get('messageCopy',{})
                if result.get('requestId') == args['requestId']:
                    result.update(status=args['status'],message=args.get('message',''))
            elif action == 'message.edit':
                source = self._session(args['sessionId'])
                original = next((m for m in source['messages'] if m['id']==args['messageId'] and m['role']=='user'),None)
                if not original:
                    raise AppError('Choose one of your own messages to edit.')
                text = args['text'].strip()
                if not text:
                    raise AppError('Enter a message before regenerating.')
                if source.get('configurationBusy'):
                    raise AppError('Wait for configuration changes to finish.',409)
                if not self.runtime:
                    raise AppError('The Amplifier runtime is unavailable.')
                if self.state.get('updates',{}).get('phase')=='activating':
                    raise AppError('An update is activating. Please retry in a moment.',409)
                session = self._new_session({'title':source['title']+' · edited','workspace':source['workspace'],'bundle':source['bundle']})
                from .session_store import fork_session
                try:
                    session.update(fork_session(self.data_dir,source,session['id'],before_message_id=original['id']))
                except ValueError as exc:
                    raise AppError(str(exc),409) from exc
                session['editOrigin'] = {'sessionId':source['id'],'messageId':original['id']}
                input_id = command_id or str(uuid.uuid4())
                self._message(session,'user',text,'text' if original.get('via')=='text' else 'chat',inputId=input_id,attachments=copy.deepcopy(original.get('attachments',[])))
                self._activity(session,'queued','Generating from your edited message.',reset=True)
                session['status']='working'
                ensure_turn(session,input_id,text)
                self.state['sessions'].insert(0,session)
                self.state['selectedSessionId']=session['id']
                self.state['view']['messageEdit']=None
                from .workspace_canvas import select_session_workspace
                select_session_workspace(self.state,session)
                pending.append((self._send,(copy.deepcopy(session),text,input_id)))
            elif action == "session.fork":
                source = self._session(args["id"])
                if source.get("configurationBusy"):
                    raise AppError("Wait for configuration changes to finish before forking.",409)
                session = self._new_session({"title": source["title"] + " · fork", "workspace": source["workspace"], "bundle": source["bundle"]})
                # loop-live checkpoints before publishing idle. Fork that full
                # context (including tool receipts), never reconstruct a running
                # session from the visible assistant bubbles alone.
                from .session_store import fork_session
                try:
                    session.update(fork_session(self.data_dir,source,session["id"],turn=args.get("turn")))
                except ValueError as exc:
                    raise AppError(str(exc),409) from exc
                self.state["sessions"].insert(0, session)
                self.state["selectedSessionId"] = session["id"]
                self.state['view']['messageEdit']=None
                from .workspace_canvas import select_session_workspace
                select_session_workspace(self.state,session)
            elif action in {"state.export", "session.export", "theme.export"}:
                content = self.state if action == "state.export" else self._session(args["id"]) if action == "session.export" else self.state["theme"]["css"]
                mime = "text/css" if action == "theme.export" else "application/json"
                effects.append({"type": "download", "filename": "amplifier-skin.css" if action == "theme.export" else "amplifier-export.json", "mime": mime, "mimeType": mime, "content": content if isinstance(content, str) else json.dumps(content, indent=2)})
            elif action == "attachment.add":
                from .attachments import save,MAX_FILES
                session=self._session(args.get('sessionId'))
                draft=session.setdefault('draftAttachments',[])
                if len(draft)>=MAX_FILES:raise AppError('Attach up to 8 files per message.')
                draft.append(save(self.data_dir,args['name'],args['base64']))
            elif action == "attachment.remove":
                session=self._session(args.get('sessionId'))
                session['draftAttachments']=[row for row in session.get('draftAttachments',[]) if row['id']!=args['id']]
            elif action == "conversation.send":
                session = self._session(args.get("sessionId"))
                text = args["text"].strip()
                requested=args.get('attachmentIds',[])
                available={row['id']:row for row in session.get('draftAttachments',[])}
                if any(identity not in available for identity in requested):raise AppError('An attachment is no longer in this draft. Refresh and retry.')
                attachments=[available[identity] for identity in requested]
                if not text and not attachments:raise AppError("Enter a message or attach a file.")
                text=text or 'Please review the attached files.'
                input_id = command_id or str(uuid.uuid4())
                self._message(session, "user", text, args.get("via", self.state["view"]["mode"]), inputId=input_id,attachments=attachments)
                session["draftAttachments"]=[row for row in session.get("draftAttachments",[]) if row["id"] not in requested]
                if session["title"] in {"New conversation","A new conversation","Untitled conversation"}:
                    session["title"] = text[:64]
                self._activity(session, "queued", "Your message is queued for Amplifier.", reset=session["status"] not in {"working", "starting"})
                session["status"] = "working"
                session.pop("error", None)
                if self.state["selectedSessionId"] == session["id"] and self.state["view"].get("draft", "").strip() == args["text"].strip():
                    self.state["view"]["draft"] = ""
                ensure_turn(session,input_id,text)
                pending.append((self._send, (copy.deepcopy(session), text, input_id)))
            elif action == "conversation.stop":
                session = self._session()
                session["status"] = "stopping"
                pending.append((self._stop, (session["id"],)))
            elif action == "worker.spawn":
                session = self._session()
                text = args["instruction"].strip()
                if not text:
                    raise AppError("Describe the worker's task.")
                instruction = "Delegate the following task to a background worker lane using your delegation tool. Keep the main conversation available. Task: " + text
                if args.get("bundle"):
                    instruction += "\nRequested worker bundle: " + args["bundle"]
                input_id = command_id or str(uuid.uuid4())
                self._message(session, "user", text, "worker request", inputId=input_id)
                session["status"] = "working"
                ensure_turn(session,input_id,text)
                pending.append((self._send, (copy.deepcopy(session), instruction, input_id)))
            elif action in {"worker.stop", "worker.steer"}:
                session = self._session()
                if not self.runtime:
                    raise AppError("The execution runtime is unavailable.")
                method = self.runtime.stop_worker if action == "worker.stop" else self.runtime.steer_worker
                call_args = (session["id"], args["id"]) + ((args["text"],) if action == "worker.steer" else ())
                pending.append((method, call_args))
            elif action == "approval.respond":
                session = self._session()
                approval = next((a for a in session["approvals"] if a["id"] == args["id"] and a.get("status") == "pending"), None)
                if not approval:
                    raise AppError("This permission request is no longer pending.", 409)
                if origin not in {"ui", "user"}:
                    raise AppError("This tool requested user approval. It must be answered by the user.", 403)
                decision = "allow" if args["decision"] in {"allow", "approve"} else "deny"
                approval["status"] = decision
                if self.runtime:
                    pending.append((self.runtime.approval, (session["id"], args["id"], decision)))
            elif action == "attention.read":
                from .attention import snapshot
                current = {item['id']:item for item in snapshot(self.state)['items']}
                if any(identity not in current for identity in args['ids']):
                    raise AppError('Attention items changed; refresh before marking them read.')
                receipts = self.state.setdefault('attentionRead', {})
                for identity in args['ids']:receipts[identity] = current[identity]['fingerprint']
                self.state['attentionRead'] = {key:value for key,value in receipts.items() if key in current}
            elif action == "view.update":
                patch = args["patch"]
                allowed = {"mode", "panel", "draft", "scheme", "layout", "selectedWorkerId", "contextVisible", "commandsVisible", "notificationPermission", "themeDraft", "themeDraftName", "themePreview", "newSessionDraft", "sessionSetup", "workerDraft", "notice", "agentAction", "agentArgs", "bundleManager", "moduleEditor", "settingsSection", "maintenanceDraft", "providerEditor", "routingEditor","registryDraft", "historyFilter", "runtimeDraft", "expandedExecutions", "executionExpanded", "executionDetails", "settingsExpanded", "settingsFilters", "locationPicker", "composerModel", "canvasWidth", "navPinned", "navExpanded", "navFilter", "workspaceDraft", "canvasDraft", "messageEdit", "smartToolsEditor"}
                if set(patch) - allowed:
                    raise AppError("Unknown view setting.")
                for key, options in {"mode": {"call", "text", "chat"}, "scheme": {"light", "dark", "system"}, "layout": {"balanced", "conversation", "work"}}.items():
                    if key in patch and patch[key] not in options:
                        raise AppError("Invalid " + key)
                if "canvasWidth" in patch and (type(patch["canvasWidth"]) not in {int, float} or not 300 <= patch["canvasWidth"] <= 900):
                    raise AppError("Canvas width must be between 300 and 900 pixels.")
                for key in ("navPinned", "navExpanded"):
                    if key in patch and type(patch[key]) is not bool:
                        raise AppError("Navigation switches must be true or false.")
                self.state["view"].update(copy.deepcopy(patch))
            elif action.startswith("smartTools."):
                if not self.smart_tools: raise AppError("Smart Tools service is unavailable.")
                if action == 'smartTools.context':
                    self.smart_canvas.context(args)
                else:
                    scoped_args = copy.deepcopy(args)
                    if action in {'smartTools.call','smartTools.open'}:
                        scoped_args.setdefault('sessionId',self.state.get('selectedSessionId'))
                    if action == 'smartTools.appCall':
                        self.smart_canvas.binding(args['canvasId'])
                    pending.append((self.smart_canvas.command,(action,scoped_args,command_id,origin)))
            elif action.startswith(("bundle.","bundles.","configuration.","runtime.","permissions.","history.","maintenance.","notifications.","providers.","routing.","modules.","sources.","locations.")):
                if not self.management: raise AppError("Management service is unavailable.")
                pending.append((self.management.command,(action,copy.deepcopy(args),command_id)))
            elif action.startswith("updates."):
                if not self.update_manager: raise AppError("Update service is unavailable.")
                pending.append((self.update_manager.command, (action.split(".")[1],)))
            elif action == "settings.update":
                patch = args["patch"]
                if set(patch) - {"preferredVoice", "fallbackVoice", "bundle", "workspace", "notifications", "updates"}:
                    raise AppError("Unknown setting.")
                if "preferredVoice" in patch and patch["preferredVoice"] not in {"gpt-live-1", "gpt-realtime-2.1"}:
                    raise AppError("Select a supported voice model.")
                if "bundle" in patch and (not isinstance(patch["bundle"],str) or not patch["bundle"].strip()):
                    raise AppError("Enter a default bundle.")
                if "updates" in patch:
                    options = patch["updates"]
                    if not isinstance(options,dict) or set(options)-{"autoCheck","autoInstall","intervalHours"}:
                        raise AppError("Invalid update settings.")
                    for key in ("autoCheck","autoInstall"):
                        if key in options and type(options[key]) is not bool: raise AppError("Update switches must be true or false.")
                    if "intervalHours" in options and (type(options["intervalHours"]) is not int or not 1 <= options["intervalHours"] <= 168):
                        raise AppError("Check interval must be between 1 and 168 hours.")
                    patch = {**patch, "updates": {**self.state["settings"].get("updates",{}), **options}}
                    if patch["updates"].get("autoInstall") and not patch["updates"].get("autoCheck"):
                        raise AppError("Enable automatic checking before automatic installation.")
                self.state["settings"].update(copy.deepcopy(patch))
            elif action == "theme.apply":
                validate_theme(args["css"])
                self.state["theme"] = {"name": args["name"] or "Custom skin", "css": args["css"]}
            elif action == "theme.reset":
                self.state["theme"] = {"name": "Converge", "css": self.default_theme()}
            elif action == "notification.request":
                effects.append({"type": "notification.request"})
            elif action in {"call.start", "call.mute", "call.end"}:
                call_args = dict(args)
                if action == "call.start":
                    session = self._session()
                    if self.voice_service and not self.voice_service.api_key:
                        raise AppError("Set OPENAI_API_KEY in the terminal environment to enable calls.")
                    self.state["voice"].update({"status": "connecting", "sessionId": session["id"]})
                    call_args["sessionId"] = session["id"]
                elif action == "call.mute":
                    self.state["voice"]["muted"] = args["muted"]
                elif self.voice_service:
                    pending.append((self._end_call, ()))
                self.state["voice"]["command"] = {"id": command_id or str(uuid.uuid4()), "type": action, "args": call_args}
                effects.append({"type": action, "args": call_args, **args})
            if action in {'session.create','session.fork','message.edit'}:
                from .naming import persist
                persist(self.data_dir,session)
            if action in {'session.fork','message.edit'}:
                fork_artifacts(self.state,source['id'],session)
            if previous_scope != (self.state.get('selectedSessionId'),self.state.get('selectedWorkspaceId')):
                restore(self.state,self.db,open_panel=previous_open)
            self.state["events"].append({"id": command_id, "action": action, "origin": origin, "at": time.time()})
            self.state["events"] = self.state["events"][-200:]
            for effect in effects:
                effect.update({"id": str(uuid.uuid4()), "createdAt": time.time(), "origin": origin})
            self.state.setdefault("deviceCommands", []).extend(copy.deepcopy([effect for effect in effects if effect["type"] != "download" or action == "canvas.download"]))
            self.state["deviceCommands"] = self.state["deviceCommands"][-20:]
            receipt = {"accepted": True, "revision": self.state["revision"] + 1, "effects": effects}
            if action.startswith("smartTools.") and action != "smartTools.context":
                receipt["operationId"] = command_id
            if command_id:
                self.db.execute("INSERT INTO commands VALUES (?,?,?)", (command_id, fingerprint, json.dumps(receipt)))
            self._publish()
            result = {**receipt, "state": self.get_state()}
        for fn, values in pending:
            self._task(self._guard(fn, values))
        return result

    async def _guard(self, fn, args):
        try:
            await fn(*args)
        except Exception as exc:
            sid = args[0].get("id") if args and isinstance(args[0], dict) else args[0] if args else None
            await self.on_runtime_event("runtime.error", {"sessionId": sid, "error": str(exc)})

    async def _send(self, session, text, input_id):
        if not self.runtime:
            raise AppError("The Amplifier runtime is unavailable.")
        await self.runtime.send(session, text, input_id, self.on_runtime_event)

    async def _end_call(self):
        try:
            await self.voice_service.end()
        except Exception as exc:
            await self.set_voice_status({"status": "error", "error": str(exc)})

    async def _stop(self, sid):
        if self.runtime:
            await self.runtime.stop(sid)
        await self.on_runtime_event("runtime.status", {"sessionId": sid, "status": "stopped"})

    async def record_voice_usage(self,session_id,call_id,response_id,model,usage,phase='completed'):
        from .voice_usage import normalize_voice_usage
        async with self.lock:
            try:session=self._session(session_id)
            except AppError:return
            identity='voice:'+call_id+':'+response_id
            tree=session.get('execution',{})
            existing=next((n for n in tree.get('nodes',[]) if n['id']==identity),None)
            turn_id=('voice:'+call_id) if response_id=='session' else existing.get('turnId') if existing else tree.get('currentTurnId')
            if not turn_id:turn_id='voice:'+call_id
            if not any(t['id']==turn_id for t in tree.get('turns',[])):
                previous=tree.get('currentTurnId')
                ensure_turn(session,turn_id,'Voice call · session usage' if response_id=='session' else 'Voice call')
                if previous:session['execution']['currentTurnId']=previous
            ingest_execution(session,{'id':identity,'turnId':turn_id,'sessionId':session_id,'rootSessionId':session_id,
                'kind':'llm','phase':phase,'label':model+' · voice','provider':'OpenAI voice','model':model,
                'startedAt':existing.get('startedAt',time.time()) if existing else time.time(),
                **({'endedAt':time.time(),'usage':normalize_voice_usage(usage)} if phase=='completed' else {})})
            if phase=='completed' and turn_id.startswith('voice:'):
                for turn in session['execution']['turns']:
                    if turn['id']==turn_id:turn.update(phase='completed',endedAt=time.time())
            self._publish()

    async def on_runtime_event(self, kind, payload):
        async with self.lock:
            try:
                session = self._session(payload.get("rootSessionId") or payload.get("sessionId"))
            except AppError:
                return
            if kind == 'session.naming':
                from .naming import automatic,persist
                name=payload.get('name');description=payload.get('description')
                if automatic(session) and isinstance(name,str) and name.strip():
                    session.update(title=name.strip()[:200],titleSource='generated')
                if isinstance(description,str) and description.strip():session['description']=description.strip()[:1000]
                persist(self.data_dir,session)
            elif kind == 'session.naming.progress':
                from .naming import read
                from .host.storage import SessionStore
                directory=SessionStore(self.data_dir/'sessions').directory(session.get('runtimeSessionId') or session['id'])
                directory.mkdir(parents=True,exist_ok=True,mode=0o700)
                data=read(directory);data['naming_completed_inputs']=payload.get('completedInputs',[])
                SessionStore._atomic(directory/'naming.json',json.dumps(data))
            elif kind == "execution.event":
                ingest_execution(session,payload)
            elif kind == "runtime.status":
                # Provider requests/retries describe current work; only lifecycle
                # events or accepted input can start work. Late/background notices
                # must not lock a finished conversation's fork/edit controls.
                if payload.get('activityOnly') and session.get('status') not in {'working','starting'}:
                    return
                session["status"] = payload.get("status", "idle")
                # A successfully initialized session supersedes its old startup
                # failure. Idle/stopped alone do not prove recovery (providers
                # may report an error immediately before becoming idle).
                if session["status"] == "ready":
                    session.pop("error", None)
                labels = {"starting": "Preparing your Amplifier session…", "working": "Waiting for the model response…", "ready": "Ready to work", "idle": "Ready", "stopped": "Stopped", "stopping": "Stopping work…"}
                activity = self._activity(session, payload.get("phase", session["status"]), payload.get("detail") or labels.get(session["status"], session["status"]))
                if session["status"] in {"idle", "stopped"}:
                    activity["activeTools"] = []
                    finish_execution(session,"completed" if session["status"]=="idle" else "stopped")
                    if session.get("configurationPending") and session["status"]=="idle":self._task(self.refresh_configuration(session["id"]))
                elif activity["activeTools"] and not payload.get("detail"):
                    activity.update({"phase": "tools", "label": "Running tools"})
                if payload.get("phase") or payload.get("detail"):
                    session["progress"] = {key: payload[key] for key in ("phase", "detail", "elapsedSeconds") if key in payload}
                elif session["status"] != "starting":
                    session.pop("progress", None)
                if payload.get("report"):
                    session["runtimeReport"] = payload["report"]
                if payload.get("runtimeSessionId"):
                    session["runtimeSessionId"] = payload["runtimeSessionId"]
            elif kind == "runtime.error":
                session["status"] = "error"
                finish_execution(session,"error")
                session["error"] = str(payload.get("error") or payload.get("message") or "Runtime failed")
                self._activity(session, "error", session["error"])["activeTools"] = []
            elif kind == "runtime.generation":
                event = {**payload, "at": time.time()}
                session.setdefault("generations", []).append(event)
                session["generations"] = session["generations"][-200:]
                if payload.get("event") == "generation.finished":
                    if self.management:
                        self._task(self._notify_completion(copy.deepcopy(session),copy.deepcopy(payload)))
                    pending = payload.get("active_job_ids", [])
                    self._activity(session, "waiting-workers" if pending else "processing",
                        f"Waiting for {len(pending)} delegated tasks" if pending else "Response ready")
            elif kind == "assistant.message":
                original = next((m for m in reversed(session["messages"]) if m.get("inputId") == payload.get("inputId") and m["role"] == "user"), {})
                self._message(session, "assistant", payload.get("text", ""), original.get("via", "call" if str(payload.get("inputId", "")).startswith("voice:") else "chat"), inputId=payload.get("inputId"), source="amplifier")
                session.pop("streaming", None)
            elif kind == "assistant.delta":
                session["streaming"] = session.get("streaming", "") + payload.get("text", payload.get("delta", ""))
            elif kind == "worker.updated":
                worker = next((w for w in session["workers"] if w["id"] == payload.get("id")), None)
                if worker:
                    worker.update(payload)
                else:
                    session["workers"].append(copy.deepcopy(payload))
                retrying = payload.get("phase") == "retrying"
                activity = self._activity(session, "retrying" if retrying else "workers", "A worker is retrying a model request" if retrying else "Delegated work is reporting progress")
                activity["lastEvent"] = {"worker": payload.get("name", "Worker"), "status": payload.get("status"), "at": time.time()}
            elif kind == "approval.requested":
                session["approvals"].append({**payload, "status": "pending"})
                self._activity(session, "approval", "Waiting for your approval")
            elif kind == "approval.resolved":
                for approval in session["approvals"]:
                    if approval["id"] == payload.get("id"):
                        approval["status"] = payload.get("decision", "expired")
            else:
                event = {"type": kind, **payload, "at": time.time()}
                if kind == "runtime.tool":
                    activity = self._activity(session, "tools", "Running tools")
                    tools = activity["activeTools"]
                    identity = payload.get("callId") or payload.get("tool")
                    if payload.get("phase") == "pre":
                        if not any((t.get("callId") or t.get("tool")) == identity for t in tools):
                            tools.append({"callId": payload.get("callId"), "tool": payload.get("tool", "tool"), "startedAt": event["at"]})
                        activity["label"] = "Delegating work" if payload.get("tool") == "delegate" else "Using " + str(payload.get("tool") or "a tool")
                    elif payload.get("phase") in {"post", "error"}:
                        activity["activeTools"] = [t for t in tools if (t.get("callId") or t.get("tool")) != identity]
                        if not activity["activeTools"]:
                            activity.update({"phase": "model", "label": "Waiting for the model response…"})
                    activity["lastEvent"] = {"tool": payload.get("tool"), "phase": payload.get("phase"), "at": event["at"]}
                session.setdefault("runtimeEvents", []).append(event)
                session["runtimeEvents"] = session["runtimeEvents"][-100:]
            self._publish()

    async def refresh_configuration(self,identity):
        async with self.lock:
            try:session=self._session(identity)
            except AppError:return
            if not session.get('configurationPending') or session.get('configurationBusy') or session['status'] not in {'idle','stopped','interrupted','error'}:return
            if any(w.get('persistent') and w.get('status') in {'idle','running','starting'} for w in session.get('workers',[])):return
            session['configurationBusy']=True
            self._publish()
        try:
            if self.runtime:await self.runtime.stop(identity)
            async with self.lock:
                session=self._session(identity);session['configurationPending']=False
                self._publish()
        finally:
            async with self.lock:
                try:self._session(identity)['configurationBusy']=False
                except AppError:pass
                self._publish()

    async def _notify_completion(self,session,generation):
        try:
            await self.management.notifications.send(session,generation)
        except Exception:
            async with self.lock:
                self.state['notificationError']='Notification delivery failed. Check notification settings.'
                self._publish()

    def state_resource(self, identity):
        from .state_storage import resource
        return resource(self.db, identity)

    async def app_bridge(self, operation, args, session_id):
        if operation in {"get_state", "state.get"}:
            from .agent_state import read_state
            return read_state(self.get_state(), args, session_id=session_id, resolve=self.state_resource)
        if operation in {"list_actions", "actions.list"}:
            actions = self.get_actions()
            prefix = args.get('prefix', '')
            if not isinstance(prefix, str):
                raise AppError('Action prefix must be text.')
            return [item for item in actions if item['name'].startswith(prefix)]
        if operation in {"dispatch", "action.dispatch"}:
            from .agent_state import read_state
            action_args=copy.deepcopy(args.get('args',{}))
            if args['action'] in {'canvas.show','smartTools.call','smartTools.open'}:
                action_args.setdefault('sessionId',session_id)
            result = await self.dispatch(args["action"], action_args, origin="agent", command_id=args.get("id"), expected_revision=args.get("expectedRevision"))
            return {**result, 'effects':[{'id':e.get('id'),'type':e.get('type')} for e in result.get('effects',[])], 'state':read_state(result['state'], {}, session_id=session_id, resolve=self.state_resource)}
        raise AppError("Unknown app bridge operation.")

    async def update_device(self, payload):
        client_id = str(payload.get("clientId") or "browser")[:100]
        self.state["devices"][client_id] = {**payload, "updatedAt": time.time()}
        # View snapshots are observational and don't invalidate command revisions.
        self._save()

    async def record_voice_transcript(self, role, text, *, voice_id, item_id, append=False, session_id=None):
        async with self.lock:
            session = self._session(session_id)
            existing = next((m for m in session["messages"] if m.get("voiceId") == voice_id and m.get("voiceItemId") == item_id), None)
            if existing:
                existing["text"] = existing["text"] + text if append else text
            else:
                self._message(session, role, text, "call", voiceId=voice_id, voiceItemId=item_id)
            self._publish()

    async def set_voice_status(self, payload):
        async with self.lock:
            self.state["voice"].update(payload)
            self._publish()

    async def voice_delegate(self, text, command_id, session_id=None):
        # Persist acceptance before scheduling, just like typed commands. A repeated
        # provider event or reconnect must never execute the same tool request twice.
        async with self.lock:
            if self.state.get("updates",{}).get("phase") == "activating":
                raise AppError("An ecosystem update is activating. Please retry in a moment.",409)
            session = self._session(session_id)
            if session.get("configurationBusy"):raise AppError("Applying conversation settings; retry shortly.",409)
            fingerprint = hashlib.sha256(json.dumps(["voice_delegate", session["id"], text]).encode()).hexdigest()
            previous = self.db.execute("SELECT fingerprint,receipt FROM commands WHERE id=?", (command_id,)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise AppError("This voice command ID was already used with different contents.", 409)
                return {**json.loads(previous[1]), "duplicate": True}
            receipt = {"accepted": True, "inputId": command_id, "sessionId": session["id"]}
            self.db.execute("INSERT INTO commands VALUES (?,?,?)", (command_id, fingerprint, json.dumps(receipt)))
            session["status"] = "working"
            self._activity(session, "queued", "Sending voice request to Amplifier", reset=True)
            ensure_turn(session,command_id,text)
            self._publish()
            snapshot = copy.deepcopy(session)
        self._task(self._guard(self._send, (snapshot, text, command_id)))
        return receipt

    async def wait_for_response(self, session_id, input_id=None, after_count=0, timeout=120):
        # Text blocks can precede tools. Only loop-live's identified completion
        # event proves the manager has finished a turn for this input.
        queue = self.subscribe()
        try:
            async with asyncio.timeout(timeout):
                while True:
                    session = self._session(session_id)
                    for event in session.get("generations", []):
                        if input_id and input_id not in event.get("input_ids", []):
                            continue
                        if event["event"] == "generation.finished":
                            return {key: event.get(key) for key in
                                ("text", "generation_id", "input_ids", "active_job_ids", "disposition")}
                        if event["event"] in {"generation.failed", "generation.detached"}:
                            raise AppError("The Amplifier turn ended without a completed response.")
                    if session["status"] in {"error", "stopped", "interrupted"}:
                        raise AppError(session.get("error", "Execution ended before the response completed."))
                    await queue.get()
        finally:
            self.unsubscribe(queue)

    async def close(self):
        self.closed = True
        if self.update_manager:
            await self.update_manager.close()
        if self.management and self.management.setup_manager:
            await self.management.setup_manager.close()
        if self.runtime:
            await self.runtime.close()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.smart_tools:
            await self.smart_tools.close()
        if self.management:
            await self.management.provider_catalog.close()
        self._save()
        self.db.close()
