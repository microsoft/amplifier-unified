import os
from pathlib import Path
import subprocess
import unittest
import pytest

from amplifier_web.runtime_controls import public_config, restore_redactions, validate_plan


class ControlsTests(unittest.TestCase):
    def test_redacted_secrets_restore_by_instance_after_reordering(self):
        old={'providers':[{'module':'provider-test','id':'a','config':{'api_key':'private-a','max_tokens':400}},
                          {'module':'provider-test','id':'b','config':{'api_key':'private-b','max_tokens':500}}]}
        public=public_config(old)
        self.assertNotIn('private',str(public))
        self.assertEqual(public['providers'][0]['config']['max_tokens'],400)
        public['providers'].reverse()
        restored=restore_redactions(public,old)
        self.assertEqual(restored['providers'][0]['config']['api_key'],'private-b')

    def test_invalid_mount_changes_fail_before_persistence(self):
        plan={'session':{'orchestrator':{'module':'loop-live'},'context':{'module':'context-simple'}},
              'providers':[{'module':'provider-test'}]}
        validate_plan(plan)
        plan['providers'][0]['enabled']=False
        with self.assertRaisesRegex(ValueError,'provider'):
            validate_plan(plan)


def test_real_core_controls_and_tool_approval():
    python=os.environ.get('UNIFIED_RUNTIME_PYTHON')
    if not python:
        pytest.skip('Set UNIFIED_RUNTIME_PYTHON to test public Core/Foundation session controls')
    script=Path(__file__).parent/'fixtures/standalone_controls_probe.py'
    completed=subprocess.run([python,str(script)],capture_output=True,text=True,timeout=45)
    assert completed.returncode==0,completed.stdout+completed.stderr
    assert '"approval_enforced": true' in completed.stdout
