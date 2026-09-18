"""Real Git checkouts: generated cache differences are not source edits."""
import importlib._bootstrap_external
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from amplifier_web.host.config import _import_global
from amplifier_web.service import AppService
from amplifier_web.updates import UpdateManager, active_release, cache_changes, foundation_home


def git(path, *args):
    return subprocess.check_output(['git', *map(str, args)], cwd=path, stderr=subprocess.DEVNULL).decode().strip()


def pyc(source):
    return importlib._bootstrap_external._code_to_timestamp_pyc(compile(source, 'module.py', 'exec'), 0, len(source))


@pytest.fixture
def repository(tmp_path):
    root = tmp_path/'repository'
    root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.email', 'cache-test@example.invalid')
    git(root, 'config', 'user.name', 'Cache fixture')
    (root/'bundle.py').write_text('value = 1\n')
    (root/'CLAUDE.md').write_text('Original instructions\n')
    (root/'AGENTS.md').symlink_to('CLAUDE.md')
    (root/'__pycache__').mkdir()
    (root/'__pycache__/module.cpython-313.pyc').write_bytes(pyc('value = 1'))
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'Original')
    return root


def cache_artifacts(root):
    (root/'__pycache__/module.cpython-313.pyc').write_bytes(pyc('value = 2'))
    (root/'AGENTS.md').unlink()
    (root/'AGENTS.md').write_bytes((root/'CLAUDE.md').read_bytes())


class Runtime:
    async def close(self):pass


@pytest.fixture
async def service(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_UNIFIED_IMPORT_HOME', str(tmp_path/'no-import'))
    app = AppService(tmp_path/'app', Runtime(), workspace=tmp_path)
    app.update_manager = UpdateManager(app)
    yield app
    await app.close()


async def test_only_verified_existing_cache_artifacts_are_eligible(repository, service):
    cache = service.data_dir/'foundation/cache/example'
    shutil.copytree(repository, cache, symlinks=True)
    (cache/'.amplifier_cache_meta.json').write_text(json.dumps({'git_url':'https://example.invalid/repo', 'ref':'main'}))
    cache_artifacts(cache)
    before = (cache/'AGENTS.md').read_bytes(), (cache/'__pycache__/module.cpython-313.pyc').read_bytes()
    protected, artifacts = await cache_changes(cache)
    assert protected == []
    assert set(artifacts) == {'AGENTS.md', '__pycache__/module.cpython-313.pyc'}
    rows = await service.update_manager.inventory_sources()
    assert rows[0]['eligible'] and rows[0]['status'] == 'not_checked'
    assert not (cache/'AGENTS.md').is_symlink()
    assert before == ((cache/'AGENTS.md').read_bytes(), (cache/'__pycache__/module.cpython-313.pyc').read_bytes())
    (cache/'bundle.py').write_text('real source change\n')
    row = (await service.update_manager.inventory_sources())[0]
    assert not row['eligible'] and row['status'] == 'local_changes'


@pytest.mark.parametrize('change', ['source', 'staged-bytecode', 'deleted-bytecode', 'invalid-bytecode', 'bytecode-mode', 'modified-link', 'link-mode', 'deleted-link', 'changed-link', 'changed-target', 'staged-target', 'escaping-link', 'absolute-link', 'target-chain'])
async def test_real_edits_and_unproven_links_stay_protected(repository, tmp_path, change):
    root = repository
    cache_artifacts(root)
    if change == 'source':(root/'bundle.py').write_text('user = "source edit"\n')
    elif change == 'staged-bytecode':git(root, 'add', '__pycache__/module.cpython-313.pyc')
    elif change == 'deleted-bytecode':(root/'__pycache__/module.cpython-313.pyc').unlink()
    elif change == 'invalid-bytecode':(root/'__pycache__/module.cpython-313.pyc').write_bytes(b'User-authored data, not Python bytecode')
    elif change == 'bytecode-mode':(root/'__pycache__/module.cpython-313.pyc').chmod(0o755)
    elif change == 'modified-link':(root/'AGENTS.md').write_text('User-authored instructions\n')
    elif change == 'link-mode':(root/'AGENTS.md').chmod(0o755)
    elif change == 'deleted-link':(root/'AGENTS.md').unlink()
    elif change == 'changed-link':
        (root/'AGENTS.md').unlink();(root/'AGENTS.md').symlink_to('bundle.py')
    elif change in {'changed-target', 'staged-target'}:
        (root/'CLAUDE.md').write_text('New user-authored instructions\n')
        (root/'AGENTS.md').write_bytes((root/'CLAUDE.md').read_bytes())
        if change == 'staged-target':git(root, 'add', 'CLAUDE.md')
    elif change in {'escaping-link', 'absolute-link'}:
        (tmp_path/'outside.md').write_text('Outside the repository\n')
        (root/'AGENTS.md').unlink();(root/'AGENTS.md').symlink_to('../outside.md' if change == 'escaping-link' else tmp_path/'outside.md')
        git(root, 'add', 'AGENTS.md');git(root, 'commit', '-m', 'Tracked external link')
        (root/'AGENTS.md').unlink();(root/'AGENTS.md').write_bytes((tmp_path/'outside.md').read_bytes())
    elif change == 'target-chain':
        (root/'CLAUDE.md').unlink();(root/'CLAUDE.md').symlink_to('bundle.py')
        git(root, 'add', 'CLAUDE.md');git(root, 'commit', '-m', 'Tracked target chain')
        (root/'AGENTS.md').write_bytes((root/'CLAUDE.md').read_bytes())
    protected, _ = await cache_changes(root)
    assert protected, change


async def test_nul_paths_and_renames_cannot_be_misclassified(repository):
    name = ' instructions\twith\nwhitespace.md '
    (repository/name).symlink_to('CLAUDE.md')
    git(repository, 'add', '.');git(repository, 'commit', '-m', 'Unusual filename')
    (repository/name).unlink();(repository/name).write_bytes((repository/'CLAUDE.md').read_bytes())
    assert await cache_changes(repository) == ([], [name])
    git(repository, 'mv', 'bundle.py', ' renamed.py ')
    protected, artifacts = await cache_changes(repository)
    assert protected and artifacts == [name]


async def test_staging_normalizes_only_verified_artifacts_and_preserves_live_cache(repository, service):
    old = git(repository, 'rev-parse', 'HEAD')
    (repository/'bundle.py').write_text('value = 3\n')
    (repository/'CLAUDE.md').write_text('New upstream instructions\n')
    git(repository, 'commit', '-am', 'Updated')
    new = git(repository, 'rev-parse', 'HEAD')
    cache = service.data_dir/'foundation/cache/example'
    shutil.copytree(repository, cache, symlinks=True)
    git(cache, 'checkout', '--detach', old)
    (cache/'.amplifier_cache_meta.json').write_text(json.dumps({'git_url':'https://example.invalid/repo', 'ref':'main'}))
    cache_artifacts(cache)
    # Do not remove arbitrary untracked files, including files named *.pyc.
    (cache/'__pycache__/personal-notes.pyc').write_text('keep my notes')
    before = (cache/'AGENTS.md').read_bytes(), (cache/'__pycache__/module.cpython-313.pyc').read_bytes()
    manager = service.update_manager
    manager.inventory = [{'id':'repo', 'path':'cache/example', 'url':str(repository), 'label':'Fixture', 'current':old, 'latest':new, 'ref':'main', 'eligible':True, 'status':'update'}]
    async def validate(stage, release):
        staged = stage/'foundation/cache/example'
        assert git(staged, 'rev-parse', 'HEAD') == new
        assert (staged/'AGENTS.md').is_symlink()
        assert (staged/'AGENTS.md').read_text() == 'New upstream instructions\n'
        assert (staged/'__pycache__/module.cpython-313.pyc').read_bytes() == pyc('value = 1')
        assert (staged/'__pycache__/personal-notes.pyc').read_text() == 'keep my notes'
    manager.validate = validate
    await manager.install()
    assert service.state['updates']['phase'] == 'installed'
    assert foundation_home(service.data_dir) != service.data_dir/'foundation'
    assert git(cache, 'rev-parse', 'HEAD') == old
    assert not (cache/'AGENTS.md').is_symlink()
    assert before == ((cache/'AGENTS.md').read_bytes(), (cache/'__pycache__/module.cpython-313.pyc').read_bytes())


async def test_source_edit_added_after_check_blocks_staging(repository, service):
    old = git(repository, 'rev-parse', 'HEAD')
    cache = service.data_dir/'foundation/cache/example'
    shutil.copytree(repository, cache, symlinks=True)
    cache_artifacts(cache)
    assert (await cache_changes(cache))[0] == []
    (cache/'bundle.py').write_text('a later real source edit\n')
    manager = service.update_manager
    manager.inventory = [{'id':'repo', 'path':'cache/example', 'url':str(repository), 'label':'Fixture', 'current':old, 'latest':old, 'ref':'main', 'eligible':True, 'status':'update'}]
    await manager.install()
    assert service.state['updates']['phase'] == 'error'
    assert not active_release(service.data_dir)
    assert (cache/'bundle.py').read_text() == 'a later real source edit\n'
    assert not (cache/'AGENTS.md').is_symlink()


def test_import_preserves_symlinks_including_external_links(tmp_path, repository):
    legacy = tmp_path/'legacy'
    shutil.copytree(repository, legacy/'cache/repository', symlinks=True)
    outside = tmp_path/'external';outside.mkdir();(outside/'notes.txt').write_text('private')
    (legacy/'cache/repository/external').symlink_to(outside, target_is_directory=True)
    home = tmp_path/'imported'
    _import_global(home, legacy)
    imported = home/'foundation/cache/repository'
    assert (imported/'AGENTS.md').is_symlink()
    assert (imported/'AGENTS.md').readlink() == Path('CLAUDE.md')
    assert (imported/'external').is_symlink()
    assert git(imported, 'status', '--porcelain', '--untracked-files=no') == ''
