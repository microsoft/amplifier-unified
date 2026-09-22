"""Read-only discovery of this process's artifact runtime.

Installed package metadata is evidence of installation, not successful import or
rendering. Optional import probes run fixed public libraries in isolated children.
No dependency is installed or workspace code run.
"""
from __future__ import annotations

import asyncio
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
    proc = None
    observed = {}
    try:
        with tempfile.TemporaryDirectory(prefix='amplifier-artifact-probe-') as cwd:
            proc = await asyncio.create_subprocess_exec(sys.executable, '-I', '-c', code,
                cwd=cwd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=16384, start_new_session=os.name != 'nt')
            async with asyncio.timeout(15):
                data = await proc.stdout.read(16385)
                if len(data) <= 16384:
                    await proc.wait()
                    if proc.returncode == 0:
                        line = next((line for line in data.decode('utf-8', 'replace').splitlines()
                            if line.startswith('ARTIFACT_IMPORTS=')), '')
                        observed = json.loads(line.partition('=')[2]) if line else {}
    except (OSError, TimeoutError, ValueError):
        pass
    finally:
        if proc is not None:
            try:
                if os.name != 'nt': os.killpg(proc.pid, signal.SIGKILL)
                elif proc.returncode is None: proc.kill()
            except ProcessLookupError:
                pass
            async with asyncio.timeout(2):
                while await proc.stdout.read(4096):
                    pass
                await proc.wait()
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
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(row["path"], *spec["args"],
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, limit=4096, start_new_session=os.name != "nt")
        async with asyncio.timeout(2):
            output = await proc.stdout.read(2049)
            if len(output) > 2048:
                return {**row, "versionStatus": "unknown", "reason": "Version output exceeded its limit."}
            await proc.wait()
        match = re.search(spec["pattern"], output.decode("utf-8", "replace"))
        if proc.returncode == 0 and match:
            return {**row, "version": match[1], "versionStatus": "verified"}
        return {**row, "versionStatus": "unknown", "reason": "Executable did not return a recognized version."}
    except (TimeoutError, OSError):
        return {**row, "versionStatus": "unknown", "reason": "Version probe failed or timed out."}
    finally:
        if proc is not None:
            try:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGKILL)
                elif proc.returncode is None:
                    proc.kill()
            except ProcessLookupError:
                pass
            # A full StreamReader pauses its pipe and can keep wait() pending
            # even after SIGKILL. Drain without retaining any further output.
            async with asyncio.timeout(2):
                while await proc.stdout.read(4096):
                    pass
                await proc.wait()


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
