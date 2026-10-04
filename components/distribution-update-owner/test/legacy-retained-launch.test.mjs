import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,realpath,rm,writeFile,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {PosixOwnedProcessLifecycle,createLegacyRetainedLauncher,prepareLegacyProcessRecovery,
  observeLegacyProcessRecovery,inspectLegacyProcessRecovery,legacyRecoveryDigest as digest} from '../dist/index.js';

async function fixture(t){
  const root=await realpath(await mkdtemp(join(tmpdir(),'retained-launch-'))),directory=join(root,'authority');
  const entry=join(root,'child.mjs'),running=join(root,'running.json');
  await writeFile(entry,`import {writeFileSync} from 'node:fs';writeFileSync(process.argv[2],JSON.stringify({instanceId:process.env.AMPLIFIER_DISTRIBUTION_INSTANCE_ID,dataScope:process.env.AMPLIFIER_DISTRIBUTION_DATA_SCOPE}));process.on('SIGTERM',()=>process.exit(0));setTimeout(()=>process.exit(0),8000);`);
  const binding={expected:{installationId:'saved-install',ownerId:'saved-owner',dataScope:'saved-scope',instanceId:'old',releaseDigest:'a'.repeat(64)},
    original:{commandId:'original-update',fenceId:'old-fence',receiptDigest:digest('original-unknown')},
    prepared:{id:'new-release',version:'1.0.2',revision:'b'.repeat(40),digest:'b'.repeat(64)},
    configurationDigest:digest('configuration'),owners:[{id:'owner',bindingDigest:digest('saved-root')}],
    historyDigest:digest('saved-history'),unknownOutcomesDigest:digest('unknown-remote-work')};
  let stopped=false,excluded=true,held=true,qualified=true,launches=0,initials=0,controller;
  const target={identity:binding.prepared,handle:'signed-prepared-target'};
  const inspect=async()=>{
    if(lifecycle.processes.inspect().state!=='running')return null;
    try{return {...JSON.parse(await readFile(running,'utf8')),identity:target.identity,ready:true};}catch{return null;}
  };
  const lifecycle=new PosixOwnedProcessLifecycle({ownerId:binding.expected.ownerId,readinessMs:1500,stopMs:1000,
    resolve:async()=>{launches++;return {command:process.execPath,args:[entry,running]};},inspect,
    admitRestart:async()=>assert.fail('ordinary update admission is forbidden'),
    initialProvisioning:{claim:async()=>{initials++;assert.fail('initial claim is forbidden');}}});
  const retained={binding,original:{status:'unknown',phase:'admission_requested',admission:null,admittedRunning:null,activation:null},
    fence:{commandId:'original-update',fenceId:'old-fence',instanceId:'old',dataScope:'saved-scope',purpose:'distribution-update',phase:'unknown',owners:['owner']}};
  const assertExclusionHeld=async()=>{if(!excluded)throw Error('external exclusion lost');};
  const assertReplacementHeld=async()=>{if(!held)throw Error('replacement gates unconfirmed');};
  const launcher=createLegacyRetainedLauncher({directory,binding,lifecycle,target:async()=>target,verifyPrepared:async()=>qualified,
    assertExclusionHeld,assertRetainedUnchanged:async()=>{},assertReplacementHeld});
  const ports={inspectRetained:async()=>structuredClone(retained),verifyPrepared:async()=>qualified,
    bindCustody:async()=>({witnessDigest:digest('synthetic old custody'),exited:Promise.resolve(),close(){},confirmExited:async()=>{assert.ok(stopped);await assertExclusionHeld();}}),
    interruptService:async()=>{stopped=true;},startRetained:launcher,
    inspectReplacement:async()=>{const r=await inspect();return r?{...binding.expected,...r,releaseDigest:r.identity.digest}:null;},assertReplacementHeld};
  controller=await prepareLegacyProcessRecovery({directory,recoveryId:'maintenance',expected:binding,ports});
  t.after(async()=>{controller.close();await lifecycle.close();await rm(root,{recursive:true,force:true});});
  await controller.interrupt();await controller.confirmStopped();
  return {directory,controller,ports,lifecycle,launcher,binding,get launches(){return launches;},get initials(){return initials;},
    set excluded(v){excluded=v;},set held(v){held=v;},set qualified(v){qualified=v;}};
}

test('retained launcher owns one actual child and never reuses pristine authority',async t=>{
  const f=await fixture(t);await f.controller.startReplacement();const result=await f.controller.observeReplacement();
  assert.equal(result.phase,'ready');assert.equal(result.originalOutcome,'unknown');assert.equal(f.launches,1);assert.equal(f.initials,0);
  assert.equal((await f.lifecycle.inspectOwned()).instanceId,result.nextInstanceId);
  await assert.rejects(f.launcher({recoveryId:result.recoveryId,binding:f.binding,instanceId:result.nextInstanceId}));assert.equal(f.launches,1);
});
test('lost reply after actual child starts is observation-only and rechecks replacement gates',async t=>{
  const f=await fixture(t);f.held=false;
  await assert.rejects(f.controller.startReplacement(),/gates unconfirmed/);
  assert.equal(f.launches,1);assert.equal((await inspectLegacyProcessRecovery(f.directory)).phase,'restart_requested');
  f.controller.close();
  await assert.rejects(observeLegacyProcessRecovery({directory:f.directory,expected:f.binding,ports:f.ports}),/gates unconfirmed/);
  f.held=true;const ready=await observeLegacyProcessRecovery({directory:f.directory,expected:f.binding,ports:f.ports});
  assert.equal(ready.phase,'ready');assert.equal(ready.originalDisposition,'interrupted');assert.equal(f.launches,1);
});
test('consumed retained launch permit cannot launch again after pre-effect guard failure',async t=>{
  const f=await fixture(t);let checks=0;
  const failAfterClaim=createLegacyRetainedLauncher({directory:f.directory,binding:f.binding,lifecycle:f.lifecycle,
    target:async()=>({identity:f.binding.prepared,handle:'target'}),verifyPrepared:async()=>true,
    assertExclusionHeld:async()=>{},assertReplacementHeld:async()=>{},
    assertRetainedUnchanged:async()=>{if(++checks===2)throw Error('retained state changed');}});
  f.ports.startRetained=failAfterClaim;
  await assert.rejects(f.controller.startReplacement(),/retained state changed/);assert.equal(f.launches,0);
  const r=await inspectLegacyProcessRecovery(f.directory);
  await assert.rejects(f.launcher({recoveryId:r.recoveryId,binding:f.binding,instanceId:r.nextInstanceId}),/EEXIST/);
  assert.equal(f.launches,0);assert.equal(f.initials,0);
});
test('exclusion loss or target drift never reaches retained child creation',async t=>{
  for(const mode of ['exclusion','target'])await t.test(mode,async t=>{
    const f=await fixture(t);if(mode==='exclusion')f.excluded=false;else f.qualified=false;
    await assert.rejects(f.controller.startReplacement());assert.equal(f.launches,0);assert.equal(f.initials,0);
  });
});
