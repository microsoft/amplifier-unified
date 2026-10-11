import {createHash} from 'node:crypto';
import type {HistoryCleanupPort,Job,Json,Options} from './types.js';
const hash=(value:unknown)=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const same=(a:unknown,b:unknown)=>JSON.stringify(a)===JSON.stringify(b);
export function historyCleanupAvailable(native:Json|undefined,port:HistoryCleanupPort|undefined){
 const c=native?.version===1?native.historyCleanup:undefined;
 return c?.version===1&&c.previewRequired===true&&c.receipt===true&&c.requiresRecoveryAdminLease===true&&c.trustedOwnerCensus===true&&c.workspacePreserved===true&&c.stableLocksPreserved===true&&c.automaticReplay===false&&port?.version===1&&['inspect','settle','receipt'].every(key=>typeof (port as any)[key]==='function');
}
/** Native owns canonical bytes; Host owns only the resulting visibility projection. */
export class HistoryCleanups {
 constructor(private port:HistoryCleanupPort,private native:Options['nativeAdmin']){}
 private nativeReceipt(job:Job,receipt:Json){
  if(receipt?.commandId!==job.nativeCommandId||receipt.operation!=='maintenance.history.remove')throw Error('Exact original history removal receipt required');
  if(receipt.state==='refused'&&receipt.executed===false){job.nativeResult=receipt;job.terminalState='refused';return false;}
  const r=receipt.result,p=job.preview!;
  if(receipt.state!=='succeeded'||r?.removed!==true||r.previewHash!==p.previewHash||r.confirmationId!==p.confirmationId||!same(r.selection,p.selection)||r.sessionCount!==p.sessionCount||r.removedBytes!==p.inventory.bytes||r.workspacePreserved!==true||r.stableLocksPreserved!==true||r.receiptsPreserved!==true||r.replayed!==false)throw Error('Native history removal is not conclusively settled');
  job.nativeResult={state:'succeeded',commandId:receipt.commandId,operation:receipt.operation};return true;
 }
 private projected(job:Job,receipt:Json){
  const r=receipt?.result,p=job.preview!,selection=job.historyCleanup!.selection!;
  if(receipt?.commandId!==job.historyCleanup?.projectionCommandId||receipt.operation!=='history.cleanup.hideChats'||receipt.state!=='succeeded'||r?.removed!==true||r.workspacePreserved!==true||r.nativeCommandId!==job.nativeCommandId||r.nativeRevision!==p.confirmationId||!same(r.sessions,selection.sessions.map((row:Json)=>row.session))||r.replayed!==false)throw Error('History visibility has no exact completed owner receipt');
  job.historyCleanup!.projectionReceipt=structuredClone(receipt);
  job.result={removed:true,selection:p.selection,sessionCount:p.sessionCount,removedBytes:p.inventory.bytes,workspacePreserved:true,stableLocksPreserved:true,receiptsPreserved:true,productArtifactsPreserved:true,replayed:false};job.terminalState='succeeded';
 }
 async perform(job:Job,changed:()=>void){
  job.nativeLeaseReleased=true;job.historyCleanup??={};changed();
  const prepare=job.operation==='recovery.history.prepare';
  try{
   const requested=prepare?job.args.selection:job.preview!.selection;
   const census=await this.native('maintenance.history.census',{selection:requested},job.context);
   const selection=await this.port.inspect(census,job.fence!,job.context),owners=selection?.ownerCensus;
   if(selection?.version!==1||!same(selection.selection,requested)||selection.nativeRevision!==census.revision||selection.nativeSessionCount!==census.sessions?.length||owners?.version!==1||owners.coverage!=='all-configured-owners-held'||owners.nativeRevision!==census.revision||!/^[a-f0-9]{64}$/.test(owners.revision)||Buffer.byteLength(JSON.stringify(selection))>1024*1024)throw Error('Complete held history owner census required');
   if(prepare){
    const p=await this.native('maintenance.history.preview',{selection:requested,ownerCensus:owners,reviewed:true},job.context);
    if(p?.kind!=='shared-history-removal'||!/^[a-f0-9]{64}$/.test(p.previewHash)||p.confirmationId!==census.revision||!same(p.selection,requested)||p.sessionCount!==selection.nativeSessionCount||!Number.isSafeInteger(p.inventory?.bytes)||p.inventory.bytes<0||!Number.isFinite(p.expiresAt)||p.permanent!==true||p.workspacePreserved!==true||p.stableLocksPreserved!==true||p.receiptsPreserved!==true||p.replayed!==false)throw Error('Invalid shared history cleanup review');
    job.preview={...p,coverage:'selected-canonical-history-and-navigation',productArtifactsPreserved:true};job.terminalState='prepared';changed();return;
   }
   if(job.args.confirmationId!==job.preview!.confirmationId)throw Object.assign(Error('Exact history confirmation required'),{data:{executed:false}});
   job.historyCleanup.selection=structuredClone(selection);job.nativeOperation='maintenance.history.remove';job.nativeCommandId='recovery:'+hash([job.accountId,job.commandId,'history-remove']);changed();
   const result=await this.native(job.nativeOperation,{commandId:job.nativeCommandId,previewHash:job.preview!.previewHash,ownerCensus:owners,confirmationId:job.args.confirmationId},job.context);
   if(!this.nativeReceipt(job,result.receipt)){changed();return;}
   await this.project(job,result.receipt,changed);
  }catch(error:any){
   if(prepare||!job.nativeCommandId||error?.data?.executed===false&&job.nativeResult?.state!=='succeeded'){job.terminalState='refused';job.reason='history-cleanup-refused-before-removal';}
   else{job.terminalState=undefined;job.reason='history-cleanup-outcome-unknown-no-replay';}
   changed();
  }
 }
 private async project(job:Job,receipt:Json,changed:()=>void){
  job.historyCleanup!.projectionCommandId??='recovery:'+hash([job.accountId,job.commandId,'history-projection']);changed();
  this.projected(job,(await this.port.settle({commandId:job.historyCleanup!.projectionCommandId!,selection:job.historyCleanup!.selection!,nativeReceipt:receipt},job.fence!,job.context)).receipt);changed();
 }
 async finish(job:Job,changed:()=>void){
  if(job.operation!=='recovery.history.remove'||!job.nativeCommandId||!job.historyCleanup?.selection)throw Error('Original history removal and retained owner selection required');
  const result=await this.native('maintenance.receipt',{commandId:job.nativeCommandId},job.context);
  if(!this.nativeReceipt(job,result.receipt)){changed();return;}
  await this.project(job,result.receipt,changed);
 }
 async inspect(job:Job,changed:()=>void){
  if(!job.nativeCommandId)return;
  try{
   const result=await this.native('maintenance.receipt',{commandId:job.nativeCommandId},job.context);
   if(this.nativeReceipt(job,result.receipt)){
    if(!job.historyCleanup?.projectionCommandId){job.terminalState=undefined;job.reason='history-removed-navigation-not-confirmed';changed();return;}
    this.projected(job,(await this.port.receipt(job.historyCleanup.projectionCommandId,job.context)).receipt);
   }
   changed();
  }catch{job.terminalState=undefined;job.reason='history-cleanup-outcome-unknown-no-replay';changed();}
 }
}
