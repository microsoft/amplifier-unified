"""The worker's shell tool guard against the REAL shell tool, where that tool is installed.

The cases of test_shell_tool_containment.py use a stand-in package. This case imports the
tool itself, at the revision that pyproject.toml pins for tests, with the guard installed
first, and runs one command. `.github/workflows/windows-private-shell.yml` installs that
revision on a Windows runner and requires that this case ran and passed there. Where the
tool is not installed, the case is skipped.
"""

import asyncio
import importlib
import importlib.util
import os
import sys

import pytest

from amplifier_web import shell_tool_containment as containment


TARGET = "amplifier_module_tool_bash"
JOB_ENTRY_POINTS = ["_get_windows_job_object", "_assign_to_windows_job"]


def real_tool_installed():
    try:
        return (importlib.util.find_spec(TARGET) is not None
                and importlib.util.find_spec("amplifier_core") is not None)
    except (ImportError, ValueError):
        return False


@pytest.fixture
def clean_imports(monkeypatch):
    """Leave no guard and no guarded tool behind for a later case in this process."""
    def owned(name):
        return name == TARGET or name.startswith(TARGET + ".")
    saved = {name: module for name, module in sys.modules.items() if owned(name)}
    for name in saved:
        del sys.modules[name]
    monkeypatch.setattr(sys, "meta_path", [
        finder for finder in sys.meta_path
        if not getattr(finder, "_unified_shell_tool_containment", False)])
    try:
        yield
    finally:
        for name in tuple(sys.modules):
            if owned(name):
                del sys.modules[name]
        sys.modules.update(saved)
        importlib.invalidate_caches()


@pytest.mark.skipif(not real_tool_installed(), reason="the real shell tool is not installed here")
def test_the_real_shell_tool_is_turned_off_and_still_runs_a_command(clean_imports, tmp_path, capsys):
    assert containment.install() == {"module": TARGET, "importHook": True, "loaded": None}
    module = importlib.import_module(TARGET)
    report = module.__unified_shell_tool_containment__
    # The two entry points to the tool's job exist under the names that the guard knows.
    assert report["missing"] == [] and report["failed"] == []
    assert report["replaced"][:2] == JOB_ENTRY_POINTS
    for name in JOB_ENTRY_POINTS:                           # The guard's functions, not the tool's.
        assert getattr(module, name).__module__ == containment.__name__, name
    assert module._get_windows_job_object() is None
    assert module._assign_to_windows_job(os.getpid()) is False
    assert capsys.readouterr().err == ""                    # The guard has nothing to report.
    config = {"working_dir": str(tmp_path)}
    if sys.platform == "win32":
        config["windows_shell"] = "gitbash"                 # A hosted Windows runner has no WSL.
    result = asyncio.run(module.BashTool(config).execute({"command": "echo guard-ok; exit 3"}))
    # With its job off, the tool still starts the command, and the command runs to its end.
    assert isinstance(result.output, dict), result.output
    assert result.output["stdout"].strip() == "guard-ok"
    assert result.output["returncode"] == 3
