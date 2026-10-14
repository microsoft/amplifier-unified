"""Retire proven update generations; unknown storage and user work are retained.

Only successful app-owned ecosystem generations are eligible. Current,
rollback, pending, process-referenced and configuration-referenced generations
remain. This is not a global Amplifier/uv cache cleaner or a history migration.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path


MAX_CANDIDATES = 4
PASS_SECONDS = 30
INSPECTION_SECONDS = 5
REFERENCE_SECONDS = 2


async def _probe(operation, values, *, timeout):
    """Run owned storage work in a process we can actually stop.

    Qualification invokes Git and hashes installed sources. Cancelling a
    to_thread await does not stop that work (or its subprocesses). Removal
    operates only on a detached retirement payload, outside admission.
    Private reference text is returned only to this caller, never diagnostics.
    """
    proc = await asyncio.create_subprocess_exec(
        sys.executable, '-m', __name__, operation,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, start_new_session=os.name != 'nt',
    )
    try:
        output, _ = await asyncio.wait_for(
            proc.communicate(json.dumps(values).encode()), timeout,
        )
        if proc.returncode:
            raise ValueError('Storage inspection could not be verified')
        return json.loads(output)
    finally:
        if proc.returncode is None:
            try:
                if os.name != 'nt':
                    import signal
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except ProcessLookupError:
                pass
            # Drain pipes after cancellation as well: wait() alone can wait
            # forever for a paused stdout transport after a large response.
            await proc.communicate()


async def inspect_references(manager, *, timeout):
    # Copy only placement metadata; never load conversation bodies or events.
    workspaces = sorted({row['workspace'] for row in manager.service.state.get('sessions', [])
                         if row.get('workspace')})
    return await _probe('references', {'home': str(manager.home), 'workspaces': workspaces}, timeout=timeout)


async def verify_generation(project, folder, *, timeout):
    await _probe('generation', {'project': str(project), 'folder': str(folder)}, timeout=timeout)


def maintenance_blocked(manager):
    state = manager.service.state['updates']
    return (getattr(manager, 'closed', False) or getattr(manager.service, 'closed', False)
            or manager.awaiting_restart()
            or bool(getattr(manager, 'lock', None) and manager.lock.locked())
            or bool(getattr(manager, 'check_task', None) and not manager.check_task.done())
            or any(state.get(key) for key in ('pendingRelease', 'pendingRollback', 'pendingApp', 'pendingSmartTools'))
            or state.get('phase') in {'checking', 'staging', 'validating', 'activating', 'app-staged'})


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


def protected_generations(manager):
    """Fresh pointer, pending intent, worker and transitive source protection."""
    from .updates import active_release
    from .generation_leases import references as worker_references

    state = manager.service.state['updates']
    pointer = active_release(manager.home)
    protected = {
        pointer.get("current"),
        pointer.get("previous"),
        state.get("pendingRelease"),
        state.get("pendingRollback"),
    }
    protected.update(worker_references(manager.home))
    for path in (Path(manager.home)/'updates/profiles').glob('*.json'):
        entry = json.loads(path.read_text())
        if entry.get('parentGeneration') in protected:
            protected.add(entry['generation'])
    root = Path(manager.directory) / "releases"
    # A retained environment may contain an explicit editable source from an
    # earlier generation. Follow those edges before deleting any source tree.
    # Shared immutable store objects are outside generation deletion entirely.
    changed = True
    while changed:
        changed = False
        for identity in tuple(protected):
            if not identity:
                continue
            graph = root / identity / 'runtime-installed.json'
            if not graph.exists():
                continue
            text = graph.read_text()
            for source in re.findall(re.escape(str(root)) + r'/([a-f0-9]{32})/', text):
                if source not in protected:
                    protected.add(source)
                    changed = True
    return protected


def _folders(manager):
    root = Path(manager.directory) / 'releases'
    return sorted(folder for folder in root.iterdir()
                  if re.fullmatch('[a-f0-9]{32}', folder.name)
                  and not folder.is_symlink() and folder.is_dir()) if root.exists() else []


def retire(manager, folder, project, *, remove_project):
    """Only metadata renames hold admission; partial retirement is preserved."""
    from .deployment import write_private

    root = Path(manager.directory) / 'retired'
    if root.is_symlink():
        raise ValueError('Unknown retirement storage')
    root.mkdir(exist_ok=True)
    entry = root / uuid.uuid4().hex
    entry.mkdir(mode=0o700)
    payload = entry / 'payload'
    payload.mkdir()
    record = {'schema': 1, 'phase': 'retiring', 'generation': folder.name,
              'project': project.name if remove_project else None}
    write_private(entry / 'retirement.json', json.dumps(record))
    # These renames stay on the app-owned filesystem. If either fails, retain
    # the incomplete receipt and both locations; do not guess a rollback.
    folder.rename(payload / 'release')
    if remove_project:
        project.rename(payload / 'runtime')
    record['phase'] = 'retired'
    write_private(entry / 'retirement.json', json.dumps(record))
    return entry


async def remove_retired(manager, entry, *, timeout):
    """Reclaim only a completed, owned retirement; resume interrupted removal."""
    root = Path(manager.directory) / 'retired'
    if (root.is_symlink() or entry.parent != root or entry.is_symlink()
            or not re.fullmatch('[a-f0-9]{32}', entry.name)):
        raise ValueError('Unknown retirement storage')
    record = json.loads((entry / 'retirement.json').read_text())
    if (record.get('schema') != 1 or record.get('phase') != 'retired'
            or not re.fullmatch('[a-f0-9]{32}', record.get('generation', ''))
            or (record.get('project') is not None and not re.fullmatch(
                '(?:q-[a-f0-9]{32}(?:-[a-f0-9]{8})?|[a-f0-9]{16})', record['project']))):
        raise ValueError('Incomplete retirement retained')
    payload = entry / 'payload'
    if payload.is_symlink():
        raise ValueError('Unknown retirement storage')
    if payload.exists():
        if any(row.name not in {'release', 'runtime'} or row.is_symlink() for row in payload.iterdir()):
            raise ValueError('Unknown retirement storage')
        await _probe('remove', {'path': str(payload)}, timeout=timeout)
    # Keep the receipt outside recursive removal so a cancelled subprocess is
    # safely resumable on a later pass or restart.
    (entry / 'retirement.json').unlink()
    entry.rmdir()


async def reclaim(manager):
    """Inspect a bounded batch without holding runtime admission hostage."""
    from .runtime_environment import project_path
    from .updates import process

    removed, retained, checked = 0, 0, 0
    deadline = time.monotonic() + PASS_SECONDS
    def remaining(limit):
        value = min(limit, deadline - time.monotonic())
        if value <= 0:
            raise TimeoutError('Storage maintenance budget exhausted')
        return value
    def result(reason=None):
        cursors = {key: getattr(manager, attribute) for key, attribute in (
            ('generationCursor', '_retention_cursor'), ('retiredCursor', '_retired_cursor'))
            if getattr(manager, attribute, None)}
        return {'removed': removed, 'retained': retained, **cursors, **({'reason': reason} if reason else {})}
    prior = manager.service.state['updates'].get('storage', {})
    if maintenance_blocked(manager):
        return result('Storage maintenance deferred while an update is pending.')
    retired = Path(manager.directory) / 'retired'
    if retired.is_symlink():
        return result('Unknown retirement storage was kept.')
    entries = sorted(retired.iterdir()) if retired.exists() else []
    cursor = getattr(manager, '_retired_cursor', prior.get('retiredCursor', ''))
    entries = [p for p in entries if p.name > cursor] + [p for p in entries if p.name <= cursor]
    for entry in entries:
        if maintenance_blocked(manager):
            return result('Storage maintenance deferred while an update is pending.')
        if checked >= MAX_CANDIDATES or time.monotonic() >= deadline:
            return result('Storage maintenance reached its batch limit; remaining storage was kept.')
        checked += 1
        manager._retired_cursor = entry.name
        try:
            await remove_retired(manager, entry, timeout=remaining(INSPECTION_SECONDS))
            removed += 1
        except (OSError, ValueError, TypeError, TimeoutError):
            retained += 1
    try:
        references = await inspect_references(manager, timeout=remaining(REFERENCE_SECONDS))
        protected = protected_generations(manager)
    except (OSError, ValueError, KeyError, TypeError, TimeoutError, subprocess.SubprocessError):
        return result('References could not be verified; all storage was kept.')
    folders = _folders(manager)
    cursor = getattr(manager, '_retention_cursor', prior.get('generationCursor', ''))
    folders = [p for p in folders if p.name > cursor] + [p for p in folders if p.name <= cursor]
    for folder in folders:
        if maintenance_blocked(manager):
            return result('Storage maintenance deferred while an update is pending.')
        if checked >= MAX_CANDIDATES or time.monotonic() >= deadline:
            return result('Storage maintenance reached its batch limit; remaining storage was kept.')
        manager._retention_cursor = folder.name
        marker = folder / "validated.json"
        if (
            folder.name in protected
            or str(folder) in references
            or not marker.is_file()
        ):
            retained += 1
            continue
        try:
            marker_text = marker.read_text()
            data = json.loads(marker_text)
            if not isinstance(data.get("hostVersion"), str) or not isinstance(
                data.get("sources"), int
            ):
                raise TypeError("Unknown generation receipt")
            project = project_path(manager.home, folder.name)
            modern = data.get("generationSchema") == 1
            if (not modern and manager.busy()) or str(project) in references or project.is_symlink():
                raise ValueError("Runtime remains referenced")
            # Exact qualification still has to hold before retiring its files.
            graph = folder / 'runtime-installed.json'
            if not graph.exists():
                raise ValueError("No installed graph proof")
            graph_text = graph.read_text()
            checked += 1
            await verify_generation(project, folder, timeout=remaining(INSPECTION_SECONDS))
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
                    timeout=remaining(INSPECTION_SECONDS),
                )
                extras = await process(
                    "git", "ls-files", "--others", cwd=source, timeout=remaining(INSPECTION_SECONDS)
                )
                if changes or any(
                    name != ".amplifier_cache_meta.json" for name in extras.splitlines()
                ):
                    raise ValueError("Local or generated changes retained")
            # Activation may have won while inspection yielded. No cleanup
            # lock is held during qualification, Git inspection or hashing.
            if maintenance_blocked(manager):
                return result('Storage maintenance deferred while an update is pending.')
            async with manager.service.runtime_lifecycle():
                if maintenance_blocked(manager):
                    return result('Storage maintenance deferred while an update is pending.')
                # Refresh external process/configuration references at the
                # deletion boundary. This read-only probe has a short timeout;
                # incomplete inspection always retains the candidate.
                fresh = await inspect_references(manager, timeout=remaining(REFERENCE_SECONDS))
                if maintenance_blocked(manager):
                    return result('Storage maintenance deferred while an update is pending.')
                if (folder.name in protected_generations(manager)
                        or str(folder) in fresh or str(project) in fresh or (not modern and manager.busy())
                        or folder.is_symlink() or project.is_symlink()
                        or project_path(manager.home, folder.name) != project
                        or marker.read_text() != marker_text or graph.read_text() != graph_text):
                    retained += 1
                    continue
                # Recompute sharing from all surviving receipts, including
                # generations created while the inspection was in progress.
                root = Path(manager.directory) / 'releases'
                surviving_projects = {project_path(manager.home, row.name).resolve()
                                      for row in root.iterdir() if row.is_dir() and row != folder}
                remove_project = (project.resolve() not in surviving_projects and project.exists()
                                  and project.parent.resolve() == (Path(manager.home) / 'runtime').resolve())
                entry = retire(manager, folder, project, remove_project=remove_project)
            # Recursive deletion may be slow or cancelled. The generation is
            # already detached, so it cannot delay activation or shutdown.
            if maintenance_blocked(manager):
                return result('Retired storage was kept while an update is pending.')
            await remove_retired(manager, entry, timeout=remaining(INSPECTION_SECONDS))
            removed += 1
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
    return result()


if __name__ == '__main__':
    values = json.load(sys.stdin)
    if sys.argv[1] == 'references':
        from types import SimpleNamespace
        manager = SimpleNamespace(home=values['home'], service=SimpleNamespace(state={
            'sessions': [{'workspace': value} for value in values['workspaces']]}))
        print(json.dumps(process_references(manager.home) + configuration_references(manager)))
    elif sys.argv[1] == 'generation':
        from .runtime_qualification import verify_recorded
        verify_recorded(Path(values['project']), Path(values['folder']))
        print('null')
    elif sys.argv[1] == 'remove':
        shutil.rmtree(values['path'])
        print('null')
    else:
        raise ValueError('Unknown storage inspection')
