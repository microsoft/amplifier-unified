"""Opt-in proof through the real owned Worker with a local fixture provider."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest


def test_current_edit_parks_and_resumes_without_replaying(tmp_path):
    if os.environ.get('RUN_HISTORY_EDIT_WORKER') != '1':
        pytest.skip('set RUN_HISTORY_EDIT_WORKER=1 for the isolated runtime proof')
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([shutil.which('uv'),'run','--project',str(root/'amplifier_web/runtime_deps'),
        'python',str(root/'tests/fixtures/history_edit_probe.py'),str(tmp_path/'fixture')],
        cwd=root,text=True,capture_output=True,timeout=180,
        env={**os.environ,'UV_PROJECT_ENVIRONMENT':str(tmp_path/'runtime')})
    assert result.returncode==0,result.stdout+result.stderr
    evidence=json.loads(result.stdout)
    assert evidence['current_edit_replaced_only_later_context'] and evidence['resumed_without_replay']
