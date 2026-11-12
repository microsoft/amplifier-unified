"""Isolated native public-tool acceptance, not live-provider/profile qualification."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_native_full_operator_discovers_cancels_and_saves(tmp_path):
    source = Path(__file__).resolve().parent / 'fixtures' / 'held_configuration_operator_probe.py'
    root = tmp_path / 'native-cancel-migration'
    result = subprocess.run([sys.executable, str(source), str(root)],
        capture_output=True, text=True, timeout=240)
    report = json.loads((root / 'evidence.json').read_text()) if (root / 'evidence.json').exists() else {}
    artifacts = os.environ.get('AMPLIFIER_TEST_ARTIFACTS')
    if artifacts:
        Path(artifacts, 'held-operator-native.json').write_text(json.dumps(report, indent=2))
    assert result.returncode == 0, result.stdout + result.stderr + json.dumps(report)
    assert report['actualManagerTool'] and report['publicDiscovery'] and report['exactCancellation']
    assert report['savedWithoutStarting'] and report['noTargetInputOrContinuation']
    assert report['oneSettingsSave'] and report['originalAuthorityPreserved']
    assert not report['cleanupErrors'] and not report['cleanupFallbackSignals']
    assert report['writerAvailableAfterClose'] and report['runtimeRowsAfterClose'] == 0
    assert all(row['returncode'] is not None for row in report['processes'])
