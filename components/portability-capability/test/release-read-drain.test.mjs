import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {OwnerConnection} from '../dist/index.js';
for(const purpose of ['retention-hide','managed-files-disposal','recovery'])test(`${purpose} releases after an in-flight read and before a later refresh`,async t=>{
 const root=await mkdtemp(join(tmpdir(),'portability-release-drain-')),script=join(root,'peer.mjs');
 await writeFile(script,`import {createInterface} from 'node:readline';const out=v=>console.log(JSON.stringify({jsonrpc:'2.0',...v}));const done=(id,result)=>out({id,result});let pending,reads=0,releases=0;createInterface({input:process.stdin}).on('line',line=>{const r=JSON.parse(line);if(r.method==='initialize')done(r.id,{protocolVersion:1,quiescence:{version:1,heldIntake:true,durableRelease:true,managedFiles:{version:1,preservesCanonical:true}}});else if(r.method==='quiescence/acquire')done(r.id,{acquired:true,intakeClosed:true,fenceId:r.params.fenceId});else if(r.method==='snapshot'){reads++;if(reads===1){pending=r.id;out({id:'catalog-read',method:'host/inspectSession',params:{}});}else done(r.id,{reads,releases});}else if(r.id==='catalog-read')done(pending,{reads,releases});else if(r.method==='quiescence/release'){releases++;done(r.id,{released:true,intakeClosed:false});}});`);
 let entered,finish;const began=new Promise(r=>entered=r),gate=new Promise(r=>finish=r);
 const owner=new OwnerConnection({command:process.execPath,args:[script],requestTimeoutMs:2000},async()=>{entered();await gate;return{};},()=>{});
 t.after(async()=>{finish();await owner.close();await rm(root,{recursive:true,force:true});});
 const context={fenceId:'reviewed',commandId:'one-effect',purpose,instanceId:'instance',dataScope:'owned'},proof={verified:true,fenceId:context.fenceId,commandId:context.commandId,instanceId:context.instanceId,dataScope:context.dataScope,outcome:'unchanged',receiptId:'exact-proof'};
 const lease=await owner.quiescenceParticipant.acquire(context);assert.ok(lease);
 const first=owner.request('snapshot',{});await began;let completed=false;const release=lease.release('unchanged',proof).then(()=>{completed=true;});
 const refresh=owner.request('snapshot',{});await assert.rejects(owner.request('action',{operation:'portability.export'}),/held/);assert.equal(completed,false);
 finish();assert.deepEqual(await first,{reads:1,releases:0});await release;assert.deepEqual(await refresh,{reads:2,releases:1});
 await lease.release('unchanged',proof);assert.deepEqual(await owner.request('snapshot',{}),{reads:3,releases:1});
});

test('a timed-out read keeps protection held and sends no release or retry',async t=>{
 const root=await mkdtemp(join(tmpdir(),'portability-release-timeout-')),script=join(root,'peer.mjs'),journal=join(root,'calls.jsonl');
 await writeFile(script,`import {appendFileSync} from 'node:fs';import {createInterface} from 'node:readline';const out=v=>console.log(JSON.stringify({jsonrpc:'2.0',...v}));createInterface({input:process.stdin}).on('line',line=>{const r=JSON.parse(line);appendFileSync(process.argv[2],JSON.stringify(r)+'\\n');if(r.method==='initialize')out({id:r.id,result:{protocolVersion:1,quiescence:{version:1,heldIntake:true,durableRelease:true}}});else if(r.method==='quiescence/acquire')out({id:r.id,result:{acquired:true,intakeClosed:true,fenceId:r.params.fenceId}});else if(r.method==='snapshot')out({id:'read',method:'host/inspectSession',params:{}});});`);
 let entered,finish;const began=new Promise(r=>entered=r),gate=new Promise(r=>finish=r);
 const owner=new OwnerConnection({command:process.execPath,args:[script,journal],requestTimeoutMs:100},async()=>{entered();await gate;return{};},()=>{});
 t.after(async()=>{finish();await owner.close();await rm(root,{recursive:true,force:true});});
 const context={fenceId:'retained',commandId:'once',purpose:'retention-hide',instanceId:'instance',dataScope:'owned'},proof={verified:true,...context,outcome:'unchanged',receiptId:'original'};
 const lease=await owner.quiescenceParticipant.acquire(context);assert.ok(lease);
 const first=assert.rejects(owner.request('snapshot',{}),/timed out/);await began;
 const release=assert.rejects(lease.release('unchanged',proof),/did not settle/);
 await Promise.all([first,release]);await assert.rejects(owner.request('action',{operation:'portability.export'}),/held/);
 finish();await new Promise(resolve=>setImmediate(resolve));
 await assert.rejects(lease.release('unchanged',proof),/closed/);
 const calls=(await readFile(journal,'utf8')).trim().split('\n').map(line=>JSON.parse(line).method);
 assert.equal(calls.filter(x=>x==='snapshot').length,1);assert.equal(calls.filter(x=>x==='quiescence/acquire').length,1);assert.equal(calls.filter(x=>x==='quiescence/release').length,0);
});
