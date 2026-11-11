"""Named PRIVATE native qualification entry: substitute transport, never update state.

Not imported or shipped by the production host. A separately source-authored test
candidate may include these exact bytes and invoke install(spec) before the real
_host entry. Its source/policy/wheel hashes and this seam MUST be reported. This
qualifies that fixture-host route, not public installer/launcher equivalence.

Only release metadata, the selected tag lookup and artifact HTTP bytes change.
Auth, owner/task/handle/graph checks, public actions, idle/Stop, stage/promotion,
restart/readiness/rollback and process containment remain canonical.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import re

PLAN = "native-update-transport.json"
FIELDS = {"schema", "kind", "target", "failBeforeIdentity"}
TARGET = {"version", "revision", "path", "sha256", "bytes"}


def validate(value):
    if (not isinstance(value, dict) or set(value) != FIELDS
            or type(value["schema"]) is not int or value["schema"] != 1
            or value["kind"] != "private-native-transport-fixture"
            or type(value["failBeforeIdentity"]) is not bool):
        raise ValueError("Invalid private native transport fixture plan.")
    target = value["target"]
    if (not isinstance(target, dict) or set(target) != TARGET
            or not isinstance(target["version"], str)
            or not re.fullmatch(r"\d+\.\d+\.\d+", target["version"])
            or not isinstance(target["revision"], str)
            or not re.fullmatch("[a-f0-9]{40}", target["revision"])
            or not isinstance(target["sha256"], str)
            or not re.fullmatch("[a-f0-9]{64}", target["sha256"])
            or type(target["bytes"]) is not int or not 0 < target["bytes"] <= 134217728
            or not isinstance(target["path"], str)):
        raise ValueError("Invalid private native artifact binding.")
    return target


def install(spec):
    from amplifier_web import app_updates, app_release_artifacts as artifacts
    from amplifier_web import windows_service, windows_files
    from amplifier_web.update_readiness import running_identity
    from amplifier_web.windows_read_files import open_file
    if not windows_service.SUPERVISED_UPDATES_QUALIFIED or not spec.supervised:
        raise ValueError("This entry requires a separately authored private test policy.")
    home = windows_files.address(spec.data_dir)
    # Never borrow a live production data root, or a caller's general path.
    if (home.name != "app" or not re.fullmatch(r"windows-consumer[a-z0-9-]+-sandbox-v[0-9]+", home.parent.name)
            or home.parent.parent.name != "candidates"):
        raise ValueError("The native transport fixture requires its isolated candidate root.")
    with windows_files.directory(home / "config/services", owned_root=home, create=False) as pinned:
        value = json.loads(pinned.read_private(PLAN, limit=8192))
    target = validate(value)
    wheel = windows_files.address(target["path"])
    if (wheel.parent != home.parent / "fixture-input"
            or wheel.name != artifacts.filename(target["version"])):
        raise ValueError("The fixture artifact escaped its exact private input folder.")
    with windows_files.directory(wheel.parent, owned_root=home.parent), open_file(wheel) as stream:
        raw = stream.read(134217729)
    if len(raw) != target["bytes"] or hashlib.sha256(raw).hexdigest() != target["sha256"]:
        raise ValueError("The fixture transport bytes changed.")
    base = artifacts.SOURCE + "/releases/download/v" + target["version"] + "/"
    descriptor = {"url": base + wheel.name, "sha256": target["sha256"], "path": str(wheel)}
    artifacts.verify(descriptor, target["version"], target["revision"])
    identity = running_identity()
    if identity["revision"] == target["revision"] and value["failBeforeIdentity"]:
        raise RuntimeError("Controlled fixture target exits before runtime identity.")
    original_read = app_updates.github_read
    original_process = app_updates.process
    original_get = artifacts._get
    tag = "v" + target["version"]
    async def release_read(path, *args, **kwargs):
        if path == "repos/" + app_updates.REPOSITORY + "/releases/latest":
            return json.dumps({"tag_name": tag, "draft": False, "prerelease": False,
                               "published_at": "2000-01-01T00:00:00Z"})
        if path == ("repos/" + app_updates.REPOSITORY + "/contents/amplifier_web/release-notes.json?ref="
                    + target["revision"]):
            return json.dumps({"releases": []})
        return await original_read(path, *args, **kwargs)
    async def process(*args, **kwargs):
        exact = ("git", "ls-remote", app_updates.SOURCE, "refs/tags/" + tag, "refs/tags/" + tag + "^{}")
        if args == exact:
            return target["revision"] + "\trefs/tags/" + tag
        return await original_process(*args, **kwargs)
    @asynccontextmanager
    async def acquisition(client, url):
        if url not in {base + "SHA256SUMS", base + wheel.name}:
            async with original_get(client, url) as response:
                yield response
            return
        data = ((target["sha256"] + "  " + wheel.name + "\n").encode()
                if url.endswith("SHA256SUMS") else raw)
        class Response:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self, size):
                for start in range(0, len(data), size):
                    yield data[start:start + size]
        yield Response()
    app_updates.github_read = release_read
    app_updates.process = process
    artifacts._get = acquisition
    # No saved application/update/receipt/pointer writes occur in this entry.
    return {"transportSubstituted": True, "publicAcceptance": False,
            "target": {key: target[key] for key in ("version", "revision", "sha256", "bytes")}}
