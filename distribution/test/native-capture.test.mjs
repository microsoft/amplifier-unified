import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,realpath,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {createNativeCoherentCaptureAdapter} from '../src/native-capture.js';
const hash=v=>createHash('sha256').update(v).digest('hex');
const context={captureId:'capture-with-nonce-0123456789-abcdef',reviewedScopeDigest:'a'.repeat(64),stopReceiptDigest:'b'.repeat(64),
 expected:{instanceId:'instance',dataScope:'scope'}};
async function fixture(t){
 const directory=await realpath(await mkdtemp(join(tmpdir(),'native-capture-')));t.after(()=>rm(directory,{recursive:true,force:true}));
 const bytes=Buffer.from('immutable native body'),artifactId='c'.repeat(32),sha256=hash(bytes),manifestDigest='d'.repeat(64),events=[];
 const state={active:true,nonce:context.captureId,adminInstanceId:'admin',captureId:'native-hold',artifacts:[],closed:false};
 const inspection={artifactId,sha256,bytes:bytes.length,manifest:{sha256:manifestDigest},
  integrity:{artifactHashVerified:true,manifestHashVerified:true},authority:{declaredRootIds:['native-home'],completeNativeBackup:true,externalWriters:{policy:'operator-attested-stopped'}}};
 const connection={close:async()=>{state.closed=true;events.push('closed');},request:async(method,params)=>{
  const op=params.operation??method,args=params.args??{};events.push(op);
  if(method==='initialize')return {};
  if(method==='_amplifier/admin/lifecycle')return op==='acquire'?{acquired:true,lease:{fenceId:context.captureId,commandId:context.captureId,leaseId:'admin-lease'}}:{released:true};
  if(op==='maintenance.capture.acquire'){assert.equal(args.stopReceiptSha256,context.stopReceiptDigest);return {...state};}
  if(op==='maintenance.capture.inspect')return {...state,instanceId:'instance',dataScope:'scope',stopReceiptSha256:context.stopReceiptDigest};
  if(op==='maintenance.archive.create')return {planId:'plan'};
  if(op==='maintenance.archive.prepare')return {previewHash:'preview'};
  if(op==='maintenance.acquire')return {leaseId:'maintenance'};
  if(op==='maintenance.snapshot'){state.artifacts=[{artifactId,sha256,manifestDigest}];return {artifactId,sha256};}
  if(op==='maintenance.artifact.inspect')return inspection;
  if(op==='maintenance.artifact.read')return {artifactId,sha256,bytes:bytes.length,offset:0,encoding:'base64',data:bytes.toString('base64'),chunkSha256:sha256,nextOffset:null};
  if(op==='maintenance.release')return {released:true};
  if(op==='maintenance.capture.release'){state.active=false;assert.equal(args.artifactSha256,sha256);return {released:true};}
  throw Error('unexpected '+op);
 }};
 const adapter=createNativeCoherentCaptureAdapter({engineId:'native',cwd:directory,stagingDirectory:directory,connect:async()=>connection});
 return {adapter,state,events,bytes,connection};
}
test('native adapter retains one nonce-bound private gate before prepare through artifact read and explicit release',async t=>{
 const f=await fixture(t),lease=await f.adapter.acquire(context),a=await lease.capture({includeCredentials:true,credentialsReviewed:true});
 assert.deepEqual(await readFile(a.path),f.bytes);assert.equal(f.state.active,true);assert.equal(f.state.closed,false);
 assert.ok(f.events.indexOf('maintenance.capture.acquire')<f.events.indexOf('maintenance.archive.prepare'));
 assert.equal((await lease.assertHeld()).artifacts[0].manifestDigest,a.descriptor.manifestDigest);
 assert.equal((await lease.release({productSha256:'e'.repeat(64)})).released,true);assert.equal(f.state.closed,true);
 assert.ok(f.events.indexOf('maintenance.artifact.read')<f.events.indexOf('maintenance.capture.release'));
 assert.ok(!f.events.some(e=>e.startsWith('session/')));
});
test('native replacement identity, stale nonce and dropped gate cannot qualify the capture',async t=>{
 for(const field of ['nonce','adminInstanceId','active']){
  const f=await fixture(t),lease=await f.adapter.acquire(context);f.state[field]=field==='active'?false:'replacement';
  await assert.rejects(lease.capture({includeCredentials:true,credentialsReviewed:true}),/lease_lost/);
  assert.ok(!f.events.includes('maintenance.archive.prepare'));await f.connection.close();
 }
});
