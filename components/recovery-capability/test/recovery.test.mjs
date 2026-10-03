import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';import {createHash,randomUUID} from 'node:crypto';import {DatabaseSync} from 'node:sqlite';import {spawn,spawnSync} from 'node:child_process';
const {createRecoveryCapabilities}=await import(process.env.RECOVERY_PACKAGE_MODULE??'../dist/index.js');
const hostModule=process.env.RECOVERY_HOST_MODULE;
const {createHost}=hostModule?await import(hostModule):{};
const sha=value=>createHash('sha256').update(value).digest('hex');
const session='ahp-session:/selected',context={clientId:'owner',origin:'ui'};
const wait=async(fn)=>{for(let n=0;n<500;n++){const value=await fn();if(value)return value;await new Promise(r=>setTimeout(r,5));}throw Error('Condition unavailable');};
function nativeFixture(){
 const calls=[],receipts=new Map(),bytes=Buffer.from('private selected archive fixture');let lease,changed=false,loseSnapshot=false,unknownSnapshot=false,hold,lostPlan;
 const previewHash=sha('unchanged fixture');
 return {calls,receipts,bytes,setChanged:v=>changed=v,setLost:v=>loseSnapshot=v,setUnknown:v=>unknownSnapshot=v,setHold:v=>hold=v,
  setLostPlan:v=>lostPlan=v,
  nativeAdmin:async(op,args)=>{
   calls.push([op,structuredClone(args)]);
   if(op==='maintenance.preview'||op==='maintenance.reset.preview')return {previewHash,coverage:'explicit-selected-native-only',containsPrivateContent:true,spec:{...args,parts:args.parts??['session-state']},inventory:{files:Array.from({length:55},(_,i)=>({path:'history/file-'+i,bytes:1,sha256:sha(String(i))})),missing:[],excluded:[],bytes:55},omissions:['all unselected native histories','all other product owners'],credentialCoverage:'explicitly excluded keys.env',...(op.endsWith('reset.preview')?{reset:{scope:'session-configuration',canonicalFilesChanged:0}}:{})};
   if(op.startsWith('maintenance.archive.')&&op!=='maintenance.archive.manifest'){
    const result=op.endsWith('create')?{planId:'a'.repeat(32),revision:0}:op.endsWith('add')?{planId:'a'.repeat(32),revision:1}:{previewHash,archivePlanId:'a'.repeat(32),coverage:'explicit-selected-native-only',spec:{parts:['shared-configuration']},inventory:{entries:120,files:110,bytes:256},omissions:['independent product stores'],exclusions:['transient locks'],credentialCoverage:'keys excluded'};
    const receipt={state:'succeeded',commandId:args.commandId,operation:op,result};receipts.set(args.commandId,receipt);if(lostPlan===op)throw Error('Lost plan response');return {...result,receipt};
   }
   if(op==='maintenance.archive.manifest'){const offset=args.cursor?Number(args.cursor):0,items=Array.from({length:Math.min(args.limit,120-offset)},(_,i)=>({path:'shared-configuration/'+String(i+offset),status:'included',bytes:1,sha256:sha(String(i+offset))}));return {previewHash,coverage:'explicit-selected-native-only',items,nextCursor:offset+items.length<120?String(offset+items.length):null};}
   if(op==='maintenance.acquire'){
    if(changed){const error=Object.assign(Error('Source changed'),{data:{executed:false,reason:'native-maintenance-refused'}});receipts.set(args.commandId,{state:'refused',executed:false});throw error;}
    lease='owned-native-lease';receipts.set(args.commandId,{state:'succeeded',result:{leaseId:lease}});return {active:true,leaseId:lease};
   }
   if(op==='maintenance.snapshot'){
    if(hold)await hold;
    if(unknownSnapshot){receipts.set(args.commandId,{state:'unknown'});throw Error('Transport ended during uncertain effect');}
    const result={artifactId:'artifact-private',sha256:sha(bytes),bytes:bytes.length,contentType:'application/x-tar',coverage:'explicit-selected-native-only'};receipts.set(args.commandId,{state:'succeeded',result});if(loseSnapshot)throw Error('Lost acknowledgement');return {...result,receipt:receipts.get(args.commandId)};
   }
   if(op==='maintenance.release'){assert.equal(args.leaseId,lease);lease=undefined;return {released:true,active:false,leaseId:args.leaseId};}
   if(op==='maintenance.receipt')return {receipt:receipts.get(args.commandId)??null};
   if(op==='maintenance.artifact.read'){const part=bytes.subarray(args.offset,args.offset+args.maxBytes);return {artifactId:'artifact-private',sha256:sha(bytes),bytes:bytes.length,offset:args.offset,encoding:'base64',data:part.toString('base64'),chunkSha256:sha(part),nextOffset:args.offset+part.length<bytes.length?args.offset+part.length:null};}
   throw Error('Unexpected native operation '+op);
  }};
}
async function fixture(extra={}){
 const directory=await mkdtemp(join(tmpdir(),'recovery-owner-')),native=nativeFixture();let host,owner,releaseLost=false,participantLost=false;
 const port={admitQuiescence:args=>host.admitQuiescence(args),inspectQuiescence:()=>host.inspectQuiescence(),quiescenceReceipt:id=>host.quiescenceReceipt(id),withQuiescenceMaintenance:(input,fn)=>host.withQuiescenceMaintenance(input,fn),releaseQuiescence:async args=>{const result=await host.releaseQuiescence(args);if(releaseLost&&args.outcome==='unchanged'){releaseLost=false;throw Error('Lost host release acknowledgement');}return result;}};
 const options={directory:join(directory,'owner'),nativeAuthority:'fixture-native',nativeAdmin:native.nativeAdmin,authorize:async ctx=>({accountId:ctx.clientId==='other'?'other-account':'owner-account'}),resolveSession:async uri=>({nativeSessionId:uri.split('/').at(-1),historyCwd:directory,nativeAuthority:'fixture-native'}),quiescence:port,...extra};
 owner=createRecoveryCapabilities(options);
 host=await createHost({stateDirectory:join(directory,'host'),allowedWorkspaceRoots:[directory],engines:[{id:'never-start',command:'/impossible/native-worker'}],capabilities:owner,quiescence:{instanceId:'test-launch',dataScope:'test-private-data',requiredOwners:['recovery'],coverage:{capabilities:{recovery:'recovery'}},participants:[{id:'recovery',acquire:async c=>{const lease=await owner.quiescenceParticipant.acquire(c);return lease?{...lease,release:async(...args)=>{await lease.release(...args);if(participantLost){participantLost=false;throw Error('Lost participant release response');}}}:null;},reconcileRelease:c=>owner.quiescenceParticipant.reconcileRelease(c)}],verifyRelease:async request=>{
  const proof=owner.readReleaseEvidence(request);assert.ok(proof,'release must read durable owner proof');assert.equal(proof.instanceId,'test-launch');assert.equal(proof.dataScope,'test-private-data');assert.equal(proof.nativeLeaseReleased,true);
  return {verified:true,fenceId:request.fenceId,commandId:request.commandId,outcome:'unchanged',instanceId:proof.instanceId,dataScope:proof.dataScope,receiptId:proof.receiptId};
 }}});
 let seq=0;const call=(operation,args={},ctx=context,commandId='action-'+(++seq))=>host.invokeCapability({version:1,topic:'recovery',channel:'ahp-root://',operation,args,commandId},{actorId:ctx.clientId,clientId:ctx.clientId,origin:ctx.origin});
 const job=async id=>(await call('recovery.job',{jobId:id})).result;
 const settled=id=>wait(async()=>{const row=await job(id);return ['prepared','succeeded','refused','unknown'].includes(row.state)?row:false;});
 const prepare=async()=>{const admitted=await call('recovery.prepare',{sessions:[session],parts:['session-history','session-state'],privateContentReviewed:true});assert.equal(admitted.result.state,'queued');return settled(admitted.result.id);};
 return {directory,native,get owner(){return owner;},host,call,job,settled,prepare,loseRelease:()=>releaseLost=true,loseParticipantRelease:()=>participantLost=true,restartOwner:async()=>{await owner.close();owner=createRecoveryCapabilities(options);},close:async()=>{await owner.close();await host.close();await rm(directory,{recursive:true,force:true});}};
}
test('installed host and owner admit before asynchronous quiescence; bounded review and account-private archive',{skip:!hostModule},async()=>{
 const f=await fixture();try{
  assert.equal(f.native.calls.length,0);const prepared=await f.prepare();assert.equal(prepared.state,'prepared');assert.equal(f.host.inspectQuiescence().intakeClosed,false);
  const first=(await f.call('recovery.preview',{jobId:prepared.id,limit:25})).result;assert.equal(first.items.length,25);assert.ok(first.nextCursor);assert.equal(first.totalEntries,55);assert.ok(!JSON.stringify(first).includes(f.directory));
  const second=(await f.call('recovery.preview',{jobId:prepared.id,cursor:first.nextCursor,limit:25})).result;assert.equal(second.items[0].path,'history/file-25');
  const admitted=await f.call('recovery.snapshot',{preparedJobId:prepared.id,previewHash:prepared.previewHash},context,'snapshot-once');const saved=await f.settled(admitted.result.id);assert.equal(saved.state,'succeeded');assert.equal(f.host.inspectQuiescence().intakeClosed,false);
  const page=await f.owner.resourceRead({channel:'ahp-root://',uri:saved.result.resourceUri,encoding:'base64'},context);assert.equal(Buffer.from(page.data,'base64').toString(),f.native.bytes.toString());
  await assert.rejects(f.owner.resourceRead({channel:'ahp-root://',uri:saved.result.resourceUri},{clientId:'other',origin:'ui'}),/unavailable/);
  await assert.rejects(f.call('recovery.job',{jobId:saved.id},{clientId:'other',origin:'ui'}),/unavailable/);
  const repeated=await f.owner.action({version:1,topic:'recovery',channel:'ahp-root://',operation:'recovery.snapshot',args:{preparedJobId:prepared.id,previewHash:prepared.previewHash},commandId:'snapshot-once'},context);assert.equal(repeated.result.id,saved.id);assert.equal(f.native.calls.filter(([op])=>op==='maintenance.snapshot').length,1);
  await assert.rejects(f.owner.action({version:1,topic:'recovery',channel:'ahp-root://',operation:'recovery.prepare',args:{sessions:['ahp-session:/other'],parts:['session-history'],privateContentReviewed:true},commandId:'cross-agent'},{clientId:'owner',origin:'agent',session}),/another conversation/);
 }finally{await f.close();}
});
test('stale reviewed files refuse before effect, conclusively release intake and require fresh review',{skip:!hostModule},async()=>{
 const f=await fixture();try{const prepared=await f.prepare();f.native.setChanged(true);const admitted=await f.call('recovery.snapshot',{preparedJobId:prepared.id,previewHash:prepared.previewHash});const result=await f.settled(admitted.result.id);assert.equal(result.state,'refused');assert.equal(f.host.inspectQuiescence().intakeClosed,false);assert.equal(f.native.calls.filter(([op])=>op==='maintenance.snapshot').length,0);}finally{await f.close();}
});
test('uncertain effects hold host and owner intake; passive reconciliation never repeats work',{skip:!hostModule},async()=>{
 const f=await fixture();try{
  const prepared=await f.prepare();f.native.setUnknown(true);const admitted=await f.call('recovery.snapshot',{preparedJobId:prepared.id,previewHash:prepared.previewHash});const result=await f.settled(admitted.result.id);assert.equal(result.state,'unknown');assert.equal(f.host.inspectQuiescence().intakeClosed,true);
  await assert.rejects(f.call('recovery.prepare',{sessions:[session],parts:['session-history'],privateContentReviewed:true}),/intake is closed/);
  const observed=await f.call('recovery.reconcile',{jobId:result.id});assert.equal(observed.result.state,'unknown');assert.equal(f.native.calls.filter(([op])=>op==='maintenance.snapshot').length,1);
  const listed=await f.call('recovery.list');assert.ok(listed.result.items.some(row=>row.id===result.id));
 }finally{await f.close();}
});
test('lost snapshot and host release acknowledgements reconcile exact receipts without replay',{skip:!hostModule},async()=>{
 const f=await fixture();try{
  const prepared=await f.prepare();f.native.setLost(true);const admitted=await f.call('recovery.snapshot',{preparedJobId:prepared.id,previewHash:prepared.previewHash});const unknown=await f.settled(admitted.result.id);assert.equal(unknown.state,'unknown');f.loseRelease();
  const first=await f.call('recovery.reconcile',{jobId:unknown.id});assert.equal(first.result.state,'unknown');assert.equal(f.host.inspectQuiescence().intakeClosed,false);
  const second=await f.call('recovery.reconcile',{jobId:unknown.id});assert.equal(second.result.state,'succeeded');assert.equal(f.native.calls.filter(([op])=>op==='maintenance.snapshot').length,1);
 }finally{await f.close();}
});
test('participant does not ignore unrelated jobs and receipt page uses indexed metadata',{skip:!hostModule},async()=>{
 const f=await fixture();try{
  const admitted=await f.owner.action({version:1,topic:'recovery',channel:'ahp-root://',operation:'recovery.prepare',commandId:'own-prepare',args:{sessions:[session],parts:['session-history'],privateContentReviewed:true}},context);
  const other=await f.owner.quiescenceParticipant.acquire({fenceId:'other',commandId:'unrelated',purpose:'distribution-update',instanceId:'test-launch',dataScope:'test-private-data'});assert.equal(other,null);
  await f.settled(admitted.result.id);
  const db=new DatabaseSync(join(f.directory,'owner/recovery.sqlite'));const plan=db.prepare('EXPLAIN QUERY PLAN SELECT id,command,state,created,revision FROM jobs WHERE account=? ORDER BY created DESC,id DESC LIMIT ?').all('owner-account',26);assert.ok(plan.some(row=>row.detail.includes('jobs_account_page')));db.close();
 }finally{await f.close();}
});

test('restarted owner marks a queued command unknown and never executes or silently retries it',{skip:!hostModule},async()=>{
 const f=await fixture();try{
  const request={version:1,topic:'recovery',channel:'ahp-root://',operation:'recovery.prepare',commandId:'crash-before-admission',args:{sessions:[session],parts:['session-history'],privateContentReviewed:true}};
  const admitted=await f.owner.action(request,context);assert.equal(admitted.result.state,'queued');await f.restartOwner();
  const receipt=(await f.owner.action({version:1,topic:'recovery',channel:'ahp-root://',operation:'recovery.command',commandId:'inspect-restart',args:{commandId:'crash-before-admission'}},context)).result;
  assert.equal(receipt.state,'unknown');assert.equal(receipt.reason,'owner-restarted-no-replay');assert.equal(f.native.calls.length,0);
  const duplicate=await f.owner.action(request,context);assert.equal(duplicate.result.id,admitted.result.id);assert.equal(duplicate.result.state,'unknown');assert.equal(f.native.calls.length,0);
 }finally{await f.close();}
});

test('agent route retains exact selected session authority while the topic remains host scoped',{skip:!hostModule},async()=>{
 const f=await fixture();try{
  const ctx={clientId:'owner',origin:'agent',session:{uri:session}},request={version:1,topic:'recovery',channel:session,operation:'recovery.prepare',commandId:'agent-selected',args:{sessionId:session,sessions:[session],parts:['session-history'],privateContentReviewed:true}};
  const admitted=await f.owner.action(request,ctx);const ready=await f.settled(admitted.result.id);assert.equal(ready.state,'prepared');
  assert.equal(f.native.calls.find(([op])=>op==='maintenance.preview')[1].sessionId,undefined);
  const exact=await f.owner.action({version:1,topic:'recovery',channel:session,operation:'recovery.job',commandId:'agent-read',args:{jobId:ready.id,sessionId:session}},ctx);assert.equal(exact.result.id,ready.id);
  await assert.rejects(f.owner.action({...request,commandId:'wrong-agent',args:{...request.args,sessionId:'ahp-session:/other'}},ctx),/differs from authenticated/);
  await assert.rejects(f.owner.action({...request,commandId:'wrong-channel',channel:'ahp-session:/other'},ctx),/exact selected session/);
 }finally{await f.close();}
});

test('participant release persists an exact receipt through partial host release and rejects changed proof',{skip:!hostModule},async()=>{
 const f=await fixture();try{
  const prepared=await f.prepare();f.loseParticipantRelease();const admitted=await f.call('recovery.snapshot',{preparedJobId:prepared.id,previewHash:prepared.previewHash});const unknown=await f.settled(admitted.result.id);assert.equal(unknown.state,'unknown');assert.equal(f.host.inspectQuiescence().intakeClosed,true);
  const evidence=f.owner.readReleaseEvidence(unknown.intakeFence);assert.ok(evidence);assert.equal(evidence.nativeLeaseReleased,true);
  const recovered=await f.owner.action({version:1,topic:'recovery',channel:'ahp-root://',operation:'recovery.reconcile',commandId:randomUUID(),args:{jobId:unknown.id}},context);assert.equal(recovered.result.state,'succeeded');assert.equal(f.host.inspectQuiescence().intakeClosed,false);assert.equal(f.native.calls.filter(([op])=>op==='maintenance.snapshot').length,1);
  await f.restartOwner();assert.deepEqual(f.owner.readReleaseEvidence(unknown.intakeFence),evidence);
  await f.owner.quiescenceParticipant.reconcileRelease({...unknown.intakeFence,purpose:'recovery',instanceId:'test-launch',dataScope:'test-private-data',outcome:'unchanged',proof:{verified:true,...unknown.intakeFence,outcome:'unchanged',instanceId:'test-launch',dataScope:'test-private-data',receiptId:evidence.receiptId}});
  await assert.rejects(f.owner.quiescenceParticipant.reconcileRelease({...unknown.intakeFence,purpose:'recovery',instanceId:'test-launch',dataScope:'test-private-data',outcome:'unchanged',proof:{verified:true,...unknown.intakeFence,outcome:'unchanged',instanceId:'test-launch',dataScope:'test-private-data',receiptId:'changed-proof'}}),/different exact proof/);
 }finally{await f.close();}
});

function childSource(directory,stay=false){
 const module=process.env.RECOVERY_PACKAGE_MODULE??new URL('../dist/index.js',import.meta.url).href;
 return `import {createRecoveryCapabilities} from ${JSON.stringify(module)};const owner=createRecoveryCapabilities({directory:${JSON.stringify(directory)},nativeAuthority:'test',nativeAdmin:async()=>({}),authorize:async()=>({accountId:'test'}),resolveSession:async()=>({}),quiescence:{}});console.log('OWNED');${stay?"setInterval(()=>owner.actionSchemas(),1000)":"await owner.close()"};`;
}
test('exclusive lifetime ownership refuses another process before it can rewrite active job state',{skip:!hostModule},async()=>{
 const f=await fixture();try{
  const pending=await f.owner.action({version:1,topic:'recovery',channel:'ahp-root://',operation:'recovery.prepare',commandId:'live-owner',args:{sessions:[session],parts:['session-history'],privateContentReviewed:true}},context);
  const competitor=spawnSync(process.execPath,['--input-type=module','-e',childSource(join(f.directory,'owner'))],{encoding:'utf8'});assert.notEqual(competitor.status,0);assert.match(competitor.stderr,/exclusive ownership unavailable/);
  const db=new DatabaseSync(join(f.directory,'owner/recovery.sqlite'));assert.equal(db.prepare('SELECT state FROM jobs WHERE id=?').get(pending.result.id).state,'queued');db.close();assert.equal((await f.settled(pending.result.id)).state,'prepared');
 }finally{await f.close();}
});
test('OS ownership lease survives no stale-PID takeover and releases on actual owner process death',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'recovery-os-lease-'));const child=spawn(process.execPath,['--input-type=module','-e',childSource(directory,true)],{stdio:['ignore','pipe','pipe']});let output='',error='';child.stdout.on('data',b=>output+=b);child.stderr.on('data',b=>error+=b);
 try{
  await wait(()=>output.includes('OWNED')||child.exitCode!==null);assert.ok(output.includes('OWNED'),error);
  const refused=spawnSync(process.execPath,['--input-type=module','-e',childSource(directory)],{encoding:'utf8'});assert.notEqual(refused.status,0);
  const exited=new Promise(resolve=>child.once('exit',resolve));child.kill('SIGKILL');await exited;
  const replacement=spawnSync(process.execPath,['--input-type=module','-e',childSource(directory)],{encoding:'utf8'});assert.equal(replacement.status,0,replacement.stderr);assert.match(replacement.stdout,/OWNED/);
 }finally{if(child.exitCode===null&&child.signalCode===null)child.kill('SIGKILL');await rm(directory,{recursive:true,force:true});}
});

for(const phase of ['maintenance.archive.create','maintenance.archive.prepare'])test(`lost ${phase} reconciles only its exact private-plan receipt without replay`,{skip:!hostModule},async()=>{
 const f=await fixture();try{
  f.native.setLostPlan(phase);const admitted=await f.call('recovery.archive.prepare',{sessions:[session],parts:['shared-configuration'],privateContentReviewed:true});const unknown=await f.settled(admitted.result.id);assert.equal(unknown.state,'unknown');assert.equal(f.host.inspectQuiescence().intakeClosed,true);
  const settled=(await f.call('recovery.reconcile',{jobId:unknown.id})).result;assert.equal(settled.state,phase.endsWith('prepare')?'prepared':'refused');assert.equal(f.host.inspectQuiescence().intakeClosed,false);assert.equal(f.native.calls.filter(([op])=>op===phase).length,1);
  if(settled.state==='prepared'){
   const first=(await f.call('recovery.preview',{jobId:settled.id,limit:7})).result;assert.equal(first.totalEntries,120);assert.equal(first.items.length,7);
   const next=(await f.call('recovery.preview',{jobId:settled.id,limit:7,cursor:first.nextCursor})).result;assert.equal(next.items[0].path,'shared-configuration/7');assert.equal(f.native.calls.filter(([op])=>op==='maintenance.archive.prepare').length,1);
   await assert.rejects(f.call('recovery.preview',{jobId:settled.id},{clientId:'other',origin:'ui'}),/unavailable/);
  }
 }finally{await f.close();}
});
test('workspace configuration selection cannot widen selected conversation authority',{skip:!hostModule},async()=>{
 const f=await fixture();try{await assert.rejects(f.call('recovery.archive.prepare',{sessions:[session],workspaceConfigurationFor:['ahp-session:/other'],parts:['workspace-configuration'],privateContentReviewed:true}),/explicitly selected/);assert.equal(f.native.calls.length,0);}finally{await f.close();}
});
