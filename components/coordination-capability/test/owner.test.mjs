import {test} from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,writeFile,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';const {createCoordinationCapabilities}=await import(process.env.COORDINATION_NODE_MODULE??'../dist/index.js');
const executable=process.env.COORDINATION_PYTHON;const session='ahp-session:/selected';
async function fixture(t,overrides={}){
 const directory=await mkdtemp(join(tmpdir(),'coordination-peer-')),config=join(directory,'config.json');await writeFile(config,JSON.stringify({dataDir:join(directory,'owner')}));
 const calls=[],listeners=new Set();let results=[],lost=false;
 const owner=createCoordinationCapabilities({owner:{command:executable,cwd:directory,args:['-m','amplifier_unified_coordination.server','--config',config]},
 listCoordinationSessions:async args=>{calls.push(['list',args]);return {items:[{uri:session}],coverage:{source:'host-indexed'}};},
 readCoordinationSession:async(s,args)=>{calls.push(['read',s,args]);return {available:true,status:'working',results:results.filter(r=>r.sequence>(args.afterSequence??0)),latestSequence:results.at(-1)?.sequence??0,approvalIds:[],coverage:{source:'host-indexed'},canFollowup:true};},
 readCoordinationWorkers:async(s,args)=>{assert.equal(args.clientId,undefined);return {available:true,item:{workerId:'child',status:'idle',persistent:true,canFollowup:true},results,latestSequence:results.at(-1)?.sequence??0};},
 controlCoordinationWorker:async(...args)=>{calls.push(['worker',...args]);if(lost)throw Error('response lost');return {accepted:true,completed:false};},
 controlCoordinationSession:async args=>{calls.push(['control',args]);return {accepted:true,inputId:args.commandId};},
 observeSession:async(s,listener)=>{assert.equal(s,session);listeners.add(listener);return ()=>listeners.delete(listener);},...overrides});
 t.after(async()=>{await owner.close();await rm(directory,{recursive:true,force:true});});
 const action=(operation,args,commandId='read',context={clientId:'client',origin:'ui'})=>owner.action({version:1,topic:'coordination',channel:'ahp-root://',operation:'coordination.'+operation,commandId,args},context);
 return {owner,calls,listeners,action,setResults:value=>results=value,lose:()=>lost=true};
}
test('independent installed owner uses indexed pages and wakes only explicit scoped waits',{skip:!executable},async t=>{
 const f=await fixture(t);const schemas=await f.owner.actionSchemas();assert.ok(schemas['coordination.wait'].schema);assert.equal(schemas['coordination.wait'].schema.properties.targets.maxItems,8);
 const page=await f.owner.read({uri:'amplifier-capability://coordination/coordination?scope=host',topic:'coordination',scope:'host',clientId:'client'});assert.equal(page.data.coordination.workersNotLoaded,true);assert.deepEqual(f.calls.map(v=>v[0]),['list']);
 const first=await f.action('wait',{targets:[{sessionId:session}]});const cursor=first.result.targets[0].nextCursor;assert.equal(first.result.targets[0].questionIds,null);
 const wait=f.action('wait',{targets:[{sessionId:session,afterCursor:cursor}],waitMs:1000});while(!f.listeners.size)await new Promise(r=>setTimeout(r,1));await new Promise(r=>setTimeout(r,20));
 f.setResults([{id:'exact-result',sequence:1,text:'real report'}]);f.owner.changed(session);const settled=await wait;assert.equal(settled.result.targets[0].results[0].id,'exact-result');assert.equal(f.listeners.size,0);
 assert.deepEqual(settled.invalidate,['coordination']);
});
test('agent authority and unknown worker commands survive independent transport without replay',{skip:!executable},async t=>{
 const f=await fixture(t);await assert.rejects(f.action('followup',{sessionId:session,text:'unauthorized'},'cross',{clientId:'client',origin:'agent',session:'ahp-session:/other'}),/explicit user/);
 const own=await f.action('followup',{sessionId:session,text:'own continuation'},'own',{clientId:'client',origin:'agent',session});assert.equal(own.result.receipt.status,'accepted');assert.equal(f.calls.find(v=>v[0]==='control')[1].origin,'agent');
 f.lose();const args={sessionId:session,workerId:'child',text:'only once'};await assert.rejects(f.action('followup',args,'lost'),/lost/);
 const repeated=await f.action('followup',args,'lost');assert.equal(repeated.result.receipt.status,'unknown');assert.equal(f.calls.filter(v=>v[0]==='worker').length,1);
 const receipt=await f.action('command',{commandId:'lost'});assert.equal(receipt.result.receipt.status,'unknown');
});

test('selected question coverage preserves host approvals and partial attention',{skip:!executable},async t=>{
 let complete=false,truncated=false;
 const f=await fixture(t,{readCoordinationSession:async()=>({available:true,status:'working',results:[],latestSequence:0,approvalIds:['approval'],attentionCoverage:{approvals:true,approvalsTruncated:truncated,questions:false}}),readCoordinationAttention:async()=>({questionIds:['question'],attentionComplete:complete,attentionCoverage:{questions:complete,approvals:false}})});
 const read=async()=>(await f.action('wait',{targets:[{sessionId:session}]})).result.targets[0];
 let row=await read();assert.equal(row.attentionCoverage.approvals,true);assert.equal(row.attentionCoverage.questions,false);assert.equal(row.attentionUnknown,true);
 complete=true;row=await read();assert.equal(row.attentionUnknown,false);assert.equal(row.attentionCoverage.questions,true);
 truncated=true;row=await read();assert.equal(row.attentionCoverage.approvalsTruncated,true);assert.equal(row.attentionUnknown,true);
});
test('owner reply deadlines clean pending calls without implicit retries',async()=>{
 const {OwnerConnection}=await import(process.env.COORDINATION_NODE_MODULE??'../dist/index.js');
 const code=`const r=require('node:readline').createInterface({input:process.stdin});let calls=0;r.on('line',line=>{const v=JSON.parse(line);calls++;if(v.method==='initialize'||v.method==='inspect')process.stdout.write(JSON.stringify({id:v.id,result:v.method==='initialize'?{protocolVersion:1}:{calls}})+'\\n');});`;
 const peer=new OwnerConnection({command:process.execPath,args:['-e',code],requestTimeoutMs:50,initializeTimeoutMs:500},async()=>{},()=>{});
 try{await assert.rejects(peer.request('mutate',{}),/timed out/);const receipt=await peer.request('inspect',{});assert.equal(receipt.calls,3);}finally{await peer.close();}
 const absent=new OwnerConnection({command:process.execPath,args:['-e',`process.stdin.resume();`],initializeTimeoutMs:50},async()=>{},()=>{});
 try{await assert.rejects(absent.request('mutate',{}),/initialization|timed out/);await assert.rejects(absent.request('mutate',{}),/closed/);}finally{await absent.close();}
});

test('real coordination owner holds intake while passive waits continue and wakes after an admitted effect settles',{skip:!executable},async t=>{
 let enter,finish,wakes=0;const entered=new Promise(r=>enter=r),gate=new Promise(r=>finish=r);
 const f=await fixture(t,{onMayBeIdle:()=>wakes++,controlCoordinationSession:async()=>{enter();await gate;return {accepted:true};}});
 const participant=f.owner.quiescenceParticipant('coordination'),context={fenceId:'fence',commandId:'update',purpose:'distribution-update',instanceId:'host-one',dataScope:'data'};
 const active=f.action('followup',{sessionId:session,text:'once'},'one');await entered;
 assert.equal(await participant.acquire(context),null);finish();await active;assert.equal(wakes,1);
 assert.equal(await participant.acquire(context),null);const fresh={...context,fenceId:'fresh-fence',commandId:'fresh-command'};
 const lease=await participant.acquire(fresh);assert.equal(lease.fenceId,fresh.fenceId);
 await assert.rejects(f.action('interrupt',{sessionId:session},'blocked'),/intake is closed/);
 assert.equal((await f.action('command',{commandId:'one'})).result.receipt.status,'accepted');
 assert.ok((await f.action('wait',{targets:[{sessionId:session}],waitMs:0})).result.targets.length);
 await lease.release('unchanged',{verified:true,...fresh,outcome:'unchanged',receiptId:'exact'});
 assert.equal(f.owner.quiescenceAccess['coordination.wait'],'read');assert.equal(f.owner.quiescenceAccess['coordination.followup'],undefined);
});
