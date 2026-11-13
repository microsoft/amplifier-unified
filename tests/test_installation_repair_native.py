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
from unittest.mock import Mock, patch
import zipfile

FIXTURES = Path(__file__).with_name('fixtures')
SPEC = importlib.util.spec_from_file_location('repair_native_fixture', FIXTURES / 'installation_repair_native.py')
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


class NativeRepairHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
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
        host = '12345678-1234-4234-8234-123456789abc'
        self.assertEqual(native.worker_command(fixture, folder, reconcile=True, host_instance=host)[-3:],
                         ['--reconcile', str(folder), host])
        with self.assertRaisesRegex(RuntimeError, 'exact current service UUID'):
            native.worker_command(fixture, folder, reconcile=True)
        with self.assertRaisesRegex(RuntimeError, 'one worker inspection'):
            native.worker_command(fixture, folder, verify=True, reconcile=True)

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
        self.assertEqual(set(native.CASES), {'happy', 'preflight', 'rollback', 'verify', 'data-lease',
                                           'namespace-lease', 'reconcile'})
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

    def test_wrong_tls_pin_never_reads_or_sends_token_or_plaintext_http(self):
        connection = Mock()
        connection.sock.getpeercert.return_value = b'foreign-leaf'
        config = {'home': '/must-not-read', 'port': 12345, 'tls': {'pin': '0' * 64}}
        with patch.object(native.http.client, 'HTTPSConnection', return_value=connection), \
                patch.object(native.http.client, 'HTTPConnection') as plaintext, \
                patch.object(native.Path, 'read_text') as read:
            with self.assertRaisesRegex(RuntimeError, 'TLS pin mismatch'):
                native.request(config, '/api/health')
        connection.connect.assert_called_once_with()
        connection.sock.getpeercert.assert_called_once_with(binary_form=True)
        connection.request.assert_not_called()
        connection.getresponse.assert_not_called()
        read.assert_not_called()
        plaintext.assert_not_called()
        connection.close.assert_called_once_with()

    def test_tls_connect_failure_does_not_read_token(self):
        connection = Mock()
        connection.connect.side_effect = OSError('unavailable')
        with patch.object(native.http.client, 'HTTPSConnection', return_value=connection), \
                patch.object(native.Path, 'read_text') as read:
            with self.assertRaises(OSError):
                native.request({'home': '/unread', 'port': 1, 'tls': {'pin': 'a' * 64}}, '/api/health')
        read.assert_not_called()
        connection.request.assert_not_called()
        connection.close.assert_called_once_with()

    def test_correct_tls_pin_precedes_token_read_and_authorization(self):
        order = []
        connection = Mock()
        connection.connect.side_effect = lambda: order.append('connect')
        connection.sock.getpeercert.side_effect = lambda **_kwargs: order.append('pin') or b'leaf'
        connection.request.side_effect = lambda *_args, **_kwargs: order.append('request')
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{"ok": true}'
        config = {'home': '/only-after-pin', 'port': 12345,
                  'tls': {'pin': hashlib.sha256(b'leaf').hexdigest()}}
        with patch.object(native.http.client, 'HTTPSConnection', return_value=connection) as https, \
                patch.object(native.http.client, 'HTTPConnection') as plaintext, \
                patch.object(native.Path, 'read_text', side_effect=lambda: order.append('token') or 'private-token'):
            self.assertEqual(native.request(config, '/api/health'), {'ok': True})
        self.assertEqual(order, ['connect', 'pin', 'token', 'request'])
        self.assertEqual(connection.request.call_args.kwargs['headers'], {'Authorization': 'Bearer private-token'})
        self.assertEqual(https.call_args.kwargs['context'].minimum_version, native.ssl.TLSVersion.TLSv1_2)
        plaintext.assert_not_called()

    def test_fixture_server_wraps_socket_with_server_tls_and_private_chain(self):
        app = native.module(FIXTURES / 'installation_repair_native_app.py')
        server, context = Mock(), Mock()
        raw_socket = server.socket
        config = {'port': 23456, 'tls': {'cert': '/private/cert.pem', 'key': '/private/key.pem'}}
        with patch.object(app.ssl, 'SSLContext', return_value=context) as factory, \
                patch.object(app, 'HTTPServer', return_value=server):
            self.assertIs(app.tls_server(config, object), server)
        factory.assert_called_once_with(app.ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain.assert_called_once_with('/private/cert.pem', '/private/key.pem')
        context.wrap_socket.assert_called_once_with(raw_socket, server_side=True)
        self.assertEqual(context.minimum_version, app.ssl.TLSVersion.TLSv1_2)

    def test_tls_server_wrap_failure_closes_listener(self):
        app = native.module(FIXTURES / 'installation_repair_native_app.py')
        server, context = Mock(), Mock()
        context.wrap_socket.side_effect = OSError('TLS setup failed')
        with patch.object(app.ssl, 'SSLContext', return_value=context), \
                patch.object(app, 'HTTPServer', return_value=server):
            with self.assertRaises(OSError):
                app.tls_server({'port': 1, 'tls': {'cert': 'cert', 'key': 'key'}}, object)
        server.server_close.assert_called_once_with()

    def test_ci_leaf_command_is_mocked_no_real_cert_generation(self):
        def generate(*args):
            Path(args[args.index('-out') + 1]).write_text('mock certificate')
            Path(args[args.index('-keyout') + 1]).write_text('mock private key')

        with patch.object(native.shutil, 'which', return_value='/mock/openssl'), \
                patch.object(native, 'execute', side_effect=generate) as execute, \
                patch.object(native.ssl, 'PEM_cert_to_DER_cert', return_value=b'mock DER'):
            tls = native.tls_leaf(self.root)
        argv = execute.call_args.args
        self.assertEqual(argv[:6], ('/mock/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes'))
        self.assertEqual(tls['pin'], hashlib.sha256(b'mock DER').hexdigest())
        if os.name == 'posix':
            self.assertEqual(Path(tls['key']).stat().st_mode & 0o777, 0o600)

    def test_operation_plan_uses_only_pinned_https_health(self):
        descriptor = self.archive()
        home = self.root / 'data'
        (home / 'installation-repair').mkdir(parents=True)
        config = {'home': str(home), 'toolRoot': '/tools', 'binRoot': '/bins', 'port': 12345,
                  'tls': {'pin': 'b' * 64}}
        fixture = {'artifact': descriptor, 'version': self.version, 'revision': self.revision,
                   'basePython': '/outside/python3.13', 'uv': '/outside/uv',
                   'executables': {key: {'resolved': '/outside/' + key, 'stat': [1, 2], 'sha256': 'c' * 64}
                                   for key in ('uv', 'basePython')}}
        with patch.object(native, 'execute') as execute:
            folder = native.new_operation(config, fixture, {})
        self.assertEqual(native.load(folder / 'plan.json')['health'], {
            'urls': ['https://127.0.0.1:12345/api/health'], 'pin': 'b' * 64})
        self.assertEqual(native.load(folder / 'plan.json')['recoverySchema'], 1)
        self.assertEqual(native.load(folder / 'plan.json')['executables'], fixture['executables'])
        self.assertEqual(native.load(folder / 'operation.json')['effectBoundary'], {
            'schema': 1, 'stopIntent': False, 'stopSettled': False, 'mutationIntent': False})
        self.assertFalse((home / 'installation-repair/admissions.json').exists())
        execute.assert_not_called()

    def test_manager_environment_platform_paths_do_not_probe_linux_on_macos(self):
        with patch.dict(native.os.environ, {'PATH': '/usr/bin', 'HOME': '/runner', 'SECRET': 'unpublished'}, clear=True), \
                patch.object(native.sys, 'platform', 'darwin'), patch.object(native, 'module') as module:
            self.assertEqual(native.manager_environment(), {'PATH': '/usr/bin', 'HOME': '/runner'})
        module.assert_not_called()
        reset = Mock()
        reset.user_service_environment.return_value = {'PATH': '/usr/bin', 'XDG_RUNTIME_DIR': '/run/user/123',
                                                     'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/123/bus'}
        with patch.object(native.sys, 'platform', 'linux'), patch.object(native, 'module', return_value=reset):
            self.assertIn('DBUS_SESSION_BUS_ADDRESS', native.manager_environment())
        reset.user_service_environment.assert_called_once_with()

    def test_prepare_refuses_and_cleanup_preserves_preexisting_definition(self):
        foreign = self.root / 'foreign.service'
        foreign.write_bytes(b'preexisting user unit')
        root, evidence = self.root / 'run', self.root / 'evidence'
        with patch.object(native, 'paths', return_value=foreign), \
                patch.object(native, 'manager_environment', return_value={}), \
                patch.object(native, 'manager_capability'), patch.object(native, 'execute') as execute, \
                patch.object(native, 'tls_leaf') as tls:
            with self.assertRaisesRegex(RuntimeError, 'preexisting service definition'):
                native.prepare(root, evidence)
        with patch.object(native, 'manager_environment') as manager, patch.object(native, 'execute') as effects:
            native.cleanup(root, evidence)
        self.assertEqual(foreign.read_bytes(), b'preexisting user unit')
        manager.assert_not_called()
        execute.assert_not_called()
        effects.assert_not_called()
        tls.assert_not_called()

    def test_cleanup_refuses_foreign_settled_helper_without_manager_mutation(self):
        root, evidence = self.root / 'run', self.root / 'evidence'
        root.mkdir()
        evidence.mkdir()
        identity = 'd' * 32
        helper = {'label': 'amplifier-installation-repair-' + identity,
                  'folder': str(root / 'data/installation-repair' / identity), 'basePython': '/outside/python'}
        native.save(root / 'owner.json', {'definition': None, 'helpers': [helper], 'processes': [], 'cases': []})
        value = {'LoadState': 'loaded', 'MainPID': '0', 'ActiveState': 'inactive', 'Restart': 'no',
                 'NRestarts': '0', 'Type': 'exec', 'WorkingDirectory': helper['folder'],
                 'ExecStart': '{ path=/foreign ; argv[]=/foreign ; ignore_errors=no ; }'}
        with patch.object(native.sys, 'platform', 'linux'), \
                patch.object(native, 'manager_environment', return_value={}), \
                patch.object(native, 'manager_capability'), \
                patch.object(native, 'properties', return_value=value), patch.object(native, 'execute') as execute:
            with self.assertRaisesRegex(RuntimeError, 'foreign or unidentifiable'):
                native.cleanup(root, evidence)
        execute.assert_not_called()
        self.assertFalse(native.load(evidence / 'cleanup.json')['passed'])

    def test_cleanup_does_not_kill_or_remove_unsettled_unknown_job(self):
        root, evidence = self.root / 'run', self.root / 'evidence'
        root.mkdir()
        evidence.mkdir()
        helper = {'label': 'saved', 'folder': '/saved', 'basePython': '/outside/python'}
        native.save(root / 'owner.json', {'definition': None, 'helpers': [helper], 'processes': [], 'cases': []})
        with patch.object(native, 'manager_environment', return_value={}), \
                patch.object(native, 'manager_capability'), \
                patch.object(native, 'owned_helper_observation', return_value={'settled': False}), \
                patch.object(native.time, 'monotonic', side_effect=[0, 121]), \
                patch.object(native, 'execute') as execute:
            with self.assertRaisesRegex(RuntimeError, 'cleanup helper settlement'):
                native.cleanup(root, evidence)
        execute.assert_not_called()
        self.assertEqual(native.load(root / 'owner.json')['helpers'], [helper])
        self.assertFalse(native.load(evidence / 'cleanup.json')['passed'])

    def test_changed_owned_definition_is_preserved_before_control(self):
        definition = self.root / 'owned.service'
        definition.write_bytes(b'original')
        info = definition.stat()
        saved = {'path': str(definition), 'sha256': native.digest(definition), 'stat': [info.st_dev, info.st_ino]}
        definition.write_bytes(b'foreign')
        with patch.object(native, 'paths', return_value=definition), patch.object(native, 'properties') as properties:
            with self.assertRaisesRegex(RuntimeError, 'owned definition changed'):
                native.owned_definition(saved, {}, [])
        properties.assert_not_called()
        self.assertEqual(definition.read_bytes(), b'foreign')

    def test_source_index_covers_all_test_inputs_and_reports_missing_workflow(self):
        completed = Mock()
        completed.stdout = self.revision
        clean = Mock()
        clean.stdout = ''
        with patch.object(native, 'execute', side_effect=[completed, clean]), \
                patch.object(native, 'WORKFLOW', '.github/workflows/absent-fixture-workflow.yml'), \
                patch.dict(native.os.environ, {}, clear=True):
            index = native.source_index()
        self.assertTrue(set(native.TEST_PATHS) <= set(index['sha256']))
        self.assertFalse(index['workflow']['present'])
        self.assertEqual(index['workflowCommit'], {})

    def test_macos_foreign_settled_helper_is_not_adopted(self):
        helper = self.mac_helper()
        text = 'program = /foreign\narguments = {\n /foreign\n}\nruns = 1\n'
        with patch.object(native.sys, 'platform', 'darwin'), \
                patch.object(native, 'launch_job', return_value=[('gui/123', text)]), \
                patch.object(native, 'execute') as execute:
            with self.assertRaisesRegex(RuntimeError, 'foreign or unidentifiable'):
                native.owned_helper_observation(helper, {})
        execute.assert_not_called()

    def test_failed_launchd_lookup_is_not_absence_when_domain_lists_label(self):
        parent, failed = Mock(), Mock()
        parent.returncode, parent.stdout = 0, 'services = {\n  0  example.saved.label\n}\n'
        failed.returncode = 1
        with patch.object(native, 'execute', side_effect=[parent, failed]):
            with self.assertRaisesRegex(RuntimeError, 'registration state is unknown'):
                native.launch_job('example.saved.label', {})

    def test_saved_child_cleanup_reaps_without_signalling(self):
        with patch.object(native.os, 'waitpid', return_value=(123, 0)) as reap, \
                patch.object(native, 'pid_start', return_value=''), \
                patch.object(native.os, 'kill', create=True) as kill:
            self.assertTrue(native.owned_child_exited(123, {}))
        reap.assert_called_once_with(123, native.os.WNOHANG)
        kill.assert_not_called()

    def test_fixture_health_reads_single_atomic_admission_not_legacy_records(self):
        app = native.module(FIXTURES / 'installation_repair_native_app.py')
        namespace = self.root / 'installation-repair'
        namespace.mkdir()
        self.assertIsNone(app.current_admission(self.root))
        operation, preview = 'a' * 32, 'b' * 32
        native.save(namespace / 'admissions.json', {'schema': 1, 'current': operation,
                                                   'previews': {preview: operation}})
        self.assertEqual(app.current_admission(self.root), operation)
        native.save(namespace / 'admissions.json', {'schema': 1, 'current': operation, 'previews': {}})
        with self.assertRaises(AssertionError):
            app.current_admission(self.root)
        source = (FIXTURES / 'installation_repair_native_app.py').read_text()
        self.assertNotIn('latest.json', source)
        self.assertNotIn('confirmed-', source)

    def test_frozen_case_map_distinguishes_refusal_unknown_and_no_effect_close(self):
        self.assertEqual(native.EXPECTATIONS['data-lease'][:4], ('unknown', True, True, False))
        self.assertEqual(native.EXPECTATIONS['namespace-lease'], ('failed', False, False, False, 1, 0, 0, 0))
        self.assertEqual(native.EXPECTATIONS['reconcile'], ('failed', False, False, False, 1, 0, 0, 0))
        self.assertEqual(native.EXPECTATIONS['verify'][-2:], (1, 2))
        for values in native.EXPECTATIONS.values():
            self.assertEqual(len(values), 8)

    def test_executable_selection_delegates_to_production_guard_without_chmod(self):
        tree = ast.parse((FIXTURES / 'installation_repair_native.py').read_text())
        selection = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                         and node.name == 'executable_selection')
        calls = [node for node in ast.walk(selection) if isinstance(node, ast.Call)]
        identities = [node for node in calls if isinstance(node.func, ast.Attribute)
                      and node.func.attr == 'executable_identity']
        self.assertEqual(len(identities), 1)  # loop covers uv and basePython
        self.assertFalse(any(isinstance(node.func, ast.Attribute) and node.func.attr == 'chmod' for node in calls))
        app_tree = ast.parse((FIXTURES / 'installation_repair_native_app.py').read_text())
        dispatch = next(node for node in app_tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'dispatch')
        self.assertTrue(any(isinstance(node, ast.Assert) for node in dispatch.body))

    def test_cleanup_requires_child_settlement_before_manager_mutation(self):
        root, evidence = self.root / 'run', self.root / 'evidence'
        root.mkdir()
        evidence.mkdir()
        helper = {'label': 'saved', 'folder': '/saved', 'basePython': '/outside/python'}
        native.save(root / 'owner.json', {'definition': None, 'helpers': [helper], 'processes': [], 'cases': []})
        with patch.object(native, 'manager_environment', return_value={}), \
                patch.object(native, 'manager_capability'), \
                patch.object(native, 'owned_helper_observation', return_value={'settled': True}), \
                patch.object(native, 'child_settlement', side_effect=RuntimeError('child settlement unproven')), \
                patch.object(native, 'execute') as execute:
            with self.assertRaisesRegex(RuntimeError, 'child settlement unproven'):
                native.cleanup(root, evidence)
        execute.assert_not_called()
        self.assertEqual(native.load(root / 'owner.json')['helpers'], [helper])
        self.assertFalse(native.load(evidence / 'cleanup.json')['passed'])

    def test_no_replay_boundary_witness_detects_identical_content_replacement(self):
        self.assertIsNone(native.child_boundary_identity(self.root))
        boundary = self.root / 'child-boundary.json'
        native.save(boundary, {'schema': 1, 'settled': True})
        before = native.child_boundary_identity(self.root)
        native.save(boundary, {'schema': 1, 'settled': True})
        self.assertNotEqual(native.child_boundary_identity(self.root), before)
        self.assertEqual(boundary.read_bytes(), before[-1])

    def mac_helper(self):
        """Test input only. Runtime fixture MUST use the production preparer."""
        identity = 'd' * 32
        folder = self.root / 'data/installation-repair' / identity
        folder.mkdir(parents=True, mode=0o700)
        helper = {'label': 'com.microsoft.amplifier-unified.repair.' + identity,
                  'folder': str(folder), 'basePython': '/outside/python3.13'}
        (folder / 'installation_repair_worker.py').write_bytes(
            (native.SOURCE / 'amplifier_web/installation_repair_worker.py').read_bytes())
        path = folder / 'helper.plist'
        payload = {'Label': helper['label'], 'ProgramArguments': native.worker_command(helper, folder),
                   'WorkingDirectory': str(folder), 'RunAtLoad': True, 'KeepAlive': False,
                   'ProcessType': 'Background', 'StandardOutPath': os.devnull,
                   'StandardErrorPath': os.devnull, 'Umask': 0o077}
        path.write_bytes(plistlib.dumps(payload, sort_keys=True))
        path.chmod(0o600)
        job = {'kind': 'launchd-user-job', 'label': helper['label'], 'domain': f'gui/{os.getuid()}',
               'plistPath': str(path), 'plistSHA256': native.digest(path),
               'cleanupState': 'retained-for-explicit-review', 'cleanupOwner': 'installation-owner',
               'detail': 'test metadata'}
        native.save(folder / 'plan.json', {'operationId': identity, 'uid': os.getuid(),
                                           'basePython': helper['basePython'], 'helperJob': job})
        native.save(folder / 'operation.json', {'operationId': identity, 'helperJob': job})
        return helper

    def mac_job_print(self, helper, *, runs=1, state='not running', pid=None, path=True):
        folder = Path(helper['folder'])
        return ('program = ' + helper['basePython'] + '\narguments = {\n'
                + ''.join('  ' + arg + '\n' for arg in native.worker_command(helper, folder)) + '}\n'
                + ('path = ' + str(folder / 'helper.plist') + '\n' if path else '')
                + f'runs = {runs}\nstate = {state}\n' + (f'pid = {pid}\n' if pid else ''))

    def test_private_launchd_plist_has_closed_keys_and_exact_receipt_digest(self):
        helper = self.mac_helper()
        job = native.checked_helper_plist(helper)
        self.assertEqual(job['kind'], 'launchd-user-job')
        self.assertEqual(job['domain'], f'gui/{os.getuid()}')
        self.assertEqual(job['plistSHA256'], native.digest(Path(helper['folder']) / 'helper.plist'))

    def test_changed_private_plist_is_refused_before_manager_observation(self):
        helper = self.mac_helper()
        path = Path(helper['folder']) / 'helper.plist'
        value = plistlib.loads(path.read_bytes())
        value['KeepAlive'] = True
        path.write_bytes(plistlib.dumps(value, sort_keys=True))
        with patch.object(native.sys, 'platform', 'darwin'), patch.object(native, 'launch_job') as manager:
            with self.assertRaisesRegex(RuntimeError, 'helper definition changed'):
                native.owned_helper_observation(helper, {}, qualify=False)
        manager.assert_not_called()
        self.assertTrue(path.exists())

    def test_rehashed_unsafe_trigger_is_not_adopted_as_new_helper_ownership(self):
        helper = self.mac_helper()
        folder = Path(helper['folder'])
        value = plistlib.loads((folder / 'helper.plist').read_bytes())
        value['StartInterval'] = 10
        (folder / 'helper.plist').write_bytes(plistlib.dumps(value, sort_keys=True))
        for name in ('plan.json', 'operation.json'):
            receipt = native.load(folder / name)
            receipt['helperJob']['plistSHA256'] = native.digest(folder / 'helper.plist')
            native.save(folder / name, receipt)
        with self.assertRaisesRegex(RuntimeError, 'unsafe keys'):
            native.checked_helper_plist(helper)

    def test_foreign_loaded_definition_path_is_refused_even_when_program_matches(self):
        helper = self.mac_helper()
        text = self.mac_job_print(helper).replace(str(Path(helper['folder']) / 'helper.plist'), '/foreign/helper.plist')
        with patch.object(native.sys, 'platform', 'darwin'), \
                patch.object(native, 'launch_job', return_value=[(f'gui/{os.getuid()}', text)]):
            with self.assertRaisesRegex(RuntimeError, 'foreign or unidentifiable'):
                native.owned_helper_observation(helper, {}, qualify=False)

    def test_foreign_loaded_argv_is_refused_with_matching_program_and_private_plist(self):
        helper = self.mac_helper()
        text = self.mac_job_print(helper).replace('  -S\n', '  -E\n')
        with patch.object(native.sys, 'platform', 'darwin'), \
                patch.object(native, 'launch_job', return_value=[(f'gui/{os.getuid()}', text)]), \
                patch.object(native, 'execute') as execute:
            with self.assertRaisesRegex(RuntimeError, 'foreign or unidentifiable'):
                native.retire_helper(helper, {})
        execute.assert_not_called()

    def test_plan_and_operation_must_share_identical_helper_custody(self):
        helper = self.mac_helper()
        path = Path(helper['folder']) / 'operation.json'
        operation = native.load(path)
        operation['helperJob']['domain'] = 'gui/foreign'
        native.save(path, operation)
        with self.assertRaisesRegex(RuntimeError, 'custody is foreign or unidentifiable'):
            native.checked_helper_plist(helper)

    def test_loaded_path_optional_observation_does_not_replace_private_plist_proof(self):
        helper = self.mac_helper()
        with patch.object(native.sys, 'platform', 'darwin'), \
                patch.object(native, 'launch_job', return_value=[(f'gui/{os.getuid()}', self.mac_job_print(helper, path=False))]):
            value = native.owned_helper_observation(helper, {})
        self.assertTrue(value['settled'])
        self.assertFalse(value['domains'][0]['loadedDefinitionPathObserved'])
        self.assertTrue(value['helperJob']['plistSHA256'])

    def test_multirun_is_failed_qualification_but_exact_settled_job_can_be_cleaned(self):
        helper = self.mac_helper()
        folder = Path(helper['folder'])
        domain = f'gui/{os.getuid()}'
        rows = [(domain, self.mac_job_print(helper, runs=2))]
        with patch.object(native.sys, 'platform', 'darwin'), patch.object(native, 'launch_job', return_value=rows):
            with self.assertRaisesRegex(RuntimeError, 'one-shot helper automatically reran'):
                native.owned_helper_observation(helper, {})
        # Qualification remains failed; cleanup only has narrowly scoped effect.
        # No child ever spawned in this unit-test fixture; do not pretend to run
        # a worker to manufacture a settlement receipt.
        worker_source = (folder / 'installation_repair_worker.py').read_bytes()
        with patch.object(native.sys, 'platform', 'darwin'), \
                patch.object(native, 'launch_job', side_effect=[rows, rows, []]), \
                patch.object(native, 'child_settlement', return_value={'spawnIntentRecorded': False}), \
                patch.object(native, 'execute') as execute:
            value = native.retire_helper(helper, {})
        self.assertTrue(value['absent'])
        self.assertFalse(value['settled']['domains'][0]['oneShotQualified'])
        self.assertEqual(value['settled']['domains'][0]['runs'], ['2'])
        execute.assert_called_once_with('launchctl', 'bootout', domain + '/' + helper['label'], env={})
        self.assertEqual((folder / 'installation_repair_worker.py').read_bytes(), worker_source)

    def test_pidless_unknown_state_cannot_authorize_bootout(self):
        helper = self.mac_helper()
        rows = [(f'gui/{os.getuid()}', self.mac_job_print(helper, state='unknown'))]
        with patch.object(native.sys, 'platform', 'darwin'), patch.object(native, 'launch_job', return_value=rows), \
                patch.object(native.time, 'monotonic', side_effect=[0, 121]), \
                patch.object(native, 'execute') as execute:
            with self.assertRaisesRegex(RuntimeError, 'cleanup helper settlement'):
                native.retire_helper(helper, {})
        execute.assert_not_called()
        self.assertTrue((Path(helper['folder']) / 'helper.plist').exists())

    def test_active_multirun_job_still_cannot_be_cleaned(self):
        helper = self.mac_helper()
        rows = [(f'gui/{os.getuid()}', self.mac_job_print(helper, runs=2, state='running', pid=4321))]
        with patch.object(native.sys, 'platform', 'darwin'), patch.object(native, 'launch_job', return_value=rows), \
                patch.object(native.time, 'monotonic', side_effect=[0, 121]), \
                patch.object(native, 'execute') as execute:
            with self.assertRaisesRegex(RuntimeError, 'cleanup helper settlement'):
                native.retire_helper(helper, {})
        execute.assert_not_called()

    def test_production_preparer_call_precedes_admission_not_custom_fixture_payload(self):
        tree = ast.parse((FIXTURES / 'installation_repair_native_app.py').read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'dispatch')
        assignment = next(node for node in ast.walk(function) if isinstance(node, ast.Assign)
                          and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                          and node.value.func.id == 'prepare')
        admissions = next(node for node in ast.walk(function) if isinstance(node, ast.Call)
                          and isinstance(node.func, ast.Attribute) and node.func.attr == 'write'
                          and any(isinstance(part, ast.Constant) and part.value == 'installation-repair/admissions.json'
                                  for part in ast.walk(node)))
        self.assertLess(assignment.lineno, admissions.lineno)
        source = ast.unparse(function)
        self.assertIn("'_prepare_launchd_job'", source)
        self.assertNotIn('plistlib.dumps', source)
        self.assertEqual(len(assignment.targets), 2)  # identical plan and operation helperJob

    def test_timeout_publishes_actual_unknown_state_before_predicate_without_relaxing(self):
        helper = self.mac_helper()
        rows = [(f'gui/{os.getuid()}', self.mac_job_print(helper, state='waiting for something'))]
        with patch.object(native.sys, 'platform', 'darwin'), patch.object(native, 'launch_job', return_value=rows), \
                patch.object(native.time, 'monotonic', side_effect=[0, 121]), \
                patch.object(native, 'execute') as execute:
            with self.assertRaisesRegex(RuntimeError, 'cleanup helper settlement'):
                native.retire_helper(helper, {}, observer=lambda item, env, **kwargs:
                    native.recorded_helper_observation(item, env, evidence=self.root, phase='cleanup', **kwargs))
        record = native.load(self.root / 'helper-observation.json')
        self.assertTrue(record['observationOnly'])
        self.assertFalse(record['observation']['settled'])
        self.assertEqual(record['observation']['domains'][0]['states'], ['waiting for something'])
        self.assertEqual(record['observation']['domains'][0]['runs'], [1])
        self.assertNotIn('passed', record)
        execute.assert_not_called()

    def test_launchd_job_state_excludes_nested_active_coalitions(self):
        helper = self.mac_helper()
        text = self.mac_job_print(helper) + 'resource coalition = {\n  state = active\n}\njetsam coalition = {\n  state = active\n}\n'
        # Mirror launchctl's real one-tab job fields and deeper nested fields.
        text = '\n'.join('\t' + line for line in text.splitlines()) + '\n'
        self.assertEqual(native.launchd_job_states(text), ['not running'])
        with patch.object(native.sys, 'platform', 'darwin'), \
                patch.object(native, 'launch_job', return_value=[(f'gui/{os.getuid()}', text)]):
            observed = native.owned_helper_observation(helper, {})
        self.assertTrue(observed['settled'])
        self.assertEqual(observed['domains'][0]['runs'], ['1'])

    def test_launchd_live_or_unknown_job_state_is_not_masked_by_nested_exit(self):
        helper = self.mac_helper()
        for state in ('running', 'waiting for something'):
            text = self.mac_job_print(helper, state=state) + 'resource coalition = {\n  state = not running\n}\n'
            with self.subTest(state=state), patch.object(native.sys, 'platform', 'darwin'), \
                    patch.object(native, 'launch_job', return_value=[(f'gui/{os.getuid()}', text)]):
                self.assertFalse(native.owned_helper_observation(helper, {})['settled'])
        with self.assertRaisesRegex(RuntimeError, 'unique program-level scope'):
            native.launchd_job_states('state = not running\n')

    def test_foreign_argv_failure_still_saves_parsed_state_but_not_foreign_argv(self):
        helper = self.mac_helper()
        text = self.mac_job_print(helper).replace('  -S\n', '  SECRET-TOKEN-DO-NOT-STORE\n')
        with patch.object(native.sys, 'platform', 'darwin'), \
                patch.object(native, 'launch_job', return_value=[(f'gui/{os.getuid()}', text)]):
            with self.assertRaisesRegex(RuntimeError, 'foreign or unidentifiable'):
                native.recorded_helper_observation(helper, {}, evidence=self.root, phase='run')
        raw = (self.root / 'helper-observation-run.json').read_text()
        record = json.loads(raw)
        self.assertEqual(record['observationErrorType'], 'RuntimeError')
        self.assertEqual(record['observation']['domains'][0]['states'], ['not running'])
        self.assertNotIn('SECRET-TOKEN', raw)
        self.assertNotIn(helper['basePython'], raw)
        self.assertNotIn('plistPath', raw)

    def test_missing_private_proof_records_unavailable_without_guessing_state(self):
        helper = self.mac_helper()
        (Path(helper['folder']) / 'helper.plist').unlink()
        with patch.object(native.sys, 'platform', 'darwin'), patch.object(native, 'launch_job') as manager:
            with self.assertRaises(FileNotFoundError):
                native.recorded_helper_observation(helper, {}, evidence=self.root, phase='cleanup', qualify=False)
        record = native.load(self.root / 'helper-observation-cleanup.json')
        self.assertFalse(record['observationAvailable'])
        self.assertEqual(record['observationErrorType'], 'FileNotFoundError')
        self.assertNotIn('observation', record)
        manager.assert_not_called()

    def test_observation_projection_cannot_serialize_raw_manager_secrets_or_environment(self):
        value = {'settled': False, 'ExecStart': 'SECRET-ARGV', 'rawPrint': 'SECRET-PRINT',
                 'environment': {'TOKEN': 'SECRET-ENV'}, 'plist': 'SECRET-PLIST', 'helperJob': {
                     'plistPath': '/private/SECRET-PATH', 'detail': 'SECRET-DETAIL', 'plistSHA256': 'a' * 64},
                 'domains': [{'domain': 'gui/123', 'states': ['waiting', 'bad=SECRET', 'z' * 129, 'bad\nstate'],
                              'pids': ['123', True, 'SECRET'], 'runs': ['2', '99999999999'],
                              'raw': 'SECRET-RAW', 'loadedDefinitionPathObserved': True}]}
        safe = native.safe_helper_observation(value)
        raw = json.dumps(safe)
        self.assertNotIn('SECRET', raw)
        self.assertEqual(safe['domains'][0]['states'], ['waiting'])
        self.assertEqual(safe['domains'][0]['pids'], [123])
        self.assertEqual(safe['domains'][0]['runs'], [2])
        self.assertEqual(safe['plistSHA256'], 'a' * 64)
        self.assertEqual(set(safe), {'settled', 'domains', 'plistSHA256'})

    def test_multirun_error_published_before_strict_qualification_failure(self):
        helper = self.mac_helper()
        rows = [(f'gui/{os.getuid()}', self.mac_job_print(helper, runs=3))]
        with patch.object(native.sys, 'platform', 'darwin'), patch.object(native, 'launch_job', return_value=rows):
            with self.assertRaisesRegex(RuntimeError, 'automatically reran'):
                native.recorded_helper_observation(helper, {}, evidence=self.root, phase='run')
        value = native.load(self.root / 'helper-observation-run.json')
        self.assertEqual(value['observation']['domains'][0]['runs'], [3])
        self.assertFalse(value['observation']['domains'][0]['oneShotQualified'])
        self.assertEqual(value['observationErrorType'], 'RuntimeError')

    def test_original_case_failure_saved_before_cleanup_error_without_private_exception(self):
        fixture = {'sourceIndex': {}, 'uv': '/private/uv', 'basePython': '/private/python', 'executables': {}}
        native.save(self.root / 'fixture.json', fixture)
        with patch.object(native, 'source_index', return_value={}), \
                patch.object(native, 'executable_selection', return_value={}), \
                patch.object(native, 'manager_environment', return_value={}), \
                patch.object(native, 'run_case', side_effect=ValueError('SECRET original private input')), \
                patch.object(native, 'cleanup', side_effect=RuntimeError('SECRET cleanup input')):
            with self.assertRaisesRegex(RuntimeError, 'cleanup input'):
                native.run(self.root, self.root)
        value = native.load(self.root / 'case-failure.json')
        self.assertEqual(value['errorType'], 'ValueError')
        self.assertFalse(value['passed'])
        self.assertNotIn('SECRET', (self.root / 'case-failure.json').read_text())
        self.assertFalse((self.root / 'result.json').exists())


if __name__ == '__main__':
    unittest.main()
