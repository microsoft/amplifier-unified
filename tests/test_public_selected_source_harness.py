"""Cheap authoring checks; never acquire or run the hosted public graph locally."""
import ast
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_adapter', ROOT / 'scripts/qualify_public_selected_source.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class PublicSelectedHarness(unittest.TestCase):
    def test_cleanup_handles_readonly_sources_without_following_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            work = outer / 'work'; work.mkdir()
            protected = work / 'immutable'; protected.mkdir()
            (protected / 'file').write_text('owned')
            unrelated = outer / 'unrelated'; unrelated.mkdir()
            (unrelated / 'keep').write_text('keep')
            (work / 'alias').symlink_to(unrelated, target_is_directory=True)
            protected.chmod(0o555)
            adapter.remove_owned_work(work)
            self.assertFalse(work.exists())
            self.assertEqual((unrelated / 'keep').read_text(), 'keep')

    def test_environment_drops_accounts_and_preserves_only_exact_loop_transport(self):
        with patch.dict(os.environ, {'PATH': '/bin', 'ANTHROPIC_API_KEY': 'do-not-forward',
                                    'GITHUB_TOKEN': 'do-not-forward', 'SSH_AUTH_SOCK': '/private'}, clear=True):
            value = adapter.clean_environment(Path('/owned/work'), Path('/owned/loop'))
        self.assertNotIn('ANTHROPIC_API_KEY', value)
        self.assertNotIn('GITHUB_TOKEN', value)
        self.assertNotIn('SSH_AUTH_SOCK', value)
        self.assertEqual(value['GIT_CONFIG_COUNT'], '1')
        self.assertEqual(value['GIT_CONFIG_VALUE_0'], 'https://github.com/microsoft/amplifier-module-loop-live')
        self.assertEqual(value['UV_NO_GITHUB_FAST_PATH'], '1')
        self.assertNotIn('UV_OFFLINE', value)
        self.assertNotIn('UV_FIND_LINKS', value)

    def test_junit_retains_outcomes_without_private_failure_text(self):
        with tempfile.TemporaryDirectory() as directory:
            raw, safe = Path(directory) / 'raw.xml', Path(directory) / 'safe.xml'
            raw.write_text('<testsuites><testsuite name="fixed" tests="2" failures="1" errors="0" skipped="0">'
                           '<testcase name="a" time="1"><failure message="private">private source</failure></testcase>'
                           '<testcase name="b" time="2"/></testsuite></testsuites>')
            adapter.bounded_junit(raw, safe)
            content = safe.read_text()
            self.assertNotIn('private source', content)
            root = ET.fromstring(content)
            self.assertEqual(root.find('testsuite').get('failures'), '1')
            self.assertEqual(len(root.findall('.//testcase')), 2)

    def test_lane_is_only_explicit_manual_and_reuses_existing_read_only_job(self):
        dispatch = yaml.load((ROOT / '.github/workflows/python-checks.yml').read_text(), Loader=yaml.BaseLoader)
        job = dispatch['jobs']['updater-public-selected-source']
        self.assertIn("github.event_name == 'workflow_dispatch'", job['if'])
        self.assertIn("inputs.suite == 'updater-public-selected-source'", job['if'])
        self.assertIn("inputs.suite != 'updater-public-selected-source'", dispatch['jobs']['python']['if'])
        self.assertEqual(job['uses'], './.github/workflows/updater-omitted-ref.yml')
        workflow = yaml.load((ROOT / '.github/workflows/updater-omitted-ref.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        self.assertEqual(workflow['on']['workflow_call']['inputs']['public_selected_source']['default'], 'false')
        for path in (ROOT / '.github/workflows').glob('*.yml'):
            if path.name not in {'python-checks.yml', 'updater-omitted-ref.yml'}:
                self.assertNotIn('updater-public-selected-source', path.read_text())

    def test_fixture_has_no_production_module_or_inventory_replacement(self):
        path = ROOT / 'tests/fixtures/public_selected_source_probe.py'
        source = path.read_text(); ast.parse(source)
        for forbidden in ('fixture_core(', 'fixture_provider(', 'repack_registry_wheels(',
                          "'manifest_path',", "'validate',", "'inventory_sources',", 'manager.inventory ='):
            self.assertNotIn(forbidden, source)
        self.assertIn('runtime_environment.manifest_path().read_bytes()', source)
        self.assertIn('await manager.check(fresh=True)', source)
        self.assertIn('await manager.install()', source)
        self.assertIn('verify_recorded(final, stage)', source)
        self.assertIn('await manager.rollback()', source)
        self.assertIn('http://127.0.0.1:9', source)


if __name__ == '__main__':
    unittest.main()
