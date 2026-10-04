// Signed disposable fixture entry; never used by a live launcher.
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {randomBytes} from 'node:crypto';
import {DatabaseSync} from 'node:sqlite';
import * as updates from '@amplifier/unified-distribution-update-owner';
import {createDistribution} from './index.js';
import {composeServiceLifecycle,assertOwnedStopAdmission} from './launch.js';
const c=JSON.parse(await readFile(process.argv[process.argv.indexOf('--config')+1],'utf8')),root=dirname(c.webDirectory);
const n=JSON.parse(await readFile(c.engines[0].args.at(-1),'utf8'));
let app,gate,control,supervisor,ready=false,closing;
const runtime=await updates.createRuntimeIdentity({entrypointUrl:import.meta.url,trustedKeys:c.supervision.trustedKeys,isReady:()=>ready});
const close=()=>closing??=(async()=>{ready=false;await control?.close();await app?.close();gate?.close();supervisor?.close();})();
try{
 const media=join(c.stateDirectory,'capabilities/media');await mkdir(join(media,'receipts'),{recursive:true});
 await writeFile(join(media,'receipts/historical.json'),JSON.stringify({fingerprint:'retained',operation:'delegate',outcome:'unknown',at:1}));
 const db=new DatabaseSync(join(media,'transcript-intents.sqlite'));
 db.exec('CREATE TABLE history(session TEXT NOT NULL,id TEXT NOT NULL,created TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(session,id))');
 db.prepare('INSERT INTO history VALUES(?,?,?,?)').run('ahp-session:/01111111-1111-4111-8111-111111111111','voice-message','2026-10-01',JSON.stringify({id:'voice-message',role:'user',text:'Preserved voice text',via:'call',durability:'native'}));db.close();
 const snapshots=join(c.stateDirectory,'snapshots');await mkdir(snapshots,{recursive:true});
 supervisor=updates.connectSupervisorFileLazy(c.supervision.discoveryFile);
 const serviceLifecycle=composeServiceLifecycle(c.supervision.serviceLifecycle,runtime,supervisor);
 gate=await updates.createManualIngressGate({directory:c.manualIngress.stateDirectory,id:'manual-preview-ingress'});
 const component=JSON.parse(await readFile(new URL('../components.json',import.meta.url),'utf8')).components['@amplifier/unified-distribution-update-owner'];
 let staged;
 app=await createDistribution({...c,quiescence:{instanceId:runtime.instanceId,dataScope:runtime.dataScope,timeoutMs:30000}},{applicationUpdateSupervisor:supervisor,serviceLifecycle,authorizeRecovery:async context=>({accountId:context.account}),verifyQuiescenceRelease:updates.createHostReleaseVerifier({supervisor:supervisor.owner,inspectRunning:runtime.inspectRunning}),runtimeOwnerBindings:[{owner:gate.participant,storage:{packageName:'@amplifier/unified-distribution-update-owner',packageVersion:component.version,revision:component.revision,configKey:'manualIngress',rootRole:'ingress',stateDirectory:c.manualIngress.stateDirectory}}],beforeRecoveryMaintenance:async({stageOwnerSnapshot})=>{staged=await stageOwnerSnapshot({ownerId:'capability:observations',snapshotCommandId:'original-seven-stores',directory:snapshots,privateContentReviewed:true});}});
 const call=(operation,args,commandId)=>app.host.invokeCapability({channel:'ahp-root://',topic:'recovery',operation,version:1,args,commandId},{actorId:'fixture-operator',clientId:'fixture-reviewer',origin:'ui'});
 const submitted=await call('recovery.appReset.prepare',{parts:['notifications.settings'],privateContentReviewed:true},'original-review');let job;
 for(let i=0;i<400;i++){job=(await call('recovery.job',{jobId:submitted.result.id},'read-'+i)).result;if(['prepared','unknown','refused'].includes(job.state))break;await new Promise(r=>setTimeout(r,25));}
 assert.equal(job.state,'prepared',JSON.stringify(job));assert.equal(staged.status,'sealed');assert.equal(app.host.inspectQuiescence().intakeClosed,false);
 const nativeRoots={'native-home':n.home,'app-home':n.appHome,...n.maintenanceFullNativeRoots,'workspace-settings':join(c.defaultWorkspace,'.amplifier')};
 const inventory=await app.storageInventory({externalCoverage:{'native-admin':'declared','native-message-metadata':'declared',portability:'declared'},externalRoots:Object.entries(nativeRoots).map(([id,path])=>({id,path,ownerIds:['native-admin','native-message-metadata'],coverage:'authoritative',capture:'native-artifact'})),nativeCapturePlans:[{engineId:'amplifier',rootIds:Object.keys(nativeRoots)}]});
 assert.equal(inventory.completeEligible,false);assert.deepEqual(inventory.omissions,[]);assert.equal(inventory.owners.length,21);assert.equal(app.host.diagnostics().activeAgents,0);
 await writeFile(c.supervision.hostControl.tokenFile,randomBytes(32).toString('hex')+'\n',{flag:'wx',mode:0o600});
 control=await updates.serveHostControl({host:app.host,inspectRunning:runtime.inspectRunning,recoveryOwners:app.quiescence.requiredOwners,token:(await readFile(c.supervision.hostControl.tokenFile,'utf8')).trim(),discovery:{file:c.supervision.hostControl.discoveryFile,tokenFile:c.supervision.hostControl.tokenFile,dataScope:runtime.dataScope}});
 ready=true;
 await writeFile(join(root,'archive-ready.json'),JSON.stringify({inventory,job,staged,owners:app.quiescence.requiredOwners,agents:0,url:app.url}),{mode:0o600});
 process.on('SIGTERM',()=>void(async()=>{assertOwnedStopAdmission(app.host,runtime,serviceLifecycle);await close();process.exit(0);})().catch(async e=>{await writeFile(join(root,'stop-error.txt'),e.stack); }));
}catch(e){await writeFile(join(root,'archive-start-error.txt'),e.stack);await close();throw e;}
