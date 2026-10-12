import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile,writeFile,realpath,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createInstallationBackupCoordinator} from '../src/installation-backup-coordinator.js';
import {createStorageInventory} from '../src/storage-inventory.js';

async function fixture(t,overrides={}){
 const root=await realpath(await mkdtemp(join(tmpdir(),'backup-controller-')));
 t.after(()=>rm(root,{recursive:true,force:true}));
 const directory=join(root,'installation'),stateDirectory=join(root,'backups');
 for(const p of [directory,stateDirectory])await mkdir(p,{mode:0o700});
 const expected={installationId:'owned',dataScope:'owned',ownerId:'service',instanceId:'before',releaseDigest:'a'.repeat(64)};
 let inventory=createStorageInventory({namespace:'owned',account:'owned',applicationStateDirectory:join(directory,'application'),
  owners:[{id:'application-updates',schemaVersion:1,revision:'fixture',participantId:'application-updates',rootIds:['application'],externalStorage:'none'}],
  roots:[{id:'application',ownerIds:['application-updates'],path:join(directory,'application'),coverage:'authoritative',capture:'tree'}]});
 const counts={stop:0,close:0,capture:0,reopen:0,resume:0},queue=[];
 let state='running',current,stopCommand,resumeCommand;
 const service={inspect:async()=>({state,identity:expected}),
  stopForMaintenance:async command=>{counts.stop++;stopCommand=command;state='stopped';},
  resume:async command=>{counts.resume++;resumeCommand=command;state='running';},
  receipt:async commandId=>commandId===resumeCommand?.commandId?{commandId,expected,status:'ready',admissionSettlement:{state:'settled'}}:null,
  waitFor:async commandId=>commandId===stopCommand?.commandId?{commandId,expected,status:'stopped',maintenanceStop:true}:{commandId,expected,status:'ready',admissionSettlement:{state:'settled'}}};
 current={service,close:async()=>{counts.close++;}};
 const options={directory,stateDirectory,outlivesDistribution:true,getSupervisor:()=>current,replaceSupervisor:s=>{current=s;},readCompositionInventory:async()=>inventory,
  readConfiguration:async()=>({configuration:{dataScope:'owned',serviceLifecycle:{installationId:'owned',ownerId:'service'}}}),
  acquireWriterExclusion:async()=>{},nativeCaptures:[],schedule:f=>queue.push(f),
  capture:async request=>{
   counts.capture++;assert.equal(current,null);assert.equal(state,'stopped');assert.equal(request.requireCompleteProduct,true);
   await writeFile(request.outputFile,'private archive',{mode:0o600});
   return {bytes:15,archiveSha256:'b'.repeat(64),manifestDigest:'c'.repeat(64),completeProduct:true,captureOutcomeRecorded:true,captureRelease:{state:'released'}};
  },reopen:async()=>{counts.reopen++;return {service,close:async()=>{counts.close++;}};},...overrides};
 const owner=await createInstallationBackupCoordinator(options);
 const review=await owner.review(),args={reviewId:review.id,reviewDigest:review.digest,privateContentReviewed:true,credentialsReviewed:true};
 return {root,owner,options,review,args,counts,queue,expected,service,setInventory:v=>{inventory=v;},inventory,
  run:async id=>{queue.shift()();return owner.waitFor(id);}};
}
test('durable acceptance precedes stop; successful archive closes and reopens exactly once',async t=>{
 const f=await fixture(t),accepted=await f.owner.submit('one',f.args);
 assert.equal(accepted.status,'accepted');assert.equal(f.counts.stop,0);
 const saved=JSON.parse(await readFile(join(f.root,'backups/commands/one/receipt.json')));assert.equal(saved.status,'accepted');
 assert.equal((await f.owner.submit('one',f.args)).status,'accepted');assert.equal(f.queue.length,1);
 const result=await f.run('one');assert.equal(result.status,'succeeded');assert.equal(result.workReplayed,false);
 assert.deepEqual(f.counts,{stop:1,close:1,capture:1,reopen:1,resume:1});
 assert.equal((await f.owner.inspect()).active,null);assert.equal((await f.owner.submit('one',f.args)).status,'succeeded');
 await assert.rejects(f.owner.submit('one',{...f.args,reviewDigest:'d'.repeat(64)}),/command_conflict/);
});
test('changed inventory is refused before service or archive effects',async t=>{
 const f=await fixture(t);await f.owner.submit('stale',f.args);
 f.setInventory(createStorageInventory({...f.inventory,account:'different'}));
 assert.equal((await f.run('stale')).status,'failed');assert.equal(f.counts.stop,0);assert.equal(f.counts.capture,0);
 assert.equal((await f.owner.inspect()).active,null);
});
test('foreign installation and incomplete coverage are refused before review or effects',async t=>{
 const f=await fixture(t);
 const foreign=await createInstallationBackupCoordinator({...f.options,readConfiguration:async()=>({configuration:{dataScope:'foreign',serviceLifecycle:{installationId:'foreign',ownerId:'service'}}})});
 await assert.rejects(foreign.review(),/binding_conflict/);
 f.setInventory(createStorageInventory({...f.inventory,omissions:[{id:'missing',reason:'Unknown private authority',blocksComplete:true}]}));
 await assert.rejects(f.owner.review(),/complete_scope_required/);assert.equal(f.counts.stop,0);
});
test('controller restart exposes interrupted original and never schedules it again',async t=>{
 const f=await fixture(t);await f.owner.submit('interrupted',f.args);
 const reopened=await createInstallationBackupCoordinator(f.options);
 assert.equal((await reopened.receipt('interrupted')).status,'unknown');
 assert.equal((await reopened.submit('interrupted',f.args)).status,'unknown');
 assert.equal(f.queue.length,1);assert.equal(f.counts.stop,0);
 assert.equal((await reopened.reconcile('interrupted')).status,'unknown');
 assert.equal(f.counts.stop,0);assert.equal(f.counts.capture,0);
 await assert.rejects(reopened.review(),/operation_pending/);
});
test('capture release uncertainty preserves stopped service and blocks a second backup',async t=>{
 const f=await fixture(t,{capture:async()=>{throw Error('archive_sealed_capture_release_unconfirmed');}});
 await f.owner.submit('uncertain',f.args);const result=await f.run('uncertain');
 assert.equal(result.status,'unknown');assert.equal(result.error,'archive_sealed_capture_release_unconfirmed');
 assert.equal(f.counts.reopen,0);assert.equal(f.counts.resume,0);
 assert.equal((await f.owner.inspect()).active.commandId,'uncertain');
 await assert.rejects(f.owner.submit('another',f.args),{code:'EEXIST'});
 assert.equal((await f.owner.submit('uncertain',f.args)).status,'unknown');
});
test('proven no-effect busy refusal leaves service running and releases admission',async t=>{
 const f=await fixture(t);f.service.waitFor=async commandId=>({commandId,status:'refused',noEffect:true,expected:f.expected});
 await f.owner.submit('busy',f.args);const result=await f.run('busy');
 assert.equal(result.status,'failed');assert.equal(result.error,'backup_service_busy');assert.equal(f.counts.close,0);assert.equal(f.counts.capture,0);
 assert.equal((await f.owner.inspect()).active,null);
});
test('private review and credential disclosure cannot be omitted or replaced by paths',async t=>{
 const f=await fixture(t);
 assert.equal(f.review.encrypted,false);assert.equal(f.review.includesCredentials,true);
 await assert.rejects(f.owner.submit('unsafe',{...f.args,credentialsReviewed:false}),/review_required/);
 await assert.rejects(f.owner.submit('unsafe',{...f.args,outputFile:'/tmp/elsewhere'}),/review_required/);
 assert.equal(f.counts.stop,0);assert.equal((await f.owner.inspect()).active,null);
});
test('lost resume acknowledgement reconciles exact retained archive and service receipt without replay',async t=>{
 const f=await fixture(t);const wait=f.service.waitFor;
 f.service.waitFor=async commandId=>{if(commandId.startsWith('backup-resume-'))throw Error('reply_lost');return wait(commandId);};
 await f.owner.submit('lost',f.args);assert.equal((await f.run('lost')).status,'unknown');
 const value=await f.owner.receipt('lost'),archive={archiveSha256:'b'.repeat(64),manifestDigest:'c'.repeat(64),manifest:{coverage:{completeProduct:true},proof:{expected:f.expected,stoppedCommandId:value.stopCommandId}}};
 await writeFile(join(f.root,'backups/commands/lost/backup.unified.capture.json'),JSON.stringify({sealed:true,release:{state:'released'},archiveSha256:archive.archiveSha256,manifestDigest:archive.manifestDigest}),{mode:0o600});
 const restarted=await createInstallationBackupCoordinator({...f.options,inspectArchive:async()=>archive});
 const before={...f.counts};assert.equal((await restarted.reconcile('lost')).status,'succeeded');assert.deepEqual(f.counts,before);
 assert.equal((await restarted.inspect()).active,null);
});


test('archive download is exact, bounded and unavailable until success',async t=>{
 const f=await fixture(t);await f.owner.submit('download',f.args);
 await assert.rejects(f.owner.readArchive('download',{sha256:'b'.repeat(64)}),/read_invalid/);
 await f.run('download');
 const page=await f.owner.readArchive('download',{sha256:'b'.repeat(64),offset:8,maxBytes:7});
 assert.equal(Buffer.from(page.data,'base64').toString(),'archive');assert.equal(page.bytes,15);
 for(const args of [{sha256:'a'.repeat(64)},{sha256:'b'.repeat(64),offset:-1},{sha256:'b'.repeat(64),maxBytes:262145}])await assert.rejects(f.owner.readArchive('download',args),/read_invalid/);
 await writeFile(join(f.root,'backups/commands/download/backup.unified'),'changed',{mode:0o600});
 await assert.rejects(f.owner.readArchive('download',{sha256:'b'.repeat(64)}),/archive_changed/);
});
