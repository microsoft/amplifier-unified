import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,realpath,rm,writeFile,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {legacyRecoveryDigest as digest,prepareLegacyProcessRecovery,inspectLegacyProcessRecovery,observeLegacyProcessRecovery,
  createLegacyMaintenanceReleaseVerifier} from '../dist/index.js';

// Complete legacy census fixture, not a claim to instantiate all business owners.
const ids=['portability','capability:attachments','workspaces','native-admin','native-message-metadata',
  'application-updates','capability:voice','capability:connectors','notifications','terminal','diagnostics',
  'capability:observations','capability:coordination','capability:worktrees','capability:publishing',
  'capability:recall','capability:feedback','recovery','history-import','history-cleanup','managed-files','manual-preview-ingress'];
async function setup(t){
  const root=await realpath(await mkdtemp(join(tmpdir(),'legacy-maintenance-')));t.after(()=>rm(root,{recursive:true,force:true}));
  const directory=join(root,'authority'),history=join(root,'transcript.jsonl');await writeFile(history,'{"role":"user","content":"saved"}\n');
  const b={expected:{installationId:'existing',ownerId:'supervisor',dataScope:'saved',instanceId:'old',releaseDigest:'a'.repeat(64)},
    original:{commandId:'original-update',fenceId:'original-hold',receiptDigest:digest({status:'unknown'})},
    prepared:{id:'replacement',version:'1.0.1',revision:'b'.repeat(40),digest:'b'.repeat(64)},
    configurationDigest:digest('unchanged config'),owners:ids.map(id=>({id,bindingDigest:digest({id,root:'existing'})})),
    historyDigest:digest(await readFile(history,'utf8')),unknownOutcomesDigest:digest(['unconfirmed external work'])};
  const retained={binding:structuredClone(b),original:{status:'unknown',phase:'admission_requested',admission:null,admittedRunning:null,activation:null},
    fence:{commandId:b.original.commandId,fenceId:b.original.fenceId,instanceId:'old',dataScope:'saved',purpose:'distribution-update',phase:'unknown',owners:ids}};
  const events=[];let stopped=false,observed=null,qualified=true,interruptError=false,startError=false,custodyError=false;
  const ports={inspectRetained:async()=>structuredClone(retained),verifyPrepared:async()=>qualified,
    bindCustody:async()=>({witnessDigest:digest('kernel witness'),exited:Promise.resolve(),
      confirmExited:async()=>{events.push('confirm');if(!stopped||custodyError)throw Error('custody unconfirmed');},close:()=>events.push('close')}),
    interruptService:async()=>{events.push('interrupt');stopped=true;if(interruptError)throw Error('lost stop reply');},
    startRetained:async r=>{events.push('start');observed={...b.expected,instanceId:r.instanceId,releaseDigest:b.prepared.digest,identity:b.prepared,ready:true};if(startError)throw Error('lost start reply');},
    inspectReplacement:async()=>structuredClone(observed),assertReplacementHeld:async()=>{}};
  const prepare=()=>prepareLegacyProcessRecovery({directory,recoveryId:'maintenance',expected:b,ports});
  const releaseRequest={...retained.fence,outcome:'ready',evidence:{forged:true}};
  const verifier=()=>createLegacyMaintenanceReleaseVerifier({binding:b,receipt:()=>inspectLegacyProcessRecovery(directory),
    inspectRunning:async()=>observed&&({instanceId:observed.instanceId,dataScope:observed.dataScope,identity:observed.identity,ready:observed.ready}),verifyPrepared:ports.verifyPrepared});
  return {root,directory,b,retained,history,events,ports,prepare,verifier,releaseRequest,
    set stopError(v){interruptError=v;},set startError(v){startError=v;},set custodyError(v){custodyError=v;},
    set qualified(v){qualified=v;},get observed(){return observed;}};
}
test('separate interruption and genuine ready preserve all22 bindings, history, original unknown; existing release shape only',async t=>{
  const f=await setup(t),before=structuredClone(f.retained),c=await f.prepare();
  assert.equal(c.receipt().originalDisposition,'unsettled');
  await c.interrupt();await c.confirmStopped();await c.startReplacement();const receipt=await c.observeReplacement();
  assert.equal(receipt.originalOutcome,'unknown');assert.equal(receipt.originalDisposition,'interrupted');assert.equal(receipt.phase,'ready');
  assert.deepEqual(f.retained,before);assert.equal(digest(await readFile(f.history,'utf8')),f.b.historyDigest);
  const proof=await f.verifier()(f.releaseRequest);
  assert.deepEqual(proof,{verified:true,fenceId:'original-hold',commandId:'original-update',outcome:'ready',instanceId:receipt.nextInstanceId,dataScope:'saved',receiptId:'maintenance'});
  assert.equal('admission' in receipt,false);assert.equal('admittedRunning' in receipt,false);assert.equal('activeWork' in proof,false);
  assert.equal(f.events.filter(e=>e==='start').length,1);c.close();
});
test('unknown custody, current/next identity, newer fence and missing owner refuse without stop',async t=>{
  for(const mode of ['custody','instance','scope','newer-fence','owner','history','unknown-work','prepared'])await t.test(mode,async t=>{
    const f=await setup(t);
    if(mode==='custody')f.ports.bindCustody=async()=>{throw Error('custody ambiguous');};
    else if(mode==='instance')f.retained.binding.expected.instanceId='different';
    else if(mode==='scope')f.retained.binding.expected.dataScope='different';
    else if(mode==='newer-fence')f.retained.fence.fenceId='newer';
    else if(mode==='owner')f.retained.fence.owners=f.retained.fence.owners.slice(1);
    else if(mode==='history')f.retained.binding.historyDigest=digest('changed');
    else if(mode==='unknown-work')f.retained.binding.unknownOutcomesDigest=digest('changed');
    else f.qualified=false;
    await assert.rejects(f.prepare());assert.deepEqual(f.events,[]);
  });
});
test('stop/start lost acknowledgements never repeat effects; passive original observation remains possible',async t=>{
  const f=await setup(t),c=await f.prepare();f.stopError=true;
  await assert.rejects(c.interrupt(),/lost stop/);await assert.rejects(c.interrupt(),/already_requested/);
  assert.equal((await inspectLegacyProcessRecovery(f.directory)).phase,'interrupt_requested');
  await c.confirmStopped();f.startError=true;await assert.rejects(c.startReplacement(),/lost start/);
  await assert.rejects(c.startReplacement(),/not_authorized/);const r=await c.observeReplacement();assert.equal(r.phase,'ready');
  assert.equal(f.events.filter(e=>e==='interrupt').length,1);assert.equal(f.events.filter(e=>e==='start').length,1);
  assert.deepEqual(await c.observeReplacement(),r);c.close();await assert.rejects(f.prepare(),/EEXIST/);
});
test('surviving descendant/expired exclusion leaves interruption unknown and cannot start',async t=>{
  const f=await setup(t),c=await f.prepare();await c.interrupt();f.custodyError=true;
  await assert.rejects(c.confirmStopped(),/custody/);assert.equal(c.receipt().phase,'interrupt_requested');
  await assert.rejects(c.startReplacement(),/not_authorized/);assert.equal(f.events.includes('start'),false);c.close();
});
test('a changed retained fence after qualified exit blocks launch; lost controller is passive only',async t=>{
  const f=await setup(t),c=await f.prepare();await c.interrupt();await c.confirmStopped();
  f.retained.fence.fenceId='newer';await assert.rejects(c.startReplacement(),/fence/);assert.equal(f.events.includes('start'),false);
  c.close();assert.equal((await inspectLegacyProcessRecovery(f.directory)).phase,'stopped');
  await assert.rejects(c.startReplacement(),/unavailable/);await assert.rejects(f.prepare());
});
test('release needs exact original22 census plus authenticated new ready, signed target and immutable receipt',async t=>{
  const f=await setup(t),c=await f.prepare();await c.interrupt();await c.confirmStopped();await c.startReplacement();await c.observeReplacement();
  const verify=f.verifier();
  for(const patch of [{fenceId:'newer'},{commandId:'other'},{instanceId:'new'},{dataScope:'other'},
    {outcome:'unchanged'},{owners:ids.slice(1)},{owners:[...ids].reverse()}])await assert.rejects(verify({...f.releaseRequest,...patch}),/unconfirmed/);
  f.observed.ready=false;await assert.rejects(verify(f.releaseRequest),/unconfirmed/);f.observed.ready=true;
  f.observed.instanceId='unrelated';await assert.rejects(verify(f.releaseRequest),/unconfirmed/);f.observed.instanceId=c.receipt().nextInstanceId;
  f.qualified=false;await assert.rejects(verify(f.releaseRequest),/unconfirmed/);f.qualified=true;
  await writeFile(join(f.directory,'key'),Buffer.alloc(32,3));await assert.rejects(verify(f.releaseRequest),/authentication_failed/);c.close();
});
test('concurrent launch attempts serialize once, duplicate controller cannot claim the same authority',async t=>{
  const f=await setup(t),c=await f.prepare();await assert.rejects(f.prepare(),/EEXIST/);
  await c.interrupt();await c.confirmStopped();
  const results=await Promise.allSettled([c.startReplacement(),c.startReplacement()]);
  assert.equal(results.filter(r=>r.status==='fulfilled').length,1);assert.equal(f.events.filter(e=>e==='start').length,1);c.close();
});
test('controller loss after retained launch permits only exact passive readiness reconciliation, never another launch',async t=>{
  const f=await setup(t),c=await f.prepare();await c.interrupt();await c.confirmStopped();await c.startReplacement();
  const ports={inspectRetained:f.ports.inspectRetained,verifyPrepared:f.ports.verifyPrepared,inspectReplacement:f.ports.inspectReplacement,
    assertReplacementHeld:f.ports.assertReplacementHeld};
  const observe=()=>observeLegacyProcessRecovery({directory:f.directory,expected:f.b,ports});
  await assert.rejects(observe(),/locked/);c.close();
  f.observed.ready=false;await assert.rejects(observe(),/unconfirmed/);assert.equal((await inspectLegacyProcessRecovery(f.directory)).phase,'restart_requested');
  f.observed.ready=true;ports.assertReplacementHeld=async()=>{throw Error('replacement locks not held');};
  await assert.rejects(observe(),/locks not held/);assert.equal((await inspectLegacyProcessRecovery(f.directory)).phase,'restart_requested');
  ports.assertReplacementHeld=f.ports.assertReplacementHeld;
  const next=await observe();assert.equal(next.phase,'ready');assert.equal(next.originalOutcome,'unknown');
  assert.deepEqual(await observe(),next);assert.equal(f.events.filter(e=>e==='start').length,1);
  f.observed.instanceId='unrelated';await assert.rejects(observe(),/unconfirmed/);
  await assert.rejects(f.prepare(),/EEXIST/);
});
