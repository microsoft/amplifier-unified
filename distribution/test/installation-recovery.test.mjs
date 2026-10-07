import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,writeFile,readFile,lstat} from 'node:fs/promises';
import {join} from 'node:path';
import {serviceFixture} from './service-config-fixture.mjs';
import {prepareInstallationRecovery} from '../src/installation-recovery.js';
import {readInstalledServiceConfiguration} from '../src/service.js';
import {createDistribution} from '../src/index.js';

async function fixture(t){
 const f=await serviceFixture(t),home=join(f.root,'native'),appHome=join(f.root,'native-app'),nativeFile=join(f.root,'native.json');
 const state=join(f.root,'application'),restoredNative=join(f.root,'native-restored'),events=[];
 for(const p of [home,appHome,state,restoredNative])await mkdir(p,{mode:0o700});
 await writeFile(join(state,'saved'),'current state');await writeFile(join(home,'saved'),'original native');
 await writeFile(nativeFile,JSON.stringify({home,appHome,transferAuthorityDirectory:join(state,'portability')}),{mode:0o600});
 const a=f.values['application.json'];Object.assign(a,{account:'account',engines:[{id:'amplifier',command:'not-executed',args:['--config',nativeFile]}],nativeAdmin:{engine:'amplifier'},catalogProcess:{command:'not-executed',args:['-m','amplifier_session_catalog','serve','--db',join(state,'catalog.sqlite'),'--home',home]},host:{managedSessionRoot:join(state,'managed')}});await f.save();
 const expected={installationId:'installation',ownerId:'owner',dataScope:'fixture',instanceId:'instance',releaseDigest:'a'.repeat(64)};
 const request={schema:'unified-installation-recovery-v1',directory:f.root,commandId:'restore-once',archiveFile:join(f.root,'archive'),archiveSha256:'a'.repeat(64),manifestDigest:'b'.repeat(64),expected,stoppedCommandId:'stop',native:{engineId:'amplifier',configurationFile:nativeFile,cwd:f.root,destination:{rootId:'owned',name:'restored'}},privateContentReviewed:true,credentialsReviewed:true,writerRetirementReviewDigest:'c'.repeat(64)};
 const manifest={coverage:{completeProduct:true},captureConsistency:{status:'qualified-at-capture'},proof:{expected,stoppedCommandId:'stop'},inventory:{digest:'d'.repeat(64),account:'account',namespace:'fixture',applicationStateDirectory:state,owners:[{participantId:'host'}],roots:[{id:'application',path:state,capture:'tree',coverage:'authoritative'}],nativeArtifacts:[{engineId:'amplifier',artifactId:'e'.repeat(32),sha256:'f'.repeat(64)}]}};
 const launcherPath=join(restoredNative,'launcher.json'),environmentPath=join(restoredNative,'environment.json');
 await writeFile(launcherPath,JSON.stringify({home:join(restoredNative,'native-home'),appHome:join(restoredNative,'app-home')}),{mode:0o600});
 await writeFile(environmentPath,JSON.stringify({AMPLIFIER_SOURCE_STORE:join(restoredNative,'sources')}),{mode:0o600});
 const ports={
  inspectArchive:async()=>({archiveSha256:request.archiveSha256,manifestDigest:request.manifestDigest,manifest}),
  withSupervisorSnapshot:async(args,body)=>{assert.deepEqual(args.expected,expected);events.push('freeze');return body();},
  restoreArchive:async({destination})=>{const p=join(destination,'roots/application');await mkdir(p,{recursive:true,mode:0o700});await writeFile(join(p,'saved'),'restored state');},
  restoreNative:async()=>{events.push('native');return {result:{restored:true,artifactId:'e'.repeat(32),sha256:'f'.repeat(64),startsWorker:false,replaysInputs:false,launcherPath,environmentPath},inspection:{finalized:true}};},
  projectCatalog:async({db,request:r})=>{assert.ok(db.includes('archive/roots/application'));assert.equal(r.source_home,home);events.push('catalog');return {rebound:2,nativeFilesModified:false};},
 };
 return {...f,request,ports,manifest,home,state,events};
}
test('recovery keeps paths, credentials and prior state; retry is a receipt read with no effects',async t=>{
 const f=await fixture(t),original=await readFile(join(f.root,'application.json'));
 const result=await prepareInstallationRecovery(f.request,f.ports);
 assert.equal(result.serviceStarted,false);assert.equal(result.workReplayed,false);
 assert.equal(await readFile(join(f.state,'saved'),'utf8'),'restored state');
 assert.equal(await readFile(join(result.preservedApplication,'saved'),'utf8'),'current state');
 assert.equal(await readFile(join(f.home,'saved'),'utf8'),'original native');
 assert.deepEqual(await readFile(join(f.root,'recovery-restore-once/original-application.json')),original);
 await readInstalledServiceConfiguration(f.root);
 const next=JSON.parse(await readFile(join(f.root,'application.json'),'utf8'));
 assert.equal(next.stateDirectory,f.state);assert.equal(next.host.managedSessionRoot,join(f.state,'managed'));
 assert.equal(next.engines[0].args[1],join(f.root,'recovery-restore-once/native.json'));
 assert.equal((await prepareInstallationRecovery(f.request,f.ports)).previouslyPrepared,true);
 assert.deepEqual(f.events,['freeze','native','catalog']);
 await assert.rejects(prepareInstallationRecovery({...f.request,archiveSha256:'0'.repeat(64)},f.ports),/command_conflict/);
});
for(const failure of ['archive','identity','partial','engine','external'])test('refuses '+failure+' before allocating recovery or executing an owner',async t=>{
 const f=await fixture(t);
 if(failure==='archive')f.request.archiveSha256='0'.repeat(64),f.ports.inspectArchive=async()=>({archiveSha256:'a'.repeat(64),manifestDigest:f.request.manifestDigest,manifest:f.manifest});
 if(failure==='identity')f.manifest.proof.expected={...f.request.expected,instanceId:'foreign'};
 if(failure==='partial')f.manifest.coverage.completeProduct=false;
 if(failure==='engine')f.request.native.engineId='other';
 if(failure==='external')f.manifest.inventory.roots.push({id:'unrelated',path:'/unrelated',capture:'tree',coverage:'authoritative'});
 await assert.rejects(prepareInstallationRecovery(f.request,f.ports),/recovery_/);
 assert.deepEqual(f.events,[]);await assert.rejects(lstat(join(f.root,'recovery-restore-once')),e=>e.code==='ENOENT');
 assert.equal(await readFile(join(f.state,'saved'),'utf8'),'current state');
});
test('interrupted publication preserves both copies and prevents all reopening without replay',async t=>{
 const f=await fixture(t);f.ports.publicationCheckpoint=async()=>{throw Error('lost after retaining original');};
 await assert.rejects(prepareInstallationRecovery(f.request,f.ports),/lost after/);
 const journal=join(f.root,'recovery-restore-once');
 assert.equal(await readFile(join(journal,'previous-application/saved'),'utf8'),'current state');
 assert.equal(await readFile(join(journal,'archive/roots/application/saved'),'utf8'),'restored state');
 assert.equal(JSON.parse(await readFile(join(journal,'receipt.json'),'utf8')).phase,'publication-unknown');
 await assert.rejects(readInstalledServiceConfiguration(f.root),/recovery_requires_inspection/);
 await assert.rejects(createDistribution({stateDirectory:f.state,webDirectory:'unused',defaultWorkspace:'unused'}),/recovery_requires_inspection/);
 await assert.rejects(prepareInstallationRecovery(f.request,f.ports),/requires_inspection/);
 assert.deepEqual(f.events,['freeze','native','catalog']);
});
