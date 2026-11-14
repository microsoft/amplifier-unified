"""Host-owned workspace registration and conversation selection.

Preserve application state, legacy identities and Canvas visibility together.
Folder placement recovery and native history discovery retain their own owners.
This is an internal host adapter, not an independent workspace registry.
"""
from __future__ import annotations

from pathlib import Path
import uuid

from .errors import _error
from .workspace_registrations import edit, register


def _registration(path, name=None):
    path = str(Path(path).expanduser().resolve())
    return {"id": uuid.uuid5(uuid.NAMESPACE_URL, path).hex, "name": name or Path(path).name or path, "path": path,
            "available": Path(path).is_dir()}


def refresh_workspace_availability(workspaces):
    """Refresh folder visibility without removing registrations or saved chats."""
    for row in workspaces:
        try:
            row['available'] = bool(row.get('path') and Path(row['path']).expanduser().is_dir())
        except (OSError, ValueError):
            row['available'] = False


def initialize(state):
    if "workspaces" not in state:
        from .managed_chats import is_managed
        paths = [state["settings"]["workspace"]] + [s["workspace"] for s in state["sessions"] if not is_managed(s)]
        state["workspaces"] = list({_registration(path)["id"]: _registration(path) for path in paths if path}.values())
    refresh_workspace_availability(state['workspaces'])
    selected = next((s for s in state.get("sessions", []) if s["id"] == state.get("selectedSessionId")), {})
    from .managed_chats import is_managed
    if not is_managed(selected) and state.get("selectedWorkspaceId") not in {w["id"] for w in state["workspaces"]}:
        state["selectedWorkspaceId"] = next((w["id"] for w in state["workspaces"] if w["path"] == state["settings"]["workspace"]), state["workspaces"][0]["id"] if state["workspaces"] else None)
    state.setdefault("canvas", {"open": False, "events": []})
    state["canvas"].setdefault("id", uuid.uuid4().hex)


def select_session_workspace(state, session):
    """Keep a conversation reachable in navigation without changing its folder."""
    initialize(state)
    from .managed_chats import is_managed
    if is_managed(session):
        state['selectedWorkspaceId'] = None
        state['view'].update(navChatScope='all', navLocationFilter='managed', navWorkspaceList=False)
        state['canvas']['open'] = False
        return
    if state['view'].get('navLocationFilter') == 'managed':
        state['view']['navLocationFilter'] = 'all'
    row = next((w for w in state['workspaces'] if w['id'] == session.get('workspaceId')), None)
    if row is None:
        if session.get('workspace'):
            row = _registration(session['workspace'])
        elif session.get('nativeProject') and session.get('workspaceId'):
            row = {'id': session['workspaceId'], 'name': session['nativeProject'], 'path': None,
                   'nativeProject': session['nativeProject'], 'available': False}
        else:
            _error('This chat has no workspace registration.')
    hidden = state.get('hiddenNativeWorkspaces', [])
    restored = {row['id']}
    if row.get('nativeProject'):
        restored.add(uuid.uuid5(uuid.NAMESPACE_URL, 'amplifier-project:' + row['nativeProject']).hex)
    hidden[:] = [identity for identity in hidden if identity not in restored]
    if not any(item["id"] == row["id"] for item in state["workspaces"]):
        state["workspaces"].append(row)
    changed = state["selectedWorkspaceId"] != row["id"]
    state["selectedWorkspaceId"] = row["id"]
    if row.get('path'):
        state["settings"]["workspace"] = row["path"]
    if changed:
        state["canvas"]["open"] = False


def _create_workspace_folder(raw_path):
    """Create a requested folder without replacing or removing existing files."""
    if not raw_path.strip():
        _error("Enter a folder path for the new workspace.")
    try:
        path = Path(raw_path).expanduser().resolve()
        if path.exists() and not path.is_dir():
            _error("A file already exists at this path. Choose a folder for the workspace.")
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            _error("The workspace path must be a folder.")
    except (OSError, RuntimeError, ValueError) as exc:
        # mkdir can have created parents before failing. Never remove them:
        # another process may already have started using one of those folders.
        _error(f"Could not create the workspace folder: {exc}")
    return path


def workspace_command(state, action, args):
    initialize(state)
    rows = state["workspaces"]
    if action in {"workspace.add", "workspace.create"}:
        path = _create_workspace_folder(args["path"]) if action == "workspace.create" else Path(args["path"]).expanduser().resolve()
        if not path.is_dir():
            _error("Choose an existing workspace folder.")
        name = args.get("name", "").strip()
        row = _registration(path, name or None)
        register(rows, row, name=name)
        state["selectedWorkspaceId"] = row["id"]
        state["settings"]["workspace"] = row["path"]
    else:
        try:
            row = edit(rows, action, args)
        except ValueError as exc:
            _error(str(exc))
        if action == "workspace.remove":
            state['pinnedWorkspaceIds'] = [identity for identity in state.get('pinnedWorkspaceIds', [])
                                           if identity != row['id']]
            from .pinned_navigation import ordered_ids
            state['pinnedItemIds'] = ordered_ids(state)
            if state["selectedWorkspaceId"] == row["id"]:
                state["selectedWorkspaceId"] = rows[0]["id"]
                if rows[0].get("path"):
                    state["settings"]["workspace"] = rows[0]["path"]
        elif action == "workspace.select":
            state["selectedWorkspaceId"] = row["id"]
            if row.get('path'):
                state["settings"]["workspace"] = row["path"]
    # Close the old workspace's preview; conversation histories are untouched.
    if action not in {"workspace.rename", "workspace.archive", "workspace.restore"}:
        state["canvas"]["open"] = False
