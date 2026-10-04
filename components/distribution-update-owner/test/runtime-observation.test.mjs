import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const ownerModule=process.env.DISTRIBUTION_OWNER_MODULE||new URL('../dist/index.js',import.meta.url).href;
const {SignedReleaseAdapter,runtimeObservation,observedHostStatus,serveHostControl,HostControlClient}=await import(ownerModule);
import {publisher,artifact} from './release-fixtures.mjs';
const execute=promisify(execFile);
const appCode=`
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {syncBuiltinESMExports} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
const api=await import(process.env.OWNER_MODULE);
const counts={bytes:0,reads:0,metadata:0};
for(const name of ['readFile','lstat','readdir','realpath','open']) {
 const original=fs.promises[name];
 fs.promises[name]=async(...args)=>{const result=await original(...args);if(name==='readFile'){counts.reads++;counts.bytes+=Buffer.byteLength(result);}else counts.metadata++;return result;};
}
syncBuiltinESMExports();
let ready=true,admissions=0,externalChecks=0;
const runtime=await api.createRuntimeIdentity({entrypointUrl:import.meta.url,trustedKeys:JSON.parse(process.env.TRUSTED_KEYS),
 isReady:async()=>{externalChecks++;await readFile(new URL('package.json',import.meta.url));return ready;},observeReady:()=>ready});
const startup={...counts};
assert.ok(startup.bytes>0);
assert.equal(runtime.observeStatus().integrity.lastCheck,null);
await runtime.inspectRunning();
const baseline={...counts},baselineChecks=externalChecks;
const control=await api.serveHostControl({token:'a'.repeat(64),inspectRunning:runtime.inspectRunning,observeRuntime:runtime.observeStatus,
 host:{inspectQuiescence:()=>({enabled:true,intakeClosed:false}),quiescenceReceipt:()=>null,
 admitQuiescence:()=>{admissions++;throw Error('must not reach changed code');},releaseQuiescence:()=>{throw Error('unused');}}});
const client=new api.HostControlClient({dataScope:runtime.dataScope,connect:()=>({url:control.url,token:'a'.repeat(64),dataScope:runtime.dataScope})});
try {
 const snapshots=[];
 for(let n=0;n<25;n++)snapshots.push(await client.observeStatus());
 assert.deepEqual(counts,baseline);assert.equal(externalChecks,baselineChecks);
 const first=snapshots[0];assert.equal(first.runtime.integrity.fresh,false);assert.equal(first.runtime.integrity.lastCheck.outcome,'verified');
 assert.equal(first.runtime.identity,undefined);assert.equal(first.runtime.ready,undefined);
 const verifiedAt=first.runtime.integrity.lastVerifiedAt;
 // The observation is detached and cannot mutate the trusted binding or record.
 first.runtime.binding.identity.id='forged';first.runtime.integrity.lastCheck.outcome='failed';
 assert.notEqual(runtime.observeStatus().binding.identity.id,'forged');
 assert.equal(runtime.observeStatus().integrity.lastCheck.outcome,'verified');
 ready=false;assert.equal((await client.observeStatus()).runtime.readyObserved,false);ready=true;
 await writeFile(new URL('node_modules/fixture-component/index.js',import.meta.url),'changed code');
 const stillObserved=await client.observeStatus();assert.equal(stillObserved.runtime.integrity.fresh,false);
 assert.deepEqual(counts,baseline);
 await assert.rejects(client.admitRestart({commandId:'cannot-admit',purpose:'distribution-update',dataScope:runtime.dataScope,signal:new AbortController().signal}));
 assert.equal(admissions,0);assert.ok(counts.bytes>baseline.bytes);
 const failed=await client.observeStatus();assert.equal(failed.runtime.integrity.lastCheck.outcome,'failed');
 assert.equal(failed.runtime.integrity.lastVerifiedAt,verifiedAt);assert.equal(failed.runtime.integrity.fresh,false);
 await assert.rejects(runtime.inspectRunning(),/runtime_inventory_mismatch/);
 console.log(JSON.stringify({startup,baseline,observations:25,observedCodeBytes:0,observedMetadataReads:0,externalChecks:baselineChecks,
 changedFileRefusedBeforeAdmission:true,failedIntegrityVisible:true,existingDeepCheckPreserved:true}));
} finally {client.close();await control.close();}
`;

test('signed process observations scan zero code bytes; changed files still fail deep admission',async t=>{
 const root=await mkdtemp(join(tmpdir(),'runtime-observation-')),pub=await publisher();
 t.after(async()=>{await pub.close();await rm(root,{recursive:true,force:true});});
 const a=await artifact(root,1,pub.origin,appCode);pub.publish([a],1);
 const adapter=new SignedReleaseAdapter({directory:join(root,'candidates'),channelUrl:pub.origin+'/channel.json',trustedKeys:pub.keys,
 accessScope:'fixture',allowedArtifactOrigins:[pub.origin],allowLoopbackHttp:true,
 resolveSources:async()=>(await fetch(pub.origin+'/sources')).json()});
 const prepared=await adapter.prepare(a.release.identity,{commandId:'fixture',signal:new AbortController().signal});
 const launch=await adapter.resolveLaunch(prepared);
 const result=JSON.parse((await execute(process.execPath,launch.args,{env:{...process.env,...launch.env,
 OWNER_MODULE:ownerModule,TRUSTED_KEYS:JSON.stringify(pub.keys),
 AMPLIFIER_DISTRIBUTION_INSTANCE_ID:'observation-instance',AMPLIFIER_DISTRIBUTION_DATA_SCOPE:'observation-scope'}})).stdout);
 assert.equal(result.observedCodeBytes,0);assert.equal(result.changedFileRefusedBeforeAdmission,true);
 assert.equal(result.existingDeepCheckPreserved,true);assert.ok(result.baseline.bytes>result.startup.bytes);
 if(process.env.DISTRIBUTION_OBSERVATION_ACCEPTANCE){
  const {writeFile}=await import('node:fs/promises');await writeFile(process.env.DISTRIBUTION_OBSERVATION_ACCEPTANCE,JSON.stringify(result,null,2)+'\n');
 }
});

test('observed schemas refuse claimed fresh authority or a RunningIdentity-shaped response',()=>{
 const runtime={schema:'distribution-runtime-observation-v1',binding:{identity:{id:'v1',version:'1.0.0',revision:'a'.repeat(40),digest:'a'.repeat(64)},instanceId:'fixture',dataScope:'fixture'},
 observedAt:1,readyObserved:true,integrity:{fresh:false,lastVerifiedAt:1,lastCheck:{sequence:1,completedAt:1,outcome:'verified'}}};
 assert.deepEqual(runtimeObservation(runtime),runtime);
 assert.throws(()=>runtimeObservation({...runtime,integrity:{...runtime.integrity,fresh:true}}));
 assert.throws(()=>runtimeObservation({...runtime,ready:true}));
 assert.throws(()=>runtimeObservation({...runtime,integrity:{...runtime.integrity,lastCheck:{sequence:0,completedAt:1,outcome:'verified'}}}));
 assert.throws(()=>observedHostStatus({...runtime.binding,ready:true}));
});


test('observation refuses missing capability and foreign scope without falling back to deep inspection',async()=>{
 let deep=0,samples=0;
 const observation={schema:'distribution-runtime-observation-v1',binding:{identity:{id:'v1',version:'1.0.0',revision:'a'.repeat(40),digest:'a'.repeat(64)},instanceId:'fixture',dataScope:'owned'},
 observedAt:1,readyObserved:true,integrity:{fresh:false,lastVerifiedAt:null,lastCheck:null}};
 for(const supported of [false,true]) {
  const server=await serveHostControl({token:'b'.repeat(64),inspectRunning:()=>{deep++;throw Error('must not deep scan');},
   ...(supported?{observeRuntime:()=>observation}:{}),host:{inspectQuiescence:()=>{samples++;return {enabled:true,intakeClosed:true};}}});
  const client=new HostControlClient({dataScope:supported?'foreign':'owned',connect:()=>({url:server.url,token:'b'.repeat(64),dataScope:supported?'foreign':'owned'})});
  try {await assert.rejects(client.observeStatus());assert.equal(deep,0);assert.equal(samples,0);}
  finally{client.close();await server.close();}
 }
});
