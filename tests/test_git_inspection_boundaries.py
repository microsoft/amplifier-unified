"""Real repositories prove passive reads do not execute conversion commands."""
import subprocess
from pathlib import Path

import pytest

from amplifier_outputs.git_review import anchors, snapshot
from amplifier_worktrees import GitWorktrees


def git(root, *arguments):
    return subprocess.run(['git', '-C', str(root), *arguments], check=True, capture_output=True).stdout


def repository(tmp_path):
    root = tmp_path / 'source'; root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    (root / '.gitattributes').write_text('*.txt filter=fixture\n')
    (root / 'tracked.txt').write_text('original\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'base')
    return root


def configured_filter(root, tmp_path, kind):
    marker = tmp_path / 'external-command-ran'
    program = tmp_path / 'filter.sh'
    program.write_text('#!/bin/sh\nprintf invoked >> "' + str(marker) + '"\ncat\n')
    program.chmod(0o700)
    for field in (['process'] if kind == 'process' else ['clean', 'smudge']):
        git(root, 'config', 'filter.fixture.' + field, str(program))
    git(root, 'config', 'filter.fixture.required', 'true')
    return marker, (root / '.git/config').read_bytes()


@pytest.mark.parametrize('kind', ['clean-smudge', 'process'])
async def test_review_neutralizes_configured_filter_commands_without_config_changes(tmp_path, kind):
    root = repository(tmp_path)
    marker, config = configured_filter(root, tmp_path, kind)
    (root / 'tracked.txt').write_text('changed raw bytes\n')
    result = await snapshot(root, 'unstaged')
    assert '+changed raw bytes' in result['text']
    assert 'filters disabled' in result['notice']
    assert not marker.exists()
    assert (root / '.git/config').read_bytes() == config
    assert (root / 'tracked.txt').read_text() == 'changed raw bytes\n'


@pytest.mark.parametrize('kind', ['clean-smudge', 'process'])
def test_managed_inspection_checkout_apply_and_cleanup_never_execute_filters(tmp_path, kind):
    root = repository(tmp_path)
    (root / 'tracked.txt').write_text('staged\n')
    git(root, 'add', 'tracked.txt')
    (root / 'tracked.txt').write_text('staged\nunstaged\n')
    marker, config = configured_filter(root, tmp_path, kind)
    index = (root / '.git/index').read_bytes()
    manager = GitWorktrees(tmp_path / 'managed')
    inspected = manager.inspect(root)
    assert not marker.exists()
    clean = manager.create(root, command_id='clean', expected_revision=inspected['sourceRevision'])
    assert (Path(clean['path']) / 'tracked.txt').read_text() == 'original\n'
    carried = manager.create(root, command_id='carry', expected_revision=inspected['sourceRevision'], mode='carry_dirty')
    assert (Path(carried['path']) / 'tracked.txt').read_text() == 'staged\nunstaged\n'
    assert git(Path(carried['path']), 'show', ':tracked.txt') == b'staged\n'
    assert manager.status(carried['id'])['checkout']['dirty']
    assert manager.remove(clean['id'], clean['revision'])['status'] == 'removed'
    assert not marker.exists()
    assert (root / '.git/config').read_bytes() == config
    assert (root / '.git/index').read_bytes() == index
    assert (root / 'tracked.txt').read_text() == 'staged\nunstaged\n'


@pytest.mark.parametrize('name', ['résumé.txt', 'tab\tname.txt', 'quote"name.txt', 'back\\slash.txt', 'space name.txt'])
async def test_real_git_quoted_paths_anchor_only_the_actual_displayed_hunk(tmp_path, name):
    root = repository(tmp_path)
    file = root / name
    file.write_text('-- a/forged-old.txt\nunchanged\n')
    git(root, 'add', name); git(root, 'commit', '-m', 'quoted path')
    file.write_text('++ b/forged-new.txt\nunchanged\n')
    result = await snapshot(root, 'unstaged')
    locations = anchors(result['text'])
    assert {(name, 'left', 1), (name, 'left', 2), (name, 'right', 1), (name, 'right', 2)} <= locations
    assert not any(path.startswith('forged-') for path, _, _ in locations)


def test_anchor_parser_rejects_invalid_utf8_and_malformed_quoted_headers():
    diff = 'diff --git a/x b/x\n--- "a/\\377"\n+++ "b/\\q"\n@@ -1 +1 @@\n-old\n+new\n'
    assert not anchors(diff)
