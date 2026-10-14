import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createRetentionProtection} from '../src/retention-protection.js';
const session='ahp-session:/parent',child='ahp-session:/child',input={commandId:'effect',session,descendants:[child],operation:'hide'};
function owner(id,{references,loseRelease=false}={}){
 const calls=[],state={held:null,receipt:null};
 const release=async(context,outcome,proof)=>{
  calls.push({operation:'release',outcome,proof});
  if(outcome==='unknown')return;
  if(state.receipt){assert.deepEqual({context,outcome,proof},state.receipt);return;}
  assert.deepEqual(context,state.held);state.receipt={context,outcome,proof};state.held=null;
  if(loseRelease){loseRelease=false;throw Error('Lost owner acknowledgement');}
 };
 const participant={id,retentionHide:{version:1},async acquire(context){calls.push({operation:'acquire',context});state.held=structuredClone(context);return {ownerId:id,fenceId:context.fenceId,release:(outcome,proof)=>release(context,outcome,proof),async inspectRetentionReferences(args){assert.ok(state.held);calls.push({operation:'references',args});return references??{coverage:'complete',protected:[],omissions:[]};}};},reconcileRelease:async({outcome,proof,...context})=>release(context,outcome,proof)};
 return {participant,state,calls};
}
async function fixture(t,owners){const directory=await mkdtemp(join(tmpdir(),'retention-protection-'));let receipt=null;t.after(()=>rm(directory,{recursive:true,force:true}));return {create:()=>createRetentionProtection({directory,instanceId:'instance',dataScope:'scope',participants:owners.map(o=>o.participant),readItemReceipt:async()=>receipt}),setReceipt:r=>receipt=r};}
test('all selected-family reference reads occur under real holds; source effect proof precedes every release',async t=>{
 const a=owner('resources'),b=owner('operations'),f=await fixture(t,[a,b]),protection=f.create();
 try{
  const held=await protection.acquire(input);assert.deepEqual(held.ownerIds,['resources','operations']);assert.ok(a.state.held&&b.state.held);
  assert.deepEqual(b.calls.find(c=>c.operation==='references').args,{sessions:[session,child],limit:101});
  f.setReceipt({commandId:'effect',session,status:'hidden',executed:true,result:{hidden:true}});await held.release();
  assert.equal(protection.receipt('effect').state,'released');assert.equal(a.state.held,null);assert.match(b.state.receipt.proof.receiptId,/^retention:/);assert.equal(b.state.receipt.proof.verified,true);
 }finally{protection.close();}
});
test('future work or incomplete reference coverage refuses native admission and rolls back only live pre-effect leases',async t=>{
 for(const references of [{coverage:'complete',protected:[{session:child,reasons:['future-schedule']}],omissions:[]},{coverage:'partial',protected:[],omissions:['unavailable']}]){
  const a=owner('resources'),b=owner('operations',{references}),f=await fixture(t,[a,b]),protection=f.create();
  try{await assert.rejects(protection.acquire(input),e=>e.data.executed===false);assert.equal(protection.receipt('effect').state,'refused');assert.equal(a.state.held,null);assert.deepEqual(a.state.receipt.proof,{kind:'admission-refused'});}finally{protection.close();}
 }
});
test('unknown native outcome retains all owner gates across coordinator restart and uses only exact later proof',async t=>{
 const a=owner('resources'),f=await fixture(t,[a]);let protection=f.create();
 try{
  const held=await protection.acquire(input);f.setReceipt({commandId:'effect',session,status:'unknown'});await assert.rejects(held.release(),/not conclusively settled/);assert.ok(a.state.held);
  protection.close();protection=f.create();await assert.rejects(protection.acquire({...input,commandId:'repeat'}),/not settled/);assert.equal(a.calls.filter(c=>c.operation==='acquire').length,1);
  f.setReceipt({commandId:'effect',session:'ahp-session:/other',status:'hidden'});await assert.rejects(protection.reconcile('effect'),/not conclusively settled/);
  f.setReceipt({commandId:'effect',session,status:'refused',executed:false});await protection.reconcile('effect');assert.equal(a.state.held,null);assert.equal(protection.receipt('effect').state,'released');
 }finally{protection.close();}
});
test('a lost owner release acknowledgement reuses the identical persisted proof after restart',async t=>{
 const a=owner('resources',{loseRelease:true}),f=await fixture(t,[a]);let protection=f.create();
 try{
  const held=await protection.acquire(input);f.setReceipt({commandId:'effect',session,status:'hidden',executed:true});await assert.rejects(held.release(),/Unconfirmed owner release/);const original=structuredClone(a.state.receipt.proof);
  protection.close();protection=f.create();f.setReceipt({commandId:'effect',session,status:'hidden',executed:true,protectionRelease:'unknown'});await protection.reconcile('effect');assert.deepEqual(a.state.receipt.proof,original);assert.equal(a.calls.filter(c=>c.operation==='acquire').length,1);
 }finally{protection.close();}
});
test('missing advertised coverage refuses without touching any owner',async t=>{
 const a=owner('custom');delete a.participant.retentionHide;const f=await fixture(t,[a]),protection=f.create();
 try{await assert.rejects(protection.acquire(input),/coverage is incomplete/);assert.equal(a.calls.length,0);}finally{protection.close();}
});
