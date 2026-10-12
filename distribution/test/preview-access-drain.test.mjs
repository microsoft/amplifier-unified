import test from 'node:test';import assert from 'node:assert/strict';
import {createServer} from 'node:http';import {request} from 'node:https';import {once} from 'node:events';
import {mkdtemp,readFile,rm} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';import {execFile} from 'node:child_process';import {promisify} from 'node:util';
import {WebSocket,WebSocketServer} from 'ws';import {createPreviewAccess} from '../src/preview-access.mjs';
test('restart drain closes real WebSocket peers, prevents reconnection and never truncates an active HTTP response',{timeout:20000},async t=>{
 const root=await mkdtemp(join(tmpdir(),'ingress-drain-'));t.after(()=>rm(root,{force:true,recursive:true}));
 await promisify(execFile)('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',join(root,'key'),'-out',join(root,'cert'),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1']);
 const key=await readFile(join(root,'key')),cert=await readFile(join(root,'cert')),code='fixture-only-'.repeat(5);let active=0,httpResponse;
 const gate={enter(){active++;let finished=false;return()=>{if(!finished){active--;finished=true}}},inspect(){return {active}}};
 const backend=createServer((req,res)=>{httpResponse=res}),wss=new WebSocketServer({server:backend});await new Promise(r=>backend.listen(0,'127.0.0.1',r));
 const host='127.0.0.1:24444',origin='https://'+host,access=await createPreviewAccess({origin,host:'127.0.0.1',port:0,key,cert,accessCode:code,backendPort:backend.address().port,ingressGate:gate});let socket;
 const call=(path,headers={},body)=>new Promise((resolve,reject)=>{const req=request({host:'127.0.0.1',port:access.server.address().port,ca:cert,agent:false,path,method:body?'POST':'GET',headers:{Host:host,Origin:origin,...headers}},res=>{let text='';res.on('data',v=>text+=v);res.on('end',()=>resolve({status:res.statusCode,headers:res.headers,text}))});req.on('error',reject);req.end(body)});
 try{
  const login=await call('/preview/login',{'Content-Type':'application/json'},JSON.stringify({code})),cookie=login.headers['set-cookie'][0].split(';')[0];
  socket=new WebSocket('wss://127.0.0.1:'+access.server.address().port+'/ahp',{ca:cert,headers:{Host:host,Origin:origin,Cookie:cookie}});await once(socket,'open');const closed=once(socket,'close');
  const lease=await access.drainForRestart({purpose:'service-stop',fenceId:'first'});assert.ok(lease);await closed;assert.equal(active,0);assert.equal((await call('/anything',{Cookie:cookie})).status,503);lease.release();
  const pending=call('/slow',{Cookie:cookie});for(let i=0;i<100&&!httpResponse;i++)await new Promise(r=>setTimeout(r,10));assert.ok(httpResponse);
  assert.equal(await access.drainForRestart({purpose:'distribution-update',fenceId:'busy'}),null);assert.equal(httpResponse.destroyed,false);httpResponse.end('complete original response');assert.equal((await pending).text,'complete original response');
  for(let i=0;i<100&&active;i++)await new Promise(r=>setTimeout(r,10));assert.equal(active,0);
 }finally{socket?.terminate();httpResponse?.end();for(const peer of wss.clients)peer.terminate();await access.close();wss.close();backend.closeAllConnections();await new Promise(r=>backend.close(r));}
});
