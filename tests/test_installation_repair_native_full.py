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
import ssl
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
                 'helpers': [{'label': 'owned-helper'}], 'servicePIDs': [], 'helperPIDs': [],
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
        self.assertEqual(rows[-1]['label'], mac)

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


if __name__ == '__main__':
    unittest.main()