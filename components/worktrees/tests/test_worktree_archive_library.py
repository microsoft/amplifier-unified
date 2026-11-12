import os
from pathlib import Path
from unittest.mock import patch

import pytest
from amplifier_worktrees import GitWorktrees
from amplifier_worktrees.git import git
from worktree_fixtures import repository, create


def test_archive_restore_preserves_index_ignored_untracked_links_and_detached_commit(tmp_path):
    source = repository(tmp_path); manager = GitWorktrees(tmp_path / 'managed')
    record = create(manager, source, session_id='s'); path = Path(record['path'])
    git(path, 'config', 'user.name', 'Fixture'); git(path, 'config', 'user.email', 'fixture@example.invalid')
    (path/'a.txt').write_text('committed'); git(path, 'commit', '-am', 'detached work')
    commit = git(path, 'rev-parse', 'HEAD')
    (path/'a.txt').write_text('staged'); git(path, 'add', 'a.txt')
    (path/'a.txt').write_text('unstaged')
    (path/'ignored.txt').write_text('must retain'); (path/'new').write_text('new')
    (path/'link').symlink_to('new')
    before = git(path, 'status', '--porcelain=v1', '-z')
    archived = manager.relocate(record['id'], record['revision'], 'archive')
    target = Path(archived['path'])
    assert archived['status'] == 'archived' and not path.exists()
    assert (target/'ignored.txt').read_text() == 'must retain'
    assert (target/'a.txt').read_text() == 'unstaged' and git(target, 'show', ':a.txt') == b'staged'
    assert (target/'new').read_text() == 'new' and os.readlink(target/'link') == 'new'
    assert git(target, 'rev-parse', 'HEAD') == commit and git(target, 'status', '--porcelain=v1', '-z') == before
    with pytest.raises(ValueError, match='Restore'): manager.remove(record['id'], archived['revision'])
    restored = manager.relocate(record['id'], archived['revision'], 'restore', restore=True)
    assert restored['status'] == 'ready' and restored['path'] == str(path)
    assert (path/'ignored.txt').read_text() == 'must retain'
    duplicate = manager.relocate(record['id'], record['revision'], 'archive')
    assert duplicate['duplicate'] and path.exists() and not target.exists()
    with pytest.raises(ValueError, match='different contents'):
        manager.relocate(record['id'], restored['revision'], 'archive')



@pytest.mark.parametrize('effect', ['before', 'after', 'replacement'])
def test_lost_move_receipt_reconciles_without_replaying(effect, tmp_path):
    source = repository(tmp_path); manager = GitWorktrees(tmp_path/'managed')
    record = create(manager, source); original_save = manager.save
    def fail_after_intent(value):
        if value['status'] != 'archive_pending': raise OSError('lost receipt')
        original_save(value)
    def move(path, *args, **kwargs):
        if args[:2] == ('worktree','move'):
            if effect != 'before': git(path, *args, **kwargs)
            if effect == 'replacement': Path(path).mkdir(); (Path(path)/'unknown').write_text('retain')
            raise OSError('lost move acknowledgement')
        return git(path, *args, **kwargs)
    with patch('amplifier_worktrees.git.git', side_effect=move), patch.object(manager, 'save', side_effect=fail_after_intent):
        with pytest.raises(OSError): manager.relocate(record['id'],record['revision'],'archive')
    fresh = GitWorktrees(tmp_path/'managed')
    with patch('amplifier_worktrees.git.git', side_effect=lambda path,*args,**kw: pytest.fail('Move replayed') if args[:2]==('worktree','move') else git(path,*args,**kw)):
        result=fresh.relocate(record['id'],record['revision'],'archive')
    assert result['operation']['status'] == {'before':'not_applied','after':'applied','replacement':'unknown'}[effect]
    if effect=='replacement': assert (Path(record['path'])/'unknown').read_text()=='retain'



def test_attached_collision_and_symlink_refused(tmp_path):
    source=repository(tmp_path); manager=GitWorktrees(tmp_path/'managed')
    attached=manager.attach(source,source=source,command_id='attach')
    with pytest.raises(ValueError,match='Only app-created'): manager.relocate(attached['id'],attached['revision'],'archive-attached')
    record=create(manager,source); target=manager.directory/'archives'/record['id']; target.parent.mkdir();target.symlink_to(tmp_path/'missing')
    with pytest.raises(ValueError,match='destination'): manager.relocate(record['id'],record['revision'],'collision')
    assert Path(record['path']).exists() and target.is_symlink()



def test_inspection_reconciles_pending_archive_without_retrying_move(tmp_path):
    source=repository(tmp_path);manager=GitWorktrees(tmp_path/'managed');record=create(manager,source)
    save=manager.save
    def lost(value):
        if value['status']=='archive_pending':save(value)
        else:raise OSError('lost final receipt')
    with patch.object(manager,'save',side_effect=lost),pytest.raises(OSError):
        manager.relocate(record['id'],record['revision'],'archive')
    restored=GitWorktrees(tmp_path/'managed')
    with patch('amplifier_worktrees.git.git',side_effect=lambda path,*args,**kw:pytest.fail('Move replayed') if args[:2]==('worktree','move') else git(path,*args,**kw)):
        checked=restored.status(record['id'])
    assert checked['status']=='archived' and checked['archiveOperations']['archive']['status']=='applied'
