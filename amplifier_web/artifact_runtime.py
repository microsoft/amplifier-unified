"""Read-only discovery of this process's artifact runtime.

Installed package metadata is evidence of installation, not successful import or
rendering. Optional import probes run fixed public libraries in isolated children.
No dependency is installed or workspace code run.
"""
from __future__ import annotations

import asyncio
import base64
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import sys
import tempfile
import time


PYTHON_PACKAGES = (
    "python-docx", "python-pptx", "openpyxl", "reportlab", "pypdf", "Pillow",
    "numpy", "pandas", "matplotlib",
)
ARTIFACT_IMPORTS = {"python-docx": "docx", "python-pptx": "pptx", "openpyxl": "openpyxl",
    "reportlab": "reportlab", "pypdf": "pypdf", "Pillow": "PIL"}


async def _close_probe(proc):
    """Ask the still-owned supervisor to clean its group; never signal its PID."""
    try:
        proc.stdin.close()
        async with asyncio.timeout(2):
            trailer = await proc.stdout.read(128)
            await proc.wait()
        return trailer == b'{"cleanup":"group"}\n' and proc.returncode == -signal.SIGKILL
    except (OSError, TimeoutError, ValueError):
        return False


async def _finish_probe(start):
    try:
        proc = await start
    except OSError:
        return False
    return await _close_probe(proc)


async def _await_cleanup(cleanup):
    # A second cancellation must not abandon spawn recovery or pipe closure.
    # The independent finalizer owns both; cancellation still reaches the caller
    # after it completes, even when it first arrives during successful cleanup.
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(cleanup)
            break
        except asyncio.CancelledError:
            if cleanup.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


async def _probe(command, *, limit, timeout, stderr=False, cwd=None):
    # There is no portable Windows process-group equivalent here. Do not run a
    # probe whose descendants this helper cannot own and close.
    if os.name != 'posix':
        return None
    result = None
    start = asyncio.create_task(asyncio.create_subprocess_exec(sys.executable, '-I',
        str(Path(__file__).with_name('_artifact_probe.py')), cwd=cwd,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, limit=32768, start_new_session=True))
    try:
        # Recover the owned process if cancellation arrives during creation.
        proc = await asyncio.shield(start)
        async with asyncio.timeout(timeout + 1):
            proc.stdin.write(json.dumps({'command': list(command), 'limit': limit,
                'timeout': timeout, 'stderr': stderr}).encode() + b'\n')
            await proc.stdin.drain()
            result = json.loads(await proc.stdout.readline())
    except (OSError, TimeoutError, ValueError):
        pass
    finally:
        closed = await _await_cleanup(asyncio.create_task(_finish_probe(start)))
    if not closed or not isinstance(result, dict):
        return None
    if result.get('status') == 'completed':
        try:
            output = base64.b64decode(result['output'], validate=True)
            if len(output) > limit or type(result['returncode']) is not int:
                return None
            return {**result, 'output': output}
        except (KeyError, ValueError, TypeError):
            return None
    return result


async def verify_imports(packages):
    """Probe only fixed public libraries in a clean child of this interpreter.

    Isolated mode excludes workspace/PYTHONPATH lookalikes. Imports never run
    inside the worker itself, and neither stdout nor exception messages escape.
    """
    code = ('import importlib,json\nresults={}\n'
        'for name,module in ' + repr(ARTIFACT_IMPORTS) + '.items():\n'
        ' try:\n  importlib.import_module(module)\n  results[name]="passed"\n'
        ' except Exception as error:\n  results[name]=type(error).__name__\n'
        'print("ARTIFACT_IMPORTS="+json.dumps(results))\n')
    observed = {}
    try:
        with tempfile.TemporaryDirectory(prefix='amplifier-artifact-probe-') as cwd:
            result = await _probe([sys.executable, '-I', '-c', code],
                cwd=cwd, limit=16384, timeout=15)
            if result and result['status'] == 'completed' and result['returncode'] == 0:
                line = next((line for line in result['output'].decode('utf-8', 'replace').splitlines()
                    if line.startswith('ARTIFACT_IMPORTS=')), '')
                observed = json.loads(line.partition('=')[2]) if line else {}
    except (OSError, TimeoutError, ValueError):
        pass
    for row in packages:
        if row['name'] in ARTIFACT_IMPORTS:
            status = observed.get(row['name'], 'unknown')
            row['importVerified'] = status == 'passed'
            row['importStatus'] = 'passed' if status == 'passed' else 'unknown' if status == 'unknown' else 'failed'
    return 'passed' if all(observed.get(name) == 'passed' for name in ARTIFACT_IMPORTS) else 'failed' if observed else 'unknown'
EXECUTABLES = {
    "node": {"names": ("node",), "args": ("--version",), "pattern": r"v(\d+\.\d+\.\d+[^\s]*)"},
    "libreoffice": {"names": ("soffice", "libreoffice"), "override": "WORK_SOFFICE",
        "args": ("--version",), "pattern": r"LibreOffice(?:Dev)?\s+(\d+[\w.\-]*)"},
    "pdftoppm": {"names": ("pdftoppm",), "override": "WORK_PDFTOPPM",
        "args": ("-v",), "pattern": r"pdftoppm version\s+(\d+[\w.\-]*)"},
}


def _executable(spec):
    override = spec.get("override")
    value = os.environ.get(override, "") if override else ""
    if value:
        path = Path(value).expanduser()
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            return {"status": "invalid", "source": override,
                "reason": "Override must name an absolute executable file."}
        return {"status": "available", "source": override, "path": str(path.resolve())}
    # A project can have a node/soffice lookalike. Discovery never executes
    # relative PATH entries or the current working directory implicitly.
    cwd = Path.cwd().resolve()
    search = os.pathsep.join(str(Path(entry)) for entry in os.get_exec_path()
        if Path(entry).is_absolute() and Path(entry).resolve() != cwd)
    for name in spec["names"]:
        found = shutil.which(name, path=search)
        if found:
            return {"status": "available", "source": "PATH", "path": str(Path(found).resolve())}
    if "soffice" in spec["names"] and sys.platform == "darwin":
        found = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        if found.is_file() and os.access(found, os.X_OK):
            return {"status": "available", "source": "application", "path": str(found.resolve())}
    return {"status": "missing", "source": "PATH"}


async def _version(row, spec):
    if row["status"] != "available":
        return row
    result = await _probe([row['path'], *spec['args']], limit=2048, timeout=2, stderr=True)
    if result and result.get('status') == 'overflow':
        return {**row, "versionStatus": "unknown", "reason": "Version output exceeded its limit."}
    if not result or result.get('status') != 'completed':
        return {**row, "versionStatus": "unknown", "reason": "Version probe failed or timed out."}
    match = re.search(spec['pattern'], result['output'].decode('utf-8', 'replace'))
    if result['returncode'] == 0 and match:
        return {**row, "version": match[1], "versionStatus": "verified"}
    return {**row, "versionStatus": "unknown", "reason": "Executable did not return a recognized version."}


def _packages():
    result = []
    for name in PYTHON_PACKAGES:
        try:
            package = metadata.distribution(name)
            result.append({"name": name, "status": "installed", "version": package.version,
                "location": str(Path(package.locate_file("")).resolve()), "importVerified": False})
        except metadata.PackageNotFoundError:
            result.append({"name": name, "status": "missing", "importVerified": False})
        except (OSError, ValueError, TypeError):
            result.append({"name": name, "status": "unknown", "importVerified": False})
    return result


async def discover(scope="host", *, verify=False):
    """Describe only the current interpreter; never infer another venv's state."""
    packages = await asyncio.to_thread(_packages)
    imports = await verify_imports(packages) if verify else 'not_run'
    rows = await asyncio.to_thread(lambda: {name: _executable(spec) for name, spec in EXECUTABLES.items()})
    versions = await asyncio.gather(*(_version(rows[name], spec) for name, spec in EXECUTABLES.items()))
    return {"schemaVersion": 1, "scope": scope, "status": "available", "observedAt": time.time(),
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "python": {"path": sys.executable, "version": platform.python_version(),
            "prefix": sys.prefix, "packages": packages},
        "executables": dict(zip(EXECUTABLES, versions)),
        "validation": {"imports": imports, "rendering": "not_run", "visualReview": "not_run", "spreadsheetRecalculation": "not_run"},
        "notes": ["Use the returned Python executable to use its installed packages.",
            "Node package availability has not been inspected.",
            "Installed libraries do not prove rendering, visual review, or spreadsheet recalculation."]}
