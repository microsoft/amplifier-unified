"""Copied Git stat data is prepared once without changing source authority."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess

import pytest

from amplifier_web import runtime_qualification, update_storage


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root)


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.delenv('GIT_INDEX_FILE', raising=False)
    base = tmp_path / 'original'
    root = base / 'cache/example'
    root.mkdir(parents=True)
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    for name in ('source.txt', 'run.sh'):
        (root / name).write_text('original\n')
        # Avoid testing Git's unrelated same-second timestamp protection.
        os.utime(root / name, (1_700_000_000, 1_700_000_000))
    (root / 'run.sh').chmod(0o755)
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'source')
    (root / '.amplifier_cache_meta.json').write_text(json.dumps({
        'git_url': 'https://example.invalid/source', 'ref': 'main',
    }))
    return base, root


def modes(root):
    return {str(p.relative_to(root)): stat.S_IMODE(p.lstat().st_mode)
            for p in (root, *root.rglob('*'))}


def index_stat(root, name):
    value = git(root, 'ls-files', '--debug', name).decode()
    return {key: tuple(map(int, re.search(pattern, value).groups())) for key, pattern in {
        'ctime': r'ctime: (\d+):(\d+)', 'mtime': r'mtime: (\d+):(\d+)',
        'identity': r'dev: (\d+)\s+ino: (\d+)',
    }.items()}


@pytest.mark.parametrize('sealed', [False, True])
def test_copy_refreshes_index_after_modes_and_keeps_source_authority(source, tmp_path, sealed):
    base, original = source
    if sealed:
        for path in [*original.rglob('*'), original]:
            path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    before_index = (original / '.git/index').read_bytes()
    before_modes = modes(original)
    before_stage = git(original, 'ls-files', '--stage', '-z')
    before_head = git(original, 'rev-parse', 'HEAD')
    target = tmp_path / 'candidate'
    update_storage.copy_snapshot(base, target)
    copied = target / 'cache/example'
    assert (original / '.git/index').read_bytes() == before_index
    assert modes(original) == before_modes == modes(copied)
    assert git(copied, 'ls-files', '--stage', '-z') == before_stage
    assert git(copied, 'rev-parse', 'HEAD') == before_head
    for name in ('source.txt', 'run.sh'):
        assert (copied / name).read_bytes() == (original / name).read_bytes()
        info = (copied / name).stat()
        recorded = index_stat(copied, name)
        assert recorded['ctime'] == divmod(info.st_ctime_ns, 1_000_000_000)
        assert recorded['mtime'] == divmod(info.st_mtime_ns, 1_000_000_000)
        # Git's index stat fields are unsigned 32-bit values.
        assert recorded['identity'] == (info.st_dev & 0xffffffff, info.st_ino & 0xffffffff)
    index = (copied / '.git/index').read_bytes()
    for _ in range(2):
        assert not git(copied, '--no-optional-locks', 'status', '--porcelain', '--untracked-files=no')
        assert (copied / '.git/index').read_bytes() == index


@pytest.mark.parametrize('edit', ['worktree', 'staged-restored'])
def test_copy_preserves_dirty_worktree_and_index(source, tmp_path, edit):
    base, original = source
    path = original / 'source.txt'
    path.write_text('edited\n')
    if edit == 'staged-restored':
        git(original, 'add', 'source.txt')
        path.write_text('original\n')
    before_stage = git(original, 'ls-files', '--stage', '-z')
    before_index = (original / '.git/index').read_bytes()
    target = tmp_path / 'candidate'
    update_storage.copy_snapshot(base, target)
    copied = target / 'cache/example'
    assert git(copied, 'ls-files', '--stage', '-z') == before_stage
    assert git(copied, 'rev-parse', 'HEAD') == git(original, 'rev-parse', 'HEAD')
    assert (copied / 'source.txt').read_bytes() == path.read_bytes()
    assert git(copied, '--no-optional-locks', 'status', '--porcelain', '--untracked-files=no')
    assert (original / '.git/index').read_bytes() == before_index


def test_copy_restores_metadata_modes_on_refresh_failure(source, tmp_path, monkeypatch):
    base, original = source
    for path in [*original.rglob('*'), original]:
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    before_modes, before_index = modes(original), (original / '.git/index').read_bytes()
    target = tmp_path / 'candidate'
    def fail(command, **kwargs):
        assert Path(kwargs['cwd']) == target / 'cache/example'
        assert stat.S_IMODE((Path(kwargs['cwd']) / '.git').stat().st_mode) & stat.S_IWUSR
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(update_storage.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        update_storage.copy_snapshot(base, target)
    assert modes(original) == before_modes == modes(target / 'cache/example')
    assert (original / '.git/index').read_bytes() == before_index
    assert (target / 'cache/example/.git/index').read_bytes() == before_index


@pytest.mark.parametrize('layout', ['worktree-file', 'metadata-link', 'alternate'])
def test_copy_does_not_refresh_redirected_metadata(source, tmp_path, monkeypatch, layout):
    base, original = source
    external = tmp_path / 'external-index'
    external.write_bytes((original / '.git/index').read_bytes())
    if layout == 'metadata-link':
        (original / '.git/index').unlink()
        (original / '.git/index').symlink_to(external)
    elif layout == 'alternate':
        (original / '.git/objects/info/alternates').write_text(str(tmp_path / 'objects'))
    else:
        metadata = tmp_path / 'original-git'
        (original / '.git').rename(metadata)
        (original / '.git').write_text('gitdir: ' + str(metadata) + '\n')
    before = external.read_bytes()
    monkeypatch.setattr(update_storage.subprocess, 'run', lambda *args, **kwargs: pytest.fail('redirected metadata refresh'))
    update_storage.copy_snapshot(base, tmp_path / 'candidate')
    assert external.read_bytes() == before


def test_copy_ignores_environment_index_redirect_and_preserves_mutation_refusal(source, tmp_path, monkeypatch):
    base, original = source
    external = tmp_path / 'outside-index'
    external.write_bytes((original / '.git/index').read_bytes())
    before = external.read_bytes()
    monkeypatch.setenv('GIT_INDEX_FILE', str(external))
    monkeypatch.setenv('GIT_OPTIONAL_LOCKS', '0')
    target = tmp_path / 'candidate'
    update_storage.copy_snapshot(base, target)
    assert external.read_bytes() == before
    monkeypatch.delenv('GIT_INDEX_FILE')
    copied = target / 'cache/example'
    project, receipt = tmp_path / 'project', tmp_path / 'receipt'
    metadata = project / '.venv/lib/python3.13/site-packages/example-1.dist-info'
    metadata.mkdir(parents=True)
    (metadata / 'METADATA').write_text('Name: example\nVersion: 1\n')
    (metadata / 'direct_url.json').write_text(json.dumps({'url': copied.as_uri(), 'dir_info': {}}))
    expected = runtime_qualification.installed_graph(project)
    receipt.mkdir()
    (receipt / 'runtime-installed.json').write_text(json.dumps(expected))
    runtime_qualification.verify_recorded(project, receipt)
    (copied / 'source.txt').write_text('changed after the first check\n')
    with pytest.raises(ValueError, match='worker graph changed'):
        runtime_qualification.verify_recorded(project, receipt)
    assert (original / 'source.txt').read_text() == 'original\n'
