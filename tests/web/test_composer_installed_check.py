"""Pure wrapper safety checks: synthetic ZIPs/files, no build/install/browser."""
import ast
import base64
import copy
import csv
import importlib.util
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import installed_candidate_wheels as wheels

SPEC = importlib.util.spec_from_file_location('composer_installed_check', HERE / 'composer-installed-check.py')
wrapper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wrapper)


def encoded(data):
    return 'sha256=' + base64.urlsafe_b64encode(__import__('hashlib').sha256(data).digest()).rstrip(b'=').decode()


def record_bytes(payload, record):
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerows((name, encoded(data), str(len(data))) for name, data in sorted(payload.items()) if name != record)
    writer.writerow((record, '', ''))
    return stream.getvalue().encode()


def synthetic_wheel(root, distribution, *, extra=None, bad_record=False, symlink=False):
    prefix = wheels.OWNERS[distribution]
    dist_info = distribution.replace('-', '_') + '-1.0.dist-info'
    record = dist_info + '/RECORD'
    payload = {prefix + '/__init__.py': b"raise RuntimeError('validation must not execute me')\n",
               dist_info + '/METADATA': ('Metadata-Version: 2.1\nName: ' + distribution + '\nVersion: 1.0\n').encode(),
               dist_info + '/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
    payload.update(extra or {})
    payload[record] = record_bytes(payload, record)
    if bad_record:
        payload[prefix + '/__init__.py'] += b'# unrecorded change\n'
    path = root / (distribution.replace('-', '_') + '-1.0-py3-none-any.whl')
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in payload.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16 if symlink else (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data)
    selection = {'distribution': distribution, 'file': path.name,
                 'sha256': wheels.sha(path.read_bytes()), 'sourceCommit': 'a' * 40,
                 'keyModules': {prefix: {'path': prefix + '/__init__.py',
                                        'sha256': wheels.sha(payload[prefix + '/__init__.py'])}}}
    return path, selection, payload


class CandidateWheels(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.selections = [synthetic_wheel(self.root, name)[1] for name in wheels.OWNERS]
        self.path = self.root / 'candidates.json'
        self.save()

    def save(self, value=None):
        self.path.write_text(json.dumps(value or {'schemaVersion': 1, 'wheels': self.selections}))
        return self.path

    def test_full_wheel_record_payload_and_key_modules_without_import(self):
        result = wheels.load_candidates(self.path)
        self.assertEqual(len(result['candidates']), 2)
        self.assertEqual(result['manifestSha256'], wheels.sha(self.path.read_bytes()))
        self.assertIn('amplifier_foundation/__init__.py', result['candidates'][0]['files'])

    def test_manifest_schema_and_selection_controls(self):
        for mutation in ('version', 'count', 'extra', 'duplicate', 'source', 'distribution'):
            with self.subTest(mutation=mutation):
                value = {'schemaVersion': 1, 'wheels': copy.deepcopy(self.selections)}
                if mutation == 'version':
                    value['schemaVersion'] = True
                elif mutation == 'count':
                    value['wheels'].pop()
                elif mutation == 'extra':
                    value['unused'] = True
                elif mutation == 'duplicate':
                    value['wheels'][1] = value['wheels'][0]
                elif mutation == 'source':
                    value['wheels'][0]['sourceCommit'] = 'main'
                else:
                    value['wheels'][0]['distribution'] = 'unrelated'
                with self.assertRaises(ValueError):
                    wheels.load_candidates(self.save(value))

    def test_manifest_bound(self):
        self.path.write_text(' ' * (wheels.MAX_MANIFEST + 1))
        with self.assertRaisesRegex(ValueError, 'bound'):
            wheels.load_candidates(self.path)

    def test_manifest_symlink_denied(self):
        link = self.root / 'alias.json'
        link.symlink_to(self.path)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            wheels.load_candidates(link)

    def test_wheel_path_escape_and_symlink_denied(self):
        for filename in ('../escape.whl', '/absolute.whl', 'x/../escape.whl'):
            with self.subTest(filename=filename):
                rows = copy.deepcopy(self.selections)
                rows[0]['file'] = filename
                with self.assertRaises(ValueError):
                    wheels.load_candidates(self.save({'schemaVersion': 1, 'wheels': rows}))
        target = self.root / self.selections[0]['file']
        alias = self.root / 'alias.whl'
        alias.symlink_to(target)
        self.selections[0]['file'] = alias.name
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            wheels.load_candidates(self.save())

    def test_wheel_sha_mismatch(self):
        self.selections[0]['sha256'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'wheel SHA'):
            wheels.load_candidates(self.save())

    def test_key_module_origin_and_hash_controls(self):
        prefix = 'amplifier_foundation'
        for detail in ({prefix: {'path': prefix + '/__init__.py', 'sha256': '0' * 64}},
                       {'outside': {'path': 'outside.py', 'sha256': '0' * 64}},
                       {prefix: {'path': prefix + '/other.py', 'sha256': '0' * 64}}):
            with self.subTest(detail=detail):
                self.selections[0]['keyModules'] = detail
                with self.assertRaises(ValueError):
                    wheels.load_candidates(self.save())

    def test_bad_record_rejected_even_with_matching_outer_wheel_hash(self):
        path, row, _ = synthetic_wheel(self.root, 'amplifier-foundation', bad_record=True)
        with self.assertRaisesRegex(ValueError, 'RECORD hash'):
            wheels.wheel_identity(path, row)

    def test_console_scripts_refused_before_installer_can_generate_escape_records(self):
        info = 'amplifier_foundation-1.0.dist-info/entry_points.txt'
        path, row, _ = synthetic_wheel(self.root, 'amplifier-foundation',
                                      extra={info: b'[console_scripts]\ncommand = amplifier_foundation:main\n'})
        with self.assertRaisesRegex(ValueError, 'no installation was attempted'):
            wheels.wheel_identity(path, row)

    def test_archive_path_link_and_pth_controls(self):
        for name in ('../escape', '/absolute', 'amplifier_foundation/x.pth', 'other.py'):
            with self.subTest(name=name):
                path, row, _ = synthetic_wheel(self.root, 'amplifier-foundation', extra={name: b'x'})
                with self.assertRaises(ValueError):
                    wheels.wheel_identity(path, row)
        path, row, _ = synthetic_wheel(self.root, 'amplifier-foundation', symlink=True)
        with self.assertRaisesRegex(ValueError, 'Nonregular'):
            wheels.wheel_identity(path, row)

    def install_synthetic_files(self):
        path, row, payload = synthetic_wheel(self.root, 'amplifier-foundation')
        identity = wheels.wheel_identity(path, row)
        site = self.root / 'synthetic-site'
        for name, data in payload.items():
            target = site / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return site, identity

    def test_installed_all_files_record_pass(self):
        site, identity = self.install_synthetic_files()
        result = wheels.installed_files(site, identity)
        self.assertEqual(result['payloadFiles'], len(identity['files']))

    def test_uv_metadata_allowed_only_with_full_record_verification(self):
        site, identity = self.install_synthetic_files()
        record = site / identity['record']
        payload = {name: (site / name).read_bytes() for name in identity['files']}
        for name in ('uv_cache.json', 'uv_build.json', 'INSTALLER', 'REQUESTED', 'direct_url.json'):
            path = identity['distInfo'] + '/' + name
            payload[path] = b'{}'
            (site / path).write_bytes(payload[path])
        record.write_bytes(record_bytes(payload, identity['record']))
        wheels.installed_files(site, identity)
        (site / identity['distInfo'] / 'uv_cache.json').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'RECORD hash'):
            wheels.installed_files(site, identity)

    def test_installed_hash_change(self):
        site, identity = self.install_synthetic_files()
        (site / 'amplifier_foundation/__init__.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'payload hash'):
            wheels.installed_files(site, identity)

    def test_unrecorded_file_or_symlink(self):
        site, identity = self.install_synthetic_files()
        extra = site / 'amplifier_foundation/unexpected.py'
        extra.write_text('unrecorded')
        with self.assertRaisesRegex(ValueError, 'Unrecorded'):
            wheels.installed_files(site, identity)
        extra.unlink()
        extra.symlink_to(site / 'amplifier_foundation/__init__.py')
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            wheels.installed_files(site, identity)

    def test_installed_record_escape(self):
        site, identity = self.install_synthetic_files()
        with (site / identity['record']).open('a') as stream:
            stream.write('../escape,,\n')
        with self.assertRaisesRegex(ValueError, 'escape'):
            wheels.installed_files(site, identity)

    def test_candidate_probe_rejects_prepared_fallback_before_import(self):
        plan = self.root / 'probe.json'
        plan.write_text(json.dumps({'manifestSha256': 'a' * 64, 'candidates': [
            {'distribution': 'amplifier-foundation'}]}))
        fake_dist = unittest.mock.Mock()
        fake_dist.locate_file.return_value = self.root / 'prepared-site'
        with patch.object(wheels.sys, 'flags', unittest.mock.Mock(isolated=1)), \
             patch.object(wheels.sys, 'path', ['/outside']), \
             patch.object(wheels.importlib.metadata, 'distribution', return_value=fake_dist), \
             patch.object(wheels.importlib, 'import_module') as imported:
            with self.assertRaisesRegex(ValueError, 'prepared dependencies'):
                wheels.installed_probe(plan, self.root / 'qualification')
            imported.assert_not_called()

    def test_probe_key_module_origin_must_match_selected_installed_payload(self):
        site, identity = self.install_synthetic_files()
        plan = self.root / 'probe.json'
        plan.write_text(json.dumps({'manifestSha256': 'a' * 64, 'candidates': [identity]}))
        fake_dist = unittest.mock.Mock(version='1.0')
        fake_dist.locate_file.return_value = site
        outside = self.root / 'prepared.py'
        outside.write_text('prepared fallback')
        fake_module = unittest.mock.Mock(__file__=str(outside))
        with patch.object(wheels.sys, 'flags', unittest.mock.Mock(isolated=1)), \
             patch.object(wheels.sys, 'path', ['/outside']), \
             patch.object(wheels.importlib.metadata, 'distribution', return_value=fake_dist), \
             patch.object(wheels.importlib, 'import_module', return_value=fake_module):
            with self.assertRaisesRegex(ValueError, 'origin/hash mismatch'):
                wheels.installed_probe(plan, self.root)

    def test_unhashed_installed_record_payload_rejected(self):
        site, identity = self.install_synthetic_files()
        record = site / identity['record']
        rows = wheels.record_rows(record.read_bytes())
        key = 'amplifier_foundation/__init__.py'
        rows[key] = ('', '')
        stream = io.StringIO()
        csv.writer(stream).writerows((name, *values) for name, values in rows.items())
        record.write_text(stream.getvalue())
        with self.assertRaisesRegex(ValueError, 'requires SHA'):
            wheels.installed_files(site, identity)

    def test_corrupt_second_candidate_prevents_every_candidate_import(self):
        site, foundation = self.install_synthetic_files()
        path, row, payload = synthetic_wheel(self.root, 'amplifier-bundle-context-intelligence')
        ci = wheels.wheel_identity(path, row)
        for name, data in payload.items():
            target = site / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (site / 'context_intelligence/__init__.py').write_text('corrupted second candidate')
        plan = self.root / 'probe.json'
        plan.write_text(json.dumps({'manifestSha256': 'a' * 64, 'candidates': [foundation, ci]}))
        fake_dist = unittest.mock.Mock(version='1.0')
        fake_dist.locate_file.return_value = site
        with patch.object(wheels.sys, 'flags', unittest.mock.Mock(isolated=1)), \
             patch.object(wheels.sys, 'path', ['/outside']), \
             patch.object(wheels.importlib.metadata, 'distribution', return_value=fake_dist), \
             patch.object(wheels.importlib, 'import_module',
                          side_effect=AssertionError('Candidate executed before complete file validation')) as imported:
            with self.assertRaisesRegex(ValueError, 'Installed payload hash mismatch'):
                wheels.installed_probe(plan, self.root)
            imported.assert_not_called()

    def test_second_candidate_record_extra_and_symlink_refusals_precede_every_import(self):
        site, foundation = self.install_synthetic_files()
        path, row, payload = synthetic_wheel(self.root, 'amplifier-bundle-context-intelligence')
        ci = wheels.wheel_identity(path, row)
        for name, data in payload.items():
            target = site / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        plan = self.root / 'probe.json'
        plan.write_text(json.dumps({'manifestSha256': 'a' * 64, 'candidates': [foundation, ci]}))
        fake_dist = unittest.mock.Mock(version='1.0')
        fake_dist.locate_file.return_value = site
        extra = site / 'context_intelligence/extra.py'
        record = site / ci['record']
        for mutation, expected in [('record', 'RECORD hash'), ('extra', 'Unrecorded'),
                                   ('symlink', 'Symlink')]:
            with self.subTest(mutation=mutation):
                if mutation == 'record':
                    record.write_bytes(payload[ci['record']].replace(b'sha256=', b'sha256=bad', 1))
                elif mutation == 'extra':
                    extra.write_text('unrecorded')
                else:
                    extra.symlink_to(site / 'context_intelligence/__init__.py')
                with patch.object(wheels.sys, 'flags', unittest.mock.Mock(isolated=1)), \
                     patch.object(wheels.sys, 'path', ['/outside']), \
                     patch.object(wheels.importlib.metadata, 'distribution', return_value=fake_dist), \
                     patch.object(wheels.importlib, 'import_module',
                                  side_effect=AssertionError('No candidate may execute')) as imported:
                    with self.assertRaisesRegex(ValueError, expected):
                        wheels.installed_probe(plan, self.root)
                    imported.assert_not_called()
                record.write_bytes(payload[ci['record']])
                if extra.exists() or extra.is_symlink():
                    extra.unlink()

    def test_all_validated_before_first_import_can_run(self):
        site, foundation = self.install_synthetic_files()
        path, row, payload = synthetic_wheel(self.root, 'amplifier-bundle-context-intelligence')
        ci = wheels.wheel_identity(path, row)
        for name, data in payload.items():
            target = site / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        plan = self.root / 'probe.json'
        plan.write_text(json.dumps({'manifestSha256': 'a' * 64, 'candidates': [foundation, ci]}))
        fake_dist = unittest.mock.Mock(version='1.0')
        fake_dist.locate_file.return_value = site
        events = []
        original = wheels.installed_files

        def validate(root, identity):
            result = original(root, identity)
            events.append('validated:' + identity['distribution'])
            return result

        def importing(name):
            events.append('import:' + name)
            raise RuntimeError('stop at first import')

        with patch.object(wheels.sys, 'flags', unittest.mock.Mock(isolated=1)), \
             patch.object(wheels.sys, 'path', ['/outside']), \
             patch.object(wheels.importlib.metadata, 'distribution', return_value=fake_dist), \
             patch.object(wheels, 'installed_files', side_effect=validate), \
             patch.object(wheels.importlib, 'import_module', side_effect=importing):
            with self.assertRaisesRegex(RuntimeError, 'stop at first import'):
                wheels.installed_probe(plan, self.root)
        self.assertEqual(events, ['validated:amplifier-foundation',
                                  'validated:amplifier-bundle-context-intelligence',
                                  'import:amplifier_foundation'])


class Wrapper(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_default_and_older_modes_unchanged(self):
        self.assertEqual(wrapper.selected_scenarios(wrapper.parse_args([])),
                         ['composer-fidelity', 'composer-options', 'compact-composer', 'composer-primary-action', 'message-delivery'])
        self.assertEqual(wrapper.selected_scenarios(wrapper.parse_args(['--header-only'])),
                         ['composer-header', 'compact-composer'])
        self.assertEqual(wrapper.selected_scenarios(wrapper.parse_args(['--markdown-only'])),
                         ['markdown-composer', 'multi-quote', 'compact-composer'])
        self.assertEqual(wrapper.selected_scenarios(wrapper.parse_args(['--optimize-only'])), ['optimize-storage'])

    def test_ambiguous_modes_and_candidate_flags_rejected(self):
        for argv in (['--header-only', '--optimize-only'], ['--header-only', '--markdown-only'],
                     ['--candidate-wheel-manifest', 'missing.json'], ['--expected-client-commit', 'a' * 40]):
            with self.subTest(argv=argv), patch('sys.stderr', new=io.StringIO()):
                with self.assertRaises(SystemExit):
                    wrapper.parse_args(argv)

    def test_optimize_candidate_argv_has_no_resolver_or_index(self):
        args = wrapper.candidate_install_args('/artifacts/venv/bin/python',
            {'candidates': [{'wheelPath': '/artifacts/one.whl'}, {'wheelPath': '/artifacts/two.whl'}]})
        self.assertEqual(args, ['uv', 'pip', 'install', '--python', '/artifacts/venv/bin/python',
                               '--no-index', '--no-deps', '--reinstall', '/artifacts/one.whl', '/artifacts/two.whl'])

    def test_wrapper_component_probe_precedes_general_identity_probe(self):
        # Check the real wrapper command sites, not a mocked plan or test-only
        # sequence. This source control cannot launch either subprocess.
        tree = ast.parse((HERE / 'composer-installed-check.py').read_text())
        calls = {node.args[0].value: node.lineno for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == 'command' and node.args
                 and isinstance(node.args[0], ast.Constant)
                 and node.args[0].value in {'component-identity', 'identity'}}
        self.assertLess(calls['component-identity'], calls['identity'],
                        'Optimize must validate all installed candidates before the general identity imports')

    def test_failed_component_probe_never_dispatches_general_identity_command(self):
        contract = self.root / 'contract.json'
        contract.write_text('{"profileId":"browser-test"}')
        for name in ['pyproject.toml', 'uv.lock', 'tests/web/package.json',
                     'tests/web/package-lock.json', '.build/web-client/package.json',
                     '.build/web-client/package-lock.json', '.build/web-client/vite.config.js',
                     'components/web-client.source.json']:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}')
        output = self.root / 'out'
        calls = []

        def fake_command(name, argv, **kwargs):
            # Simulate only the filesystem outputs needed to reach probe dispatch.
            # No command, build, install, venv process or candidate code executes.
            calls.append(name)
            if name == 'wheel-build':
                (output / 'wheel').mkdir()
                with zipfile.ZipFile(output / 'wheel/synthetic.whl', 'w') as archive:
                    archive.writestr('amplifier_web/__init__.py', b'# synthetic\n')
            elif name == 'venv':
                (output / 'venv/lib' / ('python' + '.'.join(map(str, sys.version_info[:2])))
                 / 'site-packages').mkdir(parents=True)
            elif name == 'component-identity':
                raise ValueError('selected candidate refused before import')
            elif name == 'identity':
                self.fail('General identity command dispatched before component admission')

        with patch.object(wrapper, 'ROOT', self.root), patch.object(wrapper, 'OUT', output), \
             patch.dict('os.environ', {'AMPLIFIER_TEST_CONTRACT': str(contract)}), \
             patch.object(wrapper, 'manifest', return_value={}), \
             patch.object(wrapper, 'accepted_client', return_value='a' * 40), \
             patch.object(wrapper, 'provenance_packet', return_value=(b'{}', 'pinned')), \
             patch.object(wrapper, 'command', side_effect=fake_command):
            with self.assertRaisesRegex(ValueError, 'candidate refused before import'):
                wrapper.main(['--optimize-only'])
        self.assertEqual(calls, ['wheel-build', 'venv', 'install', 'component-identity'])
        self.assertEqual(json.loads((output / 'result.json').read_text())['status'], 'error')

    def packet(self):
        fixture = self.root / 'tests/fixtures/optimize_storage_ui_server.py'
        journey = self.root / 'tests/web/optimize-storage-browser.mjs'
        fixture.parent.mkdir(parents=True)
        journey.parent.mkdir(parents=True)
        fixture.write_text('fixture')
        journey.write_text('journey')
        packet = {'serverCommit': 'b' * 40, 'fixtureSha256': wheels.sha(b'fixture'),
                  'journeySha256': wheels.sha(b'journey')}
        path = self.root / 'packet.json'
        path.write_text(json.dumps(packet))
        return path

    def test_packet_exact_hash_files_and_commit(self):
        path = self.packet()
        args = wrapper.parse_args(['--optimize-only', '--server-provenance', str(path),
                                   '--server-provenance-sha256', wheels.sha(path.read_bytes())])
        with patch.object(wrapper, 'ROOT', self.root):
            self.assertEqual(wrapper.provenance_packet(args)[0], path.read_bytes())
            (self.root / 'tests/web/optimize-storage-browser.mjs').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'journeySha256'):
                wrapper.provenance_packet(args)

    def test_packet_wrong_digest_or_missing(self):
        path = self.packet()
        args = wrapper.parse_args(['--optimize-only', '--server-provenance', str(path),
                                   '--server-provenance-sha256', '0' * 64])
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            wrapper.provenance_packet(args)
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'requires a gitless'):
                wrapper.provenance_packet(wrapper.parse_args(['--optimize-only']))

    def test_expected_client_independent_of_selection(self):
        (self.root / 'components').mkdir()
        (self.root / 'components/web-client.source.json').write_text(json.dumps({'source': {'revision': 'c' * 40}}))
        with patch.object(wrapper, 'ROOT', self.root), patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'explicit accepted'):
                wrapper.accepted_client(wrapper.parse_args(['--optimize-only']))
            args = wrapper.parse_args(['--optimize-only', '--expected-client-commit', 'c' * 40])
            self.assertEqual(wrapper.accepted_client(args), 'c' * 40)
            args.expected_client_commit = 'd' * 40
            with self.assertRaisesRegex(ValueError, 'differs'):
                wrapper.accepted_client(args)

    def test_only_optimize_reads_candidate_manifest(self):
        # Stop at the existing wheel-build call; no command/build actually runs.
        (self.root / 'pyproject.toml').write_text('source')
        contract = self.root / 'contract.json'
        contract.write_text('{"profileId":"browser-test"}')
        with patch.object(wrapper, 'ROOT', self.root), patch.object(wrapper, 'OUT', self.root / 'out'), \
             patch.dict('os.environ', {'AMPLIFIER_TEST_CONTRACT': str(contract)}), \
             patch.object(wrapper, 'manifest', return_value={}), \
             patch.object(wrapper, 'digest', return_value='hash'), \
             patch.object(wrapper, 'load_candidates') as loaded, \
             patch.object(Path, 'read_bytes', return_value=b'input'), \
             patch.object(wrapper, 'command', side_effect=RuntimeError('stop before build')):
            with self.assertRaisesRegex(RuntimeError, 'stop before build'):
                wrapper.main(['--header-only'])
            loaded.assert_not_called()

    def proof(self):
        output = self.root / 'optimize-storage'
        destination = output / 'optimize-storage-browser-finite'
        destination.mkdir(parents=True)
        value = {'passed': True, 'browserExecuted': True, 'acceptanceComplete': True, 'notCovered': [],
                 'provenance': {'provenancePacketSha256': 'pinned', 'componentSha256': 'component'},
                 'servedScripts': [{'path': '/assets/app.js', 'status': 200, 'sha256': 'bytes'}]}
        path = destination / 'result.json'
        path.write_text(json.dumps(value))
        return output, path, value

    def test_real_browser_receipt_and_served_join(self):
        output, _, _ = self.proof()
        result = wrapper.optimize_proof(output, {'assets/app.js': 'bytes', 'client-component.json': 'component'},
                                        '/venv/app', {'verified': True}, 'pinned')
        self.assertEqual(result['packageRoot'], '/venv/app')

    def test_missing_or_incomplete_browser_never_passes(self):
        output, path, value = self.proof()
        for key, invalid in [('passed', False), ('browserExecuted', False), ('acceptanceComplete', False),
                             ('notCovered', ['pending']), ('cleanupFailure', 'not closed'),
                             ('servedScripts', [])]:
            with self.subTest(key=key):
                path.write_text(json.dumps({**value, key: invalid}))
                with self.assertRaises(ValueError):
                    wrapper.optimize_proof(output, {'assets/app.js': 'bytes', 'client-component.json': 'component'},
                                           '/venv/app', {'verified': True}, 'pinned')
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            wrapper.optimize_proof(output, {}, '/venv/app', {'verified': True}, 'pinned')

    def test_bad_static_or_provenance_never_passes(self):
        output, _, _ = self.proof()
        for static, packet in [({'assets/app.js': 'changed', 'client-component.json': 'component'}, 'pinned'),
                               ({'assets/app.js': 'bytes', 'client-component.json': 'component'}, 'wrong'),
                               ({'assets/app.js': 'bytes', 'client-component.json': 'changed'}, 'pinned')]:
            with self.assertRaises(ValueError):
                wrapper.optimize_proof(output, static, '/venv/app', {'verified': True}, packet)


if __name__ == '__main__':
    unittest.main()