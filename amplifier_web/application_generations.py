"""Qualified app environments are launch targets, not reinstall inputs.

The uv-managed launcher remains the bootstrap and manual-upgrade authority.
Once it ships this mechanism, app activation changes a private pointer instead
of reinstalling the already-tested packages into the serving environment.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path


def directory(home, revision, generation):
    if not re.fullmatch("[a-f0-9]{40}", revision) or not re.fullmatch(
        "[a-f0-9]{32}", generation
    ):
        raise ValueError("Invalid app generation identity")
    return Path(home).resolve() / "updates/applications" / revision / generation


def active(home):
    path = Path(home) / "updates/application-active.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if data.get("schema") != 1:
        raise ValueError("Unknown app generation receipt")
    folder = directory(home, data["revision"], data["generation"])
    if folder.is_symlink() or not folder.resolve().is_relative_to(Path(home).resolve()):
        raise ValueError("App generation escapes its owner")
    validated = json.loads((folder / "validated.json").read_text())
    if any(
        validated.get(key) != data.get(key)
        for key in ("revision", "generation", "version", "componentDigest")
    ):
        raise ValueError("App generation does not match its qualification receipt")
    return {**data, "folder": folder, "prefix": folder / "tools/amplifier-unified"}


def supports(prefix):
    return (
        any(
            (path / "amplifier_web/application_generations.py").is_file()
            for path in Path(prefix).glob("lib/python*/site-packages")
        )
        or (
            Path(prefix) / "Lib/site-packages/amplifier_web/application_generations.py"
        ).is_file()
    )


def bootstrap_identity(prefix):
    """Local install identity changes when a manual uv upgrade replaces it."""
    prefix = Path(prefix).resolve()
    folders = [
        *prefix.glob("lib/python*/site-packages/amplifier_unified-*.dist-info"),
        *prefix.glob("Lib/site-packages/amplifier_unified-*.dist-info"),
    ]
    if len(folders) != 1:
        raise ValueError("Application bootstrap identity could not be verified")
    digest = hashlib.sha256((folders[0] / "METADATA").read_bytes())
    direct = folders[0] / "direct_url.json"
    digest.update(direct.read_bytes() if direct.exists() else b"<index>")
    return {"prefix": str(prefix), "digest": digest.hexdigest()}


def promote(home, validated, bootstrap):
    from .deployment import write_private

    pointer = active(home)
    record = {
        "schema": 1,
        "bootstrap": bootstrap_identity(bootstrap),
        **{
            key: validated[key]
            for key in ("revision", "generation", "version", "componentDigest")
        },
    }
    if pointer:
        record["previous"] = {
            key: value
            for key, value in pointer.items()
            if key not in {"folder", "prefix", "previous"}
        }
    write_private(Path(home) / "updates/application-active.json", json.dumps(record))
    return active(home)


def delegate(argv=None):
    """Exec the exact qualified interpreter, retaining CLI args and data scope.

    A newer manual uv upgrade takes precedence over an older active generation.
    Explicit development launches do not become a global application bootstrap.
    """
    if os.environ.get("AMPLIFIER_APP_GENERATION_EXEC") or not Path(
        __file__
    ).resolve().is_relative_to(Path(sys.prefix).resolve()):
        return
    args = list(sys.argv[1:] if argv is None else argv)
    from .host.config import app_home

    home = app_home()
    for index, argument in enumerate(args):
        if argument == "--data-dir" and index + 1 < len(args):
            home = Path(args[index + 1]).expanduser().resolve()
        elif argument.startswith("--data-dir="):
            home = Path(argument.split("=", 1)[1]).expanduser().resolve()
    target = active(home)
    if not target:
        return
    # A source checkout, development venv, or manually replaced uv tool must
    # never be redirected by another installation's generation pointer.
    try:
        owner = bootstrap_identity(sys.prefix)
    except (OSError, ValueError):
        return
    if target.get("bootstrap") != owner:
        return
    from . import __version__
    from .app_updates import version_tuple

    if version_tuple(__version__) > version_tuple(target["version"]):
        return
    # The private generation's interpreter and packages must still agree. The
    # serving host performs its separate health/readiness qualification too.
    import subprocess

    from .app_component_graph import GRAPH_PROBE, digest, normalized

    python = target["prefix"] / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    result = subprocess.run(
        [str(python), "-I", "-c", GRAPH_PROBE],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if (
        result.returncode
        or digest(normalized(json.loads(result.stdout))) != target["componentDigest"]
    ):
        raise ValueError("Active app packages changed; its generation was preserved")
    os.environ["AMPLIFIER_APP_GENERATION_EXEC"] = str(target["generation"])
    os.environ["AMPLIFIER_WEB_HOME"] = str(home)
    # Match qualification's isolated import path. A repository in the caller's
    # working directory must not replace the app in this tested environment.
    os.execv(str(python), [str(python), "-I", "-m", "amplifier_web", *args])
