import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,realpath,readFile,writeFile,rm,chmod} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {once} from 'node:events';
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
const api=await import(process.env.DISTRIBUTION_OWNER_MODULE??'../dist/index.js');
const {createManualSystemdHandoffSource,inspectExistingStateBindings,createManualIngressGate}=api;
const fixtureURL=new URL('./manual-systemd-fixture.mjs',import.meta.url);
async function until(fn){for(let i=0;i<250;i++){if(await fn())return;await new Promise(r=>setTimeout(r,10));}throw Error('fixture_timeout');}
async function setup(t,extra={}){
 const root=await realpath(await mkdtemp(join(tmpdir(),'manual-'))),source=join(root,'s'),data=join(root,'data');await mkdir(data);
 await writeFile(join(data,'history'),'existing history');
 const bindings=[{id:'data',kind:'directory',path:data}],target={identity:{id:'fixture',version:'1.0.0',revision:'a'.repeat(40),digest:'a'.repeat(64)},handle:'fixture'};
 const config={root,source,data,bindings,target,api:process.env.DISTRIBUTION_OWNER_MODULE??new URL('../dist/index.js',import.meta.url).href,...extra};
 const file=join(root,'config.json');await writeFile(file,JSON.stringify(config));
 let child,exit,stderr='';const start=()=>{child=spawn(process.execPath,[fileURLToPath(fixtureURL),file],{stdio:['ignore','ignore','pipe']});child.stderr.on('data',x=>stderr+=x);exit=once(child,'exit');};start();
 t.after(async()=>{if(child.exitCode===null&&child.signalCode===null)child.kill('SIGTERM');await exit;await rm(root,{recursive:true,force:true});});
 await until(async()=>{try{await readFile(join(root,'ready.json'));return true;}catch{if(child.exitCode!==null)throw Error(stderr);return false;}});
 const ready=JSON.parse(await readFile(join(root,'ready.json'),'utf8'));
 const claim={commandId:'handoff',stoppedCommandId:'source-stop',expected:ready.expected,next:{...ready.expected,instanceId:'new-instance'},target:target.identity,
   participantIds:['ingress'],dataBindingDigest:await inspectExistingStateBindings(bindings)};
 // Contract fixture only: Linux test separately exercises actual pidfd/systemd.
 const observer={capture:async()=>ready.witness,bind:async()=>({witness:ready.witness,exited:exit.then(()=>{}),close(){}}),confirmExited:async()=>assert.notEqual(child.exitCode,null)};
 const sourcePort=createManualSystemdHandoffSource({sourceDirectory:source,claimDirectory:join(root,'claim'),bindings,observer});
 return {root,source,bindings,claim,sourcePort,ready,config,start,exit:()=>exit,child:()=>child,stderr:()=>stderr};
}
test('manual launcher retires before closing real ingress, attests exact exit and preserves roots',async t=>{
 const f=await setup(t),proof=await f.sourcePort.claim(f.claim);assert.equal(proof.stopped.handoffRetired,true);assert.deepEqual(proof.stopped.qualifiedOwners,['ingress']);
 assert.equal(proof.stopped.exitProof.instanceId,'old-instance');assert.equal(await readFile(join(f.root,'data/history'),'utf8'),'existing history');
 const events=(await readFile(join(f.root,'events.jsonl'),'utf8')).trim().split('\n').map(JSON.parse);
 assert.deepEqual(events.map(e=>e.event),['held','closing','closed']);assert.equal(events[1].retired,true);assert.equal(events[1].gate.held.fenceId,proof.stopped.fenceId);
 assert.deepEqual(await f.sourcePort.claim(f.claim),proof);await assert.rejects(f.sourcePort.claim({...f.claim,next:{...f.claim.next,instanceId:'other'}}),/consumed_or_unknown/);
 f.start();const [code]=await f.exit();assert.notEqual(code,0);assert.match(f.stderr(),/EEXIST/);
});
test('busy source stays running and cannot fabricate stop proof',async t=>{
 const f=await setup(t,{busy:true});await assert.rejects(f.sourcePort.claim(f.claim),/manual_source_busy/);assert.equal(f.child().exitCode,null);
 await assert.rejects(f.sourcePort.claim(f.claim),/consumed_or_unknown/);
});
test('incomplete held census keeps authority unresolved and never closes source',async t=>{
 const f=await setup(t,{missingOwner:true});await assert.rejects(f.sourcePort.claim(f.claim),/census/);assert.equal(f.child().exitCode,null);
 assert.equal(JSON.parse(JSON.parse(await readFile(join(f.source,'authority.json'),'utf8')).text).phase,'admission_requested');
});
test('binding/owner mismatch refuses before host hold',async t=>{
 for(const mode of ['owners','configuration'])await t.test(mode,async t=>{
  const f=await setup(t),claim=structuredClone(f.claim);if(mode==='owners')claim.participantIds=['wrong'];else claim.dataBindingDigest='b'.repeat(64);
  await assert.rejects(f.sourcePort.claim(claim),/binding_mismatch/);assert.equal(f.child().exitCode,null);
  await assert.rejects(readFile(join(f.root,'events.jsonl')),e=>e.code==='ENOENT');
 });
});
test('tampered source authentication rejects without retirement',async t=>{
 const f=await setup(t);await writeFile(join(f.source,'key'),Buffer.alloc(32,42));await assert.rejects(f.sourcePort.claim(f.claim),/unconfirmed/);assert.equal(f.child().exitCode,null);
});
test('closed receipt without actual exit or missing closure never yields a proof',async t=>{
 const f=await setup(t,{closeFailure:true});let settled=false;const pending=f.sourcePort.claim(f.claim).finally(()=>settled=true);pending.catch(()=>{});
 await until(async()=>{try{return (await readFile(join(f.root,'events.jsonl'),'utf8')).includes('closing');}catch{return false;}});
 assert.equal(settled,false);f.child().kill('SIGTERM');await assert.rejects(pending,/closure_unconfirmed/);
 await assert.rejects(f.sourcePort.claim(f.claim),/consumed_or_unknown/);
});
test('ingress fence blocks new work and survives a new gate instance',async t=>{
 const root=await realpath(await mkdtemp(join(tmpdir(),'manual-gate-')));t.after(()=>rm(root,{recursive:true,force:true}));
 const config={directory:join(root,'gate'),id:'ingress'},gate=await createManualIngressGate(config),c={purpose:'service-stop',fenceId:'f',commandId:'c',instanceId:'old',dataScope:'scope',serviceIdentity:{installationId:'fixture',ownerId:'fixture',instanceId:'old',dataScope:'scope',releaseDigest:'a'.repeat(64)}};
 await assert.rejects(createManualIngressGate(config),/locked/);
 const done=gate.enter();assert.equal(await gate.participant.acquire(c),null);done();done();
 const lease=await gate.participant.acquire(c);assert.equal(gate.enter(),null);gate.close();const next=await createManualIngressGate(config);t.after(()=>next.close());assert.equal(next.enter(),null);
 await assert.rejects(next.participant.reconcileRelease({...c,proof:{verified:true,kind:'service-lifecycle'}}),/unconfirmed/);
 await next.participant.reconcileRelease({...c,outcome:'ready',proof:{verified:true,kind:'service-lifecycle',fenceId:'f',commandId:'c',dataScope:'scope',instanceId:'new',outcome:'ready',receiptId:'receipt',serviceOutcome:'resumed',exitReceiptId:'exit',readyReceiptId:'ready',resumeCommandId:'resume',expected:c.serviceIdentity,observed:{...c.serviceIdentity,instanceId:'new'}}});const admitted=next.enter();assert.equal(typeof admitted,'function');admitted();
});

for(const purpose of ['retention-hide','managed-files-disposal'])test(`${purpose} holds ingress authority while receipt requests continue and business writes remain fenced`,async t=>{
 const root=await realpath(await mkdtemp(join(tmpdir(),'ingress-maint-'))),gate=await createManualIngressGate({directory:join(root,'gate'),id:'ingress'});
 const context={purpose,fenceId:'maintenance',commandId:'command',instanceId:'instance',dataScope:'scope'};
 let lease,businessHeld=false;
 const server=createServer(async(req,res)=>{
  const done=gate.enter();assert.equal(typeof done,'function');res.once('close',done);
  if(req.url==='/begin'){
   // Hold is requested inside a real HTTP lifetime, as in the maintenance UI.
   lease=await gate.participant.acquire(context);businessHeld=true;res.end('accepted');
  }else if(req.url==='/write'&&businessHeld)res.writeHead(409).end('business owner fenced');
  else res.end('receipt available');
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin='http://127.0.0.1:'+server.address().port;
 t.after(async()=>{await new Promise(r=>server.close(r));gate.close();await rm(root,{recursive:true,force:true});});
 assert.equal(await (await fetch(origin+'/begin')).text(),'accepted');assert.ok(lease);
 assert.equal(await (await fetch(origin+'/receipt')).text(),'receipt available');assert.equal((await fetch(origin+'/write')).status,409);
 const stop={purpose:'service-stop',fenceId:'stop',commandId:'stop',instanceId:'instance',dataScope:'scope',serviceIdentity:{installationId:'i',ownerId:'o',instanceId:'instance',dataScope:'scope',releaseDigest:'a'.repeat(64)}};
 assert.equal(await gate.participant.acquire(stop),null);
 const request={sessions:['ahp-session:/fixture'],limit:101,...(purpose==='managed-files-disposal'?{allocation:{allocationId:'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',allocationHash:'a'.repeat(64),treeHash:'b'.repeat(64),bytes:0,entryCount:0,executionDirectory:join(root,'managed')}}:{})};
 const inspect=purpose==='managed-files-disposal'?lease.inspectManagedFilesReferences:lease.inspectRetentionReferences;
 assert.deepEqual(await inspect(request),{coverage:'complete',protected:[],omissions:[]});
 await assert.rejects(inspect({...request,sessions:['ahp-session:/other']}),/selection_changed/);
 if(request.allocation)await assert.rejects(inspect({...request,allocation:{...request.allocation,executionDirectory:root}}),/state_protected/);
 await lease.release('unknown');await assert.rejects(inspect(request),/not_live/);
 await assert.rejects(gate.participant.reconcileRelease({...context,outcome:'unchanged',proof:{kind:'admission-refused'}}),/unconfirmed/);
 assert.equal(await (await fetch(origin+'/receipt')).text(),'receipt available');assert.equal(await gate.participant.acquire(stop),null);
 await gate.participant.reconcileRelease({...context,outcome:'unchanged',proof:{verified:true,receiptId:'receipt',...context,outcome:'unchanged'}});businessHeld=false;
 assert.equal((await fetch(origin+'/write')).status,200);
});
