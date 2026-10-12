// Offline publication reconciliation. No owner execution, replay, or service start.
import {constants} from 'node:fs';
import {open,lstat,realpath,readdir,readFile,rename,rm} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {withOfflineSupervisorSnapshot,token} from '@amplifier/unified-distribution-update-owner';
import {readInstallationConfiguration} from './installation.js';
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const requireProof=(ok)=>{if(!ok)throw Error('recovery_publication_proof_changed');};
const exists=async path=>{try{await lstat(path);return true}catch(e){if(e.code==='ENOENT')return false;throw e}};
async function sync(path){const fd=await open(path,constants.O_RDONLY);try{await fd.sync()}finally{await fd.close()}}
async function save(path,value){const temp=path+'.'+randomUUID();const fd=await open(temp,'wx',0o600);try{await fd.writeFile(typeof value==='string'?value:JSON.stringify(value)+'\n');await fd.sync()}finally{await fd.close()}await rename(temp,path);await sync(dirname(path))}

// Hash exact regular-file bytes and relative structure with bounded work. Ignore
// rename-only metadata; refuse mutation during a read and links to other trees.
const proofBudget=()=>({bytes:0,entries:0,deadline:Date.now()+300000});
export async function recoveryTreeProof(root,budget=proofBudget()){
 const h=createHash('sha256');let bytes=0,entries=0;const deadline=budget.deadline;
 async function visit(path,relative){
  entries++;if(++budget.entries>200000||Date.now()>deadline)throw Error('recovery_proof_bound_exceeded');
  const before=await lstat(path);requireProof(!before.isSymbolicLink()&&await realpath(path)===path);
  h.update(JSON.stringify([relative,before.mode&0o777,before.isDirectory()?'directory':'file'])+'\n');
  if(before.isDirectory()){
   for(const name of (await readdir(path)).sort())await visit(join(path,name),relative+'/'+name);
  }else{
   requireProof(before.isFile()&&before.nlink===1);bytes+=before.size;
   budget.bytes+=before.size;if(budget.bytes>8*1024**3)throw Error('recovery_proof_bound_exceeded');
   const fd=await open(path,constants.O_RDONLY|constants.O_NOFOLLOW),content=createHash('sha256');
   try{const current=await fd.stat();requireProof(current.ino===before.ino&&current.dev===before.dev);let total=0;
    for await(const chunk of fd.createReadStream({autoClose:false})){total+=chunk.length;content.update(chunk);if(total>before.size||Date.now()>deadline)throw Error('recovery_proof_bound_exceeded')}
    requireProof(total===before.size);h.update(JSON.stringify([before.size,content.digest('hex')])+'\n');
   }finally{await fd.close()}
  }
  const after=await lstat(path);requireProof(before.ino===after.ino&&before.dev===after.dev&&before.size===after.size&&before.mtimeMs===after.mtimeMs&&before.ctimeMs===after.ctimeMs);
 }
 const identity=await lstat(root);h.update(JSON.stringify([identity.dev,identity.ino]));
 await visit(root,'');return {sha256:h.digest('hex'),bytes,entries};
}
export async function sealRecoveryPublication({directory,journal,application,restoredApp,nativePaths,freeze,result,requestDigest}){
 const budget=proofBudget(),files={};for(const path of [...new Set([...['installer-input.json','initial-provisioning.json','supervisor-configuration.json'].map(name=>join(directory,name)),...nativePaths])])files[path]=await recoveryTreeProof(path,budget);
 return {schema:'unified-recovery-publication-proof-v1',requestDigest,freeze,result,
  original:await recoveryTreeProof(application.stateDirectory,budget),restored:await recoveryTreeProof(restoredApp,budget),files,
  configuration:{original:sha(await readFile(join(journal,'original-application.json'))),next:sha(await readFile(join(journal,'next-application.json')))},
  restoredApp};
}
async function originalProof(input){
 requireProof(input&&typeof input.directory==='string'&&typeof input.commandId==='string');token(input.commandId);
 const directory=input.directory,journal=join(directory,'recovery-'+input.commandId),info=await lstat(directory);
 requireProof(info.isDirectory()&&!(info.mode&0o077)&&info.uid===process.getuid()&&await realpath(directory)===directory);
 requireProof(await realpath(journal)===journal);
 const record=await readInstallationConfiguration(join(journal,'receipt.json'));
 const proof=await readInstallationConfiguration(join(journal,'publication-proof.json'));
 requireProof(record.commandId===input.commandId&&record.publicationProofSha256===sha(JSON.stringify(proof))&&proof.schema==='unified-recovery-publication-proof-v1'&&record.requestDigest===proof.requestDigest);
 requireProof(proof.result.directory===directory&&proof.result.commandId===input.commandId&&proof.freeze.dataDirectory===join(directory,'supervisor'));
 return {directory,journal,record,proof};
}
async function inspectHeld(state){
 const {directory,journal,record,proof}=state;
 const budget=proofBudget();
 for(const [path,expected]of Object.entries(proof.files))requireProof(JSON.stringify(await recoveryTreeProof(path,budget))===JSON.stringify(expected));
 const original=await readFile(join(journal,'original-application.json')),next=await readFile(join(journal,'next-application-proof.json'));
 requireProof(sha(original)===proof.configuration.original&&sha(next)===proof.configuration.next);
 const app=join(directory,'application'),previous=join(journal,'previous-application'),restored=proof.restoredApp,retained=join(journal,'retained-restored-application');
 requireProof(restored.startsWith(join(journal,'archive','roots')+'/'));
 const layout={};for(const [name,path]of Object.entries({app,previous,restored,retained})){
  if(!await exists(path)){layout[name]=null;continue}
  const value=await recoveryTreeProof(path,budget),digest=value.sha256;
  requireProof(digest===proof.original.sha256||digest===proof.restored.sha256);
  layout[name]=digest;
 }
 requireProof([layout.app,layout.previous].filter(v=>v===proof.original.sha256).length===1);
 requireProof([layout.app,layout.restored,layout.retained].filter(v=>v===proof.restored.sha256).length===1);
 requireProof(layout.previous===null||layout.previous===proof.original.sha256);
 requireProof([layout.restored,layout.retained].every(v=>v===null||v===proof.restored.sha256));
 const config=sha(await readFile(join(directory,'application.json')));
 requireProof([proof.configuration.original,proof.configuration.next].includes(config));
 const marker=join(directory,'RECOVERY-PENDING.json');
 if(await exists(marker)){const pending=await readInstallationConfiguration(marker);requireProof(pending.commandId===record.commandId&&pending.receipt===join(journal,'receipt.json'))}
 else requireProof(['prepared','rolled-back'].includes(record.phase));
 const reviewDigest=sha(JSON.stringify({record,proof,layout,config}));
 return {reviewDigest,layout,config,paths:{app,previous,restored,retained},original,next,marker};
}
export async function inspectInstallationRecovery(input,ports={}){
 const state=await originalProof(input);
 return (ports.withSupervisorSnapshot??withOfflineSupervisorSnapshot)(state.proof.freeze,async()=>{
  const inspection=await inspectHeld(state);return {commandId:input.commandId,phase:state.record.phase,reviewDigest:inspection.reviewDigest,layout:inspection.layout,canComplete:true,canRollback:true,serviceStarted:false,workReplayed:false};
 });
}
export async function reconcileInstallationRecovery(input,ports={}){
 requireProof(['complete','rollback'].includes(input?.decision)&&/^[a-f0-9]{64}$/.test(input.reviewDigest));
 const state=await originalProof(input);
 return (ports.withSupervisorSnapshot??withOfflineSupervisorSnapshot)(state.proof.freeze,async()=>{
  const review=await inspectHeld(state);requireProof(review.reviewDigest===input.reviewDigest);
  const {directory,journal,record,proof}=state,{paths,layout}=review;
  // Retain the same fence marker through every filesystem transition, including
  // a retry after a lost acknowledgement. A review never releases it by itself.
  if(!await exists(review.marker))await save(review.marker,{schema:'unified-installation-recovery-pending-v1',commandId:input.commandId,receipt:join(journal,'receipt.json')});
  if(input.decision==='complete'){
   if(layout.app===proof.original.sha256){await rename(paths.app,paths.previous);await sync(directory);await sync(journal);await ports.publicationCheckpoint?.('reconcile-original-retained')}
   if(layout.app!==proof.restored.sha256){const from=layout.restored?paths.restored:paths.retained;await rename(from,paths.app);await sync(dirname(from));await sync(directory);await ports.publicationCheckpoint?.('reconcile-restored-published')}
   await save(join(directory,'application.json'),review.next.toString());record.phase='prepared';record.result=proof.result;
  }else{
   if(layout.app===proof.restored.sha256){await rename(paths.app,paths.retained);await sync(directory);await sync(journal);await ports.publicationCheckpoint?.('reconcile-restored-retained')}
   if(layout.app!==proof.original.sha256){await rename(paths.previous,paths.app);await sync(directory);await sync(journal);await ports.publicationCheckpoint?.('reconcile-original-published')}
   await save(join(directory,'application.json'),review.original.toString());record.phase='rolled-back';record.result={commandId:input.commandId,rolledBack:true,restoredFilesRetained:true,serviceStarted:false,workReplayed:false};
  }
  await ports.publicationCheckpoint?.('reconcile-configuration-published');
  record.reconciliation={decision:input.decision,reviewDigest:input.reviewDigest};delete record.failure;
  await save(join(journal,'receipt.json'),record);
  await ports.publicationCheckpoint?.('reconciled-receipt');
  await rm(review.marker);await sync(directory);
  return record.result;
 });
}
