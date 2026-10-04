import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile,writeFile,rm,access,realpath} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {once} from 'node:events';
import {createServer} from 'node:net';
import {request} from 'node:https';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {randomUUID} from 'node:crypto';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {WebSocket} from 'ws';
import {createManualIngressGate} from '@amplifier/unified-distribution-update-owner';
import {createDistribution} from '../src/index.js';
import {createPreviewAccess} from '../src/preview-access.mjs';
import {artifactFixture,inertRenderer} from './fixtures/terminal-artifact.mjs';
const exec=promisify(execFile),ROOT='ahp-root://',tick=()=>new Promise(r=>setTimeout(r,10));
const freePort=async()=>{const s=createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const port=s.address().port;await new Promise(r=>s.close(r));return port;};
class Peer{
 constructor(socket){this.socket=socket;this.next=0;this.pending=new Map();socket.on('message',raw=>{const v=JSON.parse(raw),p=this.pending.get(v.id);if(p){this.pending.delete(v.id);clearTimeout(p.timer);v.error?p.reject(Error(v.error.message)):p.resolve(v.result);}});}
 request(method,params){const id=++this.next;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('RPC timeout '+method)),5000);this.pending.set(id,{resolve,reject,timer});this.socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});}
 action(operation,args={},commandId=randomUUID()){return this.request('x-amplifier/capabilityAction',{channel:ROOT,topic:'terminal',operation:'terminal.'+operation,version:1,args,commandId});}
 close(){this.socket.terminate();for(const p of this.pending.values()){clearTimeout(p.timer);p.reject(Error('closed'));}this.pending.clear();}
}
test('actual installed AHP host accepts independent device bearer, preserves browser origin and admitted work',{timeout:30000},async()=>{
 const root=await realpath(await mkdtemp(join(tmpdir(),'terminal-ahp-'))),state=join(root,'state'),workspace=join(root,'workspace'),web=join(root,'web');for(const p of [state,workspace,web])await mkdir(p,{mode:0o700});await writeFile(join(web,'index.html'),'<html><head></head></html>');
 const fixture=await artifactFixture(join(root,'feed')),origin='https://127.0.0.1:24447',authority=new URL(origin).host,backendPort=await freePort(),code='owned-terminal-preview-fixture-code-'.repeat(2);
 const keyPath=join(root,'key.pem'),certPath=join(root,'cert.pem');await exec('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',keyPath,'-out',certPath,'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1']);const key=await readFile(keyPath),cert=await readFile(certPath);
 const gate=await createManualIngressGate({directory:join(state,'ingress'),id:'terminal-fixture-ingress'});let app,proxy,browser,one,two;const peers=[];
 try{
  app=await createDistribution({account:'terminal-fixture',stateDirectory:state,defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],webDirectory:web,gateway:{origin,host:'127.0.0.1',port:backendPort},terminal:{origin,artifacts:[fixture.entry]},quiescence:{instanceId:'terminal-fixture',dataScope:'owned-terminal-fixture'},engines:[{id:'fixture',command:process.execPath,args:[fileURLToPath(new URL('./fixtures/terminal-acp.mjs',import.meta.url))],env:{STARTED:join(root,'started'),RELEASE:join(root,'release'),AUDIT:join(root,'audit')}}]},{renderTerminalInstaller:inertRenderer});
  assert.ok(app.quiescence.requiredOwners.includes('terminal'));const inventory=await app.storageInventory();const entry=inventory.owners.find(v=>v.id==='terminal');assert.deepEqual(entry.rootIds,['application']);assert.equal(entry.externalStorage,'none');assert.match(entry.revision,/^sha256:/);
  proxy=await createPreviewAccess({origin,host:'127.0.0.1',port:0,key,cert,accessCode:code,backendPort,ingressGate:gate,terminalAccess:app.terminalAccess});const port=proxy.server.address().port;
  const http=(path,body,headers={})=>new Promise((resolve,reject)=>{const req=request({host:'127.0.0.1',port,ca:cert,path,method:body===undefined?'GET':'POST',headers:{Host:authority,...(body===undefined?{}:{'Content-Type':'application/json'}),...headers}},res=>{const chunks=[];res.on('data',d=>chunks.push(d));res.on('end',()=>resolve({code:res.statusCode,headers:res.headers,bytes:Buffer.concat(chunks)}));});req.on('error',reject);req.end(body===undefined?undefined:JSON.stringify(body));});
  const login=await http('/preview/login',{code},{Origin:origin});assert.equal(login.code,204);const cookie=login.headers['set-cookie'][0].split(';')[0];
  const connect=async(headers)=>{const ws=new WebSocket('wss://127.0.0.1:'+port+'/ahp',{ca:cert,headers:{Host:authority,...headers}});ws.on('error',()=>{});await once(ws,'open');const p=new Peer(ws);peers.push(p);const init=await p.request('initialize',{channel:ROOT,clientId:randomUUID(),protocolVersions:['0.9.0'],initialSubscriptions:[ROOT]});assert.ok(init._meta['amplifier.dev/capabilities'].topics.terminal);return p;};
  const refused=headers=>new Promise(resolve=>{const ws=new WebSocket('wss://127.0.0.1:'+port+'/ahp',{ca:cert,headers:{Host:authority,...headers}});ws.on('error',()=>{});ws.once('unexpected-response',(_r,res)=>{res.resume();ws.terminate();resolve(res.statusCode);});});
  assert.equal(await refused({Cookie:cookie}),403);assert.equal(await refused({Cookie:cookie,Origin:'https://wrong.example'}),403);assert.equal(await refused({Authorization:'Bearer '+code}),403);
  browser=await connect({Cookie:cookie,Origin:origin});
  async function enroll(name){const commandId=randomUUID(),prepared=await browser.action('prepare',{platform:'linux-arm64',name},commandId),p=prepared.result.receipt.result;assert.equal((await http(new URL(p.download.url).pathname)).code,403);const d=await http(new URL(p.download.url).pathname,undefined,{Cookie:cookie});assert.equal(d.code,200);const grant=JSON.parse(d.bytes.toString().split('\n')[1]);const body={preparationId:p.preparationId,grant:grant.grant,redemptionId:randomUUID(),artifactId:p.artifactId};const response=await http('/setup/terminal/redeem',body);assert.equal(response.code,201);const enrolled=JSON.parse(response.bytes);assert.equal(enrolled.origin,origin);assert.equal((await http('/setup/terminal/redeem',body)).code,409);return {...enrolled,commandId};}
  const a=await enroll('First laptop'),b=await enroll('Second laptop');one=await connect({Authorization:'Bearer '+a.token});two=await connect({Authorization:'Bearer '+b.token});
  assert.equal(await refused({Authorization:'Bearer '+a.token,Origin:'https://wrong.example'}),403);assert.equal((await http('/connection.js',undefined,{Authorization:'Bearer '+a.token})).code,403);
  assert.equal(app.host.diagnostics().activeAgents,0);const session='ahp-session:/'+randomUUID(),chat=session.replace('ahp-session:','ahp-chat:'),turnId=randomUUID();await one.request('createSession',{channel:session,provider:'fixture',workingDirectories:[pathToFileURL(workspace).href]});
  await one.request('dispatchAction',{channel:chat,clientSeq:1,action:{type:'chat/turnStarted',turnId,startedAt:new Date().toISOString(),message:{text:'Complete original input after revocation',origin:{kind:'user'}}}});
  for(let n=0;;n++){try{await access(join(root,'started'));break;}catch{assert.ok(n<300);await tick();}}
  const deviceClosed=once(one.socket,'close');const revoked=await browser.action('revoke',{deviceId:a.deviceId});assert.equal(revoked.result.receipt.status,'completed');await deviceClosed;assert.equal(two.socket.readyState,WebSocket.OPEN);assert.equal(browser.socket.readyState,WebSocket.OPEN);assert.equal(await refused({Authorization:'Bearer '+a.token}),403);
  await writeFile(join(root,'release'),'released');assert.equal((await app.host.waitForTurn(session,turnId,5000)).status,'completed');assert.equal((await readFile(join(root,'audit'),'utf8')).trim(),'prompt');
  assert.equal((await two.action('devices',{limit:1})).result.items.length,1);assert.equal((await browser.action('receipt',{commandId:a.commandId})).result.receipt.status,'completed');
  const hold=await app.host.admitQuiescence({commandId:'while-devices-connected',purpose:'recovery'});assert.equal(hold.admitted,false);
  for(const p of peers)p.close();for(let n=0;n<300&&gate.inspect().active;n++)await tick();assert.equal(gate.inspect().active,0);await proxy.close();proxy=undefined;
 }finally{for(const p of peers)p.close();if(proxy){for(let n=0;n<300&&gate.inspect().active;n++)await tick();await proxy.close();}await app?.close();gate.close();await rm(root,{recursive:true,force:true});}
});
