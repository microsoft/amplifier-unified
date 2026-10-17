"""Optional ordinary collection; automatically owned by the native lane."""
import json
import os
from pathlib import Path
import subprocess
import pytest


def test_voice_status_held_native_worker_never_replays_or_cancels(tmp_path):
    python=os.environ.get('UNIFIED_RUNTIME_PYTHON')
    if not python:
        pytest.skip('Native owner supplies the declared Core/Foundation/loop-live interpreter')
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([python,str(root/'tests/fixtures/voice_status_worker_probe.py'),str(tmp_path)],
                          cwd=root,text=True,capture_output=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
    row=json.loads(result.stdout)
    assert row['immediateStatus'] and row['providerCalls']==1
    assert row['acceptedWorkSurvivesHangup'] and row['exactWorkGeneration']
    assert not row['statusSubmittedWork']
