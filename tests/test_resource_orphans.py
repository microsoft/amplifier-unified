"""Resource rollback/crash cleanup excludes writers and retains unknown graphs."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from amplifier_web import resource_files as files
from amplifier_web.host.storage import SessionStore
from test_automatic_history import app_factory


def database(path):
    db = sqlite3.connect(path, timeout=0.02)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS state_resources(id TEXT PRIMARY KEY,value TEXT)')
    db.commit()
    return db


def finish(db, state):
    cursor = None
    counts = []
    try:
        while True:
            cursor, result = files.sweep_unindexed(db, state, cursor, limit=2)
            counts.append(result)
            if result['complete'] or result['blocked']:
                return counts
    finally:
        if cursor is not None: cursor.close()


def test_rollback_orphans_and_temp_files_are_bounded_and_shared_payload_survives(tmp_path):
    db = database(tmp_path/'app.sqlite3')
    retained = files.put(db, {'keep':'retained'})
    db.commit()
    expected = files.resolve(db, retained['$resource'], {'$blob':retained['$resource']})
    for i in range(7):
        files.put(db, {'failed':i})
        db.rollback()
    root = files.root(db)
    (root/'.checkpoint-abandoned').write_text('partial')
    (root/'leave.txt').write_text('unowned')
    (root/('f'*64+'.json')).symlink_to(tmp_path/'outside')
    counts = finish(db, {'value':retained})
    assert len(counts)>1 and all(row['inspected']<=2 for row in counts)
    assert sum(row['removed'] for row in counts)==8
    assert (root/'leave.txt').exists() and (root/('f'*64+'.json')).is_symlink()
    assert files.resolve(db, retained['$resource'], {'$blob':retained['$resource']}) == expected
    db.close()


def test_uncertain_references_and_failed_presentation_refuse_orphan_cleanup(tmp_path):
    db=database(tmp_path/'app.sqlite3')
    orphan=files.put(db,{'failed':'original'});db.rollback()
    path=files.root(db)/(orphan['$resource']+'.json')
    for state in ({'missing':orphan}, {'_viewRecoveryPending':True}):
        counts=finish(db,state)
        assert counts[-1]['blocked'] and path.exists()
    counts=finish(db,{})
    assert counts[-1]['complete'] and not path.exists()
    db.close()


def test_writer_blocks_sweep_until_commit_or_rollback_and_reuse_survives(tmp_path):
    writer=database(tmp_path/'app.sqlite3');reader=database(tmp_path/'app.sqlite3')
    ref=files.put(writer,{'value':'pending'})
    with pytest.raises(sqlite3.OperationalError):files.sweep_unindexed(reader,{})
    assert not reader.in_transaction
    writer.commit()
    assert finish(reader,{})[-1]['complete']
    path=files.root(writer)/(ref['$resource']+'.json');assert path.exists()
    stale=files.collect(reader,{})
    reader.commit()
    assert ref['$resource'] in stale
    files.put(writer,{'value':'pending'});writer.commit()
    files.remove_files(reader,stale)
    assert path.exists(), 'post-commit re-adoption must win over stale cleanup list'
    files.put(writer,{'other':'aborted'});writer.rollback()
    assert sum(x['removed'] for x in finish(reader,{}))==1
    writer.close();reader.close()


def test_index_precedes_file_creation_and_failed_file_has_no_lingering_index(tmp_path,monkeypatch):
    writer=database(tmp_path/'app.sqlite3');other=database(tmp_path/'app.sqlite3')
    original=SessionStore._atomic
    def fail(path,text):
        assert writer.in_transaction
        with pytest.raises(sqlite3.OperationalError):files.sweep_unindexed(other,{})
        raise OSError('full disk')
    with monkeypatch.context() as patch:
        patch.setattr(SessionStore,'_atomic',staticmethod(fail))
        with pytest.raises(OSError):files.put(writer,{'not':'written'})
    assert not writer.in_transaction
    other.execute('BEGIN IMMEDIATE');other.rollback()
    assert writer.execute('SELECT count(*) FROM state_resources').fetchone()[0]==0
    good=files.put(writer,{'good':'body'});writer.commit()
    assert files.resolve(writer,good['$resource'],{'$blob':good['$resource']})=={'good':'body'}
    writer.close();other.close()


def test_failed_put_preserves_callers_existing_transaction(tmp_path, monkeypatch):
    db=database(tmp_path/'app.sqlite3')
    db.execute('CREATE TABLE owned(value TEXT)')
    db.execute("INSERT INTO owned VALUES ('earlier work')")
    with monkeypatch.context() as patch:
        patch.setattr(SessionStore, '_atomic', staticmethod(lambda *args: (_ for _ in ()).throw(OSError('disk'))))
        with pytest.raises(OSError):files.put(db,{'fail':'value'})
    assert db.in_transaction
    assert db.execute('SELECT value FROM owned').fetchone()[0]=='earlier work'
    assert db.execute('SELECT count(*) FROM state_resources').fetchone()[0]==0
    db.rollback();db.close()


def test_staging_rechecks_hash_adopted_after_prune_commit(tmp_path):
    from amplifier_web.managed_deletion import _stage, _purge
    db=database(tmp_path/'app.sqlite3')
    ref=files.put(db,{'same':'content'});db.commit()
    path=files.root(db)/(ref['$resource']+'.json')
    stat=path.stat()
    plan={'token':'test','paths':[{'path':str(path),'device':stat.st_dev,'inode':stat.st_ino}]}
    db.execute('DELETE FROM state_resources');db.commit()
    other=database(tmp_path/'app.sqlite3')
    files.put(other,{'same':'content'});other.commit()
    db.execute('BEGIN IMMEDIATE')
    _stage(plan,db=db)
    db.rollback()
    assert path.exists()
    assert not path.with_name('.unified-delete-test-'+path.name).exists()
    other.close();db.close()


async def test_refused_and_successful_preview_do_not_sweep_files(app_factory):
    from test_live_clients import command
    from amplifier_web.service import AppError
    app=app_factory()
    app.clients.attach('web')
    made=await command(app,'web','session.create',{'location':{'kind':'managed'}})
    sid=made['sessionId']
    failed=files.put(app.db,{'failed':'unique orphan'});app.db.rollback()
    path=files.root(app.db)/(failed['$resource']+'.json')
    app._session(sid)['status']='working'
    with pytest.raises(AppError):
        await command(app,'web','session.deletePreview',{'id':sid})
    assert path.exists()
    app._session(sid)['status']='idle'
    preview=(await command(app,'web','session.deletePreview',{'id':sid}))['result']
    assert path.exists(), 'A read-only preview must not remove orphan files'
    deleted=await command(app,'web','session.delete',{'id':sid,'confirmationToken':preview['confirmationToken']})
    assert not deleted['result']['cleanupPending'] and not path.exists()


async def test_confirmed_reconciliation_marks_once_across_directory_batches(app_factory,monkeypatch):
    from amplifier_web.managed_deletion import _reconcile_resources
    app=app_factory()
    for i in range(260):
        files.put(app.db, {'orphan':i});app.db.rollback()
    original=files.marked_references
    calls=[]
    def observe(*args):
        calls.append(1);return original(*args)
    monkeypatch.setattr(files,'marked_references',observe)
    async with app.lock:
        await _reconcile_resources(app)
    assert len(calls)==1


def test_put_rejects_truncated_existing_file_without_indexing_it(tmp_path):
    db=database(tmp_path/'app.sqlite3')
    ref=files.put(db,{'exact':'original'})
    db.rollback()
    path=files.root(db)/(ref['$resource']+'.json')
    path.write_text('{}')
    with pytest.raises(ValueError,match='conflicts'):
        files.put(db,{'exact':'original'})
    assert path.read_text()=='{}'
    assert not db.in_transaction
    assert db.execute('SELECT count(*) FROM state_resources').fetchone()[0]==0
    db.close()


async def test_maintenance_commit_gate_cursor_and_backup_deferral(app_factory,monkeypatch):
    from amplifier_web import storage_migration
    app=app_factory()
    for i in range(260):
        files.put(app.db,{'orphan-maintenance':i});app.db.rollback()
    calls=[]
    original=files.marked_references
    def mark(*args):
        calls.append(1);return original(*args)
    monkeypatch.setattr(files,'marked_references',mark)
    app.backup_in_progress=True
    app._last_storage_sweep=0
    storage_migration.maintenance(app)
    assert not calls and getattr(app,'_resource_scan',None) is None
    app.backup_in_progress=False
    app._save_changes()
    assert not app.db.in_transaction
    assert app._resource_scan is not None
    first=len(calls)
    storage_migration.maintenance(app)
    assert len(calls)==first, '60-second gate should not mark again'
    app._last_storage_sweep=0
    app._save_changes()
    assert not app.db.in_transaction
    await app.close()
    assert getattr(app,'_resource_scan',None) is None


async def test_deletion_lock_interruption_leaves_cleanup_pending(app_factory,monkeypatch):
    from amplifier_web import managed_deletion
    from test_live_clients import command
    app=app_factory();app.clients.attach('web')
    made=await command(app,'web','session.create',{'location':{'kind':'managed'}})
    sid=made['sessionId']
    preview=(await command(app,'web','session.deletePreview',{'id':sid}))['result']
    # Prevent normal periodic GC from pre-clearing the deliberately large batch.
    import time
    app._last_storage_sweep=time.monotonic()
    for i in range(260):
        files.put(app.db,{'unindexed':i});app.db.rollback()
    real_sleep=managed_deletion.asyncio.sleep
    async def break_lock(delay):
        app.db.rollback()
        await real_sleep(0)
    with monkeypatch.context() as patch:
        patch.setattr(managed_deletion.asyncio,'sleep',break_lock)
        result=await command(app,'web','session.delete',{'id':sid,'confirmationToken':preview['confirmationToken']})
    assert result['result']['deleted'] and result['result']['cleanupPending']
    assert app.db.execute('SELECT phase FROM managed_deletions WHERE id=?',(sid,)).fetchone()[0]=='confirmed'
    from amplifier_web import state_records
    saved=state_records.load(app.db)
    managed_deletion.recover(app.data_dir,app.db,saved)
    assert app.db.execute('SELECT phase FROM managed_deletions WHERE id=?',(sid,)).fetchone()[0]=='done'
    indexed={identity+'.json' for identity, in app.db.execute('SELECT id FROM state_resources')}
    assert {path.name for path in files.root(app.db).glob('*.json')} <= indexed


@pytest.mark.parametrize('blocker', ['backup', 'uncertain_graph'])
async def test_confirmed_deletion_defers_all_cleanup_then_recovers(app_factory, monkeypatch, blocker):
    """Document the conservative gate: no physical-purge success while uncertain."""
    from amplifier_web import managed_deletion, state_records
    from test_live_clients import command
    app = app_factory()
    app.clients.attach('web')
    made = await command(app, 'web', 'session.create', {'location': {'kind': 'managed'}})
    sid = made['sessionId']
    folder = Path(app._session(sid)['workspace'])
    (folder/'owned.txt').write_text('retained until cleanup is safe')
    preview = (await command(app, 'web', 'session.deletePreview', {'id': sid}))['result']
    with monkeypatch.context() as patch:
        if blocker == 'backup':
            patch.setattr(app, 'backup_in_progress', True, raising=False)
        else:
            patch.setattr(files, 'marked_references', lambda *args: None)
        result = await command(app, 'web', 'session.delete',
                               {'id': sid, 'confirmationToken': preview['confirmationToken']})
        assert result['result']['deleted'] and result['result']['cleanupPending']
        assert result['result']['warning']
        assert (folder/'owned.txt').read_text() == 'retained until cleanup is safe'
        assert app.db.execute('SELECT phase FROM managed_deletions WHERE id=?', (sid,)).fetchone()[0] == 'confirmed'
    assert all(row['id'] != sid for row in state_records.load(app.db)['sessions'])
    managed_deletion.recover(app.data_dir, app.db, state_records.load(app.db))
    assert not folder.exists()
    assert app.db.execute('SELECT phase FROM managed_deletions WHERE id=?', (sid,)).fetchone()[0] == 'done'


@pytest.mark.parametrize('point',['renamed','temporary'])
def test_process_exit_before_commit_recovers_unindexed_resources(tmp_path,point):
    db=database(tmp_path/'app.sqlite3');keep=files.put(db,{'keep':'exact'});db.commit()
    code='''
import os,sqlite3,sys
from pathlib import Path
from amplifier_web.resource_files import put,root
from amplifier_web.host.storage import SessionStore
p,point=sys.argv[1:]
db=sqlite3.connect(p)
if point=='temporary':
 def die(path,text):
  (path.parent/'.checkpoint-crashed').write_text(text)
  os._exit(71)
 SessionStore._atomic=staticmethod(die)
put(db,{'crashed':'never committed'})
os._exit(72)
'''
    process=subprocess.run([sys.executable,'-c',code,str(tmp_path/'app.sqlite3'),point])
    assert process.returncode == (71 if point=='temporary' else 72)
    counts=finish(db,{'keep':keep})
    assert sum(row['removed'] for row in counts)==1
    assert files.resolve(db,keep['$resource'],{'$blob':keep['$resource']})=={'keep':'exact'}
    db.close()
