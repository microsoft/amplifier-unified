import os
from pathlib import Path

import pytest
from amplifier_worktrees import GitWorktrees
from amplifier_worktrees.git import MAX_BYTES, MAX_FILES, CheckoutNotStarted, git
from worktree_fixtures import repository, create


def test_clean_and_explicit_dirty_copies_preserve_source_index_and_manifest(tmp_path):
    root = repository(tmp_path); manager = GitWorktrees(tmp_path / 'managed')
    (root / 'a.txt').write_text('staged\n'); git(root, 'add', 'a.txt')
    (root / 'a.txt').write_text('staged\nunstaged\n')
    (root / 'new.txt').write_text('new evidence')
    (root / 'link').symlink_to('new.txt')
    before = manager.inspect(root)
    clean = create(manager, root, session_id='s')
    assert (Path(clean['path']) / 'a.txt').read_text() == 'original\n'
    assert not (Path(clean['path']) / 'new.txt').exists()
    carried = create(manager, root, command_id='carry', mode='carry_dirty', branch='task/change', session_id='s')
    target = Path(carried['path'])
    assert (target / 'a.txt').read_text() == 'staged\nunstaged\n'
    assert git(target, 'show', ':a.txt') == b'staged\n'
    assert (target / 'new.txt').read_text() == 'new evidence'
    assert os.readlink(target / 'link') == 'new.txt'
    assert manager.inspect(root)['sourceRevision'] == before['sourceRevision']
    assert len(carried['manifest']['untracked']) == 2
    assert create(manager, root, command_id='carry', mode='carry_dirty', branch='task/change', session_id='s')['duplicate']
    with pytest.raises(ValueError, match='different contents'): create(manager, root, command_id='carry', mode='clean')
    with pytest.raises(ValueError, match='changed or ignored'): manager.remove(carried['id'], carried['revision'])
    (Path(clean['path']) / 'ignored.txt').write_text('must retain')
    with pytest.raises(ValueError, match='changed or ignored'): manager.remove(clean['id'], clean['revision'])
    (Path(clean['path']) / 'ignored.txt').unlink()
    removed = manager.remove(clean['id'], clean['revision'], 'remove')
    assert removed['status'] == 'removed' and root.exists()
    assert manager.remove(clean['id'], clean['revision'], 'remove')['duplicate']



def test_manifest_file_limit_still_refuses_snapshot_and_carry_dirty(tmp_path):
    root = repository(tmp_path); manager = GitWorktrees(tmp_path / 'managed')
    revision = manager.inspect(root)['sourceRevision']
    contents = {f'evidence-{index}.txt': f'evidence {index}\n'.encode() for index in range(MAX_FILES + 1)}
    for name, value in contents.items():
        (root / name).write_bytes(value)
    with pytest.raises(ValueError, match='Too many untracked files'):
        manager.inspect(root)
    with pytest.raises(ValueError, match='Too many untracked files'):
        manager.create(root, command_id='oversized-carry', expected_revision=revision, mode='carry_dirty')
    assert not manager.records()
    assert {name: (root / name).read_bytes() for name in contents} == contents
    assert (root / 'a.txt').read_bytes() == b'original\n'



def test_clean_commit_does_not_scan_or_copy_dirty_and_oversized_untracked_source(tmp_path, monkeypatch):
    import amplifier_worktrees.git as owner
    root = repository(tmp_path); manager = GitWorktrees(tmp_path / 'managed')
    (root / 'a.txt').write_text('staged\n'); git(root, 'add', 'a.txt')
    (root / 'a.txt').write_text('unstaged\n')
    with (root / 'retained-evidence.bin').open('wb') as stream:
        stream.truncate(MAX_BYTES + 1)
    status, index = git(root, 'status', '--porcelain=v1'), git(root, 'show', ':a.txt')
    def no_snapshot(*args, **kwargs):
        pytest.fail('Clean committed-source operations must not inspect local file content')
    monkeypatch.setattr(owner, 'snapshot', no_snapshot)
    inspected = manager.inspect(root, mode='clean')
    assert inspected['sourceKind'] == 'commit' and 'sourceRevision' not in inspected
    created = manager.create(root, command_id='clean-commit', expected_commit=inspected['expectedCommit'])
    assert created['head'] == inspected['expectedCommit'] and 'sourceRevision' not in created
    assert Path(created['path'], 'a.txt').read_text() == 'original\n'
    assert not Path(created['path'], 'retained-evidence.bin').exists()
    assert git(root, 'status', '--porcelain=v1') == status and git(root, 'show', ':a.txt') == index
    assert (root / 'a.txt').read_text() == 'unstaged\n'
    assert (root / 'retained-evidence.bin').stat().st_size == MAX_BYTES + 1
    assert manager.create(root, command_id='clean-commit', expected_commit=inspected['expectedCommit'])['duplicate']



def test_clean_binding_ref_movement_and_snapshot_compatibility(tmp_path):
    root = repository(tmp_path); manager = GitWorktrees(tmp_path / 'managed')
    old = manager.inspect(root, mode='clean', ref='main')['expectedCommit']
    snapshot = manager.inspect(root)['sourceRevision']
    (root / 'a.txt').write_text('next\n'); git(root, 'commit', '-am', 'next')
    current = manager.inspect(root, mode='clean')['expectedCommit']
    for command, kwargs in [
        ('moved', {'expected_commit': old, 'ref': 'main'}),
        ('snapshot', {'expected_revision': snapshot}),
        ('mixed', {'expected_revision': snapshot, 'expected_commit': old}),
        ('carry', {'expected_commit': old, 'mode': 'carry_dirty'}),
        ('short', {'expected_commit': old[:8]}),
    ]:
        with pytest.raises(CheckoutNotStarted):
            manager.create(root, command_id=command, **kwargs)
    assert not manager.records()
    assert len(git(root, 'worktree', 'list', '--porcelain').split(b'worktree ')) == 2
    pinned = manager.create(root, command_id='pinned', expected_commit=old)
    assert Path(pinned['path'], 'a.txt').read_text() == 'original\n'
    with pytest.raises(ValueError, match='different contents'):
        manager.create(root, command_id='pinned', expected_commit=current)
    # Existing snapshot callers retain their original binding, including clean.
    legacy = create(manager, root, command_id='legacy')
    assert legacy['sourceRevision'] == manager.inspect(root)['sourceRevision']
    assert 'expectedCommit' not in legacy



def test_clean_after_admission_failure_is_never_claimed_not_started(tmp_path):
    root = repository(tmp_path); manager = GitWorktrees(tmp_path / 'managed')
    commit = manager.inspect(root, mode='clean')['expectedCommit']
    def admit_then_fail(effect):
        effect()
        raise ValueError('Acknowledgement lost after durable admission')
    with pytest.raises(ValueError) as failure:
        manager.create(root, command_id='lost-ack', expected_commit=commit, admit=admit_then_fail)
    assert not isinstance(failure.value, CheckoutNotStarted)
    assert manager.records()[0]['status'] == 'creating'
    with pytest.raises(ValueError) as failure:
        manager.create(root, command_id='branch-conflict', expected_commit=commit, branch='main')
    assert not isinstance(failure.value, CheckoutNotStarted)
    assert any(row['status'] == 'partial' for row in manager.records())



def test_stale_branch_conflict_and_attached_ownership(tmp_path):
    root = repository(tmp_path); manager = GitWorktrees(tmp_path / 'managed')
    revision = manager.inspect(root)['sourceRevision']
    (root / 'a.txt').write_text('later')
    with pytest.raises(ValueError, match='changed'): manager.create(root, command_id='stale', expected_revision=revision)
    with pytest.raises(ValueError, match='partial checkout'): create(manager, root, command_id='branch-conflict', branch='main')
    partial = next(row for row in manager.records() if row['status'] == 'partial')
    assert partial['manifest']['sourceUnchanged']
    attached = manager.attach(root, source=root, command_id='attach', session_id='s')
    with pytest.raises(ValueError, match='Only app-created'): manager.remove(attached['id'], 1)
    with pytest.raises(ValueError, match='normal Git ref'): create(manager, root, command_id='bad-ref', ref='--exec=bad')
    assert (root / 'a.txt').read_text() == 'later'



def test_unmerged_index_refuses_carry_and_source_stays_intact(tmp_path):
    root = repository(tmp_path); manager = GitWorktrees(tmp_path / 'managed')
    git(root, 'checkout', '-b', 'other'); (root / 'a.txt').write_text('other\n'); git(root, 'commit', '-am', 'other')
    git(root, 'checkout', 'main'); (root / 'a.txt').write_text('main\n'); git(root, 'commit', '-am', 'main')
    with pytest.raises(ValueError): git(root, 'merge', 'other')
    original = (root / 'a.txt').read_bytes()
    with pytest.raises(ValueError, match='index conflicts'): create(manager, root, mode='carry_dirty')
    assert (root / 'a.txt').read_bytes() == original and git(root, 'ls-files', '-u')



def test_unsupported_index_flags_and_nested_storage_preserve_originals(tmp_path):
    root = repository(tmp_path); manager = GitWorktrees(tmp_path / 'managed')
    (root / 'planned.txt').write_text('not staged yet')
    git(root, 'add', '-N', 'planned.txt')
    with pytest.raises(ValueError, match='index flags'): create(manager, root, mode='carry_dirty')
    assert (root / 'planned.txt').read_text() == 'not staged yet'
    nested = GitWorktrees(root / 'app-state')
    with pytest.raises(ValueError, match='outside'): create(nested, root)



def test_cleanup_keeps_detached_commits_until_they_have_a_retained_ref(tmp_path):
    root = repository(tmp_path); manager = GitWorktrees(tmp_path / 'managed')
    record = create(manager, root); target = Path(record['path'])
    (target / 'a.txt').write_text('committed independent work\n')
    git(target, 'commit', '-am', 'independent work')
    with pytest.raises(ValueError, match='Detached commits'): manager.remove(record['id'], record['revision'])
    git(target, 'branch', 'keep-independent-work')
    assert manager.remove(record['id'], record['revision'])['status'] == 'removed'
    assert git(root, 'show', 'keep-independent-work:a.txt') == b'committed independent work\n'
