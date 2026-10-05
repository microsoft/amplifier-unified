import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {ServiceLifecycleOwner,createHostServiceInitialStartVerifier} from '../dist/index.js';
const target={identity:{id:'initial',version:'1.0.0',revision:'a'.repeat(40),digest:'a'.repeat(64)},handle:'release:'+'a'.repeat(64)};
const binding={installationId:'installation',ownerId:'unit-owner',dataScope:'private-data'};
// Unit tests intentionally use fake platform/Host ports. Actual closed Host and
// kernel generation acceptance belongs to the independent Linux fixture.
async function fixture(t,mode={}) {
  const directory=await mkdtemp(join(tmpdir(),'initial-service-'));
  const counts={claims:0,launches:0,releases:0,qualified:0};
  let service,actual,fence,custody,closed=true,releaseLost=mode.releaseLost;
  function stored(){const db=new DatabaseSync(join(directory,'service.sqlite3'),{readOnly:true});try{return JSON.parse(db.prepare('SELECT value FROM commands WHERE id=?').get('initial').value);}finally{db.close();}}
  const lifecycle={ownedPid:null,processes:{ownerId:binding.ownerId,inspect:()=>({state:'unknown'}),exitProof:()=>null},
    async inspectOwned(){if(!actual)throw Error('unavailable');return actual;},
    async inspectActivationReady(){if(!closed)throw Error('open');return this.inspectOwned();},
    async verifyCustody(expected,retained){assert.deepEqual(retained,custody);assert.equal(expected.instanceId,custody.instanceId);if(mode.staleCustody)throw Error('stale-generation');},
    async startInitialOwned(r,hooks){
      assert.equal(stored().phase,'initial_claim_requested');counts.claims++;
      if(mode.claimLost)throw Error('claim-reply-lost');
      const claim={kind:'pristine-installation',installationId:binding.installationId,commandId:r.commandId,
        instanceId:r.instanceId,dataScope:r.dataScope,targetDigest:r.target.identity.digest};
      hooks.onClaim(claim);assert.deepEqual(stored().initialClaim,claim);
      counts.launches++;custody={kind:'linux-unit',unit:'amplifier-fixture.service',invocationId:'a'.repeat(32),
        instanceId:r.instanceId,dataScope:r.dataScope,releaseDigest:r.target.identity.digest};
      if(!mode.missingCustody)hooks.onCustody(custody);
      actual={identity:{...r.target.identity,...(mode.wrongFullIdentity?{revision:'b'.repeat(40)}:{})},instanceId:r.instanceId,dataScope:r.dataScope,ready:true};
      fence={purpose:'initial-start',phase:'closed',fenceId:'initial-fence',commandId:r.commandId,
        instanceId:r.instanceId,dataScope:r.dataScope,serviceIdentity:{...binding,instanceId:r.instanceId,releaseDigest:r.target.identity.digest}};
      if(!mode.missingCustody)assert.deepEqual(stored().localCustody,custody);
      if(mode.launchLost)throw Error('launch-reply-lost');
    },
    async stopOwned(){throw Error('not-used');},async resumeOwned(){throw Error('must-not-respawn');},
  };
  const host={async inspectServiceLifecycle(){return {intakeClosed:closed,fence};},
    async releaseServiceStart(r){counts.releases++;const proof=await verifier({...r,purpose:'initial-start',instanceId:actual.instanceId,
      dataScope:binding.dataScope,serviceIdentity:fence.serviceIdentity});assert.equal(proof.claimReceiptId,'initial');
      closed=false;if(releaseLost){releaseLost=false;throw Error('reply-lost');}
      return {...r,purpose:'initial-start',released:true,intakeClosed:false,expected:proof.expected,observed:proof.observed};},
    async admitServiceStop(){throw Error('not-used');},async serviceStopReceipt(){return null;},async releaseServiceStop(){throw Error('not-used');},
  };
  const options={directory,...binding,lifecycle,host,currentRelease:()=>target,releases:{async verify(){return true;},
    async qualifyActivation(){counts.qualified++;if(mode.unqualified)throw Error('not-qualified');}}};
  service=new ServiceLifecycleOwner(options);
  const verifier=createHostServiceInitialStartVerifier({service:{proof:id=>service.proof(id)},inspectRunningService:()=>fence.serviceIdentity});
  t.after(async()=>{await service.close();await rm(directory,{recursive:true,force:true});});
  return {counts,mode,get service(){return service;},get closed(){return closed;},stored,async reopen(){await service.close();service=new ServiceLifecycleOwner(options);}};
}
const start={commandId:'initial',target};
test('initial claim and unit generation persist before closed-ready activation, without synthetic stop',async t=>{
  const f=await fixture(t);f.service.startInitial(start);const ready=await f.service.waitFor('initial');
  assert.equal(ready.status,'ready');assert.equal(ready.admissionSettlement.state,'settled');assert.equal(f.closed,false);
  assert.equal(ready.exitProof,undefined);assert.equal(ready.stoppedCommandId,undefined);assert.equal(ready.localCustody,undefined);
  assert.deepEqual(f.counts,{claims:1,launches:1,releases:1,qualified:1});
  assert.deepEqual(f.service.startInitial(start),ready);
});
test('fresh qualification refusal consumes no pristine claim or process',async t=>{
  const f=await fixture(t,{unqualified:true});f.service.startInitial(start);
  assert.equal((await f.service.waitFor('initial')).status,'refused');assert.equal(f.counts.claims,0);assert.equal(f.counts.launches,0);
});
test('lost claim reply remains unknown after reopen and never retries provisioning',async t=>{
  const f=await fixture(t,{claimLost:true});f.service.startInitial(start);const old=await f.service.waitFor('initial');
  assert.equal(old.status,'unknown');assert.equal(old.phase,'initial_claim_requested');await f.reopen();
  assert.equal(f.service.startInitial(start).status,'unknown');assert.equal((await f.service.reconcile('initial')).status,'unknown');
  assert.equal(f.counts.claims,1);assert.equal(f.counts.launches,0);assert.equal(f.closed,true);
});
test('lost launch response observes the same retained closed generation after reopen',async t=>{
  const f=await fixture(t,{launchLost:true});f.service.startInitial(start);const old=await f.service.waitFor('initial');
  assert.equal(old.status,'unknown');await f.reopen();assert.equal(f.service.startInitial(start).status,'unknown');
  const ready=await f.service.reconcile('initial');assert.equal(ready.status,'ready');assert.equal(ready.expected.instanceId,old.expected.instanceId);
  assert.equal(ready.admissionSettlement.state,'settled');assert.equal(f.counts.claims,1);assert.equal(f.counts.launches,1);
});
test('missing custody, stale generation and same-digest wrong full identity never open intake',async t=>{
  for(const mode of [{missingCustody:true},{staleCustody:true},{wrongFullIdentity:true}]) {
    const f=await fixture(t,mode);f.service.startInitial(start);assert.equal((await f.service.waitFor('initial')).status,'unknown');
    await f.reopen();assert.equal((await f.service.reconcile('initial')).status,'unknown');assert.equal(f.closed,true);assert.equal(f.counts.releases,0);
  }
});
test('lost initial release ACK settles on same command without requiring intake to close again',async t=>{
  const f=await fixture(t,{releaseLost:true});f.service.startInitial(start);const old=await f.service.waitFor('initial');
  assert.equal(old.status,'ready');assert.equal(old.admissionSettlement.state,'unknown');assert.equal(f.closed,false);
  await f.reopen();const ready=await f.service.reconcile('initial');assert.equal(ready.admissionSettlement.state,'settled');assert.equal(ready.errorCode,undefined);
  assert.equal(f.counts.claims,1);assert.equal(f.counts.launches,1);assert.equal(f.counts.releases,2);
});
test('authenticated initial proof rejects caller authority, wrong identity and synthetic stop receipts',async t=>{
  const f=await fixture(t);f.service.startInitial(start);const ready=await f.service.waitFor('initial');
  let proof=ready,actual=ready.observed;
  const verify=createHostServiceInitialStartVerifier({service:{proof:()=>proof},inspectRunningService:()=>actual});
  const request={purpose:'initial-start',fenceId:ready.fenceId,commandId:ready.commandId,instanceId:actual.instanceId,
    dataScope:actual.dataScope,serviceIdentity:actual,evidence:{}};
  assert.equal((await verify(request)).kind,'service-initial-start');
  for(const change of [()=>{proof=null;},()=>{proof={...ready,initialClaim:undefined};},()=>{proof={...ready,status:'unknown'};},
    ()=>{proof={...ready,exitProof:{instanceId:'invented'}};},()=>{proof={...ready,operation:'resume'};},
    ()=>{actual={...actual,ownerId:'wrong'};},()=>{proof={...ready,activation:{...ready.activation,target:{...target.identity,digest:'b'.repeat(64)}}};}]){
    proof=ready;actual=ready.observed;change();await assert.rejects(verify({...request,evidence:{proof:ready}}));
  }
});
