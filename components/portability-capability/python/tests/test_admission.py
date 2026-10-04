import pytest
from amplifier_unified_portability.owner import Owner
from amplifier_unified_portability.admission import AdmissionJournal
CTX={'commandId':'original','fenceId':'original','instanceId':'fixture','dataScope':'fixture','purpose':'distribution-update'}
def abort(ctx=CTX):return {**ctx,'proof':{**ctx,'kind':'distribution-admission-abort','verified':True,'receiptId':'authenticated-abort'}}
async def denied(*args):raise AssertionError('No transfer effects allowed')
async def notify(*args):pass
def owner(tmp_path):
    (tmp_path/'work').mkdir()
    return Owner({'dataDir':str(tmp_path/'owner'),'workspaceRoots':[str(tmp_path/'work')],'exchangeDir':str(tmp_path/'exchange'),'stageDir':str(tmp_path/'work/stages')},denied,notify)
@pytest.mark.asyncio
async def test_plan_precedes_hold_and_all_not_entered_settle_without_native_effect(tmp_path):
    o=owner(tmp_path);j=AdmissionJournal(o)
    try:
        j.dispatch('begin',{**CTX,'owners':['portability-owner','native-a','native-b']})
        assert o.intake.fence is None
        body=j.dispatch('abortBegin',abort())
        assert [r['ownerId'] for r in body['owners']]==['portability-owner','native-a','native-b']
        assert all(r['abortReceipt']['status']=='not-acquired' for r in body['owners'][1:])
        result=j.dispatch('abortComplete',abort());assert result['status']=='not-acquired'
        assert j.dispatch('abortComplete',abort())==result
    finally:await o.close()
@pytest.mark.asyncio
async def test_python_clear_last_requires_all_native_receipts_and_no_active_work(tmp_path):
    o=owner(tmp_path);j=AdmissionJournal(o)
    try:
        j.dispatch('begin',{**CTX,'owners':['portability-owner','native-a']})
        j.dispatch('record',{**CTX,'ownerId':'portability-owner','stage':'entering','evidence':{}})
        acquired=j.acquire_owner(CTX)
        j.dispatch('record',{**CTX,'ownerId':'portability-owner','stage':'acquired','evidence':acquired})
        j.dispatch('record',{**CTX,'ownerId':'native-a','stage':'entering','evidence':{}})
        j.dispatch('abortBegin',abort())
        with pytest.raises(ValueError,match='Complete original subowner'):j.dispatch('abortComplete',abort())
        assert o.intake.fence==CTX
        result={k:CTX[k] for k in ['commandId','fenceId','instanceId','dataScope']};result.update(ownerId='native-a',status='released',receiptId='actual-native')
        j.dispatch('abortRecord',{**abort(),'receipt':result})
        o.intake.calls+=1
        with pytest.raises(ValueError,match='active'):j.dispatch('abortComplete',abort())
        assert o.intake.fence==CTX
        o.intake.calls-=1
        assert j.dispatch('abortComplete',abort())['status']=='released'
        assert o.intake.fence is None
    finally:await o.close()
@pytest.mark.asyncio
async def test_incomplete_v2_and_legacy_authority_never_supply_abort_plan(tmp_path):
    o=owner(tmp_path);j=AdmissionJournal(o)
    try:
        j.acquire_owner(CTX)
        with pytest.raises(ValueError,match='complete aggregate'):j.dispatch('releasePlan',CTX)
        with pytest.raises(ValueError,match='Complete original'):j.dispatch('abortBegin',abort())
        assert o.intake.fence==CTX
    finally:await o.close()
