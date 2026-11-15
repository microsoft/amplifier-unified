"""Installed-only launcher for the maintained pass-two presentation fixtures.

This adds no application hooks, routes or dispatcher replacements. The browser
journey supplies explicitly labelled synthetic hierarchy data through the existing
browser_detail_server ``patch`` control. Other modes run their existing fixtures
unchanged. Stage with scripts/web_test_harness.py, never run from a source checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import runpy
import sys
import tempfile
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase", required=True,
        choices=("detail", "questions", "approvals", "workspace", "workspace-config", "navigation"),
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    assert sys.flags.isolated and sys.dont_write_bytecode, "Launch with -I -B"
    assert not (root / "amplifier_web").exists(), "Stage a source-free test harness"
    expected_package = Path(os.environ["AMPLIFIER_TEST_PACKAGE_ROOT"]).resolve()
    assert not expected_package.is_relative_to(root), "Installed package is outside harness"
    # No real provider credentials are needed by these deterministic fixtures.
    for key in list(os.environ):
        if any(word in key.upper() for word in (
            "API_KEY", "ACCESS_TOKEN", "AUTH_TOKEN", "SECRET", "PASSWORD", "CREDENTIAL",
        )):
            del os.environ[key]
    os.environ["AMPLIFIER_TEST_INSTALLED_PACKAGE"] = str(expected_package)
    import amplifier_web

    package = Path(amplifier_web.__file__).resolve().parent
    assert package == expected_package, (package, expected_package)
    assert package.is_relative_to(Path(sys.prefix).resolve()), "Use the installed venv, not a source path"
    digest = lambda data: hashlib.sha256(data).hexdigest()
    wheel_path = os.environ.get("AMPLIFIER_TEST_WHEEL")
    identity_path = os.environ.get("AMPLIFIER_TEST_WHEEL_IDENTITY")
    assert wheel_path or identity_path, "Supply the original wheel or its retained identity manifest"
    identity = json.loads(Path(identity_path).read_text()) if identity_path else None
    if wheel_path:
        wheel = Path(wheel_path).resolve()
        with zipfile.ZipFile(wheel) as archive:
            files = {
                name: digest(archive.read(name))
                for name in archive.namelist()
                if name.startswith("amplifier_web/") and not name.endswith("/")
            }
        original = {"wheelSha256": digest(wheel.read_bytes()), "files": files}
        if identity is not None:
            assert identity["wheelSha256"] == original["wheelSha256"]
            assert identity["files"] == original["files"]
        identity = original
    assert isinstance(identity, dict) and identity.get("files")
    assert len(identity["wheelSha256"]) == 64
    assert all(char in "0123456789abcdef" for char in identity["wheelSha256"])
    checked = {}
    for name, expected in identity["files"].items():
        relative = PurePosixPath(name)
        assert relative.parts and relative.parts[0] == "amplifier_web" and ".." not in relative.parts
        assert not relative.is_absolute()
        installed = (package.parent / name).resolve()
        assert installed.is_relative_to(package)
        observed = digest(installed.read_bytes())
        assert observed == expected, f"Installed/wheel mismatch: {name}"
        checked[name] = observed
    assert "amplifier_web/server.py" in checked
    static = {
        name.removeprefix("amplifier_web/static/"): value
        for name, value in checked.items() if name.startswith("amplifier_web/static/")
    }
    assert "index.html" in static and any(name.endswith(".js") for name in static)
    print(json.dumps({
        "fixture": "layout-pass2-provenance", "phase": args.phase,
        "pid": os.getpid(), "packageRoot": str(package),
        "serverSha256": checked["amplifier_web/server.py"],
        "wheelSha256": identity["wheelSha256"],
        "wheelJoin": "original-wheel-opened" if wheel_path else "parent-retained-wheel-manifest",
        "manifestSha256": digest(Path(identity_path).read_bytes()) if identity_path else None,
        "installedFilesVerified": len(checked), "staticSha256": static,
        "sourceFreeHarness": str(root), "syntheticOnly": True,
    }), flush=True)
    # Expose only the maintained fixture helpers after the installed import/join.
    # browser_detail_server inserts this source-free root itself; there is no
    # amplifier_web directory there for it (or any sibling fixture) to import.
    fixtures = root / "tests" / "fixtures"
    sys.path.insert(0, str(fixtures))
    scripts = {
        "detail": "browser_detail_server.py",
        "questions": "questions_ui_server.py",
        "approvals": "approval_layout_ui_server.py",
        "workspace": "ordinary_workspace_work_ui_server.py",
        "workspace-config": "standard_workspace_configuration_ui_server.py",
        "navigation": "chat_library_server.py",
    }
    script = fixtures / scripts[args.phase]
    sys.argv = [str(script), str(root)] if args.phase == "detail" else [str(script)]
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    # Establish disposable homes before importing any application module. Some
    # reused fixtures choose a second inner home; their unspecified state homes
    # must still never fall back to the running app or the operator's home.
    with tempfile.TemporaryDirectory(prefix="layout-pass2-envelope-") as envelope:
        isolated = Path(envelope)
        os.environ.update(
            AMPLIFIER_HOME=str(isolated / "native"),
            AMPLIFIER_WEB_HOME=str(isolated / "app"),
            AMPLIFIER_UNIFIED_IMPORT_HOME=str(isolated / "legacy"),
            AMPLIFIER_SESSION_STATE_HOME=str(isolated / "session-state"),
        )
        main()