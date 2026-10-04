import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,stat,rm,cp,symlink,truncate} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {createServer,request} from 'node:http';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {EventEmitter} from 'node:events';
import {createTerminalOwner} from '../src/terminal-owner.js';
import {loadTerminalArtifact,readTerminalFile} from '../src/terminal-artifacts.js';
import {artifactFixture,inertRenderer,hash} from './fixtures/terminal-artifact.mjs';

const origin='https://terminal.example',account='fixture-account';
const context={fenceId:'fixture-fence',commandId:'fixture-backup',purpose:'recovery',instanceId:'fixture-process',dataScope:'fixture-scope'};
async function fixture(extra={}){
 const root=await mkdtemp(join(tmpdir(),'terminal-owner-')),artifact=await artifactFixture(join(root,'feed')),directory=join(root,'authority');let time=1000;
 const config={directory,account,origin,artifacts:[artifact.entry],renderInstaller:inertRenderer,now:()=>time,...extra};let owner=createTerminalOwner(config);
 const server=createServer((req,res)=>{Promise.resolve(req.url==='/setup/terminal/redeem'?owner.terminalAccess.handleRedemption(req,res):owner.httpHandlers[0].handle(req,res,{account,origin})).catch(()=>{res.writeHead(503);res.end();});});await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const http=(path,body,headers={})=>new Promise((resolve,reject)=>{const req=request({host:'127.0.0.1',port:server.address().port,path,method:body===undefined?'GET':'POST',headers:{Host:new URL(origin).host,...(body===undefined?{}:{'Content-Type':'application/json'}),...headers}},res=>{const data=[];res.on('data',v=>data.push(v));res.on('end',()=>resolve({code:res.statusCode,headers:res.headers,bytes:Buffer.concat(data)}));});req.on('error',reject);req.end(body===undefined?undefined:JSON.stringify(body));});
 const action=(operation,args={},commandId=randomUUID(),ctx={account})=>owner.action({version:1,topic:'terminal',channel:'ahp-root://',operation:'terminal.'+operation,args,commandId},ctx);
 return {root,artifact,config,get owner(){return owner;},action,http,advance:n=>time+=n,async reopen(){await owner.close();owner=createTerminalOwner(config);},async close(){await owner.close();await new Promise(r=>server.close(r));await rm(root,{recursive:true,force:true});}};
}
async function prepare(f,id=randomUUID()){const result=await f.action('prepare',{platform:'linux-arm64',name:'Owned laptop'},id),receipt=result.result.receipt;assert.equal(receipt.status,'completed');const download=await f.http(new URL(receipt.result.download.url).pathname);assert.equal(download.code,200);const privateData=JSON.parse(download.bytes.toString().split('\n')[1]);return {receipt,privateData,body:{preparationId:privateData.preparationId,grant:privateData.grant,redemptionId:randomUUID(),artifactId:privateData.artifactId}};}
test('configured release validation binds exact source, evidence, runtime target and bytes',async()=>{
 const root=await mkdtemp(join(tmpdir(),'terminal-feed-'));try{const f=await artifactFixture(root);assert.equal(loadTerminalArtifact(f.entry).selected.sha256,hash(f.wheelBytes));
  for(const change of [e=>e.runtimes.node.target.arch='x86_64',e=>e.runtimes.node.version='20.0.0',e=>e.runtimes.python.url='http://unsafe.invalid/runtime',e=>e.artifact.manifestSha256='e'.repeat(64),e=>e.runtimes.python.executable='../python']){const entry=structuredClone(f.entry);change(entry);assert.throws(()=>loadTerminalArtifact(entry));}
  const original=await readFile(f.entry.artifact.manifestPath);const duplicate=Buffer.from(original.toString().replace('"schemaVersion":1','"schemaVersion":1,"schemaVersion":1'));await writeFile(f.entry.artifact.manifestPath,duplicate);assert.throws(()=>loadTerminalArtifact({...f.entry,artifact:{...f.entry.artifact,manifestSha256:hash(duplicate)}}),/Duplicate/);
  await writeFile(f.entry.artifact.manifestPath,original);await writeFile(f.entry.artifact.evidencePath,'{}');assert.throws(()=>loadTerminalArtifact(f.entry),/evidence digest/);
 }finally{await rm(root,{recursive:true,force:true});}
});
test('one-use concurrent redemption is original-bound and private receipts never contain credentials',async()=>{
 const f=await fixture();try{
  const id='original-preparation',p=await prepare(f,id);assert.equal((await stat(join(f.config.directory,'downloads',p.privateData.preparationId+'.sh'))).mode&0o777,0o600);
  assert.deepEqual((await f.action('prepare',{platform:'linux-arm64',name:'Owned laptop'},id)).result.receipt,p.receipt);
  await assert.rejects(f.action('prepare',{platform:'linux-arm64',name:'Changed'},id),/different arguments/);
  const replies=await Promise.all([f.http('/setup/terminal/redeem',p.body),f.http('/setup/terminal/redeem',p.body)]);assert.deepEqual(replies.map(r=>r.code).sort(),[201,409]);const registered=JSON.parse(replies.find(r=>r.code===201).bytes),consumed=JSON.parse(replies.find(r=>r.code===409).bytes);assert.equal(consumed.deviceId,registered.deviceId);assert.equal(consumed.credentialAvailable,false);assert.equal(consumed.token,undefined);
  const different=JSON.parse((await f.http('/setup/terminal/redeem',{...p.body,redemptionId:randomUUID()})).bytes);assert.equal(different.status,'consumed');assert.equal(different.deviceId,undefined);
  const listed=await f.action('devices');assert.equal(listed.result.items.length,1);const exact=(await f.action('receipt',{commandId:id})).result.receipt;assert.deepEqual(exact,p.receipt);
  for(const data of [JSON.stringify(exact),JSON.stringify(listed),(await readFile(join(f.config.directory,'terminal.sqlite3'))).toString('binary')]){assert.equal(data.includes(p.privateData.grant),false);assert.equal(data.includes(registered.token),false);}
  assert.equal((await f.http(new URL(p.receipt.result.download.url).pathname)).code,410);
  await f.reopen();assert.equal((await f.action('devices')).result.items[0].deviceId,registered.deviceId);assert.equal((await f.http('/setup/terminal/redeem',p.body)).code,409);assert.deepEqual((await f.action('receipt',{commandId:id})).result.receipt,p.receipt);
 }finally{await f.close();}
});
test('expiry, account/origin mismatch, unavailable platform and changed feed never grant authority',async()=>{
 const f=await fixture();try{await assert.rejects(f.action('devices',{},randomUUID(),{account:'other'}),/account/);
  const unsupported=await f.action('prepare',{platform:'windows-arm64',name:'Unsupported'});assert.equal(unsupported.accepted,false);assert.equal(unsupported.result.receipt.executed,false);
  const p=await prepare(f);assert.equal((await f.http('/setup/terminal/redeem',p.body,{Origin:'https://other.example'})).code,403);assert.equal((await f.http('/setup/terminal/redeem',{...p.body,artifactId:'other'})).code,403);
  f.advance(1800);assert.equal((await f.http('/setup/terminal/redeem',p.body)).code,410);const prior=await f.action('prepare',{platform:'linux-arm64',name:'Owned laptop'},p.receipt.commandId);assert.equal(prior.result.receipt.result.preparationId,p.privateData.preparationId);
  await writeFile(f.artifact.entry.artifact.manifestPath,'{}');await f.reopen();const snapshot=await f.owner.read({topic:'terminal',scope:'host',uri:'amplifier-capability://terminal?scope=host'},{account});assert.equal(snapshot.data.terminal.available,false);assert.equal((await f.action('receipt',{commandId:p.receipt.commandId})).result.receipt.status,'completed');assert.equal((await f.action('prepare',{platform:'linux-arm64',name:'New'})).result.receipt.error.code,'terminal-artifact-unavailable');
 }finally{await f.close();}
});
test('unknown renderer outcome is durable, never replayed, and safe original receipt stays readable',async()=>{
 let calls=0;const f=await fixture({renderInstaller:()=>{calls++;throw Error('private rendering failure');}});try{
  const id='uncertain-original';await assert.rejects(f.action('prepare',{platform:'linux-arm64',name:'Laptop'},id),/unknown/);assert.equal(calls,1);
  await f.reopen();await assert.rejects(f.action('prepare',{platform:'linux-arm64',name:'Laptop'},id),/unknown/);assert.equal(calls,1);assert.equal((await f.action('receipt',{commandId:id})).result.receipt.status,'unknown');
 }finally{await f.close();}
});
test('device lifetimes close only their own sockets; held/restarted fences keep receipts readable',async()=>{
 const f=await fixture();try{
  const first=await prepare(f),second=await prepare(f),a=JSON.parse((await f.http('/setup/terminal/redeem',first.body)).bytes),b=JSON.parse((await f.http('/setup/terminal/redeem',second.body)).bytes);let closedA=0,closedB=0,leaseA,leaseB;
  leaseA=f.owner.terminalAccess.attachDevice('Bearer '+a.token,()=>{closedA++;leaseA.release();});leaseB=f.owner.terminalAccess.attachDevice('Bearer '+b.token,()=>{closedB++;leaseB.release();});
  assert.equal(await f.owner.quiescenceParticipant.acquire(context),null);await f.action('revoke',{deviceId:a.deviceId});assert.equal(closedA,1);assert.equal(closedB,0);assert.throws(()=>f.owner.terminalAccess.attachDevice('Bearer '+a.token,()=>{}),/unavailable/);leaseB.release();
  const held=await f.owner.quiescenceParticipant.acquire(context);assert.ok(held);assert.throws(()=>f.owner.terminalAccess.attachDevice('Bearer '+b.token,()=>{}),/intake is closed/);await assert.rejects(f.action('revoke',{deviceId:b.deviceId}),/intake is closed/);assert.equal((await f.action('devices')).result.items.length,2);
  await held.release('unknown');await f.reopen();await assert.rejects(f.action('prepare',{platform:'linux-arm64',name:'No'},randomUUID()),/intake is closed/);assert.equal((await f.action('receipt',{commandId:first.receipt.commandId})).result.receipt.status,'completed');
  await assert.rejects(f.owner.quiescenceParticipant.reconcileRelease({...context,outcome:'unchanged',proof:{kind:'admission-refused'}}));const proof={verified:true,...context,outcome:'unchanged',receiptId:'authentic-fixture-release'};await f.owner.quiescenceParticipant.reconcileRelease({...context,outcome:'unchanged',proof});await f.owner.quiescenceParticipant.reconcileRelease({...context,outcome:'unchanged',proof});await assert.rejects(f.owner.quiescenceParticipant.reconcileRelease({...context,outcome:'unchanged',proof:{...proof,receiptId:'changed'}}));
  assert.throws(()=>createTerminalOwner(f.config),/locked/);
 }finally{await f.close();}
});
test('device pages have bounded stable ordering and no token or last-seen mutation',async()=>{
 const f=await fixture();try{for(let n=0;n<4;n++){const p=await prepare(f);assert.equal((await f.http('/setup/terminal/redeem',p.body)).code,201);}const one=(await f.action('devices',{limit:2})).result,two=(await f.action('devices',{limit:2,cursor:one.nextCursor})).result;assert.equal(one.items.length,2);assert.equal(two.items.length,2);assert.equal(two.nextCursor,null);assert.equal(new Set([...one.items,...two.items].map(v=>v.deviceId)).size,4);await assert.rejects(f.action('devices',{limit:51}));
 }finally{await f.close();}
});
test('process death preserves unknown preparation and releases only the actual OS ownership lease',async()=>{
 const root=await mkdtemp(join(tmpdir(),'terminal-crash-'));let owner;try{
  const artifact=await artifactFixture(join(root,'feed')),config={directory:join(root,'authority'),account,origin,artifacts:[artifact.entry]};
  const source=`import {createTerminalOwner} from ${JSON.stringify(new URL('../src/terminal-owner.js',import.meta.url).href)}; const owner=createTerminalOwner({...${JSON.stringify(config)},renderInstaller:()=>process.exit(73)});await owner.action({version:1,topic:'terminal',channel:'ahp-root://',operation:'terminal.prepare',args:{platform:'linux-arm64',name:'Crash'},commandId:'original-crash'},{account:${JSON.stringify(account)}});`;
  await assert.rejects(promisify(execFile)(process.execPath,['--input-type=module','-e',source]),error=>error.code===73);
  let renders=0;owner=createTerminalOwner({...config,renderInstaller:()=>{renders++;throw Error('Unknown preparation must not render');}});
  const call=(operation,args,commandId='read')=>owner.action({version:1,topic:'terminal',channel:'ahp-root://',operation,args,commandId},{account});
  assert.equal((await call('terminal.receipt',{commandId:'original-crash'})).result.receipt.status,'unknown');await assert.rejects(call('terminal.prepare',{platform:'linux-arm64',name:'Crash'},'original-crash'),/unknown/);assert.equal(renders,0);assert.equal((await call('terminal.devices',{})).result.items.length,0);
 }finally{await owner?.close();await rm(root,{recursive:true,force:true});}
});
test('inactive copied authority retains grant/device receipts and its held fence without activation',async()=>{
 const f=await fixture();let restored;try{
  const p=await prepare(f),device=JSON.parse((await f.http('/setup/terminal/redeem',p.body)).bytes),held=await f.owner.quiescenceParticipant.acquire(context);assert.ok(held);await f.owner.close();
  const target=join(f.root,'inactive-restore');await cp(f.config.directory,target,{recursive:true});restored=createTerminalOwner({...f.config,directory:target});
  const request={version:1,topic:'terminal',channel:'ahp-root://',operation:'terminal.receipt',args:{commandId:p.receipt.commandId},commandId:'passive'};
  assert.deepEqual((await restored.action(request,{account})).result.receipt,p.receipt);assert.throws(()=>restored.terminalAccess.attachDevice('Bearer '+device.token,()=>{}),/intake is closed/);await assert.rejects(restored.quiescenceParticipant.reconcileRelease({...context,outcome:'unchanged',proof:{kind:'admission-refused'}}));
 }finally{await restored?.close();await f.close();}
});
test('closed response does not retire a still running owner request callback',async()=>{
 const f=await fixture();try{let resume;const wait=new Promise(resolve=>resume=resolve),response=new EventEmitter();response.setHeader=()=>{};response.writeHead=()=>{};response.end=()=>response.emit('close');
  const req={url:'/setup/terminal/redeem',method:'POST',headers:{host:new URL(origin).host,'content-type':'application/json'},async *[Symbol.asyncIterator](){await wait;yield Buffer.from('{}');}};
  const running=f.owner.terminalAccess.handleRedemption(req,response);response.emit('close');assert.equal(await f.owner.quiescenceParticipant.acquire(context),null);resume();await running;const lease=await f.owner.quiescenceParticipant.acquire(context);assert.ok(lease);await lease.release('unchanged',{kind:'admission-refused'});
 }finally{await f.close();}
});
test('artifact descriptors reject linked, oversized and FIFO inputs without unbounded reads',async()=>{
 const root=await mkdtemp(join(tmpdir(),'terminal-files-'));try{const regular=join(root,'regular'),link=join(root,'link'),fifo=join(root,'fifo');await writeFile(regular,'ok');await symlink(regular,link);assert.throws(()=>readTerminalFile(link,1024));await truncate(regular,129*1024);assert.throws(()=>readTerminalFile(regular,128*1024),/bounded/);
  await promisify(execFile)('mkfifo',[fifo]);const source=`import {readTerminalFile} from ${JSON.stringify(new URL('../src/terminal-artifacts.js',import.meta.url).href)};try{readTerminalFile(${JSON.stringify(fifo)},1024);process.exit(2);}catch(error){if(!String(error).includes('regular'))throw error;}`;await promisify(execFile)(process.execPath,['--input-type=module','-e',source],{timeout:3000});
 }finally{await rm(root,{recursive:true,force:true});}
});
