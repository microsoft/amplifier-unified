import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {localRecoveryAuthorization,startConfiguredDistribution} from '../src/launch.js';

test('JSON recovery policy is account bound and credential export needs launcher authority',async()=>{
 assert.throws(()=>localRecoveryAuthorization({account:'local',recovery:{}}),/authorization/);
 const authorize=localRecoveryAuthorization({account:'local',recovery:{authorization:'local-account'}});
 for(const origin of ['ui','agent'])assert.deepEqual(await authorize({account:'local',origin}),{accountId:'local'});
 await assert.rejects(authorize({account:'other',origin:'ui'}),/not authorized/);
 await assert.rejects(authorize({account:'local',origin:'ui'},'recovery.prepare',{includeCredentials:true,credentialsReviewed:true}),/launcher/);
 for(const operation of ['recovery.appReset.prepare','recovery.appReset.apply','recovery.appReset.restore'])await assert.rejects(authorize({account:'local',origin:'ui'},operation,{parts:['notifications.credentials'],credentialsReviewed:true}),/launcher/);
 const privateAuthority=localRecoveryAuthorization({account:'local',recovery:{authorization:'local-account',credentials:true}});
 assert.deepEqual(await privateAuthority({account:'local',origin:'ui'},'recovery.appReset.prepare',{parts:['notifications.credentials'],credentialsReviewed:true}),{accountId:'local'});
});

test('presentation-only launcher requires its own account policy without native credential authority',async()=>{
 assert.equal(localRecoveryAuthorization({account:'local'}),undefined);
 assert.throws(()=>localRecoveryAuthorization({account:'local',conversationPresentation:{}}),/authorization/);
 const presentation={authorization:'local-account',credentials:true};
 const authorize=localRecoveryAuthorization({account:'local',conversationPresentation:presentation});
 for(const origin of ['ui','agent'])assert.deepEqual(await authorize({account:'local',origin},'recovery.presentation.prepare'),{accountId:'local'});
 await assert.rejects(authorize({account:'other',origin:'ui'}),/not authorized/);
 await assert.rejects(authorize({account:'local',origin:'untrusted'}),/not authorized/);
 await assert.rejects(authorize({account:'local'},'recovery.snapshot',{includeCredentials:true}),/not authorized/);
 await assert.rejects(authorize({account:'local'},'recovery.appReset.prepare',{parts:['notifications.credentials']}),/not authorized/);
 assert.throws(()=>localRecoveryAuthorization({account:'local',recovery:{},conversationPresentation:presentation}),/authorization/);
});

test('unsigned local launcher owns a fresh instance and closes all local services',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'distribution-launch-')),workspace=join(directory,'workspace'),web=join(directory,'web');
 for(const path of [workspace,web])await mkdir(path);
 await writeFile(join(web,'index.html'),'<html><head></head><body>Owned fixture</body></html>');
 let first,second;
 const config={account:'local',stateDirectory:join(directory,'state'),defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],webDirectory:web,engines:[{id:'fixture',command:process.execPath,args:[new URL('./fixtures/acp.mjs',import.meta.url).pathname]}],quiescence:{instanceId:'must-not-reuse',dataScope:'owned'}};
 try{
  await assert.rejects(startConfiguredDistribution({...config,gateway:{host:'0.0.0.0'}}),/loopback/);
  await assert.rejects(startConfiguredDistribution({...config,applicationUpdates:{}}),/supervision/);
  first=await startConfiguredDistribution(config);
  const original=first.quiescence.instanceId;
  assert.ok(original);assert.notEqual(original,'must-not-reuse');assert.equal((await fetch(first.url)).status,200);
  await first.close();await first.close();
  second=await startConfiguredDistribution(config);
  assert.notEqual(second.quiescence.instanceId,original);
  assert.equal((await fetch(second.url)).status,200);
 }finally{await second?.close();await first?.close();await rm(directory,{recursive:true,force:true});}
});

test('trusted service composition binds launch identity and never reads proof from caller evidence',async()=>{
 const {composeServiceLifecycle,assertOwnedStopAdmission}=await import('../src/launch.js');
 const binding={installationId:'install',ownerId:'owner'},runtime={instanceId:'actual',dataScope:'owned',identity:{digest:'a'.repeat(64)}};
 const env={AMPLIFIER_DISTRIBUTION_INSTALLATION_ID:'install',AMPLIFIER_DISTRIBUTION_OWNER_ID:'owner'};
 const composed=composeServiceLifecycle(binding,runtime,{service:{}},env);
 assert.equal(composeServiceLifecycle(undefined,runtime,{},{}),undefined);
 assert.deepEqual(composed.identity,{...binding,instanceId:'actual',dataScope:'owned',releaseDigest:'a'.repeat(64)});
 assert.throws(()=>composeServiceLifecycle({...binding,instanceId:'injected'},runtime,{},env),/Invalid/);
 assert.throws(()=>composeServiceLifecycle(binding,runtime,{},{}),/owned launch/);
 await assert.rejects(composed.verifyRelease({purpose:'distribution-update',outcome:'ready',evidence:{verified:true}}));
 const state={intakeClosed:true,fence:{fenceId:'held',commandId:'stop',phase:'held',purpose:'service-stop',instanceId:'actual',dataScope:'owned',serviceIdentity:composed.identity}};
 const host={inspectQuiescence:()=>state};
 assert.doesNotThrow(()=>assertOwnedStopAdmission(host,runtime,composed));
 for(const changes of [{phase:'unknown'},{purpose:'recovery'},{instanceId:'old'},{dataScope:'other'},{serviceIdentity:{...composed.identity,ownerId:'other'}}]){
  const changed={inspectQuiescence:()=>({...state,fence:{...state.fence,...changes}})};
  assert.throws(()=>assertOwnedStopAdmission(changed,runtime,composed));
 }
 assert.doesNotThrow(()=>assertOwnedStopAdmission({inspectQuiescence:()=>({...state,fence:{...state.fence,purpose:'distribution-update'}})},runtime,composed));
 assert.throws(()=>assertOwnedStopAdmission({inspectQuiescence:()=>({intakeClosed:false})},runtime,composed));
});
