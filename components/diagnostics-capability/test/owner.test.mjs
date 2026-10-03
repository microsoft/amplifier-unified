import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm,readFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import {randomUUID} from 'node:crypto';
const {createDiagnosticsCapability}=await import(process.env.DIAGNOSTICS_PACKAGE??'../src/index.js');
const python=process.env.DIAGNOSTICS_PYTHON??resolve(import.meta.dirname,'../python/.venv/bin/python');
const context={clientId:'client-one',account:'owned',origin:'ui'};
const fence={fenceId:'fence',commandId:'update',purpose:'distribution-update',instanceId:'before',dataScope:'owned'};
const proof={verified:true,...fence,outcome:'unchanged',receiptId:'actual-outcome'};
async function fixture(t){
 const dir=await mkdtemp(join(tmpdir(),'diagnostics-consumer-')),path=join(dir,'config.json');await writeFile(path,JSON.stringify({stateDirectory:join(dir,'state')}),{mode:0o600});const changed=[];
 const owner=createDiagnosticsCapability({owner:{command:python,args:['-I','-m','amplifier_unified_diagnostics.server','--config',path]},onInvalidate:(...args)=>changed.push(args)});
 t.after(async()=>{await owner.close();await rm(dir,{recursive:true,force:true})});
 const action=async(operation,args={},caller=context,id=randomUUID())=>(await owner.action({version:1,topic:'diagnostics',channel:'ahp-root://',operation:'diagnostics.'+operation,args,commandId:id},caller)).result;
 return {owner,action,dir,changed};
}
async function until(work,predicate){for(let n=0;n<300;n++){const result=await work();if(predicate(result))return result;await new Promise(r=>setTimeout(r,10))}throw Error('Observation did not settle')}

test('installed Python owner shares settings and records while observations remain scoped, bounded and private',async t=>{
 const {owner,action,changed}=await fixture(t),state=await action('get');assert.equal(state.config.enabled,false);
 const args={expectedRevision:0,config:{...state.config,enabled:true}};
 const saved=await action('configure',args,context,'save-settings');assert.equal(saved.revision,1);
 assert.deepEqual(await action('configure',args,{...context,clientId:'second'},'save-settings'),saved);
 for(const session of ['ahp-session:/one','ahp-session:/other'])owner.observe({stream:'app',session,workspace:'/owned/workspace',event:'app:action',data:{status:'done',prompt:'NOT_CAPTURED',text:'NOT_CAPTURED'}});
 await until(()=>action('get'),result=>result.local.records===2);
 const selected=await action('records',{}, {...context,origin:'agent',session:'ahp-session:/one'});assert.equal(selected.items.length,1);assert.equal(selected.items[0].session,'ahp-session:/one');assert.ok(!JSON.stringify(selected).includes('NOT_CAPTURED'));
 await assert.rejects(action('records',{sessionId:'ahp-session:/other'},{...context,origin:'agent',session:'ahp-session:/one'}),/own conversation/);
 assert.ok(changed.every(row=>row[0]==='diagnostics'&&row[1]==='host'));
 const snapshot=await owner.read({topic:'diagnostics',scope:'host',uri:owner.manifest.topics.diagnostics.uri});assert.equal(snapshot.data.diagnostics.local.records,2);
 assert.ok(Buffer.byteLength(JSON.stringify(snapshot))<8192);
});

test('held intake remains readable, closes the observation queue and releases only with exact proof',async t=>{
 const {owner,action}=await fixture(t),state=await action('get');await action('configure',{expectedRevision:0,config:{...state.config,enabled:true}});
 for(let n=0;n<40;n++)owner.observe({stream:'app',session:'ahp-session:/one',workspace:'/owned',event:'app:event',data:{count:n}});
 const lease=await owner.quiescenceParticipant.acquire(fence);assert.ok(lease);
 const held=await action('get');assert.ok(held.local.records<=32);assert.ok(held.local.transportDropped>=8);
 assert.equal(owner.observe({stream:'app',session:'ahp-session:/one',workspace:'/owned',event:'app:held',data:{}}),false);
 assert.equal((await action('configure',{expectedRevision:1,config:held.config})).executed,false);
 await assert.rejects(lease.release('unchanged',{...proof,receiptId:''}));
 await lease.release('unchanged',proof);assert.equal((await owner.inspectQuiescence()).intakeClosed,false);
 assert.equal(owner.observe({stream:'app',session:'ahp-session:/one',workspace:'/owned',event:'app:after',data:{}}),true);
 await until(()=>action('get'),row=>row.local.records===held.local.records+1);
});

test('native observations ignore streaming deltas and have no history or session enumeration callback',async t=>{
 const {owner,action}=await fixture(t),state=await action('get');await action('configure',{expectedRevision:0,config:{...state.config,enabled:true}});
 const selected={session:'ahp-session:/one',workingDirectory:'/owned',nativeSessionId:'native'};
 await owner.nativeEvent(selected,{event:{type:'assistant.delta',data:{text:'PRIVATE'}}});
 await owner.nativeEvent(selected,{event:{type:'tool.completed',data:{tool:'fixture',status:'completed',args:{key:'PRIVATE'},result:'PRIVATE'}}});
 await until(()=>action('get'),row=>row.local.records===1);
 const page=await action('records');assert.equal(page.items[0].stream,'tools');assert.equal(page.items[0].data.tool,'fixture');assert.ok(!JSON.stringify(page).includes('PRIVATE'));
 assert.equal((await action('get')).capture.historicalScan,false);
});
