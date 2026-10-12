import {createHash} from 'node:crypto';
import type {Job,Json,Options} from './types.js';
export const backupCoverage='completed-native-private-archives-only';
const hash=(value:unknown)=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const validHash=(value:unknown)=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
export function backupAvailable(native:Json|undefined){const c=native?.version===1?native.backupCleanup:undefined;return c?.version===1&&c.previewRequired===true&&c.receipt===true&&c.requiresRecoveryAdminLease===true&&c.newestProtected===true&&c.recoveryReferencesProtected===true&&c.automaticReplay===false;}
export class BackupCleanups {
 constructor(private native:Options['nativeAdmin']){}
 private settled(job:Job,receipt:Json){
  if(receipt?.commandId!==job.nativeCommandId||receipt?.operation!=='maintenance.backups.remove')throw Error('Exact original backup cleanup receipt required');
  if(receipt.state==='refused'&&receipt.executed===false){job.nativeResult=receipt;job.terminalState='refused';return;}
  const r=receipt.result;
  if(receipt.state!=='succeeded'||r?.artifactId!==job.preview?.artifact.artifactId||r.previewHash!==job.preview?.previewHash||r.removed!==true||r.removedBytes!==job.preview?.inventory.bytes||r.newestProtected!==true||r.recoveryReferencesProtected!==true||r.canonicalFilesChanged!==0||r.replayed!==false||r.coverage!==backupCoverage)throw Error('Backup removal is not conclusively settled');
  job.nativeResult={state:'succeeded',commandId:receipt.commandId,operation:receipt.operation};job.result=structuredClone(r);job.terminalState='succeeded';
 }
 async perform(job:Job,changed:()=>void){
  job.nativeLeaseReleased=true;changed();
  const remove=job.operation==='recovery.backups.remove';
  try{
   if(!remove){
    const inspect=job.operation==='recovery.backups.inspect';
    const result=await this.native(inspect?'maintenance.backups.inspect':'maintenance.backups.preview',inspect?{}:{artifactId:job.args.artifactId,reviewed:true},job.context);
    if(Buffer.byteLength(JSON.stringify(result))>1024*1024)throw Error('Backup review exceeds its response limit');
    if(result.coverage!==backupCoverage)throw Error('Invalid backup catalog coverage');
    if(inspect){
     if(result.version!==1||!Array.isArray(result.items)||result.items.length>1000||result.newestProtected!==true||result.recoveryReferencesProtected!==true)throw Error('Invalid backup catalog');
     for(const row of result.items)if(!/^[a-f0-9]{32}$/.test(row.artifactId)||!validHash(row.sha256)||!Number.isSafeInteger(row.bytes)||row.bytes<0||typeof row.removable!=='boolean')throw Error('Invalid completed backup identity');
     job.result=structuredClone(result);job.terminalState='succeeded';
    }else{
     if(result.kind!=='private-backup-removal'||!validHash(result.previewHash)||result.artifact?.artifactId!==job.args.artifactId||result.artifact.removable!==true||result.permanent!==true||!Number.isFinite(result.expiresAt)||!Number.isSafeInteger(result.inventory?.bytes)||result.inventory.bytes<0)throw Error('Invalid backup removal review');
     job.preview=structuredClone(result);job.terminalState='prepared';
    }
   }else{
    job.nativeOperation='maintenance.backups.remove';job.nativeCommandId='recovery:'+hash([job.accountId,job.commandId,'backup-remove']);changed();
    const result=await this.native(job.nativeOperation,{commandId:job.nativeCommandId,previewHash:job.preview!.previewHash,confirmationId:job.args.confirmationId},job.context);
    this.settled(job,result.receipt);
   }
  }catch(error:any){
   if(!remove||error?.data?.executed===false){job.terminalState='refused';job.reason='backup-cleanup-refused-before-removal';}
   else{job.terminalState=undefined;job.reason='backup-cleanup-outcome-unknown-no-replay';}
  }
  changed();
 }
 async inspect(job:Job,changed:()=>void){
  if(!job.nativeCommandId)return;
  const result=await this.native('maintenance.receipt',{commandId:job.nativeCommandId},job.context);
  try{this.settled(job,result.receipt);}catch{job.terminalState=undefined;job.reason='backup-cleanup-outcome-unknown-no-replay';}
  changed();
 }
}
