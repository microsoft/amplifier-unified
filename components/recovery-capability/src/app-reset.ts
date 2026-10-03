import {createHash} from 'node:crypto';
import type {AppResetOwner,Context,FenceContext,Job,Json} from './types.js';
const hash=(value:unknown)=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const allowedOwners:Record<string,string[]>={
 'native-app-defaults':['native.app-bundle-default'],
 notifications:['notifications.settings','notifications.credentials'],
 updates:['updates.preferences'],
};
const allowed=Object.values(allowedOwners).flat();
export const appResetHash=hash;
const record=(v:unknown):v is Json=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const text=(v:unknown,max=200):v is string=>typeof v==='string'&&v.length>0&&v.length<=max;
const strings=(v:unknown,max=32)=>Array.isArray(v)&&v.length<=max&&v.every(x=>text(x,512));
const ownedParts=(v:unknown,owner:AppResetOwner):v is string[]=>Array.isArray(v)&&v.length>0&&v.length<=3&&new Set(v).size===v.length&&v.every(p=>owner.parts.includes(p));
const digest=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
export function resetOwners(native:Json|undefined,nativeAdmin:(op:string,args:Json,ctx:Context)=>Promise<Json>,provided:AppResetOwner[]=[]){
 const owners=[...provided];
 if(native?.appReset?.version===1&&native.appReset.retainedUndo===true&&native.appReset.requiresRecoveryAdminLease===true&&native.appReset.parts?.includes(allowed[0]))owners.unshift({id:'native-app-defaults',parts:[allowed[0]],perform:(op,args,_fence,ctx)=>nativeAdmin('maintenance.appReset.'+op,args,ctx)});
 const parts=new Set<string>(),ids=new Set<string>();
 if(owners.length>3)throw Error('Only the explicit native/notification/update reset owners are supported');
 for(const owner of owners){if(!Object.hasOwn(allowedOwners,owner.id)||ids.has(owner.id)||!Array.isArray(owner.parts)||!owner.parts.length||typeof owner.perform!=='function')throw Error('Invalid registered app reset owner');ids.add(owner.id);for(const part of owner.parts){if(!allowedOwners[owner.id].includes(part)||parts.has(part))throw Error('Unknown, duplicate or misowned reset part');parts.add(part);}}
 return owners;
}
/** Only public review fields survive; owner private values cannot enter product receipts. */
export function publicReview(value:Json,owner:AppResetOwner,parts:string[]){
 const keys=['ownerId','preparedId','reviewHash','parts','revision','expiresAt','containsPrivateContent','credentialsIncluded','coverage','preserved','omissions','restoresCommandId','items'];
 if(!record(value))throw Error('Invalid immutable owner reset review');
 if(Object.keys(value).some(k=>!keys.includes(k)))throw Error('Unexpected reset review field');
 if(value.ownerId!==owner.id||!text(value.preparedId)||!digest(value.reviewHash)||!text(value.revision)||!ownedParts(value.parts,owner)||JSON.stringify(value.parts)!==JSON.stringify(parts)||!Number.isFinite(value.expiresAt)||value.expiresAt<=0||value.coverage!=='explicit-app-local-parts'||value.containsPrivateContent!==true||value.credentialsIncluded!==parts.includes('notifications.credentials')||!(value.restoresCommandId===null||text(value.restoresCommandId,256))||!Array.isArray(value.items)||value.items.length!==parts.length||!strings(value.preserved)||!strings(value.omissions)||Buffer.byteLength(JSON.stringify(value))>16384)throw Error('Invalid immutable owner reset review');
 for(const [i,item] of value.items.entries())if(!record(item)||Object.keys(item).some(k=>!['part','present','operation'].includes(k))||item.part!==parts[i]||!['restore','clear-bundle-active-only','reset-to-disabled-defaults','clear-topic-and-token'].includes(item.operation)||Object.hasOwn(item,'present')&&typeof item.present!=='boolean')throw Error('Invalid owner reset review item');
 return structuredClone(value);
}
export class AppResets {
 readonly parts:string[];
 constructor(readonly owners:AppResetOwner[]){this.parts=owners.flatMap(o=>o.parts);}
 select(parts:string[]){return this.owners.filter(o=>o.parts.some(p=>parts.includes(p)));}
 owner(id:string){const value=this.owners.find(o=>o.id===id);if(!value)throw Error('Registered reset owner is unavailable');return value;}
 receipt(response:Json,owner:AppResetOwner,commandId:string,operation:string){
 const receipt=response?.receipt;
 if(!record(receipt)||receipt.commandId!==commandId||![operation,'maintenance.appReset.'+operation].includes(receipt.operation)||!['succeeded','refused','unknown','running'].includes(receipt.state))throw Error('Exact owner reset receipt required');
 if(Object.hasOwn(receipt,'ownerId')&&receipt.ownerId!==owner.id||Object.hasOwn(receipt,'replayed')&&typeof receipt.replayed!=='boolean'||Object.hasOwn(receipt,'executed')&&typeof receipt.executed!=='boolean'||['createdAt','settledAt'].some(k=>Object.hasOwn(receipt,k)&&(!Number.isFinite(receipt[k])||receipt[k]<0)))throw Error('Invalid public owner receipt field');
 if(receipt.state==='refused'&&receipt.executed!==false)throw Error('Reset refusal does not prove no effect');
 let result:Json|undefined;
 if(receipt.state==='succeeded'){
  const value=receipt.result;if(!record(value))throw Error('Owner result required');
  if(operation==='prepare')result=publicReview(value,owner,value.parts??[]);
  else{const keys=['ownerId','preparedId','reviewHash','parts','postResetRevision','restored','preserved','replayed','canonicalFilesChanged','sharedSettingsChanged'];if(Object.keys(value).some(k=>!keys.includes(k))||value.ownerId!==owner.id||!text(value.preparedId)||!digest(value.reviewHash)||!ownedParts(value.parts,owner)||!text(value.postResetRevision)||value.restored!==(operation==='restore')||!strings(value.preserved)||value.replayed!==false||Object.hasOwn(value,'canonicalFilesChanged')&&value.canonicalFilesChanged!==0||Object.hasOwn(value,'sharedSettingsChanged')&&value.sharedSettingsChanged!==false||Buffer.byteLength(JSON.stringify(value))>16384)throw Error('Invalid public owner reset result');result=structuredClone(value);}
 }
 // Non-success results can contain diagnostics/private values; never retain them.
 return {ownerId:owner.id,...Object.fromEntries(['commandId','operation','state','executed','replayed','createdAt','settledAt'].filter(k=>Object.hasOwn(receipt,k)).map(k=>[k,receipt[k]])),...(result?{result}:{})};
 }
 async perform(job:Job,changed:()=>void,prior?:Job,original?:Job){
  const operation=job.operation.slice('recovery.appReset.'.length),parts=operation==='prepare'?job.args.parts:prior!.preview!.parts;
  const owners=this.select(parts);job.appResetCommands??={};job.appResetReceipts??={};job.nativeLeaseReleased=false;changed();
  if(operation!=='prepare'){
   // Every owner must still match before the first effect. Owner CAS repeats this
   // immediately before its own transaction; no cross-owner atomicity is claimed.
   for(const review of prior!.preview!.owners){const result=await this.owner(review.ownerId).perform('inspect',{preparedId:review.preparedId,reviewHash:review.reviewHash},job.fence,job.context);if(result.applicable!==true||result.revision!==review.revision){job.terminalState='refused';job.reason='app-reset-review-expired-or-changed';job.nativeLeaseReleased=true;changed();return;}}
  }
  for(const owner of owners){
   const commandId='recovery-app-reset:'+hash([job.accountId,job.commandId,owner.id,operation]);job.appResetCommands[owner.id]=commandId;changed();
   let args:Json;
   if(operation==='prepare')args={commandId,parts:parts.filter((p:string)=>owner.parts.includes(p)),privateContentReviewed:true,...(parts.includes('notifications.credentials')&&owner.id==='notifications'?{credentialsReviewed:true}:{}),...(original?{restoreCommandId:original.appResetCommands![owner.id]}:{})};
   else{const review=prior!.preview!.owners.find((r:Json)=>r.ownerId===owner.id);args={commandId,preparedId:review.preparedId,reviewHash:review.reviewHash,...(operation==='restore'?{resetCommandId:original!.appResetCommands![owner.id],expectedPostResetRevision:original!.appResetReceipts![owner.id].result.postResetRevision}:{})};}
   let receipt:Json;
   try{const response=await owner.perform(operation,args,job.fence,job.context);receipt=this.receipt(response,owner,commandId,operation);}catch(error:any){if(error?.data?.executed!==false)throw error;receipt={ownerId:owner.id,commandId,operation,state:'refused',executed:false,replayed:false};}
   job.appResetReceipts[owner.id]=receipt;changed();
   if(receipt.state!=='succeeded')break;
  }
  this.settle(job,parts,owners,operation,changed);
 }
 settle(job:Job,parts:string[],owners:AppResetOwner[],operation:string,changed:()=>void){
  const receipts=owners.map(o=>job.appResetReceipts?.[o.id]),succeeded=receipts.filter(r=>r?.state==='succeeded');
  if(receipts.length&&receipts.every(r=>r?.state==='succeeded')){
   if(operation==='prepare'){
    const reviews=owners.map(o=>publicReview(job.appResetReceipts![o.id].result,o,parts.filter(p=>o.parts.includes(p))));
    const preview={kind:'app-local-reset',coverage:'explicit-app-local-parts',parts,owners:reviews,expiresAt:Math.min(...reviews.map(r=>r.expiresAt)),containsPrivateContent:true,credentialsIncluded:parts.includes('notifications.credentials'),completeAppReset:false,omissions:[...(!parts.includes('updates.preferences')?['supervisor preferences']:[]),'host policy','conversation presentation rows','shared settings and credentials'],restoresJobId:job.args.restoreResetJobId??null};
    job.preview={...preview,previewHash:hash(preview)};job.terminalState='prepared';
   }else{
    const rows=owners.map(o=>{const r=job.appResetReceipts![o.id].result;const review=job.preview!.owners.find((v:Json)=>v.ownerId===o.id);if(!r||r.ownerId!==o.id||typeof r.postResetRevision!=='string'||r.postResetRevision.length>200||r.preparedId!==review?.preparedId||r.reviewHash!==review?.reviewHash||JSON.stringify(r.parts)!==JSON.stringify(review.parts)||r.restored!==(operation==='restore'))throw Error('Invalid owner reset result');return {ownerId:o.id,commandId:job.appResetCommands![o.id],postResetRevision:r.postResetRevision};});
    job.result={coverage:'explicit-app-local-parts',parts,owners:rows,postResetRevision:hash(rows),restored:operation==='restore',completeAppReset:false,replayed:false};job.terminalState='succeeded';
   }
   job.nativeLeaseReleased=true;
  }else if(!succeeded.length&&receipts.some(r=>r?.state==='refused'&&r.executed===false)){
   job.terminalState='refused';job.nativeLeaseReleased=true;job.reason='app-reset-owner-refused-before-effects';
  }else{job.terminalState=undefined;job.reason='app-reset-partial-or-unknown-no-replay';}
  changed();
 }
 async inspect(job:Job,changed:()=>void){
  const operation=job.operation.slice('recovery.appReset.'.length),parts=job.operation.endsWith('.prepare')?job.args.parts:job.preview!.parts,owners=this.select(parts);
  for(const owner of owners){const commandId=job.appResetCommands?.[owner.id];if(!commandId)continue;const inspected=await owner.perform('inspect',{commandId},undefined,job.context);if(inspected.receipt)job.appResetReceipts![owner.id]=this.receipt(inspected,owner,commandId,operation);changed();}
  this.settle(job,parts,owners,operation,changed);
 }
}
