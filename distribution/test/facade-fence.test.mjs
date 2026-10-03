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
