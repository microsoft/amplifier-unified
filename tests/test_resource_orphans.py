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
    writer.commit()
    assert writer.execute('SELECT count(*) FROM state_resources').fetchone()[0]==0
    good=files.put(writer,{'good':'body'});writer.commit()
    assert files.resolve(writer,good['$resource'],{'$blob':good['$resource']})=={'good':'body'}
    writer.close();other.close()


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
