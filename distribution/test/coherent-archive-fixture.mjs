import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {readFile,writeFile,mkdir,open,rm,lstat,copyFile} from 'node:fs/promises';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
const hash=v=>createHash('sha256').update(v).digest('hex');

// A private fixture connection to an independently installed native process.
// No source import/overlay, session creation, or provider request is possible.
function connect(python,config,env){
 const child=spawn(python,['-I','-m','amplifier_acp','--config',config],{env:{...process.env,...env},stdio:['pipe','pipe','pipe']});
 const pending=new Map();let next=0,stderr='';
 child.stderr.on('data',b=>stderr+=b);
 const ended=new Promise(resolve=>child.once('exit',(code,signal)=>{for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('Native fixture exited: '+stderr));}pending.clear();resolve({code,signal});}));
 const lines=createInterface({input:child.stdout});lines.on('line',line=>{
  const row=JSON.parse(line),p=pending.get(row.id);if(!p)return;
  pending.delete(row.id);clearTimeout(p.timer);row.error?p.reject(Error(JSON.stringify(row.error))):p.resolve(row.result);
 });
 return {request:(method,params)=>new Promise((resolve,reject)=>{
  const id=++next,timer=setTimeout(()=>{pending.delete(id);reject(Error('Native fixture RPC timed out'));},60000);
  pending.set(id,{resolve,reject,timer});child.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');
 }),close:async()=>{child.stdin.end();const result=await ended;lines.close();assert.equal(result.code,0,stderr);}};
}

export async function qualifyCoherentArchive({packageRoot,root,directory,base,expected,python,nativeConfig,workspace,nativeEnv,evidenceDirectory}){
 const {createCoherentInstallationArchive,createInstallationArchive,inspectInstallationArchive,restoreInstallationArchive}=await import(pathToFileURL(join(packageRoot,'src/installation-archive.js')));
 const {createNativeCoherentCaptureAdapter}=await import(pathToFileURL(join(packageRoot,'src/native-capture.js')));
 const {createStorageInventory,validateStorageInventory}=await import(pathToFileURL(join(packageRoot,'src/storage-inventory.js')));
 const {createInstalledStorageInventory}=await import(pathToFileURL(join(packageRoot,'src/installed-storage-inventory.js')));
 const config=JSON.parse(await readFile(nativeConfig,'utf8'));
 config.workerCommand=['/impossible/coherent-archive-worker'];
 const controlConfig=join(root,'native-control.json');await writeFile(controlConfig,JSON.stringify(config),{mode:0o600});
 const staging=join(root,'native-staged');await mkdir(staging,{mode:0o700});
 const roots={'native-home':config.home,'app-home':config.appHome,...config.maintenanceFullNativeRoots,'workspace-settings':join(workspace,'.amplifier')};
 const owners=structuredClone(base.owners),nativeOwner=owners.find(o=>o.id==='native-administration');nativeOwner.externalStorage='declared';
 const externalRoots=Object.entries(roots).map(([id,path])=>({id,ownerIds:[nativeOwner.id],path,coverage:'authoritative',capture:'native-artifact'}));
 nativeOwner.rootIds.push(...externalRoots.map(r=>r.id));

 const composition=createStorageInventory({...base,owners,roots:[...base.roots,...externalRoots],nativeArtifacts:[],omissions:[]});
 const outputFile=join(root,'coherent.unified'),request={directory,compositionInventory:composition,inventoryDigest:composition.digest,
  expected,stoppedCommandId:'cleanup',outputFile,privateContentReviewed:true,includeCredentials:true,credentialsReviewed:true,requireCompleteProduct:true};
 let retirementActive=false,nativeRelease=false;
 const acquireWriterExclusion=async context=>{
  const lock=join(root,'fixture-retirement.lock'),handle=await open(lock,'wx',0o600);retirementActive=true;
  // The test created every process/root and already proved owned application
  // exit. This explicit fixture retirement assumption is not an OS proof that
  // arbitrary production editors or external native tools are stopped.
  return {evidence:{policy:'operator-reviewed-retirement',noncooperatingWriters:'not-independently-observed',
   reviewDigest:hash('fixture-owned-process-tree-and-isolated-roots')},
   assertHeld:async()=>{assert.equal(retirementActive,true);await lstat(lock);return {active:true,...context,leaseId:'fixture-retirement'};},
   release:async()=>{await handle.close();await rm(lock);retirementActive=false;return {released:true};}};
 };
 const adapter=createNativeCoherentCaptureAdapter({engineId:'native',cwd:workspace,stagingDirectory:staging,
  connect:async()=>connect(python,controlConfig,nativeEnv)});
 const recorded={engineId:'native',acquire:async context=>{
  const lease=await adapter.acquire(context);return {...lease,release:async value=>{
   assert.equal(retirementActive,true);if(value?.productSha256){const review=await inspectInstallationArchive(outputFile);assert.equal(review.archiveSha256,value.productSha256);}
   const released=await lease.release(value);nativeRelease=true;return released;
  }};
 }};
 const receipt=await createCoherentInstallationArchive(request,{acquireWriterExclusion,nativeCaptures:[recorded]});
 assert.equal(nativeRelease,true);assert.equal(retirementActive,false);assert.equal(receipt.completeProduct,true);assert.equal(receipt.completeCoverage,true);
 assert.equal(receipt.captureRelease.state,'released');assert.equal(JSON.parse(await readFile(outputFile+'.capture.json','utf8')).release.state,'released');
 const review=await inspectInstallationArchive(outputFile);assert.equal(review.manifest.captureConsistency.status,'qualified-at-capture');
 assert.equal(review.manifest.proof.qualifiedOwners.length,17);assert.equal(review.manifest.nativeProofs[0].inspection.authority.completeNativeBackup,true);
 const destination=join(root,'coherent-restored');const restored=await restoreInstallationArchive({archiveFile:outputFile,destination,...receipt,privateContentReviewed:true});
 assert.equal(restored.completeProduct,true);assert.equal(restored.inactive,true);assert.equal(restored.workReplayed,false);
 const descriptor=review.manifest.inventory.nativeArtifacts[0],path=join(staging,descriptor.artifactId+'.tar');
 assert.equal(hash(await readFile(join(destination,'native/'+descriptor.id))),descriptor.sha256);
 for(const name of ['updates','service'])assert.equal((await lstat(join(destination,'roots/installed:ledger:'+name))).isFile(),true);
 // Reusing full old bytes after native settings changed must remain partial,
 // even though every declared root is present and the service is still stopped.
 await writeFile(join(config.home,'settings.yaml'),'bundle:\n  app: []\n# changed after capture\n');
 const completed=createStorageInventory({...composition,nativeArtifacts:[descriptor]});
 const augmented=await createInstalledStorageInventory({inventory:completed,directory,includeCredentials:true,credentialsReviewed:true});
 const old={...request,requireCompleteProduct:false,outputFile:join(root,'old-native.unified'),inventory:augmented.inventory,
  inventoryDigest:augmented.inventory.digest,compositionInventory:completed,captureRequirements:augmented.captureRequirements,
  nativeWriterReviews:[review.manifest.nativeProofs[0].operatorReview],captureCapability:{status:'qualified-at-capture'}};
 const ports={validateInventory:validateStorageInventory,readNativeArtifact:async()=>({path,inspection:review.manifest.nativeProofs[0].inspection})};
 const partial=await createInstallationArchive(old,ports);assert.equal(partial.completeCoverage,true);assert.equal(partial.completeProduct,false);
 await assert.rejects(createInstallationArchive({...old,outputFile:join(root,'old-false-complete.unified'),requireCompleteProduct:true},ports),/coherent_capture_unavailable/);
 await assert.rejects(lstat(join(root,'old-false-complete.unified')),/ENOENT/);
 // Lost post-seal release replies preserve exact sealed bytes and a durable
 // unknown outcome; capture is never retried automatically.
 const lostOutput=join(root,'release-unknown.unified');
 const lost={engineId:'native',acquire:async c=>{const lease=await adapter.acquire(c);return {...lease,
  release:async value=>{await lease.release(value);throw Error('fixture lost native release reply');}};}};
 await assert.rejects(createCoherentInstallationArchive({...request,outputFile:lostOutput},{acquireWriterExclusion,nativeCaptures:[lost]}),/sealed_capture_release_unconfirmed/);
 const retained=await inspectInstallationArchive(lostOutput),outcome=JSON.parse(await readFile(lostOutput+'.capture.json','utf8'));
 assert.equal(outcome.sealed,true);assert.equal(outcome.archiveSha256,retained.archiveSha256);assert.equal(outcome.release.state,'unknown');
 assert.equal(outcome.release.native[0].state,'unknown');assert.equal(outcome.workReplayed,false);
 if(evidenceDirectory){
  await mkdir(evidenceDirectory,{recursive:true,mode:0o700});
  for(const file of [outputFile,outputFile+'.capture.json',lostOutput,lostOutput+'.capture.json'])await copyFile(file,join(evidenceDirectory,file.slice(file.lastIndexOf('/')+1)));
  await copyFile(path,join(evidenceDirectory,'native-artifact.tar'));
  for(const [name,value] of [['capture-receipt.json',receipt],['native-inspection.json',review.manifest.nativeProofs[0].inspection],['archive-review.json',review],['restore-receipt.json',restored]])await writeFile(join(evidenceDirectory,name),JSON.stringify(value,null,2)+'\n',{mode:0o600});
 }
 return {completeCoverage:true,completeProduct:true,heldNativeAndSupervisorCapture:true,nativeReleasedAfterSeal:true,
  oldArtifactCannotUpgrade:true,sealedArchiveSurvivesUnknownRelease:true,configuredOwners:17,nativeArtifactSha256:descriptor.sha256,archiveSha256:receipt.archiveSha256,
  inactiveRestore:true,workReplayed:false,externalWriters:'isolated fixture retirement assumption'};
}
