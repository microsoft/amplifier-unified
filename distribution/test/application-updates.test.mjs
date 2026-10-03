import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {DistributionUpdateOwner} from '@amplifier/unified-distribution-update-owner';
import {createApplicationUpdateCapabilities} from '../src/application-updates.js';

const initial={id:'v1',version:'1.0.0',revision:'a'.repeat(40),digest:'a'.repeat(64)};
const next={id:'v2',version:'2.0.0',revision:'b'.repeat(40),digest:'b'.repeat(64)};
const deferred=()=>{let resolve;const promise=new Promise(yes=>{resolve=yes;});return {promise,resolve};};
const invoke=(facade,operation,args={},commandId=operation)=>facade.action({version:1,channel:'ahp-root://',topic:'application-updates',operation:'updates.application.'+operation,args,commandId},{account:'owned'});
async function fixture(t,{restart}={}){
 const directory=await mkdtemp(join(tmpdir(),'application-updates-')),listeners=new Set(),calls={restart:0,check:0};
 let running={identity:initial,instanceId:'first',dataScope:'owned',ready:true};
 const owner=new DistributionUpdateOwner({directory,dataScope:'owned',initial:{identity:initial,handle:'v1'},preferences:{autoCheck:false,autoInstall:false,intervalMs:1000},
  releases:{check:async()=>{calls.check++;return {releases:[initial,next],recommendedId:'v2'};},prepare:async identity=>({identity,handle:identity.id}),verify:async()=>true},
  lifecycle:{inspect:async()=>running,admitRestart:async()=>({evidence:{activeWork:0,intakeClosed:true,instanceId:running.instanceId,dataScope:'owned',observedAt:Date.now()},release:()=>{}}),restart:async request=>{calls.restart++;if(restart)await restart(request);running={identity:request.target.identity,instanceId:request.instanceId,dataScope:request.dataScope,ready:true};}},
  onChange:()=>{for(const listener of listeners)listener();}});
 const supervisor={outlivesDistribution:true,owner,subscribe(listener){listeners.add(listener);return ()=>listeners.delete(listener);}};
 const create=()=>createApplicationUpdateCapabilities({supervisor,authorize:async context=>{if(context.account!=='owned')throw Error('Account denied');}});
 t.after(async()=>{await owner.close();await rm(directory,{recursive:true,force:true});});
 return {owner,supervisor,create,calls,listeners,setRunning:value=>{running=value;}};
}

test('closing the child facade leaves its durable supervisor alive; a new facade recovers the exact result',async t=>{
 const started=deferred(),finish=deferred(),f=await fixture(t,{restart:async()=>{started.resolve();await finish.promise;}});
 const first=f.create();await invoke(first,'check',{},'check');await f.owner.waitFor('check');
 const accepted=await invoke(first,'install',{},'install');assert.equal(accepted.accepted,true);await started.promise;
 await first.close();assert.equal(f.listeners.size,0);finish.resolve();assert.equal((await f.owner.waitFor('install')).status,'succeeded');
 const second=f.create();const recovered=await invoke(second,'receipt',{commandId:'install'});assert.equal(recovered.result.receipt.status,'succeeded');assert.equal(recovered.result.replayed,false);
 assert.equal((await invoke(second,'running')).result.identity.id,'v2');assert.equal(f.calls.restart,1);assert.equal(f.calls.check,1);
 await invoke(second,'receipt',{commandId:'missing'});assert.equal(f.calls.restart,1);await second.close();
});

test('unknown replacement is reconciled through authenticated identity without repeating a restart',async t=>{
 let replacement;const f=await fixture(t,{restart:async request=>{replacement=request;throw Error('Lost reply');}}),facade=f.create();
 await invoke(facade,'check',{},'check');await f.owner.waitFor('check');await invoke(facade,'install',{},'install');assert.equal((await f.owner.waitFor('install')).status,'unknown');
 assert.equal((await invoke(facade,'reconcile',{commandId:'install'})).result.receipt.status,'unknown');
 f.setRunning({identity:next,instanceId:replacement.instanceId,dataScope:'owned',ready:true});
 assert.equal((await invoke(facade,'reconcile',{commandId:'install'})).result.receipt.status,'succeeded');assert.equal(f.calls.restart,1);
 await assert.rejects(facade.action({version:1,channel:'ahp-root://',topic:'application-updates',operation:'updates.application.check',args:{},commandId:'denied'},{account:'foreign'}),/Account denied/);
 await assert.rejects(invoke(facade,'check',{unexpected:true},'extra'),/arguments/);assert.equal(f.calls.check,1);
 await assert.rejects(facade.read({topic:'application-updates',scope:'session',uri:'amplifier-capability://application-updates'},{account:'owned'}),/host scope/);
 assert.throws(()=>createApplicationUpdateCapabilities({supervisor:{...f.supervisor,outlivesDistribution:false},authorize:()=>{}}),/external supervisor/);
 await facade.close();
});
