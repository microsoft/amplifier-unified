import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,realpath,rm,writeFile,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createServer} from 'node:http';
const {createManualIngressGate}=await import(process.env.DISTRIBUTION_OWNER_MODULE??'../dist/index.js');
const context=(purpose='recovery')=>({purpose,fenceId:'held',commandId:'job',instanceId:'original',dataScope:'scope'});
const proof=c=>({verified:true,fenceId:c.fenceId,commandId:c.commandId,instanceId:c.instanceId,dataScope:c.dataScope,outcome:'unchanged',receiptId:'original-recovery-receipt'});
const stop={purpose:'service-stop',fenceId:'stop',commandId:'stop',instanceId:'original',dataScope:'scope',serviceIdentity:{installationId:'fixture',ownerId:'fixture',instanceId:'original',dataScope:'scope',releaseDigest:'a'.repeat(64)}};
async function directory(t){const root=await realpath(await mkdtemp(join(tmpdir(),'ingress-recovery-')));t.after(()=>rm(root,{recursive:true,force:true}));return root;}

test('recovery permits response forwarding, blocks competing stop, and reconciles exact unchanged receipt after reopen',async t=>{
 const root=await directory(t),options={directory:join(root,'gate'),id:'ingress'};let gate=await createManualIngressGate(options);
 t.after(()=>gate.close());const c=context(),done=gate.enter(),lease=await gate.participant.acquire(c);
 assert.ok(lease);assert.equal(lease.inspectRetentionReferences,undefined);assert.equal(lease.inspectManagedFilesReferences,undefined);
 const receipt=gate.enter();assert.equal(typeof receipt,'function');receipt();done();
 assert.equal(await gate.participant.acquire(stop),null);
 assert.equal(await gate.participant.acquire({...context('distribution-update'),fenceId:'update'}),null);
 await lease.release('unknown');gate.close();gate=await createManualIngressGate(options);
 const receiptAfterReopen=gate.enter();assert.equal(typeof receiptAfterReopen,'function');receiptAfterReopen();
 assert.equal(await gate.participant.acquire(stop),null);
 await assert.rejects(gate.participant.reconcileRelease({...c,outcome:'unchanged',proof:{kind:'admission-refused'}}),/unconfirmed/);
 for(const p of [{...proof(c),verified:false},{...proof(c),fenceId:'wrong'},{...proof(c),commandId:'wrong'},{...proof(c),instanceId:'wrong'},
  {...proof(c),dataScope:'wrong'},{...proof(c),receiptId:''},{...proof(c),outcome:'ready',instanceId:'replacement'}]){
  await assert.rejects(gate.participant.reconcileRelease({...c,outcome:p.outcome,proof:p}),/unconfirmed/);
 }
 await gate.participant.reconcileRelease({...c,outcome:'unchanged',proof:proof(c)});
 await gate.participant.reconcileRelease({...c,outcome:'unchanged',proof:proof(c)});
 await assert.rejects(gate.participant.reconcileRelease({...c,outcome:'unchanged',proof:{...proof(c),receiptId:'different'}}),/changed/);
 assert.equal(gate.inspect().held,null);const stopping=await gate.participant.acquire(stop);assert.ok(stopping);assert.equal(gate.enter(),null);
 await stopping.release('unchanged',{kind:'admission-refused'});
});

test('distribution update drains all forwarding and wakes admission immediately on the last completion',async t=>{
 const root=await directory(t);let idle=0;const gate=await createManualIngressGate({directory:join(root,'gate'),id:'ingress',onMayBeIdle:()=>idle++});t.after(()=>gate.close());
 const c=context('distribution-update'),response=gate.enter(),socket=gate.enter();
 assert.equal(await gate.participant.acquire(c),null);response();response();await Promise.resolve();assert.equal(idle,0);
 assert.equal(await gate.participant.acquire(c),null);socket();await Promise.resolve();assert.equal(idle,1);
 const retry={...c,fenceId:'fresh-update-attempt'};
 const lease=await gate.participant.acquire(retry);assert.ok(lease);assert.equal(gate.enter(),null);
 await lease.release('unknown');assert.equal(gate.enter(),null);
 await gate.participant.reconcileRelease({...retry,outcome:'ready',proof:{...proof(retry),outcome:'ready',instanceId:'replacement'}});
 const next=gate.enter();assert.equal(typeof next,'function');next();
});

test('unsupported purposes never acquire ingress and live no-effect recovery refusal can roll back',async t=>{
 const root=await directory(t),gate=await createManualIngressGate({directory:join(root,'gate'),id:'ingress'});t.after(()=>gate.close());
 assert.equal(await gate.participant.acquire(context('arbitrary-maintenance')),null);assert.equal(gate.inspect().held,null);
 const c=context(),lease=await gate.participant.acquire(c);await lease.release('unchanged',{kind:'admission-refused'});assert.equal(gate.inspect().held,null);
});

// Optional real public host qualification. A fixture durable recovery receipt
// supplies the host's trusted verification port; ingress cannot verify a user's
// claimed receipt or business mutation itself.
for(const changedInstance of [false,true])test(`installed public host recovery keeps HTTP receipts across reopen (changed instance: ${changedInstance})`,
 {skip:!process.env.DISTRIBUTION_TEST_HOST_MODULE},async t=>{
 const {createHost}=await import(process.env.DISTRIBUTION_TEST_HOST_MODULE),root=await directory(t),gatePath=join(root,'gate');
 let gate,host,server,origin;
 const receiptPath=join(root,'recovery.json');
 async function start(instanceId){
  gate=await createManualIngressGate({directory:gatePath,id:'ingress'});
  host=await createHost({stateDirectory:join(root,'host'),allowedWorkspaceRoots:[root],engines:[{id:'unused',command:'/no-synthetic-engine'}],
   quiescence:{instanceId,dataScope:'scope',requiredOwners:['ingress'],coverage:{},participants:[gate.participant],verifyRelease:async request=>{
    const p=JSON.parse(await readFile(receiptPath,'utf8'));assert.equal(request.evidence.receiptId,p.receiptId);
    assert.equal(request.fenceId,p.fenceId);assert.equal(request.commandId,p.commandId);return p;
   }}});
  server=createServer(async(req,res)=>{const done=gate.enter();if(!done){res.writeHead(503).end();return;}res.once('close',done);
   try{if(req.url==='/begin'){res.end(JSON.stringify(await host.admitQuiescence({commandId:'recovery-http',purpose:'recovery'})));}
    else if(req.url==='/business'&&host.inspectQuiescence().intakeClosed){res.writeHead(409).end();}
    else res.end(JSON.stringify(host.quiescenceReceipt('recovery-http')));
   }catch(e){res.writeHead(500).end(e.message);}
  });await new Promise(r=>server.listen(0,'127.0.0.1',r));origin='http://127.0.0.1:'+server.address().port;
 }
 async function close(){await new Promise(r=>server.close(r));await host.close();gate.close();}
 await start('original');t.after(close);
 const admission=await (await fetch(origin+'/begin')).json();assert.equal(admission.admitted,true,JSON.stringify(admission));
 assert.equal((await fetch(origin+'/business')).status,409);
 const original=await (await fetch(origin+'/receipt')).json();assert.equal(original.fenceId,admission.fenceId);
 const c=gate.inspect().held;await writeFile(receiptPath,JSON.stringify(proof(c)));
 await host.releaseQuiescence({fenceId:c.fenceId,commandId:c.commandId,outcome:'unknown',evidence:{}});
 await close();await start(changedInstance?'new-host-instance':'original');
 assert.equal((await fetch(origin+'/business')).status,409);
 assert.equal((await (await fetch(origin+'/receipt')).json()).fence.fenceId,admission.fenceId);
 assert.equal(await gate.participant.acquire(stop),null);
 const release=()=>host.releaseQuiescence({fenceId:c.fenceId,commandId:c.commandId,outcome:'unchanged',evidence:{receiptId:proof(c).receiptId}});
 if(changedInstance){await assert.rejects(release(),/unknown/);assert.ok(gate.inspect().held);assert.equal((await fetch(origin+'/receipt')).status,200);return;}
 const released=await release();
 assert.equal(released.released,true);assert.equal(gate.inspect().held,null);assert.equal((await fetch(origin+'/business')).status,200);
 });

test('installed HostControl update admission drains ingress then wakes through its real event stream',
 {skip:!process.env.DISTRIBUTION_TEST_HOST_MODULE},async t=>{
 const {createHost}=await import(process.env.DISTRIBUTION_TEST_HOST_MODULE);
 const {serveHostControl,HostControlClient}=await import(process.env.DISTRIBUTION_OWNER_MODULE??'../dist/index.js');
 const root=await directory(t);let control;
 const gate=await createManualIngressGate({directory:join(root,'gate'),id:'ingress',onMayBeIdle:()=>control?.notifyMayBeIdle()});
 const host=await createHost({stateDirectory:join(root,'host'),allowedWorkspaceRoots:[root],engines:[{id:'unused',command:'/no-synthetic-engine'}],
  quiescence:{instanceId:'original',dataScope:'scope',requiredOwners:['ingress'],coverage:{},participants:[gate.participant],verifyRelease:async c=>proof(c)}});
 const key='a'.repeat(64),running={identity:{id:'fixture',version:'1.0.0',revision:'a'.repeat(40),digest:'a'.repeat(64)},instanceId:'original',dataScope:'scope',ready:true};
 control=await serveHostControl({host,inspectRunning:async()=>running,token:key});
 const client=new HostControlClient({dataScope:'scope',connect:()=>({url:control.url,token:key,dataScope:'scope'})});
 t.after(async()=>{client.close();await control.close();await host.close();gate.close();});
 let wake;const initial=new Promise(r=>wake=r);client.onIdle(()=>wake());await initial;
 const c={commandId:'install',purpose:'distribution-update',dataScope:'scope',signal:new AbortController().signal};
 const response=gate.enter(),socket=gate.enter();assert.equal(await client.admitRestart(c),null);
 const idleEvent=new Promise(r=>wake=r);response();assert.equal(await client.admitRestart(c),null);
 socket();await idleEvent;
 const lease=await client.admitRestart(c);assert.ok(lease);assert.equal(gate.enter(),null);
 await lease.release('unchanged');assert.equal(gate.inspect().held,null);assert.equal(host.inspectQuiescence().intakeClosed,false);
 });
