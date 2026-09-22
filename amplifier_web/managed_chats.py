"""Application-owned chat folders. Managed storage is not a security sandbox."""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

LOCATION = {"type": "object", "properties": {"kind": {"enum": ["workspace", "managed"]}},
            "required": ["kind"], "additionalProperties": False}


def is_managed(value):
    return isinstance(value, dict) and isinstance(value.get("location"), dict) and value["location"].get("kind") == "managed"


def metadata(workspace):
    """Read the host allocation marker outside the chat's files directory."""
    path = Path(workspace).expanduser().absolute()
    if path.name != "files" or path.parent.parent.name != "chats":
        return None
    try:
        if str(uuid.UUID(path.parent.name)) != path.parent.name:
            return None
        value = json.loads((path.parent / "managed-chat.json").read_text())
        if isinstance(value, dict) and value.get("version") == 1 and value.get("id") == path.parent.name and value.get("workspace") == str(path):
            return value
    except (OSError, ValueError, TypeError):
        pass
    return None


def creation_identity(home, args, command_id):
    return args.get("id") or (str(uuid.uuid5(uuid.NAMESPACE_URL,
        str(Path(home).resolve()) + ":managed-chat:" + command_id)) if command_id else str(uuid.uuid4()))


def allocate(home, identity, receipt):
    """Create one private folder; retries verify ownership and never replace files."""
    if str(uuid.UUID(identity)) != identity:
        raise ValueError("Use a canonical UUID for a managed chat")
    root = Path(home).resolve() / "chats"
    directory, files = root / identity, root / identity / "files"
    for path in (root, directory, files):
        if path.is_symlink():
            raise ValueError("A managed chat folder was replaced by a symbolic link; its files were preserved.")
    root.mkdir(mode=0o700, exist_ok=True)
    marker = directory / "managed-chat.json"
    expected = {"version": 1, "id": identity, "workspace": str(files), "creationReceipt": receipt}
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        if not directory.is_dir() or marker.is_symlink() or not marker.is_file():
            raise ValueError("The managed chat folder already exists without its allocation record; its files were preserved.") from None
        if json.loads(marker.read_text()) != expected:
            raise ValueError("This managed chat folder belongs to a different creation request.")
    else:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(expected, stream)
    files.mkdir(mode=0o700, exist_ok=True)
    if not files.is_dir():
        raise ValueError("The managed chat files location is not a directory.")
    return str(files)


def catalog_locations(snapshot):
    """Resolve each managed native root once, off the server event loop."""
    paths = {row.get('path') for row in snapshot.get('workspaces', [])}
    paths.update(row.get('workspace') for row in snapshot.get('sessions', []))
    return {path: Path(path).is_dir() for path in paths if path and metadata(path)}
