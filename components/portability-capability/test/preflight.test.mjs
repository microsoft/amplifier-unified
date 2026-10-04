import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm,realpath} from 'node:fs/promises';
import {join} from 'node:path';import {tmpdir} from 'node:os';
const {createPortabilityCapabilities,TransferConnection}=await import(process.env.PORTABILITY_MODULE??'../dist/index.js');
const python=process.env.PORTABILITY_PYTHON;
const context={fenceId:'preflight-fence',commandId:'preflight-command',purpose:'recovery',instanceId:'owned-fixture',dataScope:'owned-fixture'};
const denied=async()=>{throw Error('No transfer, history or native execution authorized');};
const refusal=()=>Object.assign(Error('Known passive no-effect refusal'),{code:'native_transfer_unavailable',executed:false,intakeClosed:false});
async function fixture(mode='healthy',participants){
 const root=await realpath(await mkdtemp(join(tmpdir(),'portability-preflight-')));await mkdir(join(root,'work'));const config=join(root,'owner.json'),audit=join(root,'native-audit');
 await writeFile(config,JSON.stringify({dataDir:join(root,'owner'),stageDir:join(root,'work/stages'),exchangeDir:join(root,'exchange'),workspaceRoots:[join(root,'work')]}));
 const script=String.raw`const fs=require('node:fs'),rl=require('node:readline');const mode=process.argv[1],audit=process.argv[2];rl.createInterface({input:process.stdin}).on('line',line=>{const m=JSON.parse(line);fs.appendFileSync(audit,JSON.stringify({method:m.method,operation:m.params?.operation})+'\n');const send=result=>process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:m.id,result})+'\n');if(m.method==='initialize'){if(mode==='initialize-timeout')return;send({protocolVersion:1,agentCapabilities:{_meta:{'amplifier.dev/native':{version:1,...(mode==='missing'?{}:{transfer:{version:1,lifecycle:{version:1,heldIntake:true,nativeAdminWriter:true,serviceStop:{version:1}}}})}}}});}else if(m.method==='_amplifier/transfer/lifecycle'){if(m.params.operation==='acquire'){if(mode==='acquire-timeout')return;send({acquired:true,processId:'owned-passive-process',intakeClosed:true,fence:m.params.args});}else if(m.params.operation==='release')send({released:true,intakeClosed:false});else send({supported:true,intakeClosed:false});}});`;
 const peer=new TransferConnection({command:process.execPath,args:['-e',script,mode,audit],cwd:root,initializeTimeoutMs:mode==='initialize-timeout'?100:2000,requestTimeoutMs:mode==='acquire-timeout'?100:2000});
 const cap=createPortabilityCapabilities({owner:{command:python,args:['-m','amplifier_unified_portability.server','--config',config]},nativeParticipants:participants??[peer.quiescenceParticipant],inspectSession:denied,beginTransfer:denied,commitTransfer:denied,cancelTransfer:denied,adoptTransferredSession:denied,nativeTransfer:denied,exportTransferEvidence:denied,stageTransferEvidence:denied,activateTransferEvidence:denied});
 return {root,peer,cap,rows:async()=>JSON.parse('['+(await readFile(audit,'utf8')).trim().split('\n').join(',')+']'),close:async()=>{await cap.close();await peer.close();await rm(root,{recursive:true,force:true});}};
}
test('missing native capability refuses before durable Python hold or any native fence RPC',{skip:!python},async()=>{
 const f=await fixture('missing');try{assert.equal(await f.cap.quiescenceParticipant.acquire(context),null);await assert.rejects(readFile(join(f.root,'owner/intake.sqlite')));assert.equal((await f.cap.inspectQuiescence()).intakeClosed,false);assert.deepEqual((await f.rows()).map(x=>x.method),['initialize']);}finally{await f.close();}
});
test('native initialize timeout remains unknown but never takes Python hold',{skip:!python},async()=>{
 const f=await fixture('initialize-timeout');try{await assert.rejects(f.cap.quiescenceParticipant.acquire(context),e=>/timed out/.test(e.message)&&e.executed===undefined);await assert.rejects(readFile(join(f.root,'owner/intake.sqlite')));assert.equal((await f.cap.inspectQuiescence()).intakeClosed,false);assert.deepEqual((await f.rows()).map(x=>x.method),['initialize']);}finally{await f.close();}
});
test('post-dispatch native acquisition timeout retains durable owner hold and never unwinds',{skip:!python},async()=>{
 const f=await fixture('acquire-timeout');try{await assert.rejects(f.cap.quiescenceParticipant.acquire(context),e=>/timed out/.test(e.message)&&e.executed===undefined);assert.equal((await f.cap.inspectQuiescence()).intakeClosed,true);assert.deepEqual((await f.rows()).map(x=>x.operation??x.method),['initialize','acquire']);}finally{await f.close();}
});
test('direct missing-capability refusal is typed and does not reserve native intake',{skip:!python},async()=>{
 const f=await fixture('missing');try{await assert.rejects(f.peer.quiescenceParticipant.acquire(context),e=>e.code==='native_transfer_unavailable'&&e.executed===false&&e.intakeClosed===false);await assert.rejects(f.peer.perform({}),e=>e.code==='native_transfer_unavailable');assert.deepEqual((await f.rows()).map(x=>x.method),['initialize']);}finally{await f.close();}
});
for(const mode of ['known','unknown','unwind-failure'])test(`aggregate ${mode} refusal preserves exact reverse-unwind boundary`,{skip:!python},async()=>{
 let f;let releases=0,pythonHeldDuringNativeRelease=false;
 const first={id:'first',preflight:async()=>true,acquire:async()=>({ownerId:'first',fenceId:context.fenceId,release:async(outcome,proof)=>{releases++;assert.equal(outcome,'unchanged');assert.deepEqual(proof,{kind:'admission-refused'});pythonHeldDuringNativeRelease=(await f.cap.inspectQuiescence()).intakeClosed;if(mode==='unwind-failure')throw Error('Lost rollback acknowledgement');}}),reconcileRelease:denied};
 const second={id:'second',preflight:async()=>true,acquire:async()=>{throw mode==='unknown'?Error('Post-dispatch outcome unknown'):refusal();},reconcileRelease:denied};
 // Configured trusted participants expose only the narrow producer refusal type.
 f=await fixture('healthy',[first,second]);
 try{if(mode==='known'){assert.equal(await f.cap.quiescenceParticipant.acquire(context),null);assert.equal(releases,1);assert.equal(pythonHeldDuringNativeRelease,true);assert.equal((await f.cap.inspectQuiescence()).intakeClosed,false);}else{await assert.rejects(f.cap.quiescenceParticipant.acquire(context));assert.equal(releases,mode==='unknown'?0:1);assert.equal((await f.cap.inspectQuiescence()).intakeClosed,true);}}finally{await f.close();}
});
test('parallel passive initialize cannot acquire native fence twice',{skip:!python},async()=>{
 const f=await fixture();try{const leases=await Promise.all([f.peer.quiescenceParticipant.acquire(context),f.peer.quiescenceParticipant.acquire(context)]);assert.equal(leases.filter(Boolean).length,1);assert.equal((await f.rows()).filter(x=>x.operation==='acquire').length,1);await leases.find(Boolean).release('unchanged',{kind:'admission-refused'});}finally{await f.close();}
});
