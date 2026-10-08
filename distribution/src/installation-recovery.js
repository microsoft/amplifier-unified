// Local, stopped-installation recovery. Retained archive content never supplies
// executable launch configuration. The existing trusted installation does.
import {constants} from 'node:fs';
import {open,lstat,realpath,mkdir,readFile,writeFile,rename,rm} from 'node:fs/promises';
import {join,dirname,isAbsolute,resolve} from 'node:path';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify,isDeepStrictEqual} from 'node:util';
import {AdminConnection} from '@amplifier/unified-native-capabilities';
import {withOfflineSupervisorSnapshot,token} from '@amplifier/unified-distribution-update-owner';
import {readInstalledServiceConfiguration} from './service.js';
import {readInstallationConfiguration} from './installation.js';
import {inspectInstallationArchive,restoreInstallationArchive} from './installation-archive.js';
import {sealRecoveryPublication} from './installation-reconciliation.js';
import {assertInstallationRecoverySettled} from './installation-recovery-guard.js';

const execute=promisify(execFile),hash=v=>createHash('sha256').update(v).digest('hex');
const sorted=v=>Array.isArray(v)?v.map(sorted):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,sorted(v[k])])):v;
const fingerprint=v=>hash(JSON.stringify(sorted(v)));
const inside=(parent,p)=>p===parent||p.startsWith(parent+'/');
const required=(condition,code)=>{if(!condition)throw Error(code);};
async function sync(path){const fd=await open(path,constants.O_RDONLY);try{await fd.sync();}finally{await fd.close();}}
async function writeOnce(path,bytes){const fd=await open(path,'wx',0o600);try{await fd.writeFile(bytes);await fd.sync();}finally{await fd.close();}await sync(dirname(path));}
async function privateDirectory(path){
 required(isAbsolute(path)&&resolve(path)===path&&await realpath(path)===path,'recovery_path_invalid');
 const s=await lstat(path);required(s.isDirectory()&&!(s.mode&0o077)&&s.uid===process.getuid(),'private_recovery_directory_required');
}
function rebind(value,paths){
 if(typeof value==='string'){for(const [from,to]of paths)if(inside(from,value))return to+value.slice(from.length);return value;}
 if(Array.isArray(value))return value.map(v=>rebind(v,paths));
 return value&&typeof value==='object'?Object.fromEntries(Object.entries(value).map(([k,v])=>[k,rebind(v,paths)])):value;
}

/** Native's finalized receipt is the only source of restored native bindings. */
async function restoreNative({engine,cwd,context,descriptor,destination}){
 const admin=new AdminConnection({...engine,cwd,resolveWorkspace:()=>cwd,timeoutMs:1200000,initializationTimeoutMs:10000});
 let lease;
 try{
  lease=await admin.quiescenceParticipant.acquire(context);required(lease,'recovery_native_fence_refused');
  return await admin.withMaintenanceFence(context,async()=>{
   const call=(operation,args)=>admin.perform(operation,args,{clientId:'installation-recovery',origin:'ui',workingDirectory:cwd});
   const preview=await call('maintenance.restore.preview',{artifactId:descriptor.artifactId,sha256:descriptor.sha256,destination,privateContentReviewed:true,credentialsReviewed:true});
   required(preview.startsWorker===false&&preview.replaysInputs===false,'recovery_native_preview_invalid');
   const commandId=context.commandId+'-native';
   const result=await call('maintenance.restore.apply',{commandId,previewHash:preview.previewHash});
   const inspection=await call('maintenance.restore.inspect',{restoreCommandId:commandId});
   required(result.receipt?.state==='succeeded'&&inspection.finalized===true&&inspection.pending===false&&
    isDeepStrictEqual(inspection.receipt?.result,result.receipt.result),'recovery_native_outcome_unconfirmed');
   return {result:result.receipt.result,inspection};
  });
 }finally{
  try{if(lease)await lease.release('unchanged',{verified:true,...context,outcome:'unchanged',receiptId:context.commandId+'-native-closed'});}
  finally{await admin.close();}
 }
}
async function projectCatalog({catalog,db,requestFile,request}){
 required(catalog&&catalog.args?.some((v,i,a)=>v==='-m'&&a[i+1]==='amplifier_session_catalog'),'recovery_catalog_adapter_required');
 await writeOnce(requestFile,JSON.stringify(request)+'\n');
 const result=await execute(catalog.command,['-I','-B','-m','amplifier_session_catalog.restore','--db',db,'--request',requestFile],
  {env:{...process.env,...catalog.env,PYTHONDONTWRITEBYTECODE:'1'},timeout:120000,maxBuffer:1048576});
 const receipt=JSON.parse(result.stdout);
 required(receipt.restoreDigest===request.restore_digest&&receipt.nativeFilesModified===false&&Number.isSafeInteger(receipt.rebound),'recovery_catalog_projection_unconfirmed');
 return receipt;
}

/** Prepare recovery from this installation's exact stopped, complete archive.
 * Application roots return to their original path; managed files, drafts and
 * links therefore retain their locations. The previous application tree and
 * original native homes remain untouched in a private recovery directory.
 * This does not resume the service or submit any conversation input.
 * Adapters are trusted in-process dependencies, never serialized in requests. */
export async function prepareInstallationRecovery(request,ports={}){
 const fields=['schema','directory','commandId','archiveFile','archiveSha256','manifestDigest','expected','stoppedCommandId','native','privateContentReviewed','credentialsReviewed','writerRetirementReviewDigest'];
 required(request&&Object.keys(request).sort().join(',')===fields.sort().join(',')&&request.schema==='unified-installation-recovery-v1','recovery_request_invalid');
 token(request.commandId);required(request.commandId.length<=100,'recovery_request_invalid');
 required(request.privateContentReviewed===true&&request.credentialsReviewed===true&&
  [request.archiveSha256,request.manifestDigest,request.writerRetirementReviewDigest].every(v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v)),'recovery_review_required');
 await privateDirectory(request.directory);
 const journal=join(request.directory,'recovery-'+request.commandId),recordPath=join(journal,'receipt.json'),signature=fingerprint(request);
 try{
  await lstat(journal);const previous=await readInstallationConfiguration(recordPath);
  required(previous.requestDigest===signature,'recovery_command_conflict');
  required(previous.phase==='prepared'&&previous.result,'recovery_outcome_requires_inspection');
  await assertInstallationRecoverySettled(request.directory);
  return {...previous.result,previouslyPrepared:true};
 }catch(e){if(e.code!=='ENOENT')throw e;}
 const saved=await readInstalledServiceConfiguration(request.directory);
 const originalFile=join(request.directory,'application.json'),original=await readFile(originalFile),application=await readInstallationConfiguration(originalFile);
 const n=request.native;
 required(n&&Object.keys(n).sort().join(',')==='configurationFile,cwd,destination,engineId','recovery_native_request_invalid');
 const engine=application.engines?.find(e=>e.id===n.engineId);
 required(engine&&application.engines.length===1&&application.nativeAdmin?.engine===n.engineId&&
  engine.args?.some((v,i,a)=>v==='--config'&&a[i+1]===n.configurationFile),'recovery_native_binding_invalid');
 const source=await readInstallationConfiguration(n.configurationFile);
 required(source.home&&source.appHome,'recovery_native_binding_invalid');
 const review=await (ports.inspectArchive??inspectInstallationArchive)(request.archiveFile),manifest=review.manifest;
 required(review.archiveSha256===request.archiveSha256&&review.manifestDigest===request.manifestDigest,'recovery_archive_review_changed');
 required(manifest.coverage.completeProduct===true&&manifest.captureConsistency?.status==='qualified-at-capture','recovery_complete_archive_required');
 required(isDeepStrictEqual(manifest.proof.expected,request.expected)&&manifest.proof.stoppedCommandId===request.stoppedCommandId&&
  manifest.inventory.applicationStateDirectory===application.stateDirectory&&manifest.inventory.account===application.account&&
  manifest.inventory.namespace===saved.configuration.dataScope,'recovery_installation_binding_invalid');
 const appRoot=manifest.inventory.roots.find(r=>r.path===application.stateDirectory&&r.capture==='tree'&&r.coverage==='authoritative');
 const descriptor=manifest.inventory.nativeArtifacts.find(d=>d.engineId===n.engineId);
 required(appRoot&&descriptor&&manifest.inventory.nativeArtifacts.length===1,'recovery_owner_scope_invalid');
 // Other installer/supervisor files are retained as evidence, never overwritten.
 // Arbitrary additional external authoritative trees need their own owner adapter.
 required(manifest.inventory.roots.every(r=>r.capture==='omit'||r.capture==='native-artifact'||r===appRoot||
  r.id.startsWith('installed:')&&inside(request.directory,r.path)),'recovery_external_owner_required');
 const context={fenceId:request.commandId,commandId:request.commandId,purpose:'recovery',instanceId:request.expected.instanceId,dataScope:request.expected.dataScope};
 const freeze=ports.withSupervisorSnapshot??withOfflineSupervisorSnapshot;
 return freeze({dataDirectory:saved.configuration.dataDirectory,inventoryDigest:manifest.inventory.digest,
  expected:request.expected,stoppedCommandId:request.stoppedCommandId,participantIds:[...new Set(manifest.inventory.owners.map(o=>o.participantId))]},async()=>{
  required(original.equals(await readFile(originalFile)),'recovery_configuration_changed');
  await mkdir(journal,{mode:0o700});await sync(request.directory);
  let record={schema:'unified-installation-recovery-receipt-v1',requestDigest:signature,commandId:request.commandId,
   phase:'preparing',writerRetirementReviewDigest:request.writerRetirementReviewDigest,workReplayed:false};
  const save=async phase=>{record.phase=phase;const temp=recordPath+'.next';await writeOnce(temp,JSON.stringify(record)+'\n');await rename(temp,recordPath);await sync(journal);};
  await save('preparing');
  await writeOnce(join(journal,'original-application.json'),original);
  await writeOnce(join(journal,'original-native.json'),JSON.stringify(source)+'\n');
  try{
   const destination=join(journal,'archive');
   await (ports.restoreArchive??restoreInstallationArchive)({...request,destination});
   record.native=await (ports.restoreNative??restoreNative)({engine,cwd:n.cwd,context,descriptor,destination:n.destination});
   await save('native-restored');
   const native=record.native.result;
   required(native?.restored===true&&native.artifactId===descriptor.artifactId&&native.sha256===descriptor.sha256&&
    native.startsWorker===false&&native.replaysInputs===false,'recovery_native_outcome_unconfirmed');
   const launcher=await readInstallationConfiguration(native.launcherPath),environment=await readInstallationConfiguration(native.environmentPath);
   const newNativeFile=join(journal,'native.json');
   const bindings=[[source.home,launcher.home],[source.appHome,launcher.appHome],[n.configurationFile,newNativeFile]].sort((a,b)=>b[0].length-a[0].length);
   required(bindings.every(([a,b])=>typeof a==='string'&&typeof b==='string'&&isAbsolute(a)&&isAbsolute(b)&&a!==b),'recovery_native_binding_invalid');
   const next=rebind(application,bindings),nativeConfig={...rebind(source,bindings),...launcher};
   nativeConfig.maintenanceFullNativeRoots={...nativeConfig.maintenanceFullNativeRoots,
    checkpoint:environment.AMPLIFIER_SESSION_STATE_HOME,events:environment.AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH,
    sources:environment.AMPLIFIER_SOURCE_STORE,...(launcher.bundle?{bundle:launcher.bundle}:{}),
    ...(launcher.registryHome?{registry:launcher.registryHome}:{}),...(launcher.runtimeManifest?{runtimeManifest:launcher.runtimeManifest}:{})};
   next.engines[0].env={...next.engines[0].env,...environment,AMPLIFIER_HOME:launcher.home,AMPLIFIER_WEB_HOME:launcher.appHome};
   await writeOnce(newNativeFile,JSON.stringify(nativeConfig)+'\n');
   const restoredApp=join(destination,'roots',appRoot.id),catalogArgs=application.catalogProcess?.args;
   const catalogDB=catalogArgs?.[catalogArgs.indexOf('--db')+1];
   required(typeof catalogDB==='string'&&inside(application.stateDirectory,catalogDB),'recovery_catalog_binding_invalid');
   record.catalog=await (ports.projectCatalog??projectCatalog)({catalog:application.catalogProcess,
    db:restoredApp+catalogDB.slice(application.stateDirectory.length),requestFile:join(journal,'catalog-request.json'),
    request:{command_id:request.commandId,restore_digest:fingerprint(record.native.inspection),source_home:source.home,destination_home:launcher.home}});
   const nextBytes=Buffer.from(JSON.stringify(next)+'\n'),nextFile=join(journal,'next-application.json');
   await writeOnce(nextFile,nextBytes);
   required(original.equals(await readFile(originalFile)),'recovery_configuration_changed');
   record.result={prepared:true,commandId:request.commandId,receipt:recordPath,directory:request.directory,
    preservedApplication:join(journal,'previous-application'),preservedNativeHome:source.home,
    restoredNativeHome:launcher.home,catalog:record.catalog,configurationSha256:hash(nextBytes),
    stoppedCommandId:request.stoppedCommandId,expected:request.expected,serviceStarted:false,workReplayed:false};
   const proof=await sealRecoveryPublication({directory:request.directory,journal,application,restoredApp,
    nativePaths:[n.configurationFile,source.home,source.appHome,newNativeFile,native.launcherPath,native.environmentPath,...new Set([launcher.home,launcher.appHome,...Object.values(nativeConfig.maintenanceFullNativeRoots||{}).filter(v=>typeof v==='string')])],
    freeze:{dataDirectory:saved.configuration.dataDirectory,inventoryDigest:manifest.inventory.digest,expected:request.expected,stoppedCommandId:request.stoppedCommandId,participantIds:[...new Set(manifest.inventory.owners.map(o=>o.participantId))]},result:record.result,requestDigest:signature});
   await writeOnce(join(journal,'next-application-proof.json'),nextBytes);
   await writeOnce(join(journal,'publication-proof.json'),JSON.stringify(proof)+'\n');
   record.publicationProofSha256=hash(JSON.stringify(proof));
   await save('prepared-files');
   const pending=join(request.directory,'RECOVERY-PENDING.json');
   await writeOnce(pending,JSON.stringify({schema:'unified-installation-recovery-pending-v1',commandId:request.commandId,receipt:recordPath})+'\n');
   await save('publishing');
   await ports.publicationCheckpoint?.('before-publication');
   await rename(application.stateDirectory,join(journal,'previous-application'));
   await sync(request.directory);await sync(journal);
   await ports.publicationCheckpoint?.('original-retained');
   await rename(restoredApp,application.stateDirectory);
   await sync(dirname(restoredApp));await sync(request.directory);
   await ports.publicationCheckpoint?.('restored-published');
   await rename(nextFile,originalFile);await sync(request.directory);await sync(journal);
   await ports.publicationCheckpoint?.('configuration-published');
   await save('prepared');
   await rm(pending);await sync(request.directory);
   return record.result;
  }catch(error){record.failure='Inspect this original receipt; no automatic recovery or work replay.';await save(record.phase==='publishing'?'publication-unknown':'preparation-unconfirmed').catch(()=>{});throw error;}
 });
}
