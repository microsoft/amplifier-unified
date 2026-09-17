"""Workspace registrations and a bounded, declarative agent canvas.

The canvas supports a snapshot subset of A2UI v0.8 adjacency-list components;
see docs/canvas.md. Rich HTML is served separately inside an opaque-origin sandbox.
"""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
import time
import uuid
from urllib.parse import urlsplit

MAX_TEXT = 1_000_000
MAX_IMAGE = 5_000_000
MAX_SURFACE = 100_000
KINDS = ['auto', 'text', 'markdown', 'code', 'html', 'mermaid', 'dot', 'json', 'jsonl', 'image', 'a2ui', 'browser', 'babylon']
EXTENSIONS = {'.md':'markdown', '.markdown':'markdown', '.html':'html', '.htm':'html',
    '.mmd':'mermaid', '.mermaid':'mermaid', '.dot':'dot', '.gv':'dot', '.json':'json',
    '.jsonl':'jsonl', '.ndjson':'jsonl', **{x:'image' for x in ['.png','.jpg','.jpeg','.webp','.gif']},
    **{x:'code' for x in ['.py','.js','.jsx','.ts','.tsx','.css','.yaml','.yml','.toml','.sh','.sql','.xml','.svg']}}


def _error(message):
    from .service import AppError
    raise AppError(message)


def _registration(path, name=None):
    path = str(Path(path).expanduser().resolve())
    return {"id": uuid.uuid5(uuid.NAMESPACE_URL, path).hex, "name": name or Path(path).name or path, "path": path}


def initialize(state):
    if "workspaces" not in state:
        paths = [state["settings"]["workspace"]] + [s["workspace"] for s in state["sessions"]]
        state["workspaces"] = list({_registration(path)["id"]: _registration(path) for path in paths}.values())
    if state.get("selectedWorkspaceId") not in {w["id"] for w in state["workspaces"]}:
        state["selectedWorkspaceId"] = next((w["id"] for w in state["workspaces"] if w["path"] == state["settings"]["workspace"]), state["workspaces"][0]["id"] if state["workspaces"] else None)
    state.setdefault("canvas", {"open": False, "events": []})
    state["canvas"].setdefault("id", uuid.uuid4().hex)


def select_session_workspace(state, session):
    """Keep a conversation reachable in navigation without changing its folder."""
    initialize(state)
    row = _registration(session["workspace"])
    if not any(item["id"] == row["id"] for item in state["workspaces"]):
        state["workspaces"].append(row)
    changed = state["selectedWorkspaceId"] != row["id"]
    state["selectedWorkspaceId"] = row["id"]
    state["settings"]["workspace"] = row["path"]
    if changed:
        state["canvas"]["open"] = False


def workspace_command(state, action, args):
    initialize(state)
    rows = state["workspaces"]
    if action == "workspace.add":
        path = Path(args["path"]).expanduser().resolve()
        if not path.is_dir():
            _error("Choose an existing workspace folder.")
        name = args.get("name", "").strip()
        row = _registration(path, name or None)
        existing = next((w for w in rows if w["id"] == row["id"]), None)
        if not existing:
            rows.append(row)
        elif name:
            existing["name"] = name
        state["selectedWorkspaceId"] = row["id"]
        state["settings"]["workspace"] = row["path"]
    else:
        row = next((w for w in rows if w["id"] == args["id"]), None)
        if not row:
            _error("This workspace is no longer registered.")
        if action == "workspace.rename":
            name = args["name"].strip()
            if not name:
                _error("Enter a workspace name.")
            row["name"] = name
        elif action == "workspace.remove":
            if len(rows) == 1:
                _error("Keep at least one workspace registered.")
            rows.remove(row)
            if state["selectedWorkspaceId"] == row["id"]:
                state["selectedWorkspaceId"] = rows[0]["id"]
                state["settings"]["workspace"] = rows[0]["path"]
        elif action == "workspace.select":
            state["selectedWorkspaceId"] = row["id"]
            state["settings"]["workspace"] = row["path"]
    # Close the old workspace's preview; conversation histories are untouched.
    if action != "workspace.rename":
        state["canvas"]["open"] = False


def _image(data):
    if len(data) > MAX_IMAGE:
        _error("Canvas images must be 5 MB or smaller.")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    elif data.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"
    else:
        _error("Preview a PNG, JPEG, WebP, or GIF image. SVG and HTML are not supported.")
    return "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")


def validate_surface(surface):
    if not isinstance(surface, dict) or set(surface) - {"surfaceId", "root", "components"}:
        _error("A canvas surface needs surfaceId, root, and components.")
    if len(json.dumps(surface)) > MAX_SURFACE:
        _error("The canvas surface is too large.")
    if not isinstance(surface.get("surfaceId"), str) or not 0 < len(surface["surfaceId"]) <= 100:
        _error("Enter a surfaceId of up to 100 characters.")
    components = surface.get("components")
    if not isinstance(components, list) or not 1 <= len(components) <= 100:
        _error("A canvas surface supports 1–100 components.")
    rows, edges = {}, {}
    for item in components:
        if not isinstance(item, dict) or set(item) != {"id", "component"}:
            _error("Each component needs only id and component.")
        identity, component = item["id"], item["component"]
        if not isinstance(identity, str) or not 0 < len(identity) <= 100 or identity in rows:
            _error("Canvas component IDs must be unique nonempty strings.")
        if not isinstance(component, dict) or len(component) != 1:
            _error("Each canvas component needs exactly one supported type.")
        kind, props = next(iter(component.items()))
        allowed = {"Text": {"text", "usageHint"}, "Column": {"children"}, "Row": {"children"}, "Card": {"child"}, "Button": {"child", "action"}, "Divider": set()}
        if kind not in allowed or not isinstance(props, dict) or set(props) - allowed[kind]:
            _error("Unsupported canvas component or property: " + str(kind))
        children = []
        if kind == "Text":
            text = props.get("text")
            if not isinstance(text, dict) or set(text) != {"literalString"} or not isinstance(text["literalString"], str):
                _error("Canvas text supports literalString only.")
            if props.get("usageHint", "body") not in {"h1", "h2", "h3", "h4", "h5", "body", "caption"}:
                _error("Unsupported canvas text style.")
        elif kind in {"Row", "Column"}:
            child_prop = props.get("children")
            if not isinstance(child_prop, dict) or set(child_prop) != {"explicitList"} or not isinstance(child_prop["explicitList"], list):
                _error("Canvas rows and columns require explicitList children.")
            children = child_prop["explicitList"]
        elif kind in {"Card", "Button"}:
            children = [props.get("child")]
            if kind == "Button":
                action = props.get("action")
                if not isinstance(action, dict) or set(action) != {"name"} or not isinstance(action["name"], str) or not 0 < len(action["name"]) <= 200:
                    _error("Canvas buttons require an action name only.")
        if len(children) > 100 or any(not isinstance(child, str) for child in children):
            _error("Canvas child references must be component IDs.")
        rows[identity], edges[identity] = item, children
    if not isinstance(surface.get("root"), str) or surface["root"] not in rows:
        _error("The canvas root must reference a component.")
    referenced = [child for children in edges.values() for child in children]
    if len(referenced) != len(set(referenced)):
        _error("Each canvas component can have only one parent.")
    visiting, depths = set(), {}
    def walk(identity):
        if identity not in rows:
            _error("A canvas child references a missing component.")
        if identity in visiting:
            _error("Canvas components must not form cycles or exceed 20 levels.")
        if identity in depths:
            return depths[identity]
        visiting.add(identity)
        depth = 1 + max((walk(child) for child in edges[identity]), default=0)
        if depth > 20:
            _error("Canvas components must not form cycles or exceed 20 levels.")
        visiting.remove(identity)
        depths[identity] = depth
        return depth
    for identity in rows:
        walk(identity)
    return copy.deepcopy(surface)


def canvas_command(state, action, args, origin):
    initialize(state)
    if action == "canvas.close":
        state["canvas"]["open"] = False
        return
    if action in {"canvas.view", "canvas.report", "canvas.snapshot", "canvas.interact"}:
        canvas = state['canvas']
        if args['id'] != canvas.get('id'):
            if action in {'canvas.report', 'canvas.snapshot'}:
                return  # A replaced preview may finish while its report is in flight.
            _error('This canvas has been replaced. Read the current canvas first.')
        if action == 'canvas.view':
            canvas.setdefault('view', {}).update(copy.deepcopy(args['patch']))
            if 'source' in args['patch'] and canvas.get('kind') in {'html','babylon'}:
                canvas.pop('document', None)
                canvas.pop('interaction', None)
            if 'engine' in args['patch']:
                canvas.setdefault('renderReports', {})['preview'] = {'status':'pending','message':'Updating graph layout'}
        elif action == 'canvas.snapshot':
            if canvas.get('kind') not in {'html','babylon'}:
                _error('Only HTML previews report document controls.')
            canvas['document'] = copy.deepcopy(args['document'])
        elif action == 'canvas.interact':
            control = next((c for c in canvas.get('document', {}).get('controls', []) if c['id'] == args['controlId']), None)
            if not canvas.get('open') or not control or control.get('disabled'):
                _error('Read the current canvas document and choose an enabled control.')
            canvas['interaction'] = {**copy.deepcopy(args), 'requestId':uuid.uuid4().hex}
        else:
            reports = canvas.setdefault('renderReports', {})
            if len(reports) >= 100 and args['part'] not in reports:
                _error('Too many canvas render reports.')
            reports[args['part']] = {'status':args['status'], 'message':args.get('message',''), 'at':time.time()}
        return
    if action == "canvas.event":
        canvas = state["canvas"]
        surface = canvas.get("surface", {})
        if not canvas.get("open") or args["surfaceId"] != surface.get("surfaceId"):
            _error("This canvas surface is no longer active.")
        item = next((row for row in surface.get("components", []) if row["id"] == args["componentId"]), {})
        expected = item.get("component", {}).get("Button", {}).get("action", {}).get("name")
        if expected != args["name"]:
            _error("The action does not belong to this canvas button.")
        if len(json.dumps(args.get("value"))) > 10000:
            _error("The canvas interaction is too large.")
        canvas.setdefault("events", []).append({**copy.deepcopy(args), "id": str(uuid.uuid4()), "origin": origin, "at": time.time()})
        canvas["events"] = canvas["events"][-100:]
        return
    kind = args["kind"]
    if kind not in KINDS:
        _error('Unsupported canvas format.')
    if kind == 'auto':
        if not args.get('path'):
            _error('Automatic format detection needs a workspace file path.')
        kind = EXTENSIONS.get(Path(args['path']).suffix.lower(), 'text')
    canvas = {"id": uuid.uuid4().hex, "view": {}, "renderReports": {},"open": True, "kind": kind, "title": args.get("title") or "Canvas", "events": [], "workspaceId": state["selectedWorkspaceId"], "sessionId":state.get("selectedSessionId")}
    if kind == "browser":
        url = args.get('url','').strip()
        try:
            parsed=urlsplit(url)
            if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or any(ord(c)<33 for c in url):
                raise ValueError()
            parsed.port
        except ValueError:
            _error('Enter an http:// or https:// address without embedded credentials.')
        canvas['url']=url
        canvas['title']=args.get('title') or parsed.hostname
    elif kind == "a2ui":
        if args.get("path") or args.get("content"):
            _error("Supply a surface for an A2UI canvas.")
        canvas["surface"] = validate_surface(args.get("surface"))
    elif args.get("path"):
        root = next((w["path"] for w in state["workspaces"] if w["id"] == state["selectedWorkspaceId"]), None)
        if not root:
            _error("Choose a workspace first.")
        root = Path(root).resolve()
        path = Path(args["path"]).expanduser()
        path = (path if path.is_absolute() else root / path).resolve()
        if not path.is_relative_to(root):
            _error("Canvas files must be inside the selected workspace.")
        if not path.is_file():
            _error("Choose an existing file to preview.")
        limit = MAX_IMAGE if kind == "image" else MAX_TEXT
        with path.open("rb") as file:
            data = file.read(limit + 1)
        if len(data) > limit:
            _error("This file is too large to preview in the canvas.")
        try:
            canvas["content"] = _image(data) if kind == "image" else data.decode("utf-8")
        except UnicodeDecodeError:
            _error("This file is not UTF-8 text. Choose an image preview for images.")
        canvas["path"] = str(path)
        canvas["title"] = args.get("title") or path.name
    elif kind == "image":
        content = args.get("content", "")
        try:
            if not content.startswith("data:image/") or ";base64," not in content:
                raise ValueError()
            data = base64.b64decode(content.split(";base64,", 1)[1], validate=True)
        except (ValueError, TypeError):
            _error("Canvas images must be embedded image data or workspace files.")
        canvas["content"] = _image(data)
    else:
        content = args.get("content", "")
        if len(content.encode("utf-8")) > MAX_TEXT:
            _error("Canvas text must be 1 MB or smaller.")
        canvas["content"] = content
    if kind in {'mermaid', 'dot'} and len(canvas.get('content', '')) > 50_000:
        _error('Diagrams must be 50,000 characters or smaller.')
    state["canvas"] = canvas
