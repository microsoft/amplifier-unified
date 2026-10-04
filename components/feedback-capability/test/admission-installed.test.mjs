import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {DatabaseSync} from 'node:sqlite';
const {createFeedbackCapability}=await import(process.env.FEEDBACK_PACKAGE_MODULE??'../src/index.js');
const python=process.env.FEEDBACK_PYTHON,resourcesModule=process.env.FEEDBACK_RESOURCES_MODULE;
const configured={skip:!python||!resourcesModule};
const context={fenceId:'installed-fence',commandId:'installed-command',purpose:'distribution-update',instanceId:'installed-instance',dataScope:'owned-test-data'};
const proof={...context,kind:'distribution-admission-abort',verified:true,receiptId:'authenticated-installed-proof'};
const ownerId='installed:feedback';
async function fixture(t){
 const root=await mkdtemp(join(tmpdir(),'feedback-abort-')),config=join(root,'launch.json');await writeFile(config,JSON.stringify({dataDir:join(root,'python')}));
 const {createResourcesCapability}=await import(resourcesModule);let cap,abortCalls=0,loseAbort=false;
 const open=()=>{
  const resources=createResourcesCapability({directory:join(root,'uploads'),inspectSession:async()=>{throw Error('No history or execution callback permitted');}});
  const uploads={...resources,quiescenceParticipant:id=>{
   const participant=resources.quiescenceParticipant(id);return {...participant,abortAdmission:async input=>{abortCalls++;const receipt=await participant.abortAdmission(input);if(loseAbort){loseAbort=false;throw Error('Lost actual upload abort acknowledgement');}return receipt;}};
  }};
  cap=createFeedbackCapability({owner:{command:python,args:['-I','-B','-m','amplifier_unified_feedback.server','--config',config],cwd:root},uploadOwner:uploads,inspectSession:async()=>{throw Error('No session lookup');},readExport:async()=>{throw Error('No history export');}});
  return cap.quiescenceParticipant(ownerId);
 };
 let participant=open();t.after(async()=>{await cap?.close();await rm(root,{recursive:true,force:true});});
 const inspect=()=>{const db=new DatabaseSync(join(root,'python','intake.sqlite3'),{readOnly:true});try{return {journal:JSON.parse(db.prepare('SELECT value FROM feedback_aggregate_admissions WHERE fence=?').get(context.fenceId)?.value??'null'),pythonFence:JSON.parse(db.prepare('SELECT value FROM fence WHERE id=1').get()?.value??'null')};}finally{db.close();}};
 return {get cap(){return cap;},get participant(){return participant;},get abortCalls(){return abortCalls;},inspect,loseUploadAbort:()=>{loseAbort=true;},reopen:async()=>{await cap.close();participant=open();return participant;}};
}
test('installed Python/resources: complete attempts and exact receipts survive aggregate restart',configured,async t=>{
 const f=await fixture(t);await f.participant.acquire(context);assert.deepEqual(f.inspect().journal.attempts.map(row=>[row.childOwnerId,row.status]),[[ownerId+':uploads','acquired'],[ownerId+':python','acquired']]);
 await f.reopen();const receipt=await f.participant.abortAdmission({...context,proof});assert.equal(receipt.ownerId,ownerId);assert.equal(receipt.status,'released');const saved=f.inspect();assert.equal(saved.pythonFence,null);assert.deepEqual(saved.journal.receipt,receipt);assert.equal(saved.journal.attempts.filter(row=>row.abortReceipt).length,2);
 await f.reopen();assert.deepEqual(await f.participant.abortAdmission({...context,proof}),receipt);assert.equal(f.abortCalls,1);await assert.rejects(f.participant.acquire(context),/cannot dispatch/);
});
test('installed Python/resources: lost upload abort reply resumes from retained Python receipt',configured,async t=>{
 const f=await fixture(t);await f.participant.acquire(context);f.loseUploadAbort();await assert.rejects(f.participant.abortAdmission({...context,proof}),/Lost actual/);
 const partial=f.inspect();assert.equal(partial.pythonFence,null);assert.ok(partial.journal.attempts[1].abortReceipt);assert.equal(partial.journal.attempts[0].abortReceipt,undefined);assert.equal(partial.journal.receipt,undefined);
 await f.reopen();const receipt=await f.participant.abortAdmission({...context,proof});assert.equal(receipt.status,'released');assert.deepEqual(f.inspect().journal.attempts[1].abortReceipt,partial.journal.attempts[1].abortReceipt);assert.equal(f.abortCalls,2);
});
test('installed Python/resources: missing original aggregate and changed proof refuse',configured,async t=>{
 const f=await fixture(t);await assert.rejects(f.participant.abortAdmission({...context,proof}),/No original/);assert.equal(f.abortCalls,0);
 await f.participant.acquire(context);const receipt=await f.participant.abortAdmission({...context,proof}),saved=f.inspect();await f.reopen();await assert.rejects(f.participant.abortAdmission({...context,proof:{...proof,receiptId:'different-proof'}}),/differs/);assert.deepEqual(f.inspect(),saved);assert.deepEqual(await f.participant.abortAdmission({...context,proof}),receipt);assert.equal(f.abortCalls,1);
});
