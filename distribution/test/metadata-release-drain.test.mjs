import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createNotificationsCapability} from '../../components/notifications-capability/src/index.js';
import {createDiagnosticsCapability} from '../../components/diagnostics-capability/src/index.js';

for(const [topic,create,mutation] of [
 ['notifications',createNotificationsCapability,'notifications.save'],
 ['diagnostics',createDiagnosticsCapability,'diagnostics.configure'],
]) test(`${topic}: refreshes drain around the exact release; mutations stay refused`,async()=>{
 const root=await mkdtemp(join(tmpdir(),'metadata-release-')),script=join(root,'owner.mjs'),log=join(root,'requests.jsonl');
 await writeFile(script,`import {createInterface} from 'node:readline';
import {appendFileSync} from 'node:fs';
let reads=0;
const record=value=>appendFileSync(${JSON.stringify(log)},JSON.stringify(value)+'\\n');
const reply=(id,result)=>process.stdout.write(JSON.stringify({jsonrpc:'2.0',id,result})+'\\n');
createInterface({input:process.stdin}).on('line',line=>{
 const {id,method,params}=JSON.parse(line);record({method,params});
 if(method==='initialize')return reply(id,{protocolVersion:1,quiescence:{heldIntake:true,durableRelease:true}});
 if(method==='quiescence/acquire')return reply(id,{acquired:true,intakeClosed:true,fenceId:params.fenceId});
 if(method==='quiescence/release'){
  if(reads)throw Error('release overtook an admitted read');
  return setTimeout(()=>{record({released:true});reply(id,{released:true,intakeClosed:false});},40);
 }
 if(method==='snapshot'){reads++;return setTimeout(()=>{reads--;reply(id,{revision:1,enabled:false,config:{enabled:false,streams:[]},local:{}});},40);}
 throw Error('unexpected mutation reached owner: '+method);
});`);
 const cap=create({owner:{command:process.execPath,args:[script],requestTimeoutMs:2000}});
 const read=()=>cap.read({topic,scope:'host',uri:cap.manifest.topics[topic].uri});
 try{
  await read();await writeFile(log,'');
  const fence={fenceId:'held-refresh',commandId:'original-command',purpose:'recovery',instanceId:'owned',dataScope:'fixture'};
  const proof={verified:true,...fence,outcome:'unchanged',receiptId:'exact-original'};
  const lease=await cap.quiescenceParticipant.acquire(fence);assert.ok(lease);
  const before=read();await new Promise(resolve=>setImmediate(resolve));
  const releasing=lease.release('unchanged',proof);
  const after=read();
  const rejected=await cap.action({channel:'ahp-root://',topic,version:1,operation:mutation,args:{},commandId:'blocked'},{});
  assert.equal(rejected.accepted,false);
  await Promise.all([before,releasing,after]);
  const rows=(await readFile(log,'utf8')).trim().split('\n').map(JSON.parse);
  assert.deepEqual(rows.map(row=>row.method??'released'),['quiescence/acquire','snapshot','quiescence/release','released','snapshot',...(topic==='notifications'?['snapshot']:[])]);
  assert.deepEqual(rows.find(row=>row.method==='quiescence/release').params.proof,proof);
  await cap.quiescenceParticipant.reconcileRelease({...fence,outcome:'unchanged',proof});
  await assert.rejects(cap.quiescenceParticipant.reconcileRelease({...fence,outcome:'unchanged',proof:{...proof,receiptId:'different'}}),/exact retained/i);
  assert.equal((await readFile(log,'utf8')).match(/quiescence\/release/g).length,1,'settled release is not repeated');
 }finally{await cap.close();await rm(root,{recursive:true,force:true});}
});

for(const [topic,create,mutation] of [
 ['notifications',createNotificationsCapability,'notifications.save'],
 ['diagnostics',createDiagnosticsCapability,'diagnostics.configure'],
]) test(`${topic}: an unconfirmed release keeps mutations held and admits only reads`,async()=>{
 const root=await mkdtemp(join(tmpdir(),'metadata-release-failure-')),script=join(root,'owner.mjs'),log=join(root,'requests.jsonl');
 await writeFile(script,`import {createInterface} from 'node:readline';
import {appendFileSync} from 'node:fs';
const reply=(id,result)=>process.stdout.write(JSON.stringify({jsonrpc:'2.0',id,result})+'\\n');
createInterface({input:process.stdin}).on('line',line=>{
 const {id,method,params}=JSON.parse(line);appendFileSync(${JSON.stringify(log)},JSON.stringify({method,params})+'\\n');
 if(method==='initialize')return reply(id,{protocolVersion:1,quiescence:{heldIntake:true,durableRelease:true}});
 if(method==='quiescence/acquire')return reply(id,{acquired:true,intakeClosed:true,fenceId:params.fenceId});
 if(method==='quiescence/release')return setTimeout(()=>reply(id,{released:false,intakeClosed:true}),30);
 if(method==='snapshot')return reply(id,{revision:1,enabled:false,config:{enabled:false,streams:[]},local:{}});
 throw Error('unexpected mutation reached owner: '+method);
});`);
 const cap=create({owner:{command:process.execPath,args:[script],requestTimeoutMs:2000}});
 const read=()=>cap.read({topic,scope:'host',uri:cap.manifest.topics[topic].uri});
 try{
  await read();await writeFile(log,'');
  const fence={fenceId:'unconfirmed',commandId:'original-command',purpose:'recovery',instanceId:'owned',dataScope:'fixture'};
  const lease=await cap.quiescenceParticipant.acquire(fence);assert.ok(lease);
  const release=assert.rejects(lease.release('unchanged',{receiptId:'original'}),/release unconfirmed/);
  const queuedRead=read();await Promise.all([release,queuedRead]);
  const rejected=await cap.action({channel:'ahp-root://',topic,version:1,operation:mutation,args:{},commandId:'still-blocked'},{});
  assert.equal(rejected.accepted,false);
  await assert.rejects(cap.quiescenceParticipant.acquire({...fence,fenceId:'different'}),/explicit reconciliation/);
  const rows=(await readFile(log,'utf8')).trim().split('\n').map(JSON.parse);
  assert.deepEqual(rows.map(row=>row.method),['quiescence/acquire','quiescence/release','snapshot',...(topic==='notifications'?['snapshot']:[])]);
 }finally{await cap.close();await rm(root,{recursive:true,force:true});}
});
