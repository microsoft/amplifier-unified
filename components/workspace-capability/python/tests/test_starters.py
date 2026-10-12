import asyncio
import json
from pathlib import Path
import pytest
from amplifier_unified_workspaces.owner import Owner,WorkspaceError
from amplifier_unified_workspaces import workspace_provisioning as setup
from test_owner import CatalogFixture,config,action

class Catalog(CatalogFixture):
    async def __call__(self,method,args):
        if method=='starterBundles':return {'registeredBundles':[{'name':'work','value':'work','label':'Work','source':'git+https://github.com/microsoft/amplifier-bundle-work@main#subdirectory=bundle.md'},{'name':'anchors-amp-dev','value':'anchors-amp-dev','source':'git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=bundles/anchors-amp-dev/bundle.md'}]}
        return await super().__call__(method,args)

async def owner(tmp_path):
    value=Owner(config(tmp_path),Catalog());await value.request('initialize',{'workspaceStartersVersion':1});return value

async def settle(value):
    while value.setup_tasks:await asyncio.gather(*list(value.setup_tasks.values()))

async def test_reviewed_starter_snapshot_scaffold_and_repeat_survive_restart(tmp_path):
    value=await owner(tmp_path)
    try:
        listing=await action(value,'starters.list',{});assert [r['id'] for r in listing['items']]==['blank','development','amplifier-development']
        custom=(await action(value,'starters.save',{'starter':{'name':'My setup','instructions':'# Keep projects separate','scratch':True,'bundle':'work','rootGit':True}},'save'))['starter']
        plan=await action(value,'prepare',{'name':'Project','starterId':custom['id']},'prepare')
        assert plan['starter']['name']=='My setup' and not Path(plan['path']).exists()
        await action(value,'starters.save',{'id':custom['id'],'expectedRevision':1,'starter':{'name':'Changed','instructions':'Do not use this'}},'edit')
        created=await action(value,'create',{'planId':plan['planId']},'create');await settle(value)
        path=Path(plan['path']);assert (path/'AGENTS.md').read_text()=='# Keep projects separate\n';assert (path/'SCRATCH.md').exists();assert (path/'.git').is_dir()
        assert 'work' in (path/'.amplifier/settings.yaml').read_text()
        receipt=await action(value,'setup.inspect',{'workspaceId':created['workspace']['id']});assert receipt['status']=='ready'
        assert value.intake.background==0
        before={str(f.relative_to(path)):f.read_bytes() for f in path.rglob('*') if f.is_file()}
        repeat=await action(value,'create',{'planId':plan['planId']},'create');assert repeat['replayed'] is False and not value.setup_tasks
        cfg=config(tmp_path);await value.close();value=Owner(cfg,Catalog());await value.request('initialize',{'workspaceStartersVersion':1})
        assert (await action(value,'setup.inspect',{'workspaceId':created['workspace']['id']}))['status']=='ready'
        assert before=={str(f.relative_to(path)):f.read_bytes() for f in path.rglob('*') if f.is_file()}
    finally:await value.close()

async def test_custom_conflicts_invalid_bundle_and_resource_inventory(tmp_path):
    value=await owner(tmp_path)
    try:
        with pytest.raises(WorkspaceError,match='configured standalone'):await action(value,'starters.save',{'starter':{'name':'Bad','bundle':'imaginary'}},'bad')
        assert (await action(value,'receipt',{'commandId':'bad'}))['status']=='rejected'
        row=(await action(value,'starters.duplicate',{'id':'development'},'dup'))['starter']
        assert (await action(value,'starters.duplicate',{'id':'development'},'dup'))['starter']==row
        with pytest.raises(WorkspaceError,match='changed'):await action(value,'starters.remove',{'id':row['id'],'expectedRevision':0},'stale')
        with pytest.raises(WorkspaceError,match='different arguments'):await action(value,'starters.remove',{'id':row['id'],'expectedRevision':1},'dup')
        plan=await action(value,'prepare',{'name':'Resources'},'p');created=await action(value,'create',{'planId':plan['planId']},'c');wid=created['workspace']['id']
        inventory=await action(value,'resources.list',{'workspaceId':wid});assert inventory['resources']==[] and not (value.directory/'workspace-resources').exists()
        resource=(await action(value,'resources.add',{'workspaceId':wid,'kind':'preview','resourceId':'test','owner':'chat','note':'local fixture'},'resource'))['resource']
        await action(value,'resources.status',{'workspaceId':wid,'id':resource['id'],'expectedRevision':1,'status':'observed_absent','evidence':'Fixture observer confirmed no server'},'status')
        assert (await action(value,'resources.list',{'workspaceId':wid}))['resources'][0]['status']=='observed_absent'
    finally:await value.close()

async def test_setup_holds_update_admission_and_does_not_replay_interruption(tmp_path,monkeypatch):
    import threading
    value=await owner(tmp_path);started=threading.Event();release=threading.Event();original=setup.run
    def slow(*args):started.set();release.wait(5);return original(*args)
    monkeypatch.setattr(setup,'run',slow)
    try:
        plan=await action(value,'prepare',{'name':'Hold','starterId':'development'},'p');created=await action(value,'create',{'planId':plan['planId']},'c')
        await asyncio.to_thread(started.wait,2);assert value.intake.background==1
        held=await value.request('quiescence/acquire',{'fenceId':'f','commandId':'update','purpose':'distribution-update','instanceId':'i','dataScope':'d'})
        assert held['acquired'] is False
        release.set();await settle(value)
        receipt=await action(value,'setup.inspect',{'workspaceId':created['workspace']['id']})
        target=setup.receipt_path(value.directory,receipt['id']);saved=json.loads(target.read_text());saved['status']='running';saved['repositories']=[{'url':'https://example.com/repo.git','directory':'repo','ref':'','status':'running'}];target.write_text(json.dumps(saved))
        before=target.read_bytes();observed=await action(value,'setup.inspect',{'workspaceId':created['workspace']['id']});assert observed['status']=='interrupted' and target.read_bytes()==before
        with pytest.raises(WorkspaceError,match='interrupted'):await action(value,'setup.retry',{'workspaceId':created['workspace']['id'],'expectedRevision':observed['revision']},'retry')
        assert not value.setup_tasks and not (Path(plan['path'])/'repo').exists()
    finally:release.set();await value.close()
