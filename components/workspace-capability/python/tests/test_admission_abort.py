import pytest
pytestmark = pytest.mark.asyncio
from pathlib import Path
from amplifier_unified_workspaces.owner import Owner
C=dict(fenceId='original-abort-fence',commandId='original-abort-command',purpose='distribution-update',instanceId='original-launch',dataScope='owned-scope')
OWNER_ID='workspaces'
ACQUIRE='quiescence/acquire'
ABORT='quiescence/abortAdmission'
READ='quiescence/admissionAbortReceipt'
def proof():return {**C,'kind':'distribution-admission-abort','verified':True,'receiptId':'authenticated-original-abort'}
async def noop(*args):raise AssertionError('No external authority calls permitted')
async def notify(*args):pass
def make(tmp_path):
 projects=tmp_path/'projects';projects.mkdir(exist_ok=True)
 return Owner({'stateDirectory':str(tmp_path/'owner'),'allowedRoots':[str(projects)],'defaultRoot':str(projects)},noop)
async def test_actual_owner_committed_acquisition_and_abort_survive_restart(tmp_path):
 owner=make(tmp_path)
 try:
  assert (await owner.request('initialize',{}))['quiescence']['admissionAbort']['version']==1
  assert (await owner.request(ACQUIRE,C))['acquired']
 finally:await owner.close()
 owner=make(tmp_path)
 try:
  params={**C,'proof':proof(),'ownerId':OWNER_ID}
  result=await owner.request(ABORT,params)
  assert result['status']=='released' and result['ownerId']==OWNER_ID and owner.intake.fence is None
  assert await owner.request(READ,{**C,'ownerId':OWNER_ID})==result
  with pytest.raises(ValueError):await owner.request(ABORT,{**params,'proof':{**proof(),'receiptId':'changed'}})
 finally:await owner.close()
 owner=make(tmp_path)
 try:assert await owner.request(ABORT,params)==result
 finally:await owner.close()
async def test_actual_owner_original_busy_refusal_is_not_acquired(tmp_path):
 owner=make(tmp_path)
 try:
  owner.intake.calls=1
  result=await owner.request(ACQUIRE,C);assert result['acquired'] is False
  owner.intake.calls=0
  assert (await owner.request(ACQUIRE,C))['acquired'] is False
  receipt=await owner.request(ABORT,{**C,'proof':proof(),'ownerId':OWNER_ID})
  assert receipt['status']=='not-acquired' and owner.intake.fence is None
 finally:owner.intake.calls=0;await owner.close()
async def test_actual_owner_legacy_and_active_work_refuse_abort(tmp_path):
 owner=make(tmp_path)
 try:
  await owner.request(ACQUIRE,C)
  with pytest.raises(ValueError):await owner.request(ABORT,{**C,'proof':{**proof(),'kind':'service-lifecycle'},'ownerId':OWNER_ID})
  owner.intake.calls=1
  with pytest.raises(ValueError,match='in flight'):await owner.request(ABORT,{**C,'proof':proof(),'ownerId':OWNER_ID})
  owner.intake.calls=0
  owner.intake.db.execute('DELETE FROM releases WHERE fence=?',(C['fenceId'],));owner.intake.db.commit()
  with pytest.raises(ValueError,match='original'):await owner.request(ABORT,{**C,'proof':proof(),'ownerId':OWNER_ID})
  assert owner.intake.fence==C
 finally:owner.intake.calls=0;await owner.close()

async def test_actual_owner_old_abort_retry_preserves_newer_held_fence(tmp_path):
 owner=make(tmp_path)
 try:
  await owner.request(ACQUIRE,C)
  params={**C,'proof':proof(),'ownerId':OWNER_ID}
  original=await owner.request(ABORT,params)
  newer={**C,'fenceId':'newer-fence','commandId':'newer-command'}
  assert (await owner.request(ACQUIRE,newer))['acquired'] is True
  assert await owner.request(ABORT,params)==original
  assert owner.intake.fence==newer
 finally:await owner.close()

async def test_node_local_busy_refusal_is_durable_before_returning_null(tmp_path):
 owner=make(tmp_path)
 try:
  result=await owner.request('quiescence/refuseAdmission',C)
  assert result['acquired'] is False and result['executed'] is False
  assert owner.intake.fence is None
  assert await owner.request(ACQUIRE,C)==result
  receipt=await owner.request(ABORT,{**C,'proof':proof(),'ownerId':OWNER_ID})
  assert receipt['status']=='not-acquired'
 finally:await owner.close()
