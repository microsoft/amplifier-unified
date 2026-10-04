import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
const {FacadeFence}=await import(process.env.ABORT_DISTRIBUTION_MODULE?new URL('./facade-fence.js',process.env.ABORT_DISTRIBUTION_MODULE).href:'../src/facade-fence.js');
const ctx={commandId:'update',fenceId:'preparation',purpose:'distribution-update',instanceId:'original',dataScope:'owned'};
const proof={...ctx,kind:'distribution-admission-abort',verified:true,receiptId:'supervisor-abort-intent'};
async function fixture(t){
 const directory=await mkdtemp(join(tmpdir(),'facade-abort-'));let gate=new FacadeFence({directory,id:'application-updates'});
 t.after(async()=>{gate.close();await rm(directory,{recursive:true,force:true});});
 return {directory,get gate(){return gate;},reopen(){gate.close();gate=new FacadeFence({directory,id:'application-updates'});}};
}
test('passive reconciliation may settle its own hold but still joins shutdown',async t=>{
 const f=await fixture(t);await f.gate.participant.acquire(ctx);let finish;
 const reconciliation=f.gate.run(true,async()=>{
  const receipt=await f.gate.participant.abortAdmission({...ctx,proof});
  await new Promise(r=>finish=r);return receipt;
 });
 await new Promise(r=>setImmediate(r));assert.equal(f.gate.fence(),null);
 assert.throws(()=>f.gate.close(),/active/);let drained=false;
 const closing=f.gate.drain().then(()=>drained=true);await new Promise(r=>setImmediate(r));assert.equal(drained,false);
 finish();assert.equal((await reconciliation).status,'released');await closing;
});
test('facade persists exact no-effect refusal and never infers one from an absent hold',async t=>{
 const f=await fixture(t);await assert.rejects(f.gate.participant.abortAdmission({...ctx,proof}),/attempt_unconfirmed/);
 const leave=f.gate.enter(false);assert.equal(await f.gate.participant.acquire(ctx),null);
 await assert.rejects(f.gate.participant.abortAdmission({...ctx,proof}),/work_active/);leave();f.reopen();
 const result=await f.gate.participant.abortAdmission({...ctx,proof});assert.equal(result.status,'not-acquired');
 f.reopen();assert.deepEqual(await f.gate.participant.abortAdmission({...ctx,proof}),result);
 await assert.rejects(f.gate.participant.acquire(ctx),/already_settled/);
});
for(const reopen of [false,true])test(`facade same-fence refusal remains final after work finishes (reopen=${reopen})`,async t=>{
 const f=await fixture(t),leave=f.gate.enter(false);
 assert.equal(await f.gate.participant.acquire(ctx),null);leave();
 if(reopen)f.reopen();
 assert.equal(await f.gate.participant.acquire(ctx),null);assert.equal(f.gate.fence(),null);
 const fresh={...ctx,fenceId:'fresh-attempt'};
 const lease=await f.gate.participant.acquire(fresh);assert.ok(lease);
 await lease.release('unchanged',{kind:'admission-refused'});
 assert.equal((await f.gate.participant.abortAdmission({...ctx,proof})).status,'not-acquired');
});
test('repeated busy observation preserves acquisition; lost abort reply reuses only its durable receipt',async t=>{
 const f=await fixture(t),lease=await f.gate.participant.acquire(ctx);
 assert.equal(await f.gate.participant.acquire(ctx),null);await lease.release('unknown');f.reopen();
 const result=await f.gate.participant.abortAdmission({...ctx,proof});assert.equal(result.status,'released');
 f.reopen();assert.deepEqual(await f.gate.participant.abortAdmission({...ctx,proof}),result);
 await assert.rejects(f.gate.participant.abortAdmission({...ctx,proof:{...proof,receiptId:'changed'}}),/proof_changed/);
 await assert.rejects(f.gate.participant.reconcileRelease({...ctx,outcome:'unchanged',proof:{...ctx,verified:true,receiptId:'ordinary'}}),/already_settled/);
});
test('legacy facade migration retains held authority without manufacturing an acquisition record',async t=>{
 const f=await fixture(t);await f.gate.participant.acquire(ctx);f.gate.close();
 const path=join(f.directory,'intake.sqlite3');let db=new DatabaseSync(path);db.exec('DROP TABLE admission_attempts');db.close();
 const before=await readFile(path);assert.throws(()=>new FacadeFence({directory:f.directory,id:'application-updates'}),/schema is incomplete/);assert.deepEqual(await readFile(path),before);
 db=new DatabaseSync(path);db.exec('PRAGMA user_version=0');db.close();f.reopen();
 await assert.rejects(f.gate.participant.abortAdmission({...ctx,proof}),/attempt_unconfirmed/);assert.equal(f.gate.fence().fenceId,ctx.fenceId);
});
