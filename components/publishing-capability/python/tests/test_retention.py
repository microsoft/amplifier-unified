import json
import pytest
from amplifier_unified_publishing.owner import Owner
SID='ahp-session:/selected'
FENCE=dict(fenceId='retention-one',commandId='hide-reviewed',purpose='retention-hide',instanceId='instance',dataScope='private')
async def forbidden(*args):raise AssertionError('Retention inspection must not invoke a native/runtime/network callback')
async def noop(*args):pass
@pytest.mark.asyncio
async def test_held_retention_exact_selected_metadata_and_no_effects(tmp_path):
    owner=Owner({'dataDir':str(tmp_path)},forbidden,noop)
    args={'context':FENCE,'sessions':[SID],'limit':101}
    try:
        with pytest.raises(ValueError,match='held retention'):await owner.request('quiescence.retention',args)
        owner.db.execute('INSERT INTO scopes VALUES(?,?)',('scope',SID));owner.db.execute('INSERT INTO commands VALUES(?,?,?,?)',('scope','lost','sig',json.dumps({'state':'unknown'})));owner.db.commit()
        assert (await owner.request('quiescence.acquire',FENCE))['acquired']
        family=[SID]+['ahp-session:/child-'+str(i) for i in range(100)]
        report=await owner.request('quiescence.retention',{**args,'sessions':family})
        assert report['coverage']=='complete' and report['omissions']==[]
        assert any(r['session']==SID and r['reasons'] for r in report['protected'])
        assert all(r['session'] in family for r in report['protected'])
        with pytest.raises(ValueError):await owner.request('quiescence.retention',{**args,'sessions':family+['ahp-session:/overflow']})
        with pytest.raises(ValueError):await owner.request('quiescence.retention',{**args,'context':{**FENCE,'commandId':'wrong'}})
        proof={'verified':True,**FENCE,'outcome':'unchanged','receiptId':'verified-hide-receipt'}
        await owner.request('quiescence.release',{**FENCE,'outcome':'unchanged','proof':proof})
        with pytest.raises(ValueError):await owner.request('quiescence.retention',args)
    finally:await owner.close()


@pytest.mark.asyncio
async def test_startup_scope_inverse_mapping_loss_refuses_instead_of_false_unprotected(tmp_path):
    import sqlite3
    owner=Owner({'dataDir':str(tmp_path)},forbidden,noop)
    try:
        sid=owner.scope(SID)
        owner.db.execute('INSERT INTO commands VALUES(?,?,?,?)',(sid,'uncertain','sig',json.dumps({'state':'unknown'})));owner.db.commit()
        assert (await owner.request('quiescence.acquire',FENCE))['acquired']
        report=await owner.request('quiescence.retention',{'context':FENCE,'sessions':[SID],'limit':101})
        assert report['coverage']=='complete' and any(row['session']==SID for row in report['protected'])
    finally:await owner.close()
    db=sqlite3.connect(tmp_path/'admission.sqlite3');db.execute('DROP TABLE scopes');db.commit();db.close()
    for _ in range(2):
        with pytest.raises(ValueError,match='authority schema is incomplete: scopes'):
            Owner({'dataDir':str(tmp_path)},forbidden,noop)
