"""Workspace metadata follows registration identity without executing old work."""
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid
import pytest
from amplifier_unified_workspaces.migrate import import_workspaces, forbidden
from amplifier_unified_workspaces.owner import Owner
from amplifier_unified_workspaces.workspace_starters import StarterCatalog
from amplifier_unified_workspaces import workspace_provisioning as setup, workspace_resources as resources
from test_migrate import snapshot, sha


def capture(tmp_path):
    source, destination, config, rows = snapshot(tmp_path, overlays=False)
    root = tmp_path/'metadata';root.mkdir()
    catalog = StarterCatalog(root, [{'value':'retired-bundle'}])
    custom = catalog.command('workspace.starters.save', {'starter':{'name':'Custom retained','instructions':'Keep existing files','scratch':True,'rootGit':False,'bundle':'retired-bundle','trackResources':True}}, 'old-save')
    path = Path(rows[0]['path']);info=path.stat();key=str(uuid.uuid4())
    receipt = setup.initialize(root, {'planId':key,'path':str(path),'directoryIdentity':[info.st_dev,info.st_ino],'starter':custom})
    receipt.update(status='running',scaffolded=True,revision=7,repositories=[{'url':'https://example.invalid/project.git','directory':'project','ref':'','status':'running'}],rootGit={'status':'preserved'})
    setup.receipt_path(root,key).write_text(json.dumps(receipt))
    first=resources.command(root,'first','workspace.resources.add',{'kind':'preview','resourceId':'preview-1','owner':'original chat','note':'Keep until approved'},'old-resource')
    resources.command(root,'first','workspace.resources.status',{'id':first['id'],'expectedRevision':1,'status':'observed_absent','evidence':'Original observation'},'old-status')
    second=resources.command(root,'second','workspace.resources.add',{'kind':'container','resourceId':'container-2','owner':'another chat'},'other-resource')
    with sqlite3.connect(source) as db:
        state=json.loads(db.execute('SELECT value FROM state').fetchone()[0]);state['workspaces'][0]['setupId']=key
        db.execute('UPDATE state SET value=?',(json.dumps(state),))
    manifest=root/'manifest.json';refresh_manifest(root)
    return source,destination,config,rows,root,manifest,receipt,first,second


def refresh_manifest(root):
    files={str(p.relative_to(root)):sha(p) for p in root.rglob('*.json') if p.name!='manifest.json'}
    (root/'manifest.json').write_text(json.dumps({'version':1,'files':files}))


async def migrate(f):
    source,destination,config,_,_,manifest,*_=f
    return await import_workspaces(source,destination,expected_sha256=sha(source),config=config,metadata_manifest=manifest,expected_metadata_sha256=sha(manifest))


async def test_complete_metadata_restart_preserves_ids_bytes_and_no_execution(tmp_path,monkeypatch):
    f=capture(tmp_path);source,dest,config,rows,root,manifest,receipt,first,second=f
    before={p.relative_to(root):p.read_bytes() for p in root.rglob('*.json')};original=source.read_bytes()
    monkeypatch.setattr(setup,'run',lambda *a:pytest.fail('Migration must not start setup'))
    result=await migrate(f)
    assert result['scope']=='workspace-preferences-and-metadata'
    assert result['metadata']['customStarters']==1 and result['metadata']['setupReceipts']==1 and result['metadata']['resourceInventories']==2
    mapping=json.loads((dest/'identity-mapping.json').read_text())
    for _ in range(2):
        owner=Owner({**config,'stateDirectory':str(dest)},forbidden)
        try:
            assert not owner.setup_tasks
            observed=owner.workspace_setup(rows[0]['path'])
            assert observed['status']=='interrupted' and observed['revision']==7 and observed['repositories'][0]['status']=='running'
            assert setup.receipt_path(dest,receipt['id']).read_bytes()==before[Path('workspace-setup')/(receipt['id']+'.json')]
            assert StarterCatalog(dest).listing()['items'][-1]['bundle']=='retired-bundle'
            assert resources.listing(dest,mapping['first'])['resources'][0]['id']==first['id']
            assert resources.listing(dest,mapping['first'])['resources'][0]['status']=='observed_absent'
            assert resources.listing(dest,mapping['second'])['resources'][0]['id']==second['id']
            with owner.db() as db:
                assert db.execute('SELECT count(*) FROM commands').fetchone()[0]==0
                assert db.execute('SELECT count(*) FROM plans').fetchone()[0]==0
            with pytest.raises(ValueError,match='interrupted'):
                recovered=setup.recover(dest,receipt['id']);setup.retry(dest,receipt['id'],recovered['revision'])
            # Restore this projected receipt for the second restart. The retained
            # original is never modified, even by explicit recovery inspection.
            setup.receipt_path(dest,receipt['id']).write_bytes(before[Path('workspace-setup')/(receipt['id']+'.json')])
        finally:await owner.close()
    assert source.read_bytes()==original
    assert {p.relative_to(root):p.read_bytes() for p in root.rglob('*.json')}==before
    assert not (Path(rows[0]['path'])/'project').exists() and not Path(rows[1]['path']).exists()
    for name,raw in before.items():assert (dest/'legacy-workspace-metadata'/name).read_bytes()==raw


@pytest.mark.parametrize('bad',['digest','missing','omitted','path','setup-id','custom-id','resource-id','unknown-status','file-link','directory-link','duplicate-json','oversize'])
async def test_invalid_capture_never_publishes_partial_owner(tmp_path,bad):
    f=capture(tmp_path);source,dest,config,rows,root,manifest,receipt,*_=f
    target=setup.receipt_path(root,receipt['id'])
    if bad=='digest':target.write_text(target.read_text()+' ')
    elif bad=='missing':target.unlink();refresh_manifest(root)
    elif bad=='omitted':
        value=json.loads(manifest.read_text());value['files'].pop('workspace-starters.json');manifest.write_text(json.dumps(value))
    elif bad in {'path','setup-id','unknown-status'}:
        value=json.loads(target.read_text());value[{'path':'path','setup-id':'id','unknown-status':'status'}[bad]]={'path':rows[1]['path'],'setup-id':str(uuid.uuid4()),'unknown-status':'maybe-done'}[bad];target.write_text(json.dumps(value));refresh_manifest(root)
    elif bad=='custom-id':
        target=root/'workspace-starters.json';value=json.loads(target.read_text());value['items'][0]['id']='blank';target.write_text(json.dumps(value));refresh_manifest(root)
    elif bad=='resource-id':
        target=next((root/'workspace-resources').glob('*.json'));value=json.loads(target.read_text());value['resources']*=2;target.write_text(json.dumps(value));refresh_manifest(root)
    elif bad=='file-link':
        other=tmp_path/'original.json';target.rename(other);target.symlink_to(other)
    elif bad=='directory-link':
        other=tmp_path/'original-directory';(root/'workspace-setup').rename(other);(root/'workspace-setup').symlink_to(other,target_is_directory=True)
    elif bad=='duplicate-json':
        target.write_text('{"revision":1,"revision":2}');refresh_manifest(root)
    elif bad=='oversize':target.write_bytes(b' '* (16*1024*1024+1));refresh_manifest(root)
    with pytest.raises((ValueError,OSError)):await migrate(f)
    assert not dest.exists() and not list(tmp_path.glob('.workspace-migration-*'))


async def test_setup_reference_requires_sidecar_and_identity_index_survives_damage(tmp_path):
    f=capture(tmp_path);source,dest,config,*_=f
    with pytest.raises(ValueError,match='metadata capture'):
        await import_workspaces(source,dest,expected_sha256=sha(source),config=config)
    await migrate(f)
    with sqlite3.connect(dest/'workspaces.sqlite') as db:db.execute('DROP TABLE workspace_setup_links')
    with pytest.raises(ValueError,match='authority schema'):
        Owner({**config,'stateDirectory':str(dest)},forbidden)


@pytest.mark.parametrize('ambiguous',[False,True])
async def test_main_uuid_inventory_names_map_without_merging(tmp_path,ambiguous):
    f=capture(tmp_path);source,dest,config,rows,root,manifest,receipt,first,*_=f
    key=str(uuid.uuid4())
    with sqlite3.connect(source) as db:
        value=json.loads(db.execute('SELECT value FROM state').fetchone()[0]);value['workspaces'][0]['id']=key
        db.execute('UPDATE state SET value=?',(json.dumps(value),))
    previous=root/'workspace-resources'/(hashlib.sha256(b'first').hexdigest()+'.json')
    raw=previous.read_bytes();previous.rename(previous.with_name(uuid.UUID(key).hex+'.json'))
    if ambiguous:previous.with_name(hashlib.sha256(key.encode()).hexdigest()+'.json').write_bytes(raw)
    refresh_manifest(root)
    if ambiguous:
        with pytest.raises(ValueError,match='Ambiguous'):await migrate(f)
        assert not dest.exists()
    else:
        await migrate(f);mapping=json.loads((dest/'identity-mapping.json').read_text())
        assert resources.listing(dest,mapping[key])['resources'][0]['id']==first['id']
