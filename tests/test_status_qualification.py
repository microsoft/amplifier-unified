"""Installed status qualification must fail closed rather than reuse weak proof."""
import io
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import qualify_status_emitter as owner
sys.path.pop(0)


def test_candidate_wheel_must_match_reviewed_source(tmp_path):
    source = tmp_path/'source'; source.mkdir()
    (source/'__init__.py').write_text('original')
    wheel = tmp_path/'candidate.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('amplifier_status/__init__.py', 'original')
    assert owner.matching_source(wheel, 'amplifier_status', source) == 1
    (source/'__init__.py').write_text('changed after wheel build')
    with pytest.raises(ValueError, match='differs'):
        owner.matching_source(wheel, 'amplifier_status', source)
    (source/'__init__.py').write_text('original')
    (source/'new_module.py').write_text('new module missing in stale wheel')
    with pytest.raises(ValueError, match='omits'):
        owner.matching_source(wheel, 'amplifier_status', source)
    with pytest.raises(ValueError, match='no expected package files'):
        owner.matching_source(wheel, 'different', source)


def project_with_packages(packages):
    return {'tool': {'hatch': {'build': {'targets': {'wheel': {'packages': packages}}}}}}


@pytest.mark.parametrize('package', [
    'amplifier_scheduling', 'src/amplifier_scheduling',
    'components/scheduling-delivery/amplifier_scheduling',
])
def test_source_directory_maps_to_installed_prefix_without_weakening_checks(tmp_path, package):
    source = tmp_path/package; source.mkdir(parents=True)
    (source/'__init__.py').write_text('reviewed runtime')
    sub = source/'nested'; sub.mkdir()
    (sub/'module.py').write_text('reviewed submodule')
    wheel = tmp_path/'candidate.whl'
    prefix = owner.wheel_package_prefixes(project_with_packages([package]))[package]
    assert prefix == 'amplifier_scheduling'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr(prefix+'/__init__.py', 'reviewed runtime')
        archive.writestr(prefix+'/nested/module.py', 'reviewed submodule')
    if '/' in package:
        # The original qualifier passed the source directory as the archive prefix.
        with pytest.raises(ValueError, match='no expected package files'):
            owner.matching_source(wheel, package, source)
    assert owner.matching_source(wheel, prefix, source) == 2
    (sub/'module.py').write_text('changed')
    with pytest.raises(ValueError, match='differs.*nested/module.py'):
        owner.matching_source(wheel, prefix, source)
    (sub/'module.py').write_text('reviewed submodule')
    missing = source/'missing.py'; missing.write_text('must ship')
    with pytest.raises(ValueError, match='omits.*missing.py'):
        owner.matching_source(wheel, prefix, source)
    missing.unlink()
    with zipfile.ZipFile(wheel, 'a') as archive:
        archive.writestr(prefix+'/extra.py', 'unreviewed')
    with pytest.raises(ValueError, match='differs.*extra.py'):
        owner.matching_source(wheel, prefix, source)


@pytest.mark.parametrize('level', ['build', 'wheel'])
@pytest.mark.parametrize('option,value', [
    ('sources', {'src': 'renamed'}), ('sources', ['src']),
    ('only-include', ['elsewhere']), ('include', ['elsewhere/**']),
])
def test_wheel_package_mapping_refuses_unreviewed_source_selection(level, option, value):
    project = project_with_packages(['src/package'])
    build = project['tool']['hatch']['build']
    (build if level == 'build' else build['targets']['wheel'])[option] = value
    with pytest.raises(ValueError, match='unsupported wheel source selection'):
        owner.wheel_package_prefixes(project)


@pytest.mark.parametrize('packages,message', [
    ([], 'explicit wheel packages'),
    ('package', 'explicit wheel packages'),
    ([''], 'package path'), ([None], 'package path'),
    (['/outside/package'], 'package path'), (['../package'], 'package path'),
    (['src/../package'], 'package path'), (['.'], 'package path'),
    (['src//package'], 'package path'), (['src/*'], 'package path'),
    (['src\\package'], 'package path'),
    (['package', 'src/package'], 'Ambiguous'),
    (['package', 'package'], 'Ambiguous'),
    (['src/one', 'src/deeper/two'], 'overlapping'),
    (['package', 'package/sub'], 'overlapping'),
])
def test_wheel_package_mapping_refuses_ambiguous_or_unsupported_layout(packages, message):
    with pytest.raises(ValueError, match=message):
        owner.wheel_package_prefixes(project_with_packages(packages))


def test_wheel_package_mapping_uses_target_packages_before_global_packages():
    project = project_with_packages(['src/one', 'src/two'])
    build = project['tool']['hatch']['build']
    build['packages'] = ['global']
    assert owner.wheel_package_prefixes(project) == {'src/one': 'one', 'src/two': 'two'}
    del build['targets']['wheel']['packages']
    assert owner.wheel_package_prefixes(project) == {'global': 'global'}


def test_qualifier_uses_source_to_prefix_map_before_preflight(tmp_path, monkeypatch, cli_sources, host_sources):
    inputs, digest = write_cli_sources(tmp_path, cli_sources)
    host_path, host_digest = write_host_sources(tmp_path, host_sources)
    root = tmp_path/'source'; root.mkdir()
    package = 'components/scheduling-delivery/amplifier_scheduling'
    (root/'pyproject.toml').write_text(
        '[project]\ndependencies = ["amplifier-core>=2.0.2"]\n'
        '[tool.hatch.build.targets.wheel]\n'
        f'packages = ["amplifier_web", "{package}"]\n'
        '[tool.hatch.build.targets.wheel.force-include]\n'
        '"bundle.yaml" = "amplifier_web/bundle_data/bundle.yaml"\n')
    (root/'bundle.yaml').write_text('reviewed bundle')
    files = {
        'amplifier_web/__init__.py': 'web',
        'amplifier_web/status_protocol/__init__.py': 'status',
        package+'/__init__.py': 'scheduling',
    }
    for name, content in files.items():
        path = root/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    wheel = tmp_path/'candidate.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name.replace(package, 'amplifier_scheduling'), content)
        archive.writestr('amplifier_web/bundle_data/bundle.yaml', 'reviewed bundle')
        archive.writestr('amplifier_status/__init__.py', 'status')
    monkeypatch.setattr(owner, 'ROOT', root)
    monkeypatch.setattr(owner.socket, 'gethostname', lambda: 'owned-dtu')
    monkeypatch.setattr(owner, 'source_identity', lambda revision: {'revision': revision})
    monkeypatch.setattr(owner, 'wheel_identity', lambda path, name:
                        {'path': str(wheel), 'name': name, 'version': '2.0.2'})
    def before_preflight(identity):
        raise ValueError('test stops before preflight')
    monkeypatch.setattr(owner, 'published_core', before_preflight)
    work = tmp_path/'work'
    assert owner.main([
        '--owned-dtu', 'owned-dtu', '--python', 'unused', '--cli-python', 'unused',
        '--unified-wheel', str(wheel), '--status-wheel', str(wheel),
        '--core-wheel', str(wheel), '--work', str(work),
        '--cli-sources', str(inputs), '--cli-sources-sha256', digest,
        '--host-sources', str(host_path), '--host-sources-sha256', host_digest,
    ]) == 1
    receipt = json.loads((work/'result.json').read_text())
    assert receipt['error'] == 'test stops before preflight'
    assert receipt['passed'] is False and receipt['commands'] == []
    assert receipt['wheelPackagePrefixes'] == {
        'amplifier_web': 'amplifier_web', package: 'amplifier_scheduling'}
    assert receipt['wheelSourceFiles'] == {
        'unified': {'amplifier_web': 3, package: 1}, 'status': 1}


def test_standalone_tests_are_source_only_but_runtime_completeness_remains(tmp_path):
    prefix, test_root = 'amplifier_status', 'tests'
    source = tmp_path/'components/status'; source.mkdir(parents=True)
    (source/'__init__.py').write_text('runtime')
    tests = source/test_root; tests.mkdir(parents=True)
    (tests/'test_standalone.py').write_text('installed-only test')
    wheel = tmp_path/'candidate.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr(prefix+'/__init__.py', 'runtime')
    assert owner.matching_source(wheel, prefix, source) == 1
    missing = source/'new_runtime.py'; missing.write_text('must still ship')
    with pytest.raises(ValueError, match='omits.*new_runtime.py'):
        owner.matching_source(wheel, prefix, source)
    missing.unlink()
    with zipfile.ZipFile(wheel, 'a') as archive:
        archive.writestr(prefix+'/'+test_root+'/test_standalone.py', 'installed-only test')
    with pytest.raises(ValueError, match='includes standalone tests'):
        owner.matching_source(wheel, prefix, source)


def test_private_status_runtime_has_no_author_test_exemption(tmp_path):
    source = tmp_path/'amplifier_web'
    private = source/'status_protocol'; private.mkdir(parents=True)
    (private/'__init__.py').write_text('selected runtime')
    extra = private/'tests/test_unselected.py'; extra.parent.mkdir()
    extra.write_text('unselected source')
    wheel = tmp_path/'candidate.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('amplifier_web/status_protocol/__init__.py', 'selected runtime')
    with pytest.raises(ValueError, match='omits.*test_unselected.py'):
        owner.matching_source(wheel, 'amplifier_web', source)


def test_core_native_wheel_must_have_exact_publication_digest(monkeypatch):
    identity = {'version':'2.0.2', 'path':'/owned/core.whl', 'sha256':'expected'}
    def registry(digest):
        return io.BytesIO(json.dumps({'urls':[{'filename':'core.whl',
            'digests':{'sha256':digest},'packagetype':'bdist_wheel'}]}).encode())
    monkeypatch.setattr(owner.urllib.request, 'urlopen', lambda *a, **kw: registry('different'))
    with pytest.raises(ValueError, match='published registry wheel'):
        owner.published_core(identity)
    monkeypatch.setattr(owner.urllib.request, 'urlopen', lambda *a, **kw: registry('expected'))
    assert owner.published_core(identity)['wheel'] == identity


def test_candidate_force_included_bundle_files_bind_original_source(tmp_path):
    source = tmp_path/'package'; source.mkdir()
    (source/'__init__.py').write_text('original')
    external = tmp_path/'bundle.yaml'; external.write_text('exact reviewed bundle')
    wheel = tmp_path/'candidate.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('package/__init__.py', 'original')
        archive.writestr('package/bundle_data/bundle.yaml', external.read_text())
    mapping = {'package/bundle_data/bundle.yaml': external}
    assert owner.matching_source(wheel, 'package', source, mapping) == 2
    external.write_text('changed bundle')
    with pytest.raises(ValueError, match='differs'):
        owner.matching_source(wheel, 'package', source, mapping)


def test_wrong_host_and_reused_work_directory_refuse_before_effects(tmp_path, monkeypatch, cli_sources, host_sources):
    inputs, digest = write_cli_sources(tmp_path, cli_sources)
    host_path, host_digest = write_host_sources(tmp_path, host_sources)
    work = tmp_path/'owned'
    argv = ['--owned-dtu','owned-dtu','--python',sys.executable,'--cli-python',sys.executable,
            '--unified-wheel','missing','--status-wheel','missing','--core-wheel','missing',
            '--work',str(work), '--cli-sources', str(inputs), '--cli-sources-sha256', digest,
            '--host-sources', str(host_path), '--host-sources-sha256', host_digest]
    monkeypatch.setattr(owner.socket, 'gethostname', lambda:'wrong-host')
    with pytest.raises(ValueError, match='owned DTU'):
        owner.main(argv)
    assert not work.exists()
    monkeypatch.setattr(owner.socket, 'gethostname', lambda:'owned-dtu')
    work.mkdir(); receipt = work/'result.json'; receipt.write_text('old successful receipt')
    with pytest.raises(FileExistsError):
        owner.main(argv)
    assert receipt.read_text() == 'old successful receipt'


@pytest.fixture
def native_probe():
    spec = importlib.util.spec_from_file_location('status_emitter_probe', ROOT/'tests/fixtures/status_emitter_probe.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cli_sources(tmp_path, native_probe):
    store = tmp_path/'store'; store.mkdir()
    return {'schemaVersion': 1, 'store': str(store), 'sources': [
        {'url': 'https://github.com/microsoft/'+name,
         'ref': 'main',
         'commit': 'a'*40} for name in sorted(native_probe.DEFAULT_REPOSITORIES)]}


def write_cli_sources(tmp_path, value):
    path = tmp_path/'sources.json'
    data = json.dumps(value).encode()
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()


@pytest.fixture
def host_sources(tmp_path):
    store = tmp_path/'host-store'; store.mkdir()
    return {'schemaVersion': 1, 'store': str(store), 'captureCommit': 'a'*40}


def write_host_sources(tmp_path, value):
    path = tmp_path/'host-sources.json'
    data = json.dumps(value).encode()
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()


def test_native_cli_source_input_keeps_requested_refs_and_exact_commit(tmp_path, native_probe, cli_sources):
    path, digest = write_cli_sources(tmp_path, cli_sources)
    assert native_probe.read_cli_sources(path, digest) == cli_sources
    with pytest.raises(ValueError, match='digest differs'):
        native_probe.read_cli_sources(path, '0'*64)
    cli_sources['sources'][0]['commit'] = 'b'*40
    path.write_text(json.dumps(cli_sources))
    with pytest.raises(ValueError, match='digest differs'):
        native_probe.read_cli_sources(path, digest)


@pytest.mark.parametrize('change', [
    'missing', 'duplicate', 'moving', 'credentials', 'extra-key', 'wrong-ref',
    'store-relative', 'schema-bool', 'store-symlink',
])
def test_native_cli_source_input_rejects_incomplete_or_ambiguous_bindings(
        tmp_path, native_probe, cli_sources, change):
    if change == 'missing':
        cli_sources['sources'].pop()
    elif change == 'duplicate':
        cli_sources['sources'].append(dict(cli_sources['sources'][0]))
    elif change == 'moving':
        cli_sources['sources'][0]['commit'] = 'main'
    elif change == 'credentials':
        cli_sources['sources'][0]['url'] = 'https://token@github.com/microsoft/another'
    elif change == 'extra-key':
        cli_sources['allow_partial'] = True
    elif change == 'wrong-ref':
        cli_sources['sources'][0]['ref'] = 'HEAD'
    elif change == 'store-relative':
        cli_sources['store'] = 'relative'
    elif change == 'schema-bool':
        cli_sources['schemaVersion'] = True
    elif change == 'store-symlink':
        alias = tmp_path/'alias'; alias.symlink_to(cli_sources['store'], target_is_directory=True)
        cli_sources['store'] = str(alias)
    path, digest = write_cli_sources(tmp_path, cli_sources)
    with pytest.raises(ValueError):
        native_probe.read_cli_sources(path, digest)


def test_native_cli_source_input_rejects_duplicate_json_keys(tmp_path, native_probe, cli_sources):
    path, _ = write_cli_sources(tmp_path, cli_sources)
    data = path.read_bytes().replace(b'"schemaVersion": 1', b'"schemaVersion": 1, "schemaVersion": 1')
    path.write_bytes(data)
    with pytest.raises(ValueError, match='Ambiguous.*key'):
        native_probe.read_cli_sources(path, hashlib.sha256(data).hexdigest())


def test_native_cli_environment_is_fresh_and_blocks_git_not_only_uv(tmp_path, native_probe, monkeypatch):
    for key in ('HOME', 'AMPLIFIER_HOME', 'AMPLIFIER_SOURCE_STORE', 'AMPLIFIER_ALLOW_PARTIAL_BUNDLE',
                'AMPLIFIER_SKILLS_DIR', 'PYTHONPATH', 'OPENAI_API_KEY', 'GIT_CONFIG_COUNT'):
        monkeypatch.setenv(key, 'foreign')
    env = native_probe.cli_environment(tmp_path, '/owned/public-store')
    assert env['AMPLIFIER_HOME'] == str(tmp_path/'cli-home')
    assert env['HOME'] == str(tmp_path/'cli-base-home')
    assert env['AMPLIFIER_SOURCE_STORE'] == '/owned/public-store'
    assert env['UV_OFFLINE'] == 'true'
    assert env['GIT_CONFIG_COUNT'] == '1'
    assert env['GIT_CONFIG_KEY_0'] == 'protocol.allow'
    assert env['GIT_CONFIG_VALUE_0'] == 'never'
    assert env['PYTHONDONTWRITEBYTECODE'] == '1'
    assert not {'AMPLIFIER_ALLOW_PARTIAL_BUNDLE', 'AMPLIFIER_SKILLS_DIR', 'PYTHONPATH', 'OPENAI_API_KEY'} & env.keys()


@pytest.mark.asyncio
async def test_native_cli_missing_input_refuses_before_runtime_or_work(tmp_path, native_probe, monkeypatch):
    from types import SimpleNamespace
    async def unexpected(*args, **kwargs):
        pytest.fail('No subprocess may start before input validation')
    monkeypatch.setattr(native_probe.asyncio, 'create_subprocess_exec', unexpected)
    args = SimpleNamespace(root=tmp_path/'native', cli_sources=tmp_path/'absent',
                           cli_sources_sha256='0'*64)
    with pytest.raises(FileNotFoundError):
        await native_probe.main(args)
    assert not args.root.exists()


@pytest.mark.asyncio
async def test_native_cli_work_must_not_overlap_source_store(tmp_path, native_probe, cli_sources, host_sources):
    from types import SimpleNamespace
    path, digest = write_cli_sources(tmp_path, cli_sources)
    host_path, host_digest = write_host_sources(tmp_path, host_sources)
    root = Path(cli_sources['store'])/'work'
    with pytest.raises(ValueError, match='must be separate'):
        await native_probe.main(SimpleNamespace(root=root, cli_sources=path, cli_sources_sha256=digest,
                                               host_sources=host_path, host_sources_sha256=host_digest))
    assert not root.exists()


@pytest.mark.asyncio
async def test_native_cli_preparation_refuses_unisolated_environment_before_imports(
        tmp_path, native_probe, cli_sources, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', '/foreign/home')
    with pytest.raises(ValueError, match='isolated fixture environment'):
        await native_probe.prepare_cli(tmp_path, cli_sources, '0'*64)


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', [False, True])
async def test_native_cli_verifies_all_objects_before_normal_binding(
        tmp_path, native_probe, cli_sources, missing):
    events = []
    class Store:
        def verify(self, url, commit):
            events.append(('verify', url, commit))
            if missing and url == cli_sources['sources'][-1]['url']:
                raise ValueError('Missing or changed object')
            return tmp_path/url.rsplit('/', 1)[1]
        async def bind(self, cache, url, ref, commit):
            events.append(('bind', url, ref, commit))
            assert cache == tmp_path/'fresh/cache'
    if missing:
        with pytest.raises(ValueError, match='Missing or changed'):
            await native_probe.bind_cli_sources(Store(), cli_sources['sources'], tmp_path/'fresh/cache')
        assert all(event[0] == 'verify' for event in events)
    else:
        objects = await native_probe.bind_cli_sources(Store(), cli_sources['sources'], tmp_path/'fresh/cache')
        count = len(cli_sources['sources'])
        assert [event[0] for event in events] == ['verify']*count+['bind']*count
        assert set(objects) == {(row['url'], row['ref']) for row in cli_sources['sources']}


@pytest.mark.asyncio
async def test_native_cli_failed_preparation_cannot_start_either_runtime(
        tmp_path, native_probe, cli_sources, host_sources, monkeypatch):
    from types import SimpleNamespace
    path, digest = write_cli_sources(tmp_path, cli_sources)
    host_path, host_digest = write_host_sources(tmp_path, host_sources)
    args = SimpleNamespace(root=tmp_path/'native', cli_sources=path, cli_sources_sha256=digest,
                           host_sources=host_path, host_sources_sha256=host_digest)
    for key in ('CLI_PYTHON', 'AMPLIFIER_STATUS_DIR', 'AMPLIFIER_HOME',
                'AMPLIFIER_WEB_HOME', 'AMPLIFIER_SESSION_STATE_HOME', 'AMPLIFIER_SOURCE_STORE',
                'PYTHONDONTWRITEBYTECODE', 'UV_OFFLINE', 'GIT_CONFIG_NOSYSTEM',
                'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0',
                'GIT_CONFIG_VALUE_0', 'GIT_TERMINAL_PROMPT'):
        monkeypatch.setenv(key, 'test-original')
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(sys, 'dont_write_bytecode', sys.dont_write_bytecode)
    async def prepared_host(root, inputs):
        return Path(inputs['store'])/'exact-object'
    monkeypatch.setattr(native_probe, 'prepare_host_sources', prepared_host)
    calls = []
    class Process:
        pid = 123
        def __init__(self, code):
            self.returncode = code
        async def communicate(self):
            return b'/installed/amplifier_status\n', b''
        async def wait(self):
            return self.returncode
    async def start(*command, **kwargs):
        calls.append((command, kwargs))
        if len(calls) == 1:
            assert command[1:3] == ('-I', '-c')
            return Process(0)
        assert len(calls) == 2, 'No CLI registration/run/consumer may start'
        assert '--prepare-cli' in command
        assert not kwargs.get('start_new_session', False)
        assert kwargs['env']['GIT_CONFIG_VALUE_0'] == 'never'
        return Process(1)
    monkeypatch.setattr(native_probe.asyncio, 'create_subprocess_exec', start)
    with pytest.raises(ValueError, match='CLI prepared inputs refused'):
        await native_probe.main(args)
    assert len(calls) == 2
    result = json.loads((args.root/'result.json').read_text())
    assert result['passed'] is False and result['phase'] == 'cli_preparation'
    assert result['timing']['firstCliTool'] is None
    assert result['cleanupCompleted'] is False
    assert not (args.root/'app').exists()


def test_native_cli_approval_binding_matches_foundation_omitted_ref(native_probe, cli_sources):
    from amplifier_foundation.paths.resolution import parse_uri
    uri = 'git+https://github.com/microsoft/amplifier-module-hooks-approval'
    parsed = parse_uri(uri)
    row = next(row for row in cli_sources['sources'] if row['url'] == uri.removeprefix('git+'))
    assert row['ref'] == parsed.ref == 'main'


def test_native_cli_local_admission_preserves_packaged_resource_root(tmp_path, native_probe):
    from amplifier_foundation.paths.resolution import parse_uri
    package = tmp_path/'amplifier_app_cli'
    bundle = package/'_bundle'
    for relative in ('behaviors/cli-expertise.yaml', 'context/cli-awareness.md',
                     'agents/cli-expert.md', 'agents/cli-doctor.md'):
        path = bundle/relative; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('packaged resource')
    def contained(path):
        path = path.resolve(strict=True)
        assert path.is_relative_to(package)
        return path, package
    uri = bundle.as_uri()+'#subdirectory=behaviors/cli-expertise.yaml'
    active, origin = native_probe.local_cli_source(parse_uri(uri), tmp_path, contained)
    assert active == bundle/'behaviors/cli-expertise.yaml'
    assert origin == bundle
    assert all((origin/path).is_file() for path in (
        'context/cli-awareness.md', 'agents/cli-expert.md', 'agents/cli-doctor.md'))


def test_native_cli_outer_qualifier_refuses_missing_inputs_before_effects(tmp_path, monkeypatch):
    monkeypatch.setattr(owner.socket, 'gethostname', lambda: 'owned-dtu')
    work = tmp_path/'work'
    argv = ['--owned-dtu', 'owned-dtu', '--python', 'unused', '--cli-python', 'unused',
            '--unified-wheel', 'absent', '--status-wheel', 'absent', '--core-wheel', 'absent',
            '--work', str(work)]
    with pytest.raises(SystemExit):
        owner.main(argv)
    assert not work.exists()
    with pytest.raises(FileNotFoundError):
        owner.main([*argv, '--cli-sources', str(tmp_path/'absent.json'), '--cli-sources-sha256', '0'*64,
                    '--host-sources', str(tmp_path/'host-absent.json'), '--host-sources-sha256', '0'*64])
    assert not work.exists()


@pytest.mark.parametrize('exit_mode', ['timeout', 'failed', 'success'])
def test_native_cli_outer_owner_settles_preparation_descendant(tmp_path, exit_mode):
    import os
    import subprocess
    import time
    if not sys.platform.startswith('linux'):
        pytest.skip('Owned status lane uses Linux process containment')
    pidfile = tmp_path/'child.pid'
    # Model the real nesting: native fixture -> run_cli_preparation -> Git-like
    # helper. The helper must inherit the group that run_owned contains.
    command = [sys.executable, '-I', '-S', '-c',
               'import asyncio,importlib.util,os,pathlib,sys; '
               'spec=importlib.util.spec_from_file_location("probe",sys.argv[1]); '
               'probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe); '
               'child="import os,pathlib,subprocess,sys,time; '
               'p=subprocess.Popen([sys.executable,\\\"-I\\\",\\\"-S\\\",\\\"-c\\\",\\\"import time; time.sleep(60)\\\"]); '
               'pathlib.Path(sys.argv[1]).write_text(str(p.pid)); '
               'time.sleep(60) if sys.argv[2]==\\\"timeout\\\" else None; '
               'sys.exit(1 if sys.argv[2]==\\\"failed\\\" else 0)"; '
               'code=asyncio.run(probe.run_cli_preparation('
               '[sys.executable,"-I","-S","-c",child,sys.argv[2],sys.argv[3]],'
               'cwd=pathlib.Path.cwd(),env=dict(os.environ),log=asyncio.subprocess.DEVNULL,timeout=60)); '
               'sys.exit(code)',
               str(ROOT/'tests/fixtures/status_emitter_probe.py'), str(pidfile), exit_mode]
    if exit_mode == 'timeout':
        with pytest.raises(subprocess.TimeoutExpired):
            owner.run_owned(command, cwd=tmp_path, env=dict(os.environ),
                            log=subprocess.DEVNULL, timeout=3)
    else:
        assert owner.run_owned(command, cwd=tmp_path, env=dict(os.environ),
                               log=subprocess.DEVNULL, timeout=5) == (1 if exit_mode == 'failed' else 0)
    assert pidfile.exists(), 'Helper did not start its descendant'
    pid = int(pidfile.read_text())
    def running():
        try:
            return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
        except FileNotFoundError:
            return False
    deadline = time.monotonic()+5
    while running() and time.monotonic() < deadline:
        time.sleep(.01)
    assert not running(), 'Preparation descendant escaped outer cleanup'


def cli_tool_row():
    return {
        'producer': {'host': 'cli', 'pid': 123, 'instanceId': 'private-instance'},
        'where': {'sessionId': 'private-session', 'cwd': 'PRIVATE workspace'},
        'lifecycle': 'live', 'freshness': 'live', 'observedAt': 100.15, 'sequence': 4,
        'now': {'status': 'tool', 'tools': [
            {'name': 'status_hold', 'id': 'hold-call', 'startedAt': 100.05}]},
        'plan': {'text': 'PRIVATE plan label'},
    }


@pytest.mark.parametrize('later_publication', [100.3, 102.15], ids=['dirty-update', 'heartbeat'])
def test_native_first_observation_survives_later_publications(native_probe, later_publication):
    row = cli_tool_row()
    first = native_probe.cli_tool_sample([row], 123, 99., 100.2, 20.)
    assert first['sequence'] == 4 and first['publishedAt'] == 100.15
    # Mutation of the source row must not mutate the captured numeric sample.
    row['sequence'] = 5
    row['observedAt'] = later_publication
    later = native_probe.cli_tool_sample([row], 123, 99., later_publication+.05, 22.)
    assert later['sequence'] == 5 and later['publishedAt'] == later_publication
    assert native_probe.cli_tool_sample(
        [row], 123, 99., later_publication+.05, 22., first) is first
    timing = {'firstCliTool': first, 'toolEntryAt': 100.1}
    assert native_probe.check_cli_tool_latency(timing) == pytest.approx(.1)
    # The later snapshot remains useful for the independent consumer assertion,
    # never as a replacement for the first observation's latency.
    assert later['identitySha256'] == first['identitySha256']
    assert first['receivedAt'] == 100.2 and first['sequence'] == 4
    assert 'PRIVATE' not in json.dumps(first)
    assert 'private-instance' not in json.dumps(first)
    assert 'private-session' not in json.dumps(first)


@pytest.mark.parametrize('received,entered', [
    (101.1, 100.1), (102., 100.1), (100.2, 100.3),
], ids=['one-second-boundary', 'late-first-even-with-early-publication', 'negative-lag'])
def test_native_first_observation_same_budget_rejects_late_or_negative(native_probe, received, entered):
    first = native_probe.cli_tool_sample([cli_tool_row()], 123, 99., received, 20.)
    assert first['publishedAt'] == 100.15  # An early publisher stamp cannot rescue a slow read.
    timing = {'firstCliTool': first, 'toolEntryAt': entered}
    with pytest.raises(AssertionError):
        native_probe.check_cli_tool_latency(timing)
    assert timing['cliToolObservationSeconds'] == received-entered


def test_native_first_observation_cannot_resample_a_failed_first_read(native_probe):
    row = cli_tool_row()
    first = native_probe.cli_tool_sample([row], 123, 99., 102., 20.)
    timing = {'firstCliTool': first, 'toolEntryAt': 100.1}
    with pytest.raises(AssertionError):
        native_probe.check_cli_tool_latency(timing)
    # Even a later caller supplying a favorable clock/sample cannot overwrite it.
    row['sequence'] += 1
    same = native_probe.cli_tool_sample([row], 123, 99., 100.2, 21., first)
    assert same is first and same['receivedAt'] == 102.
    with pytest.raises(AssertionError):
        native_probe.check_cli_tool_latency({'firstCliTool': same, 'toolEntryAt': 100.1})


def test_native_first_observation_marker_read_waits_for_cli_exit(tmp_path, native_probe):
    from types import SimpleNamespace
    cli = SimpleNamespace(pid=123, returncode=None)
    marker = tmp_path/'tool-started-123.json'
    marker.write_text('{"at":')  # The real Path.write_text writer has not closed yet.
    first = native_probe.cli_tool_sample([cli_tool_row()], cli.pid, 99., 100.2, 20.)
    timing = {'firstCliTool': first, 'toolEntryAt': None}
    with pytest.raises(AssertionError, match='still running'):
        native_probe.record_cli_tool_entry(tmp_path, cli, timing)
    assert timing['firstCliTool'] is first and timing['toolEntryAt'] is None
    marker.write_text('{"at": 100.1}')
    cli.returncode = 0
    native_probe.record_cli_tool_entry(tmp_path, cli, timing)
    assert timing['firstCliTool'] is first
    assert first['receivedAt'] == 100.2
    assert native_probe.check_cli_tool_latency(timing) == pytest.approx(.1)


@pytest.mark.parametrize('change', [
    'pid', 'pid-type', 'host', 'name', 'call-id', 'stale', 'ended', 'status',
    'old-publication', 'old-tool', 'future-publication', 'nan-tool',
])
def test_native_first_observation_wrong_identity_or_stale_row_cannot_satisfy(native_probe, change):
    row = cli_tool_row()
    if change == 'pid':
        row['producer']['pid'] = 124
    elif change == 'pid-type':
        row['producer']['pid'] = 123.
    elif change == 'host':
        row['producer']['host'] = 'unified'
    elif change == 'name':
        row['now']['tools'][0]['name'] = 'other'
    elif change == 'call-id':
        row['now']['tools'][0]['id'] = 'old-hold-call'
    elif change == 'stale':
        row['freshness'] = 'stale'
    elif change == 'ended':
        row['lifecycle'] = 'ended'
    elif change == 'status':
        row['now']['status'] = 'thinking'
    elif change == 'old-publication':
        row['observedAt'] = 98.
    elif change == 'old-tool':
        row['now']['tools'][0]['startedAt'] = 98.
    elif change == 'future-publication':
        row['observedAt'] = 101.
    elif change == 'nan-tool':
        row['now']['tools'][0]['startedAt'] = float('nan')
    assert native_probe.cli_tool_sample([row], 123, 99., 100.2, 20.) is None
    with pytest.raises(AssertionError, match='No first CLI'):
        native_probe.check_cli_tool_latency({'firstCliTool': None, 'toolEntryAt': 100.1})


def test_native_first_observation_ambiguous_rows_are_not_cherry_picked(native_probe):
    rows = [cli_tool_row(), cli_tool_row()]
    rows[1]['where']['sessionId'] = 'another-session'
    with pytest.raises(AssertionError, match='Ambiguous'):
        native_probe.cli_tool_sample(rows, 123, 99., 100.2, 20.)


@pytest.mark.parametrize('entry', [None, float('nan'), float('inf'), True, 98.])
def test_native_first_observation_requires_current_numeric_tool_entry(native_probe, entry):
    first = native_probe.cli_tool_sample([cli_tool_row()], 123, 99., 100.2, 20.)
    with pytest.raises(AssertionError):
        native_probe.check_cli_tool_latency({'firstCliTool': first, 'toolEntryAt': entry})


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['success', 'late', 'private-error', 'cleanup-error'])
async def test_native_first_observation_receipt_survives_failure_without_private_text(
        tmp_path, native_probe, cli_sources, host_sources, monkeypatch, outcome):
    from types import SimpleNamespace
    inputs, digest = write_cli_sources(tmp_path, cli_sources)
    host_path, host_digest = write_host_sources(tmp_path, host_sources)
    args = SimpleNamespace(root=tmp_path/'native', cli_sources=inputs, cli_sources_sha256=digest,
                           host_sources=host_path, host_sources_sha256=host_digest)
    async def run_probe(args, root, inputs, host_inputs, result):
        result['phase'] = 'first_observation_assertion'
        timing = result['timing']
        timing['phases']['first_observation_assertion'] = 20.
        timing['firstCliTool'] = native_probe.cli_tool_sample(
            [cli_tool_row()], 123, 99., 102. if outcome == 'late' else 100.2, 20.)
        timing['toolEntryAt'] = 100.1
        if outcome == 'private-error':
            raise ValueError('PRIVATE prompt, credential, exception body')
        native_probe.check_cli_tool_latency(timing)
        result['passed'] = True
        if outcome == 'cleanup-error':
            result['consumer'] = {'text': 'PRIVATE success-only row'}
            result['cliPreparation'] = {'text': 'PRIVATE preparation detail'}
            raise ValueError('PRIVATE cleanup failure')
        result['cleanupCompleted'] = True
    monkeypatch.setattr(native_probe, 'run_probe', run_probe)
    if outcome == 'success':
        await native_probe.main(args)
    else:
        with pytest.raises(AssertionError if outcome == 'late' else ValueError):
            await native_probe.main(args)
    raw = (args.root/'result.json').read_text()
    result = json.loads(raw)
    assert result['passed'] is (outcome == 'success')
    assert result['cleanupCompleted'] is (outcome == 'success')
    assert result['phase'] == 'first_observation_assertion'
    assert result['timing']['firstCliTool']['sequence'] == 4
    assert result['timing']['toolEntryAt'] == 100.1
    assert result['timing']['phases'] == {'first_observation_assertion': 20.}
    if outcome == 'late':
        assert result['timing']['cliToolObservationSeconds'] == pytest.approx(1.9)
    assert 'PRIVATE' not in raw and 'private-instance' not in raw and 'private-session' not in raw
    assert 'consumer' not in result
    assert 'cliPreparation' not in result


def test_native_host_input_binds_exact_commit_and_digest(tmp_path, native_probe, host_sources):
    path, digest = write_host_sources(tmp_path, host_sources)
    assert native_probe.read_host_sources(path, digest) == host_sources
    assert native_probe.host_settings(['optional'], host_sources) == {
        'bundle': {'app': []}, 'web_bundles': {'excluded': ['optional']},
        'sources': {'modules': {'hook-context-intelligence':
            'git+'+native_probe.CAPTURE_REPOSITORY+'@'+'a'*40
            +'#subdirectory=modules/hook-context-intelligence'}}}
    with pytest.raises(ValueError, match='digest'):
        native_probe.read_host_sources(path, '0'*64)


@pytest.mark.parametrize('change', ['moving', 'extra', 'schema', 'relative', 'duplicate', 'oversize'])
def test_native_host_input_refuses_ambiguous_or_unpinned_sources(tmp_path, native_probe, host_sources, change):
    if change == 'moving':
        host_sources['captureCommit'] = 'main'
    elif change == 'extra':
        host_sources['url'] = 'https://unreviewed.invalid'
    elif change == 'schema':
        host_sources['schemaVersion'] = True
    elif change == 'relative':
        host_sources['store'] = 'relative'
    path, digest = write_host_sources(tmp_path, host_sources)
    if change in {'duplicate', 'oversize'}:
        data = path.read_bytes()
        data = (data.replace(b'"schemaVersion": 1', b'"schemaVersion": 1, "schemaVersion": 1')
                if change == 'duplicate' else data+b' '*65536)
        path.write_bytes(data); digest = hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError):
        native_probe.read_host_sources(path, digest)


@pytest.mark.parametrize('placement', ['same-store', 'nested-store', 'work-in-host'])
def test_native_host_input_store_separation(tmp_path, native_probe, cli_sources, host_sources, placement):
    root = tmp_path/'native'
    if placement == 'same-store':
        host_sources['store'] = cli_sources['store']
    elif placement == 'nested-store':
        host_sources['store'] = str(Path(cli_sources['store'])/'host')
    else:
        root = Path(host_sources['store'])/'native'
    with pytest.raises(ValueError, match='separate'):
        native_probe.separate_source_work(root, cli_sources, host_sources)


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [None, 'bundle-pin', 'hook-pin', 'object'])
async def test_native_host_prepare_verifies_installed_graph_before_exact_binding(
        tmp_path, native_probe, host_sources, monkeypatch, bad):
    import importlib.metadata
    from types import SimpleNamespace
    import amplifier_foundation.sources.shared as shared
    from amplifier_web.host import config
    calls = []
    def distribution(name):
        hook = name == 'amplifier-module-hook-context-intelligence'
        commit = 'b'*40 if bad == ('hook-pin' if hook else 'bundle-pin') else 'a'*40
        direct = {'url': native_probe.CAPTURE_REPOSITORY,
                  'vcs_info': {'vcs': 'git', 'commit_id': commit}}
        if hook:
            direct['subdirectory'] = 'modules/hook-context-intelligence'
        return SimpleNamespace(read_text=lambda name: json.dumps(direct))
    monkeypatch.setattr(importlib.metadata, 'distribution', distribution)
    registry = tmp_path/'actual-registry'
    monkeypatch.setattr(config, 'read_config', lambda *a, **kw: SimpleNamespace(
        registry_home=registry, module_sources=native_probe.host_settings([], host_sources)['sources']['modules']))
    class Store:
        def __init__(self, path):
            assert path == host_sources['store']
        def verify(self, url, commit):
            calls.append('verify')
            assert url == native_probe.CAPTURE_REPOSITORY and commit == 'a'*40
            if bad == 'object':
                raise ValueError('Missing complete object')
            return tmp_path/'complete-object'
        async def bind(self, cache, url, ref, commit):
            assert calls == ['verify']
            assert cache == registry/'cache' and ref == commit == 'a'*40
            calls.append('bind')
    monkeypatch.setattr(shared, 'SharedSourceStore', Store)
    if bad:
        with pytest.raises(ValueError if bad == 'object' else AssertionError):
            await native_probe.prepare_host_sources(tmp_path, host_sources)
        assert 'bind' not in calls
    else:
        assert await native_probe.prepare_host_sources(tmp_path, host_sources) == tmp_path/'complete-object'
        assert calls == ['verify', 'bind']


@pytest.mark.asyncio
async def test_native_host_missing_input_refuses_before_work(tmp_path, native_probe, cli_sources):
    from types import SimpleNamespace
    path, digest = write_cli_sources(tmp_path, cli_sources)
    args = SimpleNamespace(root=tmp_path/'native', cli_sources=path, cli_sources_sha256=digest,
                           host_sources=tmp_path/'absent', host_sources_sha256='0'*64)
    with pytest.raises(FileNotFoundError):
        await native_probe.main(args)
    assert not args.root.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [
    None, 'changed-worker', 'session', 'unstable', 'unknown', 'absent-ci',
    'file-unknown', 'hash', 'record', 'foreign-ci', 'ready-outside', 'duplicate-stamp',
])
async def test_native_host_provenance_joins_current_worker_without_loading_modules(
        tmp_path, native_probe, monkeypatch, bad):
    import importlib.metadata
    import base64
    from types import SimpleNamespace
    root = tmp_path/'native'; root.mkdir()
    source = tmp_path/'source'; source.mkdir()
    capture = tmp_path/'capture'; capture.mkdir()
    prefix = tmp_path/'prefix'; prefix.mkdir()
    monkeypatch.setattr(native_probe, 'ROOT', source)
    monkeypatch.setattr(sys, 'prefix', str(prefix))
    records = {}
    def file(path):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixed source')
        return path
    origins = {
        '__main__': file(source/'amplifier_web/runtime_worker.py'),
        'amplifier_web': file(source/'amplifier_web/__init__.py'),
        'amplifier_core': file(prefix/'amplifier_core/__init__.py'),
        'amplifier_foundation': file(prefix/'amplifier_foundation/__init__.py'),
        native_probe.CAPTURE_MODULE: file(capture/'modules/hook-context-intelligence'/
                                         native_probe.CAPTURE_MODULE/'__init__.py'),
    }
    for path in origins.values():
        records[path] = base64.urlsafe_b64encode(hashlib.sha256(path.read_bytes()).digest()).decode().rstrip('=')
    dist = SimpleNamespace(files=[SimpleNamespace(hash=SimpleNamespace(mode='sha256', value=value), path=path)
                                  for path, value in records.items()],
                           locate_file=lambda item: item.path)
    if bad == 'record':
        next(item for item in dist.files if item.path == origins['amplifier_core']).hash.value = 'wrong'
    monkeypatch.setattr(importlib.metadata, 'distributions', lambda: [dist])
    monkeypatch.setattr(importlib.metadata, 'distribution', lambda name: SimpleNamespace(
        locate_file=lambda name: prefix/name))
    path = root/'settings-not-created.yaml'
    if bad == 'ready-outside':
        path = tmp_path/'outside'
    row = {'process': SimpleNamespace(pid=123, returncode=None), 'started_at': 5.,
           'runtime_id': 'runtime', 'sessionSettingsPath': str(path),
           'configurationStamp': [[str(path), None]]}
    if bad == 'duplicate-stamp':
        row['configurationStamp'].append([str(path), None])
    modules = []
    for name in dict.fromkeys([*native_probe.WORKER_MODULES, 'amplifier_core', 'amplifier_foundation']):
        item = {'module': name, 'loaded': name in origins, 'fileStatus': 'unknown',
                'importedBytesVerified': False}
        if name in origins:
            origin = origins[name]
            item.update(origin=str(origin), fileStatus='observed', bytes=origin.stat().st_size,
                        currentFileSha256=hashlib.sha256(origin.read_bytes()).hexdigest())
        modules.append(item)
    ci = next(item for item in modules if item['module'] == native_probe.CAPTURE_MODULE)
    if bad == 'absent-ci':
        ci['loaded'] = False
    elif bad == 'file-unknown':
        ci['fileStatus'] = 'unknown'
    elif bad == 'hash':
        ci['currentFileSha256'] = '0'*64
    elif bad == 'foreign-ci':
        ci['origin'] = str(file(root/'unexpected-real-source/__init__.py'))
    calls = []
    class Runtime:
        workers = {'sid': row}
        async def provenance(self, sid, modules=()):
            calls.append((sid, modules))
            return {'status': 'unknown' if bad == 'unknown' else 'observed',
                    'identityStable': bad != 'unstable', 'mounted': True,
                    'workerInstance': '999:5.0' if bad == 'changed-worker' else '123:5.0',
                    'runtimeSessionId': 'other' if bad == 'session' else 'runtime',
                    'modules': evidence}
    evidence = modules
    proof = {}
    if bad:
        with pytest.raises(AssertionError):
            await native_probe.observe_worker(Runtime(), 'sid', root, capture, proof)
        assert proof['passed'] is False
    else:
        await native_probe.observe_worker(Runtime(), 'sid', root, capture, proof)
        assert proof['passed'] and proof['readyStampMatches']
        assert proof['readySettingsSha256'] is None and not proof['readySettingsPresent']
        assert not proof['importedBytesVerified'] and not proof['exhaustive']
        assert next(item for item in proof['modules'] if item['module'] == native_probe.CAPTURE_MODULE)['originClass'] == 'capture'
        assert 'unexpected-real-source' not in json.dumps(proof)
    assert len(calls) == (0 if bad in {'ready-outside', 'duplicate-stamp'} else 1)
    if calls:
        assert calls == [('sid', native_probe.WORKER_MODULES)] and len(native_probe.WORKER_MODULES) <= 16
