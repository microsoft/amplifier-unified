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
        if method in {'list','libraryQuery'}:self.session_queries.append(args);return {'items':[]}
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

async def test_global_session_pages_keep_authority_and_never_broaden_an_invalid_selection(tmp_path):
    cfg=config(tmp_path);catalog=CatalogFixture();owner=Owner(cfg,catalog)
    try:
        await action(owner,'sessions',{'query':'Saved','archive':'archived','limit':3,'cursor':'bounded-page'})
        query=catalog.session_queries[-1]
        assert query=={'connectionId':owner.scope('client-a'),'limit':3,'allowedWorkspaceRoots':owner.roots,'archive':'archived','search':'Saved','cursor':'bounded-page'}
        for invalid in (None,'','workspace:unknown'):
            with pytest.raises(WorkspaceError):await action(owner,'sessions',{'id':invalid})
        assert len(catalog.session_queries)==1
        with pytest.raises(WorkspaceError):await action(owner,'sessions',{'archive':'historical'})
        with pytest.raises(WorkspaceError):await action(owner,'sessions',{'limit':51})
        assert len(catalog.session_queries)==1
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
    result=subprocess.run([sys.executable,'-I','-c',script,json.dumps(cfg),plan['planId']],capture_output=True)
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
        assert (await owner.request('quiescence/acquire',FENCE))['acquired'] is False
        fresh={**FENCE,'fenceId':'fresh-fence','commandId':'fresh-command'}
        assert (await owner.request('quiescence/acquire',fresh))['acquired'] is True
        assert len(idle)>=2
        with pytest.raises(WorkspaceError,match='intake is held') as error:await action(owner,'prepare',{'name':'Never admitted'},'denied')
        assert error.value.executed is False and owner.public_receipt('denied') is None
        release=unchanged();release.update(fresh);release['proof'].update(fresh)
        await owner.request('quiescence/release',release)
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

async def test_authorized_location_pages_stale_cursor_no_body_reads_and_no_outside_disclosure(tmp_path,monkeypatch):
    cfg=config(tmp_path);root=Path(cfg['allowedRoots'][0]);owner=Owner(cfg,CatalogFixture())
    try:
        for i in range(23):(root/f'dir-{i:02}').mkdir()
        (root/'keep.txt').write_text('private bytes');(root/'.hidden').mkdir();(root/'outside-link').symlink_to(tmp_path,target_is_directory=True)
        request=lambda args:owner.request('action',{'operation':'locations.list','args':args,'clientId':'one'})
        monkeypatch.setattr(Path,'read_text',lambda *_a,**_k:(_ for _ in ()).throw(AssertionError('File body read')))
        args={'path':str(root),'limit':7,'controlId':'picker'};page=await request(args);seen=page['entries'][:]
        assert page['parent'] is None and page['controlId']=='picker' and all(x['directory'] for x in page['entries'])
        cursor=page['nextCursor']
        with pytest.raises(WorkspaceError,match='cursor'):await owner.request('action',{'operation':'locations.list','args':{**args,'cursor':cursor},'clientId':'other'})
        while page.get('nextCursor'):
            page=await request({**args,'cursor':page['nextCursor']});seen+=page['entries']
        assert [r['name'] for r in seen]==[f'dir-{i:02}' for i in range(23)]
        all_rows=(await request({'path':str(root),'directoriesOnly':False}))['entries'];assert len(all_rows)==24 and all_rows[-1]['name']=='keep.txt'
        (root/'new').mkdir()
        with pytest.raises(WorkspaceError,match='Directory changed'):await request({**args,'cursor':cursor})
        with pytest.raises(WorkspaceError,match='outside'):await request({'path':str(tmp_path)})
    finally:await owner.close()

async def test_child_mkdir_exact_receipt_no_registration_or_replay_after_loss(tmp_path):
    cfg=config(tmp_path);catalog=CatalogFixture();owner=Owner(cfg,catalog);root=Path(cfg['allowedRoots'][0]);request={'operation':'locations.create','args':{'path':str(root),'name':'Chosen Folder','controlId':'folder'},'commandId':'mkdir','clientId':'one'}
    try:
        result=await owner.request('action',request);assert Path(result['path']).is_dir() and result['registered'] is False and not catalog.rows
        (Path(result['path'])/'keep').write_text('retained')
        assert (await owner.request('action',request))['receipt']['status']=='completed'
        with pytest.raises(WorkspaceError) as error:await owner.request('action',{**request,'commandId':'different'})
        assert error.value.executed is False and error.value.receipt['status']=='rejected'
        original=owner.allocate
        def interrupted(plan):original(plan);raise RuntimeError('lost after mkdir')
        owner.allocate=interrupted
        uncertain={**request,'commandId':'uncertain','args':{'path':str(root),'name':'Uncertain'}}
        with pytest.raises(WorkspaceError) as error:await owner.request('action',uncertain)
        assert error.value.executed is None
    finally:await owner.close()
    owner=Owner(cfg,catalog)
    try:
        with pytest.raises(WorkspaceError,match='unresolved'):await owner.request('action',uncertain)
        receipt=await action(owner,'receipt',{'commandId':'uncertain'});assert receipt['status']=='unknown' and receipt['inspection']['exists']
        assert (root/'Chosen Folder'/'keep').read_text()=='retained' and not catalog.rows
    finally:await owner.close()

async def test_shared_default_root_cas_survives_restart_reset_and_fences_mutation(tmp_path):
    cfg=config(tmp_path);catalog=CatalogFixture();owner=Owner(cfg,catalog);next_root=Path(cfg['allowedRoots'][0])/'new-root';initial=owner.defaults()
    try:
        plan=await action(owner,'prepare',{'name':'Before'},'before')
        changed=await action(owner,'defaults.set',{'defaultRoot':str(next_root),'expectedConfigRevision':initial['configRevision']},'default')
        assert changed['defaultRoot']==str(next_root) and not next_root.exists()
        assert (await action(owner,'defaults',{}))['configRevision']==changed['configRevision']
        with pytest.raises(WorkspaceError,match='revision changed'):await action(owner,'defaults.set',{'defaultRoot':'','expectedConfigRevision':initial['configRevision']},'stale')
        with pytest.raises(WorkspaceError,match='stale'):await action(owner,'create',{'planId':plan['planId']},'old-plan')
        assert (await action(owner,'defaults.set',{'defaultRoot':str(next_root),'expectedConfigRevision':initial['configRevision']},'default'))['replayed'] is False
    finally:await owner.close()
    owner=Owner(cfg,catalog)
    try:
        assert owner.defaults()=={k:changed[k] for k in initial}
        with pytest.raises(WorkspaceError,match='outside'):await action(owner,'defaults.set',{'defaultRoot':str(tmp_path),'expectedConfigRevision':owner.config_revision},'outside-default')
        reset=await action(owner,'defaults.set',{'defaultRoot':'','expectedConfigRevision':owner.config_revision},'reset');assert reset['defaultRoot']==cfg['defaultRoot'] and reset['configRevision']!=initial['configRevision']
        ctx={'fenceId':'held','commandId':'update','purpose':'distribution-update','instanceId':'instance','dataScope':'scope'}
        assert (await owner.request('quiescence/acquire',ctx))['acquired']
        assert (await action(owner,'defaults',{}))['defaultRoot']==cfg['defaultRoot']
        with pytest.raises(WorkspaceError,match='held'):await action(owner,'defaults.set',{'defaultRoot':'','expectedConfigRevision':owner.config_revision},'fenced')
        with pytest.raises(WorkspaceError,match='held'):await owner.request('action',{'operation':'locations.create','args':{'path':cfg['allowedRoots'][0],'name':'no'},'commandId':'fenced-mkdir'})
    finally:await owner.close()

async def test_directory_scan_budget_is_an_explicit_error_not_incomplete_page(tmp_path):
    cfg=config(tmp_path);root=Path(cfg['allowedRoots'][0]);owner=Owner(cfg,CatalogFixture())
    try:
        for i in range(10001):(root/str(i)).touch()
        with pytest.raises(WorkspaceError) as error:await owner.request('action',{'operation':'locations.list','args':{'path':str(root),'limit':1},'clientId':'viewer'})
        assert error.value.code=='directory_scan_budget' and error.value.executed is False
    finally:await owner.close()

async def test_library_version_requires_host_binding_and_carries_all_selectors(tmp_path):
    catalog=CatalogFixture();owner=Owner(config(tmp_path),catalog)
    try:
        with pytest.raises(WorkspaceError,match='unavailable'):await action(owner,'sessions',{'libraryQueryVersion':1})
        await owner.request('initialize',{'libraryQueryVersion':1})
        await action(owner,'sessions',{'libraryQueryVersion':1,'query':'literal*','sort':'name','activity':'attention','location':'managed','archive':'all','limit':40})
        q=catalog.session_queries[-1]
        assert q['sort']=='name' and q['activity']=='attention' and q['location']=='managed' and q['search']=='literal*' and q['archive']=='all' and q['limit']==40
        assert q['allowedWorkspaceRoots']==owner.roots
        with pytest.raises(WorkspaceError,match='advertised'):await action(owner,'sessions',{'sort':'name'})
        with pytest.raises(WorkspaceError,match='selector'):await action(owner,'sessions',{'libraryQueryVersion':1,'sort':'unsupported'})
        with pytest.raises(WorkspaceError):await action(owner,'sessions',{'libraryQueryVersion':1,'allowedWorkspaceRoots':['/']})
    finally:await owner.close()
