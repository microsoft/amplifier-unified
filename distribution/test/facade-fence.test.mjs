import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';import {FacadeFence} from '../src/facade-fence.js';
test('facade keeps forwarding owned, persists fence, excludes competitors and replays only release proof',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'facade-intake-'));let idle=0,finish;let owner=new FacadeFence({directory,id:'updates',onMayBeIdle:()=>idle++});const ctx={fenceId:'one',commandId:'update',purpose:'distribution-update',instanceId:'old',dataScope:'scope'};
 try{
  assert.throws(()=>new FacadeFence({directory,id:'other'}),/locked/);
  const running=owner.run(false,()=>new Promise(resolve=>finish=resolve));assert.equal(await owner.participant.acquire(ctx),null);assert.throws(()=>owner.close(),/active/);finish();await running;assert.equal(idle,1);
  const held=await owner.participant.acquire(ctx);await held.release('unknown');owner.close();owner=new FacadeFence({directory,id:'updates'});
  await assert.rejects(owner.run(false,()=>{throw Error('must not forward')}),/intake is closed/);assert.equal(await owner.run(true,()=>42),42);await assert.rejects(owner.participant.reconcileRelease({...ctx,outcome:'unchanged',proof:{kind:'admission-refused'}}),/Pre-effect/);
  const proof={verified:true,...ctx,outcome:'ready',instanceId:'new',receiptId:'supervisor-exact'};await owner.participant.reconcileRelease({...ctx,outcome:'ready',proof});owner.close();owner=new FacadeFence({directory,id:'updates'});await owner.participant.reconcileRelease({...ctx,outcome:'ready',proof});
  assert.equal(await owner.run(false,()=>1),1);await assert.rejects(owner.participant.acquire(ctx),/cannot be reused/);
 }finally{owner.close();await rm(directory,{recursive:true,force:true});}
});

test('service facade retains complete identity across restart and refuses generic or mismatched release',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'service-facade-'));
 const identity={installationId:'install',ownerId:'owner',instanceId:'old',dataScope:'scope',releaseDigest:'a'.repeat(64)};
 const ctx={fenceId:'service-one',commandId:'stop',purpose:'service-stop',instanceId:'old',dataScope:'scope',serviceIdentity:identity};
 let owner=new FacadeFence({directory,id:'updates',serviceStop:true});
 const proof={verified:true,kind:'service-lifecycle',fenceId:ctx.fenceId,commandId:ctx.commandId,dataScope:'scope',instanceId:'new',outcome:'ready',receiptId:'resume',serviceOutcome:'resumed',expected:identity,observed:{...identity,instanceId:'new'},resumeCommandId:'resume',exitReceiptId:'exit',readyReceiptId:'ready'};
 try{
  assert.deepEqual(owner.participant.serviceStop,{version:1});
  await assert.rejects(owner.participant.acquire({...ctx,serviceIdentity:undefined}),/service/);
  const lease=await owner.participant.acquire(ctx);assert.ok(lease);assert.deepEqual(owner.fence().serviceIdentity,identity);
  await lease.release('unknown');owner.close();owner=new FacadeFence({directory,id:'updates',serviceStop:true});
  assert.deepEqual(owner.fence().serviceIdentity,identity);
  await assert.rejects(owner.participant.reconcileRelease({...ctx,outcome:'unchanged',proof:{kind:'admission-refused'}}),/Pre-effect/);
  for(const invalid of [
   {...proof,kind:undefined},
   {...proof,expected:{...identity,installationId:'other'}},
   {...proof,observed:{...proof.observed,ownerId:'other'}},
   {...proof,observed:{...proof.observed,releaseDigest:'b'.repeat(64)}},
   {...proof,exitReceiptId:undefined},
   {...proof,serviceOutcome:'stop-refused'},
  ])await assert.rejects(owner.participant.reconcileRelease({...ctx,outcome:'ready',proof:invalid}));
  await assert.rejects(owner.participant.reconcileRelease({...ctx,serviceIdentity:{...identity,ownerId:'other'},outcome:'ready',proof}),/Exact held/);
  await owner.participant.reconcileRelease({...ctx,outcome:'ready',proof});
  await owner.participant.reconcileRelease({...ctx,outcome:'ready',proof});
  assert.equal(owner.fence(),null);assert.equal(await owner.run(false,()=>42),42);
 }finally{owner.close();await rm(directory,{recursive:true,force:true});}
});

test('service gate is opt-in and definitive busy refusal restores only its unchanged held instance',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'service-facade-refusal-'));
 const identity={installationId:'install',ownerId:'owner',instanceId:'old',dataScope:'scope',releaseDigest:'a'.repeat(64)};
 const ctx={fenceId:'service-refusal',commandId:'stop',purpose:'service-stop',instanceId:'old',dataScope:'scope',serviceIdentity:identity};
 let owner=new FacadeFence({directory,id:'updates'});
 try{
  assert.equal(owner.participant.serviceStop,undefined);await assert.rejects(owner.participant.acquire(ctx),/not configured/);
  owner.close();owner=new FacadeFence({directory,id:'updates',serviceStop:true});
  const lease=await owner.participant.acquire(ctx);
  const proof={verified:true,kind:'service-lifecycle',fenceId:ctx.fenceId,commandId:ctx.commandId,dataScope:'scope',instanceId:'old',outcome:'unchanged',receiptId:'stop',serviceOutcome:'stop-refused',expected:identity,observed:identity,refusalReceiptId:'stop'};
  await assert.rejects(lease.release('unchanged',{...proof,resumeCommandId:'wrong'}));
  await lease.release('unchanged',proof);assert.equal(owner.fence(),null);
  const another=await owner.participant.acquire({...ctx,fenceId:'pre-effect',commandId:'pre-effect'});
  await another.release('unchanged',{kind:'admission-refused'});assert.equal(owner.fence(),null);
 }finally{owner.close();await rm(directory,{recursive:true,force:true});}
});
