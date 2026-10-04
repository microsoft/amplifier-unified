import pytest
pytestmark = pytest.mark.asyncio
from pathlib import Path
from amplifier_unified_notifications.owner import Owner
C=dict(fenceId='original-abort-fence',commandId='original-abort-command',purpose='distribution-update',instanceId='original-launch',dataScope='owned-scope')
OWNER_ID='notifications'
ACQUIRE='quiescence/acquire'
ABORT='quiescence/abortAdmission'
READ='quiescence/admissionAbortReceipt'
def proof():return {**C,'kind':'distribution-admission-abort','verified':True,'receiptId':'authenticated-original-abort'}
async def noop(*args):raise AssertionError('No external authority calls permitted')
async def notify(*args):pass
def make(tmp_path):
 return Owner({'stateDirectory':str(tmp_path/'owner')},transport=noop)
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

async def test_admitted_request_remains_active_until_idle_callback_returns(tmp_path):
 import asyncio
 owner=make(tmp_path);body_entered=asyncio.Event();body_done=asyncio.Event();callback_entered=asyncio.Event();callback_done=asyncio.Event();work=None
 async def held_callback(*args):
  callback_entered.set();await callback_done.wait()
 async def admitted_body(*args):
  body_entered.set();await body_done.wait();return {'finished':True}
 try:
  old={**C,'fenceId':'completed-original','commandId':'completed-command'}
  await owner.request(ACQUIRE,old)
  old_receipt=await owner.request(ABORT,{**old,'proof':{**proof(),**old},'ownerId':OWNER_ID})
  owner.idle=held_callback
  work=asyncio.create_task(owner.request('action',{'operation':'notifications.get','args':{}}))
  await asyncio.wait_for(callback_entered.wait(),2)
  assert (await owner.request(ACQUIRE,C))['acquired'] is False
  await asyncio.wait_for(callback_entered.wait(),2)
  assert work.done() is False and owner.intake.background>0
  with pytest.raises(ValueError,match='in flight'):
   await owner.request(ABORT,{**C,'proof':proof(),'ownerId':OWNER_ID})
  assert await owner.request(READ,{**old,'ownerId':OWNER_ID})==old_receipt
  callback_done.set();await work
  assert owner.intake.background==0
  settled=await owner.request(ABORT,{**C,'proof':proof(),'ownerId':OWNER_ID})
  assert settled['status']=='not-acquired'
  newer={**C,'fenceId':'newer-after-callback','commandId':'newer-command'}
  assert (await owner.request(ACQUIRE,newer))['acquired'] is True
  assert await owner.request(READ,{**old,'ownerId':OWNER_ID})==old_receipt
  assert await owner.request(ABORT,{**C,'proof':proof(),'ownerId':OWNER_ID})==settled
  assert owner.intake.fence==newer
 finally:
  body_done.set();callback_done.set()
  if work is not None:await asyncio.gather(work,return_exceptions=True)
  await owner.close()

@pytest.mark.parametrize('callback_name',['changed','idle'])
async def test_delivery_lifetime_includes_actual_post_transport_callbacks(tmp_path,callback_name):
 import asyncio
 owner=make(tmp_path);transport_entered=asyncio.Event();transport_done=asyncio.Event();callback_entered=asyncio.Event();callback_done=asyncio.Event()
 async def transport(*args):
  transport_entered.set();await transport_done.wait();return 202
 async def callback():
  callback_entered.set();await callback_done.wait()
 try:
  old={**C,'fenceId':'completed-delivery-original','commandId':'completed-delivery-command'}
  await owner.request(ACQUIRE,old)
  old_receipt=await owner.request(ABORT,{**old,'proof':{**proof(),**old},'ownerId':OWNER_ID})
  owner.transport=transport
  await owner.request('action',{'operation':'notifications.save','args':{'expectedRevision':0,'patch':{'enabled':True,'topic':'OWNED_FIXTURE','token':'OWNED_FIXTURE'}},'commandId':'enable-fixture'})
  result=await owner.request('completion',{'session':'ahp-session:/fixture','eventId':'one','kind':'response','title':'fixture','text':'fixture'})
  assert result['accepted'];await asyncio.wait_for(transport_entered.wait(),2)
  setattr(owner,callback_name,callback)
  assert (await owner.request(ACQUIRE,C))['acquired'] is False
  transport_done.set();await asyncio.wait_for(callback_entered.wait(),2)
  assert len(owner.tasks)==1 and owner.intake.calls==0 and owner.intake.background>0
  assert owner.exact(result['commandId'])['status']=='server-accepted'
  with pytest.raises(ValueError,match='in flight'):
   await owner.request(ABORT,{**C,'proof':proof(),'ownerId':OWNER_ID})
  assert await owner.request(READ,{**old,'ownerId':OWNER_ID})==old_receipt
  callback_done.set();await asyncio.gather(*list(owner.tasks));await asyncio.sleep(0)
  settled=await owner.request(ABORT,{**C,'proof':proof(),'ownerId':OWNER_ID})
  assert settled['status']=='not-acquired'
  newer={**C,'fenceId':'newer-delivery-hold','commandId':'newer-delivery-command'}
  assert (await owner.request(ACQUIRE,newer))['acquired'] is True
  assert await owner.request(READ,{**old,'ownerId':OWNER_ID})==old_receipt
  assert await owner.request(ABORT,{**C,'proof':proof(),'ownerId':OWNER_ID})==settled
  assert owner.intake.fence==newer
 finally:
  transport_done.set();callback_done.set();await owner.close()
