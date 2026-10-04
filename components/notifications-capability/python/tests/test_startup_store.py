"""Inert fixtures: no HTTP, device notification, reset apply or restore."""
from contextlib import contextmanager, closing
import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path
import pytest
from amplifier_unified_notifications.owner import Owner, STORE_TABLES, existing_store


@contextmanager
def database(path):
    db=sqlite3.connect(path)
    try:
        with db:yield db
    finally:db.close()

def config(tmp_path):return {'stateDirectory':str(tmp_path/'state')}
def close(owner):asyncio.run(owner.close())
def image(path):
    return {suffix:hashlib.sha256(Path(str(path)+suffix).read_bytes()).hexdigest()
            for suffix in ('','-wal','-shm','-journal') if Path(str(path)+suffix).is_file()}
def seed(tmp_path):
    owner=Owner(config(tmp_path));owner.save({'expectedRevision':0,'patch':{'preview':True}},'saved')
    body=json.dumps({'state':'unknown','operation':'apply','commandId':'reset-unknown'})
    with owner.db:owner.db.execute('INSERT INTO app_reset_commands VALUES(?,?,?)',('reset-unknown','authored-fixture',body))
    close(owner);return tmp_path/'state'/'notifications.sqlite'

@pytest.mark.parametrize('table',list(STORE_TABLES))
def test_missing_authority_refuses_without_repair(tmp_path,table):
    path=seed(tmp_path)
    with database(path) as db:db.execute('DROP TABLE '+table)
    before=image(path)
    for _ in range(2):
        with pytest.raises(ValueError,match='explicit recovery'):Owner(config(tmp_path))
        assert image(path)==before
    with database(path) as db:assert not db.execute('SELECT 1 FROM sqlite_schema WHERE name=?',(table,)).fetchone()

@pytest.mark.parametrize('table',['settings','credentials'])
def test_missing_singleton_refuses_before_defaults_or_legacy(tmp_path,table):
    path=seed(tmp_path)
    with database(path) as db:db.execute('DELETE FROM '+table+' WHERE id=1')
    legacy=tmp_path/'legacy.json';legacy.write_text(json.dumps({'push':True,'topic':'fixture'}))
    before=image(path)
    with pytest.raises(ValueError,match='singleton'):Owner({**config(tmp_path),'legacySettingsPath':str(legacy)})
    assert image(path)==before

def test_legacy_four_table_profile_requires_reviewed_migration(tmp_path):
    path=seed(tmp_path)
    with database(path) as db:
        db.execute('DROP TABLE app_reset_commands');db.execute('DROP TABLE app_reset_previews')
    before=image(path)
    with pytest.raises(ValueError,match='reviewed migration'):Owner(config(tmp_path))
    assert image(path)==before

@pytest.mark.parametrize('suffix',['-wal','-shm','-journal'])
@pytest.mark.parametrize('kind',['empty','nonempty','dangling'])
def test_orphan_sidecar_never_bootstraps(tmp_path,suffix,kind):
    directory=tmp_path/'state';directory.mkdir();path=directory/'notifications.sqlite';side=Path(str(path)+suffix)
    if kind=='dangling':side.symlink_to(directory/'absent')
    else:side.write_bytes(b'' if kind=='empty' else b'fixture')
    with pytest.raises(ValueError,match='sidecar'):Owner(config(tmp_path))
    assert not path.exists() and os.path.lexists(side)
    if kind=='dangling':assert side.is_symlink()
    else:assert side.read_bytes()==(b'' if kind=='empty' else b'fixture')

@pytest.mark.parametrize('payload',[b'',b'not sqlite'])
def test_existing_invalid_main_is_not_new(tmp_path,payload):
    directory=tmp_path/'state';directory.mkdir();path=directory/'notifications.sqlite';path.write_bytes(payload)
    before=image(path)
    with pytest.raises(ValueError,match='explicit recovery'):Owner(config(tmp_path))
    assert image(path)==before

def test_healthy_receipts_singletons_indexes_and_new_legacy(tmp_path):
    legacy=tmp_path/'legacy.json';legacy.write_text(json.dumps({'push':True,'topic':'fixture','token':'inert','preview':True}))
    owner=Owner({**config(tmp_path),'legacySettingsPath':str(legacy)})
    assert owner.public()['enabled'] and owner.private()[1]['token']=='inert'
    saved=owner.save({'expectedRevision':0,'patch':{'enabled':False}},'saved');close(owner)
    path=tmp_path/'state'/'notifications.sqlite'
    with database(path) as db:
        for name in ('delivery_order','delivery_status','retention_deliveries','app_reset_pending'):db.execute('DROP INDEX '+name)
        db.execute('INSERT INTO app_reset_commands VALUES(?,?,?)',('unknown','fixture',json.dumps({'state':'unknown','operation':'apply'})))
    owner=Owner(config(tmp_path))
    try:
        assert owner.exact('saved')==saved and owner.public()['revision']==1
        assert owner.save({'expectedRevision':0,'patch':{'enabled':False}},'saved')['revision']==1
        assert owner.app_reset.receipt('unknown')['state']=='unknown'
        assert owner.db.execute("SELECT count(*) FROM sqlite_schema WHERE type='index' AND name IN ('delivery_order','delivery_status','retention_deliveries','app_reset_pending')").fetchone()[0]==4
    finally:close(owner)

@pytest.mark.parametrize('length',[4097,8192])
def test_oversize_ddl_rejected_before_normalization(tmp_path,length):
    path=seed(tmp_path)
    with database(path) as db:
        db.execute('PRAGMA writable_schema=ON')
        definition=STORE_TABLES['settings'];db.execute('UPDATE sqlite_schema SET sql=? WHERE name=?',(definition+' '*(length-len(definition)),'settings'))
        db.execute('PRAGMA schema_version=99')
    before=image(path)
    with pytest.raises(ValueError,match='explicit recovery'):Owner(config(tmp_path))
    assert image(path)==before

def test_read_probe_has_only_bounded_metadata_and_keyed_rows(tmp_path,monkeypatch):
    path=seed(tmp_path);statements=[];original=sqlite3.connect
    def connect(*args,**kwargs):
        assert kwargs.get('uri') and '?mode=ro' in str(args[0])
        db=original(*args,**kwargs);db.set_trace_callback(statements.append);return db
    monkeypatch.setattr(sqlite3,'connect',connect)
    assert existing_store(path)
    assert len(statements)==8
    assert all('substr(sql,1,4097)' in sql or sql in ('SELECT 1 FROM settings WHERE id=1','SELECT 1 FROM credentials WHERE id=1') for sql in statements)


def test_committed_wal_fault_refuses_without_changing_main_or_wal(tmp_path):
    import subprocess,sys
    path=seed(tmp_path)
    script="import sqlite3,os,sys;db=sqlite3.connect(sys.argv[1]);db.execute('PRAGMA wal_autocheckpoint=0');db.execute('DROP TABLE deliveries');db.commit();os._exit(0)"
    subprocess.run([sys.executable,'-I','-B','-c',script,str(path)],check=True)
    assert Path(str(path)+'-wal').stat().st_size>0
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)) as checkpoint:
        assert checkpoint.execute("SELECT 1 FROM sqlite_schema WHERE name='deliveries'").fetchone()
    # SHM is SQLite coordination, not authority; read-only WAL access can update
    # read marks. Main and every committed WAL byte must remain unchanged.
    before={key:value for key,value in image(path).items() if key!='-shm'}
    with pytest.raises(ValueError,match='explicit recovery'):Owner(config(tmp_path))
    assert {key:value for key,value in image(path).items() if key!='-shm'}==before


async def test_unknown_delivery_receipt_never_reinvokes_inert_callback(tmp_path):
    calls=[]
    async def inert(*args):calls.append('attempt');raise RuntimeError('unconfirmed fixture')
    owner=Owner(config(tmp_path),transport=inert)
    owner.save({'expectedRevision':0,'patch':{'enabled':True,'topic':'inert'}},'enabled')
    event={'session':'fixture-session','eventId':'same-event','kind':'response'}
    receipt=await owner.enqueue(event);await asyncio.gather(*list(owner.tasks))
    assert owner.exact(receipt['commandId'])['status']=='unknown';await owner.close()
    owner=Owner(config(tmp_path),transport=inert)
    try:
        assert (await owner.enqueue(event))['status']=='unknown' and calls==['attempt']
    finally:await owner.close()


def test_committed_wal_singleton_loss_is_not_hidden_by_intact_main(tmp_path):
    import subprocess,sys
    path=seed(tmp_path)
    subprocess.run([sys.executable,'-I','-B','-c',"import sqlite3,os,sys;db=sqlite3.connect(sys.argv[1]);db.execute('PRAGMA wal_autocheckpoint=0');db.execute('DELETE FROM settings WHERE id=1');db.commit();os._exit(0)",str(path)],check=True)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)) as checkpoint:
        assert checkpoint.execute('SELECT 1 FROM settings WHERE id=1').fetchone()
    before={k:v for k,v in image(path).items() if k!='-shm'}
    with pytest.raises(ValueError,match='singleton'):Owner(config(tmp_path))
    assert {k:v for k,v in image(path).items() if k!='-shm'}==before


def test_healthy_committed_wal_view_preserves_exact_receipt(tmp_path):
    import subprocess,sys
    path=seed(tmp_path)
    subprocess.run([sys.executable,'-I','-B','-c',"import sqlite3,os,sys;db=sqlite3.connect(sys.argv[1]);db.execute('PRAGMA wal_autocheckpoint=0');db.execute('INSERT INTO commands VALUES(?,?,?)',('wal-only','fixture','{\"status\":\"unknown\"}'));db.commit();os._exit(0)",str(path)],check=True)
    before={k:v for k,v in image(path).items() if k!='-shm'}
    assert existing_store(path)
    assert {k:v for k,v in image(path).items() if k!='-shm'}==before
    owner=Owner(config(tmp_path))
    try:assert owner.exact('wal-only')=={'status':'unknown'}
    finally:close(owner)
