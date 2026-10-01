import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest

shared = pytest.importorskip('amplifier_foundation.sources.shared')
from amplifier_web.update_sources import adopt_clean_sources, bindings, stage_binding


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True).strip()


def checkout(root, revision=None):
    root.mkdir(parents=True)
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    (root / 'bundle.md').write_text('old')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'fixture')
    revision = git(root, 'rev-parse', 'HEAD')
    (root / '.amplifier_cache_meta.json').write_text(json.dumps({'git_url': 'https://example.invalid/repo', 'ref': 'main', 'commit': revision}))
    return revision


@pytest.mark.asyncio
async def test_owned_adoption_new_binding_and_rollback_keep_exact_snapshots(tmp_path, monkeypatch):
    home, stage = tmp_path / 'app', tmp_path / 'app/updates/releases/one'
    root = stage / 'foundation/cache/legacy'
    first = checkout(root)
    manager = SimpleNamespace(home=home)
    monkeypatch.delenv('AMPLIFIER_SOURCE_STORE', raising=False)
    await adopt_clean_sources(manager, stage)
    assert not root.exists()
    rows = await bindings(home, stage / 'foundation', {})
    assert len(rows) == 1 and rows[0]['current'] == first
    store = shared.SharedSourceStore(home / 'source-store')
    old = store.checkout(rows[0]['url'], first)
    repo = tmp_path / 'newer'
    subprocess.check_call(['git', 'clone', '--quiet', str(old), str(repo)])
    git(repo, 'config', 'user.name', 'Fixture')
    git(repo, 'config', 'user.email', 'fixture@example.invalid')
    (repo / 'bundle.md').write_text('new')
    git(repo, 'commit', '-am', 'new')
    second = git(repo, 'rev-parse', 'HEAD')
    await store.ensure(rows[0]['url'], second, existing=repo)
    from amplifier_web.update_storage import copy_snapshot
    new_stage = home / 'updates/releases/two'
    copy_snapshot(stage, new_stage)
    await stage_binding(manager, new_stage, {**rows[0], 'latest': second})
    assert (old / 'bundle.md').read_text() == 'old'
    assert (await bindings(home, stage / 'foundation', {}))[0]['current'] == first
    assert (await bindings(home, new_stage / 'foundation', {}))[0]['current'] == second


@pytest.mark.asyncio
async def test_dirty_duplicate_protects_entire_source_identity(tmp_path, monkeypatch):
    home, stage = tmp_path / 'app', tmp_path / 'app/updates/releases/one'
    one = stage / 'foundation/cache/one'
    checkout(one)
    two = stage / 'foundation/cache/two'
    from amplifier_web.update_storage import copy_snapshot
    copy_snapshot(one, two)
    (two / 'bundle.md').write_text('local edit')
    monkeypatch.delenv('AMPLIFIER_SOURCE_STORE', raising=False)
    await adopt_clean_sources(SimpleNamespace(home=home), stage)
    assert one.exists() and two.exists()
    assert not list((stage / 'foundation/cache/.source-bindings').glob('*.json'))
    assert (two / 'bundle.md').read_text() == 'local edit'
