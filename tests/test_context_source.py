"""A shared Python dependency must never conceal a different selected source."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from amplifier_web.host import context_source as policy
from amplifier_web.host.components import ComponentResolver, HostComponents

REPO = 'https://github.com/microsoft/amplifier-module-context-simple'
HINT = 'git+' + REPO + '@main'
PACKAGE = 'amplifier_module_context_simple'


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def copies(tmp_path, monkeypatch):
    root = tmp_path / 'prepared'
    installed = tmp_path / 'site-packages' / PACKAGE
    package = root / PACKAGE
    package.mkdir(parents=True)
    installed.mkdir(parents=True)
    for directory in (package, installed):
        (directory / '__init__.py').write_text('from ._text_estimate import estimate\n')
        (directory / '_text_estimate.py').write_text('def estimate(): return 42\n')
    (root / 'pyproject.toml').write_text('[project]\nname = "context-fixture"\nversion = "1"\n')
    git(root, 'init', '-q')
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
        'commit', '-qm', 'Fixture')
    commit = git(root, 'rev-parse', 'HEAD')
    metadata = {'git_url': REPO, 'ref': 'main', 'commit': commit}
    direct = {'url': REPO, 'vcs_info': {'vcs': 'git', 'commit_id': commit}}
    (root / '.amplifier_cache_meta.json').write_text(json.dumps(metadata))
    distribution = SimpleNamespace(read_text=lambda _: json.dumps(direct))
    monkeypatch.setattr(policy.importlib.metadata, 'distribution', lambda _: distribution)
    monkeypatch.setattr('amplifier_web.host.components.installed_package_source', lambda *_: str(installed))
    source = SimpleNamespace(resolve=lambda: root)
    return SimpleNamespace(root=root, package=package, installed=installed, source=source,
                           metadata=metadata, direct=direct, commit=commit)


@pytest.mark.parametrize('method', ['resolve', 'async_resolve'])
async def test_same_revision_and_bytes_share_source_across_child_and_remount(copies, method):
    seen = []
    def resolve(module, hint):
        seen.append((module, hint))
        return copies.source
    async def async_resolve(module, hint):
        return resolve(module, hint)
    wrapper = ComponentResolver(SimpleNamespace(resolve=resolve, async_resolve=async_resolve), HostComponents())
    for _ in range(2):  # a later child/remount must choose the same package
        source = getattr(wrapper, method)('context-simple', HINT)
        if method == 'async_resolve':
            source = await source
        assert source.resolve() == copies.installed
    assert seen == [('context-simple', HINT)] * 2


@pytest.mark.parametrize('difference', [
    'fork_hint', 'local_hint', 'different_ref', 'short_commit', 'different_commit',
    'fork_distribution', 'fork_cache', 'wrong_head', 'dirty_tracked', 'dirty_manifest',
    'untracked_package', 'different_installed_bytes', 'extra_installed_file', 'missing_metadata',
    'editable_distribution', 'symlink', 'package_budget',
])
def test_unproved_sources_are_preserved(copies, difference):
    hint = HINT
    if difference == 'fork_hint': hint = HINT.replace('/microsoft/', '/someone/')
    elif difference == 'local_hint': hint = str(copies.root)
    elif difference == 'different_ref': hint = HINT.replace('@main', '@other')
    elif difference == 'short_commit': copies.direct['vcs_info']['commit_id'] = copies.commit[:7]
    elif difference == 'different_commit': copies.direct['vcs_info']['commit_id'] = '0' * 40
    elif difference == 'fork_distribution': copies.direct['url'] = REPO.replace('/microsoft/', '/someone/')
    elif difference == 'fork_cache': copies.metadata['git_url'] = REPO.replace('/microsoft/', '/someone/')
    elif difference == 'wrong_head':
        git(copies.root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
            'commit', '--allow-empty', '-qm', 'Different revision')
    elif difference == 'dirty_tracked': (copies.package / '__init__.py').write_text('changed')
    elif difference == 'dirty_manifest': (copies.root / 'pyproject.toml').write_text('changed')
    elif difference == 'untracked_package': (copies.package / 'extra.py').write_text('untracked')
    elif difference == 'different_installed_bytes': (copies.installed / '__init__.py').write_text('changed')
    elif difference == 'extra_installed_file': (copies.installed / 'extra.py').write_text('extra')
    elif difference == 'editable_distribution': copies.direct['dir_info'] = {'editable': True}
    elif difference == 'symlink': (copies.installed / 'alias.py').symlink_to(copies.installed / '__init__.py')
    elif difference == 'package_budget': (copies.installed / 'large').write_bytes(b'0' * (4 * 1024 * 1024 + 1))
    (copies.root / '.amplifier_cache_meta.json').write_text(json.dumps(copies.metadata))
    if difference == 'missing_metadata': (copies.root / '.amplifier_cache_meta.json').unlink()
    assert policy.equivalent_context_source('context-simple', hint, copies.source) is copies.source


def test_explicit_matching_full_revision_and_canonical_repo(copies):
    copies.direct['url'] += '.git'
    source = policy.equivalent_context_source('context-simple', 'git+' + REPO + '@' + copies.commit, copies.source)
    assert source.resolve() == copies.installed


def test_equivalence_rechecked_after_package_changes(copies):
    assert policy.equivalent_context_source('context-simple', HINT, copies.source).resolve() == copies.installed
    (copies.package / '_text_estimate.py').write_text('changed')
    assert policy.equivalent_context_source('context-simple', HINT, copies.source) is copies.source


def test_other_modules_do_not_scan_distributions_or_sources(monkeypatch):
    def unexpected(*_): raise AssertionError('Unrelated modules must not be scanned')
    monkeypatch.setattr(policy.importlib.metadata, 'distribution', unexpected)
    source = SimpleNamespace(resolve=unexpected)
    assert policy.equivalent_context_source('context-managed', HINT, source) is source


def test_git_unavailable_or_slow_preserves_original(copies, monkeypatch):
    def timeout(*_): raise subprocess.TimeoutExpired('git', 1)
    monkeypatch.setattr(policy, '_git', timeout)
    assert policy.equivalent_context_source('context-simple', HINT, copies.source) is copies.source


async def test_real_boundary_import_and_core_loader_remount(tmp_path, monkeypatch):
    """Real modules/Core, synthetic Git provenance; no providers or execution."""
    import importlib.metadata
    import shutil
    boundary = pytest.importorskip('amplifier_module_context_managed.boundary')
    simple = pytest.importorskip(PACKAGE)
    from amplifier_core.loader import ModuleLoader
    from amplifier_core.validation.base import import_module_from_path

    assert boundary.SimpleContextManager is simple.SimpleContextManager
    installed = Path(simple.__file__).parent
    root = tmp_path / 'checkout'
    shutil.copytree(installed, root / PACKAGE, ignore=shutil.ignore_patterns('__pycache__'))
    git(root, 'init', '-q')
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
        'commit', '-qm', 'Same package files')
    commit = git(root, 'rev-parse', 'HEAD')
    (root / '.amplifier_cache_meta.json').write_text(json.dumps(
        {'git_url': REPO, 'ref': 'main', 'commit': commit}))
    original_distribution = importlib.metadata.distribution
    distribution = original_distribution(policy._DISTRIBUTION)
    direct = {'url': REPO, 'vcs_info': {'vcs': 'git', 'commit_id': commit}}
    fake = SimpleNamespace(locate_file=distribution.locate_file,
                           read_text=lambda name: json.dumps(direct) if name == 'direct_url.json' else distribution.read_text(name))
    monkeypatch.setattr(importlib.metadata, 'distribution',
                        lambda name: fake if name == policy._DISTRIBUTION else original_distribution(name))
    with pytest.raises(ImportError, match='cached submodule'):
        import_module_from_path(root / PACKAGE)

    async def resolve(*_, **__): return SimpleNamespace(resolve=lambda: root)
    wrapper = ComponentResolver(SimpleNamespace(async_resolve=resolve), HostComponents())
    coordinator = SimpleNamespace(get=lambda name: wrapper if name == 'module-source-resolver' else None)
    for _ in range(2):
        loader = ModuleLoader(coordinator)
        assert callable(await loader.load('context-simple', {}, source_hint=HINT))
        assert loader._loaded_module_paths['context-simple'] == installed.resolve()
        # A second instance from the same loader also respects canonical source.
        assert callable(await loader.load('context-simple', {}, source_hint=HINT))
    # The same process still rejects a selected fork. No sys.modules purge or
    # custom-source substitution can accidentally make this appear successful.
    with pytest.raises(Exception, match='cached submodule'):
        await ModuleLoader(coordinator).load('context-simple', {}, source_hint=HINT.replace('/microsoft/', '/custom/'))
