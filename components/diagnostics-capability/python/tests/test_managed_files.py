import json
import pytest
from amplifier_unified_diagnostics.owner import Owner
SID='ahp-session:/selected'
FENCE=dict(fenceId='retention-one',commandId='hide-reviewed',purpose='managed-files-disposal',instanceId='instance',dataScope='private')
async def forbidden(*args):raise AssertionError('Retention inspection must not invoke a native/runtime/network callback')
async def noop(*args):pass
@pytest.mark.asyncio
async def test_held_managed_files_exact_selected_metadata_and_no_effects(tmp_path):
    owner=Owner({'stateDirectory':str(tmp_path)})
    args={'context':FENCE,'sessions':[SID],'limit':101,'allocation':dict(allocationId='12345678-1234-1234-1234-123456789abc',executionDirectory='/owned/managed/files',allocationHash='a'*64,treeHash='b'*64,entryCount=1,bytes=5)}
    try:
        with pytest.raises(ValueError,match='held retention'):await owner.request('quiescence.managedFiles',args)
        owner.db.execute('INSERT INTO records(id,at,stream,session,workspace,event,data) VALUES(?,?,?,?,?,?,?)',('event',0,'app',SID,'/fixture','test','{}'));owner.db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',('lost','event','destination','r1','{}','unknown',0,None,0));owner.db.commit()
        assert (await owner.request('quiescence/acquire',FENCE))['acquired']
        family=[SID]+['ahp-session:/child-'+str(i) for i in range(100)]
        report=await owner.request('quiescence.managedFiles',{**args,'sessions':family})
        assert report['coverage']=='complete' and report['omissions']==[]
        assert any(r['session']==SID and r['reasons'] for r in report['protected'])
        assert all(r['session'] in family for r in report['protected'])
        with pytest.raises(ValueError):await owner.request('quiescence.managedFiles',{**args,'sessions':family+['ahp-session:/overflow']})
        with pytest.raises(ValueError):await owner.request('quiescence.managedFiles',{**args,'context':{**FENCE,'commandId':'wrong'}})
        proof={'verified':True,**FENCE,'outcome':'unchanged','receiptId':'verified-hide-receipt'}
        await owner.request('quiescence/release',{**FENCE,'outcome':'unchanged','proof':proof})
        with pytest.raises(ValueError):await owner.request('quiescence.managedFiles',args)
    finally:await owner.close()
