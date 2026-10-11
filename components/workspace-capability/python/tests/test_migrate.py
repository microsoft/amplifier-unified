import hashlib
import json
import os
from pathlib import Path
import sqlite3
import pytest

from amplifier_unified_workspaces.migrate import import_workspaces, forbidden
from amplifier_unified_workspaces.owner import Owner, identity
from amplifier_unified_workspaces import workspace_navigation as nav


def snapshot(tmp_path, *, overlays=True):
    workspace = tmp_path/'work'; workspace.mkdir()
    (workspace/'existing').mkdir()
    rows = [{'id':'first', 'name':'Saved alias', 'path':str(workspace/'existing')},
            {'id':'second', 'name':'Missing folder', 'path':str(workspace/'missing')}]
    source = tmp_path/'snapshot.sqlite3'
    db = sqlite3.connect(source)
    db.execute('CREATE TABLE state(id INTEGER PRIMARY KEY,value TEXT NOT NULL)')
    db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,fingerprint TEXT,receipt TEXT)')
    db.execute('INSERT INTO commands VALUES(?,?,?)', ('retained-unknown','original','{"status":"unknown"}'))
    base = dict(revision=1, workspaces=rows, pinnedWorkspaceIds=['first'])
    db.execute('INSERT INTO state VALUES(1,?)', (json.dumps(base),))
    if overlays:
        db.execute('CREATE TABLE state_records(kind TEXT,id TEXT,value TEXT,PRIMARY KEY(kind,id))')
        for key, value in [('workspaces',rows),('pinnedWorkspaceIds',['second','first','second','removed'])]:
            db.execute('INSERT INTO state_records VALUES(?,?,?)', ('global',key,json.dumps(dict(present=True,value=value))))
    db.commit(); db.close()
    return source, tmp_path/'target', dict(allowedRoots=[str(workspace)],defaultRoot=str(workspace)), rows


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def migrate(source, destination, config):
    return await import_workspaces(source,destination,expected_sha256=sha(source),config=config)


@pytest.mark.parametrize('overlays',[False, True])
async def test_legacy_base_and_latest_overlays_survive_owner_restart(tmp_path,overlays):
    source,destination,config,rows=snapshot(tmp_path,overlays=overlays); before=source.read_bytes()
    result=await migrate(source,destination,config)
    assert result['pins']==(2 if overlays else 1) and result['workReplayed'] is False
    assert result['ignoredStaleOrDuplicatePins']==(2 if overlays else 0)
    assert not Path(rows[1]['path']).exists() and source.read_bytes()==before
    assert (destination/'legacy-app.sqlite3').read_bytes()==before
    assert os.stat(destination).st_mode & 0o777 == 0o700
    assert os.stat(destination/'legacy-app.sqlite3').st_mode & 0o777 == 0o600
    mapping=json.loads((destination/'identity-mapping.json').read_text())
    assert mapping=={row['id']:identity(row['path']) for row in rows}
    for _ in range(2):
        owner=Owner({**config,'stateDirectory':str(destination)},forbidden)
        try:
            page=nav.page(owner,{},'reader')
            expected=['second','first'] if overlays else ['first']
            assert page['ids']==[mapping[id] for id in expected]
            assert [r['name'] for r in page['items']]==(['Missing folder','Saved alias'] if overlays else ['Saved alias'])
            if overlays:assert page['items'][0]['availability']=='missing'
            with owner.db() as db:assert db.execute('SELECT count(*) FROM commands').fetchone()[0]==0
        finally:await owner.close()
    with pytest.raises(ValueError,match='already exists'):
        await migrate(source,destination,config)
    assert source.read_bytes()==before


@pytest.mark.parametrize('bad',['duplicate-id','same-path','outside','unresolved','bad-pins','missing-registry','bad-overlay'])
async def test_invalid_or_ambiguous_preferences_publish_nothing(tmp_path,bad):
    source,destination,config,rows=snapshot(tmp_path)
    db=sqlite3.connect(source)
    if bad=='duplicate-id':rows[1]['id']=rows[0]['id']
    elif bad=='same-path':rows[1]['path']=rows[0]['path']
    elif bad=='outside':rows[1]['path']=str(tmp_path/'outside')
    elif bad=='unresolved':rows[1]['path']=None
    key='workspaces';value=dict(present=True,value=rows)
    if bad=='bad-pins':key='pinnedWorkspaceIds';value=dict(present=True,value='broken')
    elif bad=='missing-registry':value=dict(present=False,value=None)
    elif bad=='bad-overlay':value={'value':rows}
    db.execute('UPDATE state_records SET value=? WHERE kind=? AND id=?',(json.dumps(value),'global',key))
    db.commit();db.close();before=source.read_bytes()
    with pytest.raises(ValueError):await migrate(source,destination,config)
    assert not destination.exists() and source.read_bytes()==before
    assert not list(tmp_path.glob('.workspace-migration-*'))
    assert not Path(rows[1]['path'] or tmp_path/'unresolved').exists() or bad=='same-path'


async def test_capture_identity_and_closed_snapshot_required(tmp_path):
    source,destination,config,rows=snapshot(tmp_path)
    with pytest.raises(ValueError,match='changed'):
        await import_workspaces(source,destination,expected_sha256='0'*64,config=config)
    companion=Path(str(source)+'-wal');companion.write_bytes(b'pending')
    with pytest.raises(ValueError,match='closed'):
        await migrate(source,destination,config)
    companion.unlink();link=tmp_path/'linked.sqlite3';link.symlink_to(source)
    with pytest.raises(OSError):await migrate(link,destination,config)
    assert not destination.exists()


async def test_failure_during_conversion_does_not_publish_partial_owner(tmp_path,monkeypatch):
    source,destination,config,rows=snapshot(tmp_path);before=source.read_bytes()
    original=Owner.record;calls=0
    def fail(owner,*args):
        nonlocal calls
        calls+=1
        if calls==2:raise RuntimeError('simulated storage failure')
        return original(owner,*args)
    monkeypatch.setattr(Owner,'record',fail)
    with pytest.raises(RuntimeError,match='storage failure'):await migrate(source,destination,config)
    assert not destination.exists() and source.read_bytes()==before
    assert not list(tmp_path.glob('.workspace-migration-*'))


async def test_explicit_overlay_removal_does_not_resurrect_base_pins(tmp_path):
    source,destination,config,rows=snapshot(tmp_path)
    db=sqlite3.connect(source)
    db.execute("UPDATE state_records SET value=? WHERE kind='global' AND id='pinnedWorkspaceIds'",(json.dumps(dict(present=False,value=None)),))
    db.commit();db.close()
    assert (await migrate(source,destination,config))['pins']==0


async def test_latest_overlay_replaces_obsolete_base_values(tmp_path):
    source,destination,config,rows=snapshot(tmp_path)
    db=sqlite3.connect(source)
    db.execute('UPDATE state SET value=? WHERE id=1',(json.dumps(dict(revision=0,workspaces='obsolete',pinnedWorkspaceIds=None)),))
    db.commit();db.close()
    assert (await migrate(source,destination,config))['pins']==2
