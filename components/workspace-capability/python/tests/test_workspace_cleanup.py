import shutil
from pathlib import Path
import pytest
from amplifier_unified_workspaces.owner import Owner
from test_owner import CatalogFixture, config, FENCE


async def setup(tmp_path):
    owner=Owner(config(tmp_path),CatalogFixture())
    target=Path(owner.roots[0])/'work';target.mkdir();(target/'user-file').write_text('retained until native removes it')
    row=owner.record(target,'Work',False)
    fence={**FENCE,'purpose':'recovery'}
    assert (await owner.request('quiescence/acquire',fence))['acquired']
    return owner,target,row,fence


def proof(row):
    return {'commandId':'native-removal','operation':'maintenance.workspace.remove','state':'succeeded',
            'result':{'workspaceId':row['id'],'previewHash':'a'*64,'removed':True,
                      'historyPreserved':True,'canonicalFilesChanged':0,'replayed':False}}


async def test_held_registration_census_and_receipted_archive_preserve_registration_and_pins(tmp_path):
    owner,target,row,fence=await setup(tmp_path)
    try:
        view=await owner.request('quiescence.workspaceCleanup.inspect',{'workspaceId':row['id'],'context':fence})
        assert view['registeredPaths']==[str(target)] and view['registrationRevision']==row['revision']
        args={'context':fence,'commandId':'archive-once','registration':view,'nativeReceipt':proof(row)}
        with pytest.raises(ValueError,match='exists again'):
            await owner.request('quiescence.workspaceCleanup.archiveRegistration',args)
        assert not owner.local(row['id'])['archived'] and (target/'user-file').exists()
        shutil.rmtree(target)  # Only the native owner would do this in the real composition.
        result=await owner.request('quiescence.workspaceCleanup.archiveRegistration',args)
        assert result['receipt']['state']=='succeeded' and owner.local(row['id'])['archived']
        assert result['receipt']['result']['filesDeletedByWorkspaceOwner'] is False
        target.mkdir();(target/'new-work').write_text('new')
        same=await owner.request('quiescence.workspaceCleanup.archiveRegistration',args)
        assert same==result and (target/'new-work').exists()
        assert await owner.request('quiescence.workspaceCleanup.receipt',{'commandId':'archive-once'})==result
        assert owner.local(row['id'])['path']==str(target) and not owner.local(row['id'])['hidden']
    finally:await owner.close()


async def test_inspection_requires_held_recovery_and_refuses_external_resources_or_uncertain_requests(tmp_path):
    owner,target,row,fence=await setup(tmp_path)
    try:
        inspect={'workspaceId':row['id'],'context':fence}
        with pytest.raises(ValueError,match='held recovery'):
            await owner.request('quiescence.workspaceCleanup.inspect',{**inspect,'context':{**fence,'fenceId':'wrong'}})
        from amplifier_unified_workspaces.workspace_resources import command
        command(owner.directory,row['id'],'workspace.resources.add',{'kind':'server','resourceId':'fixture','owner':'user'},'record')
        with pytest.raises(ValueError,match='external resources'):
            await owner.request('quiescence.workspaceCleanup.inspect',inspect)
        assert (target/'user-file').exists()
    finally:await owner.close()


async def test_unknown_native_receipt_never_archives(tmp_path):
    owner,target,row,fence=await setup(tmp_path)
    try:
        view=await owner.request('quiescence.workspaceCleanup.inspect',{'workspaceId':row['id'],'context':fence})
        with pytest.raises(ValueError,match='Conclusive'):
            await owner.request('quiescence.workspaceCleanup.archiveRegistration',{'context':fence,'commandId':'refused','registration':view,'nativeReceipt':{**proof(row),'state':'unknown'}})
        assert not owner.local(row['id'])['archived'] and owner.public_receipt('refused') is None
    finally:await owner.close()
