import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomBytes} from 'node:crypto';
import {DistributionUpdateOwner,createHostAdmissionAbortVerifier,serveHostControl,HostControlClient,
  admissionAbortReceipt,PosixOwnedProcessLifecycle} from '../dist/index.js';

const a={id:'one',version:'1.0.0',revision:'a'.repeat(40),digest:'a'.repeat(64)};
const b={id:'two',version:'2.0.0',revision:'b'.repeat(40),digest:'b'.repeat(64)};
const original={identity:a,instanceId:'original',dataScope:'fixture',ready:true};
const binding={commandId:'update',fenceId:'original-fence',instanceId:'original',dataScope:'fixture',purpose:'distribution-update'};
const candidate=identity=>({identity,handle:identity.id});
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const tick=()=>new Promise(r=>setImmediate(r));
function completed() {
  const {purpose,...bound}=binding;
  return {schema:'host-admission-abort-v1',...bound,status:'aborted',intakeClosed:false,
    owners:[{...bound,ownerId:'attempted',status:'released',receiptId:'owner-abort'},
      {...bound,ownerId:'no-effect',status:'not-acquired',receiptId:'owner-no-acquire'}],receiptId:'update'};
}
async function fixture(t) {
  const directory=await mkdtemp(join(tmpdir(),'partial-admission-'));
  const state={running:structuredClone(original),fence:structuredClone(binding),completed:null,inFlight:false};
  const calls={admit:0,abort:0,restart:0,prepare:0,passive:0};
  let owner;
  const options={directory,dataScope:'fixture',initial:candidate(a),preferences:{autoCheck:false,autoInstall:false,intervalMs:1000},
    releases:{check:async()=>({releases:[a,b],recommendedId:b.id}),prepare:async id=>{calls.prepare++;return candidate(id);},verify:async()=>true},
    lifecycle:{inspect:async()=>state.running,admitRestart:async()=>{calls.admit++;throw Error('lost acquisition reply');},
      restart:async()=>{calls.restart++;throw Error('must never restart');},
      inspectAdmissionFence:async()=>state.fence,
      inspectAdmissionAbort:async()=>{calls.passive++;return state.completed;},
      abortAdmission:async request=>{
        calls.abort++;
        // Host authenticates a durable distinct supervisor intent, not the
        // request's assertions. Acquisition concurrency/coverage is Host-owned.
        const verify=createHostAdmissionAbortVerifier({supervisor:owner,inspectRunning:()=>state.running});
        assert.equal((await verify(binding)).kind,'distribution-admission-abort');
        assert.deepEqual(request,{...binding,observed:original});
        if(state.inFlight) throw Error('acquisition still running');
        return state.completed=completed();
      }}};
  owner=new DistributionUpdateOwner(options);
  t.after(async()=>{await owner.close();await rm(directory,{recursive:true,force:true});});
  owner.check('check');await owner.waitFor('check');
  owner.prepare('prepare',b.id);await owner.waitFor('prepare');
  owner.activate('update',{preparedCommandId:'prepare',targetDigest:b.digest,expectedCurrentId:a.id});
  assert.equal((await owner.waitFor('update')).phase,'admission_requested');
  return {get owner(){return owner;},options,state,calls,async reopen(){await owner.close();owner=new DistributionUpdateOwner(options);}};
}

test('partial acquisition abort settles only original update and preserves staged/current history',async t=>{
  const f=await fixture(t),before=f.owner.inspect();
  const result=await f.owner.reconcile('update');
  assert.equal(result.status,'failed');assert.equal(result.phase,'admission_aborted');
  assert.deepEqual(result.admissionSettlement,{state:'settled',outcome:'unchanged',updatedAt:result.admissionSettlement.updatedAt});
  assert.equal(result.admission,undefined);assert.equal(result.activation,undefined);
  const proof=f.owner.restartProof('update');
  assert.equal(proof.instanceId,null);assert.equal(proof.admission,null);assert.equal(proof.admittedRunning,null);
  assert.deepEqual(proof.admissionAbort.receipt,completed());
  assert.deepEqual(f.owner.inspect().current,before.current);
  assert.deepEqual(f.owner.inspect().previous,before.previous);
  assert.deepEqual(f.owner.inspect().staged,before.staged);
  assert.deepEqual(f.owner.receipt('check'),before.operations.find(r=>r.id==='check'));
  assert.equal(f.owner.inspect().restartUnresolved,false);
  await f.owner.reconcile('update');f.owner.notifyIdle();await tick();
  assert.equal(f.calls.admit,1);assert.equal(f.calls.abort,1);assert.equal(f.calls.restart,0);assert.equal(f.calls.prepare,1);
});

test('unsupported Host, missing binding, and in-flight acquire remain unknown with no new admission',async t=>{
  const f=await fixture(t);
  const abort=f.options.lifecycle.abortAdmission;
  delete f.options.lifecycle.abortAdmission;
  await f.owner.reconcile('update');assert.equal(f.calls.abort,0);
  f.options.lifecycle.abortAdmission=abort;f.state.fence=null;
  await f.owner.reconcile('update');assert.equal(f.calls.abort,0);
  f.state.fence=structuredClone(binding);f.state.inFlight=true;
  await f.owner.reconcile('update');assert.equal(f.calls.abort,1);
  assert.equal(f.owner.receipt('update').status,'unknown');assert.equal(f.owner.inspect().restartUnresolved,true);
  assert.equal(f.calls.restart,0);assert.equal(f.calls.admit,1);
});

for (const [name,mutate] of [
  ['command',v=>v.commandId='different'],['fence',v=>v.fenceId='different'],
  ['instance',v=>v.instanceId='different'],['scope',v=>v.dataScope='different'],
  ['closed intake',v=>v.intakeClosed=true],['unknown owner',v=>v.owners[0].status='unknown'],
  ['unbound owner',v=>v.owners[0].commandId='different'],
  ['duplicate owner',v=>v.owners.push(v.owners[0])],['extra field',v=>v.privateKey='do-not-project'],
  ['unbounded list',v=>v.owners=Array(129).fill(v.owners[0])],
]) test(`malformed or mismatched ${name} proof cannot settle the update`,async t=>{
  const f=await fixture(t);
  f.options.lifecycle.abortAdmission=async()=>{const result=completed();mutate(result);return result;};
  await f.owner.reconcile('update');
  assert.equal(f.owner.receipt('update').status,'unknown');assert.equal(f.owner.inspect().restartUnresolved,true);
  assert.doesNotMatch(JSON.stringify(f.owner.diagnostics()),/do-not-project/);
});

for (const [name,mutate] of [
  ['command',v=>v.commandId='different'],['instance',v=>v.instanceId='different'],
  ['scope',v=>v.dataScope='different'],['purpose',v=>v.purpose='recovery'],
]) test(`wrong original ${name} binding never reaches the unwind`,async t=>{
  const f=await fixture(t);mutate(f.state.fence);
  await f.owner.reconcile('update');assert.equal(f.calls.abort,0);assert.equal(f.owner.receipt('update').status,'unknown');
});

test('lost unwind reply is resolved by completed passive receipt, including ledger reopen',async t=>{
  const f=await fixture(t),abort=f.options.lifecycle.abortAdmission;
  f.options.lifecycle.abortAdmission=async req=>{await abort(req);throw Error('reply lost after Host commit');};
  await f.owner.reconcile('update');assert.equal(f.owner.receipt('update').status,'unknown');
  f.state.fence=null; // Host no longer holds intake; original intent is still durable.
  await f.reopen();
  assert.equal((await f.owner.reconcile('update')).phase,'admission_aborted');
  assert.equal(f.calls.abort,1);assert.equal(f.calls.restart,0);assert.equal(f.calls.admit,1);
});

test('concurrent same-command reconciliation coalesces; shutdown waits for an in-flight unwind',async t=>{
  const f=await fixture(t),gate=deferred(),abort=f.options.lifecycle.abortAdmission;
  f.options.lifecycle.abortAdmission=async req=>{await gate.promise;return abort(req);};
  const first=f.owner.reconcile('update'),second=f.owner.reconcile('update');assert.equal(first,second);
  await tick();let closed=false;const closing=f.owner.close().then(()=>closed=true);
  await tick();assert.equal(closed,false);gate.resolve();
  assert.equal((await first).phase,'admission_aborted');await second;await closing;
  assert.equal(f.calls.abort,1);
});

test('replacement process or release cannot settle original uncertain command',async t=>{
  const f=await fixture(t),abort=f.options.lifecycle.abortAdmission;
  f.options.lifecycle.abortAdmission=async req=>{const result=await abort(req);f.state.running={...original,instanceId:'replacement'};return result;};
  await f.owner.reconcile('update');assert.equal(f.owner.receipt('update').status,'unknown');
  await f.owner.reconcile('update');assert.equal(f.calls.abort,1);
  f.state.running={...original,identity:b};await f.owner.reconcile('update');assert.equal(f.owner.receipt('update').status,'unknown');
});

for(const [name,mutate] of [
  ['started restart',v=>v.instanceId='next'],['admitted phase',v=>v.phase='admitted'],
  ['synthesized held lease',v=>v.admission={activeWork:0,intakeClosed:true}],
  ['synthesized observed admission',v=>v.admittedRunning=original],
  ['different saved intent',v=>v.admissionAbort.request.fenceId='different'],
  ['different observed release',v=>v.admissionAbort.request.observed.identity=b],
]) test(`Host abort verifier refuses ${name} despite caller claims`,async t=>{
  const f=await fixture(t);f.state.inFlight=true;await f.owner.reconcile('update');
  const proof=f.owner.restartProof('update');mutate(proof);
  const verify=createHostAdmissionAbortVerifier({supervisor:{restartProof:()=>proof},inspectRunning:()=>original});
  await assert.rejects(verify({...binding,evidence:{verified:true,activeWork:0}}));
});

test('authenticated Host transport keeps completed abort inspection separate from admission',async t=>{
  const key=randomBytes(32).toString('hex');let held={...binding,phase:'unknown'},saved=null,calls=0;
  const host={inspectQuiescence:()=>({enabled:true,intakeClosed:!!held,fence:held}),
    quiescenceReceipt:()=>({admitted:false,intakeClosed:!!held,fence:held}),
    admitQuiescence:()=>{throw Error('must not acquire');},releaseQuiescence:()=>{throw Error('must not use generic release');},
    quiescenceAdmissionAbortReceipt:()=>saved??undefined,
    abortQuiescenceAdmission:request=>{calls++;const {purpose,...bound}=binding;assert.deepEqual(request,bound);saved=completed();held=null;return saved;}};
  const server=await serveHostControl({host,inspectRunning:()=>original,token:key});
  const client=new HostControlClient({dataScope:'fixture',connect:()=>({url:server.url,token:key,dataScope:'fixture'})});
  t.after(async()=>{client.close();await server.close();});
  assert.deepEqual(await client.inspectAdmissionFence('update'),binding);
  assert.equal(await client.inspectAdmissionAbort('update'),null);assert.equal(calls,0);
  assert.deepEqual(await client.abortAdmission({...binding,observed:original}),completed());
  assert.equal(await client.inspectAdmissionFence('update'),null);
  assert.deepEqual(await client.inspectAdmissionAbort('update'),completed());assert.equal(calls,1);
  await assert.rejects(client.rpc('admission-abort',{commandId:'update',fenceId:'original-fence',instanceId:'replacement',dataScope:'fixture'}));
  assert.equal(calls,1);
});

test('POSIX adapter will not adopt an unowned running service to settle an admission',async()=>{
  let calls=0;
  const lifecycle=new PosixOwnedProcessLifecycle({ownerId:'test-owner',inspect:async()=>original,admitRestart:async()=>null,
    resolve:async()=>{throw Error('no launch');},inspectAdmissionFence:async()=>binding,inspectAdmissionAbort:async()=>completed(),
    abortAdmission:async()=>{calls++;return completed();}});
  await assert.rejects(lifecycle.inspectAdmissionFence('update'),/ownership/);
  await assert.rejects(lifecycle.inspectAdmissionAbort('update'),/ownership/);
  await assert.rejects(lifecycle.abortAdmission({...binding,observed:original}),/ownership/);
  assert.equal(calls,0);
});
