import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,readFile,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
const {createManualIngressGate}=await import(process.env.DISTRIBUTION_OWNER_MODULE??'../dist/index.js');
const ctx={commandId:'update',fenceId:'preparation',purpose:'distribution-update',instanceId:'original',dataScope:'owned'};
const proof={...ctx,kind:'distribution-admission-abort',verified:true,receiptId:'supervisor-abort-intent'};
async function fixture(t){
 const directory=await realpath(await mkdtemp(join(tmpdir(),'ingress-abort-'))),options={directory:join(directory,'ingress'),id:'ingress'};
 let gate=await createManualIngressGate(options);
 t.after(async()=>{gate.close();await rm(directory,{recursive:true,force:true});});
 return {get gate(){return gate;},options,async reopen(){gate.close();gate=await createManualIngressGate(options);}};
}
test('ingress abort commits its receipt and hold removal together; reopen cannot reacquire or generically release it',async t=>{
 const f=await fixture(t),lease=await f.gate.participant.acquire(ctx);
 await lease.release('unknown');assert.equal(f.gate.enter(),null);
 await f.reopen();
 const result=await f.gate.participant.abortAdmission({...ctx,proof});
 assert.equal(result.status,'released');assert.equal(result.ownerId,'ingress');assert.equal(f.gate.inspect().held,null);
 const done=f.gate.enter();assert.equal(typeof done,'function');done();
 await f.reopen();assert.deepEqual(await f.gate.participant.abortAdmission({...ctx,proof}),result);
 await assert.rejects(f.gate.participant.acquire(ctx),/already_settled/);
 await assert.rejects(f.gate.participant.reconcileRelease({...ctx,outcome:'unchanged',proof:{...ctx,verified:true,receiptId:'ordinary'}}),/already_settled/);
 await assert.rejects(f.gate.participant.abortAdmission({...ctx,proof:{...proof,receiptId:'changed'}}),/proof_changed/);
});
test('ingress absence never proves no acquisition; a durable busy refusal does, after forwarding finishes',async t=>{
 const f=await fixture(t);
 await assert.rejects(f.gate.participant.abortAdmission({...ctx,proof}),/attempt_unconfirmed/);
 const done=f.gate.enter();assert.equal(await f.gate.participant.acquire(ctx),null);
 await assert.rejects(f.gate.participant.abortAdmission({...ctx,proof}),/work_active/);
 done();await f.reopen();
 assert.equal((await f.gate.participant.abortAdmission({...ctx,proof})).status,'not-acquired');
});
test('recorded pre-effect rollback can be acknowledged without deleting a different held fence',async t=>{
 const f=await fixture(t),lease=await f.gate.participant.acquire(ctx);
 await lease.release('unchanged',{kind:'admission-refused'});
 const other={...ctx,fenceId:'other',commandId:'other'};
 const next=await f.gate.participant.acquire(other);
 await assert.rejects(f.gate.participant.abortAdmission({...ctx,proof}),/hold_unconfirmed/);
 assert.equal(f.gate.inspect().held.fenceId,'other');
 await next.release('unchanged',{kind:'admission-refused'});
 assert.equal((await f.gate.participant.abortAdmission({...ctx,proof})).status,'released');
});
for(const [name,change] of [
 ['command',p=>p.commandId='other'],['fence',p=>p.fenceId='other'],['instance',p=>p.instanceId='other'],
 ['scope',p=>p.dataScope='other'],['purpose',p=>p.purpose='recovery'],['kind',p=>p.kind='admission-refused'],
 ['verification',p=>p.verified=false],['extra authority',p=>p.allow=true],['missing receipt',p=>delete p.receiptId],
])test(`ingress rejects ${name} proof without releasing its hold`,async t=>{
 const f=await fixture(t);await f.gate.participant.acquire(ctx);
 const changed=structuredClone(proof);change(changed);
 await assert.rejects(f.gate.participant.abortAdmission({...ctx,proof:changed}));
 assert.equal(f.gate.inspect().held.fenceId,ctx.fenceId);
});
test('new ingress schema cannot repair a missing journal; known legacy schema migrates without inventing evidence',async t=>{
 const f=await fixture(t);await f.gate.participant.acquire(ctx);f.gate.close();
 const path=join(f.options.directory,'intake.sqlite3');let db=new DatabaseSync(path);
 db.exec('DROP TABLE admission_attempts');db.close();const before=await readFile(path);
 await assert.rejects(createManualIngressGate(f.options),/ledger_unconfirmed/);assert.deepEqual(await readFile(path),before);
 db=new DatabaseSync(path);db.exec('PRAGMA user_version=1');db.close();
 await f.reopen(); // Deliberately reconstruct the exact supported legacy profile.
 assert.equal(f.gate.inspect().held.fenceId,ctx.fenceId);
 await assert.rejects(f.gate.participant.abortAdmission({...ctx,proof}),/attempt_unconfirmed/);
});
