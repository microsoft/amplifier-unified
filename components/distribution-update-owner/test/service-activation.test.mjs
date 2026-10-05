import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ServiceLifecycleOwner, createHostServiceReleaseVerifier } from '../dist/index.js';

const release = n => ({id:'artifact-'+n,version:`1.0.${n==='a'?0:1}`,revision:n.repeat(40),digest:n.repeat(64)});
const a={identity:release('a'),handle:'a'}, b={identity:release('b'),handle:'b'};
const binding={installationId:'fixture-installation',ownerId:'fixture-owner',dataScope:'fixture-data'};
const expected={...binding,instanceId:'a-instance',releaseDigest:a.identity.digest};
const activation={schema:'distribution-service-activation-v1',target:b.identity};

function proofFixture() {
  let actual={...expected,instanceId:'b-instance',releaseDigest:b.identity.digest};
  const stop={commandId:'stop',operation:'stop',status:'stopped',phase:'stopped',expected,
    fenceId:'fence',resumeCommandId:'resume',updatedAt:1,
    exitProof:{ownerId:expected.ownerId,instanceId:expected.instanceId,ownerReceiptId:'exit',observedAt:1,code:0,signal:null}};
  const resume={commandId:'resume',operation:'resume',status:'ready',phase:'ready',expected,
    fenceId:'fence',stoppedCommandId:'stop',observed:actual,activation,updatedAt:2};
  const request={fenceId:'fence',commandId:'stop',purpose:'service-stop',instanceId:expected.instanceId,
    dataScope:expected.dataScope,serviceIdentity:expected,outcome:'resumed',resumeCommandId:'resume',evidence:{}};
  const verify=createHostServiceReleaseVerifier({service:{proof:id=>({stop,resume})[id]??null},
    inspectRunningService:()=>actual});
  return {stop,resume,request,verify,setActual:v=>{actual=v;}};
}

test('cross-release proof binds the authenticated activation, never caller evidence',async()=>{
  const f=proofFixture();
  assert.deepEqual((await f.verify(f.request)).activation,activation);
  delete f.resume.activation;
  await assert.rejects(f.verify({...f.request,evidence:{activation}}),/service_activation_unconfirmed/);
});

test('cross-release proof rejects malformed or mismatched signed-target bindings',async()=>{
  for(const bad of [
    {...activation,schema:'distribution-service-activation-v0'},
    {...activation,target:a.identity},
    {...activation,target:{...b.identity,digest:'not-a-digest'}},
  ]) {
    const f=proofFixture(); f.resume.activation=bad;
    await assert.rejects(f.verify(f.request));
  }
});

test('versioned activation does not relax exact installation, command, exit, or instance checks',async()=>{
  const changes=[
    f=>{f.resume.status='unknown';},
    f=>{f.resume.stoppedCommandId='another-stop';},
    f=>{f.stop.resumeCommandId='another-resume';},
    f=>{f.stop.exitProof.instanceId='another-instance';},
    f=>{f.setActual({...f.resume.observed,instanceId:expected.instanceId});},
    f=>{f.setActual({...f.resume.observed,installationId:'other-installation'});},
    f=>{f.setActual({...f.resume.observed,ownerId:'other-owner'});},
    f=>{f.setActual({...f.resume.observed,dataScope:'other-data'});},
    f=>{f.request.purpose='distribution-update';},
  ];
  for(const change of changes){const f=proofFixture();change(f);await assert.rejects(f.verify(f.request));}
});

test('legacy same-release proof remains valid without an activation binding',async()=>{
  const f=proofFixture();
  delete f.resume.activation;
  f.resume.observed={...expected,instanceId:'replacement-a'};
  f.setActual(f.resume.observed);
  assert.equal((await f.verify(f.request)).activation,undefined);
});

// Unit-level owner test: the platform and Host are explicit fakes here. The
// separate Linux integration must prove real custody, signed artifacts and Host.
async function ownerFixture(t,{qualify=true,qualificationFails=false,launchReplyLost=false}={}){
  const directory=await mkdtemp(join(tmpdir(),'activation-owner-'));
  const counts={verify:0,qualify:0,launch:0,release:0};
  let service, current=a, actual={identity:a.identity,instanceId:expected.instanceId,dataScope:binding.dataScope,ready:true},fence;
  let exited=null;
  const host={
    async admitServiceStop(c){
      fence={phase:'held',purpose:'service-stop',commandId:c.commandId,fenceId:'fence',
        instanceId:c.expected.instanceId,dataScope:c.expected.dataScope,serviceIdentity:c.expected};
      return {...fence,admitted:true,expected:c.expected,evidence:{activeWork:0,intakeClosed:true,
        instanceId:c.expected.instanceId,dataScope:c.expected.dataScope}};
    },
    async inspectServiceLifecycle(){return {fence};},
    async serviceStopReceipt(){return fence;},
    async releaseServiceStop(r){
      counts.release++;
      const observed=service.proof(r.resumeCommandId).observed;
      return {...r,purpose:'service-stop',released:true,intakeClosed:false,expected,observed};
    },
  };
  const lifecycle={
    supportsReleaseActivation:true,ownedPid:null,
    processes:{ownerId:binding.ownerId,inspect:()=>({state:'unknown'}),exitProof:()=>exited},
    async inspectOwned(){if(!actual)throw Error('not-running');return actual;},
    async inspectActivationReady(){return this.inspectOwned();},
    async stopOwned(e){actual=null;return exited={ownerId:binding.ownerId,instanceId:e.instanceId,
      ownerReceiptId:'exit',observedAt:Date.now(),code:0,signal:null};},
    async resumeOwned(r){counts.launch++;current=r.target;actual={identity:r.target.identity,
      instanceId:r.instanceId,dataScope:r.dataScope,ready:true};if(launchReplyLost)throw Error('reply-lost');},
  };
  const options={directory,...binding,host,lifecycle,currentRelease:()=>current,releases:{
    async verify(){counts.verify++;return true;},
    ...(qualify?{async qualifyActivation(){counts.qualify++;if(qualificationFails)throw Error('source-advanced');}}:{}),
  }};
  service=new ServiceLifecycleOwner(options);
  t.after(async()=>{await service.close();await rm(directory,{recursive:true,force:true});});
  service.stop({commandId:'stop',expected});
  assert.equal((await service.waitFor('stop')).status,'stopped');
  return {counts,get service(){return service;},async reopen(){await service.close();service=new ServiceLifecycleOwner(options);}};
}

test('forward activation requires fresh qualification before consuming stopped authority',async t=>{
  for(const options of [{qualify:false},{qualificationFails:true}]){
    const f=await ownerFixture(t,options);
    f.service.resume({commandId:'resume',expected,stoppedCommandId:'stop',target:b});
    assert.equal((await f.service.waitFor('resume')).status,'refused');
    assert.equal(f.service.proof('stop').resumeCommandId,undefined);
    assert.equal(f.counts.launch,0);
  }
});

test('forward activation persists exact incoming artifact through reopen without relaunch',async t=>{
  const f=await ownerFixture(t,{launchReplyLost:true});
  const command={commandId:'resume',expected,stoppedCommandId:'stop',target:b};
  f.service.resume(command);
  const uncertain=await f.service.waitFor('resume');
  assert.equal(uncertain.status,'unknown');
  assert.deepEqual(uncertain.activation,activation);
  assert.equal(f.counts.qualify,1);
  await f.reopen();
  assert.equal(f.service.resume(command).status,'unknown');
  const recovered=await f.service.reconcile('resume');
  assert.equal(recovered.status,'ready');
  assert.equal(recovered.observed.instanceId,uncertain.observed.instanceId);
  assert.deepEqual(recovered.activation,activation);
  assert.equal(f.counts.launch,1);
  assert.equal(f.counts.qualify,1);
  assert.equal(f.counts.release,1);
});
