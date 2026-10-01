"""Retire proven update generations; unknown storage and user work are retained.

Only successful app-owned ecosystem generations are eligible. Current,
rollback, pending, process-referenced and configuration-referenced generations
remain. This is not a global Amplifier/uv cache cleaner or a history migration.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path


def process_references(home):
    """Keep command lines private; inability to inspect means no reclamation."""
    result = subprocess.run(
        ["ps", "-axo", "command="],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode:
        raise ValueError("Process references could not be checked")
    root = str(Path(home).resolve())
    return "\n".join(line for line in result.stdout.splitlines() if root in line)


def configuration_references(manager):
    # Settings paths, registry receipts and worker locks may explicitly select
    # an older local source. A display "usage unknown" must never delete it.
    from .session_files import amplifier_home

    home = Path(manager.home)
    paths = list((home / "config").rglob("*.yaml"))
    shared = amplifier_home()
    paths.append(shared / "settings.yaml")
    for session in manager.service.state.get("sessions", []):
        workspace = session.get("workspace")
        if workspace:
            paths.extend(
                Path(workspace) / ".amplifier" / name
                for name in ("settings.yaml", "settings.local.yaml")
            )
    paths.extend((shared / "projects").glob("*/sessions/*/settings.yaml"))
    text = []
    for path in set(paths):
        if path.exists():
            text.append(path.read_text())
    return "\n".join(text)


async def reclaim(manager):
    """Called after successful activation while holding the runtime lifecycle."""
    from .runtime_environment import project_path
    from .runtime_qualification import verify_recorded
    from .updates import active_release, process

    state = manager.service.state["updates"]
    if manager.busy() or manager.awaiting_restart():
        return {"removed": 0, "retained": 0}
    try:
        references = await asyncio.to_thread(process_references, manager.home)
        references += await asyncio.to_thread(configuration_references, manager)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {
            "removed": 0,
            "retained": 0,
            "reason": "References could not be verified; all storage was kept.",
        }
    pointer = active_release(manager.home)
    protected = {
        pointer.get("current"),
        pointer.get("previous"),
        state.get("pendingRelease"),
        state.get("pendingRollback"),
    }
    root = Path(manager.directory) / "releases"
    removed, retained = 0, 0
    candidates = []
    for folder in sorted(root.iterdir()) if root.exists() else []:
        if (
            not re.fullmatch("[a-f0-9]{32}", folder.name)
            or folder.is_symlink()
            or not folder.is_dir()
        ):
            continue
        marker = folder / "validated.json"
        if (
            folder.name in protected
            or str(folder) in references
            or not marker.is_file()
        ):
            retained += 1
            continue
        try:
            data = json.loads(marker.read_text())
            if not isinstance(data.get("hostVersion"), str) or not isinstance(
                data.get("sources"), int
            ):
                raise TypeError("Unknown generation receipt")
            project = project_path(manager.home, folder.name)
            if str(project) in references or project.is_symlink():
                raise ValueError("Runtime remains referenced")
            # Exact qualification still has to hold before retiring its files.
            if not (folder / "runtime-installed.json").exists():
                raise ValueError("No installed graph proof")
            await asyncio.to_thread(verify_recorded, project, folder)
            for meta in (folder / "foundation/cache").rglob(
                ".amplifier_cache_meta.json"
            ):
                source = meta.parent
                if source.is_symlink() or not source.resolve().is_relative_to(
                    folder.resolve()
                ):
                    raise ValueError("External source reference")
                changes = await process(
                    "git",
                    "--no-optional-locks",
                    "status",
                    "--porcelain",
                    "--untracked-files=no",
                    cwd=source,
                    timeout=10,
                )
                extras = await process(
                    "git", "ls-files", "--others", cwd=source, timeout=10
                )
                if changes or any(
                    name != ".amplifier_cache_meta.json" for name in extras.splitlines()
                ):
                    raise ValueError("Local or generated changes retained")
            candidates.append((folder, project))
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            RuntimeError,
            TimeoutError,
            subprocess.SubprocessError,
        ):
            retained += 1
    surviving_projects = set()
    for folder in root.iterdir() if root.exists() else []:
        if folder.is_dir() and folder not in {row[0] for row in candidates}:
            try:
                surviving_projects.add(
                    project_path(manager.home, folder.name).resolve()
                )
            except (OSError, ValueError):
                return {
                    "removed": 0,
                    "retained": retained,
                    "reason": "An unknown project reference was kept.",
                }
    for folder, project in candidates:
        # No new work is admitted by the caller's lifecycle lock. Recheck busy
        # before each removal. Never delete baseline, shared sources or uv tools.
        if manager.busy():
            break
        await asyncio.to_thread(shutil.rmtree, folder)
        if (
            project.resolve() not in surviving_projects
            and project.exists()
            and project.parent.resolve() == (Path(manager.home) / "runtime").resolve()
        ):
            await asyncio.to_thread(shutil.rmtree, project)
        removed += 1
    return {"removed": removed, "retained": retained}
