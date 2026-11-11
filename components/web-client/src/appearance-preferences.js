const MISSING='missing';

function plainObject(value){
 return value!==null&&typeof value==='object'&&!Array.isArray(value)&&
  (Object.getPrototypeOf(value)===Object.prototype||Object.getPrototypeOf(value)===null);
}

function clone(value){return value===undefined?undefined:JSON.parse(JSON.stringify(value))}

function stable(value){
 if(Array.isArray(value))return '['+value.map(stable).join(',')+']';
 if(plainObject(value))return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+stable(value[key])).join(',')+'}';
 return JSON.stringify(value);
}

function equal(left,right){return stable(left)===stable(right)}

export function effectivePreferenceValue(field,preferences={},currentId='builtin:default'){
 switch(field){
  case 'appearanceId':return preferences.appearanceId??currentId;
  case 'scheme':return preferences.scheme??'system';
  case 'layout':return preferences.layout??'balanced';
  case 'decorations':return preferences.decorations!==false;
  case 'density':return preferences.density??'comfortable';
  case 'accent':return Object.hasOwn(preferences,'accent')?preferences.accent:undefined;
  case 'interfaceDetail':return preferences.interfaceDetail??'standard';
  case 'executionDetail':return preferences.executionDetail??'standard';
  case 'executionVisibility':return Object.fromEntries(['step','turn','conversation'].map(scope=>[scope,preferences.executionVisibility?.[scope]!==false]));
  case 'executionMetrics':return Array.isArray(preferences.executionMetrics)?clone(preferences.executionMetrics):[];
  case 'conversationStatus':return preferences.conversationStatus===true;
  case 'workStartView':return preferences.workStartView??'appearance';
  case 'collapseInterimReplies':return preferences.collapseInterimReplies===true;
  case 'planPlacement':return preferences.planPlacement??'composer';
  default:return preferences[field];
 }
}

export function appearancePreferenceMatches(field,left,right){
 if(field==='executionVisibility')return equal(
  effectivePreferenceValue(field,{[field]:left}),effectivePreferenceValue(field,{[field]:right}));
 return equal(left,right);
}

// Keep the optimistic value until the admitted write's revision reaches the UI.
// Value equality alone cannot distinguish an old frame from a superseding edit.
export function reconcileAppearancePreferenceValues(values,appearance){
 let next=values;
 for(const [key,entry] of Object.entries(values)){
  if(entry.status!=='stored')continue;
  const confirmation=entry.confirmation||{};
  const replaced=confirmation.namespace&&appearance.namespace&&confirmation.namespace!==appearance.namespace||
   confirmation.generation&&appearance.generation&&confirmation.generation!==appearance.generation;
  const scope=entry.scope==='shared'?'shared':'client';
  const field=entry.operation;
  const profile=scope==='shared'?appearance.shared:appearance.local;
  const revision=profile?.revision;
  const profileValues=profile?.values||{};
  const confirmed=Number.isSafeInteger(confirmation.revision)
   ?Number.isSafeInteger(revision)&&revision>=confirmation.revision
   :appearancePreferenceMatches(field,effectivePreferenceValue(field,profileValues),entry.value);
  if(replaced||confirmed){if(next===values)next={...values};delete next[key]}
 }
 return next;
}

export function sparseExecutionVisibilityPatch(next,rendered){
 if(!plainObject(next))return next;
 const current=plainObject(rendered)?rendered:{};
 return Object.fromEntries(Object.entries(next).filter(([scope,value])=>!Object.is(current[scope],value)));
}

function message(error,fallback){
 return typeof error?.message==='string'&&error.message?error.message:fallback;
}

export function readAppearanceStorageRecord(storage,namespace){
 if(!storage||typeof storage.getItem!=='function')throw Error('Browser preference storage is unavailable.');
 const value=storage.getItem(namespace);
 if(value===null)return {version:MISSING,activeScope:'shared',values:null};
 let record;
 try{record=JSON.parse(value)}catch{throw Error('The saved browser appearance preferences are invalid.')}
 if(!plainObject(record)||Object.keys(record).length!==3||!Object.hasOwn(record,'version')||
  !Object.hasOwn(record,'activeScope')||!Object.hasOwn(record,'values')||
  typeof record.version!=='string'||!record.version||record.version===MISSING||record.version.length>256||
  !['shared','client'].includes(record.activeScope)||
  record.values!==null&&!plainObject(record.values)||
  record.activeScope==='client'&&record.values===null)
  throw Error('The saved browser appearance preferences are invalid.');
 return {version:record.version,activeScope:record.activeScope,values:clone(record.values)};
}

export function applyAppearancePreferenceCommand(record,command,createVersion){
 if(!record||!command||record.version!==command.expectedStorageVersion)
  throw Error('Another browser tab changed these preferences. The newer value was kept.');
 const commandKeys=plainObject(command)?Object.keys(command).sort().join(','):'';
 if(commandKeys!=='after,expectedRevision,expectedStorageVersion,generation,id,namespace,status'||
  command.status!=='pending'||typeof command.namespace!=='string'||typeof command.generation!=='string'||
  typeof command.id!=='string'||!Number.isSafeInteger(command.expectedRevision)||
  typeof command.expectedStorageVersion!=='string')
  throw Error('The browser preference command is invalid.');
 const after=command.after;
 if(!plainObject(after)||Object.keys(after).length!==2||
  !Object.hasOwn(after,'activeScope')||!Object.hasOwn(after,'values')||
  !['shared','client'].includes(after.activeScope)||
  after.values!==null&&!plainObject(after.values)||
  after.activeScope==='client'&&after.values===null)
  throw Error('The browser preference command is invalid.');
 // Apply the exact admitted record: shell edits can update AND select the
 // retained profile atomically, and Undo can restore the pre-initialized record.
 const version=createVersion();
 if(typeof version!=='string'||!version||version===MISSING||version===record.version||version.length>256)
  throw Error('Could not create a new browser preference version.');
 return {version,activeScope:after.activeScope,values:clone(after.values)};
}

function browserVersion(){
 const source=globalThis.crypto;
 if(source?.randomUUID)return source.randomUUID();
 if(!source?.getRandomValues)throw Error('Secure browser storage versioning is unavailable.');
 return Array.from(source.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,'0')).join('');
}

export function createAppearancePreferenceClient({
 dispatch,
 storage=()=>globalThis.localStorage,
 locks=()=>globalThis.navigator?.locks,
 eventTarget=()=>globalThis.window,
 createVersion=browserVersion,
 ackTimeoutMs=10000,
 onStatus=()=>{},
}={}){
 let invoke=dispatch,namespace=null,generation=null,connected=false,acknowledged=null;
 let latestSnapshot=null,documentRevision=0,tail=Promise.resolve(),storageListener=null;
 let reconnectPending=true,readinessFailure=null;
 let attempted=new Set(),settlements=new Map(),waiters=new Map(),outcomes=new Map(),readyWaiters=new Set();
 let currentStatus={status:'idle'};

 const status=value=>{
  currentStatus=value;
  try{onStatus(value)}catch{}
 };
 const stamp=()=>({namespace,generation});
 const sameGeneration=value=>value?.namespace===namespace&&value?.generation===generation;
 const current=value=>connected&&sameGeneration(value);
 const ownsSettlement=work=>current(work)&&settlements.get(work.commandId)===work;
 const enqueue=operation=>{
  const task=tail.catch(()=>{}).then(operation);
  tail=task.catch(()=>{});
  return task;
 };
 const storageValue=()=>typeof storage==='function'?storage():storage;
 const lockManager=()=>typeof locks==='function'?locks():locks;
 const target=()=>typeof eventTarget==='function'?eventTarget():eventTarget;
 const lockName=()=>`amplifier.appearance:${namespace}`;

 async function withNamespaceLock(operation){
  const manager=lockManager();
  if(!manager||typeof manager.request!=='function')
   throw Error('This browser does not support safe local appearance updates.');
  return manager.request(lockName(),{mode:'exclusive'},operation);
 }

 async function readRecord(){
  return withNamespaceLock(()=>readAppearanceStorageRecord(storageValue(),namespace));
 }

 async function readPreferences(){
  if(typeof invoke!=='function')throw Error('Appearance actions are unavailable.');
  const receipt=await invoke('appearance.preferences.read',{});
  if(receipt?.accepted!==true||!receipt.result)throw Error(receipt?.error||'Could not refresh appearance preferences.');
  return receipt.result;
 }

 async function report(args,work){
  if(typeof invoke!=='function')throw Error('Appearance actions are unavailable.');
  let timer,timedOut=false;
  // Validate the eventual admission before racing the UI deadline. A timeout
  // stops waiting, not observation of a later definitive refusal of our write.
  // Namespace fences storage/document ownership locally; it is not a report wire field.
  const request=Promise.resolve().then(()=>invoke('appearance.client.report',args)).then(receipt=>{
   if(receipt?.accepted!==true){
    const error=Error(receipt?.error||'The browser preference receipt was not accepted.');
    error.reportRejected=receipt?.accepted===false;
    throw error;
   }
   return receipt.result||{};
  }).catch(error=>{
   // The HTTP action adapter throws explicit negative receipts. Do not infer
   // rejection from a status code, transport exception or unreadable response.
   if(error?.accepted===false)error.reportRejected=true;
   throw error;
  });
  const lateRejection=error=>{
   if(!timedOut||!error?.reportRejected||!work||!sameGeneration(work)||settlements.get(work.commandId)!==work)return;
   work.rejected=true;
   work.error=message(error,'The browser preference activation was rejected.');
   // Reuse the exact-record rollback and failed/ready recovery, including when
   // preference reads are unavailable. Old generations/settled work cannot act.
   void enqueue(()=>reconcileSettlement(work));
  };
  void request.then(result=>{
   if(result.status==='failed')lateRejection({reportRejected:true,message:result.error});
  },lateRejection);
  return Number.isFinite(ackTimeoutMs)&&ackTimeoutMs>0
   ?Promise.race([request,new Promise((_,reject)=>{timer=setTimeout(()=>{
    timedOut=true;reject(Error('The browser preference acknowledgement timed out.'));
   },ackTimeoutMs)})]).finally(()=>clearTimeout(timer))
   :request;
 }

 function resolveReady(){
  readinessFailure=null;
  for(const waiter of readyWaiters)waiter.resolve({status:'ready'});
  readyWaiters.clear();
 }

 function rejectReady(error){
  for(const waiter of readyWaiters)waiter.reject(error);
  readyWaiters.clear();
 }

 function recordReadinessFailure(error){
  readinessFailure=error;
  rejectReady(error);
 }

 function settleCommand(id,result){
  outcomes.set(id,result);
  while(outcomes.size>100)outcomes.delete(outcomes.keys().next().value);
  const waiter=waiters.get(id);
  if(!waiter)return;
  waiters.delete(id);
  if(result.ok)waiter.resolve(result.value);
  else waiter.reject(Error(result.error||'The browser preference change failed.'));
 }

 function waitForCommand(id){
  if(typeof id!=='string'||!id)return Promise.reject(Error('The browser preference command is unavailable.'));
  const known=outcomes.get(id);
  if(known)return known.ok?Promise.resolve(known.value):Promise.reject(Error(known.error));
  if(waiters.has(id))return waiters.get(id).promise;
  let resolve,reject;
  const promise=new Promise((yes,no)=>{resolve=yes;reject=no});
  waiters.set(id,{promise,resolve,reject});
  return promise;
 }

 function waitUntilReady(){
  if(!connected)return Promise.reject(Error('This browser is not connected to its appearance preferences.'));
  if(readinessFailure)return Promise.reject(readinessFailure);
  if(acknowledged?.namespace===namespace&&acknowledged?.generation===generation)
   return Promise.resolve({status:'ready'});
  let resolve,reject;
  const promise=new Promise((yes,no)=>{resolve=yes;reject=no});
  readyWaiters.add({resolve,reject});
  return promise;
 }

 async function failedStorageReport(snapshot,error,owner=stamp()){
  if(!sameGeneration(owner))return;
  const failure=Error(message(error,'Browser preference storage is unavailable.'));
  recordReadinessFailure(failure);
  if(!current(owner))return;
  const local=snapshot?.local||{};
  try{
   const result=await report({
    generation:owner.generation,revision:Number.isSafeInteger(local.revision)?local.revision:0,
    storageVersion:typeof local.storageVersion==='string'?local.storageVersion:MISSING,
    activeScope:snapshot?.activeScope==='client'?'client':'shared',
    values:local.values===null?null:plainObject(local.values)?clone(local.values):null,
    status:'failed',error:failure.message,
   });
   status({status:'failed',error:result.error||failure.message});
  }catch(reportError){
   status({status:'failed',error:message(reportError,failure.message)});
  }
 }

 async function reconcileActual(){
  const owner=stamp();
  if(!current(owner))return false;
  let server,record;
  try{
   server=await readPreferences();
   if(!current(owner)||server.namespace!==owner.namespace||server.generation!==owner.generation)return false;
   latestSnapshot=server;
   record=await readRecord();
   if(!current(owner))return false;
  }catch(error){
   const pending=server?.command?.status==='pending'?server.command:latestSnapshot?.command;
   if(pending?.status==='pending')await failCommand(pending,error,owner);
   else await failedStorageReport(server||latestSnapshot,error,owner);
   return false;
  }
  const local=server.local||{},serverRevision=Number.isSafeInteger(local.revision)?local.revision:0;
  const serverVersion=typeof local.storageVersion==='string'?local.storageVersion:MISSING;
  const serverValues=local.values===null?null:plainObject(local.values)?local.values:null;
  const serverActiveScope=server.activeScope==='client'?'client':'shared';
  const aligned=record.version===serverVersion&&record.activeScope===serverActiveScope&&equal(record.values,serverValues);
  const needsReport=!local.reported||!!local.error||!aligned||reconnectPending;
  if(needsReport){
   const revision=aligned?Math.max(serverRevision,documentRevision):Math.max(serverRevision,documentRevision)+1;
   try{
    await report({generation:owner.generation,revision,storageVersion:record.version,
     activeScope:record.activeScope,values:record.values,status:'ready'});
   }catch(error){
    status({status:'failed',error:message(error,'Could not confirm browser appearance preferences.')});
    recordReadinessFailure(error);
    return false;
   }
   if(!current(owner))return false;
   documentRevision=Math.max(documentRevision,revision);
   acknowledged={namespace:owner.namespace,generation:owner.generation,revision,storageVersion:record.version,
    activeScope:record.activeScope,values:clone(record.values)};
   reconnectPending=false;
   status({status:'ready',storageVersion:record.version,revision});
   resolveReady();
  }else{
   documentRevision=Math.max(documentRevision,serverRevision);
   acknowledged={namespace:owner.namespace,generation:owner.generation,revision:serverRevision,storageVersion:record.version,
    activeScope:record.activeScope,values:clone(record.values)};
   reconnectPending=false;
   status({status:'ready',storageVersion:record.version,revision:serverRevision});
   resolveReady();
  }
  latestSnapshot=server;
  return true;
 }

 function finishFailedSettlement(work,error,server){
  const detail=message(error,work.error||'The browser preference change failed.');
  settlements.delete(work.commandId);
  if(server?.command?.id===work.commandId)
   latestSnapshot={...server,command:{...server.command,status:'failed',error:detail}};
  if(work.status==='stored'){
   acknowledged=null;
   reconnectPending=true;
  }
  status({status:'failed',commandId:work.commandId,error:detail});
  settleCommand(work.commandId,{ok:false,error:detail});
  if(!acknowledged)recordReadinessFailure(Error(detail));
 }

 function finishStoredSettlement(work,result,server){
  settlements.delete(work.commandId);
  const local=server?.local||{};
  const matches=work.status==='stored'&&local.storageVersion===work.storageVersion&&
   server?.activeScope===work.activeScope&&equal(local.values,work.values);
  if(server?.command?.id===work.commandId)
   latestSnapshot={...server,command:{...server.command,status:'stored',undoId:result?.undoId||server.command.undoId}};
  if(matches){
   const revision=Number.isSafeInteger(local.revision)?local.revision:work.revision;
   acknowledged={namespace:work.namespace,generation:work.generation,revision,storageVersion:work.storageVersion,
    activeScope:work.activeScope,values:clone(work.values)};
   documentRevision=Math.max(documentRevision,revision||0);
   reconnectPending=false;
   resolveReady();
  }else{
   acknowledged=null;
   reconnectPending=true;
  }
  status({status:'stored',commandId:work.commandId,storageVersion:work.storageVersion||local.storageVersion,
   activeScope:work.activeScope,revision:work.revision||local.revision});
  settleCommand(work.commandId,{ok:true,value:{...result,status:'stored',commandId:work.commandId,
   activeScope:work.activeScope,values:clone(work.values),storageVersion:work.storageVersion,
   namespace:work.namespace,generation:work.generation,revision:work.revision}});
 }

 async function rollbackRejectedRecord(work){
  const owner={namespace:work.namespace,generation:work.generation};
  if(!ownsSettlement(work))return false;
  if(work.storageRecovered)return true;
  await withNamespaceLock(()=>{
   if(!ownsSettlement(work))return;
   const record=readAppearanceStorageRecord(storageValue(),owner.namespace);
   // Restore only this command's exact write, never a newer tab's record. A fresh
   // token (including for an originally absent record) prevents storage-version ABA.
   if(record.version===work.storageVersion&&record.activeScope===work.activeScope&&equal(record.values,work.values)){
    const version=createVersion();
    if(typeof version!=='string'||!version||version===MISSING||version.length>256||
       version===record.version||version===work.before.version)
     throw Error('Could not create a new browser preference recovery version.');
    const restored={version,activeScope:work.before.activeScope,values:clone(work.before.values)};
    storageValue().setItem(owner.namespace,JSON.stringify(restored));
    if(!equal(readAppearanceStorageRecord(storageValue(),owner.namespace),restored))
     throw Error('The browser preference recovery could not be read back exactly.');
   }
   work.storageRecovered=true;
  });
  return ownsSettlement(work);
 }

 async function recoverRejectedSettlement(work,server){
  const owner={namespace:work.namespace,generation:work.generation};
  if(!await rollbackRejectedRecord(work))return false;
  if(!ownsSettlement(work))return false;
  const local=server.local||{},detail=work.error||'The browser preference activation was rejected.';
  if(server.command?.status==='pending'){
   // Failed ACKs settle the command but do not update the server mirror. Report
   // its confirmed base here; ordinary ready reconciliation then reports storage.
   const result=await report({
    generation:owner.generation,revision:Number.isSafeInteger(local.revision)?local.revision:0,
    storageVersion:typeof local.storageVersion==='string'?local.storageVersion:MISSING,
    activeScope:server.activeScope==='client'?'client':'shared',
    values:local.values===null?null:plainObject(local.values)?clone(local.values):null,
    status:'failed',commandId:work.commandId,error:detail,
   });
   if(!ownsSettlement(work))return false;
   if(result.status!=='failed')throw Error(result.error||'The rejected browser preference command was not settled.');
  }
  finishFailedSettlement(work,Error(detail),server);
  await reconcileActual();
  return true;
 }

 async function reconcileSettlement(work){
  const owner={namespace:work.namespace,generation:work.generation};
  // A refusal queued behind a successful retry no longer owns this settlement.
  if(!ownsSettlement(work))return false;
  try{
   let server,timer;
   try{
    // Give stored-command proof the first opportunity, but never let a stalled
    // read indefinitely retain a known rejected record. Only this bounded await
    // consumes the read: a response arriving after the deadline has no effects.
    server=await Promise.race([readPreferences(),new Promise((_,reject)=>{
     timer=setTimeout(()=>reject(Error('The browser preference settlement read timed out.')),
      Number.isFinite(ackTimeoutMs)&&ackTimeoutMs>0?ackTimeoutMs:10000);
    })]);
   }catch(error){
    // Preserve exact-record recovery when reads are unavailable. When a read
    // succeeds, its proven stored command takes precedence over an older refusal.
    if(work.rejected&&!await rollbackRejectedRecord(work))return false;
    throw error;
   }finally{clearTimeout(timer)}
   if(!ownsSettlement(work))return false;
   if(server.namespace!==owner.namespace||server.generation!==owner.generation)return false;
   latestSnapshot=server;
   const command=server.command;
   if(command?.id===work.commandId&&command.generation===owner.generation&&command.status==='stored'){
    finishStoredSettlement(work,{status:'stored',commandId:work.commandId,undoId:command.undoId},server);
    return true;
   }
   if(work.rejected&&!await rollbackRejectedRecord(work))return false;
   if(!command||command.id!==work.commandId||command.generation!==owner.generation){
    finishFailedSettlement(work,'A newer browser preference decision replaced this command. It was not replayed.',server);
    return true;
   }
   if(work.status==='stored'&&(work.rejected||command.status==='failed')){
    work.rejected=true;
    return await recoverRejectedSettlement(work,server);
   }
   if(command.status==='failed'){
    finishFailedSettlement(work,command.error||work.error,server);
    return true;
   }
   if(command.status!=='pending'){
    finishFailedSettlement(work,command.error||'The browser preference command was not stored.',server);
    return true;
   }
   if(work.status==='failed'){
    const local=server.local||{};
    const result=await report({
     generation:owner.generation,revision:Number.isSafeInteger(local.revision)?local.revision:0,
     storageVersion:typeof local.storageVersion==='string'?local.storageVersion:MISSING,
     activeScope:server.activeScope==='client'?'client':'shared',
     values:local.values===null?null:plainObject(local.values)?clone(local.values):null,
     status:'failed',commandId:work.commandId,error:work.error,
    });
    if(!ownsSettlement(work))return false;
    if(result.status==='failed'){
     finishFailedSettlement(work,result.error||work.error,{...server,command:{...command,status:'failed',error:result.error||work.error}});
     return true;
    }
    if(result.status==='stored'){
     finishStoredSettlement(work,result,server);
     return true;
    }
    throw Error(result.error||'The failed browser preference command was not settled.');
   }
   const record=await readRecord();
   if(!ownsSettlement(work))return false;
   if(record.version===work.storageVersion&&record.activeScope===work.activeScope&&equal(record.values,work.values)){
    let result;
    try{
     result=await report({
      generation:owner.generation,revision:work.revision,storageVersion:work.storageVersion,
      activeScope:work.activeScope,values:clone(work.values),status:'stored',commandId:work.commandId,
     },work);
    }catch(error){
     if(!ownsSettlement(work))return false;
     if(!error.reportRejected)throw error;
     work.rejected=true;work.error=message(error,'The browser preference activation was rejected.');
     return await recoverRejectedSettlement(work,server);
    }
    if(!ownsSettlement(work))return false;
    if(result.status==='stored'){
     const updated={...server,activeScope:work.activeScope,command:{...command,status:'stored',undoId:result.undoId},
      local:{...(server.local||{}),revision:work.revision,storageVersion:work.storageVersion,
       values:clone(work.values),reported:true,error:null}};
     finishStoredSettlement(work,result,updated);
     return true;
    }
    if(result.status==='failed'){
     work.rejected=true;work.error=result.error||command.error||'The browser preference command was not stored.';
     return await recoverRejectedSettlement(work,{...server,command:{...command,status:'failed',error:work.error}});
    }
    throw Error(result.error||'The stored browser preference command was not settled.');
   }
   const detail=work.error||'The browser value could not be confirmed; the latest stored value was retained and the command was not replayed.';
   const local=server.local||{};
   const result=await report({
    generation:owner.generation,revision:Number.isSafeInteger(local.revision)?local.revision:0,
    storageVersion:typeof local.storageVersion==='string'?local.storageVersion:MISSING,
    activeScope:server.activeScope==='client'?'client':'shared',
    values:local.values===null?null:plainObject(local.values)?clone(local.values):null,
    status:'failed',commandId:work.commandId,error:detail,
   });
   if(!ownsSettlement(work))return false;
   if(result.status==='failed'){
    finishFailedSettlement(work,result.error||detail,{...server,command:{...command,status:'failed',error:result.error||detail}});
    return true;
   }
   if(result.status==='stored'){
    finishStoredSettlement(work,result,server);
    return true;
   }
   throw Error(result.error||'The unconfirmed browser preference command was not settled.');
  }catch(error){
   if(!ownsSettlement(work))return false;
   status({status:'failed',commandId:work.commandId,error:message(error,'Could not reconcile the browser preference receipt.')});
   return false;
  }
 }

 async function failCommand(command,error,owner=stamp()){
  if(!sameGeneration(owner)||command?.generation!==owner.generation||typeof command?.id!=='string')return false;
  const detail=message(error,'Browser preference storage failed; shared preferences were not changed.');
  const server=latestSnapshot?.namespace===owner.namespace&&latestSnapshot?.generation===owner.generation
   ?latestSnapshot:null;
  const local=server?.local||{};
  const work={
   namespace:owner.namespace,generation:owner.generation,commandId:command.id,status:'failed',error:detail,
   revision:Number.isSafeInteger(local.revision)?local.revision:0,
   storageVersion:typeof local.storageVersion==='string'?local.storageVersion:MISSING,
   activeScope:server?.activeScope==='client'?'client':'shared',
   values:local.values===null?null:plainObject(local.values)?clone(local.values):null,
  };
  settlements.set(command.id,work);
  if(!acknowledged)recordReadinessFailure(Error(detail));
  let reconciled=false;
  if(current(owner))try{
   // This command is known not to have reached its storage write. Report that
   // generation-bound outcome from the last observed local record directly;
   // an extra verification read must not prevent a writable server receipt.
   const result=await report({
    generation:owner.generation,revision:work.revision,storageVersion:work.storageVersion,
    activeScope:work.activeScope,values:clone(work.values),status:'failed',commandId:command.id,error:detail,
   });
   if(!current(owner))throw Error('The browser disconnected before the failed preference receipt was confirmed.');
   if(result.status==='failed'){
    const settledError=result.error||detail;
    const settledServer=server?{
     ...server,
     activeScope:work.activeScope,
     local:{...(server.local||{}),revision:work.revision,storageVersion:work.storageVersion,
      values:clone(work.values),reported:true,error:settledError},
     command:server.command?.id===command.id
      ?{...server.command,status:'failed',error:settledError}:server.command,
    }:null;
    finishFailedSettlement(work,settledError,settledServer);
    reconciled=true;
   }else status({status:'failed',commandId:command.id,error:result.error||detail});
  }catch(reportError){
   status({status:'failed',commandId:command.id,error:message(reportError,detail)});
  }
  if(!reconciled){
   status({status:'failed',commandId:command.id,error:detail});
   settleCommand(command.id,{ok:false,error:detail});
  }
  return reconciled;
 }

 async function processCommand(command){
  if(!command||typeof command.id!=='string'||attempted.has(command.id)||settlements.has(command.id)||command.status!=='pending')return;
  const owner=stamp();
  if(!current(owner)||command.generation!==owner.generation||command.namespace!==owner.namespace){
   settleCommand(command.id,{ok:false,error:'This browser preference command belongs to an older document and was not replayed.'});
   return;
  }
  if(!acknowledged){
   const ready=await reconcileActual();
   if(!current(owner)){
    if(sameGeneration(owner))await failCommand(command,Error('The browser disconnected before the preference value was written.'),owner);
    return;
   }
   const latestCommand=latestSnapshot?.command;
   if(!ready||!acknowledged){
    if(latestCommand?.id===command.id&&latestCommand.status!=='pending')return;
    if(!settlements.has(command.id))await failCommand(command,readinessFailure||Error('Browser preferences could not be verified before this command was written.'),owner);
    return;
   }
  }
  let server;
  try{server=await readPreferences()}catch(error){
   await failCommand(command,error,owner);
   return;
  }
  if(!current(owner)){
   if(sameGeneration(owner))await failCommand(command,Error('The browser disconnected before the preference value was written.'),owner);
   return;
  }
  if(server.namespace!==owner.namespace||server.generation!==owner.generation){
   settleCommand(command.id,{ok:false,error:'This browser preference command belongs to an older document and was not replayed.'});
   return;
  }
  latestSnapshot=server;
  const currentCommand=server.command;
  if(!currentCommand||currentCommand.id!==command.id||currentCommand.status!=='pending'){
   if(currentCommand?.id===command.id&&currentCommand.status==='stored'){
    const local=server.local||{};
    if(local.storageVersion===acknowledged.storageVersion){
     settleCommand(command.id,{ok:true,value:{status:'stored',commandId:command.id,undoId:currentCommand.undoId}});
     return;
    }
   }
   const outcome=currentCommand?.id===command.id?currentCommand.status:'superseded';
   const error=currentCommand?.error||'A newer browser preference decision replaced this command. It was not replayed.';
   settleCommand(command.id,{ok:false,error});
   status({status:'failed',commandId:command.id,error,outcome});
   return;
  }
  const expectedRevision=Number.isSafeInteger(currentCommand.expectedRevision)?currentCommand.expectedRevision:-1;
  let before,next,work,writeAttempted=false,storageWriteError=null;
  try{
   if(acknowledged.revision!==expectedRevision||
      acknowledged.storageVersion!==currentCommand.expectedStorageVersion||
      acknowledged.activeScope!==server.activeScope||
      !equal(acknowledged.values,server.local?.values??null))
    throw Error('Another browser preference change arrived first. The newer value was kept.');
   const settled=await withNamespaceLock(async()=>{
    if(!current(owner))throw Error('The browser document changed before storage could be updated.');
    const record=readAppearanceStorageRecord(storageValue(),namespace);
    if(record.version!==currentCommand.expectedStorageVersion||
       record.activeScope!==acknowledged.activeScope||!equal(record.values,acknowledged.values))
     throw Error('Another browser tab changed these preferences. The newer value was kept.');
    const updated=applyAppearancePreferenceCommand(record,currentCommand,createVersion);
    before=record;
    next=updated;
    attempted.add(currentCommand.id);
    writeAttempted=true;
    try{storageValue().setItem(namespace,JSON.stringify(updated))}catch(error){storageWriteError=error;throw error}
    const readback=readAppearanceStorageRecord(storageValue(),namespace);
    if(!equal(readback,updated))throw Error('The browser preference write could not be read back exactly.');
    if(!current(owner))throw Error('The browser disconnected after storing this value; it was not replayed.');
    const revision=expectedRevision+1;
    work={namespace:owner.namespace,generation:owner.generation,commandId:currentCommand.id,status:'stored',
     revision,storageVersion:updated.version,activeScope:updated.activeScope,values:clone(updated.values),before};
    const result=await report({
     generation:owner.generation,revision,storageVersion:updated.version,
     activeScope:updated.activeScope,values:clone(updated.values),status:'stored',commandId:currentCommand.id,
    },work);
    if(result.status!=='stored'){
     const error=Error(result.error||'Browser storage was not acknowledged.');
     error.reportRejected=result.status==='failed';
     throw error;
    }
    return {result,revision};
   });
   if(!current(owner))return;
   const {result,revision}=settled;
   documentRevision=Math.max(documentRevision,revision);
   acknowledged={namespace:owner.namespace,generation:owner.generation,revision,storageVersion:next.version,
    activeScope:next.activeScope,values:clone(next.values)};
   latestSnapshot={...server,activeScope:next.activeScope,
    command:{...currentCommand,status:'stored',undoId:result.undoId},
    local:{...(server.local||{}),revision,storageVersion:next.version,values:clone(next.values),reported:true,error:null}};
   settlements.delete(currentCommand.id);
   status({status:'stored',commandId:currentCommand.id,storageVersion:next.version,activeScope:next.activeScope,revision});
   settleCommand(currentCommand.id,{ok:true,value:{...result,status:'stored',commandId:currentCommand.id,
    activeScope:next.activeScope,values:clone(next.values),storageVersion:next.version,
    namespace:owner.namespace,generation:owner.generation,revision}});
  }catch(error){
   if(!writeAttempted){
    await failCommand(currentCommand,error,owner);
    return;
   }
   const detail=error.reportRejected?message(error,'The browser preference activation was rejected.'):storageWriteError
    ?`${message(storageWriteError,'The browser storage write failed.')} The write outcome was not confirmed; the latest stored value was retained and no command was replayed.`
    :'The browser value was saved, but its activation was not confirmed. The latest stored value was retained; no command was replayed.';
   work??={namespace:owner.namespace,generation:owner.generation,commandId:currentCommand.id,status:'stored',
    revision:expectedRevision+1,storageVersion:next.version,activeScope:next.activeScope,values:clone(next.values),before};
   work.rejected=error.reportRejected===true;work.error=detail;
   settlements.set(currentCommand.id,work);
   const reconciled=await reconcileSettlement(work);
   if(!reconciled){
    status({status:'failed',commandId:currentCommand.id,error:detail});
    settleCommand(currentCommand.id,{ok:false,error:detail});
   }
  }
 }

 function onStorage(event){
  if(!connected||!namespace||event?.key!==null&&event?.key!==namespace)return;
  void enqueue(async()=>{
   const pending=[...settlements.values()].find(work=>sameGeneration(work));
   if(pending&&!await reconcileSettlement(pending))return;
   const synced=await reconcileActual();
   const command=latestSnapshot?.command;
   if(synced&&command?.status==='pending')await processCommand(command);
  }).catch(error=>status({status:'failed',error:message(error,'Could not refresh browser preferences.')}));
 }

 function attachStorageListener(){
  const destination=target();
  if(!destination?.addEventListener||storageListener)return;
  storageListener=onStorage;
  destination.addEventListener('storage',storageListener);
 }

 function detachStorageListener(){
  if(!storageListener)return;
  target()?.removeEventListener?.('storage',storageListener);
  storageListener=null;
 }

 function disconnect(){
  if(!connected)return;
  connected=false;
  reconnectPending=true;
  detachStorageListener();
  const failure=Error('The browser disconnected before the preference receipt was confirmed. No command was replayed.');
  rejectReady(failure);
  for(const [id,waiter] of waiters){waiters.delete(id);waiter.reject(failure)}
  status({status:'disconnected'});
 }

 function observe(appearance,{connected:available=appearance?.availability==='connected'}={}){
  if(!appearance?.namespace||!appearance?.generation)return Promise.resolve();
  if(!available){disconnect();return Promise.resolve()}
  const changed=namespace!==appearance.namespace||generation!==appearance.generation;
  const reconnected=!connected;
  if(changed){
   const failure=Error('The browser document changed before its preference receipt was confirmed.');
   rejectReady(failure);
   for(const [id,waiter] of waiters){waiters.delete(id);waiter.reject(failure)}
   for(const [id,work] of settlements){
    if(work.namespace!==appearance.namespace||work.generation!==appearance.generation){
     settlements.delete(id);
     settleCommand(id,{ok:false,error:failure.message});
    }
   }
   namespace=appearance.namespace;
   generation=appearance.generation;
   readinessFailure=null;
   acknowledged=null;
   documentRevision=Number.isSafeInteger(appearance.local?.revision)?appearance.local.revision:0;
   attempted=new Set();
   reconnectPending=true;
  }
  connected=true;
  latestSnapshot=appearance;
  attachStorageListener();
  const local=appearance.local||{};
  const needsSync=changed||reconnected||!acknowledged||!local.reported||!!local.error||!!readinessFailure||
   local.revision!==acknowledged?.revision||local.storageVersion!==acknowledged?.storageVersion||
   appearance.activeScope!==acknowledged?.activeScope||
   !equal(local.values||{},acknowledged?.values||{});
  return enqueue(async()=>{
   const owner={namespace:appearance.namespace,generation:appearance.generation};
   if(!current(owner))return;
   const pending=[...settlements.values()].find(work=>sameGeneration(work));
   if(pending&&!await reconcileSettlement(pending))return;
   if(needsSync)await reconcileActual();
   const currentCommand=latestSnapshot?.command;
   if(currentCommand?.status==='pending')await processCommand(currentCommand);
  });
 }

 function reconcile(){
  return enqueue(async()=>{
   const owner=stamp();
   if(!current(owner))return false;
   const pending=[...settlements.values()].find(work=>sameGeneration(work));
   if(pending&&!await reconcileSettlement(pending))return false;
   return reconcileActual();
  });
 }

 function reportRendered(renderedCompositionId){
  if(typeof renderedCompositionId!=='string'||!renderedCompositionId)return Promise.resolve({rendered:false});
  return enqueue(async()=>{
   if(!connected||!acknowledged)return {rendered:false};
   const owner=stamp(),local=acknowledged;
   if(local.namespace!==namespace||local.generation!==generation)return {rendered:false};
   return report({
    generation:owner.generation,revision:local.revision,storageVersion:local.storageVersion,
    activeScope:local.activeScope,values:clone(local.values),status:'ready',renderedCompositionId,
   });
  });
 }

 return {
  setDispatch(value){invoke=value},
  observe,
  disconnect,
  reconcile,
  waitUntilReady,
  waitForCommand,
  reportRendered,
  storageChanged:onStorage,
  getStatus:()=>currentStatus,
  getSnapshot:()=>({namespace,generation,connected,acknowledged:acknowledged&&clone(acknowledged)}),
 };
}
