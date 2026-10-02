"""Explicit qualification plans and reusable generation receipts.

Unknown sources require full worker qualification. Data-only reuse requires an
allowlisted data contract, an exact before/after tree comparison, and a previously
qualified environment. Repository names alone never grant the fast path.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

SCHEMA = 1
CATALOG = "https://github.com/microsoft/amplifier-smart-tools-catalog"


def catalog_data(path):
    path = Path(path)
    return (
        path.name
        in {"README.md", "LICENSE", "SECURITY.md", "SUPPORT.md", "CODE_OF_CONDUCT.md"}
        and len(path.parts) == 1
        or path.parts[0] == "tools"
        and path.name in {"SMART_TOOL.md", "source.json", "provenance.json"}
        or path.as_posix() == "catalog.json"
    )


async def tree(root):
    from .updates import process

    records = await process("git", "ls-tree", "-rz", "HEAD", cwd=root, raw=True)
    return {
        row.split(b"\t", 1)[1].decode(): row.split(b"\t", 1)[0].decode()
        for row in records.split(b"\0")
        if row
    }


async def build(manager, stage, candidates):
    from .runtime_environment import receipt_directory
    from .updates import active_release, foundation_home, source_key

    previous = active_release(manager.home).get("current")
    receipt = receipt_directory(manager.home, previous)
    mode, reason = "worker", "Changed runtime or bundle inputs need preparation"
    reusable = (receipt / "runtime-project.json").is_file() and (
        receipt / "runtime-installed.json"
    ).is_file()
    descriptor = receipt / "profiles-qualified.json"
    if reusable and descriptor.is_file():
        from .bundles import offered_profiles
        from .host.config import read_config
        from .runtime_profiles import configuration_key

        workspace = stage / "qualification-workspace"
        workspace.mkdir(exist_ok=True)
        config = read_config(
            workspace, home=stage, shared_home=stage / "shared-config", global_only=True
        )
        offered = json.loads(descriptor.read_text())
        reusable = offered.get("configuration") == configuration_key(
            config.settings, manager.home, stage
        ) and offered.get("profiles") == offered_profiles(config)
    else:
        reusable = False  # Legacy qualification did not freeze all offered profiles.
    data_only = reusable and bool(candidates)
    changed = []
    for row in candidates:
        if (
            row.get("kind")
            in {"runtime dependency", "runtime environment", "included source"}
            or not row.get("url")
            or source_key(row["url"], row.get("ref") or "HEAD")[0] != CATALOG
        ):
            data_only = False
            break
        if row.get("sharedSource"):
            from amplifier_foundation.sources.shared import SharedSourceStore

            from .update_sources import store_environment

            store = SharedSourceStore(
                store_environment(manager.home)["AMPLIFIER_SOURCE_STORE"]
            )
            before = store.checkout(row["url"], row["current"])
            after = store.checkout(row["url"], row["latest"])
        else:
            before, after = (
                foundation_home(manager.home) / row["path"],
                stage / "foundation" / row["path"],
            )
        old, new = await asyncio.gather(tree(before), tree(after))
        paths = {
            path for path in old.keys() | new.keys() if old.get(path) != new.get(path)
        }
        # Symlinks, executables and unknown formats require normal preparation.
        if not paths or any(
            not catalog_data(path)
            or new.get(path, "100644 blob").split()[0] != "100644"
            for path in paths
        ):
            data_only = False
            break
        for path in paths:
            if path in new and path.endswith(".json"):
                json.loads((after / path).read_text())
        changed.extend(sorted(paths))
    if data_only:
        mode, reason = (
            "data",
            "Only catalog data changed; reuse the qualified worker environment",
        )
    return {
        "schema": SCHEMA,
        "mode": mode,
        "reason": reason,
        "previous": previous,
        "changedFiles": sorted(set(changed)),
        "activation": "per-worker",
        "workerProtocol": 1,
    }


async def reuse_runtime(manager, stage, plan):
    from .runtime_environment import project_path, receipt_directory
    from .runtime_qualification import verify_recorded

    previous = plan["previous"]
    source = receipt_directory(manager.home, previous)
    project = project_path(manager.home, previous)
    await asyncio.to_thread(verify_recorded, project, source)
    # No installer and no mounting synthetic workers for a catalog edit.
    # Copy small evidence files; all generations refer to the same immutable venv.
    for path in source.glob("runtime*"):
        if path.is_file():
            shutil.copy2(path, stage / path.name)
    for name in ("profiles.json", "profiles-qualified.json"):
        if (source / name).is_file():
            shutil.copy2(source / name, stage / name)
