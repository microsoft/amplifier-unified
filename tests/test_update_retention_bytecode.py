"""Real Git/import caches must not permanently pin an unused generation."""
import json
import marshal
import os
from pathlib import Path
import py_compile
import subprocess
import sys

import pytest

from amplifier_web import runtime_qualification, update_retention as retention
from test_update_retention import manager_fixture


VERIFY_GENERATION = retention.verify_generation


def git(source, *args):
    return subprocess.check_output(['git', *args], cwd=source, text=True).strip()


def imported_generation(tmp_path, monkeypatch, *, subdirectory=False):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    folder = manager.directory / 'releases' / names[3]
    source = folder / 'foundation/cache/fixture'
    source.mkdir(parents=True)
    (source / 'fixture.py').write_text('VALUE = 42\n')
    if subdirectory:
        (source / 'package').mkdir()
        (source / 'package/__init__.py').write_text('VALUE = 1\n')
    git(source, 'init', '-q', '-b', 'main')
    git(source, 'config', 'user.name', 'Fixture Author')
    git(source, 'config', 'user.email', 'fixture@example.invalid')
    git(source, 'add', '.')
    git(source, 'commit', '-qm', 'Fixture source')
    git(source, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    (source / '.amplifier_cache_meta.json').write_text(json.dumps({
        'git_url': 'https://example.invalid/fixture', 'ref': 'main'}))
    metadata = project / '.venv/lib' / ('python' + '.'.join(map(str, sys.version_info[:2]))) / 'site-packages/fixture-1.0.dist-info'
    metadata.mkdir(parents=True)
    (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: fixture\nVersion: 1.0\n')
    (metadata / 'direct_url.json').write_text(json.dumps({
        'url': (source / 'package' if subdirectory else source).as_uri(), 'dir_info': {'editable': True}}))
    (folder / 'runtime-installed.json').write_text(json.dumps(runtime_qualification.installed_graph(project)))
    monkeypatch.setattr(retention, 'verify_generation', VERIFY_GENERATION)
    env = {**os.environ, 'PYTHONPATH': str(source)}
    env.pop('PYTHONDONTWRITEBYTECODE', None)
    subprocess.run([sys.executable, '-c', 'import fixture; assert fixture.VALUE == 42'],
                   cwd=source, env=env, check=True)
    cached, = (source / '__pycache__').glob('*.pyc')
    runtime_qualification.verify_recorded(project, folder)
    return manager, names, project, folder, source, cached


@pytest.mark.parametrize('variant', ['import', 'opt-1', 'opt-2', 'checked-hash', 'unchecked-hash'])
async def test_normal_import_bytecode_does_not_pin_verified_unused_generation(tmp_path, monkeypatch, variant):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    if variant.startswith('opt-'):
        cached = Path(py_compile.compile(str(source / 'fixture.py'), optimize=int(variant[-1]), doraise=True))
    elif variant.endswith('-hash'):
        mode = (py_compile.PycInvalidationMode.CHECKED_HASH if variant == 'checked-hash'
                else py_compile.PycInvalidationMode.UNCHECKED_HASH)
        py_compile.compile(str(source / 'fixture.py'), invalidation_mode=mode, doraise=True)
    assert cached.exists()
    preview = await retention.reclaim(manager, inspect_only=True)
    assert [row['id'] for row in preview['candidates']] == [names[3]]
    assert cached.exists()  # Inspection must not normalize or remove files.
    result = await retention.reclaim(manager, selected={names[3]})
    assert result['removed'] == 1
    assert not folder.exists()
    assert project.exists()  # Shared with current and rollback generations.
    assert all((manager.directory / 'releases' / name).exists() for name in names[:3])


@pytest.mark.parametrize('extra', ['notes.txt', 'untracked.py', 'fixture.egg-info/PKG-INFO',
                                  '.venv/pyvenv.cfg', 'fixture.pyc', '__pycache__/unknown.cpython-313.pyc'])
async def test_unknown_files_still_protect_generation(tmp_path, monkeypatch, extra):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    target = source / extra
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(cached.read_bytes() if extra.endswith('.pyc') else b'retain me')
    assert (await retention.reclaim(manager))['removed'] == 0
    assert target.exists() and cached.exists()


@pytest.mark.parametrize('change', ['modified-source', 'local-commit', 'untracked-source',
                                   'symlink-cache', 'symlink-directory', 'malformed-bytecode',
                                   'backup-suffix', 'foreign-tag', 'bad-magic', 'header-only',
                                   'truncated-code', 'appended-payload', 'different-code', 'missing-source-proof'])
async def test_bytecode_exception_requires_unchanged_verified_tracked_source(tmp_path, monkeypatch, change):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    if change == 'modified-source':
        (source / 'fixture.py').write_text('VALUE = 99\n')
    elif change == 'local-commit':
        git(source, 'checkout', '-qb', 'unpublished')
        (source / 'private.txt').write_text('keep this local work')
        git(source, 'add', 'private.txt')
        git(source, 'commit', '-qm', 'Unpublished work')
        git(source, 'checkout', '-q', 'main')
    elif change == 'untracked-source':
        (source / 'unknown.py').write_text('VALUE = 42\n')
        cached.with_name('unknown.' + cached.name.split('.', 1)[1]).write_bytes(cached.read_bytes())
    elif change in {'symlink-cache', 'symlink-directory'}:
        outside = tmp_path / 'other-owned-fixture'
        outside.mkdir()
        destination = outside / cached.name
        cached.rename(destination)
        if change == 'symlink-cache':
            cached.symlink_to(destination)
        else:
            cached.parent.rmdir()
            cached.parent.symlink_to(outside, target_is_directory=True)
    elif change == 'malformed-bytecode':
        cached.write_text('A note with a misleading filename must be retained.')
    elif change in {'backup-suffix', 'foreign-tag'}:
        renamed = cached.with_suffix('.backup') if change == 'backup-suffix' else cached.with_name('fixture.cpython-999.pyc')
        cached.rename(renamed)
        cached = renamed
    elif change == 'bad-magic':
        cached.write_bytes(b'XX\r\n' + cached.read_bytes()[4:])
    elif change == 'header-only':
        cached.write_bytes(cached.read_bytes()[:16])
    elif change == 'truncated-code':
        cached.write_bytes(cached.read_bytes()[:17])
    elif change == 'appended-payload':
        cached.write_bytes(cached.read_bytes() + b'preserve this extra data')
    elif change == 'different-code':
        cached.write_bytes(cached.read_bytes()[:16] + marshal.dumps(
            compile('VALUE = 99\n', str(source / 'fixture.py'), 'exec', dont_inherit=True)))
    else:
        # A cache unrelated to the installed graph has no tracked-content
        # proof even though the real recorded graph still verifies.
        direct, = project.glob('.venv/lib/python*/site-packages/*/direct_url.json')
        direct.write_text(json.dumps({'url': 'https://example.invalid/fixture',
                                     'vcs_info': {'vcs': 'git', 'commit_id': 'a' * 40}}))
        (folder / 'runtime-installed.json').write_text(json.dumps(runtime_qualification.installed_graph(project)))
        runtime_qualification.verify_recorded(project, folder)
    assert (await retention.reclaim(manager))['removed'] == 0
    assert folder.exists() and cached.exists()


async def test_subdirectory_install_proof_covers_tracked_siblings(tmp_path, monkeypatch):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch, subdirectory=True)
    graph = json.loads((folder / 'runtime-installed.json').read_text())
    assert graph[0]['path'] == str(source / 'package')
    assert graph[0]['cacheSource']['subdirectory'] == 'package'
    assert [row['id'] for row in (await retention.reclaim(manager, inspect_only=True))['candidates']] == [names[3]]
    # The real graph digest is rooted above the package subdirectory. Changing
    # this tracked sibling invalidates the recorded source, not just Git status.
    (source / 'fixture.py').write_text('VALUE = 99\n')
    with pytest.raises(ValueError):
        runtime_qualification.verify_recorded(project, folder)
    assert (await retention.reclaim(manager))['removed'] == 0
    assert cached.exists() and folder.exists()


@pytest.mark.parametrize('guard', ['lease', 'configuration', 'process', 'rollback'])
async def test_import_cache_does_not_waive_generation_protection(tmp_path, monkeypatch, guard):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    if guard == 'lease':
        from amplifier_web.generation_leases import acquire
        acquire(manager.home, names[3], os.getpid())
    elif guard == 'configuration':
        manager.service.state['referenced'] += str(folder)
    elif guard == 'process':
        monkeypatch.setattr(retention, 'process_references', lambda home: str(folder))
    else:
        (manager.directory / 'active.json').write_text(json.dumps({'current': names[0], 'previous': names[3]}))
    assert (await retention.reclaim(manager))['removed'] == 0
    assert cached.exists()
