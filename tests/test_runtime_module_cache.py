"""Real staged/live entry points must consume the same validated module cache."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("entrypoint", ["probe", "fresh-worker", "resumed-worker", "failed-worker"])
def test_real_runtime_uses_active_module_cache(entrypoint):
    python = os.environ.get("UNIFIED_RUNTIME_PYTHON")
    if not python:
        pytest.skip("Set UNIFIED_RUNTIME_PYTHON for Core/Foundation runtime integration")
    script = Path(__file__).parent / "fixtures/runtime_module_cache_probe.py"
    result = subprocess.run(
        [python, str(script), entrypoint], capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"shared_cache_unchanged": true' in result.stdout
    assert '"model_calls": 0' in result.stdout
