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
import {createHash} from 'node:crypto';
const exec=promisify(execFile),deferred=()=>{let resolve;return {promise:new Promise(r=>resolve=r),resolve};};
const tick=()=>new Promise(r=>setImmediate(r));
test('late upgrade after closed frontend retains actual backend socket until close',{timeout:15000},async()=>{
 const root=await mkdtemp(join(tmpdir(),'u-ingress-race-'));
 const keyPath=join(root,'key.pem'),certPath=join(root,'cert.pem');
 await exec('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',keyPath,'-out',certPath,'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1']);
 const key=await readFile(keyPath),cert=await readFile(certPath),code='fixture-only-access-code-'.repeat(3);
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
  access=await module.createPreviewAccess({origin:'https://127.0.0.1:24444',host:'127.0.0.1',port:0,key,cert,accessCode:code,backendPort:backend.address().port,ingressGate:gate});
  const port=access.server.address().port,host='127.0.0.1:24444',origin='https://'+host;
  const cookie=await new Promise((resolve,reject)=>{
   const req=httpsRequest({host:'127.0.0.1',port,method:'POST',path:'/preview/login',ca:cert,headers:{Host:host,Origin:origin,'Content-Type':'application/json'}},res=>{
    assert.equal(res.statusCode,204);res.resume();res.on('end',()=>resolve(res.headers['set-cookie'][0].split(';')[0]));
   });req.on('error',reject);req.end(JSON.stringify({code}));
  });
  for(let n=0;n<20&&active;n++)await tick();assert.equal(active,0);
  releases=0;
  client=tlsConnect({host:'127.0.0.1',port,ca:cert});client.on('error',()=>{});
  await once(client,'secureConnect');
  client.write('GET /ahp HTTP/1.1\r\nHost: '+host+'\r\nOrigin: '+origin+'\r\nCookie: '+cookie+'\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n');
  await reached.promise;
  assert.equal(active,1);assert.equal(proxyBack.destroyed,false);
  const closed=once(client,'close');client.destroy();await closed;await frontClosed.promise;
  assert.equal(frontObserved,true);assert.equal(active,1);
  releaseUpgrade();
  for(let n=0;n<20&&!releaseRequestClose;n++)await tick();
  assert.equal(typeof releaseRequestClose,'function','real request close is held until upgrade ownership is registered');
  releaseRequestClose();
  assert.equal(typeof releaseBack,'function');assert.equal(backObserved,false);
  assert.equal(active,1,'gate stays busy while the real upgraded backend socket has not closed');
  assert.throws(()=>access.close(),/manual_ingress_active/);
  const backendClosed=once(proxyBack,'close');releaseBack();await backendClosed;
  assert.equal(backObserved,true);assert.equal(active,0);assert.equal(releases,1);
  await writeFile(join(root,'receipt.json'),JSON.stringify({schema:'preview-ingress-race-v1',actualTLSFrontend:true,actualBackendSocket:true,delayedUpgrade:true,closedFrontend:true,requestCloseObserved:true,gateHeldUntilBackendClosed:true,gateReleasedOnce:true,sourceSha256:createHash('sha256').update(await readFile(new URL(process.env.PREVIEW_ACCESS_TARGET??'../src/preview-access.mjs',import.meta.url))).digest('hex')},null,2));
  console.log('race_receipt='+join(root,'receipt.json'));
 }finally{
  client?.destroy();releaseBack?.();serverBack?.destroy();
  await tick();if(access&&active===0)await access.close();
  backend.closeAllConnections();await new Promise(r=>backend.close(r));
  mock.restoreAll();
 }
});
