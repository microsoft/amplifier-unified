import json
import pytest
from amplifier_unified_workspaces.owner import Owner
SID='ahp-session:/selected'
FENCE=dict(fenceId='retention-one',commandId='hide-reviewed',purpose='retention-hide',instanceId='instance',dataScope='private')
async def forbidden(*args):raise AssertionError('Retention inspection must not invoke a native/runtime/network callback')
async def noop(*args):pass
@pytest.mark.asyncio
async def test_held_retention_exact_selected_metadata_and_no_effects(tmp_path):
    owner=Owner({'stateDirectory':str(tmp_path/'state'),'allowedRoots':[str(tmp_path)],'defaultRoot':str(tmp_path/'new')},forbidden)
    args={'context':FENCE,'sessions':[SID],'limit':101}
    try:
        with pytest.raises(ValueError,match='held retention'):await owner.request('quiescence.retention',args)
        with owner.db() as db:db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?)',('lost','workspace.create','{}','unknown',None,0))
        assert (await owner.request('quiescence/acquire',FENCE))['acquired']
        family=[SID]+['ahp-session:/child-'+str(i) for i in range(100)]
        report=await owner.request('quiescence.retention',{**args,'sessions':family})
        assert report['coverage']=='complete' and report['omissions']==[]
        assert any(r['session']==SID and r['reasons'] for r in report['protected'])
        assert all(r['session'] in family for r in report['protected'])
        with pytest.raises(ValueError):await owner.request('quiescence.retention',{**args,'sessions':family+['ahp-session:/overflow']})
        with pytest.raises(ValueError):await owner.request('quiescence.retention',{**args,'context':{**FENCE,'commandId':'wrong'}})
        proof={'verified':True,**FENCE,'outcome':'unchanged','receiptId':'verified-hide-receipt'}
        await owner.request('quiescence/release',{**FENCE,'outcome':'unchanged','proof':proof})
        with pytest.raises(ValueError):await owner.request('quiescence.retention',args)
    finally:await owner.close()
