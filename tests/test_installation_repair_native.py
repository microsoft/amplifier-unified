"""Cheap stdlib checks of the CI harness, never local native-manager execution.

Run directly: python tests/test_installation_repair_native.py
These checks do not count as real systemd/launchd qualification.
"""
from __future__ import annotations

import ast
import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import shlex
import tempfile
import unittest
from unittest.mock import patch
import zipfile

FIXTURES = Path(__file__).with_name('fixtures')
SPEC = importlib.util.spec_from_file_location('repair_native_fixture', FIXTURES / 'installation_repair_native.py')
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


class NativeRepairHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.version, self.revision = '0.89.0', 'a' * 40
        self.artifacts = native.module(native.SOURCE / 'amplifier_web/app_release_artifacts.py')

    def archive(self):
        path = self.root / self.artifacts.filename(self.version)
        native.wheel(path, self.revision, self.version)
        return {'path': str(path), 'sha256': native.digest(path),
                'url': self.artifacts.SOURCE + '/releases/download/v' + self.version + '/' + path.name}

    def test_fixture_archive_passes_real_verifier_but_not_published_trust(self):
        descriptor = self.archive()
        self.assertEqual(self.artifacts.verify(descriptor, self.version, self.revision), descriptor)
        self.assertFalse(native.LIMITS['officialPublishedTrust'])
        self.assertFalse(native.LIMITS['fullAppHealthDoctor'])

    def test_archive_contains_exact_production_sources_and_valid_record(self):
        descriptor = self.archive()
        with zipfile.ZipFile(descriptor['path']) as archive:
            for name in native.PRODUCTION:
                self.assertEqual(archive.read('amplifier_web/' + name),
                                 (native.SOURCE / 'amplifier_web' / name).read_bytes())
            metadata = archive.read('amplifier_unified-' + self.version + '.dist-info/METADATA')
            self.assertNotIn(b'Requires-Dist:', metadata)
            self.assertIn(b'Native test substitute', metadata)
            rows = csv.reader(io.StringIO(archive.read('amplifier_unified-' + self.version + '.dist-info/RECORD').decode()))
            for name, checksum, size in rows:
                if checksum:
                    raw = archive.read(name)
                    expected = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')
                    self.assertEqual(checksum, 'sha256=' + expected)
                    self.assertEqual(int(size), len(raw))

    def test_verifier_rejects_tampered_fixture_and_wrong_revision(self):
        descriptor = self.archive()
        with self.assertRaises(ValueError):
            self.artifacts.verify(descriptor, self.version, 'b' * 40)
        with Path(descriptor['path']).open('ab') as stream:
            stream.write(b'tamper')
        with self.assertRaises(ValueError):
            self.artifacts.verify(descriptor, self.version, self.revision)

    def test_gate_refuses_local_execution_before_any_subprocess(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(native, 'execute') as execute:
            with self.assertRaisesRegex(RuntimeError, 'GitHub-hosted'):
                native.gate()
            execute.assert_not_called()

    def test_linux_literal_generated_command_and_mixed_kill_group(self):
        with patch.object(native.sys, 'platform', 'linux'):
            raw = native.definition(Path('/owned/bin/amplifier-unified'), Path('/owned/data'),
                                    Path('/owned/workspace'), {'PATH': '/usr/bin:/bin'})
        text = raw.decode()
        command = next(row.removeprefix('ExecStart=') for row in text.splitlines() if row.startswith('ExecStart='))
        self.assertEqual(shlex.split(command), ['/owned/bin/amplifier-unified', '--no-open', '--data-dir',
                                               '/owned/data', '--workspace', '/owned/workspace', 'serve'])
        self.assertIn('KillMode=mixed\n', text)
        self.assertIn('# Managed by amplifier-unified; do not edit.', text)
        self.assertEqual(text.count('ExecStart='), 1)

    def test_macos_literal_generated_label(self):
        with patch.object(native.sys, 'platform', 'darwin'):
            raw = native.definition(Path('/owned/bin/amplifier-unified'), Path('/owned/data'),
                                    Path('/owned/workspace'), {'PATH': '/usr/bin:/bin'})
        value = plistlib.loads(raw)
        self.assertEqual(value['Label'], native.LABEL)
        self.assertEqual(value['AmplifierUnifiedMarker'], 'amplifier-unified launch agent v1')
        self.assertEqual(len(value['ProgramArguments']), 7)
        self.assertTrue(value['KeepAlive'])
        self.assertNotIn('Program', value)

    def test_exact_isolated_worker_command_and_verify(self):
        fixture = {'basePython': '/outside/python3.13'}
        folder = Path('/private/data/installation-repair') / ('c' * 32)
        ordinary = native.worker_command(fixture, folder)
        self.assertEqual(ordinary[:3], ['/outside/python3.13', '-I', '-S'])
        self.assertEqual(ordinary[3:], [str(folder / 'installation_repair_worker.py'), str(folder)])
        self.assertEqual(native.worker_command(fixture, folder, verify=True)[-2:], ['--verify', str(folder)])

    def test_controller_dispatch_is_not_reimplemented_or_patched(self):
        tree = ast.parse((FIXTURES / 'installation_repair_native_app.py').read_text())
        launches = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == 'controller' and node.func.attr == 'launch']
        self.assertEqual(len(launches), 1)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    self.assertFalse(isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                                     and target.value.id in {'controller', 'worker', 'reset'})

    def test_evidence_limits_and_all_required_cases_are_explicit(self):
        self.assertEqual(set(native.CASES), {'happy', 'preflight', 'rollback', 'verify', 'data-lease'})
        self.assertGreaterEqual(len(native.LIMITS['substitutions']), 3)
        self.assertNotIn('env', native.LIMITS)

    def test_atomic_private_json_and_no_clobber_wheel(self):
        target = self.root / 'test.json'
        native.save(target, {'safe': 1})
        self.assertEqual(native.load(target), {'safe': 1})
        if os.name == 'posix':
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        descriptor = self.archive()
        with self.assertRaises(FileExistsError):
            native.wheel(Path(descriptor['path']), self.revision, self.version)

    def test_helper_label_is_exact_operation_scoped(self):
        identity = 'd' * 32
        with patch.object(native.sys, 'platform', 'linux'):
            self.assertEqual(native.helper_label(identity), 'amplifier-installation-repair-' + identity)
        with patch.object(native.sys, 'platform', 'darwin'):
            self.assertEqual(native.helper_label(identity), native.LABEL + '.repair.' + identity)

    def test_terminal_unknown_is_retained_as_terminal_not_pass(self):
        native.save(self.root / 'operation.json', {'status': 'unknown', 'stage': 'interrupted'})
        self.assertEqual(native.terminal(self.root)['status'], 'unknown')
        native.save(self.root / 'operation.json', {'status': 'running', 'stage': 'reinstalling'})
        self.assertIsNone(native.terminal(self.root))

    def test_loaded_helper_requires_no_restart_policy(self):
        value = {'LoadState': 'loaded', 'MainPID': '123', 'ActiveState': 'active',
                 'Restart': 'no', 'NRestarts': '0'}
        with patch.object(native.sys, 'platform', 'linux'), patch.object(native, 'properties', return_value=value):
            self.assertFalse(native.helper_observation('owned', {})['settled'])
            value['Restart'] = 'always'
            with self.assertRaisesRegex(RuntimeError, 'one-shot'):
                native.helper_observation('owned', {})

    def test_effect_counters_are_observed_checkpoints_not_uv_call_claims(self):
        rows = [{'kind': 'probe', 'stage': 'candidate'}, {'kind': 'probe', 'stage': 'fresh'},
                {'kind': 'service-start'}, {'kind': 'service-stop'}, {'kind': 'service-start'}]
        (self.root / 'effects.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
        self.assertEqual(native.effect_counts(self.root), {
            'probe:candidate': 1, 'probe:fresh': 1, 'service-start': 2, 'service-stop': 1})


if __name__ == '__main__':
    unittest.main()
