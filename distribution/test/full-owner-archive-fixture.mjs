// Signed disposable fixture entry; never used by a live launcher.
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {randomBytes} from 'node:crypto';
import {DatabaseSync} from 'node:sqlite';
import * as updates from '@amplifier/unified-distribution-update-owner';
import {createMediaCapability} from '@amplifier/unified-media-capability';
import {createDistribution,createInstalledStorageInventory} from './index.js';
import {composeServiceLifecycle,assertOwnedStopAdmission} from './launch.js';
const c=JSON.parse(await readFile(process.argv[process.argv.indexOf('--config')+1],'utf8')),root=dirname(c.webDirectory);
const n=JSON.parse(await readFile(c.engines[0].args.at(-1),'utf8'));
const restoring=await readFile(join(root,'restore-activation-request.json'),'utf8').then(JSON.parse).catch(e=>{if(e.code==='ENOENT')return null;throw e;});
let app,gate,control,supervisor,ready=false,closing;
const runtime=await updates.createRuntimeIdentity({entrypointUrl:import.meta.url,trustedKeys:c.supervision.trustedKeys,isReady:()=>ready});
const close=()=>closing??=(async()=>{ready=false;await control?.close();await app?.close();gate?.close();supervisor?.close();})();
try{
 if(!restoring&&c.legacyClientState){
  await mkdir(dirname(c.legacyClientState.database),{recursive:true});
  const legacy=new DatabaseSync(c.legacyClientState.database);
  try{legacy.exec('CREATE TABLE client_views(id TEXT PRIMARY KEY,value TEXT);CREATE TABLE state(id INTEGER PRIMARY KEY,value TEXT)');legacy.prepare('INSERT INTO client_views VALUES(?,?)').run('original-browser',await readFile(join(root,'legacy-client-fixture.json'),'utf8'));legacy.prepare('INSERT INTO state VALUES(1,?)').run(JSON.stringify({workspaces:[]}));}finally{legacy.close();}
 }
 if(!restoring){
 const media=join(c.stateDirectory,'capabilities/media');await mkdir(join(media,'receipts'),{recursive:true});
 // Let the installed owner create its authoritative schema. A projection-only
 // SQLite file is correctly refused as incomplete by current startup checks.
 const unexpected=()=>{throw Error('Archive fixture must not start media work');};
 const seedMedia=await createMediaCapability({directory:media,inspectSession:unexpected,delegate:unexpected,recordTranscript:unexpected});
 await seedMedia.close();
 await writeFile(join(media,'receipts/historical.json'),JSON.stringify({fingerprint:'retained',operation:'delegate',outcome:'unknown',at:1}));
 const db=new DatabaseSync(join(media,'transcript-intents.sqlite'));
 db.prepare('INSERT INTO history VALUES(?,?,?,?)').run('ahp-session:/01111111-1111-4111-8111-111111111111','voice-message','2026-10-01',JSON.stringify({id:'voice-message',role:'user',text:'Preserved voice text',via:'call',durability:'native'}));db.close();
 }
 const snapshots=join(c.stateDirectory,'snapshots');await mkdir(snapshots,{recursive:true});
 supervisor=updates.connectSupervisorFileLazy(c.supervision.discoveryFile);
 const serviceLifecycle=composeServiceLifecycle(c.supervision.serviceLifecycle,runtime,supervisor);
 gate=await updates.createManualIngressGate({directory:c.manualIngress.stateDirectory,id:'manual-preview-ingress'});
 const component=JSON.parse(await readFile(new URL('../components.json',import.meta.url),'utf8')).components['@amplifier/unified-distribution-update-owner'];
 let staged;
 const openApplication=()=>createDistribution({...c,quiescence:{instanceId:runtime.instanceId,dataScope:runtime.dataScope,timeoutMs:30000}},{applicationUpdateSupervisor:supervisor,serviceLifecycle,authorizeRecovery:async context=>({accountId:context.account}),verifyQuiescenceRelease:updates.createHostReleaseVerifier({supervisor:supervisor.owner,inspectRunning:runtime.inspectRunning}),runtimeOwnerBindings:[{owner:gate.participant,storage:{packageName:'@amplifier/unified-distribution-update-owner',packageVersion:component.version,revision:component.revision,configKey:'manualIngress',rootRole:'ingress',stateDirectory:c.manualIngress.stateDirectory}}],beforeRecoveryMaintenance:async({stageOwnerSnapshot})=>{staged=await stageOwnerSnapshot({ownerId:'capability:observations',snapshotCommandId:'original-seven-stores',directory:snapshots,privateContentReviewed:true});}});
 app=await openApplication();
 let continuation;
 const seed=await readFile(join(root,'legacy-seed.json'),'utf8').then(JSON.parse).catch(e=>{if(e.code==='ENOENT')return null;throw e;});
 if(seed&&!restoring){
  await app.host.reconcileLibrary();
  const page=await app.host.queryLibrary({connectionId:'fixture-reader',allowedWorkspaceRoots:[c.defaultWorkspace],limit:10,archive:'all'});
  assert.equal(page.items.length,1,JSON.stringify(page));const session=page.items[0].uri;
  assert.equal(app.host.diagnostics().activeAgents,0);
  const commandId='explicit-full-owner-continuation';
  await app.host.submitTurn(session,{commandId,text:'CONTINUE-MIGRATED-41. Use the previous phrase and save the next artifact.',origin:'ui',clientId:'fixture-user'});
  const result=await app.host.waitForTurn(session,commandId,90000);
  assert.equal(result.status,'completed',JSON.stringify(result));assert.match(result.text,/violet compass/);
  await app.close();app=null;
  const requests=await readFile(join(root,'provider-requests.jsonl'),'utf8');assert.equal(requests.trim().split('\n').length,2);
  assert.equal(await readFile(join(root,'effects.jsonl'),'utf8'),'write\n');
  const rows=(await readFile(join(n.home,seed.relativeDirectory,'transcript.jsonl'),'utf8')).trim().split('\n').map(JSON.parse);
  const prefix=rows.slice(0,seed.originalRows.length).map((row,index)=>{
   const copy=structuredClone(row),original=seed.originalRows[index];
   if(original.metadata?._seq===undefined&&copy.metadata?._seq!==undefined){assert.equal(copy.metadata._seq,index);delete copy.metadata._seq;if(!original.metadata&&!Object.keys(copy.metadata).length)delete copy.metadata;}
   return copy;
  });
  assert.deepEqual(prefix,seed.originalRows);
  assert.equal(rows.filter(r=>r.role==='user'&&String(r.content).includes('CONTINUE-MIGRATED-41')).length,1);
  app=await openApplication();await app.host.reconcileLibrary();
  assert.equal(app.host.diagnostics().activeAgents,0);
  assert.equal(await readFile(join(root,'provider-requests.jsonl'),'utf8'),requests);
  const context=await app.host.readSessionContext(session,10);assert.ok(context.messages.some(m=>m.text.includes('violet compass')));
  continuation={session,providerCalls:2,toolEffects:1,newInputOnce:true,originalRowsPreserved:seed.originalRows.length,coldRestartReplayed:false,configuredOwners:app.quiescence.requiredOwners};
 }
 let job;
 if(!restoring){
 const call=(operation,args,commandId)=>app.host.invokeCapability({channel:'ahp-root://',topic:'recovery',operation,version:1,args,commandId},{actorId:'fixture-operator',clientId:'fixture-reviewer',origin:'ui'});
 const submitted=await call('recovery.appReset.prepare',{parts:['notifications.settings'],privateContentReviewed:true},'original-review');
 for(let i=0;i<400;i++){job=(await call('recovery.job',{jobId:submitted.result.id},'read-'+i)).result;if(['prepared','unknown','refused'].includes(job.state))break;await new Promise(r=>setTimeout(r,25));}
 assert.equal(job.state,'prepared',JSON.stringify(job));assert.equal(staged.status,'sealed');assert.equal(app.host.inspectQuiescence().intakeClosed,false);
 }
 const nativeRoots={'native-home':n.home,'app-home':n.appHome,...n.maintenanceFullNativeRoots,'workspace-settings':join(c.defaultWorkspace,'.amplifier')};
 const inventory=await app.storageInventory({externalCoverage:{'native-admin':'declared','native-message-metadata':'declared',portability:'declared'},externalRoots:Object.entries(nativeRoots).map(([id,path])=>({id,path,ownerIds:['native-admin','native-message-metadata'],coverage:'authoritative',capture:'native-artifact'})),nativeCapturePlans:[{engineId:'amplifier',rootIds:Object.keys(nativeRoots)}]});
 assert.equal(inventory.completeEligible,false);assert.deepEqual(inventory.omissions,[]);assert.equal(inventory.owners.length,21);assert.equal(app.host.diagnostics().activeAgents,0);
 if(!restoring)await writeFile(c.supervision.hostControl.tokenFile,randomBytes(32).toString('hex')+'\n',{flag:'wx',mode:0o600});
 control=await updates.serveHostControl({host:app.host,inspectRunning:runtime.inspectRunning,recoveryOwners:app.quiescence.requiredOwners,token:(await readFile(c.supervision.hostControl.tokenFile,'utf8')).trim(),discovery:{file:c.supervision.hostControl.discoveryFile,tokenFile:c.supervision.hostControl.tokenFile,dataScope:runtime.dataScope}});
 ready=true;
 if(!restoring)await writeFile(join(root,'archive-ready.json'),JSON.stringify({inventory,job,staged,owners:app.quiescence.requiredOwners,agents:0,url:app.url,...(continuation?{continuation}:{})}),{mode:0o600});
 process.on('SIGTERM',()=>void(async()=>{assertOwnedStopAdmission(app.host,runtime,serviceLifecycle);await close();process.exit(0);})().catch(async e=>{await writeFile(join(root,'stop-error.txt'),e.stack); }));
 if(restoring)void(async()=>{
  const before=await readFile(join(root,'provider-requests.jsonl'),'utf8'),history=join(n.home,seed.relativeDirectory,'transcript.jsonl'),prior=(await readFile(history,'utf8')).trim().split('\n').map(JSON.parse);
  for(let i=0;i<1200&&app.host.inspectQuiescence().intakeClosed;i++)await new Promise(resolve=>setTimeout(resolve,25));
  assert.equal(app.host.inspectQuiescence().intakeClosed,false);
  assert.equal(app.host.diagnostics().activeAgents,0);assert.equal(await readFile(join(root,'provider-requests.jsonl'),'utf8'),before);
  await app.host.reconcileLibrary();const page=await app.host.queryLibrary({connectionId:'fixture-restored-reader',allowedWorkspaceRoots:[c.defaultWorkspace],limit:10,archive:'all'});
  assert.equal(page.items.length,1,JSON.stringify(page));const session=page.items[0].uri;
  const originalReady=JSON.parse(await readFile(join(root,'archive-ready.json'),'utf8'));assert.equal(session,originalReady.continuation.session);
  const admitted=await app.host.submitTurn(session,{commandId:restoring.commandId,text:restoring.text,origin:'ui',clientId:'fixture-user'});assert.equal(admitted.accepted,true,JSON.stringify(admitted));
  const done=await app.host.waitForTurn(session,restoring.commandId,90000);assert.equal(done.status,'completed',JSON.stringify(done));assert.match(done.text,/violet compass/);
  const rows=(await readFile(history,'utf8')).trim().split('\n').map(JSON.parse);assert.deepEqual(rows.slice(0,prior.length),prior);assert.equal(rows.filter(r=>r.role==='user'&&r.content===restoring.text).length,1);
  const calls=(await readFile(join(root,'provider-requests.jsonl'),'utf8')).trim().split('\n').length;assert.equal(calls,before.trim().split('\n').length+1);assert.equal(await readFile(join(root,'effects.jsonl'),'utf8'),'write\n');
  const nextInventory=await createInstalledStorageInventory({inventory,directory:dirname(c.stateDirectory),includeCredentials:true,credentialsReviewed:true});
  assert.deepEqual(nextInventory.inventory.omissions,[]);
  const retainedRecovery=nextInventory.inventory.roots.filter(r=>r.id.startsWith('installed:recovery-'));
  assert.equal(retainedRecovery.length,1);assert.equal(retainedRecovery[0].capture,'tree');assert.equal(retainedRecovery[0].coverage,'authoritative');
  await writeFile(join(root,'restore-activation-result.json'),JSON.stringify({owners:app.quiescence.requiredOwners,session,restoredHome:n.home,priorRowsPreserved:prior.length,explicitNewInputs:1,providerCalls:1,additionalToolEffects:0,startupReplayed:false,recoveryAuthorityIncludedInNextInventory:true}),{flag:'wx',mode:0o600});
 })().catch(async e=>{await writeFile(join(root,'restore-activation-error.txt'),e.stack);});
}catch(e){await writeFile(join(root,'archive-start-error.txt'),e.stack);await close();throw e;}
