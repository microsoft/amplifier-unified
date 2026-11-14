"""Qualified app environments are launch targets, not reinstall inputs.

The uv-managed launcher remains the bootstrap and manual-upgrade authority.
Once it ships this mechanism, app activation changes a private pointer instead
of reinstalling the already-tested packages into the serving environment.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
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
    home = Path(home).resolve()
    legacy = home / "updates/applications" / revision / generation
    if sys.platform != "win32":
        return legacy
    # Windows DLL loading can still refuse >MAX_PATH package files even when
    # Git/Python filesystem operations succeeded. Keep full source/attempt
    # identities in the immutable qualification, not duplicated in this path.
    compact = home / "updates/a" / generation
    old_exists, compact_exists = os.path.lexists(legacy), os.path.lexists(compact)
    if old_exists and compact_exists:
        raise ValueError("Ambiguous Windows app generation locations; neither was changed")
    # Retained older candidates are data, not a fallback installation. Never
    # move, copy, normalize or discard them while selecting a new candidate.
    return legacy if old_exists else compact


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
    with _pointer_lock(home):
        return _promote(home, validated, bootstrap)


@contextmanager
def _pointer_lock(home):
    from filelock import FileLock

    folder = Path(home) / 'updates'
    if os.name == "nt":
        from .windows_files import directory
        with directory(folder, owned_root=home, create=True):
            with FileLock(folder / 'application-active.lock', timeout=0, mode=0o600):
                yield
    else:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        with FileLock(folder / 'application-active.lock', timeout=0, mode=0o600):
            yield


def _promote(home, validated, bootstrap):
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
    write_private(Path(home) / "updates/application-active.json", json.dumps(record),
                  **({"owned_root": home} if os.name == "nt" else {}))
    return active(home)


def rebind(home, expected, bootstrap):
    """Bind the active generation to a launcher that was replaced at its own path.

    Only the launcher refresh calls this. It confirms that the host is stopped
    immediately before the call. The stored launcher identity must equal
    `expected` exactly, and the replacement must be at the same folder: a
    pointer of another installation is never rebound. The write can land and a
    later check here can still fail: the caller restores with `restore_binding`
    from its saved bytes, not from this function's outcome. The retained
    `previous` entry stays as history; the next promotion copies this rebound
    top level, so a later rollback matches.
    """
    with _pointer_lock(home):
        from .deployment import write_private

        path = Path(home) / "updates/application-active.json"
        record = json.loads(path.read_text())
        current = bootstrap_identity(bootstrap)
        if (record.get("schema") != 1 or record.get("bootstrap") != expected
                or current["prefix"] != expected.get("prefix") or current == expected):
            raise ValueError("The application launch target does not belong to the replaced launcher")
        active(home)  # The pointer and its generation still agree before the write.
        record["bootstrap"] = current
        write_private(path, json.dumps(record), **({"owned_root": home} if os.name == "nt" else {}))
        result = active(home)
        if result["bootstrap"] != current:
            raise ValueError("The application launch target was not rebound")
        return result


class BindingChanged(ValueError):
    """The launch record changed in a way that the launcher refresh did not make."""


def restore_binding(home, saved):
    """Put back the launch record that a launcher refresh saved before its rebind.

    Only the launcher binding is put back, at the same folder. A record that
    already names the saved launcher needs nothing, even if a later promotion
    changed the rest. A record that names another launcher and also changed in
    another way belongs to a later promotion and is never overwritten
    (`BindingChanged`). Returns whether a write was needed.
    """
    with _pointer_lock(home):
        from .deployment import write_private

        path = Path(home) / "updates/application-active.json"
        current, original = json.loads(path.read_bytes()), json.loads(saved)
        if isinstance(current, dict) and current.get("bootstrap") == original["bootstrap"]:
            return False
        rest = lambda record: {key: value for key, value in record.items() if key != "bootstrap"}
        if (not isinstance(current, dict) or rest(current) != rest(original)
                or not isinstance(current.get("bootstrap"), dict)
                or current["bootstrap"].get("prefix") != original["bootstrap"].get("prefix")):
            raise BindingChanged("The application launch target changed after the launcher refresh")
        write_private(path, saved, **({"owned_root": home} if os.name == "nt" else {}))
        if path.read_bytes() != saved:
            raise ValueError("The application launch target was not restored")
        return True


def restore(home, promoted, previous, bootstrap, *, expected_bootstrap):
    """Restore only the exact pointer this promotion replaced, never an install."""
    with _pointer_lock(home):
        _restore(home, promoted, previous, bootstrap, expected_bootstrap)


def _restore(home, promoted, previous, bootstrap, expected_bootstrap):
    from .deployment import write_private

    if (active(home) != promoted or promoted['bootstrap'] != expected_bootstrap
            or bootstrap_identity(bootstrap) != expected_bootstrap):
        raise ValueError('The application launch target changed during recovery')
    expected = {key: value for key, value in (previous or {}).items()
                if key not in {'folder', 'prefix', 'previous'}}
    if promoted.get('previous') != (expected or None):
        raise ValueError('The previous application generation does not match this promotion')
    if previous is not None and previous['bootstrap'] != expected_bootstrap:
        raise ValueError('The previous application belongs to a different bootstrap')
    path = Path(home) / 'updates/application-active.json'
    if previous is None:
        path.unlink()
    else:
        write_private(path, json.dumps({key: value for key, value in previous.items()
                                       if key not in {'folder', 'prefix'}}),
                      **({"owned_root": home} if os.name == "nt" else {}))
    if active(home) != previous:
        raise ValueError('The previous application launch target was not restored')


def verify_candidate(target):
    """Use this launcher's reader to verify the exact target before execution."""
    import subprocess

    from .app_component_graph import GRAPH_PROBE, digest, normalized

    validated = json.loads((Path(target['folder']) / 'validated.json').read_text())
    artifact = validated.get('artifact')
    prefix = Path(target['prefix'])
    if artifact is not None and bootstrap_identity(prefix) != validated.get('installation'):
        raise ValueError('Active app installation changed; its generation was preserved')
    python = prefix / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    result = subprocess.run([str(python), '-I', '-c', GRAPH_PROBE],
        capture_output=True, text=True, timeout=30, check=False,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode or digest(normalized(json.loads(result.stdout),
            **({'artifact': artifact} if artifact is not None else {}))) != target['componentDigest']:
        raise ValueError('Active app packages changed; its generation was preserved')


async def can_read_candidate(process, bootstrap, python, validated, *, home=None):
    """Prove the actual bootstrap can read this graph before choosing promotion.

    Older launchers lack the archive-aware reader. The caller retains its normal
    uv replacement path for them; an invalid candidate is still an error.
    """
    script = '''import json,sys
from pathlib import Path
from amplifier_web import application_generations as generations
verify = getattr(generations, "verify_candidate", None)
folder = Path(sys.argv[2])
readable = not (sys.platform == "win32" and sys.argv[4] and
 generations.directory(sys.argv[4], sys.argv[5], sys.argv[6]).resolve() != folder.resolve())
if verify is None or not readable:
 print("unsupported")
else:
 verify({"prefix": Path(sys.argv[1]), "folder": Path(sys.argv[2]), "componentDigest": sys.argv[3]})
 print("compatible")
'''
    prefix = Path(python).parent.parent
    launcher = Path(bootstrap) / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    output = await process(str(launcher), '-I', '-c', script, str(prefix),
        str(prefix.parent.parent), validated['componentDigest'], str(home or ''),
        validated.get('revision', ''), validated.get('generation', ''), timeout=40)
    if output.strip() not in {'unsupported', 'compatible'}:
        raise ValueError('The application bootstrap compatibility check returned invalid data')
    return output.strip() == 'compatible'


def delegate(argv=None):
    """Exec the exact qualified interpreter, retaining CLI args and data scope.

    A newer manual uv upgrade takes precedence over an older active generation.
    Explicit development launches do not become a global application bootstrap.
    """
    # Children inherit the generation marker. It is traceability, not a guard:
    # invoking the public launcher from a child must still select the qualified
    # app. The bootstrap identity below prevents recursive delegation.
    if not Path(__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
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
    python = target["prefix"] / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    verify_candidate(target)
    os.environ["AMPLIFIER_APP_GENERATION_EXEC"] = str(target["generation"])
    os.environ["AMPLIFIER_WEB_HOME"] = str(home)
    # Match qualification's isolated import path. A repository in the caller's
    # working directory must not replace the app in this tested environment.
    os.execv(str(python), [str(python), "-I", "-m", "amplifier_web", *args])
