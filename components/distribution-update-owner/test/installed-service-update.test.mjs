import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DistributionUpdateOwner,ServiceLifecycleOwner} from '../dist/index.js';

const release = (id,n) => ({id,version:`1.0.${n}`,revision:id.repeat(40),digest:id.repeat(64)});
const a=release('a',0),b=release('b',1),candidate=identity=>({identity,handle:identity.id});
const binding={installationId:'installed-fixture',ownerId:'owner',dataScope:'fixture'};
async function fixture(t){
 const root=await mkdtemp(join(tmpdir(),'installed-service-update-'));
 let owner,service,actual={identity:a,instanceId:'initial',dataScope:'fixture',ready:true},fence=null,exit=null;
 let loseLaunch=false,failQualification=false,loseStop=false;
 const counts={stop:0,launch:0,qualify:0,release:0,legacy:0};
 const releases={check:async()=>({releases:[a,b],recommendedId:'b'}),prepare:async i=>candidate(i),verify:async()=>true,
  qualifyActivation:async()=>{counts.qualify++;if(failQualification)throw Error('source_changed');}};
 const platform={supportsReleaseActivation:true,ownedPid:null,
  processes:{ownerId:'owner',inspect:()=>({state:'unknown'}),exitProof:()=>exit},
  inspectOwned:async()=>{if(!actual)throw Error('no_runtime');return actual;},
  inspectActivationReady:async()=>{if(!actual)throw Error('no_runtime');return actual;},
  stopOwned:async e=>{counts.stop++;actual=null;exit={ownerId:'owner',instanceId:e.instanceId,ownerReceiptId:'exit-'+counts.stop,observedAt:Date.now(),code:0,signal:null};if(loseStop)throw Error('stop_reply_lost');return exit;},
  observeExit:async()=>exit,
  resumeOwned:async r=>{counts.launch++;actual={identity:r.target.identity,instanceId:r.instanceId,dataScope:r.dataScope,ready:true};if(loseLaunch)throw Error('launch_reply_lost');},
 };
 const host={
  admitServiceStop:async c=>{fence={phase:'held',purpose:'service-stop',commandId:c.commandId,fenceId:c.commandId,
   instanceId:c.expected.instanceId,dataScope:'fixture',serviceIdentity:c.expected};
   return {...fence,admitted:true,expected:c.expected,evidence:{activeWork:0,intakeClosed:true,instanceId:c.expected.instanceId,dataScope:'fixture'}};},
  inspectServiceLifecycle:async()=>({fence}),serviceStopReceipt:async()=>fence,
  releaseServiceStop:async r=>{counts.release++;const stop=service.proof(r.commandId),resume=service.proof(r.resumeCommandId);
   assert.equal(stop.status,'stopped');assert.equal(resume.status,'ready');fence=null;
   return {...r,purpose:'service-stop',released:true,intakeClosed:false,expected:stop.expected,observed:resume.observed};},
 };
 function open(){
  owner=new DistributionUpdateOwner({directory:join(root,'updates'),dataScope:'fixture',initial:candidate(a),releases,
   preferences:{autoCheck:false,autoInstall:false,intervalMs:1000},
   lifecycle:{inspect:async()=>actual,admitRestart:async()=>{counts.legacy++;throw Error('legacy_called');},restart:async()=>{counts.legacy++;throw Error('legacy_called');}},
   serviceLifecycle:()=>service,mutationBlocked:()=>service.blocksUpdates()});
  service=new ServiceLifecycleOwner({directory:join(root,'service'),...binding,host,lifecycle:platform,releases,
   currentRelease:()=>owner.qualifiedCurrent(),updateMutationBlocked:id=>owner.blocksServiceStop(id)});
  owner.start();
 }
 open();
 t.after(async()=>{await owner.close();await service.close();await rm(root,{recursive:true,force:true});});
 return {get owner(){return owner;},get service(){return service;},get actual(){return actual;},counts,
  loseLaunch:()=>{loseLaunch=true;},loseStop:()=>{loseStop=true;},failQualification:()=>{failQualification=true;},
  async check(){owner.check('check');assert.equal((await owner.waitFor('check')).status,'succeeded');},
  async reopen(){await owner.close();await service.close();open();}};
}
test('normal update delegates actual stop and release activation through the single service ledger',async t=>{
 const f=await fixture(t);await f.check();f.owner.install('update');
 const done=await f.owner.waitFor('update');assert.equal(done.status,'succeeded');assert.equal(done.phase,'ready');
 assert.equal(done.admissionSettlement.state,'settled');assert.equal(f.actual.identity.id,'b');
 assert.deepEqual(f.counts,{stop:1,launch:1,qualify:1,release:1,legacy:0});
 assert.equal(f.owner.restartProof('update'),null); // No second proof can authorize Host release.
});
test('rollback uses verified retained bytes without demanding forward source freshness',async t=>{
 const f=await fixture(t);await f.check();f.owner.install('update');await f.owner.waitFor('update');
 f.failQualification();f.owner.rollback('rollback','b');const done=await f.owner.waitFor('rollback');
 assert.equal(done.status,'succeeded');assert.equal(f.actual.identity.id,'a');assert.equal(f.counts.qualify,1);
 assert.equal(f.counts.legacy,0);assert.equal(f.counts.launch,2);
});
test('lost launch reply reopens and observes the same service command without relaunch',async t=>{
 const f=await fixture(t);await f.check();f.loseLaunch();f.owner.install('update');
 assert.equal((await f.owner.waitFor('update')).status,'unknown');const instance=f.actual.instanceId;
 await f.reopen();const result=await f.owner.reconcile('update');
 assert.equal(result.status,'succeeded');assert.equal(result.admissionSettlement.state,'settled');
 assert.equal(f.actual.instanceId,instance);assert.equal(f.counts.launch,1);assert.equal(f.counts.stop,1);
});
test('reconciliation after an uncertain stop never creates the unattempted resume',async t=>{
 const f=await fixture(t);await f.check();f.loseStop();f.owner.install('update');
 assert.equal((await f.owner.waitFor('update')).status,'unknown');await f.reopen();
 assert.equal((await f.owner.reconcile('update')).status,'unknown');assert.equal(f.counts.launch,0);assert.equal(f.counts.stop,1);
});
