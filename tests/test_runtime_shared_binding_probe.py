"""Shared binding identity survives adoption and the real update-probe child.

Synthetic modules, no model generation. The ordinary probe inherits prepared
test dependencies but installers target only its own venv. The opt-in full
worker check resolves the packaged graph through its isolated DTU transport.
"""
import asyncio
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tomllib
from types import SimpleNamespace

import pytest

from amplifier_web import builtin_behaviors, runtime_environment as environments
from amplifier_web import runtime_policy_reconciliation as policy
from amplifier_web import runtime_qualification as qualification
from amplifier_web import update_sources, updates
from amplifier_web.host.config import PRECONFIGURED_BUNDLES, read_config
from amplifier_web.runtime_source_alignment import verify_checked_selection
from test_runtime_checked_cache import BACKEND, Diagnostics, git

URL = 'https://example.invalid/shared-hook'
NAME = 'fixture-shared-hook'
SUBDIRECTORY = 'modules/hook-fixture'
GENERATION = 'e' * 32


@pytest.fixture
async def shared_source(tmp_path, monkeypatch):
    if os.environ.get('FOUNDATION_INTEGRATION_REVISION'):
        import amplifier_foundation.sources.shared as implementation
        assert hashlib.sha256(Path(implementation.__file__).read_bytes()).hexdigest() == (
            os.environ['FOUNDATION_INTEGRATION_SHARED_SHA256'])
        direct = json.loads(metadata.distribution('amplifier-foundation').read_text('direct_url.json'))
        assert direct['vcs_info']['commit_id'] == os.environ['FOUNDATION_INTEGRATION_REVISION']
        assert direct['url'].removesuffix('.git') == 'https://github.com/microsoft/amplifier-foundation'
    from amplifier_foundation.sources.shared import SharedSourceStore
    home = tmp_path / 'app'
    stage = environments.receipt_directory(home, GENERATION)
    root = stage / 'foundation/cache/legacy'
    root.mkdir(parents=True)
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    git(root, 'remote', 'add', 'origin', URL + '.git')
    module = root / SUBDIRECTORY
    module.mkdir(parents=True)
    (module / 'pyproject.toml').write_text(
        '[project]\nname="' + NAME + '"\nversion="1.0"\ndependencies=[]\n'
        '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n')
    (module / 'backend.py').write_text(BACKEND)
    (root / 'bundle.md').write_text('---\nbundle:\n  name: shared-hook\n---\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'synthetic shared hook')
    revision = git(root, 'rev-parse', 'HEAD')
    (root / '.amplifier_cache_meta.json').write_text(json.dumps({
        'git_url': URL, 'ref': 'main', 'commit': revision}))
    store = SharedSourceStore(home / 'source-store')
    monkeypatch.setenv('AMPLIFIER_SOURCE_STORE', str(store.root))
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    # Seed the immutable object with .git. Adoption of the equivalent legacy
    # checkout overwrites this same canonical binding with the non-.git spelling.
    obj = await store.bind(stage / 'foundation/cache', URL + '.git', 'main', revision, existing=root)
    immutable = (obj / '.amplifier_cache_meta.json').read_bytes()
    manager = SimpleNamespace(home=home, diagnostics=Diagnostics())
    await update_sources.adopt_clean_sources(manager, stage)
    assert not root.exists()
    binding = next((stage / 'foundation/cache/.source-bindings').glob('*.json'))
    assert json.loads(binding.read_text()) == {'git_url': URL, 'ref': 'main', 'commit': revision}
    assert (obj / '.amplifier_cache_meta.json').read_bytes() == immutable
    config = read_config(stage, home=stage, shared_home=stage / 'shared-config', global_only=True)
    collector = policy.Collector()
    collector.observe('git+' + URL + '.git@main', config)
    batch = SimpleNamespace(names={NAME: obj / SUBDIRECTORY})
    checked = {NAME: {'url': URL + '.git', 'ref': 'main', 'revision': revision,
                      'subdirectory': SUBDIRECTORY}}
    return SimpleNamespace(home=home, stage=stage, obj=obj, revision=revision, store=store,
        manager=manager, binding=binding, immutable=immutable, config=config,
        collector=collector, batch=batch, checked=checked)


@pytest.mark.parametrize('binding_suffix,object_suffix', [('', '.git'), ('.git', ''), ('.git', '.git'), ('', '')])
async def test_shared_binding_uses_canonical_repository_identity(shared_source, monkeypatch, binding_suffix, object_suffix):
    c = shared_source
    # Exercise both insertion and lookup normalization, not only one direction.
    record = json.loads(c.binding.read_text())
    record['git_url'] = URL + binding_suffix
    c.binding.write_text(json.dumps(record))
    if not object_suffix:
        # A second real store publication supplies the opposite metadata spelling.
        other = c.home / 'opposite-store'
        from amplifier_foundation.sources.shared import SharedSourceStore
        store = SharedSourceStore(other)
        obj = await store.bind(c.stage / 'foundation/cache', URL, 'main', c.revision, existing=c.obj)
        monkeypatch.setenv('AMPLIFIER_SOURCE_STORE', str(other))
        c.batch.names[NAME] = obj / SUBDIRECTORY
        c.binding.write_text(json.dumps(record))
    before = c.binding.read_bytes()
    report = await c.collector.report(c.batch, c.config)
    verify_checked_selection(c.checked, report, exact=True)
    assert report['packages'][NAME]['status'] == 'selected'
    assert c.binding.read_bytes() == before
    assert (c.obj / '.amplifier_cache_meta.json').read_bytes() == c.immutable


@pytest.mark.parametrize('change', ['host', 'repository', 'commit', 'ref', 'missing', 'subdirectory', 'local', 'dirty'])
async def test_shared_binding_cannot_authorize_another_identity(shared_source, change):
    c = shared_source
    data = json.loads(c.binding.read_text())
    if change == 'host': data['git_url'] = URL.replace('example.invalid', 'other.invalid')
    elif change == 'repository': data['git_url'] = URL + '-other.git'
    elif change == 'commit': data['commit'] = 'f' * 40
    elif change == 'ref': data['ref'] = 'stable'
    elif change == 'subdirectory': c.checked[NAME]['subdirectory'] = 'another-module'
    elif change == 'local': c.collector.local_sources.add(c.obj / SUBDIRECTORY)
    elif change == 'dirty':
        path = c.obj / SUBDIRECTORY / 'pyproject.toml'
        path.chmod(0o644)
        path.write_text(path.read_text() + '\n# protected edit\n')
    c.binding.write_text(json.dumps(data))
    before = c.binding.read_bytes()
    if change == 'missing':
        c.binding.unlink()
    if change == 'dirty':
        with pytest.raises(ValueError, match='changed'):
            await c.collector.report(c.batch, c.config)
    else:
        report = await c.collector.report(c.batch, c.config)
        with pytest.raises(environments.ProtectedRuntimeSource):
            verify_checked_selection(c.checked, report, exact=True)
    assert not c.binding.exists() if change == 'missing' else c.binding.read_bytes() == before


@pytest.mark.parametrize('choice', ['equivalent', 'equivalent-runtime-partial',
                                  'host', 'repository', 'commit', 'ref', 'subdirectory'])
async def test_real_probe_installs_checked_shared_hook_after_adoption_and_freezes(
        shared_source, tmp_path, monkeypatch, record_property, request, choice):
    c = shared_source
    endpoints = choice == 'equivalent-runtime-partial'
    if endpoints and (os.name != 'posix' or not hasattr(socket, 'AF_UNIX')):
        pytest.skip('Real Unix socket/FIFO build input requires POSIX')
    endpoint = None
    if endpoints:
        from amplifier_foundation.sources.shared import resolve_shared_source, source_id
        resolved = await resolve_shared_source(
            'git+' + URL + '.git@main#subdirectory=' + SUBDIRECTORY,
            c.stage / 'foundation/cache')
        assert resolved.active_path == c.obj / SUBDIRECTORY
        # Runtime Git metadata can contain endpoints even in an otherwise
        # immutable checkout. Never add them to tracked module content.
        git_dir = c.obj / '.git'
        mode = stat.S_IMODE(git_dir.stat().st_mode)
        git_dir.chmod(mode | 0o700)
        endpoint = socket.socket(socket.AF_UNIX)
        request.addfinalizer(endpoint.close)
        # Linux sockaddr_un is short; a relative bind still creates a real
        # endpoint in the long commit-addressed object path.
        previous = Path.cwd()
        try:
            os.chdir(git_dir)
            endpoint.bind('fsmonitor.sock')
            os.mkfifo('fsmonitor.fifo')
        finally:
            os.chdir(previous)
            git_dir.chmod(mode)
        build = c.store.root / 'builds' / source_id(URL + '.git', c.revision)
        (build / '.git').mkdir(parents=True)
        (build / '.amplifier_cache_meta.json').write_bytes(c.immutable)
        residue = build / 'unrelated-local-work'
        residue.write_bytes(b'preserve attributable partial contents\n')
        residue.chmod(0o440)
        partial_before = residue.read_bytes(), stat.S_IMODE(residue.stat().st_mode)
        # Include Git bytes and every mode, without opening socket/FIFO nodes.
        def inventory():
            return {str(path.relative_to(c.obj)):
                    (stat.S_IMODE(path.lstat().st_mode),
                     hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
                    for path in [c.obj, *c.obj.rglob('*')]}
        object_before = inventory()
    assert sys.version_info[:2] == (3, 13)
    uv = shutil.which('uv')
    assert uv, 'Requires the prepared Python 3.13 Docker fixture'
    for key in ('UV_CONFIG_FILE', 'UV_OVERRIDE', 'UV_CONSTRAINT', 'UV_BUILD_CONSTRAINT',
                'UV_PROJECT_ENVIRONMENT', 'UV_OFFLINE'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('UV_NO_INDEX', '1')
    monkeypatch.setenv('UV_CACHE_DIR', str(tmp_path / 'uv-cache'))
    monkeypatch.setenv('UV_PYTHON_DOWNLOADS', 'never')
    # The existing prepared interpreter supplies Foundation/host dependencies;
    # no fixture install targets it. The hook's one batch install is isolated.
    project = c.home / 'runtime/preparation'
    project.mkdir(parents=True)
    manifest = tmp_path / 'worker.toml'
    manifest.write_text('[project]\nname="probe-fixture"\nversion="1"\nrequires-python=">=3.13"\ndependencies=[]\n')
    monkeypatch.setattr(environments, 'manifest_path', lambda: manifest)
    (project / 'pyproject.toml').write_bytes(manifest.read_bytes())
    await updates.process(uv, 'lock', '--project', str(project), '--python', sys.executable)
    await updates.process(uv, 'venv', '--system-site-packages', '--python', sys.executable, str(project / '.venv'))
    # uv's system-site-packages means base CPython, not the invoking prepared
    # venv. Reuse those explicit dependency paths in this disposable interpreter.
    import site
    candidate_site = next((project / '.venv/lib').glob('python*/site-packages'))
    (candidate_site / 'prepared-dependencies.pth').write_text('\n'.join(site.getsitepackages()) + '\n')
    workspace = c.stage / 'qualification-workspace'
    workspace.mkdir()
    shared = c.stage / 'shared-config'
    shared.mkdir(exist_ok=True)
    local = tmp_path / 'local-modules'
    for name in ('context-fixture', 'provider-fixture'):
        folder = local / name
        folder.mkdir(parents=True)
        (folder / '__init__.py').write_text('# Dependency-free preparation-only fixture\n')
    profile = workspace / 'profile.yaml'
    from amplifier_web.bundles import SNAPSHOT_VERSION
    profile.write_text(json.dumps({
        'bundle': {'name': 'fixture', 'version': SNAPSHOT_VERSION},
        'session': {'orchestrator': {'module': 'loop-streaming'},
                    'context': {'module': 'context-fixture', 'source': str(local / 'context-fixture')}},
        'providers': [{'module': 'provider-fixture', 'source': str(local / 'provider-fixture')}],
        'hooks': [{'module': 'hook-fixture', 'source': 'git+' + URL + '.git@main#subdirectory=' + SUBDIRECTORY}],
    }))
    settings = {'bundle': {'active': 'fixture', 'added': {'fixture': str(profile)}},
                'web_bundles': {'excluded': list(builtin_behaviors.DEFAULT_BEHAVIORS),
                    'entries': [{'name': name, 'uri': uri, 'role': 'standalone', 'enabled': False}
                                for name, uri in PRECONFIGURED_BUNDLES.items()]}}
    (shared / 'settings.yaml').write_text(json.dumps(settings))
    profiles = c.stage / 'profiles.json'
    profiles.write_text('["fixture"]')
    checked = c.stage / 'runtime-sources.json'
    if choice == 'host': c.checked[NAME]['url'] = URL.replace('example.invalid', 'other.invalid')
    elif choice == 'repository': c.checked[NAME]['url'] = URL + '-other'
    elif choice == 'commit': c.checked[NAME]['revision'] = 'f' * 40
    elif choice == 'ref': c.checked[NAME]['ref'] = 'stable'
    elif choice == 'subdirectory': c.checked[NAME]['subdirectory'] = 'another-module'
    checked.write_text(json.dumps(c.checked))
    overrides = c.stage / 'install-overrides.txt'
    overrides.write_text('')
    env = {**os.environ, 'AMPLIFIER_WEB_HOME': str(c.stage), 'AMPLIFIER_HOME': str(shared),
           'AMPLIFIER_UNIFIED_RELEASE': '', 'AMPLIFIER_INSTALL_PREPARATION': 'shared-binding-fixture',
           'AMPLIFIER_UPDATE_ATTEMPT': 'shared-binding-fixture'}
    before = {c.binding: c.binding.read_bytes(), checked: checked.read_bytes(),
              c.obj / '.amplifier_cache_meta.json': c.immutable}
    probe = Path(updates.__file__).with_name('update_probe.py')
    command = [str(project / '.venv/bin/python'), str(probe), str(workspace), 'fixture',
               '--profiles', str(profiles), '--checked-sources', str(checked),
               '--install-overrides', str(overrides), '--global-only']
    record_property('probeCommand', json.dumps(command))
    identity = subprocess.check_output([command[0], '-c',
        'import importlib.metadata as m,hashlib,json,sys; from pathlib import Path; '
        'import amplifier_foundation; import amplifier_foundation.sources.shared as s; '
        'print(json.dumps({"python":sys.version,"executable":sys.executable,'
        '"foundation":m.version("amplifier-foundation"),"foundationPath":amplifier_foundation.__file__,'
        '"sharedSha256":hashlib.sha256(Path(s.__file__).read_bytes()).hexdigest(),'
        '"origins":{n:json.loads(m.distribution(n).read_text("direct_url.json")) for n in '
        '("amplifier-foundation","amplifier-module-hooks-session-naming")}}))'],
        text=True, env=env)
    record_property('probeIdentity', identity.strip())
    expected = os.environ.get('FOUNDATION_INTEGRATION_REVISION')
    if expected:
        facts = json.loads(identity)
        assert facts['sharedSha256'] == os.environ['FOUNDATION_INTEGRATION_SHARED_SHA256']
        for name, direct in facts['origins'].items():
            assert direct['url'].removesuffix('.git') == 'https://github.com/microsoft/amplifier-foundation'
            assert direct['vcs_info']['commit_id'] == expected
            assert direct.get('subdirectory', '') == (
                'modules/hooks-session-naming' if name.endswith('session-naming') else '')
    record_property('unifiedSource', str(probe))
    # The actual child must encounter different raw URL spellings, not a test
    # that accidentally normalizes both records before reaching the collector.
    assert json.loads(c.binding.read_text())['git_url'] == URL
    assert json.loads(c.immutable)['git_url'] == URL + '.git'
    payload_before = qualification.package_content(project)
    result = await asyncio.to_thread(subprocess.run, command, env=env,
        text=True, capture_output=True, timeout=90)
    record_property('probeStdout', result.stdout)
    record_property('probeStderr', result.stderr)
    from amplifier_web.update_diagnostics import PROBE_PREFIX
    facts = [json.loads(line.removeprefix(PROBE_PREFIX)) for line in result.stdout.splitlines()
             if line.startswith(PROBE_PREFIX)][-1]
    assert {path: path.read_bytes() for path in before} == before
    if choice not in {'equivalent', 'equivalent-runtime-partial'}:
        assert result.returncode == 1, result.stdout + result.stderr
        assert not facts['ok'] and facts['errorType'] == 'ProtectedRuntimeSource'
        assert qualification.package_content(project) == payload_before
        assert not qualification.installed_graph(project)
        return
    assert result.returncode == 0, result.stdout + result.stderr
    assert facts['ok'] and facts['stage'] == 'prepared'
    assert facts['packages'] == facts['resolverTransactions'] == 1
    assert facts['sources'] == 4  # Hook plus installed loop and two source-only fixture modules.
    installed = environments.installed_sources(project)
    assert installed[NAME]['current'] == c.revision
    assert installed[NAME]['subdirectory'] == SUBDIRECTORY
    final = await qualification.freeze(c.manager, GENERATION, project)
    qualification.verify_recorded(final, c.stage)
    assert environments.installed_sources(final)[NAME]['current'] == c.revision
    assert c.store.verify(URL, c.revision) == c.obj
    if endpoints:
        assert inventory() == object_before
        assert stat.S_ISSOCK((git_dir / 'fsmonitor.sock').lstat().st_mode)
        assert stat.S_ISFIFO((git_dir / 'fsmonitor.fifo').lstat().st_mode)
        assert not (build / '.git/fsmonitor.sock').exists()
        assert not (build / '.git/fsmonitor.fifo').exists()
        assert json.loads((build / '.amplifier_cache_meta.json').read_text())['buildInput'] is True
        retained = list(build.parent.glob('.' + build.name + '.incomplete-*/checkout'))
        assert len(retained) == 1
        saved = retained[0] / residue.name
        assert (saved.read_bytes(), stat.S_IMODE(saved.stat().st_mode)) == partial_before
        record_property('buildRecovery', json.dumps({
            'build': str(build), 'retained': str(retained[0]),
            'sourceRevision': c.revision, 'subdirectory': SUBDIRECTORY,
            'sourceInventorySha256': hashlib.sha256(
                json.dumps(object_before, sort_keys=True).encode()).hexdigest()}))
        endpoint.close()


@pytest.mark.skipif(not os.environ.get('FOUNDATION_INTEGRATION_REVISION'),
                    reason='Opt-in full packaged worker graph in the owned cross-repository DTU')
async def test_packaged_worker_preparation_and_included_foundation_update(
        tmp_path, monkeypatch, record_property):
    """Real full worker graph and UpdateManager.validate, no model generation.

    The DTU serves the exact candidate repository as its isolated main; this is
    candidate transport evidence, never proof of upstream main acceptance.
    """
    from amplifier_web.service import AppService
    from amplifier_web.bundles import SNAPSHOT_VERSION

    revision = os.environ['FOUNDATION_INTEGRATION_REVISION']
    update_revision = os.environ.get('FOUNDATION_INTEGRATION_UPDATE_REVISION', revision)
    expected_hash = os.environ['FOUNDATION_INTEGRATION_SHARED_SHA256']
    assert len(revision) == 40 and all(c in '0123456789abcdef' for c in revision)
    assert len(expected_hash) == 64 and all(c in '0123456789abcdef' for c in expected_hash)
    url = 'https://github.com/microsoft/amplifier-foundation'
    names = {'amplifier-foundation': '',
             'amplifier-module-hooks-session-naming': 'modules/hooks-session-naming'}
    root = Path(__file__).resolve().parents[1]
    home, shared = tmp_path / 'app', tmp_path / 'shared'
    home.mkdir(); shared.mkdir()
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    monkeypatch.setenv('AMPLIFIER_HOME', str(shared))
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    for key in ('AMPLIFIER_UNIFIED_RELEASE', 'AMPLIFIER_SOURCE_STORE', 'PYTHONPATH',
                'UV_OVERRIDE', 'UV_CONSTRAINT', 'UV_BUILD_CONSTRAINT',
                'UV_PROJECT_ENVIRONMENT', 'UV_CONFIG_FILE', 'UV_OFFLINE', 'UV_NO_INDEX'):
        monkeypatch.delenv(key, raising=False)
    uv = shutil.which('uv')
    assert uv and sys.version_info[:2] == (3, 13)
    baseline = environments.prepare_project(home)
    packaged = environments.manifest_path().read_bytes()
    assert (baseline / 'pyproject.toml').read_bytes() == packaged
    predecessor_lock = Path(os.environ.get('FOUNDATION_INTEGRATION_BASELINE_LOCK', root / 'uv.lock'))
    shutil.copyfile(predecessor_lock, baseline / 'uv.lock')
    # The full packaged graph preserves the host's old Foundation resolution.
    await updates.process(uv, 'sync', '--project', str(baseline), '--python', '3.13', timeout=900)
    old = environments.locked_sources(predecessor_lock)['amplifier-foundation'].fragment
    assert old != revision
    assert environments.installed_sources(baseline)['amplifier-foundation']['current'] == old
    baseline_graph = qualification.installed_graph(baseline)
    baseline_payload = qualification.package_content(baseline)
    # A served DTU branch may have a mechanically created snapshot commit.
    # Prove the exact input commit separately rather than relabel that snapshot.
    fresh_home = tmp_path / 'fresh-candidate'
    fresh_data = tomllib.loads(packaged.decode())
    for name in names:
        fresh_data['tool']['uv']['sources'][name]['rev'] = revision
    fresh_content = ('\n'.join(json.dumps(key) + ' = ' + environments.toml_value(value)
                              for key, value in fresh_data.items()) + '\n').encode()
    fresh = environments.prepare_project(fresh_home, content=fresh_content)
    shutil.copyfile(baseline / 'uv.lock', fresh / 'uv.lock')
    await updates.process(uv, 'sync', '--project', str(fresh), '--python', '3.13', timeout=900)
    fresh_sources = environments.installed_sources(fresh)
    for name, subdirectory in names.items():
        assert fresh_sources[name]['current'] == revision
        assert fresh_sources[name]['url'].removesuffix('.git') == url
        assert fresh_sources[name]['subdirectory'] == subdirectory
    fresh_identity = json.loads(await updates.process(str(fresh / '.venv/bin/python'), '-I', '-c',
        'import hashlib,json,sys; from pathlib import Path; '
        'import amplifier_foundation.sources.shared as s; '
        'print(json.dumps({"prefix":sys.prefix,"source":s.__file__,'
        '"sha256":hashlib.sha256(Path(s.__file__).read_bytes()).hexdigest()}))'))
    assert Path(fresh_identity['source']).resolve().is_relative_to((fresh / '.venv').resolve())
    assert fresh_identity['sha256'] == expected_hash
    receipt = environments.receipt_directory(home)
    receipt.mkdir(parents=True, exist_ok=True)
    for source, target in [('pyproject.toml', 'runtime.toml'), ('uv.lock', 'runtime.lock')]:
        shutil.copyfile(baseline / source, receipt / target)
    def predecessor_receipts():
        return {str(path.relative_to(receipt)): path.read_bytes()
                for path in receipt.rglob('*') if path.is_file()}
    before = predecessor_receipts()
    beforelock = (baseline / 'uv.lock').read_bytes()

    provider = tmp_path / 'provider-fixture'
    provider.mkdir()
    (provider / '__init__.py').write_text('''
from pathlib import Path
from amplifier_core.models import ProviderInfo
__amplifier_module_type__ = "provider"
class FixtureProvider:
    name = "fixture"
    def __init__(self, config): self.config = config
    def get_info(self):
        return ProviderInfo(id="fixture", display_name="Fixture", defaults={"model": "offline"})
    def get_config_schema(self): return {"fields": []}
    async def list_models(self): return []
    def parse_tool_calls(self, response): return []
    async def complete(self, request, **kwargs):
        Path(self.config["calls"]).write_text("unexpected model call")
        raise AssertionError("Worker qualification must not generate")
async def mount(coordinator, config=None):
    await coordinator.mount("providers", FixtureProvider(config), name="fixture")
''')
    fixture_package = 'amplifier-module-provider-fixture'
    (provider / 'pyproject.toml').write_text(
        '[project]\nname="' + fixture_package + '"\nversion="1.0"\ndependencies=[]\n'
        '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n')
    (provider / 'backend.py').write_text(BACKEND.replace(
        "wheel.writestr(name + '.py', 'version = ' + repr(version))",
        "wheel.writestr(name + '/__init__.py', Path('__init__.py').read_text())"))
    calls = tmp_path / 'model-calls'
    profile = tmp_path / 'profile.yaml'
    profile.write_text(json.dumps({
        'bundle': {'name': 'foundation-integration', 'version': SNAPSHOT_VERSION},
        'session': {'orchestrator': {'module': 'loop-streaming'},
                    'context': {'module': 'context-simple'}},
        'providers': [{'module': 'provider-fixture', 'source': str(provider),
                       'config': {'calls': str(calls)}}]}))
    settings = {'bundle': {'active': 'foundation-integration',
                          'added': {'foundation-integration': str(profile)}},
                'web_bundles': {'excluded': list(builtin_behaviors.DEFAULT_BEHAVIORS),
                    'entries': [{'name': name, 'uri': uri, 'role': 'standalone', 'enabled': False}
                                for name, uri in PRECONFIGURED_BUNDLES.items()]}}
    (shared / 'settings.yaml').write_text(json.dumps(settings))
    monkeypatch.chdir(tmp_path)

    class Runtime:
        workers = {}
        async def close(self): pass
        def has_pending_operations(self): return False
    app = AppService(home, Runtime(), workspace=tmp_path)
    manager = updates.UpdateManager(app)
    app.update_manager = manager
    generation = 'f' * 32
    stage = environments.receipt_directory(home, generation)
    stage.mkdir(parents=True)
    shutil.copytree(shared, stage / 'shared-config')
    rows = await environments.update_inventory(home)
    # Request only these two upgrades; verify the actual graph delta below.
    manager.inventory = [{**row, 'latest': update_revision, 'status': 'update',
                          'updateTier': 'included'}
                         for row in rows if row.get('package') in names]
    assert {row['package'] for row in manager.inventory} == set(names)
    assert all(row['eligible'] and row['ref'] == 'main' for row in manager.inventory)
    report = {'candidateRevision': revision, 'updateRevision': update_revision,
              'predecessorRevision': old,
              'freshCandidateSources': {name: fresh_sources[name] for name in names},
              'freshCandidateIdentity': fresh_identity,
              'freshCandidateManifestSha256': hashlib.sha256(fresh_content).hexdigest(),
              'packagedManifestSha256': hashlib.sha256(packaged).hexdigest(),
              'selected': manager.inventory}
    try:
        await manager.validate(stage, generation)
        final = environments.project_path(home, generation)
        assert all((stage / name).is_file() for name in (
            'runtime-installed.json', 'runtime-project.json', 'profiles-qualified.json',
            'runtime-base.toml', 'runtime.toml', 'runtime.lock', 'runtime-sources.json'))
        qualification.verify_recorded(final, stage)
        graph = qualification.installed_graph(final)
        def source_identity(row):
            direct = row.get('directUrl') or {}
            vcs = direct.get('vcs_info') or {}
            # Freeze intentionally replaces requested main with its exact SHA.
            # That spelling change is not an upgrade to another source commit.
            return (row['version'], direct.get('url'), vcs.get('commit_id'),
                    direct.get('subdirectory', ''), direct.get('dir_info'))
        old_graph = {row['name']: source_identity(row) for row in baseline_graph}
        new_graph = {row['name']: source_identity(row) for row in graph}
        changed = {name for name in old_graph.keys() | new_graph.keys()
                   if old_graph.get(name) != new_graph.get(name)}
        assert changed == set(names) | {fixture_package}, changed
        selected = {row['name']: row for row in graph if row['name'] in names}
        for name, subdirectory in names.items():
            direct = selected[name]['directUrl']
            assert direct['url'].removesuffix('.git') == url
            assert direct['vcs_info']['commit_id'] == update_revision
            assert direct.get('subdirectory', '') == subdirectory
        identity = json.loads(await updates.process(str(final / '.venv/bin/python'), '-I', '-c',
            'import hashlib,json,sys; from pathlib import Path; '
            'import amplifier_foundation.sources.shared as s; '
            'print(json.dumps({"prefix":sys.prefix,"source":s.__file__,'
            '"sha256":hashlib.sha256(Path(s.__file__).read_bytes()).hexdigest()}))'))
        assert Path(identity['source']).resolve().is_relative_to((final / '.venv').resolve())
        assert Path(identity['prefix']).resolve() == (final / '.venv').resolve()
        assert identity['sha256'] == expected_hash
        assert (baseline / 'uv.lock').read_bytes() == beforelock
        assert predecessor_receipts() == before
        assert qualification.installed_graph(baseline) == baseline_graph
        assert qualification.package_content(baseline) == baseline_payload
        assert not calls.exists()
        report.update(installedFoundation=selected, identity=identity,
            receipts={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in stage.iterdir() if p.is_file()},
            graphDelta=sorted(changed),
            predecessorReceipts={name: hashlib.sha256(content).hexdigest()
                                 for name, content in before.items()},
            predecessorLockSha256=hashlib.sha256(beforelock).hexdigest(),
            predecessorUnchanged=True, providerCalls=0)
        record_property('fullWorkerIntegration', json.dumps(report))
        output = os.environ.get('FOUNDATION_INTEGRATION_OUTPUT')
        if output:
            Path(output).write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    finally:
        await app.close()
        assert not calls.exists()
