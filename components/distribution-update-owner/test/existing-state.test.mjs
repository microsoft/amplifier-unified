import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, realpath, writeFile, readFile, rm, rename, symlink} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const api = await import(process.env.DISTRIBUTION_OWNER_MODULE ?? '../dist/index.js');
const {ServiceLifecycleOwner,PosixOwnedProcessLifecycle,createHostServiceReleaseVerifier,
  createOwnedServiceHandoffSource,launchExistingStateHandoff,inspectExistingStateBindings} = api;
const release={id:'fixture',version:'1.0.0',revision:'a'.repeat(40),digest:'a'.repeat(64)};
const target={identity:release,handle:'retained-fixture'};
const binding={installationId:'fixture-installation',ownerId:'fixture-owner',dataScope:'fixture-scope'};
const owners=['history-fixture','resources-fixture'];
const tick=()=>new Promise(r=>setImmediate(r));
async function until(fn){for(let i=0;i<300;i++){if(await fn())return;await new Promise(r=>setTimeout(r,10));}throw Error('fixture_deadline');}
async function fixture(t){
 const root=await realpath(await mkdtemp(join(tmpdir(),'existing-state-'))), data=join(root,'data'),config=join(root,'config.json');
 await mkdir(data);await writeFile(join(data,'history'),'saved conversation\n');await writeFile(config,'{"dataScope":"fixture-scope"}');
 const bindings=[{id:'history',path:data,kind:'directory'},{id:'configuration',path:config,kind:'file'}];
 const entry=join(root,'app.mjs'), ready=join(root,'ready.json');
 await writeFile(entry,`import {readFile,writeFile} from 'node:fs/promises';
 const text=await readFile(process.env.EXISTING_DATA+'/history','utf8');
 await writeFile(process.env.READY_FILE,JSON.stringify({instanceId:process.env.AMPLIFIER_DISTRIBUTION_INSTANCE_ID,dataScope:process.env.AMPLIFIER_DISTRIBUTION_DATA_SCOPE,text}));
 process.on('SIGTERM',()=>process.exit(0));setTimeout(()=>process.exit(0),20000);`);
 let fence=null,busy=false,failRelease=false,verify=true;const all=[],services=[],calls={launch:0,source:0,release:0};
 const inspect=async()=>{const live=all.map(l=>l.processes.inspect()).find(p=>p.state==='running');if(!live)return null;
  try{const r=JSON.parse(await readFile(ready,'utf8'));return r.instanceId===live.identity.instanceId?{identity:release,instanceId:r.instanceId,dataScope:r.dataScope,ready:true}:null;}catch{return null;}};
 let current;
 const verifier=createHostServiceReleaseVerifier({service:{proof:id=>current.proof(id)},inspectRunningService:async()=>{const r=await currentLifecycle.inspectOwned();return {...binding,instanceId:r.instanceId,releaseDigest:r.identity.digest};}});
 let currentLifecycle;
 const host={admitServiceStop:async r=>{
  if(busy)return {admitted:false,executed:false,intakeClosed:false,purpose:'service-stop',expected:r.expected};
  fence={fenceId:'fence-'+r.commandId,commandId:r.commandId,purpose:'service-stop',instanceId:r.expected.instanceId,dataScope:binding.dataScope,serviceIdentity:r.expected,phase:'held',owners};
  return {...fence,admitted:true,intakeClosed:true,expected:r.expected,evidence:{activeWork:0,intakeClosed:true,instanceId:r.expected.instanceId,dataScope:binding.dataScope,observedAt:Date.now()}};
 },inspectServiceLifecycle:async()=>({fence}),serviceStopReceipt:async()=>fence,
 releaseServiceStop:async r=>{calls.release++;if(failRelease)throw Error('lost_reply');const p=await verifier({...r,instanceId:fence.instanceId,dataScope:binding.dataScope,purpose:'service-stop',serviceIdentity:fence.serviceIdentity});return {released:true,intakeClosed:false,purpose:'service-stop',...r,expected:p.expected,observed:p.observed,receiptId:p.receiptId};}};
 function create(name){
  const lifecycle=new PosixOwnedProcessLifecycle({ownerId:binding.ownerId,stopMs:1000,readinessMs:1000,inspect,admitRestart:async()=>{throw Error('wrong_admission');},resolve:async()=>{calls.launch++;return spec;}});all.push(lifecycle);
  const options={directory:join(root,name),...binding,lifecycle,host,releases:{verify:async()=>verify},currentRelease:()=>target};
  let service=new ServiceLifecycleOwner(options);services.push(service);
  return {lifecycle,get service(){return service;},select(){current=service;currentLifecycle=lifecycle;},async reopen(){await service.close();service=new ServiceLifecycleOwner(options);services.push(service);this.select();}};
 }
 const spec={command:process.execPath,args:[entry],env:{EXISTING_DATA:data,READY_FILE:ready}};
 const source=create('source');source.select();
 // This fixture owns a genuinely launched child through its IPC channel. It
 // never labels existing application files as pristine initial provisioning.
 await source.lifecycle.processes.start(spec,{instanceId:'source-instance',dataScope:binding.dataScope,releaseDigest:release.digest},new AbortController().signal);
 await until(()=>inspect());source.lifecycle.processes.confirmReady({instanceId:'source-instance',dataScope:binding.dataScope,releaseDigest:release.digest});
 const expected={...binding,instanceId:'source-instance',releaseDigest:release.digest};
 const original=createOwnedServiceHandoffSource({service:source.service,bindings});
 const sourcePort={claim:async r=>{calls.source++;return original.claim(r);}};
 const command={commandId:'handoff',stoppedCommandId:'source-stop',expected,target:release,participantIds:owners};
 async function stopSource(){source.select();source.service.stop({commandId:'source-stop',expected});const r=await source.service.waitFor('source-stop');await tick();return r;}
 async function handoff(dest,extra={}){dest.select();await launchExistingStateHandoff({destination:dest.service,source:sourcePort,command,bindings,...extra});return dest.service.waitFor(extra.command?.commandId??command.commandId);}
 t.after(async()=>{for(const s of services)await s.close();for(const l of all)await l.close().catch(()=>{});await rm(root,{recursive:true,force:true});});
 return {root,data,config,bindings,source,sourcePort,command,expected,calls,create,stopSource,handoff,setBusy:v=>busy=v,setReleaseFailure:v=>failRelease=v,setVerify:v=>verify=v};
}

test('existing-state handoff retires exact old authority, preserves data and starts one new owned child',async t=>{
 const f=await fixture(t);const stopped=await f.stopSource();assert.equal(stopped.status,'stopped');assert.deepEqual(stopped.qualifiedOwners,owners);
 const oldProof=stopped.exitProof, dest=f.create('destination'), result=await f.handoff(dest);
 assert.equal(result.operation,'handoff');assert.equal(result.status,'ready');assert.equal(result.admissionSettlement.state,'settled');
 assert.deepEqual(result.exitProof,oldProof);assert.equal(result.observed.releaseDigest,f.expected.releaseDigest);assert.equal(result.observed.dataScope,f.expected.dataScope);assert.notEqual(result.observed.instanceId,f.expected.instanceId);
 assert.equal(dest.lifecycle.processes.inspect().state,'running');assert.equal(f.source.lifecycle.processes.inspect().state,'exited');
 assert.equal((await f.source.service.inspect()).state,'retired');assert.equal(f.source.service.blocksUpdates(),true);
 assert.equal(f.source.service.resume({commandId:'old-resume',stoppedCommandId:'source-stop',expected:f.expected}).errorCode,'service_authority_retired');
 assert.equal(await readFile(join(f.data,'history'),'utf8'),'saved conversation\n');assert.equal(JSON.parse(await readFile(join(f.root,'ready.json'),'utf8')).text,'saved conversation\n');
 assert.equal(f.calls.source,1);assert.equal(f.calls.launch,1);
 await f.handoff(dest);assert.equal(f.calls.source,1);assert.equal(f.calls.launch,1);
 assert.doesNotMatch(JSON.stringify(result),new RegExp(f.root));
 // The newly supervised host supports the same ordinary stop/refusal/resume.
 f.setBusy(true);dest.service.stop({commandId:'busy',expected:result.observed});assert.equal((await dest.service.waitFor('busy')).errorCode,'service_busy');
 f.setBusy(false);dest.service.stop({commandId:'dest-stop',expected:result.observed});assert.equal((await dest.service.waitFor('dest-stop')).status,'stopped');
 dest.service.resume({commandId:'dest-resume',expected:result.observed,stoppedCommandId:'dest-stop'});const resumed=await dest.service.waitFor('dest-resume');
 assert.equal(resumed.status,'ready');assert.equal(resumed.admissionSettlement.state,'settled');assert.notEqual(resumed.observed.instanceId,result.observed.instanceId);
});

test('source retirement persists, refuses a second destination and does not adopt an observed child',async t=>{
 const f=await fixture(t);await f.stopSource();const first=f.create('first');assert.equal((await f.handoff(first)).status,'ready');
 const second=f.create('second');assert.equal((await f.handoff(second)).status,'unknown');assert.equal(second.lifecycle.ownedPid,null);assert.equal(f.calls.launch,1);
 await f.source.reopen();assert.equal((await f.source.service.inspect()).state,'retired');assert.equal(f.source.service.blocksUpdates(),true);
 assert.equal(f.source.service.resume({commandId:'after-reopen',expected:f.expected,stoppedCommandId:'source-stop'}).status,'refused');
});

test('lost source-claim response is durable uncertainty and never repeats or launches on reopen',async t=>{
 const f=await fixture(t);await f.stopSource();const dest=f.create('destination');
 const source={claim:async r=>{await f.sourcePort.claim(r);throw Error('reply_lost');}};
 const result=await f.handoff(dest,{source});assert.equal(result.status,'unknown');assert.equal(result.phase,'handoff_requested');assert.equal(f.calls.launch,0);
 assert.equal((await f.source.service.inspect()).state,'retired');await dest.reopen();assert.equal(dest.service.blocksUpdates(),true);
 await f.handoff(dest,{source});await dest.service.reconcile('handoff');assert.equal(f.calls.source,1);assert.equal(f.calls.launch,0);
 dest.service.resume({commandId:'not-resume',stoppedCommandId:'source-stop',expected:f.expected});assert.equal((await dest.service.waitFor('not-resume')).status,'refused');
});

test('lost host release response reconciles exact new readiness without a second spawn',async t=>{
 const f=await fixture(t);await f.stopSource();f.setReleaseFailure(true);const dest=f.create('destination');const result=await f.handoff(dest);
 assert.equal(result.status,'ready');assert.equal(result.admissionSettlement.state,'unknown');f.setReleaseFailure(false);await tick();
 assert.equal((await dest.service.reconcile('handoff')).admissionSettlement.state,'settled');assert.equal(f.calls.launch,1);
});

test('handoff refuses an upgrade, wrong scope, incomplete owner census, or unverified bytes',async t=>{
 for(const variant of ['upgrade','scope','owners','bytes'])await t.test(variant,async t=>{
  const f=await fixture(t);await f.stopSource();const dest=f.create('destination');
  if(variant==='upgrade')await assert.rejects(f.handoff(dest,{command:{...f.command,target:{...release,digest:'b'.repeat(64)}}}),/binding_invalid/);
  if(variant==='scope')await assert.rejects(f.handoff(dest,{command:{...f.command,expected:{...f.expected,dataScope:'other'}}}),/binding_conflict/);
  if(variant==='owners')assert.equal((await f.handoff(dest,{command:{...f.command,participantIds:[owners[0]]}})).status,'unknown');
  if(variant==='bytes'){f.setVerify(false);assert.equal((await f.handoff(dest)).errorCode,'retained_release_unverified');}
  assert.equal(f.calls.launch,0);assert.equal(f.source.service.receipt('source-stop').handoffRetired,undefined);
 });
});

test('missing authentic source stop or claimed retirement cannot launch',async t=>{
 const f=await fixture(t);const dest=f.create('destination');assert.equal((await f.handoff(dest)).status,'unknown');assert.equal(f.calls.launch,0);
 assert.equal(f.source.lifecycle.processes.inspect().state,'running');
});

test('binding retains roots but detects changed configuration, replaced directory and links',async t=>{
 const f=await fixture(t);const before=await inspectExistingStateBindings(f.bindings);
 await writeFile(join(f.data,'ordinary-work'),'new data');assert.equal(await inspectExistingStateBindings(f.bindings),before);
 await writeFile(f.config,'{"dataScope":"other-scope"}');assert.notEqual(await inspectExistingStateBindings(f.bindings),before);
 await rename(f.data,f.data+'-old');await mkdir(f.data);assert.notEqual(await inspectExistingStateBindings(f.bindings),before);
 await symlink(f.data,join(f.root,'linked'));await assert.rejects(inspectExistingStateBindings([{id:'bad',path:join(f.root,'linked'),kind:'directory'}]),/binding_invalid/);
});

test('changed data binding between destination and source refuses without consuming source',async t=>{
 const f=await fixture(t);await f.stopSource();const dest=f.create('destination');
 const source={claim:async r=>{await writeFile(f.config,'changed');return f.sourcePort.claim(r);}};
 assert.equal((await f.handoff(dest,{source})).status,'unknown');assert.equal(f.calls.launch,0);assert.equal(f.source.service.receipt('source-stop').handoffRetired,undefined);
});

test('invalid source retirement or exact-exit proof never authorizes destination launch',async t=>{
 for(const field of ['handoffRetired','exitProof'])await t.test(field,async t=>{
  const f=await fixture(t);await f.stopSource();const dest=f.create('destination');
  const source={claim:async r=>{const p=await f.sourcePort.claim(r);if(field==='handoffRetired')delete p.stopped.handoffRetired;else p.stopped.exitProof.instanceId='other-instance';return p;}};
  assert.equal((await f.handoff(dest,{source})).status,'unknown');assert.equal(f.calls.launch,0);assert.equal((await f.source.service.inspect()).state,'retired');
 });
});

test('a destination with prior authority is refused before source retirement',async t=>{
 const f=await fixture(t);await f.stopSource();const dest=f.create('destination');
 dest.service.adopt({commandId:'prior',expected:f.expected});
 const result=await f.handoff(dest);assert.equal(result.errorCode,'existing_state_fresh_owner_required');assert.equal(f.calls.source,0);assert.equal(f.calls.launch,0);
});

test('failed fresh child retains bounded startup diagnostics and cannot be replayed',async t=>{
 const f=await fixture(t);await f.stopSource();
 // Fault injection for the port-level test. Installed tests separately verify
 // immutable signed bytes; no production release verification is bypassed.
 await writeFile(join(f.root,'app.mjs'),"await import('./missing-fixture-dependency.mjs');");
 const dest=f.create('destination');const result=await f.handoff(dest);assert.equal(result.status,'unknown');assert.equal(result.phase,'resume_requested');
 assert.equal(result.startupFailure.reason,'dependency_unavailable');assert.equal((await f.source.service.inspect()).state,'retired');
 await dest.reopen();await f.handoff(dest);await dest.service.reconcile('handoff');assert.equal(f.calls.launch,1);assert.equal(f.calls.source,1);
 assert.doesNotMatch(JSON.stringify(result),/missing-fixture-dependency|\.mjs/);
});
