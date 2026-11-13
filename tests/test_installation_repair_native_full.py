"""Cheap stdlib authoring checks only: no installs, native managers or network.

Run: python -I -B tests/test_installation_repair_native_full.py
Passing these checks is NOT full-app/native acceptance. Only the CI harness can
qualify the actual built wheel/real dependency graph/CLI/service/repair/doctor.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import ssl
import subprocess
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

FIXTURES = Path(__file__).with_name('fixtures')


def module(name):
    spec = importlib.util.spec_from_file_location(name, FIXTURES / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


full = module('installation_repair_native_full')
acquisition = module('installation_repair_acquisition')


class FullHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_local_gate_refuses_before_any_native_command(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(full.native, 'execute') as command:
            for action in ('prepare', 'run', 'cleanup'):
                with self.subTest(action=action), self.assertRaisesRegex(RuntimeError, 'GitHub-hosted'):
                    full.gate(action)
            command.assert_not_called()

    def test_exact_parent_freeze_required_not_merely_ci_flag(self):
        with patch.object(full.native, 'gate', return_value=(self.root / 'tiny', None)), \
             patch.object(full.sys, 'platform', 'linux'), \
             patch.object(full.platform, 'freedesktop_os_release', return_value={'ID': 'ubuntu'}), \
             patch.object(full, 'execute', return_value=SimpleNamespace(stdout='a' * 40)), \
             patch.dict(os.environ, {'INSTALLATION_REPAIR_FULL_ACCEPTED_REVISION': 'b' * 40}):
            with self.assertRaisesRegex(RuntimeError, 'production freeze'):
                full.gate('prepare')
            with patch.dict(os.environ, {'INSTALLATION_REPAIR_FULL_ACCEPTED_REVISION': 'a' * 40}):
                root, evidence = full.gate('run')
                self.assertEqual(root.name, 'installation-repair-native-full')
                self.assertEqual(evidence.name, 'installation-repair-full-evidence')

    def test_cleanup_not_blocked_by_changed_freeze(self):
        with patch.object(full.native, 'gate', return_value=(self.root / 'tiny', None)), \
             patch.object(full.sys, 'platform', 'darwin'), \
             patch.object(full.platform, 'mac_ver', return_value=('26.0', (), '')), \
             patch.object(full, 'execute') as command:
            full.gate('cleanup')
            command.assert_not_called()

    def test_macos_older_host_refused(self):
        with patch.object(full.native, 'gate', return_value=(self.root / 'tiny', None)), \
             patch.object(full.sys, 'platform', 'darwin'), \
             patch.object(full.platform, 'mac_ver', return_value=('15.0', (), '')):
            with self.assertRaisesRegex(RuntimeError, 'macOS 26'):
                full.gate('cleanup')

    def test_all_package_roots_and_force_includes_not_only_web(self):
        project = {'tool': {'hatch': {'build': {'targets': {'wheel': {
            'packages': ['amplifier_web', 'components/outputs/amplifier_outputs'],
            'exclude': ['/amplifier_web/tests/**'],
            'force-include': {'bundle.yaml': 'amplifier_web/bundle_data/bundle.yaml',
                              'context': 'amplifier_web/bundle_data/context'},
        }}}}}}
        self.assertEqual(full.payload_sources(project, [
            'amplifier_web/cli.py', 'amplifier_web/static/index.html', 'amplifier_web/tests/test.py',
            'components/outputs/amplifier_outputs/__init__.py', 'bundle.yaml', 'context/guide.md',
            'tests/not-shipped.py']), {
                'amplifier_web/cli.py': 'amplifier_web/cli.py',
                'amplifier_web/static/index.html': 'amplifier_web/static/index.html',
                'amplifier_outputs/__init__.py': 'components/outputs/amplifier_outputs/__init__.py',
                'amplifier_web/bundle_data/bundle.yaml': 'bundle.yaml',
                'amplifier_web/bundle_data/context/guide.md': 'context/guide.md'})

    def test_metadata_join_handles_hatch_specifier_order(self):
        self.assertEqual(full.requirement_key('aiohttp>=3.12,<4'), full.requirement_key('aiohttp<4,>=3.12'))
        self.assertEqual(full.requirement_key('amplifier-memory @ git+https://example.test/repo@main'),
                         full.requirement_key('amplifier-memory@ git+https://example.test/repo@main'))
        self.assertNotEqual(full.requirement_key('aiohttp>=3.12,<4'), full.requirement_key('aiohttp>=3.12,<5'))
        with self.assertRaises(RuntimeError):
            full.requirement_key('test>=1; python_version>"3"')

    def test_installed_payload_join_detects_changed_sibling_static_or_metadata(self):
        site = self.root / 'lib/python3.13/site-packages'
        payload = {'sha256': {}}
        for name in ('amplifier_web/static/index.html', 'amplifier_outputs/__init__.py',
                     'amplifier_unified-0.89.0.dist-info/entry_points.txt'):
            path = site / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
            payload['sha256'][name] = full.digest(path)
        self.assertEqual(full.installed_join(self.root, payload)['wheelFilesMatched'], 3)
        for name in payload['sha256']:
            path = site / name
            raw = path.read_bytes()
            path.write_bytes(raw + b'tampered')
            with self.assertRaisesRegex(RuntimeError, 'payload differs'):
                full.installed_join(self.root, payload)
            path.write_bytes(raw)

    def test_tls_pin_is_checked_before_credential_access_or_http(self):
        connection = Mock()
        connection.sock.getpeercert.return_value = b'wrong leaf'
        config = {'port': 1, 'home': str(self.root), 'tls': {'pin': 'a' * 64, 'ca': 'unused'}}
        with patch.object(full.ssl, 'create_default_context') as context, \
             patch.object(full.http.client, 'HTTPSConnection', return_value=connection), \
             patch.object(Path, 'read_text') as token:
            with self.assertRaisesRegex(RuntimeError, 'pin mismatch'):
                full.request(config, '/api/health')
            context.assert_called_once_with(cafile='unused')
            token.assert_not_called()
            connection.request.assert_not_called()
            connection.close.assert_called_once()

    def test_invalid_chain_or_san_never_reads_or_sends_token(self):
        connection = Mock()
        connection.connect.side_effect = ssl.SSLCertVerificationError('not a trusted SAN')
        config = {'port': 1, 'home': str(self.root), 'tls': {'pin': 'a' * 64, 'ca': 'unused'}}
        with patch.object(full.ssl, 'create_default_context'), \
             patch.object(full.http.client, 'HTTPSConnection', return_value=connection), \
             patch.object(Path, 'read_text') as token:
            with self.assertRaises(ssl.SSLCertVerificationError):
                full.request(config, '/api/health')
            token.assert_not_called()
            connection.request.assert_not_called()

    def test_real_api_envelope_and_pin_then_token(self):
        events = []
        connection = Mock()
        connection.connect.side_effect = lambda: events.append('connected')
        connection.sock.getpeercert.side_effect = lambda **kwargs: events.append('peer') or b'leaf'
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{"accepted":true,"result":{"supported":true}}'
        config = {'port': 1, 'home': str(self.root),
                  'tls': {'pin': hashlib.sha256(b'leaf').hexdigest(), 'ca': 'unused'}}
        with patch.object(full.ssl, 'create_default_context'), \
             patch.object(full.http.client, 'HTTPSConnection', return_value=connection), \
             patch.object(Path, 'read_text', side_effect=lambda: events.append('token') or 'private'):
            self.assertTrue(full.action(config, 'maintenance.installationRepairPreview', {})['supported'])
        self.assertEqual(events, ['connected', 'peer', 'token'])
        args, kwargs = connection.request.call_args
        self.assertEqual(args, ('POST', '/api/actions'))
        self.assertEqual(json.loads(kwargs['body']), {'action': 'maintenance.installationRepairPreview', 'args': {}})
        self.assertNotIn('id', json.loads(kwargs['body']))

    def test_lost_ack_reads_mapping_and_sends_confirm_once(self):
        config = {'home': str(self.root / 'data')}
        repair = Path(config['home']) / 'installation-repair'
        repair.mkdir(parents=True)
        confirmation = {'previewId': 'a' * 32, 'hostInstanceId': 'exact-old-host', 'confirm': True}
        full.save(repair / 'admissions.json', {'schema': 1, 'current': 'b' * 32,
                                             'previews': {confirmation['previewId']: 'b' * 32}})
        for error in (ConnectionResetError(), http.client.RemoteDisconnected(), RuntimeError('HTTP failure'),
                      json.JSONDecodeError('bad receipt', '', 0)):
            with self.subTest(error=type(error).__name__), \
                 patch.object(full, 'action', side_effect=error) as send:
                folder = full.confirm_once(self.root, config, confirmation)
                self.assertEqual(folder, repair / ('b' * 32))
                self.assertEqual(full.load(self.root / 'confirmation.json'), confirmation)
                send.assert_called_once_with(config, 'maintenance.installationRepairConfirm', confirmation)

    def test_missing_admission_does_not_reconfirm(self):
        with patch.object(full, 'action', side_effect=TimeoutError) as send, \
             patch.object(full.native, 'wait_for', side_effect=RuntimeError('timed out')):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                full.confirm_once(self.root, {'home': str(self.root)}, {'previewId': 'a' * 32})
            self.assertEqual(send.call_count, 1)

    def identity_inputs(self):
        host = '12912ed3-c6bf-4df7-970b-30b4863543ca'
        config = {'revision': 'a' * 40, 'version': '0.89.0', 'home': '/private/full/data',
                  'artifact': {'url': 'https://github.com/microsoft/amplifier-unified/releases/download/v0.89.0/amplifier_unified-0.89.0-py3-none-any.whl'}}
        health = {'ok': True, 'app': 'amplifier-unified', 'revision': config['revision'],
                  'version': config['version'], 'hostInstanceId': host, 'instanceId': 'd' * 32}
        target = {'version': config['version'], 'revision': config['revision'], 'source': config['artifact']['url']}
        preview = {'supported': True, 'scope': 'application-installation', 'dataDir': config['home'],
                   'installedExtras': [], 'restart': 'managed-service', 'target': target,
                   'hostInstanceId': host, 'previewId': 'b' * 32}
        return config, health, preview

    def test_service_uuid_and_updater_hex_are_separate_and_never_normalized(self):
        config, health, _ = self.identity_inputs()
        self.assertEqual(full.health_identities(health, config), (health['hostInstanceId'], health['instanceId']))
        for bad in (None, '', 42, 'wrong-uuid', health['instanceId'],
                    health['hostInstanceId'].replace('-', ''), health['hostInstanceId'].upper()):
            with self.subTest(host=bad), self.assertRaisesRegex(RuntimeError, 'hostInstanceId UUID'):
                full.health_identities({**health, 'hostInstanceId': bad}, config)
        without_host = {key: value for key, value in health.items() if key != 'hostInstanceId'}
        with self.assertRaisesRegex(RuntimeError, 'hostInstanceId UUID'):
            full.health_identities(without_host, config)
        for bad in (None, '', 'not-hex', health['hostInstanceId'], health['hostInstanceId'].replace('-', '')):
            with self.subTest(updater=bad), self.assertRaisesRegex(RuntimeError, 'updater instanceId'):
                full.health_identities({**health, 'instanceId': bad}, config)

    def test_authenticated_health_requires_exact_source_version_app_and_ok(self):
        config, health, _ = self.identity_inputs()
        for key, bad in (('revision', 'c' * 40), ('version', '0.88.0'), ('app', 'substitute'), ('ok', False)):
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'source/app mismatch'):
                full.health_identities({**health, key: bad}, config)

    def test_preview_confirm_and_plan_bind_service_uuid_not_updater_identity(self):
        config, health, preview = self.identity_inputs()
        with patch.object(full, 'action', return_value=preview) as request:
            actual, confirmation = full.preview_confirmation(config, self.root, health)
        self.assertEqual(actual, preview)
        self.assertEqual(confirmation, {'hostInstanceId': health['hostInstanceId'],
                                       'previewId': preview['previewId'], 'confirm': True})
        request.assert_called_once_with(config, 'maintenance.installationRepairPreview', {})
        plan = {'hostInstanceId': health['hostInstanceId'], 'home': config['home'], 'extras': [],
                'target': preview['target'], 'previewId': preview['previewId'], 'operationId': 'e' * 32}
        full.require_plan_scope(plan, config, health, confirmation, 'e' * 32)
        for key, bad in (('hostInstanceId', health['instanceId']), ('home', '/foreign/data'),
                         ('extras', ['native-desktop']), ('operationId', 'f' * 32), ('previewId', 'f' * 32),
                         ('target', {**preview['target'], 'revision': 'f' * 40})):
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'plan changed'):
                full.require_plan_scope({**plan, key: bad}, config, health, confirmation, 'e' * 32)

    def test_preview_scope_mismatch_never_yields_confirmation(self):
        config, health, preview = self.identity_inputs()
        for key, bad in (('hostInstanceId', health['instanceId']),
                         ('hostInstanceId', '11058c39-19d3-4965-a5de-64ed4d035174'),
                         ('scope', 'whole-runtime'), ('dataDir', '/foreign/data'),
                         ('installedExtras', ['native-desktop']), ('restart', 'custom'),
                         ('target', {**preview['target'], 'source': 'https://foreign/wheel'}), ('previewId', 'bad')):
            with self.subTest(key=key, bad=bad), patch.object(full, 'action', return_value={**preview, key: bad}):
                with self.assertRaises(RuntimeError):
                    full.preview_confirmation(config, self.root, health)

    def test_unsupported_preview_and_api_input_errors_leave_explicit_evidence(self):
        config, health, preview = self.identity_inputs()
        refused = {**preview, 'supported': False, 'reason': 'The loaded service is not this host.'}
        with patch.object(full, 'action', return_value=refused):
            with self.assertRaisesRegex(RuntimeError, 'preview refused'):
                full.preview_confirmation(config, self.root, health)
        evidence = full.load(self.root / 'preview.json')
        self.assertFalse(evidence['supported'])
        self.assertEqual(evidence['reason'], refused['reason'])
        with patch.object(full, 'action', side_effect=RuntimeError('full-app authenticated API request failed (HTTP 409)')):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 409'):
                full.preview_confirmation(config, self.root, health)
        evidence = full.load(self.root / 'preview.json')
        self.assertFalse(evidence['passed'])
        self.assertEqual(evidence['failure'], 'preview-api-input-or-response')
        self.assertIn('HTTP 409', evidence['detail'])

    def test_public_action_unwraps_operation_and_status_envelopes_without_guessing(self):
        operation = {'operationId': 'a' * 32, 'status': 'ready'}
        for name, result in (('maintenance.installationRepairConfirm', operation),
                             ('maintenance.installationRepairStatus', {'operation': operation})):
            with self.subTest(name=name), patch.object(full, 'request', return_value={
                    'accepted': True, 'revision': 3, 'effects': [], 'hostInstanceId': 'service-envelope',
                    'result': result}):
                self.assertEqual(full.action({}, name, {}), result)
        for envelope in (None, [], {}, {'accepted': False, 'result': operation},
                         {'accepted': True, 'operation': operation}, {'accepted': True, 'result': None}):
            with self.subTest(envelope=envelope), patch.object(full, 'request', return_value=envelope):
                with self.assertRaisesRegex(RuntimeError, 'unrecognized accepted/result'):
                    full.action({}, 'maintenance.installationRepairConfirm', {})

    def test_atomic_admission_joins_current_and_exact_preview_no_legacy_fallback(self):
        repair = self.root
        preview, identity = 'a' * 32, 'b' * 32
        full.save(repair / 'admissions.json', {'schema': 1, 'current': identity, 'previews': {preview: identity}})
        self.assertEqual(full.admitted_operation(repair, preview), identity)
        self.assertIsNone(full.admitted_operation(repair, 'c' * 32))
        for bad in ({'schema': True, 'current': identity, 'previews': {preview: identity}},
                    {'schema': 1, 'current': 'x', 'previews': {preview: identity}},
                    {'schema': 1, 'current': identity, 'previews': {'bad': identity}},
                    {'schema': 1, 'current': identity, 'previews': {preview: 'bad'}},
                    {'schema': 1, 'current': None, 'previews': {preview: identity}},
                    {'schema': 1, 'current': 'c' * 32, 'previews': {preview: identity, 'd' * 32: 'c' * 32}}):
            full.save(repair / 'admissions.json', bad)
            with self.subTest(admission=bad), self.assertRaises(RuntimeError):
                full.admitted_operation(repair, preview)

    def test_legacy_or_prepared_files_without_atomic_admission_never_authorize_recovery(self):
        preview, identity = 'a' * 32, 'b' * 32
        full.save(self.root / 'latest.json', {'operationId': identity})
        full.save(self.root / ('confirmed-' + preview + '.json'), {'operationId': identity})
        with self.assertRaises(FileNotFoundError):
            full.admitted_operation(self.root, preview)
        full.save(self.root / 'admissions.json', {'schema': 1, 'current': None, 'previews': {}})
        self.assertIsNone(full.admitted_operation(self.root, preview))

    def test_ready_requires_real_doctor_health_and_reinstallation(self):
        receipt = {'status': 'ready', 'doctorVerified': True, 'healthVerified': True, 'reinstalled': True}
        full.require_ready(receipt)
        for key in receipt:
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                full.require_ready({**receipt, key: False})
        with self.assertRaises(RuntimeError):
            full.require_ready({**receipt, 'status': 'reinstalled'})

    def test_cleanup_preserves_unowned_preexisting_definition(self):
        full.save(self.root / 'owner.json', {'definition': None, 'helpers': []})
        with patch.object(full.native, 'manager_capability') as manager, patch.object(full, 'execute') as command:
            full.cleanup(self.root, self.root)
            manager.assert_not_called()
            command.assert_not_called()

    def test_unknown_cleanup_not_replayed(self):
        full.save(self.root / 'owner.json', {'definition': {'path': 'uninspected'}, 'helpers': [], 'cleanupStarted': True})
        with patch.object(full, 'execute') as command:
            with self.assertRaisesRegex(RuntimeError, 'already attempted'):
                full.cleanup(self.root, self.root)
            command.assert_not_called()

    def test_foreign_or_active_helper_cannot_be_torn_down(self):
        full.save(self.root / 'owner.json', {'definition': None, 'helpers': [{'label': 'owned'}],
                                           'cleanupStarted': False})
        with patch.object(full.native, 'manager_environment', return_value={}), \
             patch.object(full.native, 'manager_capability'), \
             patch.object(full.native, 'wait_for', side_effect=RuntimeError('foreign/unsettled helper')), \
             patch.object(full, 'execute') as command:
            with self.assertRaisesRegex(RuntimeError, 'foreign/unsettled'):
                full.cleanup(self.root, self.root)
            command.assert_not_called()
            self.assertFalse(full.load(self.root / 'cleanup.json')['passed'])

    def test_captured_service_requires_definition_identity_before_custody(self):
        full.save(self.root / 'owner.json', {'definition': {'path': 'owned'}, 'servicePIDs': []})
        with patch.object(full.sys, 'platform', 'linux'), \
             patch.object(full.native, 'service_observation', return_value={'MainPID': '123'}), \
             patch.object(full.native, 'owned_definition', side_effect=RuntimeError('foreign definition')):
            with self.assertRaisesRegex(RuntimeError, 'foreign'):
                full.capture_service(self.root, {})
        self.assertEqual(full.load(self.root / 'owner.json')['servicePIDs'], [])

    def test_successful_cleanup_records_real_readback_absence_and_observed_pids(self):
        path = self.root / 'owned.service'
        path.write_text('test-only definition, never submitted')
        owner = {'definition': {'path': str(path), 'sha256': full.digest(path),
                               'stat': [path.stat().st_dev, path.stat().st_ino], 'home': str(self.root / 'data')},
                 'helpers': [{'label': 'owned-helper', 'folder': str(self.root)}], 'servicePIDs': [], 'helperPIDs': [],
                 'cleanupStarted': False}
        full.save(self.root / 'owner.json', owner)
        stopped = False

        def execute(*args, **kwargs):
            nonlocal stopped
            if 'stop' in args:
                stopped = True

        def properties(*args):
            if args[0] != full.native.UNIT:
                return {'LoadState': 'not-found'}
            return {'MainPID': '0' if stopped else '123', 'LoadState': 'loaded' if path.exists() else 'not-found',
                    'FragmentPath': str(path), 'NeedDaemonReload': 'no', 'DropInPaths': ''}

        absent = {'settled': True, 'LoadState': 'not-found', 'MainPID': '0'}
        reset = SimpleNamespace(service_commands=lambda home: (['systemctl', '--user', 'stop', full.native.UNIT],))
        with patch.object(full.sys, 'platform', 'linux'), \
             patch.object(full.native, 'paths', return_value=path), \
             patch.object(full.native, 'manager_environment', return_value={}), \
             patch.object(full.native, 'manager_capability'), \
             patch.object(full.native, 'service_observation', return_value={'MainPID': '123'}), \
             patch.object(full.native, 'pid_start', side_effect=lambda pid, env: '' if stopped else 'observed-start'), \
             patch.object(full.native, 'properties', side_effect=properties), \
             patch.object(full.native, 'owned_helper_observation', return_value=absent), \
             patch.object(full.native, 'child_settlement', return_value={'settled': True}), \
             patch.object(full.native, 'module', return_value=reset), \
             patch.object(full, 'execute', side_effect=execute):
            full.cleanup(self.root, self.root)
        result = full.load(self.root / 'cleanup.json')
        self.assertTrue(result['passed'] and result['definitionAbsent'] and result['processesExited'])
        self.assertEqual(result['servicePIDs'], [{'pid': 123, 'start': 'observed-start'}])
        self.assertEqual(result['helpers'][0]['absent'], True)
        self.assertFalse(path.exists())

    def test_fullapp_seed_and_readonly_retention_scripts_compile(self):
        for script in (full.PRECONFIGURE, full.SENTINEL):
            compile(script, '<CI-only script>', 'exec')
        self.assertIn("'autoCheck':False,'autoInstall':False", full.PRECONFIGURE)
        self.assertIn('await service.close()', full.PRECONFIGURE)
        self.assertIn("tls.setup_local_ca(home,config)", full.PRECONFIGURE)
        self.assertIn('?mode=ro', full.SENTINEL)
        for forbidden in ('background_updates=', 'background_work=', 'preload_providers=', 'Runtime(',
                          'conversation.send', 'sitecustomize', 'mock'):
            self.assertNotIn(forbidden, full.PRECONFIGURE)

    def test_native_harness_has_no_production_monkeypatches_or_fake_effects(self):
        source = (FIXTURES / 'installation_repair_native_full.py').read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn(node.module, ('unittest.mock', 'pytest'))
        for forbidden in ('native.wheel(', 'new_operation(', 'worker.run(', 'repair.launch =',
                          'UV_OFFLINE', "'--no-deps'", "'sync'", '.pth'):
            # .pth appears only in the guard/documentation, never written.
            if forbidden != '.pth':
                self.assertNotIn(forbidden, source)
        self.assertEqual(full.LIMITS['officialPublishedTrust'], False)
        self.assertEqual(full.native.worker_command({'basePython': '/stable/python'}, Path('/operation'))[:3],
                         ['/stable/python', '-I', '-S'])

    def test_dispatch_evidence_rejects_any_second_attempt_even_if_worker_noops(self):
        label = 'amplifier-installation-repair-' + 'c' * 32
        event = {'label': label, 'servicePID': 10}
        path = self.root / 'dispatch-audit.jsonl'
        path.write_text(json.dumps(event) + '\n')
        self.assertEqual(full.dispatches(self.root, label, 10), [event])
        path.write_text(path.read_text() * 2)
        with self.assertRaisesRegex(RuntimeError, 'exactly one'):
            full.dispatches(self.root, label, 10)


class FailureDiagnosticTests(unittest.TestCase):
    """Artificial metadata/graphs only. Never execute or install a prefix."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.worker = full.native.module(full.SOURCE / 'amplifier_web/installation_repair_worker.py')
        self.reset = full.native.module(full.SOURCE / 'amplifier_web/reset.py')
        self.identity = 'a' * 32
        self.folder = self.root / 'data/installation-repair' / self.identity
        self.folder.mkdir(parents=True)
        for name in full.native.SIBLINGS:
            shutil.copyfile(full.SOURCE / 'amplifier_web' / name, self.folder / name)
        self.plan = {'home': str(self.root / 'data'), 'toolRoot': str(self.root / 'tools'),
                     'operationId': self.identity, 'target': {'version': '0.89.0', 'revision': 'b' * 40},
                     'artifact': {'path': str(self.folder / 'amplifier_unified-0.89.0-py3-none-any.whl'),
                                  'url': 'https://github.com/microsoft/amplifier-unified/releases/download/v0.89.0/amplifier_unified-0.89.0-py3-none-any.whl',
                                  'sha256': 'c' * 64}, 'env': {'TOKEN': 'secret-env-not-evidence'}}
        self.receipt = {'operationId': self.identity, 'status': 'rolled-back', 'stage': 'rolled-back',
                        'reinstalled': False, 'serviceState': 'running', 'errorType': 'ValueError',
                        'effectBoundary': {'schema': 1, 'stopIntent': True, 'stopSettled': True, 'mutationIntent': True},
                        'detail': 'private detail not for evidence'}
        # Hash-only artifact fixture; not a wheel/archive and never installed.
        artifact = Path(self.plan['artifact']['path'])
        artifact.write_bytes(b'unit-test artifact checksum input only')
        artifact.chmod(0o600)
        self.plan['artifact']['sha256'] = full.digest(artifact)
        self.failed = self.reset.tool_recovery_root(Path(self.plan['toolRoot'])) / self.identity / 'failed'
        self.candidate_prefix = self.folder / 'candidate-tools/amplifier-unified'
        self.secret = 'credential-sentinel-never-serialize'
        self.url = 'https://user:' + self.secret + '@example.test/repo.git?token=' + self.secret
        self.commit = 'd' * 40
        self.stamp = {'schema': 1, 'repository': 'microsoft/amplifier-unified',
                      'version': '0.89.0', 'revision': self.plan['target']['revision']}
        self.raw_app = {'name': 'amplifier-unified', 'version': '0.89.0', 'stamp': self.stamp,
                        'direct': {'url': Path(self.plan['artifact']['path']).as_uri(), 'archive_info': {}}}
        self.app = self.worker._app_source(self.plan, self.raw_app)
        self.candidate = [self.app, self.dependency('main')]
        self.fresh = [self.app, self.dependency(self.commit)]
        full.save(self.folder / 'child-boundary.json', {'schema': 1, 'settled': True})
        full.save(self.folder / 'candidate-graph.json', self.candidate)
        self.prefix(self.failed, self.commit)
        self.prefix(self.candidate_prefix, 'main')
        self.enterContext(patch.object(full, 'execute', side_effect=AssertionError('no commands in diagnostics')))
        self.enterContext(patch.object(full.native, 'execute', side_effect=AssertionError('no native commands')))
        self.spawn = self.enterContext(patch.object(subprocess, 'Popen', side_effect=AssertionError('never spawn retained code')))

    def dependency(self, requested):
        return {'name': 'dependency-fixture', 'version': '1.2.3',
                'direct': {'url': self.url, 'vcs_info': {'vcs': 'git', 'commit_id': self.commit,
                                                      'requested_revision': requested}}}

    def prefix(self, prefix, requested):
        site = prefix / 'lib/python3.13/site-packages'
        for row in (self.raw_app, self.dependency(requested)):
            info = site / (row['name'].replace('-', '_') + '-' + row['version'] + '.dist-info')
            info.mkdir(parents=True)
            (info / 'METADATA').write_text('Metadata-Version: 2.1\nName: ' + row['name'] + '\nVersion: ' + row['version'] + '\n')
            (info / 'direct_url.json').write_text(json.dumps(row['direct']))
        app = site / 'amplifier_web'
        app.mkdir()
        for name in full.native.PRODUCTION:
            shutil.copyfile(full.SOURCE / 'amplifier_web' / name, app / name)
        (app / 'release-source.json').write_text(json.dumps(self.stamp))
        # No usable interpreter/launcher, package import, wheel or install exists.
        (prefix / 'bin').mkdir()
        (prefix / 'bin/python').write_text('DO NOT EXECUTE: unit-test metadata fixture')
        # Authoring host may use group-writable umask; make only these temporary
        # test inputs private so the UNCHANGED production ownership gate applies.
        for path in (prefix, *prefix.rglob('*')):
            path.chmod(0o700 if path.is_dir() else 0o600)

    def test_artificial_graphs_show_commit_equal_requested_ref_unequal_without_raw_values(self):
        report = full.compare_diagnostic_graphs(self.candidate, self.fresh)
        self.assertFalse(report['equal'])
        self.assertEqual(report['differenceCount'], 1)
        row = report['differences'][0]
        self.assertEqual(row['name'], 'dependency-fixture')
        self.assertTrue(row['fields']['direct.vcs_info.commit_id']['equal'])
        ref = row['fields']['direct.vcs_info.requested_revision']
        self.assertFalse(ref['equal'])
        self.assertEqual(ref['candidate']['sha256'], full.value_hash('main'))
        self.assertEqual(ref['failed']['sha256'], full.value_hash(self.commit))
        text = json.dumps(report)
        for forbidden in (self.url, self.secret, 'https://', 'user:', '"main"'):
            self.assertNotIn(forbidden, text)
        self.assertEqual(report['candidatePackages'][1]['gitCommit'], self.commit)

    def test_equal_graph_is_only_observation_not_repair_success(self):
        report = full.compare_diagnostic_graphs(self.candidate, self.candidate)
        self.assertTrue(report['equal'])
        self.assertEqual(report['differences'], [])
        with self.assertRaisesRegex(RuntimeError, 'did not verify ready'):
            full.accept_ready(self.folder, self.plan, self.receipt, self.root)
        self.assertEqual(self.receipt['errorType'], 'ValueError')
        self.assertFalse(full.load(self.root / 'failure-diagnostics.json')['causeQualified'])

    def test_passive_graph_matches_production_row_normalization_without_execution(self):
        rows, hashes = full.passive_prefix_graph(self.worker, self.plan, self.failed)
        self.assertEqual(rows, self.fresh)
        self.assertEqual(hashes['staticProtocol'], 1)
        self.assertEqual(hashes['normalizedGraphSHA256'], full.value_hash(self.fresh))
        self.assertTrue(all(item['matchesCandidateSource'] for key, item in hashes['sourceHashes'].items()
                            if key != 'release-source.json'))
        self.assertEqual(len(hashes['distributionHashes']), 2)
        with patch.object(self.worker, 'command', return_value=SimpleNamespace(
                stdout=json.dumps([self.raw_app, self.dependency(self.commit)]))):
            production_rows = self.worker.installed_graph(self.plan, self.failed / 'bin/python', {})
        self.assertEqual(rows, production_rows)
        self.spawn.assert_not_called()

    def test_provenance_refusal_precedes_any_metadata_discovery(self):
        with patch.object(self.worker, '_installed_provenance', side_effect=ValueError('foreign prefix')), \
             patch.object(full.metadata.Distribution, 'discover') as discover:
            with self.assertRaises(ValueError):
                full.passive_prefix_graph(self.worker, self.plan, self.failed)
            discover.assert_not_called()
        self.spawn.assert_not_called()

    def test_real_provenance_rejects_tampered_stamp_before_discovery(self):
        path = self.failed / 'lib/python3.13/site-packages/amplifier_web/release-source.json'
        path.write_text(json.dumps({**self.stamp, 'revision': 'f' * 40}))
        with patch.object(full.metadata.Distribution, 'discover') as discover:
            with self.assertRaises(ValueError):
                full.passive_prefix_graph(self.worker, self.plan, self.failed)
            discover.assert_not_called()

    def test_symlinked_dependency_metadata_is_not_read(self):
        path = self.failed / 'lib/python3.13/site-packages/dependency_fixture-1.2.3.dist-info/direct_url.json'
        original = self.root / 'foreign-private-metadata'
        original.write_text('{"private":"' + self.secret + '"}')
        path.unlink()
        path.symlink_to(original)
        with self.assertRaises(ValueError):
            full.passive_prefix_graph(self.worker, self.plan, self.failed)
        self.spawn.assert_not_called()

    def test_bounded_failure_report_preserves_native_error_and_redacts_all_private_inputs(self):
        report = full.failure_diagnostics(self.folder, self.plan, self.receipt)
        self.assertEqual(report['originalErrorType'], 'ValueError')
        self.assertEqual(report['diagnosticErrors'], [])
        self.assertFalse(report['graphComparison']['equal'])
        self.assertTrue(report['candidateDiskMatchesRecordedGraph'])
        self.assertEqual(report['effectBoundary'], self.receipt['effectBoundary'])
        self.assertTrue(report['childSettlement']['settled'])
        self.assertEqual(report['candidateGraphSHA256'], full.digest(self.folder / 'candidate-graph.json'))
        self.assertTrue(report['operationArtifact']['matchesExpected'])
        self.assertIn('protocolProbeSHA256', report['workerArtifacts'])
        text = json.dumps(report)
        self.assertLess(len(text), 24_000)
        for forbidden in (self.secret, self.url, 'secret-env-not-evidence', 'private detail', 'https://', '"env"', str(self.root)):
            self.assertNotIn(forbidden, text)
        self.spawn.assert_not_called()

    def test_failed_prefix_refusal_is_own_diagnostic_error_not_original_cause(self):
        shutil.move(self.failed / 'lib/python3.13/site-packages/amplifier_unified-0.89.0.dist-info/METADATA',
                    self.root / 'retained-metadata')
        report = full.failure_diagnostics(self.folder, self.plan, self.receipt)
        self.assertEqual(report['originalErrorType'], 'ValueError')
        self.assertIn({'section': 'failed-prefix', 'errorType': 'FileNotFoundError'}, report['diagnosticErrors'])
        self.assertNotIn('graphComparison', report)
        self.spawn.assert_not_called()

    def test_unknown_rollback_incomplete_or_unsettled_children_never_inspect_prefix(self):
        for status, stage, settled in (('unknown', 'rollback-incomplete', True),
                                        ('rolled-back', 'rolled-back', False)):
            full.save(self.folder / 'child-boundary.json', {'schema': 1, 'settled': settled})
            with self.subTest(status=status, settled=settled), patch.object(full, 'passive_prefix_graph') as inspect:
                report = full.failure_diagnostics(self.folder, self.plan, {**self.receipt, 'status': status, 'stage': stage})
                self.assertEqual(report['prefixInspection'], 'refused-unproven-rollback-or-child-settlement')
                inspect.assert_not_called()

    def test_missing_child_ledger_never_assumes_settled(self):
        (self.folder / 'child-boundary.json').unlink()
        with patch.object(full, 'passive_prefix_graph') as inspect:
            report = full.failure_diagnostics(self.folder, self.plan, self.receipt)
            self.assertIsNone(report['childSettlement'])
            inspect.assert_not_called()
        self.assertIn({'section': 'child-settlement', 'errorType': 'FileNotFoundError'}, report['diagnosticErrors'])

    def test_diagnostic_failure_cannot_replace_readiness_failure_or_publish_exception_text(self):
        with patch.object(full, 'failure_diagnostics', side_effect=ValueError(self.url)):
            with self.assertRaisesRegex(RuntimeError, 'did not verify ready'):
                full.accept_ready(self.folder, self.plan, self.receipt, self.root)
        report = full.load(self.root / 'failure-diagnostics.json')
        self.assertEqual(report['diagnosticErrors'], [{'section': 'diagnostic-collection', 'errorType': 'ValueError'}])
        self.assertEqual(report['originalErrorType'], 'ValueError')
        self.assertNotIn(self.secret, json.dumps(report))
        self.assertEqual(self.receipt['errorType'], 'ValueError')

    def test_failure_evidence_written_before_unchanged_ready_requirement(self):
        with patch.object(full, 'require_ready', side_effect=RuntimeError('original readiness failure')) as require:
            with self.assertRaisesRegex(RuntimeError, 'original readiness failure'):
                full.accept_ready(self.folder, self.plan, self.receipt, self.root)
        self.assertTrue((self.root / 'failure-diagnostics.json').is_file())
        require.assert_called_once_with(self.receipt)

    def test_ready_success_does_not_inspect_failed_prefix_or_collect_diagnostics(self):
        receipt = {'status': 'ready', 'doctorVerified': True, 'healthVerified': True, 'reinstalled': True}
        with patch.object(full, 'failure_diagnostics') as collect, patch.object(full, 'save') as save:
            full.accept_ready(self.folder, self.plan, receipt, self.root)
            collect.assert_not_called()
            save.assert_not_called()

    def test_unknown_source_worker_never_imported_or_prefix_read(self):
        path = self.folder / 'installation_repair_worker.py'
        path.write_bytes(path.read_bytes() + b'\n# unaccepted source\n')
        with patch.object(full.native, 'module') as import_worker, patch.object(full, 'passive_prefix_graph') as inspect:
            report = full.failure_diagnostics(self.folder, self.plan, self.receipt)
            import_worker.assert_not_called()
            inspect.assert_not_called()
        self.assertEqual(report['diagnosticErrors'], [{'section': 'worker-source', 'errorType': 'RuntimeError'}])
        self.assertEqual(report['originalErrorType'], 'ValueError')

    def test_untrusted_graph_fields_are_hashed_and_invalid_identifiers_refused(self):
        dependency = self.dependency(self.secret)
        dependency['direct']['unexpected-' + self.secret] = {'env': self.secret}
        report = full.compare_diagnostic_graphs(self.candidate, [self.app, dependency])
        self.assertNotIn(self.secret, json.dumps(report))
        self.assertFalse(report['equal'])
        with self.assertRaises(RuntimeError):
            full.compare_diagnostic_graphs(self.candidate, [self.app, {**dependency, 'version': self.url}])

    def test_difference_details_and_graph_size_are_bounded(self):
        candidate = [self.app, *({'name': 'package-' + str(index), 'version': '1', 'direct': None}
                                 for index in range(65))]
        failed = [candidate[0], *({**row, 'version': '2'} for row in candidate[1:])]
        report = full.compare_diagnostic_graphs(candidate, failed)
        self.assertEqual(report['differenceCount'], 65)
        self.assertEqual(len(report['differences']), 64)
        self.assertTrue(report['differencesTruncated'])
        with self.assertRaises(RuntimeError):
            full.diagnostic_graph([self.app] * 513)

    def test_diagnostic_evidence_write_failure_leaves_original_readiness_failure(self):
        with patch.object(full, 'save', side_effect=PermissionError('private write detail')):
            with self.assertRaisesRegex(RuntimeError, 'did not verify ready'):
                full.accept_ready(self.folder, self.plan, self.receipt, self.root)
        self.assertEqual(self.receipt['errorType'], 'ValueError')


class AcquisitionAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.version, self.revision = '0.89.0', 'a' * 40
        # Minimal archive bytes test ONLY the real acquisition verifier. This
        # is never installed, run, or passed to full-app acceptance.
        self.artifacts = full.native.module(full.SOURCE / 'amplifier_web/app_release_artifacts.py')
        archive = self.root / self.artifacts.filename(self.version)
        with zipfile.ZipFile(archive, 'x') as output:
            output.writestr(self.artifacts.STAMP, json.dumps({'schema': 1, 'repository': self.artifacts.REPOSITORY,
                                                            'version': self.version, 'revision': self.revision}))
        self.config = {'version': self.version, 'revision': self.revision, 'root': str(self.root),
                       'home': str(self.root / 'data'), 'adapterSHA256': acquisition.sha(acquisition.__file__),
                       'artifact': {'path': str(archive), 'sha256': acquisition.sha(archive),
                                    'url': self.artifacts.SOURCE + '/releases/download/v' + self.version + '/' + archive.name}}
        self.manifest = self.root / 'fixture.json'
        full.save(self.manifest, self.config)
        self.reset = SimpleNamespace(REPOSITORY='microsoft/amplifier-unified',
                                     release_metadata=Mock(), download_command=Mock(return_value='ordinary download'))
        package = ModuleType('amplifier_web')
        package.reset, package.app_release_artifacts = self.reset, self.artifacts
        self.enterContext(patch.dict('sys.modules', {'amplifier_web': package}))
        self.enterContext(patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}))
        self.original_download = self.reset.download_command
        self.original_verify = self.artifacts.verify
        self.audit_registration = self.enterContext(patch.object(acquisition.sys, 'addaudithook'))
        acquisition.install(self.manifest)
        self.folder = Path(self.config['home']) / 'installation-repair' / ('b' * 32)
        self.folder.mkdir(parents=True)

    def test_patches_exactly_three_acquisition_attributes(self):
        tree = ast.parse(Path(acquisition.__file__).read_text())
        assignments = [ast.unparse(target) for node in ast.walk(tree) if isinstance(node, ast.Assign)
                       for target in node.targets if isinstance(target, ast.Attribute)]
        self.assertEqual(sorted(assignments), ['artifacts.prepare', 'reset.download_command', 'reset.release_metadata'])
        self.assertIs(self.artifacts.verify, self.original_verify)
        self.assertEqual(self.reset.release_metadata({}), {'tag_name': 'v0.89.0', 'draft': False, 'prerelease': False})

    def test_only_exact_tag_lookup_intercepted_all_other_downloads_use_production(self):
        args = ('git', 'ls-remote', 'https://github.com/microsoft/amplifier-unified',
                'refs/tags/v0.89.0', 'refs/tags/v0.89.0^{}')
        self.assertEqual(self.reset.download_command(*args, env={}, timeout=60).stdout,
                         self.revision + '\trefs/tags/v0.89.0\n')
        self.original_download.assert_not_called()
        for changed in (args[:-1], ('git', 'ls-remote', 'https://foreign/repo', *args[3:]),
                        ('uv', 'tool', 'install')):
            self.assertEqual(self.reset.download_command(*changed, env={'private': 'not logged'}), 'ordinary download')
        self.assertEqual(self.original_download.call_count, 3)

    def test_copies_exact_verified_bytes_then_reserves_helper_before_return(self):
        with patch.object(acquisition, 'helper_custody') as custody:
            result = asyncio.run(self.artifacts.prepare(self.folder, self.version, self.revision))
            custody.assert_called_once_with(self.config, self.folder)
        self.assertEqual(Path(result['path']).read_bytes(), Path(self.config['artifact']['path']).read_bytes())
        self.assertEqual(self.artifacts.verify(result, self.version, self.revision), result)
        self.assertEqual(Path(result['path']).stat().st_mode & 0o777, 0o600)

    def test_tampered_candidate_rejected_before_copy_or_custody(self):
        Path(self.config['artifact']['path']).write_bytes(b'tampered')
        with patch.object(acquisition, 'helper_custody') as custody:
            with self.assertRaises(ValueError):
                asyncio.run(self.artifacts.prepare(self.folder, self.version, self.revision))
            custody.assert_not_called()
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_wrong_revision_delivery_or_folder_never_acquired(self):
        for folder, revision, delivery in ((self.folder, 'c' * 40, None),
                                            (self.root, self.revision, None),
                                            (self.folder, self.revision, {'url': 'foreign'})):
            with self.subTest(folder=folder, revision=revision, delivery=delivery), \
                 patch.object(acquisition, 'helper_custody') as custody:
                with self.assertRaises(ValueError):
                    asyncio.run(self.artifacts.prepare(folder, self.version, revision, delivery=delivery))
                custody.assert_not_called()

    def test_operation_copy_never_overwritten_on_replay(self):
        with patch.object(acquisition, 'helper_custody') as custody:
            asyncio.run(self.artifacts.prepare(self.folder, self.version, self.revision))
            with self.assertRaises(FileExistsError):
                asyncio.run(self.artifacts.prepare(self.folder, self.version, self.revision))
            self.assertEqual(custody.call_count, 1)

    def test_adapter_refuses_nonci_or_changed_disclosed_source(self):
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}):
            with self.assertRaisesRegex(RuntimeError, 'exact disclosed CI'):
                acquisition.install(self.manifest)
        full.save(self.manifest, {**self.config, 'adapterSHA256': 'f' * 64})
        with self.assertRaisesRegex(RuntimeError, 'exact disclosed CI'):
            acquisition.install(self.manifest)

    def test_audit_only_observes_repair_dispatch_and_never_serializes_private_args(self):
        audit = self.audit_registration.call_args.args[0]
        private = {'TOKEN': 'never-publish-this'}
        label = 'amplifier-installation-repair-' + 'b' * 32
        audit('subprocess.Popen', ('systemd-run', ['systemd-run', '--unit=' + label, '--', 'private-command'],
                                   'private-working-directory', private))
        audit('subprocess.Popen', ('uv', ['uv', 'tool', 'install', 'private-url'], None, private))
        path = self.root / 'dispatch-audit.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(rows, [{'label': label, 'servicePID': os.getpid()}])
        self.assertNotIn('private', path.read_text())
        mac = 'com.microsoft.amplifier-unified.repair.' + 'c' * 32
        audit('subprocess.Popen', ('launchctl', ['launchctl', 'submit', '-l', mac, '--', 'private'], None, private))
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        # Legacy submit is specifically NOT acceptable one-shot bootstrap proof.
        self.assertEqual(rows, [{'label': label, 'servicePID': os.getpid()}])

    def test_audit_failure_does_not_block_actual_dispatch(self):
        audit = self.audit_registration.call_args.args[0]
        label = 'amplifier-installation-repair-' + 'b' * 32
        with patch.object(acquisition.os, 'open', side_effect=PermissionError):
            audit('subprocess.Popen', ('systemd-run', ['systemd-run', '--unit=' + label], None, {}))

    def test_custody_is_saved_before_any_native_effect_and_refuses_preexisting_jobs(self):
        config = {**self.config, 'nativeHarness': acquisition.__file__,
                  'nativeHarnessSHA256': acquisition.sha(acquisition.__file__), 'basePython': '/stable/python'}
        owner_path = self.root / 'owner.json'
        full.save(owner_path, {'helpers': []})
        fake = SimpleNamespace(check=full.check, sys=SimpleNamespace(platform='linux'),
                               manager_environment=Mock(return_value={}), helper_label=full.native.helper_label,
                               properties=Mock(return_value={'LoadState': 'not-found'}),
                               load=full.load, save=full.save)
        spec = Mock()
        with patch.object(acquisition.importlib.util, 'spec_from_file_location', return_value=spec), \
             patch.object(acquisition.importlib.util, 'module_from_spec', return_value=fake):
            acquisition.helper_custody(config, self.folder)
            self.assertEqual(full.load(owner_path)['helpers'], [{
                'label': full.native.helper_label(self.folder.name), 'folder': str(self.folder),
                'basePython': '/stable/python'}])
            full.save(owner_path, {'helpers': []})
            fake.properties.return_value = {'LoadState': 'loaded', 'ActiveState': 'inactive'}
            with self.assertRaisesRegex(RuntimeError, 'preexisting'):
                acquisition.helper_custody(config, self.folder)
            self.assertEqual(full.load(owner_path)['helpers'], [])

    def test_changed_custody_source_never_executed(self):
        config = {**self.config, 'nativeHarness': acquisition.__file__, 'nativeHarnessSHA256': '0' * 64}
        with patch.object(acquisition.importlib.util, 'spec_from_file_location') as spec:
            with self.assertRaisesRegex(RuntimeError, 'helper changed'):
                acquisition.helper_custody(config, self.folder)
            spec.assert_not_called()


class FullLaunchdBootstrapTests(unittest.TestCase):
    """Closed a0d829e8 job inputs and native readbacks, all effects mocked."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.folder = self.root / 'data/installation-repair' / ('e' * 32)
        self.folder.mkdir(parents=True)
        self.config = {'root': str(self.root), 'home': str(self.root / 'data'), 'uid': os.getuid(),
                       'basePython': '/stable/owned/python3.13',
                       'adapterSHA256': full.digest(Path(acquisition.__file__))}
        self.reservation = acquisition.launchd_reservation(self.config, self.folder)
        self.helper = {'label': self.reservation['label'], 'folder': str(self.folder),
                       'basePython': self.config['basePython'], 'launchdReservation': self.reservation}
        full.save(self.root / 'fixture.json', self.config)
        full.save(self.root / 'owner.json', {'helpers': [self.helper], 'helperPIDs': []})
        self.definition = {
            'Label': self.helper['label'],
            'ProgramArguments': [self.config['basePython'], '-I', '-S',
                                 str(self.folder / 'installation_repair_worker.py'), str(self.folder)],
            'WorkingDirectory': str(self.folder), 'RunAtLoad': True, 'KeepAlive': False,
            'ProcessType': 'Background', 'StandardOutPath': os.devnull, 'StandardErrorPath': os.devnull, 'Umask': 0o077}
        self.plist = self.folder / 'helper.plist'
        self.raw = plistlib.dumps(self.definition, sort_keys=True)
        self.plist.write_bytes(self.raw)
        self.plist.chmod(0o600)
        self.job = {**self.reservation, 'plistSHA256': hashlib.sha256(self.raw).hexdigest(),
                    'cleanupState': 'retained-for-explicit-review', 'cleanupOwner': 'installation-owner',
                    'detail': 'The one-shot registration may remain after exit. Remove only this exact settled job after reviewing its receipt; do not relaunch it.'}
        self.plan = {'operationId': self.folder.name, 'home': self.config['home'], 'uid': self.config['uid'],
                     'basePython': self.config['basePython'], 'helperJob': self.job}
        self.operation = {'operationId': self.folder.name, 'helperJob': self.job}
        self.write_inputs()
        # Joined ownership reader imports this exact copied worker and performs
        # its real private-file checks; no missing-file polling is mocked away.
        worker = self.folder / 'installation_repair_worker.py'
        shutil.copyfile(full.SOURCE / 'amplifier_web/installation_repair_worker.py', worker)
        worker.chmod(0o600)
        full.save(self.folder / 'child-boundary.json', {'schema': 1, 'settled': True})

        def once(read, predicate=bool, **kwargs):
            value = read()
            if not predicate(value):
                raise RuntimeError('bounded unit observation is not positively settled')
            return value

        # All synthetic manager transitions are immediate. A missing fixture
        # input/state must fail this test, never enter a production sleep loop.
        self.enterContext(patch.object(full.native, 'wait_for', side_effect=once))
        self.spawn = self.enterContext(patch.object(subprocess, 'Popen', side_effect=AssertionError('no native input')))

    def write_inputs(self):
        full.save(self.folder / 'plan.json', self.plan)
        full.save(self.folder / 'operation.json', self.operation)

    def hook(self):
        with patch.object(acquisition.sys, 'addaudithook') as register:
            acquisition.observe_dispatches(self.config)
        return register.call_args.args[0]

    def audit(self, hook, argv):
        hook('subprocess.Popen', ('launchctl', argv, 'private cwd', {'TOKEN': 'credential-audit-sentinel'}))

    def args(self):
        return ['launchctl', 'bootstrap', self.job['domain'], str(self.plist)]

    def test_exact_private_closed_plist_and_plan_operation_custody(self):
        job, saved, definition = acquisition.checked_launchd_inputs(self.config, self.folder)
        self.assertEqual(job, self.job)
        self.assertEqual(definition, self.definition)
        self.assertEqual(saved, {'sha256': self.job['plistSHA256'],
                                 'stat': [self.plist.stat().st_dev, self.plist.stat().st_ino]})
        self.spawn.assert_not_called()

    def test_known_bootstrap_observed_only_as_label_and_service_pid(self):
        self.audit(self.hook(), self.args())
        text = (self.root / 'dispatch-audit.jsonl').read_text()
        self.assertEqual(json.loads(text), {'label': self.job['label'], 'servicePID': os.getpid()})
        for secret in ('credential-audit-sentinel', str(self.folder), 'private cwd', self.config['basePython'],
                       'helper.plist', 'TOKEN', 'http'):
            self.assertNotIn(secret, text)

    def test_submit_unknown_bootstrap_domain_path_extra_args_and_no_reservation_ignored(self):
        hook = self.hook()
        for args in (['launchctl', 'submit', '-l', self.job['label'], '--', 'private'],
                     ['launchctl', 'bootstrap', 'gui/foreign', str(self.plist)],
                     ['launchctl', 'bootstrap', self.job['domain'], str(self.root / 'foreign.plist')],
                     [*self.args(), '--extra']):
            self.audit(hook, args)
        self.assertFalse((self.root / 'dispatch-audit.jsonl').exists())
        full.save(self.root / 'owner.json', {'helpers': []})
        self.audit(hook, self.args())
        self.assertFalse((self.root / 'dispatch-audit.jsonl').exists())

    def test_closed_job_rejects_keepalive_triggers_argv_workdir_stdio_process_umask(self):
        changes = (('KeepAlive', True), ('KeepAlive', {'SuccessfulExit': False}), ('RunAtLoad', False),
                   ('ProgramArguments', ['/foreign/python']), ('WorkingDirectory', '/foreign'),
                   ('StandardOutPath', '/foreign/log'), ('StandardErrorPath', '/foreign/err'),
                   ('ProcessType', 'Interactive'), ('Umask', 0o022),
                   ('StartInterval', 1), ('EnvironmentVariables', {'SECRET': 'credential-audit-sentinel'}))
        for key, value in changes:
            with self.subTest(key=key, value=value):
                raw = plistlib.dumps({**self.definition, key: value}, sort_keys=True)
                self.plist.write_bytes(raw)
                # Even consistent forged plan hashes cannot relax the closed plist.
                forged = {**self.job, 'plistSHA256': hashlib.sha256(raw).hexdigest()}
                self.plan['helperJob'] = self.operation['helperJob'] = forged
                self.write_inputs()
                with self.assertRaises(ValueError):
                    acquisition.checked_launchd_inputs(self.config, self.folder)
                self.audit(self.hook(), self.args())
                self.assertFalse((self.root / 'dispatch-audit.jsonl').exists())

    def test_changed_digest_missing_mode_and_symlink_private_definition_refused(self):
        for mode in (0o644, 0o640):
            self.plist.chmod(mode)
            with self.assertRaisesRegex(ValueError, 'nonprivate'):
                acquisition.checked_launchd_inputs(self.config, self.folder)
        self.plist.chmod(0o600)
        self.plist.write_bytes(self.raw + b'\n')
        with self.assertRaisesRegex(ValueError, 'definition changed'):
            acquisition.checked_launchd_inputs(self.config, self.folder)
        self.plist.unlink()
        with self.assertRaises(FileNotFoundError):
            acquisition.checked_launchd_inputs(self.config, self.folder)
        target = self.root / 'foreign-private-plist'
        target.write_bytes(self.raw)
        target.chmod(0o600)
        self.plist.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'redirected'):
            acquisition.checked_launchd_inputs(self.config, self.folder)

    def test_plan_and_operation_metadata_must_match_exact_closed_owner(self):
        for key, bad in (('kind', 'launchd-submitted-job'), ('domain', 'gui/foreign'),
                         ('plistPath', '/foreign/helper.plist'), ('cleanupOwner', 'foreign'),
                         ('label', 'foreign'), ('plistSHA256', 'f' * 64)):
            with self.subTest(key=key):
                self.plan['helperJob'] = {**self.job, key: bad}
                self.write_inputs()
                with self.assertRaises(ValueError):
                    acquisition.checked_launchd_inputs(self.config, self.folder)
        self.plan['helperJob'] = self.job
        self.operation['helperJob'] = {**self.job, 'cleanupState': 'foreign'}
        self.write_inputs()
        with self.assertRaises(ValueError):
            acquisition.checked_launchd_inputs(self.config, self.folder)

    def test_provisional_custody_does_not_require_as_yet_uncreated_plist(self):
        self.plist.unlink()
        full.save(self.root / 'owner.json', {'helpers': []})
        config = {**self.config, 'nativeHarness': acquisition.__file__,
                  'nativeHarnessSHA256': acquisition.sha(acquisition.__file__)}
        fake = SimpleNamespace(check=full.check, sys=SimpleNamespace(platform='darwin'),
                               manager_environment=Mock(return_value={}),
                               helper_label=lambda identity: 'com.microsoft.amplifier-unified.repair.' + identity,
                               launch_job=Mock(return_value=[]), load=full.load, save=full.save)
        with patch.object(acquisition.importlib.util, 'spec_from_file_location', return_value=Mock()), \
             patch.object(acquisition.importlib.util, 'module_from_spec', return_value=fake):
            acquisition.helper_custody(config, self.folder)
        self.assertEqual(full.load(self.root / 'owner.json')['helpers'], [self.helper])
        self.assertFalse(self.plist.exists())

    def test_bind_completes_saved_metadata_bytes_before_native_observation(self):
        with patch.object(full.sys, 'platform', 'darwin'):
            bound = full.bind_helper_custody(self.root, self.helper)
            self.assertEqual(bound['helperJob'], self.job)
            self.assertEqual(bound['kind'], 'launchd-user-job')
            self.assertEqual(bound['plistSHA256'], full.digest(self.plist))
            self.assertEqual(full.load(self.root / 'owner.json')['helpers'], [bound])
            self.assertEqual(full.bind_helper_custody(self.root, bound), bound)
        self.spawn.assert_not_called()

    def test_bound_inode_replacement_same_bytes_is_not_adopted_for_cleanup(self):
        with patch.object(full.sys, 'platform', 'darwin'):
            bound = full.bind_helper_custody(self.root, self.helper)
            self.plist.rename(self.folder / 'retained-old-definition')
            self.plist.write_bytes(self.raw)
            self.plist.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError, 'bound helper'):
                full.bind_helper_custody(self.root, bound)

    def test_foreign_metadata_blocks_observation_or_cleanup_effects(self):
        self.operation['helperJob'] = {**self.job, 'domain': 'gui/foreign'}
        self.write_inputs()
        with patch.object(full.sys, 'platform', 'darwin'), \
             patch.object(full.native, 'owned_helper_observation') as observe, \
             patch.object(full, 'execute') as execute:
            with self.assertRaises(ValueError):
                full.owned_full_helper(self.root, self.helper, {})
            observe.assert_not_called()
            execute.assert_not_called()

    def manager_text(self):
        return ('path = ' + str(self.plist) + '\nworking directory = ' + str(self.folder)
                + '\nstdout path = /dev/null\nstderr path = /dev/null\nprogram = ' + self.config['basePython']
                + '\narguments = {\n' + '\n'.join(self.definition['ProgramArguments'])
                + '\n}\nruns = 1\nstate = not running\n')

    def test_native_helper_receives_bound_fields_and_actual_gui_runtime_matches(self):
        observed = {'label': self.job['label'], 'settled': True,
                    'domains': [{'domain': self.job['domain'], 'pids': [], 'runs': ['1']}]}
        with patch.object(full.sys, 'platform', 'darwin'), \
             patch.object(full.native, 'owned_helper_observation', return_value=observed) as native, \
             patch.object(full.native, 'launch_job', return_value=[(self.job['domain'], self.manager_text())]):
            actual = full.owned_full_helper(self.root, self.helper, {})
            self.assertEqual(actual, observed)
            saved = native.call_args.args[0]
            self.assertEqual(saved['helperJob'], self.job)
            self.assertEqual(saved['domain'], self.job['domain'])
            self.assertIn('stat', saved['savedDefinition'])

    def test_runtime_source_domain_workdir_stdio_and_restart_mismatch_refused(self):
        with patch.object(full.sys, 'platform', 'darwin'):
            bound = full.bind_helper_custody(self.root, self.helper)
            observed = {'label': self.job['label'], 'settled': True, 'domains': []}
            for domain, text in (
                    ('gui/foreign', self.manager_text()),
                    (self.job['domain'], self.manager_text().replace(str(self.plist), '/foreign/plist')),
                    (self.job['domain'], self.manager_text().replace('working directory = ', 'wrong directory = ')),
                    (self.job['domain'], self.manager_text().replace('/dev/null', '/foreign/log')),
                    (self.job['domain'], self.manager_text() + 'properties = keepalive\n')):
                with self.subTest(domain=domain, text=text), \
                     patch.object(full.native, 'owned_helper_observation', return_value=observed), \
                     patch.object(full.native, 'launch_job', return_value=[(domain, text)]):
                    with self.assertRaises(RuntimeError):
                        full.owned_full_helper(self.root, bound, {})

    def test_settled_exact_job_absence_keeps_bound_custody_without_redispatch(self):
        observed = {'label': self.job['label'], 'settled': True, 'domains': []}
        with patch.object(full.sys, 'platform', 'darwin'), \
             patch.object(full.native, 'owned_helper_observation', return_value=observed), \
             patch.object(full.native, 'launch_job', return_value=[]):
            self.assertEqual(full.owned_full_helper(self.root, self.helper, {}), observed)
        self.spawn.assert_not_called()

    def test_unmodified_native_ownership_rejects_actual_argv_or_automatic_rerun(self):
        with patch.object(full.sys, 'platform', 'darwin'):
            bound = full.bind_helper_custody(self.root, self.helper)
            for text in (self.manager_text().replace('\n-I\n', '\n-foreign\n'),
                         self.manager_text().replace('runs = 1', 'runs = 2'),
                         self.manager_text().replace('program = ' + self.config['basePython'], 'program = /foreign/python')):
                with self.subTest(text=text), \
                     patch.object(full.native, 'launch_job', return_value=[(self.job['domain'], text)]):
                    with self.assertRaises(RuntimeError):
                        full.owned_full_helper(self.root, bound, {})

    def test_cleanup_boots_out_only_bound_settled_gui_job_then_confirms_absence(self):
        owner = full.load(self.root / 'owner.json')
        owner.update(definition=None, cleanupStarted=False, servicePIDs=[])
        full.save(self.root / 'owner.json', owner)
        present = True
        commands = []

        def read_job(label, env):
            return [(self.job['domain'], self.manager_text())] if present else []

        def command(*args, **kwargs):
            nonlocal present
            commands.append(args)
            self.assertEqual(args, ('launchctl', 'bootout', self.job['domain'] + '/' + self.job['label']))
            # Durable closed bytes/custody existed before the only manager input.
            saved = full.load(self.root / 'owner.json')['helpers'][0]
            self.assertEqual(saved['helperJob'], self.job)
            self.assertEqual(saved['savedDefinition']['sha256'], full.digest(self.plist))
            present = False

        with patch.object(full.sys, 'platform', 'darwin'), \
             patch.object(full.native, 'manager_environment', return_value={}), \
             patch.object(full.native, 'manager_capability'), \
             patch.object(full.native, 'launch_job', side_effect=read_job), \
             patch.object(full.native, 'paths', return_value=self.root / 'not-our-service'), \
             patch.object(full, 'execute', side_effect=command):
            full.cleanup(self.root, self.root)
        receipt = full.load(self.root / 'cleanup.json')
        self.assertTrue(receipt['passed'])
        self.assertEqual(len(commands), 1)
        self.assertTrue(receipt['helpers'][0]['absent'])
        self.assertTrue(self.plist.exists())  # private fixture retained for CI owner
        self.spawn.assert_not_called()

    def test_cleanup_changed_definition_refuses_bootout_and_preserves_custody(self):
        with patch.object(full.sys, 'platform', 'darwin'):
            full.bind_helper_custody(self.root, self.helper)
            owner = full.load(self.root / 'owner.json')
            owner.update(definition=None, cleanupStarted=False, servicePIDs=[])
            full.save(self.root / 'owner.json', owner)
            self.plist.write_bytes(self.raw + b'\n')
            with patch.object(full.native, 'manager_environment', return_value={}), \
                 patch.object(full.native, 'manager_capability'), patch.object(full, 'execute') as command:
                with self.assertRaises(ValueError):
                    full.cleanup(self.root, self.root)
                command.assert_not_called()
        self.assertFalse(full.load(self.root / 'cleanup.json')['passed'])
        self.assertTrue(self.plist.exists())

    def test_qualification_flag_propagates_and_default_run_stays_strict(self):
        observed = {'label': self.job['label'], 'settled': True, 'domains': []}
        with patch.object(full.sys, 'platform', 'darwin'), \
             patch.object(full.native, 'owned_helper_observation', return_value=observed) as native, \
             patch.object(full.native, 'launch_job', return_value=[]):
            bound = full.bind_helper_custody(self.root, self.helper)
            full.observe_helper(self.root, bound, {})
            native.assert_called_once_with(bound, {}, qualify=True)
            native.reset_mock()
            full.observe_helper(self.root, bound, {}, qualify=False)
            native.assert_called_once_with(bound, {}, qualify=False)

    def test_failed_multirun_qualification_still_allows_exact_settled_cleanup_only(self):
        text = self.manager_text().replace('runs = 1', 'runs = 2')
        with patch.object(full.sys, 'platform', 'darwin'), \
             patch.object(full.native, 'launch_job', return_value=[(self.job['domain'], text)]):
            with self.assertRaisesRegex(RuntimeError, 'automatically reran'):
                full.owned_full_helper(self.root, self.helper, {})
        self.operation.update(status='unknown', errorType='ValueError')
        self.write_inputs()
        original_receipt = full.digest(self.folder / 'operation.json')
        with patch.object(self, 'manager_text', return_value=text):
            self.test_cleanup_boots_out_only_bound_settled_gui_job_then_confirms_absence()
        receipt = full.load(self.root / 'cleanup.json')
        self.assertTrue(receipt['doesNotQualifyRepair'])
        self.assertEqual(receipt['scope'], 'owned-resource-cleanup-only')
        self.assertFalse(receipt['helpers'][0]['settled']['domains'][0]['oneShotQualified'])
        self.assertEqual(full.digest(self.folder / 'operation.json'), original_receipt)
        self.assertEqual(full.load(self.folder / 'operation.json')['status'], 'unknown')

    def test_missing_run_count_fails_run_qualification_but_not_exact_settled_cleanup(self):
        text = self.manager_text().replace('runs = 1\n', '')
        with patch.object(full.sys, 'platform', 'darwin'), \
             patch.object(full.native, 'launch_job', return_value=[(self.job['domain'], text)]):
            with self.assertRaisesRegex(RuntimeError, 'run count is unobserved'):
                full.owned_full_helper(self.root, self.helper, {})
        with patch.object(self, 'manager_text', return_value=text):
            self.test_cleanup_boots_out_only_bound_settled_gui_job_then_confirms_absence()
        self.assertFalse(full.load(self.root / 'cleanup.json')['helpers'][0]['settled']['domains'][0]['oneShotQualified'])

    def test_failed_loaded_policy_qualification_not_waived_by_cleanup_success(self):
        text = self.manager_text() + 'properties = keepalive\n'
        with patch.object(full.sys, 'platform', 'darwin'), \
             patch.object(full.native, 'launch_job', return_value=[(self.job['domain'], text)]):
            with self.assertRaisesRegex(RuntimeError, 'unqualified restart policy'):
                full.owned_full_helper(self.root, self.helper, {})
        # The closed private definition is still KeepAlive=false, manager is
        # positively stopped, children settled. Cleanup does not qualify the run.
        with patch.object(self, 'manager_text', return_value=text):
            self.test_cleanup_boots_out_only_bound_settled_gui_job_then_confirms_absence()
        self.assertTrue(full.load(self.root / 'cleanup.json')['doesNotQualifyRepair'])

    def test_cleanup_refuses_active_pidless_unknown_foreign_argv_or_domain(self):
        base = self.manager_text()
        for domain, text in (
                (self.job['domain'], base.replace('state = not running', 'state = running') + 'pid = 123\n'),
                (self.job['domain'], base.replace('state = not running', 'state = unknown')),
                (self.job['domain'], base.replace('state = not running\n', '')),
                (self.job['domain'], base.replace('\n-I\n', '\n-foreign\n')),
                ('gui/foreign', base),
                (self.job['domain'], base.replace(str(self.plist), '/foreign/helper.plist'))):
            full.save(self.root / 'owner.json', {'definition': None, 'helpers': [self.helper],
                      'servicePIDs': [], 'helperPIDs': [], 'cleanupStarted': False})
            with self.subTest(domain=domain, text=text), patch.object(full.sys, 'platform', 'darwin'), \
                 patch.object(full.native, 'manager_environment', return_value={}), \
                 patch.object(full.native, 'manager_capability'), \
                 patch.object(full.native, 'launch_job', return_value=[(domain, text)]), \
                 patch.object(full.native, 'pid_start', return_value='synthetic-active-child'), \
                 patch.object(full, 'execute') as command:
                with self.assertRaises(RuntimeError):
                    full.cleanup(self.root, self.root)
                command.assert_not_called()
            self.assertFalse(full.load(self.root / 'cleanup.json')['passed'])

    def test_cleanup_refuses_unsettled_child_even_with_exact_stopped_manager(self):
        full.save(self.folder / 'child-boundary.json', {'schema': 1, 'settled': False})
        owner = full.load(self.root / 'owner.json')
        owner.update(definition=None, servicePIDs=[], cleanupStarted=False)
        full.save(self.root / 'owner.json', owner)
        with patch.object(full.sys, 'platform', 'darwin'), \
             patch.object(full.native, 'manager_environment', return_value={}), \
             patch.object(full.native, 'manager_capability'), \
             patch.object(full.native, 'launch_job', return_value=[(self.job['domain'], self.manager_text())]), \
             patch.object(full, 'execute') as command:
            with self.assertRaisesRegex(RuntimeError, 'child settlement is unproven'):
                full.cleanup(self.root, self.root)
            command.assert_not_called()
        self.assertFalse(full.load(self.root / 'cleanup.json')['passed'])

    def test_cleanup_bootout_failure_is_not_retried_or_given_alias_fallback(self):
        owner = full.load(self.root / 'owner.json')
        owner.update(definition=None, servicePIDs=[], cleanupStarted=False)
        full.save(self.root / 'owner.json', owner)
        with patch.object(full.sys, 'platform', 'darwin'), \
             patch.object(full.native, 'manager_environment', return_value={}), \
             patch.object(full.native, 'manager_capability'), \
             patch.object(full.native, 'launch_job', return_value=[(self.job['domain'], self.manager_text())]), \
             patch.object(full, 'execute', side_effect=RuntimeError('bootout observation failed')) as command:
            with self.assertRaisesRegex(RuntimeError, 'bootout observation failed'):
                full.cleanup(self.root, self.root)
            command.assert_called_once_with('launchctl', 'bootout', self.job['domain'] + '/' + self.job['label'], env={})
        self.assertFalse(full.load(self.root / 'cleanup.json')['passed'])


if __name__ == '__main__':
    unittest.main()