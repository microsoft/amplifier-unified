import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {DatabaseSync} from 'node:sqlite';
const moduleUrl=process.env.DISTRIBUTION_OWNER_MODULE??new URL('../dist/index.js',import.meta.url).href;
const {DistributionUpdateOwner,serveSupervisor,SupervisorClient,serveHostControl,HostControlClient}=await import(moduleUrl);
const fence={purpose:'recovery',fenceId:'fence-one',commandId:'recovery-one',instanceId:'instance-one',dataScope:'fixture'};
const initial={identity:{id:'v1',version:'1.0.0',revision:'a'.repeat(40),digest:'b'.repeat(64)},handle:'release-one'};
const original={autoCheck:true,autoInstall:true,intervalMs:54321};
const disabled={autoCheck:false,autoInstall:false,intervalMs:14400000};
const tick=()=>new Promise(r=>setImmediate(r));
const prepare=(port,id='prepare',extra={})=>port.perform('prepare',{commandId:id,parts:['updates.preferences'],privateContentReviewed:true,...extra},fence);
const apply=(port,review,id='apply',extra={})=>port.perform('apply',{commandId:id,preparedId:review.preparedId,reviewHash:review.reviewHash,...extra},fence);
async function fixture(t,extra={}){
 const directory=await mkdtemp(join(tmpdir(),'preferences-reset-'));
 const counts={check:0,prepare:0,verify:0,restart:0};
 const options={directory,dataScope:'fixture',initial,preferences:original,
  releases:{check:async()=>{counts.check++;return {releases:[initial.identity],recommendedId:'v1'};},prepare:async()=>{counts.prepare++;return initial;},verify:async()=>{counts.verify++;return true;}},
  lifecycle:{inspect:async()=>({identity:initial.identity,instanceId:'instance-one',dataScope:'fixture',ready:true}),admitRestart:async()=>null,restart:async()=>{counts.restart++;}},
  verifyRecoveryFence:async actual=>assert.deepEqual(actual,fence),...extra};
 let owner=new DistributionUpdateOwner(options);
 t.after(async()=>{await owner.close();await rm(directory,{recursive:true,force:true});});
 return {get owner(){return owner;},options,counts,directory,reopen:async()=>{await owner.close();owner=new DistributionUpdateOwner(options);return owner;}};
}
test('reset/restore are separately reviewed atomic preference changes preserving release authority',async t=>{
 const f=await fixture(t),before=f.owner.inspect();
 const prepared=await prepare(f.owner.appReset),review=prepared.receipt.result;
 assert.equal(prepared.receipt.state,'succeeded');assert.equal(review.containsPrivateContent,true);assert.equal(review.credentialsIncluded,false);
 assert.equal(JSON.stringify(review).includes('54321'),false);assert.equal(JSON.stringify(review).includes('autoInstall'),false);
 assert.equal((await f.owner.appReset.perform('inspect',{preparedId:review.preparedId,reviewHash:review.reviewHash},fence)).applicable,true);
 const applied=await apply(f.owner.appReset,review);assert.equal(applied.receipt.state,'succeeded');assert.deepEqual(f.owner.inspect().preferences,disabled);
 const after=f.owner.inspect();for(const key of ['current','previous','staged','catalog','lastCheck'])assert.deepEqual(after[key],before[key]);
 assert.deepEqual(f.counts,{check:0,prepare:0,verify:0,restart:0});
 const second=await apply(f.owner.appReset,review,'different-command');assert.equal(second.receipt.state,'refused');assert.equal(second.receipt.result,undefined);
 const restoreReview=(await prepare(f.owner.appReset,'review-restore',{restoreCommandId:'apply'})).receipt.result;
 assert.equal(restoreReview.items[0].operation,'restore');
 assert.equal((await apply(f.owner.appReset,restoreReview,'wrong-apply')).receipt.state,'refused');
 const restored=await f.owner.appReset.perform('restore',{commandId:'restore',preparedId:restoreReview.preparedId,reviewHash:restoreReview.reviewHash,resetCommandId:'apply',expectedPostResetRevision:applied.receipt.result.postResetRevision},fence);
 assert.equal(restored.receipt.result.restored,true);assert.deepEqual(f.owner.inspect().preferences,original);
 assert.deepEqual(await apply(f.owner.appReset,review),applied,'original command receipt survives restore without changing preferences');
 await assert.rejects(apply(f.owner.appReset,{...review,reviewHash:'c'.repeat(64)}),e=>e.data?.executed===false);
});
test('ordinary no-op writes and A-B-A changes invalidate reviews and restore authority',async t=>{
 const f=await fixture(t),review=(await prepare(f.owner.appReset)).receipt.result;
 f.owner.setPreferences('ordinary-noop',original);await f.owner.waitFor('ordinary-noop');await tick();
 assert.equal((await apply(f.owner.appReset,review)).receipt.state,'refused');
 const r2=(await prepare(f.owner.appReset,'prepare-two')).receipt.result;
 const applied=await apply(f.owner.appReset,r2,'apply-two');
 for(const [id,prefs] of [['change',original],['change-back',disabled]]){f.owner.setPreferences(id,prefs);await f.owner.waitFor(id);await tick();}
 const restore=await prepare(f.owner.appReset,'review-stale-restore',{restoreCommandId:'apply-two'});
 assert.equal(restore.receipt.state,'refused');assert.equal(restore.receipt.result,undefined);
 assert.deepEqual((await f.owner.appReset.perform('inspect',{commandId:'apply-two'})).receipt,applied.receipt);
});
test('expiry, wrong review hash, forged scope and absent host authority fail without private results',async t=>{
 const f=await fixture(t),review=(await prepare(f.owner.appReset)).receipt.result;
 assert.equal((await apply(f.owner.appReset,{...review,reviewHash:'0'.repeat(64)})).receipt.state,'refused');
 t.mock.timers.enable({apis:['Date'],now:review.expiresAt+1});
 assert.equal((await apply(f.owner.appReset,review,'expired')).receipt.state,'refused');t.mock.timers.reset();
 for(const [i,wrong] of [{...fence,instanceId:'forged'},{...fence,dataScope:'foreign'},{...fence,purpose:'distribution-update'},undefined].entries()){
  const r=await f.owner.appReset.perform('prepare',{commandId:'forged-'+i,parts:['updates.preferences'],privateContentReviewed:true},wrong);
  assert.equal(r.receipt.state,'refused');assert.equal(r.receipt.executed,false);assert.equal(r.receipt.result,undefined);
 }
 const absent=await fixture(t,{verifyRecoveryFence:undefined});assert.equal((await prepare(absent.owner.appReset)).receipt.state,'refused');
 assert.deepEqual(f.owner.inspect().preferences,original);
});
test('reset cannot race ordinary commands, background work or shutdown while authenticating the fence',async t=>{
 let release;const gate=new Promise(r=>release=r);
 const f=await fixture(t,{verifyRecoveryFence:()=>gate});
 const pending=prepare(f.owner.appReset);await tick();
 assert.throws(()=>f.owner.setPreferences('racing',disabled),/preferences_reset_busy/);
 f.owner.start();f.owner.notifyIdle();await tick();assert.equal(f.counts.check,0);
 assert.equal((await prepare(f.owner.appReset,'parallel')).receipt.state,'refused');
 const closing=f.owner.close();release();const finished=await pending;assert.equal(finished.receipt.state,'succeeded');await closing;
 await f.reopen();assert.deepEqual((await f.owner.appReset.perform('inspect',{commandId:'prepare'})).receipt,finished.receipt);
});
test('pending normal work refuses reset without cancelling, replaying or changing that work',async t=>{
 let release;const gate=new Promise(r=>release=r);
 const f=await fixture(t,{releases:{check:async()=>{await gate;return {releases:[],recommendedId:null};}}});
 f.owner.check('manual');await tick();
 assert.equal((await prepare(f.owner.appReset)).receipt.state,'refused');
 release();assert.equal((await f.owner.waitFor('manual')).status,'succeeded');assert.deepEqual(f.owner.inspect().preferences,original);
});
test('restore does not start automatic work until the trusted idle wake, and a stale restore review refuses',async t=>{
 const f=await fixture(t),r=(await prepare(f.owner.appReset)).receipt.result;
 const applied=await apply(f.owner.appReset,r);
 let restore=(await prepare(f.owner.appReset,'restore-review',{restoreCommandId:'apply'})).receipt.result;
 const args={commandId:'restore',preparedId:restore.preparedId,reviewHash:restore.reviewHash,resetCommandId:'apply',expectedPostResetRevision:applied.receipt.result.postResetRevision};
 f.owner.start();await f.owner.appReset.perform('restore',args,fence);await tick();assert.equal(f.counts.check,0);
 f.owner.notifyIdle();await tick();await new Promise(r=>setTimeout(r,10));assert.equal(f.counts.check,1);
 await tick();const next=(await prepare(f.owner.appReset,'next-review')).receipt.result;
 const second=await apply(f.owner.appReset,next,'next-reset');
 restore=(await prepare(f.owner.appReset,'stale-review',{restoreCommandId:'next-reset'})).receipt.result;
 f.owner.setPreferences('ordinary-after-review',disabled);await f.owner.waitFor('ordinary-after-review');await tick();
 const result=await f.owner.appReset.perform('restore',{commandId:'stale-restore',preparedId:restore.preparedId,reviewHash:restore.reviewHash,resetCommandId:'next-reset',expectedPostResetRevision:second.receipt.result.postResetRevision},fence);
 assert.equal(result.receipt.state,'refused');assert.deepEqual(f.owner.inspect().preferences,disabled);
});
test('passive recovery never projects arbitrary private results from non-success receipts',async t=>{
 const f=await fixture(t);await prepare(f.owner.appReset);
 const db=new DatabaseSync(join(f.directory,'updates.sqlite3'));t.after(()=>db.close());
 for(const state of ['refused','running','unknown']){
  db.prepare("UPDATE preference_reset_commands SET value=json_set(value,'$.state',?,'$.result',json(?)) WHERE id='prepare'").run(state,JSON.stringify({private:'secret-fixture'}));
  const result=await f.owner.appReset.perform('inspect',{commandId:'prepare'});
  assert.equal(result.receipt.state,state);assert.equal(result.receipt.result,undefined);assert.equal(JSON.stringify(result).includes('secret-fixture'),false);
 }
});
test('authenticated supervisor exposes the real reset port and passive original receipts after reopen',async t=>{
 const f=await fixture(t),key='a'.repeat(64);let server=await serveSupervisor({owner:f.owner,token:key});
 t.after(()=>server.close());let client=new SupervisorClient({url:server.url,token:key});
 assert.equal(client.appReset.id,'updates');
 const review=(await prepare(client.appReset)).receipt.result,applied=await apply(client.appReset,review);
 assert.equal(applied.receipt.state,'succeeded');
 const unauthorized=await fetch(new URL('v1/rpc',server.url),{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({operation:'app-reset',args:{operation:'inspect',args:{commandId:'apply'}}})});assert.equal(unauthorized.status,401);
 client.close();await server.close();await f.reopen();server=await serveSupervisor({owner:f.owner,token:key});client=new SupervisorClient({url:server.url,token:key});t.after(()=>client.close());
 assert.deepEqual(await client.appReset.perform('inspect',{commandId:'apply'}),applied);
 assert.deepEqual(await apply(client.appReset,review),applied);assert.deepEqual(f.owner.inspect().preferences,disabled);
 await assert.rejects(apply(client.appReset,{...review,reviewHash:'f'.repeat(64)}),e=>e.data?.executed===false);
});
test('process exit after committed reset keeps exact receipt and no automatic replay on supervisor reopen',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'reset-process-'));t.after(()=>rm(directory,{recursive:true,force:true}));
 const worker=new URL('./app-reset-worker.mjs',import.meta.url);
 const run=phase=>{const child=spawnSync(process.execPath,[worker.pathname,phase,directory],{encoding:'utf8',env:{...process.env,DISTRIBUTION_OWNER_MODULE:moduleUrl}});assert.equal(child.status,0,child.stderr);return JSON.parse(child.stdout);};
 const first=run('apply'),second=run('inspect');assert.deepEqual(second.receipt,first.receipt);assert.deepEqual(second.preferences,disabled);assert.equal(second.effects,0);
});
test('host-control refuses forged/incomplete/non-held recovery census',async t=>{
 let current={enabled:true,intakeClosed:true,activeAdmissions:0,continuations:0,activeMaintenance:1,fence:{...fence,phase:'held',owners:['one','two']}};
 const host={inspectQuiescence:()=>current,quiescenceReceipt:()=>({admitted:true,fenceId:fence.fenceId,commandId:fence.commandId}),admitQuiescence:()=>{throw Error('no admission');},releaseQuiescence:()=>{throw Error('no release');}};
 const opts={host,token:'d'.repeat(64),inspectRunning:()=>({identity:initial.identity,instanceId:'instance-one',dataScope:'fixture',ready:true})};
 const server=await serveHostControl({...opts,recoveryOwners:['two','one']});t.after(()=>server.close());
 const client=new HostControlClient({dataScope:'fixture',connect:()=>({url:server.url,token:opts.token,dataScope:'fixture'})});t.after(()=>client.close());
 await client.verifyRecoveryFence(fence);
 for(const change of [{owners:['one']},{owners:['one','two','extra']},{owners:['one','one','two']},{phase:'unknown'},{instanceId:'other'}]){
  current.fence={...fence,phase:'held',owners:['one','two'],...change};await assert.rejects(client.verifyRecoveryFence(fence));
 }
 current.fence={...fence,phase:'held',owners:['one','two']};current.activeAdmissions=1;await assert.rejects(client.verifyRecoveryFence(fence));current.activeAdmissions=0;
 const noCensus=await serveHostControl(opts);t.after(()=>noCensus.close());const absent=new HostControlClient({dataScope:'fixture',connect:()=>({url:noCensus.url,token:opts.token,dataScope:'fixture'})});t.after(()=>absent.close());await assert.rejects(absent.verifyRecoveryFence(fence));
});

test('installed public host full recovery census drives actual supervisor reset, without releasing the host fence', {skip:!process.env.DISTRIBUTION_TEST_HOST_MODULE},async t=>{
 const {createHost}=await import(process.env.DISTRIBUTION_TEST_HOST_MODULE);
 const dir=await mkdtemp(join(tmpdir(),'reset-real-host-'));t.after(()=>rm(dir,{recursive:true,force:true}));
 const owners=['business','storage'];let acquired=0;
 const host=await createHost({stateDirectory:join(dir,'host'),allowedWorkspaceRoots:[dir],engines:[{id:'unused',command:'/no-engine'}],quiescence:{instanceId:'instance-one',dataScope:'fixture',requiredOwners:owners,coverage:{},participants:owners.map(id=>({id,acquire:async c=>{acquired++;return {ownerId:id,fenceId:c.fenceId,release:async()=>{}};}})),verifyRelease:async()=>{throw Error('not released by reset');}}});t.after(()=>host.close());
 const server=await serveHostControl({host,token:'c'.repeat(64),recoveryOwners:owners,inspectRunning:()=>({identity:initial.identity,instanceId:'instance-one',dataScope:'fixture',ready:true})});t.after(()=>server.close());
 const client=new HostControlClient({dataScope:'fixture',connect:()=>({url:server.url,token:'c'.repeat(64),dataScope:'fixture'})});t.after(()=>client.close());
 const f=await fixture(t,{verifyRecoveryFence:client.verifyRecoveryFence});const transport=await serveSupervisor({owner:f.owner,token:'b'.repeat(64)});t.after(()=>transport.close());const remote=new SupervisorClient({url:transport.url,token:'b'.repeat(64)});t.after(()=>remote.close());
 const admission=await host.admitQuiescence({commandId:'recovery-one',purpose:'recovery'});assert.equal(admission.admitted,true);assert.equal(acquired,2);
 const actual={...fence,fenceId:admission.fenceId};
 const review=(await remote.appReset.perform('prepare',{commandId:'prepare',parts:['updates.preferences'],privateContentReviewed:true},actual)).receipt.result;
 assert.ok(review);const applied=await remote.appReset.perform('apply',{commandId:'apply',preparedId:review.preparedId,reviewHash:review.reviewHash},actual);assert.equal(applied.receipt.state,'succeeded');
 assert.equal(host.inspectQuiescence().fence.phase,'held');assert.equal(host.inspectQuiescence().intakeClosed,true);
 assert.deepEqual(f.owner.inspect().preferences,disabled);
});
