import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from amplifier_unified_workspaces.owner import Owner,WorkspaceError,identity

class CatalogFixture:
    def __init__(self):self.rows={};self.checkpoints={};self.batches=[];self.session_queries=[]
    async def __call__(self,method,args):
        if method=='workspaceProjectionStatus':return {'revision':self.checkpoints.get(args['source'],0)}
        if method=='projectWorkspaces':
            assert args['afterRevision']==self.checkpoints.get(args['source'],0)
            self.rows.update({r['id']:{**r,'available':Path(r['path']).is_dir(),'availability':'present' if Path(r['path']).is_dir() else 'missing','source':'registered','checkedAt':'now'} for r in args['records']});self.batches.append(len(args['records']));self.checkpoints[args['source']]=args['throughRevision'];return {'revision':args['throughRevision']}
        if method=='getWorkspace':return self.rows.get(args['id'])
        if method=='listWorkspaces':return {'items':[r for r in self.rows.values() if (args.get('includeHidden') or not r['hidden']) and (args.get('includeUnavailable') or r['available'])][:args['limit']]}
        if method=='list':self.session_queries.append(args);return {'items':[]}
        raise AssertionError(method)

def config(tmp_path):
    projects=tmp_path/'projects';projects.mkdir(exist_ok=True)
    return {'stateDirectory':str(tmp_path/'owner'),'allowedRoots':[str(projects)],'defaultRoot':str(projects/'new')}

async def action(owner,operation,args,command='command'):
    return await owner.request('action',{'operation':'workspace.'+operation,'args':args,'commandId':command,'clientId':'client-a'})

async def test_create_repeat_display_rename_remove_preserves_directory_history(tmp_path):
    catalog=CatalogFixture();owner=Owner(config(tmp_path),catalog)
    try:
        plan=await action(owner,'prepare',{'name':'My Project'},'prepare');assert plan['disposition']=='create' and not Path(plan['path']).exists()
        created=await action(owner,'create',{'planId':plan['planId']},'create');path=Path(created['workspace']['path']);(path/'events.jsonl').write_text('retain every byte')
        repeated=await action(owner,'create',{'planId':plan['planId']},'create');assert repeated['receipt']['status']=='completed' and repeated['replayed'] is False
        second=await action(owner,'create',{'planId':plan['planId']},'same-plan-new-command');assert second['workspace']['id']==created['workspace']['id']
        renamed=await action(owner,'rename',{'id':created['workspace']['id'],'name':'Shared / Display','expectedRevision':created['workspace']['revision']},'rename')
        assert renamed['workspace']['path']==str(path) and renamed['workspace']['name']=='Shared / Display'
        with pytest.raises(WorkspaceError,match='revision changed') as error:await action(owner,'remove',{'id':created['workspace']['id'],'expectedRevision':1},'stale')
        assert error.value.executed is False and error.value.receipt['status']=='rejected'
        removed=await action(owner,'remove',{'id':created['workspace']['id'],'expectedRevision':renamed['workspace']['revision']},'remove')
        assert removed['filesDeleted'] is False and removed['historyPreserved'] is True and removed['workspace']['hidden']
        assert (await action(owner,'list',{}))['items']==[] and (path/'events.jsonl').read_text()=='retain every byte'
        restored=await action(owner,'add',{'path':str(path)},'restore');assert not restored['workspace']['hidden'] and restored['workspace']['name']=='Shared / Display'
        await action(owner,'sessions',{'id':restored['workspace']['id'],'limit':3});assert 'parentUri' not in catalog.session_queries[-1]
        await action(owner,'sessions',{'id':restored['workspace']['id'],'parentUri':'ahp-session:/root','limit':3});assert catalog.session_queries[-1]['parentUri']=='ahp-session:/root'
    finally:await owner.close()

async def test_existing_collision_outside_root_symlink_and_owner_lease(tmp_path):
    cfg=config(tmp_path);owner=Owner(cfg,CatalogFixture())
    try:
        with pytest.raises(WorkspaceError,match='already running'):Owner(cfg,CatalogFixture())
        root=Path(cfg['defaultRoot']);root.mkdir();existing=root/'PROJECT';existing.mkdir();(existing/'keep').write_text('untouched')
        plan=await action(owner,'prepare',{'name':'project'},'plan');assert plan['disposition']=='attach'
        with pytest.raises(WorkspaceError,match='explicit attachment'):await action(owner,'create',{'planId':plan['planId']},'do-not-adopt')
        with pytest.raises(WorkspaceError,match='outside'):await action(owner,'add',{'path':str(tmp_path)},'outside')
        link=root/'link';link.symlink_to(tmp_path,target_is_directory=True)
        with pytest.raises(WorkspaceError,match='outside'):await action(owner,'add',{'path':str(link)},'symlink')
        assert (existing/'keep').read_text()=='untouched'
        with pytest.raises(WorkspaceError,match='Unknown workspace'):await action(owner,'add',{'path':str(existing),'overwrite':True},'extra')
    finally:await owner.close()

async def test_plan_race_and_changed_ancestor_are_known_refusals(tmp_path):
    owner=Owner(config(tmp_path),CatalogFixture())
    try:
        first=await action(owner,'prepare',{'name':'Same'},'p1');second=await action(owner,'prepare',{'name':'Same'},'p2')
        await action(owner,'create',{'planId':first['planId']},'one')
        with pytest.raises(WorkspaceError,match='now exists') as error:await action(owner,'create',{'planId':second['planId']},'two')
        assert error.value.executed is False
        root=Path(owner.roots[0]);plan=await action(owner,'prepare',{'name':'Other','root':str(root/'another')},'other')
        root.rename(root.with_name('old'));root.mkdir()
        with pytest.raises(WorkspaceError,match='changed after preparation') as error:await action(owner,'create',{'planId':plan['planId']},'changed')
        assert error.value.executed is False and not Path(plan['path']).exists()
    finally:await owner.close()

async def test_interrupted_mkdir_restart_does_not_replay_or_register(tmp_path):
    cfg=config(tmp_path);owner=Owner(cfg,CatalogFixture());plan=await action(owner,'prepare',{'name':'Interrupted'},'prepare');await owner.close()
    script='''import asyncio,json,os,sys
from amplifier_unified_workspaces.owner import Owner
class Crash(Owner):
 def allocate(self,plan):
  super().allocate(plan);os._exit(73)
async def catalog(*args):return {'revision':0}
async def main():
 o=Crash(json.loads(sys.argv[1]),catalog)
 await o.request('action',{'operation':'workspace.create','args':{'planId':sys.argv[2]},'commandId':'interrupted','clientId':'test'})
asyncio.run(main())'''
    result=subprocess.run([sys.executable,'-c',script,json.dumps(cfg),plan['planId']],capture_output=True)
    assert result.returncode==73,result.stderr.decode()
    owner=Owner(cfg,CatalogFixture())
    try:
        path=Path(plan['path']);before=path.stat().st_ino;receipt=await action(owner,'receipt',{'commandId':'interrupted'})
        assert receipt['status']=='unknown' and receipt['inspection']['exists'] and receipt['inspection']['directory']
        with pytest.raises(WorkspaceError,match='unresolved'):await action(owner,'create',{'planId':plan['planId']},'interrupted')
        with pytest.raises(WorkspaceError,match='unresolved'):await action(owner,'create',{'planId':plan['planId']},'different-id')
        assert path.stat().st_ino==before and owner.local(identity(path)) is None
        attached=await action(owner,'add',{'path':str(path)},'explicit-attach');assert attached['workspace']['path']==str(path)
    finally:await owner.close()

async def test_projection_repair_paged_and_missing_registration_kept_on_disk(tmp_path):
    owner=Owner(config(tmp_path),CatalogFixture())
    try:
        for i in range(251):owner.record(Path(owner.roots[0])/f'missing-{i}','Name '+str(i),i%2==0)
        await owner.synchronize();assert owner.catalog.batches==[100,100,51]
        owner.catalog=CatalogFixture();await owner.synchronize();assert owner.catalog.batches==[100,100,51]
        assert len(owner.catalog.rows)==251 and (await action(owner,'list',{}))['items']==[]
        row=next(iter(owner.catalog.rows.values()));assert (await action(owner,'inspect',{'id':row['id']}))['inspection']=={'exists':False}
    finally:await owner.close()

async def test_changed_command_arguments_and_configuration_cannot_reuse_authority(tmp_path):
    cfg=config(tmp_path);owner=Owner(cfg,CatalogFixture())
    plan=await action(owner,'prepare',{'name':'Project'},'prepare')
    with pytest.raises(WorkspaceError,match='identity was reused'):await action(owner,'prepare',{'name':'Elsewhere'},'prepare')
    await owner.close();cfg['defaultRoot']=str(Path(cfg['allowedRoots'][0])/'different');owner=Owner(cfg,CatalogFixture())
    try:
        with pytest.raises(WorkspaceError,match='stale'):await action(owner,'create',{'planId':plan['planId']},'stale-plan')
        assert not Path(plan['path']).exists()
    finally:await owner.close()


FENCE={'fenceId':'workspace-fence','commandId':'update-command','purpose':'distribution-update','instanceId':'old-host','dataScope':'owned-data'}
def unchanged():return {**FENCE,'outcome':'unchanged','proof':{'verified':True,**FENCE,'outcome':'unchanged','receiptId':'verified-owner-nochange'}}

async def test_quiescence_counts_complete_catalog_sync_and_lock_waiters(tmp_path):
    entered=asyncio.Event();resume=asyncio.Event();catalog=CatalogFixture();idle=[]
    async def callback(method,args):
        if method=='projectWorkspaces':entered.set();await resume.wait()
        return await catalog(method,args)
    async def on_idle():idle.append(True)
    owner=Owner(config(tmp_path),callback,on_idle)
    try:
        plan=await action(owner,'prepare',{'name':'Held catalog'},'prepare')
        creation=asyncio.create_task(action(owner,'create',{'planId':plan['planId']},'create'))
        await entered.wait();assert Path(plan['path']).is_dir()
        queued=asyncio.create_task(action(owner,'prepare',{'name':'Queued intent'},'queued'));await asyncio.sleep(0)
        proof=await owner.request('quiescence/inspect',{});assert proof['calls']==2
        assert (await owner.request('quiescence/acquire',FENCE))['executed'] is False
        resume.set();await asyncio.gather(creation,queued)
        assert (await owner.request('quiescence/acquire',FENCE))['acquired'] is True
        assert len(idle)>=2
        with pytest.raises(WorkspaceError,match='intake is held') as error:await action(owner,'prepare',{'name':'Never admitted'},'denied')
        assert error.value.executed is False and owner.public_receipt('denied') is None
        await owner.request('quiescence/release',unchanged())
    finally:resume.set();await owner.close()

async def test_held_reads_never_repair_catalog_and_release_is_durable_exact(tmp_path):
    cfg=config(tmp_path);catalog=CatalogFixture();owner=Owner(cfg,catalog)
    path=Path(cfg['allowedRoots'][0])/'existing';path.mkdir()
    result=await action(owner,'add',{'path':str(path)},'add');workspace=result['workspace']
    await owner.request('quiescence/acquire',FENCE)
    # Repairable catalog loss does not grant write permission during a held gate.
    catalog.rows.clear();catalog.checkpoints.clear();catalog.batches.clear()
    page=await action(owner,'list',{});assert page['coverage']['projection']['repairDeferred'] is True
    row=await action(owner,'inspect',{'id':workspace['id']});assert row['id']==workspace['id'] and row['projection']['complete'] is False
    await action(owner,'sessions',{'id':workspace['id']});assert catalog.batches==[]
    assert (await action(owner,'receipt',{'commandId':'add'}))['status']=='completed'
    assert (await owner.request('quiescence/release',{**FENCE,'outcome':'unknown'}))['intakeClosed'] is True
    await owner.close();owner=Owner(cfg,catalog)
    try:
        assert (await owner.request('quiescence/inspect',{}))['intakeClosed'] is True
        with pytest.raises(WorkspaceError,match='intake is held'):await action(owner,'add',{'path':str(path)},'blocked-after-restart')
        await owner.request('quiescence/release',unchanged());await owner.close();owner=Owner(cfg,catalog)
        assert (await owner.request('quiescence/release',unchanged()))['released'] is True
        with pytest.raises(ValueError,match='release receipt'):await owner.request('quiescence/release',{**unchanged(),'proof':{**unchanged()['proof'],'receiptId':'different'}})
        page=await action(owner,'list',{});assert len(page['items'])==1 and catalog.batches==[1]
    finally:await owner.close()

async def test_unknown_mkdir_receipt_is_retained_without_becoming_active_work(tmp_path):
    cfg=config(tmp_path);owner=Owner(cfg,CatalogFixture())
    with owner.db() as db:db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?)',('uncertain','workspace.create','{}','admitted',json.dumps({'path':str(Path(cfg['defaultRoot'])/'unknown')}),0))
    await owner.close();owner=Owner(cfg,CatalogFixture())
    try:
        assert (await owner.request('quiescence/inspect',{}))['commands']=={'admitted':0,'unknown':1}
        assert (await owner.request('quiescence/acquire',FENCE))['acquired'] is True
        assert (await action(owner,'receipt',{'commandId':'uncertain'}))['status']=='unknown'
        assert not Path(cfg['defaultRoot']).exists()
        await owner.request('quiescence/release',unchanged())
    finally:await owner.close()
