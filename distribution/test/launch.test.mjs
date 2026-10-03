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
