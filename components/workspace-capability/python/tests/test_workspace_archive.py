import json
from pathlib import Path
import pytest
from amplifier_session_catalog import Catalog
from amplifier_unified_workspaces.owner import Owner, WorkspaceError
from amplifier_unified_workspaces import workspace_navigation as nav
from test_owner import config, action


async def test_archive_restore_preserves_pins_files_history_and_exact_receipts(tmp_path):
    catalog = Catalog(tmp_path/'catalog.sqlite')
    methods = {'projectWorkspaces':catalog.project_workspaces,
               'workspaceProjectionStatus':catalog.workspace_projection_status,
               'getWorkspace':catalog.get_workspace,'listWorkspaces':catalog.list_workspaces,
               'list':catalog.list}
    async def callback(method, args):return methods[method](**args)
    cfg=config(tmp_path);owner=Owner(cfg,callback)
    try:
        await owner.request('initialize',{'workspaceArchiveVersion':1})
        rows=[]
        for name in ('Alpha','Beta','Gamma'):
            path=Path(owner.roots[0])/name;path.mkdir()
            row=(await action(owner,'add',{'path':str(path)},'add-'+name))['workspace'];rows.append(row)
            pins=await action(owner,'pins',{})
            await action(owner,'pin',{'id':row['id'],'pinned':True,'expectedRevision':pins['revision']},'pin-'+name)
        row=rows[1];path=Path(row['path']);(path/'user-file').write_bytes(b'unchanged\x00\xff')
        catalog.upsert({'uri':'ahp-session:/retained','nativeSessionId':'retained','engineId':'native','workingDirectory':str(path),'title':'Running work is unrelated'})
        history=catalog.get('ahp-session:/retained');pins=await action(owner,'pins',{})
        args={'id':row['id'],'expectedRevision':row['revision']}
        archived=await action(owner,'archive',args,'archive')
        assert archived['workspace']['archived'] and not archived['workStopped']
        assert [r['id'] for r in (await action(owner,'list',{}))['items']]==[rows[0]['id'],rows[2]['id']]
        assert (await action(owner,'list',{'archive':'archived','query':'Beta'}))['items'][0]['id']==row['id']
        assert (await action(owner,'pins',{}))['ids']==[rows[0]['id'],rows[2]['id']]
        with owner.db() as db:assert nav.saved(db)['ids']==pins['ids']
        assert catalog.get('ahp-session:/retained')==history
        assert (await action(owner,'sessions',{'id':row['id']}))['items'][0]['uri']==history['uri']
        with pytest.raises(WorkspaceError,match='changed'):
            await action(owner,'pinOrder',{'ids':pins['ids'],'expectedRevision':pins['revision']},'stale-order')
        with pytest.raises(WorkspaceError,match='changed'):
            await action(owner,'restore',args,'stale-restore')
        # Missing folders remain findable and restorable without recreating them.
        moved=path.with_name('moved');path.rename(moved)
        await owner.close();owner=Owner(cfg,callback);await owner.request('initialize',{'workspaceArchiveVersion':1})
        assert (await action(owner,'archive',args,'archive'))['workspace']['revision']==archived['workspace']['revision']
        restored=await action(owner,'restore',{'id':row['id'],'expectedRevision':archived['workspace']['revision']},'restore')
        assert not restored['workspace']['archived'] and not path.exists()
        assert (await action(owner,'pins',{}))['ids']==pins['ids']
        assert (moved/'user-file').read_bytes()==b'unchanged\x00\xff'
        assert catalog.get('ahp-session:/retained')['title']==history['title']
        assert (await action(owner,'receipt',{'commandId':'archive'}))['status']=='completed'
    finally:await owner.close()


async def test_unadvertised_unknown_and_held_archive_never_repeat(tmp_path):
    from test_owner import CatalogFixture,FENCE
    owner=Owner(config(tmp_path),CatalogFixture())
    try:
        path=Path(owner.roots[0])/'work';path.mkdir();row=owner.record(path,'Work',False)
        args={'id':row['id'],'expectedRevision':row['revision']}
        with pytest.raises(WorkspaceError,match='unavailable'):await action(owner,'archive',args,'unadvertised')
        await owner.request('initialize',{'workspaceArchiveVersion':1})
        with owner.db() as db:db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?)',('unknown','workspace.archive',json.dumps(args,sort_keys=True),'unknown','{}',0))
        with pytest.raises(WorkspaceError,match='unresolved'):await action(owner,'archive',args,'unknown')
        await owner.request('quiescence/acquire',FENCE)
        with pytest.raises(WorkspaceError,match='held'):await action(owner,'archive',args,'held')
        assert owner.public_receipt('held') is None and not owner.local(row['id'])['archived']
    finally:await owner.close()
