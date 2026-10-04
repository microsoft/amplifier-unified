import test,{mock} from 'node:test';
import assert from 'node:assert/strict';
import {request as httpRequest,createServer as httpServer} from 'node:http';
import {request as httpsRequest} from 'node:https';
import {connect as tlsConnect} from 'node:tls';
import {readFile,mkdtemp,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {once} from 'node:events';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createHash,randomUUID} from 'node:crypto';
import {createTerminalOwner} from '../src/terminal-owner.js';
import {artifactFixture,inertRenderer} from './fixtures/terminal-artifact.mjs';
const exec=promisify(execFile),deferred=()=>{let resolve;return {promise:new Promise(r=>resolve=r),resolve};};
const tick=()=>new Promise(r=>setImmediate(r));
test('device revocation during late upgrade retains its actual owner lease until backend closure',{timeout:15000},async()=>{
 const root=await mkdtemp(join(tmpdir(),'u-ingress-race-'));
 const keyPath=join(root,'key.pem'),certPath=join(root,'cert.pem');
 await exec('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',keyPath,'-out',certPath,'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1']);
 const key=await readFile(keyPath),cert=await readFile(certPath),code='fixture-only-access-code-'.repeat(3);
 const artifact=await artifactFixture(join(root,'feed')),owner=createTerminalOwner({directory:join(root,'authority'),account:'fixture',origin:'https://127.0.0.1:24444',artifacts:[artifact.entry],renderInstaller:inertRenderer});
 const prepared=await owner.action({version:1,topic:'terminal',channel:'ahp-root://',operation:'terminal.prepare',args:{platform:'linux-arm64',name:'Race fixture'},commandId:'prepare'},{account:'fixture'});
 const p=prepared.result.receipt.result,privateProfile=JSON.parse((await readFile(join(root,'authority/downloads',p.preparationId+'.sh'),'utf8')).split('\n')[1]);
 const fence={fenceId:'race',commandId:'hold',purpose:'recovery',instanceId:'owned',dataScope:'fixture'};
 const backend=httpServer();let serverBack,client,access,proxyBack,releaseBack,releaseUpgrade,releaseRequestClose;
 const reached=deferred(),frontClosed=deferred();let frontObserved=false,backObserved=false,releases=0,active=0;
 const gate={enter(){active++;let done=false;return ()=>{if(done)return;done=true;active--;releases++;};},inspect(){return {active};}};
 const handshake='HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n';
 backend.on('upgrade',(req,socket)=>{serverBack=socket;socket.on('error',()=>{});socket.write(handshake);});
 await new Promise(r=>backend.listen(0,'127.0.0.1',r));
 mock.module('node:http',{namedExports:{request(...args){
  const request=httpRequest(...args),emit=request.emit.bind(request),destroy=request.destroy.bind(request);
  let delayed=false;
  request.emit=(name,...values)=>{
   if(name==='upgrade'){
    delayed=true;proxyBack=values[1];const actualDestroy=proxyBack.destroy.bind(proxyBack);
    proxyBack.once('close',()=>{backObserved=true;});
    proxyBack.destroy=(...destroyArgs)=>{releaseBack=()=>actualDestroy(...destroyArgs);return proxyBack;};
    releaseUpgrade=()=>emit(name,...values);reached.resolve();return true;
   }
   if(name==='close'&&delayed){releaseRequestClose=()=>emit(name,...values);return true;}
   return emit(name,...values);
  };
  request.destroy=(...args)=>{if(delayed){frontObserved=true;frontClosed.resolve();return request;}return destroy(...args);};
  return request;
 }}});
 const module=await import(new URL(process.env.PREVIEW_ACCESS_TARGET??'../src/preview-access.mjs',import.meta.url));
 try{
  access=await module.createPreviewAccess({origin:'https://127.0.0.1:24444',host:'127.0.0.1',port:0,key,cert,accessCode:code,backendPort:backend.address().port,ingressGate:gate,terminalAccess:owner.terminalAccess});
  const port=access.server.address().port,host='127.0.0.1:24444',origin='https://'+host;
  const registered=await new Promise((resolve,reject)=>{
   const req=httpsRequest({host:'127.0.0.1',port,method:'POST',path:'/setup/terminal/redeem',ca:cert,headers:{Host:host,'Content-Type':'application/json'}},res=>{
    assert.equal(res.statusCode,201);const chunks=[];res.on('data',chunk=>chunks.push(chunk));res.on('end',()=>resolve(JSON.parse(Buffer.concat(chunks))));
   });req.on('error',reject);req.end(JSON.stringify({preparationId:p.preparationId,grant:privateProfile.grant,redemptionId:randomUUID(),artifactId:p.artifactId}));
  });
  for(let n=0;n<20&&active;n++)await tick();assert.equal(active,0);
  releases=0;
  client=tlsConnect({host:'127.0.0.1',port,ca:cert});client.on('error',()=>{});
  await once(client,'secureConnect');
  client.write('GET /ahp HTTP/1.1\r\nHost: '+host+'\r\nAuthorization: Bearer '+registered.token+'\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n');
  await reached.promise;
  assert.equal(active,1);assert.equal(proxyBack.destroyed,false);
  const closed=once(client,'close');await owner.action({version:1,topic:'terminal',channel:'ahp-root://',operation:'terminal.revoke',args:{deviceId:registered.deviceId},commandId:'revoke'},{account:'fixture'});await closed;await frontClosed.promise;
  assert.equal(frontObserved,true);assert.equal(active,1);
  releaseUpgrade();
  for(let n=0;n<20&&!releaseRequestClose;n++)await tick();
  assert.equal(typeof releaseRequestClose,'function','real request close is held until upgrade ownership is registered');
  releaseRequestClose();
  assert.equal(typeof releaseBack,'function');assert.equal(backObserved,false);
  assert.equal(await owner.quiescenceParticipant.acquire(fence),null);
  assert.equal(active,1,'gate stays busy while the real upgraded backend socket has not closed');
  assert.throws(()=>access.close(),/manual_ingress_active/);
  const backendClosed=once(proxyBack,'close');releaseBack();await backendClosed;
  assert.equal(backObserved,true);assert.equal(active,0);assert.equal(releases,1);const lease=await owner.quiescenceParticipant.acquire(fence);assert.ok(lease);await lease.release('unchanged',{kind:'admission-refused'});
  await writeFile(join(root,'receipt.json'),JSON.stringify({schema:'terminal-device-ingress-race-v1',actualTerminalOwner:true,revocationDuringPendingUpgrade:true,actualTLSFrontend:true,actualBackendSocket:true,delayedUpgrade:true,closedFrontend:true,requestCloseObserved:true,gateHeldUntilBackendClosed:true,gateReleasedOnce:true,sourceSha256:createHash('sha256').update(await readFile(new URL(process.env.PREVIEW_ACCESS_TARGET??'../src/preview-access.mjs',import.meta.url))).digest('hex')},null,2));
  console.log('race_receipt='+join(root,'receipt.json'));
 }finally{
  client?.destroy();releaseBack?.();serverBack?.destroy();
  await tick();if(access&&active===0)await access.close();
  backend.closeAllConnections();await new Promise(r=>backend.close(r));
  await owner.close();mock.restoreAll();
 }
});
