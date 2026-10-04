const contextKeys=['commandId','dataScope','fenceId','instanceId','purpose'];
const proofKeys=[...contextKeys,'kind','receiptId','verified'];
const receiptKeys=['commandId','dataScope','fenceId','instanceId','ownerId','receiptId','status'];
const exact=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
const token=value=>{if(typeof value!=='string'||!value||value.length>200||/[\x00-\x1f]/.test(value))throw Error('Bounded Feedback admission identity required');return value;};
const keys=(value,required,optional=[])=>!!value&&typeof value==='object'&&!Array.isArray(value)&&required.every(key=>Object.hasOwn(value,key))&&Object.keys(value).every(key=>required.includes(key)||optional.includes(key));
const same=(left,right)=>exact(left)===exact(right);
function bound(input){
 const context=Object.fromEntries(contextKeys.map(key=>[key,token(input?.[key])]));
 if(context.purpose!=='distribution-update'||input.serviceIdentity!==undefined)throw Error('Exact distribution admission context required');
 return context;
}
function proofFor(context,input){
 if(!keys(input,proofKeys)||input.kind!=='distribution-admission-abort'||input.verified!==true||contextKeys.some(key=>input[key]!==context[key]))throw Error('Exact distinct authenticated Feedback admission abort proof required');
 token(input.receiptId);return structuredClone(input);
}
function receiptFor(context,ownerId,input,expected){
 if(!keys(input,receiptKeys)||input.ownerId!==ownerId||!['released','not-acquired'].includes(input.status)||contextKeys.filter(key=>key!=='purpose').some(key=>input[key]!==context[key])||(expected&&input.status!==expected))throw Error('Exact original Feedback child abort receipt required');
 token(input.receiptId);return structuredClone(input);
}
function acquisitionFor(context,value){
 if(value?.acquired===true&&same(value,{acquired:true,fenceId:context.fenceId,intakeClosed:true}))return 'acquired';
 if(value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length<=16&&Buffer.byteLength(JSON.stringify(value))<=4096&&value.acquired===false&&value.executed===false)return 'refused';
 throw Error('Feedback child acquisition remains unconfirmed');
}

/** Node orchestrates; only the existing Python intake database owns this journal.
 * An attempted child is committed before dispatch. Abort visits the complete
 * retained sequence in reverse and records every original child receipt. */
export class AggregateAdmission {
 constructor({ownerId,owner,uploads,releaseOwner,enter,leave,active}){
  this.ownerId=token(ownerId);this.owner=owner;this.uploads=uploads;this.releaseOwner=releaseOwner;
  this.enter=enter;this.leave=leave;this.active=active;
  this.owners=[token(ownerId+':uploads'),token(ownerId+':python')];
  if(uploads.id!==this.owners[0])throw Error('Feedback upload participant identity differs from original declaration');
 }
 async exclusive(callback){this.enter();try{return await callback();}finally{this.leave();}}
 assertIdle(){if(this.active()||this.owner.admissionPending)throw Error('Feedback work or owner replies are still pending');}
 async supported(){
  const initialized=await this.owner.request('initialize',{});
  if(initialized.quiescence?.aggregateAdmission?.version!==1||initialized.quiescence?.admissionAbort?.version!==1||typeof this.uploads.abortAdmission!=='function')throw Error('Installed Feedback aggregate admission contract unavailable');
 }
 validate(context,value){
  if(!keys(value,['version','context','ownerId','owners','attempts'],['abortProof','receipt'])||value.version!==1||!same(value.context,context)||value.ownerId!==this.ownerId||!same(value.owners,this.owners)||!Array.isArray(value.attempts)||value.attempts.length>2||Buffer.byteLength(JSON.stringify(value))>16384)throw Error('Feedback aggregate journal differs from original binding');
  for(let index=0;index<value.attempts.length;index++){
   const attempt=value.attempts[index];
   if(!keys(attempt,['childOwnerId','status'],['acquisition','abortReceipt'])||attempt.childOwnerId!==this.owners[index]||!['pending','acquired','refused'].includes(attempt.status)||(index&&value.attempts[index-1].status!=='acquired'))throw Error('Feedback attempted child sequence is incomplete or changed');
   if(attempt.status==='pending'){if(Object.hasOwn(attempt,'acquisition'))throw Error('Pending Feedback child has contradictory acquisition');}
   else if(acquisitionFor(context,attempt.acquisition)!==attempt.status)throw Error('Feedback acquisition differs from retained disposition');
   if(Object.hasOwn(attempt,'abortReceipt')){
    if(!value.abortProof||value.attempts.slice(index+1).some(row=>!row.abortReceipt))throw Error('Feedback child receipts are not in reverse attempted order');
    receiptFor(context,attempt.childOwnerId,attempt.abortReceipt,{acquired:'released',refused:'not-acquired'}[attempt.status]);
   }
  }
  if(Object.hasOwn(value,'abortProof'))proofFor(context,value.abortProof);
  if(Object.hasOwn(value,'receipt')){
   if(!value.abortProof||value.attempts.some(row=>!row.abortReceipt))throw Error('Feedback aggregate completion lacks every attempted child receipt');
   receiptFor(context,this.ownerId,value.receipt,this.status(value));
  }
  return structuredClone(value);
 }
 status(journal){return journal.attempts.some(row=>row.abortReceipt?.status==='released')?'released':'not-acquired';}
 async request(operation,context,fields={}){return this.owner.request('quiescence.aggregateAdmission',{operation,context,ownerId:this.ownerId,...fields});}
 async read(context){const value=await this.request('read',context);return value===null?null:this.validate(context,value);}
 async change(operation,context,fields,expected){
  const value=this.validate(context,await this.request(operation,context,fields));
  if(!same(value,expected))throw Error('Feedback aggregate journal did not retain the exact transition');
  return value;
 }
 async acquire(input){return this.exclusive(async()=>{
  const context=bound(input);await this.supported();
  if(await this.read(context))throw Error('Original Feedback admission cannot dispatch child acquisition again');
  let journal=await this.change('begin',context,{owners:this.owners},{version:1,context,ownerId:this.ownerId,owners:this.owners,attempts:[]});
  // The committed empty attempted sequence proves a local refusal before any
  // child dispatch. Work already admitted through the public adapter may settle.
  if(this.active()||this.owner.admissionPending)return null;
  const attempt=async childOwnerId=>{
   const expected=structuredClone(journal);expected.attempts.push({childOwnerId,status:'pending'});
   journal=await this.change('attempt',context,{childOwnerId},expected);
  };
  const result=async(childOwnerId,acquisition)=>{
   const status=acquisitionFor(context,acquisition),expected=structuredClone(journal);
   Object.assign(expected.attempts.find(row=>row.childOwnerId===childOwnerId),{status,acquisition});
   journal=await this.change('result',context,{childOwnerId,acquisition},expected);
  };
  await attempt(this.owners[0]);
  const uploadLease=await this.uploads.acquire(context);
  if(uploadLease===null){await result(this.owners[0],{acquired:false,executed:false});return null;}
  if(uploadLease?.ownerId!==this.owners[0]||uploadLease?.fenceId!==context.fenceId||typeof uploadLease?.release!=='function')throw Error('Feedback upload acquisition is unconfirmed');
  await result(this.owners[0],{acquired:true,fenceId:context.fenceId,intakeClosed:true});
  await attempt(this.owners[1]);
  const acquired=await this.owner.request('quiescence.acquire',context);
  await result(this.owners[1],acquired);
  if(acquired.acquired===false){await uploadLease.release('unchanged',{kind:'admission-refused'});return null;}
  return {ownerId:this.ownerId,fenceId:context.fenceId,uploadLease,
   release:(outcome,proof)=>this.release(context,outcome,proof,uploadLease)};
 });}
 async release(input,outcome,proof,uploadLease){return this.exclusive(async()=>{
  const context=bound(input);
  const journal=await this.read(context);if(journal?.abortProof)throw Error('Feedback admission abort requires its distinct retained proof');
  await this.releaseOwner(context,outcome,proof,!!uploadLease);
  if(uploadLease)await uploadLease.release(outcome,proof);
  else await this.uploads.reconcileRelease({...context,outcome,proof});
 });}
 async abort(input){return this.exclusive(async()=>{
  const context=bound(input),proof=proofFor(context,input.proof);this.assertIdle();await this.supported();
  let journal=await this.read(context);if(!journal)throw Error('No original Feedback aggregate admission journal');
  if(journal.abortProof&&!same(journal.abortProof,proof))throw Error('Feedback abort proof differs from retained original');
  if(journal.receipt)return structuredClone(journal.receipt);
  if(!journal.abortProof)journal=await this.change('abortIntent',context,{proof},{...journal,abortProof:proof});
  for(let index=journal.attempts.length-1;index>=0;index--){
   const attempt=journal.attempts[index];if(attempt.abortReceipt)continue;
   this.assertIdle();
   const raw=attempt.childOwnerId===this.owners[0]?await this.uploads.abortAdmission({...context,proof}):await this.owner.request('quiescence.abortAdmission',{...context,proof,ownerId:this.owners[1]});
   const receipt=receiptFor(context,attempt.childOwnerId,raw,{acquired:'released',refused:'not-acquired'}[attempt.status]);
   const expected=structuredClone(journal);expected.attempts[index].abortReceipt=receipt;
   journal=await this.change('abortReceipt',context,{childOwnerId:attempt.childOwnerId,receipt},expected);
  }
  this.assertIdle();
  return receiptFor(context,this.ownerId,await this.request('complete',context),this.status(journal));
 });}
}
