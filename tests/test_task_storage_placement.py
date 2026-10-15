"""Storage follows only its existing owned checkout's verified retained moves."""
import copy
from pathlib import Path

import pytest

from amplifier_web import task_storage_placement as placement
from amplifier_web.host_identity import local_host_identity
from amplifier_worktrees import GitWorktrees
from amplifier_worktrees.git import git


def owned_checkout(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    git(source, 'init', '-b', 'main')
    git(source, 'config', 'user.name', 'Fixture')
    git(source, 'config', 'user.email', 'fixture@example.invalid')
    (source / 'source.txt').write_text('source\n')
    git(source, 'add', 'source.txt')
    git(source, 'commit', '-m', 'source')
    home = tmp_path / 'app'
    owner = GitWorktrees(home / 'managed-worktrees', execution_host=local_host_identity())
    record = owner.create(source, command_id='storage-placement',
                          expected_revision=owner.inspect(source)['sourceRevision'], session_id='root')
    return home, owner, record


def test_legacy_and_unbound_roots_never_search_or_relocate(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    home = tmp_path / 'app'
    assert placement.root(home, {}) is None
    row = placement.capture(home, workspace)
    assert 'storageCheckout' not in row
    monkeypatch.setattr(placement, '_record', lambda *a: pytest.fail('Unbound root inspected worktrees'))
    assert placement.root(home, row) == workspace
    workspace.rename(tmp_path / 'retained')
    workspace.mkdir()
    with pytest.raises(ValueError, match='replaced'):
        placement.root(home, row)
    assert not home.exists()


@pytest.mark.parametrize('nested', [False, True])
def test_real_archive_restore_preserves_storage_identity_and_files(tmp_path, monkeypatch, nested):
    home, owner, record = owned_checkout(tmp_path)
    execution = Path(record['path'])
    if nested:
        execution /= 'nested'
        execution.mkdir()
    row = placement.capture(home, execution)
    storage = execution / '.amplifier' / 'task-storage'
    storage.mkdir(parents=True)
    (storage / '.gitignore').write_text('*\n')
    (storage / 'evidence.txt').write_text('retained')
    monkeypatch.setattr(placement, 'common', lambda *a: pytest.fail('Read launched Git'))
    for index in range(2):
        record = owner.relocate(record['id'], record['revision'], f'archive-{index}')
        archived = Path(record['path']) / ('nested' if nested else '')
        assert placement.root(home, row) == archived
        assert (archived / '.amplifier/task-storage/evidence.txt').read_text() == 'retained'
        record = owner.relocate(record['id'], record['revision'], f'restore-{index}', restore=True)
        assert placement.root(home, row) == execution
    assert row['storageRoot'] == str(execution)


@pytest.mark.parametrize('change', ['unknown', 'pending', 'path', 'signature', 'repository', 'sessionId', 'source', 'host',
                                 'move-source', 'move-identity', 'move-state', 'missing-move'])
def test_changed_or_uncertain_checkout_records_are_not_followed(tmp_path, change):
    home, owner, record = owned_checkout(tmp_path)
    execution = Path(record['path'])
    row = placement.capture(home, execution)
    record = owner.relocate(record['id'], record['revision'], 'archive')
    if change in {'unknown', 'pending'}:
        record['status'] = 'archive_' + change
    elif change == 'path':
        record['path'] = str(tmp_path)
    elif change in {'signature', 'repository', 'sessionId', 'source'}:
        record[change] = 'different'
    elif change == 'host':
        record['executionHost']['id'] = 'different-host'
    elif change == 'missing-move':
        record['archiveOperations'] = {}
    else:
        operation = record['archiveOperations']['archive']
        if change == 'move-source':
            operation['source'] = str(tmp_path)
        elif change == 'move-identity':
            operation['directoryIdentity'] = [0, 0]
        else:
            operation['status'] = 'unknown'
    owner.save(record)
    with pytest.raises(ValueError):
        placement.root(home, row)
    assert not execution.exists()


def test_capture_after_archive_and_failed_move_receipt_is_read_only(tmp_path, monkeypatch):
    home, owner, record = owned_checkout(tmp_path)
    row = placement.capture(home, record['path'])
    # A failed move with a verified not_applied receipt leaves the original root.
    checkout = Path(record['path'])
    record['archiveOperations'] = {'failed': {
        'action': 'archive', 'status': 'not_applied', 'source': str(checkout),
        'target': str(owner.directory / 'archives' / record['id']),
        'directoryIdentity': row['storageCheckout']['directoryIdentity'],
    }}
    owner.save(record)
    assert placement.root(home, row) == checkout
    record = owner.relocate(record['id'], record['revision'], 'archive')
    after = placement.capture(home, record['path'])
    assert after['storageRoot'] == record['path']
    record = owner.relocate(record['id'], record['revision'], 'restore', restore=True)
    monkeypatch.setattr(owner, 'save', lambda *a: pytest.fail('Reader changed owner'))
    assert placement.root(home, after) == checkout


def test_exact_record_read_rejects_symlinks_and_nested_root_replacement(tmp_path):
    home, owner, record = owned_checkout(tmp_path)
    execution = Path(record['path']) / 'nested'
    execution.mkdir()
    row = placement.capture(home, execution)
    execution.rename(execution.with_name('retained'))
    execution.symlink_to(execution.with_name('retained'), target_is_directory=True)
    with pytest.raises(ValueError, match='symbolic link'):
        placement.root(home, row)
    execution.unlink()
    execution.with_name('retained').rename(execution)
    record_path = owner.record_path(record['id'])
    retained = record_path.with_suffix('.retained')
    record_path.rename(retained)
    record_path.symlink_to(retained)
    with pytest.raises(OSError):
        placement.root(home, row)


def test_binding_cannot_redirect_relative_root_or_follow_foreign_host(tmp_path):
    home, _, record = owned_checkout(tmp_path)
    row = placement.capture(home, record['path'])
    for field, value in [('relativePath', '../outside'), ('relativePath', str(tmp_path)),
                         ('localHostId', 'different-host')]:
        changed = copy.deepcopy(row)
        changed['storageCheckout'][field] = value
        with pytest.raises(ValueError):
            placement.root(home, changed)
