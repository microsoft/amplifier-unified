"""Checked Git policies must also govern composed bundle install roots."""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import runtime_source_alignment as alignment, updates
from amplifier_web import runtime_qualification as qualification

URL = 'https://example.invalid/fixture-foundation'


def git(path, *args):
    return subprocess.check_output(['git', *map(str, args)], cwd=path,
                                   stderr=subprocess.DEVNULL, text=True).strip()


class Diagnostics:
    state = {}
    async def run(self, phase, fn, *args, **kwargs):
        return await fn(*args, **kwargs)
    def record(self, *args, **kwargs):
        pass


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    repo = tmp_path / 'repository'
    repo.mkdir()
    git(repo, 'init', '-b', 'main')
    git(repo, 'config', 'user.email', 'fixture@example.invalid')
    git(repo, 'config', 'user.name', 'Fixture')
    (repo / 'bundle.md').write_text('old')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'old')
    old = git(repo, 'rev-parse', 'HEAD')
    (repo / 'bundle.md').write_text('new')
    git(repo, 'commit', '-am', 'new')
    new = git(repo, 'rev-parse', 'HEAD')
    home = tmp_path / 'app'
    stage = home / ('updates/releases/' + 'c' * 32)
    stage.mkdir(parents=True)
    policy = {'url': URL, 'ref': 'main', 'revision': new, 'subdirectory': ''}
    (stage / 'runtime-sources.json').write_text(json.dumps({'fixture-foundation': policy}))
    manager = SimpleNamespace(home=home, diagnostics=Diagnostics())
    async def publish(**kwargs):
        pass
    manager.publish = publish
    manager.stage_cache_sources = lambda stage, rows: updates.UpdateManager.stage_cache_sources(manager, stage, rows)
    original = updates.process
    fetches = []
    async def process(*args, **kwargs):
        if 'fetch' in args:
            fetches.append(args)
            args = tuple(str(repo) if value == URL else value for value in args)
        return await original(*args, **kwargs)
    monkeypatch.setattr(updates, 'process', process)
    monkeypatch.delenv('AMPLIFIER_SOURCE_STORE', raising=False)

    def copy(name='fixture', *, ref='main', revision=old):
        root = stage / 'foundation/cache' / name
        shutil.copytree(repo, root)
        git(root, 'checkout', '--detach', revision)
        (root / '.amplifier_cache_meta.json').write_text(json.dumps({
            'git_url': URL, 'ref': ref, 'commit': revision}))
        return root
    return SimpleNamespace(repo=repo, home=home, stage=stage, old=old, new=new,
                           policy=policy, manager=manager, copy=copy, fetches=fetches)


async def test_all_matching_cache_copies_follow_checked_revision(candidate):
    c = candidate
    roots = [c.copy(), c.copy('skills/fixture')]
    live = c.home / 'foundation/cache/live'
    shutil.copytree(roots[0], live)
    await alignment.align_checked_sources(c.manager, c.stage)
    for root in roots:
        assert git(root, 'rev-parse', 'HEAD') == c.new
        assert (root / 'bundle.md').read_text() == 'new'
        assert json.loads((root / '.amplifier_cache_meta.json').read_text())['ref'] == 'main'
    assert git(live, 'rev-parse', 'HEAD') == c.old
    assert len(c.fetches) == 2
    await alignment.align_checked_sources(c.manager, c.stage)
    assert len(c.fetches) == 2  # An unchanged checked source needs no download.


@pytest.mark.parametrize('kind', ['dirty', 'metadata', 'conflict'])
async def test_protected_or_conflicting_sources_are_not_modified(candidate, kind):
    c = candidate
    root = c.copy()
    if kind == 'dirty':
        (root / 'bundle.md').write_text('my edit')
    elif kind == 'metadata':
        marker = root / '.amplifier_cache_meta.json'
        data = json.loads(marker.read_text()); data['commit'] = c.new
        marker.write_text(json.dumps(data))
    else:
        second = {**c.policy, 'revision': c.old, 'subdirectory': 'modules/child'}
        (c.stage / 'runtime-sources.json').write_text(json.dumps({'one': c.policy, 'two': second}))
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file() and '.git' not in p.parts}
    with pytest.raises(ValueError):
        await alignment.align_checked_sources(c.manager, c.stage)
    assert git(root, 'rev-parse', 'HEAD') == c.old and not c.fetches
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file() and '.git' not in p.parts}


@pytest.mark.parametrize('ref', ['release', 'v1.2', 'a' * 40])
async def test_different_ref_and_pinned_copies_are_preserved(candidate, ref):
    c = candidate
    root = c.copy(ref=ref)
    await alignment.align_checked_sources(c.manager, c.stage)
    assert git(root, 'rev-parse', 'HEAD') == c.old and not c.fetches


async def test_external_link_and_recorded_generation_cannot_be_repaired(candidate, tmp_path):
    c = candidate
    root = c.copy()
    external = tmp_path / 'external'
    root.rename(external)
    root.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError):
        alignment._owned(root / '.amplifier_cache_meta.json', c.stage / 'foundation')
    (c.stage / 'runtime.lock').write_text('recorded')
    with pytest.raises(ValueError, match='recorded'):
        await alignment.align_checked_sources(c.manager, c.stage)
    assert git(external, 'rev-parse', 'HEAD') == c.old


async def test_shared_binding_moves_to_exact_checked_object_and_retains_old(candidate):
    from amplifier_foundation.sources.shared import SharedSourceStore
    c = candidate
    old = c.copy()
    store = SharedSourceStore(c.home / 'source-store')
    original = await store.bind(c.stage / 'foundation/cache', URL, 'main', c.old, existing=old)
    await store.ensure(URL, c.new, existing=c.repo)
    shutil.rmtree(old)
    registry = c.stage / 'foundation/registry.json'
    registry.write_text(json.dumps({'bundles': {'fixture': {
        'uri': 'git+' + URL + '@main', 'local_path': str(original)}}}))
    await alignment.align_checked_sources(c.manager, c.stage)
    rows = list((c.stage / 'foundation/cache/.source-bindings').glob('*.json'))
    assert len(rows) == 1
    assert json.loads(rows[0].read_text())['commit'] == c.new
    assert json.loads(registry.read_text())['bundles']['fixture']['local_path'] == str(store.checkout(URL, c.new))
    assert store.verify(URL, c.old) == original and (original / 'bundle.md').read_text() == 'old'


async def test_source_guard_reports_only_mismatch_field_names(tmp_path, monkeypatch):
    from amplifier_web import runtime_environment as environments
    generation = 'a' * 32
    receipt = environments.receipt_directory(tmp_path, generation)
    receipt.mkdir(parents=True)
    (receipt / 'runtime-sources.json').write_text(json.dumps({'fixture-foundation': {
        'url': URL, 'ref': 'main', 'revision': 'a' * 40, 'subdirectory': ''}}))
    project = tmp_path / 'worker'; project.mkdir()
    (project / 'uv.lock').write_text('version=1')
    (project / 'pyproject.toml').write_text(
        '[project]\nname = "fixture-runtime"\nversion = "0.0.0"\n'
        'dependencies = ["fixture-foundation"]\n')
    graph = [{'name': 'fixture-foundation', 'version': '1', 'directUrl': {
        'url': 'file:///private/path', 'dir_info': {}}, 'cacheSource': {
            'url': URL, 'ref': 'main', 'revision': 'b' * 40,
            'subdirectory': '', 'dirty': False}}]
    monkeypatch.setattr(qualification, 'installed_graph', lambda _: graph)
    with pytest.raises(ValueError) as error:
        await qualification.freeze(SimpleNamespace(home=tmp_path), generation, project)
    assert error.value.diagnostic_facts == {
        'reason': 'runtime-installed-source-mismatch', 'package': 'fixture-foundation',
        'resolutionMismatches': ['revision']}
    assert '/private/' not in str(error.value)



BACKEND = """
from pathlib import Path
import tomllib
import zipfile

def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    info = tomllib.loads(Path('pyproject.toml').read_text())['project']
    name, version = info['name'].replace('-', '_'), info['version']
    dist = name + '-' + version
    filename = dist + '-py3-none-any.whl'
    with zipfile.ZipFile(Path(wheel_directory) / filename, 'w') as wheel:
        wheel.writestr(name + '.py', 'version = ' + repr(version))
        wheel.writestr(dist + '.dist-info/METADATA',
                       'Metadata-Version: 2.3\\nName: ' + info['name'] + '\\nVersion: ' + version + '\\n')
        wheel.writestr(dist + '.dist-info/WHEEL',
                       'Wheel-Version: 1.0\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n')
        wheel.writestr(dist + '.dist-info/RECORD', '')
    return filename
build_editable = build_wheel
"""


@pytest.mark.parametrize('align', [False, True])
async def test_real_composed_bundle_cannot_replace_checked_git_with_stale_cache(candidate, monkeypatch, align):
    """Real uv lock/sync + Foundation composition/batch; no model or network."""
    from amplifier_foundation import Bundle
    from amplifier_foundation.modules.batch import DependencyBatch
    from amplifier_foundation.modules import activator
    from amplifier_web import runtime_environment as environments
    c = candidate
    for version in ('1.0', '2.0'):
        for name, path in [('fixture-foundation', c.repo),
                           ('fixture-hooks', c.repo / 'modules/hooks-fixture')]:
            path.mkdir(parents=True, exist_ok=True)
            (path / 'pyproject.toml').write_text(
                '[project]\nname="' + name + '"\nversion="' + version + '"\n'
                '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n')
            (path / 'backend.py').write_text(BACKEND)
        git(c.repo, 'add', '.')
        git(c.repo, 'commit', '-m', 'package ' + version)
        if version == '1.0':
            c.old = git(c.repo, 'rev-parse', 'HEAD')
    c.new = git(c.repo, 'rev-parse', 'HEAD')
    root = c.copy(revision=c.old)
    manifest = ('[project]\nname="fixture-runtime"\nversion="1"\nrequires-python=">=3.13"\n'
                'dependencies=["fixture-foundation"]\n'
                '[tool.uv.sources]\nfixture-foundation={git="' + URL + '",branch="main"}\n').encode()
    row = {'kind': 'runtime dependency', 'package': 'fixture-foundation', 'url': URL,
           'ref': 'main', 'subdirectory': '', 'current': c.old, 'latest': c.new, 'eligible': True}
    async def update_manifest(_):
        return [row], manifest
    monkeypatch.setattr(environments, 'update_manifest', update_manifest)
    manifest_file = c.home / 'base.toml'; manifest_file.write_bytes(manifest)
    monkeypatch.setattr(environments, 'manifest_path', lambda: manifest_file)
    # Only this fixture process's Git resolution redirects the test URI.
    config = c.home / 'gitconfig'
    config.write_text('[url "' + c.repo.as_uri() + '"]\n insteadOf = ' + URL + '\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(config))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('UV_NO_INDEX', '1')
    monkeypatch.setenv('UV_CACHE_DIR', str(c.home / 'uv-cache'))
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    generation = c.stage.name
    project = await environments.stage(c.manager, generation, [row], finalize=False)
    await updates.process(shutil.which('uv'), 'sync', '--locked', '--project', str(project),
                          '--python', sys.executable, timeout=30)
    before = qualification.installed_graph(project)
    assert next(r for r in before if r['name'] == 'fixture-foundation')['directUrl']['vcs_info']['commit_id'] == c.new
    policy = await qualification.prepare_overrides(project, c.stage / 'runtime-install-overrides.txt')
    if align:
        await alignment.align_checked_sources(c.manager, c.stage)

    # Real Bundle.prepare creates both explicit root and child install entries.
    # Inject only the disposable destination interpreter, never a shared venv.
    original = activator.ModuleActivator
    class OwnedActivator(original):
        def __init__(self, *args, **kwargs):
            kwargs['install_python'] = str(project / '.venv/bin/python')
            super().__init__(*args, **kwargs)
    monkeypatch.setattr(activator, 'ModuleActivator', OwnedActivator)
    bundle = Bundle(name='fixture', base_path=root, hooks=[
        {'module': 'hooks-fixture', 'source': str(root / 'modules/hooks-fixture')}])
    batch = DependencyBatch()
    await bundle.prepare(strict=True, refresh_dependencies=True, dependency_batch=batch,
                         cache_dir=c.stage / 'foundation/cache', install_overrides=policy)
    assert batch.names['fixture-foundation'] == root
    await batch.install()
    graph = qualification.installed_graph(project)
    installed = environments.installed_sources(project, graph=graph)
    assert installed['fixture-foundation']['cacheManaged']
    assert installed['fixture-foundation']['current'] == (c.new if align else c.old)
    assert installed['fixture-hooks']['current'] == (c.new if align else c.old)
    assert next(r for r in graph if r['name'] == 'fixture-foundation')['version'] == ('2.0' if align else '1.0')
    if not align:
        with pytest.raises(ValueError) as error:
            await qualification.freeze(c.manager, generation, project)
        assert error.value.diagnostic_facts['resolutionMismatches'] == ['revision']
    else:
        # All actual installed identity fields now match the original policy.
        checked = json.loads((c.stage / 'runtime-sources.json').read_text())['fixture-foundation']
        assert (installed['fixture-foundation']['url'], installed['fixture-foundation']['current'],
                installed['fixture-foundation']['subdirectory']) == (checked['url'], checked['revision'], '')
        frozen = await qualification.freeze(c.manager, generation, project)
        qualification.verify_recorded(frozen, c.stage)
        saved = json.loads((c.stage / 'runtime-sources.json').read_text())
        assert saved['fixture-foundation'] == checked



async def test_unrelated_local_source_is_not_rewritten(candidate):
    c = candidate
    root = c.copy()
    marker = root / '.amplifier_cache_meta.json'
    data = json.loads(marker.read_text()); data['git_url'] = c.repo.as_uri()
    marker.write_text(json.dumps(data))
    await alignment.align_checked_sources(c.manager, c.stage)
    assert git(root, 'rev-parse', 'HEAD') == c.old and not c.fetches


async def test_git_worktree_cannot_redirect_staging_into_another_owner(candidate, tmp_path):
    c = candidate
    root = c.stage / 'foundation/cache/foreign'
    git(c.repo, 'worktree', 'add', '--detach', root, c.old)
    (root / '.amplifier_cache_meta.json').write_text(json.dumps({
        'git_url': URL, 'ref': 'main', 'commit': c.old}))
    with pytest.raises(ValueError, match='owned Git checkout'):
        await alignment.align_checked_sources(c.manager, c.stage)
    assert git(root, 'rev-parse', 'HEAD') == c.old and not c.fetches


@pytest.mark.parametrize('location', ['shared-config/cache', 'custom'])
async def test_legacy_shared_and_explicit_local_paths_are_not_alignment_targets(candidate, location):
    c = candidate
    root = c.copy()
    other = (c.stage / location if location == 'shared-config/cache' else c.home.parent / location) / 'fixture'
    other.parent.mkdir(parents=True, exist_ok=True)
    root.rename(other)
    before = (other / '.amplifier_cache_meta.json').read_bytes()
    await alignment.align_checked_sources(c.manager, c.stage)
    assert git(other, 'rev-parse', 'HEAD') == c.old and not c.fetches
    assert (other / '.amplifier_cache_meta.json').read_bytes() == before



async def test_review_registry_keeps_other_owners(candidate):
    from amplifier_foundation.sources.shared import SharedSourceStore
    c = candidate
    root = c.copy(); store = SharedSourceStore(c.home / 'source-store')
    original = await store.bind(c.stage / 'foundation/cache', URL, 'main', c.old, existing=root)
    await store.bind(c.stage / 'foundation/cache', URL, c.old, c.old, existing=root)
    await store.ensure(URL, c.new, existing=c.repo)
    shutil.rmtree(root)
    data = {'version': 1, 'bundles': {
        'moving': {'uri': 'git+' + URL + '@main', 'local_path': str(original)},
        'pinned': {'uri': 'git+' + URL + '@' + c.old, 'local_path': str(original)},
        'local': {'uri': str(original / 'bundle.md'), 'local_path': str(original)},
    }}
    registry = c.stage / 'foundation/registry.json'; registry.write_text(json.dumps(data))
    await alignment.align_checked_sources(c.manager, c.stage)
    actual = json.loads(registry.read_text())
    assert actual['bundles']['local'] == data['bundles']['local']
    assert actual['bundles']['pinned'] == data['bundles']['pinned']
    assert actual['bundles']['moving']['local_path'] == str(store.checkout(URL, c.new))


async def test_review_external_git_worktree_is_preserved(candidate, tmp_path):
    c = candidate; root = c.copy()
    external = tmp_path / 'external'; external.mkdir()
    (external / 'bundle.md').write_text('old')
    git(root, 'config', 'core.worktree', str(external))
    try:
        await alignment.align_checked_sources(c.manager, c.stage)
    except ValueError:
        pass
    assert (external / 'bundle.md').read_text() == 'old'
    assert git(root, 'rev-parse', 'HEAD') == c.old


@pytest.mark.parametrize('route', ['align', 'binding', 'adopt'])
async def test_registry_retargeting_uses_authored_git_identity(candidate, route):
    from amplifier_foundation.sources.shared import SharedSourceStore
    from amplifier_web.update_sources import adopt_clean_sources, stage_binding
    c = candidate; root = c.copy(); store = SharedSourceStore(c.home / 'source-store')
    original = root
    if route != 'adopt':
        original = await store.bind(c.stage / 'foundation/cache', URL, 'main', c.old, existing=root)
        await store.ensure(URL, c.new, existing=c.repo)
        shutil.rmtree(root)
    data = {'version': 1, 'bundles': {
        'moving': {'uri': 'git+' + URL + '.git@refs/heads/main#subdirectory=sub',
                   'local_path': str(original / 'sub'), 'note': str(original)},
        'pinned': {'uri': 'git+' + URL + '@' + c.old, 'local_path': str(original)},
        'local': {'uri': (original / 'bundle.md').as_uri(), 'local_path': str(original)},
        'other-ref': {'uri': 'git+' + URL + '@release', 'local_path': str(original)},
        'other-repo': {'uri': 'git+' + URL + '-other@main', 'local_path': str(original)},
        'sibling-prefix': {'uri': 'git+' + URL + '@main', 'local_path': str(original) + '-other'},
        'malformed': {'uri': 'git+https://[bad', 'local_path': str(original)},
        'unknown': str(original),
    }}
    registry = c.stage / 'foundation/registry.json'; registry.write_text(json.dumps(data))
    if route == 'align':
        await alignment.align_checked_sources(c.manager, c.stage)
    elif route == 'binding':
        binding = next((c.stage / 'foundation/cache/.source-bindings').glob('*.json'))
        await stage_binding(c.manager, c.stage, {'path': str(binding.relative_to(c.stage / 'foundation')),
                                               'url': URL, 'ref': 'main', 'current': c.old, 'latest': c.new})
    else:
        await adopt_clean_sources(c.manager, c.stage)
    actual = json.loads(registry.read_text())
    for key, row in data['bundles'].items():
        if key != 'moving':
            assert actual['bundles'][key] == row
    expected = dict(data['bundles']['moving'])
    expected['local_path'] = str(store.checkout(URL, c.old if route == 'adopt' else c.new) / 'sub')
    assert actual['bundles']['moving'] == expected
    assert (original / 'bundle.md').read_text() == 'old'


@pytest.mark.parametrize('route', ['align', 'direct', 'adopt'])
@pytest.mark.parametrize('redirect', ['worktree', 'include', 'commondir', 'objects', 'refs', 'index', 'alternates', 'env-worktree', 'env-index', 'env-gitdir', 'env-common', 'env-objects'])
async def test_git_indirection_cannot_modify_external_owners(candidate, tmp_path, monkeypatch, route, redirect):
    from amplifier_web.update_sources import adopt_clean_sources
    c = candidate; root = c.copy()
    external = tmp_path / 'external'
    shutil.copytree(root, external)
    if redirect == 'worktree':
        git(root, 'config', 'core.worktree', str(external))
    elif redirect == 'include':
        config = tmp_path / 'redirect.gitconfig'
        config.write_text('[core]\nworktree = ' + str(external) + '\n')
        git(root, 'config', 'include.path', str(config))
    elif redirect == 'commondir':
        (root / '.git/commondir').write_text(str(external / '.git'))
    elif redirect == 'alternates':
        (root / '.git/objects/info/alternates').write_text(str(external / '.git/objects'))
    elif redirect.startswith('env-'):
        variable, value = {
            'env-worktree': ('GIT_WORK_TREE', external),
            'env-index': ('GIT_INDEX_FILE', external / '.git/index'),
            'env-gitdir': ('GIT_DIR', external / '.git'),
            'env-common': ('GIT_COMMON_DIR', external / '.git'),
            'env-objects': ('GIT_OBJECT_DIRECTORY', external / '.git/objects'),
        }[redirect]
        monkeypatch.setenv(variable, str(value))
    else:
        path = root / '.git' / redirect
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        path.symlink_to(external / '.git' / redirect)
    before = {str(p.relative_to(external)): p.read_bytes() for p in external.rglob('*') if p.is_file()}
    original_head = (root / '.git/HEAD').read_bytes()
    row = {'path': str(root.relative_to(c.stage / 'foundation')), 'url': URL,
           'ref': 'main', 'current': c.old, 'latest': c.new, 'label': 'fixture'}
    if route == 'adopt':
        await adopt_clean_sources(c.manager, c.stage)
        assert root.exists()
        assert not list((c.stage / 'foundation/cache/.source-bindings').glob('*.json'))
    else:
        with pytest.raises(ValueError):
            if route == 'align':
                await alignment.align_checked_sources(c.manager, c.stage)
            else:
                await c.manager.stage_cache_sources(c.stage, [row])
    assert before == {str(p.relative_to(external)): p.read_bytes() for p in external.rglob('*') if p.is_file()}
    assert (root / '.git/HEAD').read_bytes() == original_head
    assert not c.fetches


@pytest.mark.parametrize('boundary', ['publish', 'fetch'])
async def test_git_ownership_is_rechecked_before_each_mutation(candidate, tmp_path, monkeypatch, boundary):
    c = candidate; root = c.copy()
    external = tmp_path / 'external'; external.mkdir()
    (external / 'bundle.md').write_text('old')
    if boundary == 'publish':
        async def publish(**kwargs):
            git(root, 'config', 'core.worktree', str(external))
        c.manager.publish = publish
    else:
        original = updates.process
        async def process(*args, **kwargs):
            result = await original(*args, **kwargs)
            if 'fetch' in args:
                git(root, 'config', 'core.worktree', str(external))
            return result
        monkeypatch.setattr(updates, 'process', process)
    with pytest.raises(ValueError):
        await alignment.align_checked_sources(c.manager, c.stage)
    assert (external / 'bundle.md').read_text() == 'old'
    assert git(root, 'rev-parse', 'HEAD') == c.old
    assert len(c.fetches) == (1 if boundary == 'fetch' else 0)


@pytest.mark.parametrize('route', ['align', 'direct'])
@pytest.mark.parametrize('owner', ['pinned', 'local', 'other-ref', 'unknown'])
async def test_legacy_cache_shared_with_other_registry_owners_is_preserved(candidate, route, owner):
    c = candidate; root = c.copy()
    uri = {'pinned': 'git+' + URL + '@' + c.old,
           'local': (root / 'bundle.md').as_uri(),
           'other-ref': 'git+' + URL + '@release', 'unknown': 'unrecognized-source'}[owner]
    registry = c.stage / 'foundation/registry.json'
    registry.write_text(json.dumps({'bundles': {
        'moving': {'uri': 'git+' + URL + '@main', 'local_path': str(root)},
        'protected': {'uri': uri, 'local_path': str(root)},
    }}))
    before = registry.read_bytes()
    with pytest.raises(ValueError, match='shared'):
        if route == 'align':
            await alignment.align_checked_sources(c.manager, c.stage)
        else:
            await c.manager.stage_cache_sources(c.stage, [{'path': str(root.relative_to(c.stage / 'foundation')),
                'url': URL, 'ref': 'main', 'current': c.old, 'latest': c.new, 'label': 'fixture'}])
    assert (root / 'bundle.md').read_text() == 'old'
    assert git(root, 'rev-parse', 'HEAD') == c.old and not c.fetches
    assert registry.read_bytes() == before


async def test_exclusive_moving_legacy_registration_remains_updatable(candidate):
    c = candidate; root = c.copy()
    registry = c.stage / 'foundation/registry.json'
    registry.write_text(json.dumps({'bundles': {'moving': {
        'uri': 'git+' + URL + '@main', 'local_path': str(root)}}}))
    before = registry.read_bytes()
    await alignment.align_checked_sources(c.manager, c.stage)
    assert (root / 'bundle.md').read_text() == 'new'
    assert registry.read_bytes() == before


async def test_mixed_legacy_owner_preflight_protects_all_copies(candidate):
    c = candidate; clean = c.copy('a-clean'); shared = c.copy('z-shared')
    (c.stage / 'foundation/registry.json').write_text(json.dumps({'bundles': {
        'local': {'uri': (shared / 'bundle.md').as_uri(), 'local_path': str(shared)}}}))
    with pytest.raises(ValueError, match='shared'):
        await alignment.align_checked_sources(c.manager, c.stage)
    assert all(git(root, 'rev-parse', 'HEAD') == c.old for root in (clean, shared))
    assert not c.fetches


async def test_new_registry_owner_before_checkout_is_rechecked(candidate, monkeypatch):
    c = candidate; root = c.copy()
    original = updates.process
    async def process(*args, **kwargs):
        result = await original(*args, **kwargs)
        if 'fetch' in args:
            (c.stage / 'foundation/registry.json').write_text(json.dumps({'bundles': {
                'local': {'uri': (root / 'bundle.md').as_uri(), 'local_path': str(root)}}}))
        return result
    monkeypatch.setattr(updates, 'process', process)
    with pytest.raises(ValueError, match='shared'):
        await alignment.align_checked_sources(c.manager, c.stage)
    assert (root / 'bundle.md').read_text() == 'old'
    assert git(root, 'rev-parse', 'HEAD') == c.old


async def test_already_aligned_mixed_registry_does_not_block_noop(candidate):
    c = candidate; root = c.copy(revision=c.new)
    (c.stage / 'foundation/registry.json').write_text(json.dumps({'bundles': {
        'local': {'uri': (root / 'bundle.md').as_uri(), 'local_path': str(root)}}}))
    await alignment.align_checked_sources(c.manager, c.stage)
    assert git(root, 'rev-parse', 'HEAD') == c.new and not c.fetches
