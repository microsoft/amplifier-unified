"""Exercise direct-script imports in a wheel-like host/runtime layout."""
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "amplifier_web"


@pytest.mark.parametrize("entrypoint", ["update_probe.py", "runtime_worker.py"])
def test_worker_scripts_do_not_expose_host_site_packages(tmp_path, entrypoint):
    outer = tmp_path / "host-site-packages"
    package = outer / "amplifier_web"
    package.mkdir(parents=True)
    runtime = tmp_path / "runtime-site-packages"
    runtime.mkdir()
    for name in (entrypoint, "runtime_protocol.py", "__init__.py", "runtime_bootstrap.py"):
        if (PACKAGE / name).exists():
            shutil.copy2(PACKAGE / name, package / name)
    (outer / "host_only_dependency.py").write_text("HOST_ONLY = True\n")
    metadata = outer / "amplifier_bundle_context_intelligence-0.1.3.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: amplifier-bundle-context-intelligence\nVersion: 0.1.3\n"
    )
    (outer / "runtime_dependency.py").write_text("SOURCE = 'host'\n")
    (runtime / "runtime_dependency.py").write_text("SOURCE = 'runtime'\n")
    checks = textwrap.dedent("""
        import importlib.util
        import importlib.metadata
        import runtime_dependency
        assert importlib.util.find_spec("host_only_dependency") is None, "host imports leaked"
        assert runtime_dependency.SOURCE == "runtime", "host shadows runtime dependency"
        try:
            importlib.metadata.distribution("amplifier-bundle-context-intelligence")
        except importlib.metadata.PackageNotFoundError:
            pass
        else:
            raise AssertionError("host distribution metadata leaked")
    """)
    (package / "host").mkdir()
    (package / "host" / "__init__.py").write_text("")
    (package / "update_diagnostics.py").write_text(
        "PROBE_PREFIX = 'PROBE:'\ndef exception_type(error): return type(error).__name__\n"
    )
    (package / "host" / "session.py").write_text(
        checks + textwrap.dedent("""
        class Session:
            async def cleanup(self): pass
        async def prepare_manager(*args, **kwargs):
            return Session(), None, {"standalone": True, "providers": ["test"]}
        """)
    )
    (package / "host" / "config.py").write_text(
        checks + "\nprint('ISOLATED', flush=True)\nraise SystemExit(0)\n"
    )
    # -I -S exclude the checkout, ambient PYTHONPATH and pytest's own venv.
    # Add only the runtime's
    # dependency path and the script directory, never the host site-packages root.
    runner = textwrap.dedent("""
        import asyncio, runpy, sys
        from pathlib import Path
        script = Path(sys.argv[1])
        sys.path.insert(0, sys.argv[2])
        sys.path.insert(0, str(script.parent))
        sys.argv = [str(script), "/unused", "anchors"]
        if script.name == "update_probe.py":
            runpy.run_path(str(script), run_name="__main__")
        else:
            namespace = runpy.run_path(str(script), run_name="worker_test")
            worker = namespace["Worker"]()
            worker.start.__func__.__globals__["publish"] = lambda event: print(event)
            asyncio.run(worker.start({"id": "test"}))
    """)
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", runner, str(package / entrypoint), str(runtime)],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    if entrypoint == "update_probe.py":
        assert '"ok": true' in result.stdout, result.stdout
    else:
        assert "ISOLATED" in result.stdout, result.stdout