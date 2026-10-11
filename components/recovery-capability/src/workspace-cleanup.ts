import {createHash} from 'node:crypto';
import type {Job,Json,Options,WorkspaceCleanupPort} from './types.js';
const hash=(value:unknown)=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const refused=(error:any)=>error?.data?.executed===false;
export function cleanupAvailable(native:Json|undefined,port:WorkspaceCleanupPort|undefined){
 const capability=native?.version===1?native.workspaceCleanup:undefined;
 return capability?.version===1&&capability.previewRequired===true&&capability.receipt===true&&capability.requiresRecoveryAdminLease===true&&capability.trustedOwnerCensus===true&&capability.historyPreserved===true&&capability.automaticReplay===false&&port?.version===1&&['inspect','archive','receipt'].every(key=>typeof (port as any)[key]==='function');
}
/** Native removal and product archival are explicit stages, never one guessed transaction. */
export class WorkspaceCleanups {
 constructor(private port:WorkspaceCleanupPort,private native:Options['nativeAdmin']){}
 private nativeReceipt(job:Job,receipt:Json){
  if(receipt?.commandId!==job.nativeCommandId||receipt?.operation!=='maintenance.workspace.remove')throw Error('Exact original cleanup receipt required');
  if(receipt.state==='refused'&&receipt.executed===false){job.nativeResult=receipt;job.terminalState='refused';return false;}
  const result=receipt.result;
  if(receipt.state!=='succeeded'||result?.workspaceId!==job.preview?.workspaceId||result.previewHash!==job.preview?.previewHash||result.removed!==true||result.historyPreserved!==true||result.canonicalFilesChanged!==0||result.replayed!==false||result.removedBytes!==job.preview?.inventory.bytes)throw Error('Native folder removal is not conclusively settled');
  job.nativeResult={state:'succeeded',commandId:receipt.commandId,operation:receipt.operation};
  return true;
 }
 private archived(job:Job,receipt:Json){
  if(receipt?.commandId!==job.workspaceCleanup?.archiveCommandId||receipt.operation!=='workspace.cleanup.archive'||receipt.state!=='succeeded'||receipt.result?.workspaceId!==job.preview?.workspaceId||receipt.result.archived!==true||receipt.result.historyPreserved!==true||receipt.result.nativeCommandId!==job.nativeCommandId||receipt.result.replayed!==false)throw Error('Workspace archival has no exact completed owner receipt');
  job.workspaceCleanup!.archiveReceipt={commandId:receipt.commandId,operation:receipt.operation,state:receipt.state,result:{workspaceId:receipt.result.workspaceId,archived:true,historyPreserved:true,nativeCommandId:job.nativeCommandId,replayed:false}};
  job.result={workspaceId:job.preview!.workspaceId,removed:true,archived:true,historyPreserved:true,canonicalFilesChanged:0,removedBytes:job.preview!.inventory.bytes,replayed:false};job.terminalState='succeeded';
 }
 async perform(job:Job,changed:()=>void){
  job.nativeLeaseReleased=true;job.workspaceCleanup??={};changed();
  const prepare=job.operation==='recovery.workspace.prepare';
  try{
   const workspaceId=prepare?job.args.workspaceId:job.preview!.workspaceId;
   const selection=await this.port.inspect(workspaceId,job.fence!,job.context);
   if(selection?.workspaceId!==workspaceId||selection.coverage!=='all-configured-owners-held'||typeof selection.path!=='string'||Buffer.byteLength(JSON.stringify(selection))>1024*1024)throw Error('Bounded complete workspace owner census required');
   if(prepare){
    const {product:_product,...nativeSelection}=selection;
    const preview=await this.native('maintenance.workspace.preview',{selection:nativeSelection,reviewed:true},job.context);
    if(!/^[a-f0-9]{64}$/.test(preview?.previewHash)||preview.workspaceId!==workspaceId||preview.confirmationPath!==selection.path||preview.historyPreserved!==true||preview.permanent!==true||preview.includesUncommittedWork!==true||preview.replayed!==false||!Number.isFinite(preview.expiresAt)||!preview.inventory||!['bytes','entries'].every(key=>Number.isSafeInteger(preview.inventory[key])&&preview.inventory[key]>=0))throw Error('Invalid native workspace cleanup review');
    job.preview={kind:'workspace-folder-removal',coverage:'reviewed-workspace-files-only',previewHash:preview.previewHash,workspaceId,confirmationPath:preview.confirmationPath,inventory:{bytes:preview.inventory.bytes,entries:preview.inventory.entries},expiresAt:preview.expiresAt,historyPreserved:true,permanent:true,includesUncommittedWork:true};job.terminalState='prepared';changed();return;
   }
   if(job.args.confirmationPath!==job.preview!.confirmationPath)throw Object.assign(Error('Exact folder confirmation required'),{data:{executed:false}});
   job.workspaceCleanup.selection=structuredClone(selection);changed();
   job.nativeOperation='maintenance.workspace.remove';job.nativeCommandId='recovery:'+hash([job.accountId,job.commandId,'workspace-remove']);changed();
   const {product:_product,...nativeSelection}=selection;
   const result=await this.native(job.nativeOperation,{commandId:job.nativeCommandId,previewHash:job.preview!.previewHash,selection:nativeSelection,confirmationPath:job.args.confirmationPath},job.context);
   if(!this.nativeReceipt(job,result.receipt)){changed();return;}
   // Persist admission before asking the registration owner to archive. A lost
   // response can only be inspected through this same owner command identity.
   job.workspaceCleanup.archiveCommandId='recovery:'+hash([job.accountId,job.commandId,'workspace-archive']);changed();
   const archived=await this.port.archive({commandId:job.workspaceCleanup.archiveCommandId,selection,nativeReceipt:result.receipt},job.fence!,job.context);
   this.archived(job,archived.receipt);changed();
  }catch(error){
   if(prepare||!job.nativeCommandId||refused(error)&&job.nativeResult?.state!=='succeeded'){job.terminalState='refused';job.reason='workspace-cleanup-refused-before-removal';}
   else{job.terminalState=undefined;job.reason='workspace-cleanup-outcome-unknown-no-replay';}
   changed();
  }
 }
 async finish(job:Job,changed:()=>void){
  if(job.operation!=='recovery.workspace.remove'||!job.nativeCommandId||!job.workspaceCleanup?.selection)throw Error('Original workspace removal and retained owner selection required');
  const result=await this.native('maintenance.receipt',{commandId:job.nativeCommandId},job.context);
  if(!this.nativeReceipt(job,result.receipt)){changed();return;}
  job.workspaceCleanup.archiveCommandId??='recovery:'+hash([job.accountId,job.commandId,'workspace-archive']);changed();
  this.archived(job,(await this.port.archive({commandId:job.workspaceCleanup.archiveCommandId,selection:job.workspaceCleanup.selection,nativeReceipt:result.receipt},job.fence!,job.context)).receipt);changed();
 }
 async inspect(job:Job,changed:()=>void){
  if(!job.nativeCommandId)return;
  const result=await this.native('maintenance.receipt',{commandId:job.nativeCommandId},job.context);
  try{
   if(this.nativeReceipt(job,result.receipt)){
    if(!job.workspaceCleanup?.archiveCommandId){job.reason='folder-removed-archival-not-confirmed';changed();return;}
    this.archived(job,(await this.port.receipt(job.workspaceCleanup.archiveCommandId,job.context)).receipt);
   }
   changed();
  }catch{job.terminalState=undefined;job.reason='workspace-cleanup-outcome-unknown-no-replay';changed();}
 }
}
