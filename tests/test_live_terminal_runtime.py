"""Opt-in terminal adapter acceptance against the real isolated runtime."""
import json
import os
from pathlib import Path
import subprocess

import pytest


def test_terminal_clients_follow_detach_reconnect_and_stop_real_worker(tmp_path):
    python = os.environ.get("UNIFIED_RUNTIME_PYTHON")
    if not python:
        pytest.skip("set UNIFIED_RUNTIME_PYTHON to Python with the pinned runtime and host dependencies")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([python, str(root / "tests/fixtures/live_terminal_probe.py"), str(tmp_path)],
                            cwd=root, text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence == dict.fromkeys(("two_live_clients", "idle_resume", "duplicate_runs_once",
                                      "detach_keeps_work", "reconnect_restores_history",
                                      "explicit_stop_cancels_provider"), True)
