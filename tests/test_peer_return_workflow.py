"""Full Worker-process qualification; run only in the prepared offline guest.

Unlike the fast coordination tests this module imports no product/native runtime
at collection time. Missing native dependencies are a failed qualification with
an explicit prerequisite diagnostic, never an importorskip or a fake admission.
"""
import json
from pathlib import Path
import subprocess
import sys

import pytest


def test_existing_roots_correct_rereview_and_return_through_real_workers(tmp_path):
    probe = Path(__file__).parent / "fixtures" / "peer_return_worker_probe.py"
    with subprocess.Popen(
        [sys.executable, str(probe), str(tmp_path / "peer-return")],
        cwd=probe.parents[2], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=165)
        except subprocess.TimeoutExpired:
            # The probe handles SIGTERM by cancelling its owner and closing its
            # actual RuntimeManager. Do not leave native workers behind on failure.
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            pytest.fail(f"Peer-return Worker qualification timed out.\n{stdout}\n{stderr}")
    assert process.returncode == 0, (
        "Peer-return Worker qualification failed (not skipped).\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    evidence = json.loads((tmp_path / "peer-return" / "evidence.json").read_text())
    assert evidence["status"] == "passed", evidence
    assert evidence["workerProcesses"] == 2
    assert evidence["correctionRounds"] == 2
    assert evidence["originalResponseRevisions"] == [1, 2, 3]
    assert evidence["automaticReturns"] == 5
    assert evidence["lateReturnSuppressed"] is True
    assert evidence["provider"] == "scripted-offline"
    assert evidence["streaming"] is False
