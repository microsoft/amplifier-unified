import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rm,realpath} from 'node:fs/promises';
import {join} from 'node:path';import {tmpdir} from 'node:os';
const current=await import(process.env.PORTABILITY_MODULE??'../dist/index.js');
const python=process.env.PORTABILITY_PYTHON,acp=process.env.AMPLIFIER_ACP_PYTHON;
const oldModule=process.env.PORTABILITY_OLD_MODULE,oldPython=process.env.PORTABILITY_OLD_PYTHON;
const enabled=!!(python&&acp);
const ctx={commandId:'original-admission',fenceId:'original-fence',instanceId:'fixture-installation',dataScope:'owned-fixture',purpose:'distribution-update'};
const abort=(c=ctx)=>({...c,proof:{...c,kind:'distribution-admission-abort',verified:true,receiptId:'authenticated-abort:'+c.fenceId}});
const normal=(c=ctx)=>({...c,verified:true,outcome:'unchanged',receiptId:'ordinary-release:'+c.fenceId});
const denied=async()=>{throw Error('No history, provider or transfer effect authorized');};
async function fixture(){
 const root=await realpath(await mkdtemp(join(tmpdir(),'portability-admission-'))),work=join(root,'workspace');await mkdir(work);
 const config=join(root,'owner.json'),nativeConfig=join(root,'native.json');
 await writeFile(config,JSON.stringify({dataDir:join(root,'authority'),stageDir:join(work,'stages'),exchangeDir:join(root,'exchange'),workspaceRoots:[work]}));
 await writeFile(nativeConfig,JSON.stringify({home:join(root,'home'),appHome:join(root,'app'),transferAuthorityDirectory:join(root,'authority'),transferWorkspaceRoots:[work],adminWorkspaceRoots:[work]}));
 const env={AMPLIFIER_HOME:join(root,'home'),AMPLIFIER_WEB_HOME:join(root,'app'),AMPLIFIER_SESSION_STATE_HOME:join(root,'writers'),XDG_CACHE_HOME:join(root,'cache'),PYTHONDONTWRITEBYTECODE:'1'};
 const live=[];
 function open(module=current,ownerPython=python,ids=['native-transfer-a','native-transfer-b'],delayed=false){
  const proxy=String.raw`const {spawn}=require('node:child_process'),{createInterface}=require('node:readline');const p=spawn(process.argv[1],['-I','-B','-m','amplifier_acp','--config',process.argv[2]],{stdio:'pipe'});const delayed=new Set();createInterface({input:process.stdin}).on('line',line=>{const m=JSON.parse(line);if(m.method==='_amplifier/transfer/lifecycle'&&m.params.operation==='acquire')delayed.add(m.id);p.stdin.write(line+'\n');}).on('close',()=>p.stdin.end());createInterface({input:p.stdout}).on('line',line=>{const m=JSON.parse(line);if(delayed.delete(m.id))setTimeout(()=>process.stdout.write(line+'\n'),350);else process.stdout.write(line+'\n');});p.stderr.resume();p.on('exit',code=>{process.exitCode=code??1;});`;
  const peers=ids.map(ownerId=>new module.TransferConnection({command:delayed?process.execPath:acp,args:delayed?['-e',proxy,acp,nativeConfig]:['-I','-B','-m','amplifier_acp','--config',nativeConfig],requestTimeoutMs:delayed?100:5000,cwd:work,env,ownerId}));
  const cap=module.createPortabilityCapabilities({owner:{command:ownerPython,args:['-I','-B','-m','amplifier_unified_portability.server','--config',config],env},nativeParticipants:peers.map(p=>p.quiescenceParticipant),inspectSession:denied,beginTransfer:denied,commitTransfer:denied,cancelTransfer:denied,adoptTransferredSession:denied,nativeTransfer:denied,exportTransferEvidence:denied,stageTransferEvidence:denied,activateTransferEvidence:denied});
  const value={cap,peers,close:async()=>{await cap.close();for(const p of peers)await p.close();}};live.push(value);return value;
 }
 return {root,open,close:async()=>{for(const x of live)await x.close();await rm(root,{recursive:true,force:true});}};
}
test('actual two native peers settle durable original aggregate after process replacement',{skip:!enabled},async()=>{
 const f=await fixture();try{let x=f.open();assert.ok(await x.cap.quiescenceParticipant.acquire(ctx));assert.equal((await x.cap.inspectQuiescence()).intakeClosed,true);await x.close();x=f.open();const receipt=await x.cap.quiescenceParticipant.abortAdmission(abort());assert.deepEqual(Object.keys(receipt).sort(),['ownerId','status','receiptId','commandId','fenceId','instanceId','dataScope'].sort());assert.equal(receipt.ownerId,'portability');assert.equal(receipt.status,'released');assert.equal((await x.cap.inspectQuiescence()).intakeClosed,false);for(const p of x.peers)assert.equal((await p.inspectQuiescence()).intakeClosed,false);assert.deepEqual(await x.cap.quiescenceParticipant.abortAdmission(abort()),receipt);await assert.rejects(x.cap.quiescenceParticipant.abortAdmission({...abort(),proof:{...abort().proof,receiptId:'changed'}}));}finally{await f.close();}
});
test('generic unchanged lifecycle release cannot prove first admission-abort',{skip:!enabled},async()=>{
 const f=await fixture();try{let x=f.open();const lease=await x.cap.quiescenceParticipant.acquire(ctx);await lease.release('unchanged',normal());await x.close();x=f.open();await assert.rejects(x.cap.quiescenceParticipant.abortAdmission(abort()));assert.equal((await x.cap.inspectQuiescence()).intakeClosed,false);}finally{await f.close();}
});
test('explicit original pre-effect unwind can settle old receipt under newer same-owner hold',{skip:!enabled},async()=>{
 const f=await fixture();try{const x=f.open();const lease=await x.cap.quiescenceParticipant.acquire(ctx);await lease.release('unchanged',{kind:'admission-refused'});const newer={...ctx,commandId:'new-command',fenceId:'new-fence'};assert.ok(await x.cap.quiescenceParticipant.acquire(newer));const old=await x.cap.quiescenceParticipant.abortAdmission(abort());assert.equal(old.status,'released');assert.deepEqual((await x.cap.inspectQuiescence()).fence,newer);for(const p of x.peers)assert.deepEqual((await p.inspectQuiescence()).fence,newer);assert.deepEqual(await x.cap.quiescenceParticipant.abortAdmission(abort()),old);assert.equal((await x.cap.quiescenceParticipant.abortAdmission(abort(newer))).status,'released');}finally{await f.close();}
});
for(const outcome of ['unchanged','ready'])test(`old installed ${outcome} release survives replacement without manufacturing legacy abort evidence`,{skip:!enabled||!oldModule||!oldPython},async()=>{
 const old=await import(oldModule),f=await fixture();try{let x=f.open(old,oldPython,['native-transfer-a']);assert.ok(await x.cap.quiescenceParticipant.acquire(ctx));await x.close();x=f.open(current,python,['native-transfer-a']);await assert.rejects(x.cap.quiescenceParticipant.abortAdmission(abort()));assert.equal((await x.cap.inspectQuiescence()).intakeClosed,true);await x.cap.quiescenceParticipant.reconcileRelease({...ctx,outcome,proof:{...normal(),outcome,instanceId:outcome==='unchanged'?ctx.instanceId:'qualified-replacement-instance'}});assert.equal((await x.cap.inspectQuiescence()).intakeClosed,false);assert.equal((await x.peers[0].inspectQuiescence()).intakeClosed,false);await assert.rejects(x.cap.quiescenceParticipant.abortAdmission(abort()));}finally{await f.close();}
});

test('actual late native acquisition reply stays pending after timeout and is never reacquired',{skip:!enabled},async()=>{
 const f=await fixture();try{const x=f.open(current,python,['native-transfer-a'],true);await assert.rejects(x.cap.quiescenceParticipant.acquire(ctx),/timed out/);assert.equal((await x.cap.inspectQuiescence()).intakeClosed,true);await assert.rejects(x.cap.quiescenceParticipant.abortAdmission(abort()),/pending/);await new Promise(resolve=>setTimeout(resolve,400));assert.equal((await x.cap.quiescenceParticipant.abortAdmission(abort())).status,'released');assert.equal((await x.cap.inspectQuiescence()).intakeClosed,false);}finally{await f.close();}
});
