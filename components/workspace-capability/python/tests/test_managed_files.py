import json
import pytest
from amplifier_unified_workspaces.owner import Owner
SID='ahp-session:/selected'
FENCE=dict(fenceId='retention-one',commandId='hide-reviewed',purpose='managed-files-disposal',instanceId='instance',dataScope='private')
async def forbidden(*args):raise AssertionError('Retention inspection must not invoke a native/runtime/network callback')
async def noop(*args):pass
@pytest.mark.asyncio
async def test_held_managed_files_exact_selected_metadata_and_no_effects(tmp_path):
    owner=Owner({'stateDirectory':str(tmp_path/'state'),'allowedRoots':[str(tmp_path)],'defaultRoot':str(tmp_path/'new')},forbidden)
    args={'context':FENCE,'sessions':[SID],'limit':101,'allocation':dict(allocationId='12345678-1234-1234-1234-123456789abc',executionDirectory='/owned/managed/files',allocationHash='a'*64,treeHash='b'*64,entryCount=1,bytes=5)}
    try:
        with pytest.raises(ValueError,match='held retention'):await owner.request('quiescence.managedFiles',args)
        with owner.db() as db:db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?)',('lost','workspace.create','{}','unknown',None,0))
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

@pytest.mark.asyncio
async def test_retained_cross_conversation_file_reference_protects_disposal(tmp_path):
    owner=Owner({'stateDirectory':str(tmp_path/'state'),'allowedRoots':[str(tmp_path)],'defaultRoot':str(tmp_path/'new')},forbidden)
    args={'context':FENCE,'sessions':[SID],'limit':101,'allocation':dict(allocationId='12345678-1234-1234-1234-123456789abc',executionDirectory='/owned/managed/files',allocationHash='a'*64,treeHash='b'*64,entryCount=1,bytes=5)}
    try:
        with owner.db() as db:
            db.execute('INSERT INTO registrations VALUES(?,?,?,?,?)',('retained','/owned/managed/files/output','hidden retained registration',1,1))
        query='SELECT 1 FROM registrations WHERE path=? OR (path>=? AND path<?) LIMIT 1'
        with owner.db() as db:assert 'INDEX' in str([tuple(row) for row in db.execute('EXPLAIN QUERY PLAN '+query,('/owned/managed/files','/owned/managed/files/','/owned/managed/files0')).fetchall()])
        assert (await owner.request('quiescence/acquire',FENCE))['acquired']
        hidden=await owner.request('quiescence.managedFiles',args)
        assert hidden['coverage']=='complete' and hidden['protected'][0]['session']==SID
        assert hidden['protected'][0]['reasons']
        proof={'verified':True,**FENCE,'outcome':'unchanged','receiptId':'native-refused-files'}
        await owner.request('quiescence/release',{**FENCE,'outcome':'unchanged','proof':proof})
    finally:await owner.close()
