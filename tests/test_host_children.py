import os
from pathlib import Path
import subprocess
import pytest


def test_real_foundation_child_lifecycle_and_delegate_contract():
    python=os.environ.get('UNIFIED_RUNTIME_PYTHON')
    if not python:
        pytest.skip('Set UNIFIED_RUNTIME_PYTHON to a warmed standalone runtime for the real-core probe')
    probe=Path(__file__).parent/'fixtures/standalone_children_probe.py'
    result=subprocess.run([python,str(probe)],text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr+result.stdout
    assert '"delegate_compatible": true' in result.stdout
    assert '"cli_imports": false' in result.stdout
