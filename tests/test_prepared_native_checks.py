"""Cheap controls for the ordinary native suite; no Docker/native dependencies."""
import builtins
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def test_prepared_suite_is_one_ordinary_manual_job():
    import yaml
    value = yaml.safe_load((ROOT / '.github/workflows/python-checks.yml').read_text())
    events = value.get('on', value.get(True))
    assert 'prepared-runtime-native' in events['workflow_dispatch']['inputs']['suite']['options']
    assert 'prepared-default-native' in events['workflow_dispatch']['inputs']['suite']['options']
    assert 'pull_request' not in events
    job = value['jobs']['prepared-runtime-native']
    assert 'workflow_dispatch' in job['if'] and job['permissions'] == {'contents': 'read'}
    assert job['if'] == "${{ github.event_name == 'workflow_dispatch' && !inputs.core_wheels && (inputs.suite == 'prepared-runtime-native' || inputs.suite == 'prepared-default-native') }}"
    assert job['env']['CHECK_SUITE'] == '${{ inputs.suite }}'
    assert all('secrets.' not in str(s) for s in job['steps'])
    checkouts = [s for s in job['steps'] if s.get('uses', '').startswith('actions/checkout@')]
    assert all(s['with']['persist-credentials'] is False for s in checkouts)
    assert any(s.get('if') == 'always()' and s.get('uses', '').startswith('actions/upload-artifact@') for s in job['steps'])
    assert "inputs.suite != 'prepared-runtime-native'" in value['jobs']['python']['if']
    assert "inputs.suite != 'prepared-default-native'" in value['jobs']['python']['if']
    upload = next(s for s in job['steps'] if s.get('uses', '').startswith('actions/upload-artifact@'))
    assert upload['with']['path'] == 'output/${{ inputs.suite }}/'
    assert upload['with']['name'].startswith('${{ inputs.suite }}-')
    commands = '\n'.join(s.get('run', '') for s in job['steps'])
    assert 'tee "output/$CHECK_SUITE/driver.log"' in commands
    assert not (ROOT / '.github/workflows/prepared-runtime-native.yml').exists()
    assert not list((ROOT / 'scripts').glob('candidate_native_*.py'))
    assert 'registered.json' not in (ROOT / 'tests/test_prepared_preset_native.py').read_text()
    driver = (ROOT / 'scripts/fresh_cleanup_native_checks.py').read_text()
    # Selection remains a pre-effect snapshot; the terminal result must not
    # inherit its initial unrun label after the exact case guard succeeds.
    assert "'native': 'unrun'" in driver and "'native': 'passed'" in driver
    assert driver.index('count = verify_cases(') < driver.index("'native': 'passed'")


def test_checkout_gate_uses_actual_bytes_and_distinct_evidence(monkeypatch):
    from native_cleanup_fixture import CheckoutGate
    from amplifier_web import environment_cleanup_execution as execution
    app = object()
    gate = CheckoutGate(app)
    assert gate.bootstrap()['source'] == gate.raw['amplifier_web/environment_cleanup_launcher.py'].hex()
    assert CheckoutGate.receive is execution.Gate.receive
    assert CheckoutGate.validate is execution.Gate.validate
    assert 'serving' not in gate.evidence({'phase': 'loaded'})
    assert gate.evidence({})['kind'] == 'qualification-test-checkout'
    gate.recheck()
    path = next(iter(gate.paths.values()))
    original = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda p: b'changed' if p == path else original(p))
    with pytest.raises(ValueError, match='changed'):
        gate.recheck()


def test_checkout_gate_is_only_pytest_scoped_and_permit_not_replaced(monkeypatch):
    from native_cleanup_fixture import checkout_cleanup
    from amplifier_web import environment_cleanup_execution as execution, task_environments as owner
    original = execution.Gate
    permit, app = object(), SimpleNamespace(data_dir=Path('/unused'))
    seen = {}
    def invoke(*args, **kw):
        seen.update(kw)
        return {'ok': True}
    monkeypatch.setattr(owner, 'invoke', invoke)
    with monkeypatch.context() as scoped:
        checkout_cleanup(scoped)
        assert owner.invoke_cleanup(app, {'backend': 'docker'}, {}, 'remove', {}, permit) == {'ok': True}
    assert execution.Gate is original and seen['_cleanup_permit'] is permit
    assert seen['_cleanup_gate'].evidence({})['kind'] == 'qualification-test-checkout'


@pytest.mark.parametrize('value', [
    {'complete': False, 'candidates': [], 'protectedIds': []},
    {'complete': True, 'candidates': [], 'protectedIds': ['unknown']},
    {'complete': True, 'candidates': [{'id': 'live'}], 'protectedIds': []},
])
def test_unresolved_dependencies_never_allow_next_profile(value):
    from native_cleanup_fixture import require_absent
    with pytest.raises(AssertionError):
        require_absent(value)


def test_absent_dependencies_and_prior_cleanup_latch(tmp_path):
    from native_cleanup_fixture import require_absent, require_prior_cleanup
    require_absent({'complete': True, 'candidates': [], 'protectedIds': []})
    (tmp_path / 'python-project.json').write_text(json.dumps({'cleanupComplete': False}))
    with pytest.raises(AssertionError):
        require_prior_cleanup(tmp_path, 'browser-test')
    (tmp_path / 'python-project.json').write_text(json.dumps({'cleanupComplete': True, 'passed': False}))
    require_prior_cleanup(tmp_path, 'browser-test')  # A known test failure with safe cleanup is not physical custody.


@pytest.mark.parametrize('names', [[], ['python-project'], ['python-project', 'python-project'], ['python-project', 'browser-test', 'extra']])
def test_exact_native_case_accounting(names):
    from fresh_cleanup_native_checks import verify_cases
    suite = ET.Element('testsuites')
    for name in names:
        ET.SubElement(suite, 'testcase', name='test_native_default_prepared_preset_runtime[' + name + ']')
    with pytest.raises(AssertionError):
        verify_cases(0, ET.tostring(suite), 'prepared-runtime-native')


@pytest.mark.parametrize('bad', ['skipped', 'failure', 'error'])
def test_native_skips_and_failures_are_not_qualification(bad):
    from fresh_cleanup_native_checks import verify_cases
    suite = ET.Element('testsuites')
    for profile in ['python-project', 'browser-test']:
        case = ET.SubElement(suite, 'testcase', name='test_native_default_prepared_preset_runtime[' + profile + ']')
    ET.SubElement(case, bad)
    with pytest.raises(AssertionError):
        verify_cases(0, ET.tostring(suite), 'prepared-runtime-native')


def test_success_requires_zero_exit_and_two_named_native_cases():
    from fresh_cleanup_native_checks import verify_cases
    suite = ET.Element('testsuites')
    for profile in ['python-project', 'browser-test']:
        ET.SubElement(suite, 'testcase', name='test_native_default_prepared_preset_runtime[' + profile + ']')
    assert verify_cases(0, ET.tostring(suite), 'prepared-runtime-native') == 2
    with pytest.raises(AssertionError):
        verify_cases(1, ET.tostring(suite), 'prepared-runtime-native')


@pytest.mark.parametrize('scope', ['prepared-runtime-native', 'prepared-default-native', 'fresh-cleanup-native'])
def test_actual_driver_records_both_sources_before_backend(tmp_path, monkeypatch, scope):
    import fresh_cleanup_native_checks as driver
    root = tmp_path / 'checkout'; root.mkdir()
    product = root / 'amplifier_web'; product.mkdir()
    (product / 'task_environments.py').write_text("DTU_COMMIT='pin'\nAPPROVED_IMAGE='image'\nREPOSITORY='dtu'\n")
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c',
                    'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    expected = driver.git(root, 'rev-parse', 'HEAD')
    monkeypatch.setattr(driver, 'ROOT', root)
    for name, value in {'CHECK_SUITE': scope, 'CLEANUP_NATIVE_ROOT': str(tmp_path / 'work'),
                        'RUNNER_TEMP': str(tmp_path), 'GITHUB_ACTIONS': 'true', 'GITHUB_SHA': expected,
                        'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '2', 'CANDIDATE_REF': expected}.items():
        monkeypatch.setenv(name, value)
    real_git = driver.git
    monkeypatch.setattr(driver, 'git', lambda where, *args: 'pin' if where.name == 'dtu-core' else real_git(where, *args))
    class BackendNotStarted(Exception):
        pass
    def before_backend(argv, **kwargs):
        assert argv == ['docker', 'version']
        record = json.loads((root / 'output' / scope / 'selection.json').read_text())
        assert record['revision'] == record['candidate'] == expected and record['runAttempt'] == '2'
        assert record['native'] == 'unrun'
        filename, names = driver.SUITES[scope]
        assert record['suite'] == scope
        assert record['targets'] == ['tests/' + filename + '::' + name for name in names]
        raise BackendNotStarted
    monkeypatch.setattr(driver, 'run', before_backend)
    with pytest.raises(BackendNotStarted):
        driver.main()


async def test_reused_native_app_explicitly_selects_each_profile_workspace(tmp_path):
    from amplifier_web.service import AppService
    from test_prepared_preset_native import NoModelRuntime
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir(); second.mkdir()
    app = AppService(tmp_path / 'app', NoModelRuntime(), workspace=first)
    await app.close()
    app = AppService(tmp_path / 'app', NoModelRuntime(), workspace=second)
    try:
        assert not any(w['path'] == str(second) for w in app.state['workspaces'])
        await app.dispatch('workspace.add', {'path': str(second)})
        selected = next(w for w in app.state['workspaces'] if w['id'] == app.state['selectedWorkspaceId'])
        assert selected['path'] == str(second)
    finally:
        await app.close()


def test_original_candidate_probe_and_node_identities_are_unchanged():
    from test_prepared_preset_native import PROBE
    from fresh_cleanup_native_checks import SUITES
    assert len(PROBE.encode()) == 1642
    assert hashlib.sha256(PROBE.encode()).hexdigest() == 'e3888034e23cbdf849ce14473cfab120c6bd4d95de6ea1969676adc5055ab6ee'
    assert SUITES['prepared-runtime-native'][1] == [
        'test_native_default_prepared_preset_runtime[python-project]',
        'test_native_default_prepared_preset_runtime[browser-test]']
    assert SUITES['prepared-default-native'][1] == [
        'test_native_current_prepared_preset_defaults[python-project]',
        'test_native_current_prepared_preset_defaults[browser-test]']


@pytest.mark.parametrize('scope', ['prepared-runtime-native', 'prepared-default-native'])
@pytest.mark.parametrize('bad', ['other-scope', 'missing', 'duplicate', 'reversed', 'extra',
                                  'nonzero', 'failure', 'error', 'skipped', None])
def test_native_scope_cannot_consume_other_or_incomplete_cases(scope, bad):
    from fresh_cleanup_native_checks import SUITES, verify_cases
    names = list(SUITES[scope][1])
    other = 'prepared-default-native' if scope == 'prepared-runtime-native' else 'prepared-runtime-native'
    if bad == 'other-scope': names = SUITES[other][1]
    elif bad == 'missing': names.pop()
    elif bad == 'duplicate': names[1] = names[0]
    elif bad == 'reversed': names.reverse()
    elif bad == 'extra': names.append('extra')
    xml = ET.Element('testsuites')
    for name in names:
        case = ET.SubElement(xml, 'testcase', name=name)
    if bad in {'failure', 'error', 'skipped'}:
        ET.SubElement(case, bad)
    if bad is None:
        assert verify_cases(0, ET.tostring(xml), scope) == 2
    else:
        with pytest.raises(AssertionError):
            verify_cases(1 if bad == 'nonzero' else 0, ET.tostring(xml), scope)


async def test_wrong_scope_refuses_before_lifecycle_or_backend(monkeypatch):
    import test_prepared_preset_native as native
    monkeypatch.setattr(native, '_prepared_case', lambda *a: pytest.fail('Wrong scope reached lifecycle'))
    for suite, function in [('prepared-default-native', native.test_native_default_prepared_preset_runtime),
                            ('prepared-runtime-native', native.test_native_current_prepared_preset_defaults)]:
        monkeypatch.setenv('CHECK_SUITE', suite)
        with pytest.raises(AssertionError, match='Wrong native qualification scope'):
            await function('python-project', monkeypatch)


def test_unknown_scope_never_starts_driver_effects(tmp_path, monkeypatch):
    import fresh_cleanup_native_checks as driver
    monkeypatch.setenv('CHECK_SUITE', 'guessed-default')
    monkeypatch.setattr(driver, 'ROOT', tmp_path)
    monkeypatch.setattr(driver, 'run', lambda *a, **k: pytest.fail('Unexpected effect'))
    with pytest.raises(KeyError):
        driver.main()
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def default_probe_observation():
    """Execute the actual probe with synthetic observations, never native mounts."""
    import _struct
    from amplifier_web import test_environment_profiles as profiles
    from test_prepared_preset_native import DEFAULT_PROBE
    snapshot = {'archive_sha256': 'a' * 64, 'source_head': 'b' * 40,
                'snapshot_commit': 'c' * 40, 'snapshot_tree': 'd' * 40}
    contract = profiles.execution_contract('python-project', {
        'archiveSha256': snapshot['archive_sha256'], 'sourceHead': snapshot['source_head'],
        'snapshotCommit': snapshot['snapshot_commit'], 'snapshotTree': snapshot['snapshot_tree']})
    expected = {'contract': contract, 'snapshot': snapshot, 'profile': 'python-project',
                'files': {'pyproject.toml': hashlib.sha256(b'fixture').hexdigest()}, 'tmpMiB': 512}
    base = '/opt/unified-prepared/python/cpython-3.13'
    native = base + '/lib/python3.13/lib-dynload'
    lib = native + '/_struct.so'
    state = {'contract': copy.deepcopy(contract), 'library': b'native fixture', 'copies': {}, 'loads': [],
             'written': {}, 'tmpFlags': os.ST_NOEXEC | os.ST_NOSUID, 'tmpMiB': 512,
             'rootFlags': os.ST_RDONLY, 'nativeFlags': os.ST_RDONLY, 'writable': False,
             'loadError': False, 'tmpLoads': False, 'changedHash': False, 'groups': [],
             'source': b'fixture', 'exists': set(), 'cwd': '/workspace', 'operation': 0x12345678}

    class ObservedPath(PurePosixPath):
        @classmethod
        def cwd(cls): return cls(state['cwd'])
        def resolve(self, strict=False): return self
        def exists(self): return str(self) in state['exists']
        def stat(self): return SimpleNamespace(st_mode=stat.S_IFREG | 0o644)
        def read_text(self): return json.dumps(state['contract'])
        def read_bytes(self):
            if str(self) == '/workspace/pyproject.toml': return state['source']
            if str(self) in state['copies']: return state['copies'][str(self)]
            if state['changedHash'] and state['loads']: return b'changed'
            return state['library']
        def write_text(self, value): state['written'][str(self)] = value

    def filesystem(path):
        flag = state['tmpFlags'] if str(path) == '/tmp' else state['rootFlags'] if str(path) == '/' else state['nativeFlags']
        return SimpleNamespace(f_flag=flag, f_blocks=state['tmpMiB'] * 256, f_frsize=4096)
    def load(path):
        state['loads'].append(path)
        if path == '/tmp/native.so':
            if not state['tmpLoads']: raise OSError('noexec')
        elif state['loadError']:
            raise OSError('native load failed')
    def copyfile(source, target):
        state['copies'][str(target)] = source.read_bytes()
    env = {'AMPLIFIER_TEST_PYTHON': contract['python'], 'AMPLIFIER_TEST_CONTRACT': contract['manifest'],
           'AMPLIFIER_TEST_ARTIFACTS': '/artifacts', 'HOME': '/tmp/unified-test/home',
           'XDG_CACHE_HOME': '/tmp/unified-test/cache'}
    modules = {
        'ctypes': SimpleNamespace(CDLL=load), 'hashlib': hashlib, 'json': json, 'stat': stat,
        'pathlib': SimpleNamespace(Path=ObservedPath), 'shutil': SimpleNamespace(copyfile=copyfile),
        'packaging': SimpleNamespace(__version__='25.0'), 'pytest': SimpleNamespace(__version__='8.4.2'),
        'sys': SimpleNamespace(prefix='/opt/unified-prepared/venv', executable=contract['python'],
                               base_prefix=base, argv=['-c', '512', json.dumps(expected)]),
        'sysconfig': SimpleNamespace(get_config_var=lambda key: native),
        '_struct': SimpleNamespace(__file__=lib, __spec__=SimpleNamespace(origin=lib),
                                   pack=lambda *a: _struct.pack('!I', state['operation']),
                                   unpack=_struct.unpack),
        'os': SimpleNamespace(environ=env, getuid=lambda: 65534, getgid=lambda: 65534,
                              getgroups=lambda: state['groups'], R_OK=os.R_OK, W_OK=os.W_OK,
                              ST_NOEXEC=os.ST_NOEXEC, ST_NOSUID=os.ST_NOSUID, ST_RDONLY=os.ST_RDONLY,
                              access=lambda path, mode: state['writable'], statvfs=filesystem)}
    def execute(code=DEFAULT_PROBE):
        namespace = {'__builtins__': {**vars(builtins), '__import__': lambda name, *a, **k: modules[name]}}
        exec(compile(code, '<actual-prepared-probe>', 'exec'), namespace)
        return json.loads(state['written']['/artifacts/runtime-proof.json'])
    return expected, state, modules, execute


@pytest.mark.parametrize('bad', [None, 'profile', 'source-contract', 'python', 'artifacts', 'manifest',
                                'runtime-field', 'runtime-env', 'runtime-mount', 'git', 'source-file',
                                'cwd', 'tmp-budget', 'tmp-executable', 'tmp-readonly', 'root-writable',
                                'native-origin', 'native-writable', 'native-noexec', 'hash-drift',
                                'native-load', 'tmp-load', 'groups', 'home', 'operation'])
def test_actual_default_probe_rejects_changed_observations(default_probe_observation, bad):
    from test_prepared_preset_native import _default_proof
    expected, state, modules, execute = default_probe_observation
    if bad == 'profile': state['contract']['profileId'] = 'browser-test'
    elif bad == 'source-contract': state['contract']['source']['sourceHead'] = 'e' * 40
    elif bad == 'python': modules['sys'].executable = '/wrong/python'
    elif bad == 'artifacts': modules['os'].environ['AMPLIFIER_TEST_ARTIFACTS'] = '/workspace'
    elif bad == 'manifest': modules['os'].environ['AMPLIFIER_TEST_CONTRACT'] = '/wrong/contract'
    elif bad == 'runtime-field': state['contract']['runtimeWorkdir'] = {}
    elif bad == 'runtime-env': modules['os'].environ['AMPLIFIER_TEST_RUNTIME'] = '/unified-runtime'
    elif bad == 'runtime-mount': state['exists'].add('/unified-runtime')
    elif bad == 'git': state['exists'].add('/workspace/.git')
    elif bad == 'source-file': state['source'] = b'changed'
    elif bad == 'cwd': state['cwd'] = '/other'
    elif bad == 'tmp-budget': state['tmpMiB'] = 128
    elif bad == 'tmp-executable': state['tmpFlags'] &= ~os.ST_NOEXEC
    elif bad == 'tmp-readonly': state['tmpFlags'] |= os.ST_RDONLY
    elif bad == 'root-writable': state['rootFlags'] = 0
    elif bad == 'native-origin': modules['_struct'].__spec__.origin = '/workspace/native.so'
    elif bad == 'native-writable': state['nativeFlags'] = 0
    elif bad == 'native-noexec': state['nativeFlags'] |= os.ST_NOEXEC
    elif bad == 'hash-drift': state['changedHash'] = True
    elif bad == 'native-load': state['loadError'] = True
    elif bad == 'tmp-load': state['tmpLoads'] = True
    elif bad == 'groups': state['groups'] = [0]
    elif bad == 'home': modules['os'].environ['HOME'] = '/workspace'
    elif bad == 'operation': state['operation'] = 0
    if bad is None:
        proof = execute()
        _default_proof(proof, expected)
        assert state['loads'] == [proof['nativePath'], '/tmp/native.so']
        assert 'nativeMap' not in proof
    else:
        with pytest.raises((AssertionError, OSError)):
            execute()
        assert state['written'] == {}, 'A failed probe must not publish success'


@pytest.mark.parametrize('bad', ['scope', 'claim', 'candidate', 'source', 'budget', 'hash', 'operation'])
def test_retained_default_proof_cannot_change_claim(default_probe_observation, bad):
    from test_prepared_preset_native import _default_proof
    expected, _, _, execute = default_probe_observation
    proof = execute()
    if bad == 'scope': proof['scope'] = 'candidate-runtime'
    elif bad == 'claim': proof['preparedNativeLoad'] = False
    elif bad == 'candidate': proof['nativeMap'] = True
    elif bad == 'source': proof['contract']['source']['sourceHead'] = 'e' * 40
    elif bad == 'budget': proof['tmpMiB'] = 128
    elif bad == 'hash': proof['nativeSha256'] = 'not-a-hash'
    elif bad == 'operation': proof['knownOperation'] = '00000000'
    with pytest.raises(AssertionError):
        _default_proof(proof, expected)


def test_candidate_missing_runtime_field_never_uses_default_probe(default_probe_observation):
    from test_prepared_preset_native import PROBE
    _, state, _, execute = default_probe_observation
    with pytest.raises(KeyError, match='runtimeWorkdir'):
        execute(PROBE)
    assert state['loads'] == [] and state['written'] == {}


@pytest.mark.parametrize('profile', ['python-project', 'browser-test'])
@pytest.mark.parametrize('base', ['/usr/local', '/opt/unified-prepared/python/cpython-3.13'])
def test_default_probe_handles_prepared_or_image_python(default_probe_observation, profile, base):
    from test_prepared_preset_native import _default_proof
    expected, state, modules, execute = default_probe_observation
    expected['profile'] = expected['contract']['profileId'] = state['contract']['profileId'] = profile
    expected['tmpMiB'] = state['tmpMiB'] = 1024 if profile == 'browser-test' else 512
    modules['sys'].argv = ['-c', str(expected['tmpMiB']), json.dumps(expected)]
    modules['sys'].base_prefix = base
    native = base + '/lib/python3.13/lib-dynload'
    modules['sysconfig'].get_config_var = lambda key: native
    modules['_struct'].__file__ = modules['_struct'].__spec__.origin = native + '/_struct.so'
    _default_proof(execute(), expected)
