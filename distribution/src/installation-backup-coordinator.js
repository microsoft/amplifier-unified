import {constants} from 'node:fs';
import {open,mkdir,rename,realpath,lstat,unlink,readdir} from 'node:fs/promises';
import {isAbsolute,join,dirname,sep} from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {isDeepStrictEqual} from 'node:util';
import {createCoherentInstallationArchive,inspectInstallationArchive} from './installation-archive.js';
import {openInstalledService,readInstalledServiceConfiguration} from './service.js';
import {validateStorageInventory} from './storage-inventory.js';

const id=v=>typeof v==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$/.test(v);
const hash=v=>createHash('sha256').update(JSON.stringify(v)).digest('hex');
const inside=(root,p)=>p===root||p.startsWith(root+sep);
const pending=new Set(['accepted','stopping','capturing','resuming']);
const safeError=e=>/^[a-z_]{1,100}$/.test(e?.message)?e.message:'backup_operation_unconfirmed';
async function privateDirectory(p){
 if(!isAbsolute(p)||await realpath(p)!==p)throw Error('backup_private_directory_required');
 const s=await lstat(p);if(!s.isDirectory()||s.isSymbolicLink()||s.mode&0o077)throw Error('backup_private_directory_required');
}
async function syncDirectory(p){const h=await open(p,constants.O_RDONLY);try{await h.sync();}finally{await h.close();}}
async function writeOnce(p,value){
 const h=await open(p,'wx',0o600);try{await h.writeFile(JSON.stringify(value)+'\n');await h.sync();}finally{await h.close();}
 await syncDirectory(dirname(p));
}
async function read(p){
 const h=await open(p,constants.O_RDONLY|constants.O_NOFOLLOW);
 try{const s=await h.stat();if(!s.isFile()||s.nlink!==1||s.size>1048576||s.mode&0o077)throw Error('backup_private_record_required');return JSON.parse(await h.readFile('utf8'));}finally{await h.close();}
}

/** Lives in the installation controller, outside the replaceable app. The
 * callbacks are local composition authority, never browser-supplied adapters.
 * A durable submission precedes stopping. Reopening/reading never resumes an
 * interrupted operation, captures again, or starts a stopped installation. */
export async function createInstallationBackupCoordinator({directory,stateDirectory,outlivesDistribution,
 getSupervisor,replaceSupervisor,readCompositionInventory,acquireWriterExclusion,nativeCaptures,
 capture=createCoherentInstallationArchive,inspectArchive=inspectInstallationArchive,
 reopen=openInstalledService,readConfiguration=readInstalledServiceConfiguration,now=Date.now,schedule=setImmediate}){
 if(outlivesDistribution!==true||[getSupervisor,replaceSupervisor,readCompositionInventory,acquireWriterExclusion].some(f=>typeof f!=='function')||!Array.isArray(nativeCaptures))throw Error('external_backup_controller_required');
 await privateDirectory(directory);await privateDirectory(stateDirectory);
 if(inside(directory,stateDirectory)||inside(stateDirectory,directory))throw Error('backup_state_must_be_separate');
 for(const name of ['reviews','commands']){await mkdir(join(stateDirectory,name),{recursive:true,mode:0o700});await privateDirectory(join(stateDirectory,name));}
 const running=new Map(),listeners=new Set();let closed=false;
 const activeFile=join(stateDirectory,'active.json'),commandPath=commandId=>join(stateDirectory,'commands',commandId);
 const notify=()=>{for(const f of listeners)try{f();}catch{}};
 async function boundInventory(expected){
  const saved=await readConfiguration(directory),c=saved.configuration,
   inventory=validateStorageInventory(await readCompositionInventory());
  if(expected?.installationId!==c.serviceLifecycle?.installationId||expected?.ownerId!==c.serviceLifecycle?.ownerId||expected?.dataScope!==c.dataScope||
   inventory.namespace!==c.dataScope||inventory.applicationStateDirectory!==join(directory,'application'))throw Error('backup_installation_binding_conflict');
  if(inventory.nativeArtifacts.length||inventory.owners.some(o=>o.externalStorage==='unresolved')||inventory.omissions.some(o=>o.blocksComplete)||
   inventory.roots.some(r=>r.coverage==='authoritative'&&r.capture==='omit'||inside(r.path,stateDirectory)||inside(stateDirectory,r.path))||
   (inventory.nativeCapturePlans??[]).length!==nativeCaptures.length||nativeCaptures.some(a=>!(inventory.nativeCapturePlans??[]).some(p=>p.engineId===a.engineId)))throw Error('backup_complete_scope_required');
  return inventory;
 }
 async function active(){try{return await read(activeFile);}catch(e){if(e.code==='ENOENT')return null;throw e;}}
 async function saved(commandId){if(!id(commandId))throw Error('backup_command_invalid');try{return await read(join(commandPath(commandId),'receipt.json'));}catch(e){if(e.code==='ENOENT')return null;throw e;}}
 async function persist(value){
  const path=join(commandPath(value.commandId),'receipt.json'),tmp=path+'.'+randomUUID();
  await writeOnce(tmp,value);await rename(tmp,path);await syncDirectory(dirname(path));notify();
 }
 const publicReceipt=v=>v?{...v,...(pending.has(v.status)&&!running.has(v.commandId)?{status:'unknown',error:'backup_controller_interrupted'}:{}),workReplayed:false}:null;
 async function review(){
  if(closed)throw Error('backup_controller_closed');
  if(await active())throw Error('backup_operation_pending');
  const supervisor=getSupervisor(),state=await supervisor?.service?.inspect();
  if(state?.state!=='running')throw Error('backup_running_installation_required');
  const inventory=await boundInventory(state.identity);
  const value={schema:'unified-installation-backup-review-v1',id:randomUUID(),createdAt:now(),expiresAt:now()+600000,
   expected:state.identity,inventoryDigest:inventory.digest,owners:inventory.owners.map(o=>o.id).sort(),
   includesCredentials:true,encrypted:false,temporaryServiceStop:true,workReplayed:false};
  value.digest=hash(value);await writeOnce(join(stateDirectory,'reviews',value.id+'.json'),value);return value;
 }
 async function releaseActive(commandId){const v=await active();if(v?.commandId===commandId){await unlink(activeFile);await syncDirectory(stateDirectory);}}
 async function run(commandId,reviewed){
  let receipt=await saved(commandId),effect=false;
  try{
   const supervisor=getSupervisor(),observed=await supervisor?.service?.inspect(),inventory=await boundInventory(observed?.identity);
   if(observed?.state!=='running'||!isDeepStrictEqual(observed.identity,reviewed.expected)||inventory.digest!==reviewed.inventoryDigest||now()>reviewed.expiresAt)throw Error('backup_review_stale');
   receipt={...receipt,status:'stopping'};await persist(receipt);effect=true;
   await supervisor.service.stopForMaintenance({commandId:receipt.stopCommandId,expected:reviewed.expected});
   const stopped=await supervisor.service.waitFor(receipt.stopCommandId);
   if(stopped?.commandId===receipt.stopCommandId&&isDeepStrictEqual(stopped.expected,reviewed.expected)&&stopped.status==='refused'&&stopped.noEffect===true&&(!stopped.fenceId||stopped.admissionSettlement?.state==='settled')){
    effect=false;receipt={...receipt,stopped};throw Error('backup_service_busy');
   }
   if(stopped?.commandId!==receipt.stopCommandId||stopped.status!=='stopped'||stopped.maintenanceStop!==true||!isDeepStrictEqual(stopped.expected,reviewed.expected))throw Error('backup_stop_unconfirmed');
   await supervisor.close();replaceSupervisor(null);
   receipt={...receipt,status:'capturing',stopped};await persist(receipt);
   const archive=await capture({directory,compositionInventory:inventory,inventoryDigest:inventory.digest,
    expected:reviewed.expected,stoppedCommandId:receipt.stopCommandId,outputFile:join(commandPath(commandId),'backup.unified'),
    privateContentReviewed:true,includeCredentials:true,credentialsReviewed:true,requireCompleteProduct:true},
    {acquireWriterExclusion,nativeCaptures});
   if(archive.completeProduct!==true||archive.captureRelease?.state!=='released'||archive.captureOutcomeRecorded!==true)throw Error('backup_capture_unconfirmed');
   receipt={...receipt,status:'resuming',archive};await persist(receipt);
   const next=await reopen(directory);replaceSupervisor(next);
   await next.service.resume({commandId:receipt.resumeCommandId,expected:reviewed.expected,stoppedCommandId:receipt.stopCommandId});
   const resumed=await next.service.waitFor(receipt.resumeCommandId);
   if(resumed?.commandId!==receipt.resumeCommandId||resumed.status!=='ready'||resumed.admissionSettlement?.state!=='settled'||!isDeepStrictEqual(resumed.expected,reviewed.expected))throw Error('backup_resume_unconfirmed');
   receipt={...receipt,status:'succeeded',resumed,completedAt:now()};await persist(receipt);await releaseActive(commandId);
  }catch(error){
   receipt={...receipt,status:effect?'unknown':'failed',error:safeError(error),updatedAt:now()};await persist(receipt);
   if(!effect)await releaseActive(commandId);
  }
  return receipt;
 }
 async function submit(commandId,args){
  if(closed)throw Error('backup_controller_closed');
  if(!id(commandId)||!args||Object.keys(args).sort().join(',')!=='credentialsReviewed,privateContentReviewed,reviewDigest,reviewId'||
    !id(args.reviewId)||args.privateContentReviewed!==true||args.credentialsReviewed!==true||!/^[a-f0-9]{64}$/.test(args.reviewDigest))throw Error('backup_review_required');
  const requestDigest=hash({reviewId:args.reviewId,reviewDigest:args.reviewDigest,privateContentReviewed:true,credentialsReviewed:true});
  const prior=await saved(commandId);
  if(prior){if(prior.requestDigest!==requestDigest)throw Error('backup_command_conflict');return publicReceipt(prior);}
  const reviewed=await read(join(stateDirectory,'reviews',args.reviewId+'.json'));
  const {digest,...body}=reviewed;
  if(digest!==args.reviewDigest||hash(body)!==digest||now()>reviewed.expiresAt)throw Error('backup_review_stale');
  await writeOnce(activeFile,{schema:'unified-installation-backup-active-v1',commandId,requestDigest});
  // A crash after exclusive admission deliberately retains that admission.
  // No other command can silently supersede an uncertain original request.
  await mkdir(commandPath(commandId),{mode:0o700});
  const value={schema:'unified-installation-backup-receipt-v1',commandId,requestDigest,reviewId:reviewed.id,
   expected:reviewed.expected,inventoryDigest:reviewed.inventoryDigest,status:'accepted',createdAt:now(),
   stopCommandId:'backup-stop-'+commandId,resumeCommandId:'backup-resume-'+commandId,workReplayed:false};
  await persist(value);
  let complete;const settled=new Promise(resolve=>{complete=resolve;});running.set(commandId,settled);
  schedule(()=>{void run(commandId,reviewed).then(complete,complete).finally(()=>{running.delete(commandId);notify();});});
  return value;
 }
 async function inspect(){
  const names=await readdir(join(stateDirectory,'commands'));if(names.length>10000)throw Error('backup_receipt_inventory_limit');
  const receipts=[];for(const name of names)if(id(name)){const value=await saved(name);if(value)receipts.push(publicReceipt(value));}
  receipts.sort((a,b)=>b.createdAt-a.createdAt||b.commandId.localeCompare(a.commandId));
  return {available:true,active:await active(),receipts:receipts.slice(0,50),includesCredentials:true,encrypted:false,workReplayed:false};
 }
 // Observation can recognize an already-completed resume whose acknowledgement
 // was lost. It never issues capture, stop, resume, or a replacement command.
 async function reconcile(commandId){
  if(running.has(commandId))return publicReceipt(await saved(commandId));
  const value=await saved(commandId);if(!value||['failed','succeeded'].includes(value.status))return value;
  let archive,outcome;
  try{
   archive=await inspectArchive(join(commandPath(commandId),'backup.unified'));
   outcome=await read(join(commandPath(commandId),'backup.unified.capture.json'));
  }catch(error){if(error.code==='ENOENT')return publicReceipt(value);throw error;}
  const resumed=await getSupervisor()?.service?.receipt(value.resumeCommandId);
  if(archive.manifest?.coverage?.completeProduct===true&&outcome.sealed===true&&outcome.release?.state==='released'&&
   outcome.archiveSha256===archive.archiveSha256&&outcome.manifestDigest===archive.manifestDigest&&
   isDeepStrictEqual(archive.manifest.proof?.expected,value.expected)&&archive.manifest.proof?.stoppedCommandId===value.stopCommandId&&
   resumed?.status==='ready'&&resumed.admissionSettlement?.state==='settled'&&isDeepStrictEqual(resumed.expected,value.expected)){
    const result={...value,status:'succeeded',archive:{...(value.archive??{}),archiveSha256:archive.archiveSha256,manifestDigest:archive.manifestDigest,completeProduct:true},resumed,reconciledAt:now(),workReplayed:false};
    await persist(result);await releaseActive(commandId);return result;
  }
  return publicReceipt(value);
 }
 async function readArchive(commandId,{sha256,offset=0,maxBytes=262144}={}){
  const value=await saved(commandId),archive=value?.archive;
  if(value?.status!=='succeeded'||archive?.completeProduct!==true||sha256!==archive.archiveSha256||!Number.isSafeInteger(offset)||offset<0||!Number.isSafeInteger(maxBytes)||maxBytes<1||maxBytes>262144)throw Error('backup_archive_read_invalid');
  const h=await open(join(commandPath(commandId),'backup.unified'),constants.O_RDONLY|constants.O_NOFOLLOW);
  try{
   const before=await h.stat();if(!before.isFile()||before.nlink!==1||before.mode&0o077||before.size!==archive.bytes||offset>before.size)throw Error('backup_archive_changed');
   const bytes=Buffer.alloc(Math.min(maxBytes,before.size-offset));let position=0;
   while(position<bytes.length){const chunk=await h.read(bytes,position,bytes.length-position,offset+position);if(!chunk.bytesRead)throw Error('backup_archive_changed');position+=chunk.bytesRead;}
   const after=await h.stat();if(before.size!==after.size||before.mtimeMs!==after.mtimeMs||before.ctimeMs!==after.ctimeMs)throw Error('backup_archive_changed');
   return {commandId,sha256,bytes:before.size,offset,encoding:'base64',data:bytes.toString('base64'),chunkSha256:createHash('sha256').update(bytes).digest('hex')};
  }finally{await h.close();}
 }
 return {outlivesDistribution:true,review,submit,inspect,reconcile,receipt:async commandId=>publicReceipt(await saved(commandId)),
  readArchive,
  subscribe:f=>{listeners.add(f);return()=>listeners.delete(f);},
  waitFor:commandId=>running.get(commandId)??saved(commandId).then(publicReceipt),
  async close(){if(running.size)throw Error('backup_operation_pending');closed=true;listeners.clear();}};
}
