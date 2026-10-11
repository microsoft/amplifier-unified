import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {once} from 'node:events';
import {randomUUID} from 'node:crypto';
import {WebSocket} from 'ws';
const {createDistribution}=await import(process.env.IMAGE_HOST_DISTRIBUTION??'../src/index.js');

for(const supported of [true,false])test('image runtime default follows actual native peer grant: '+supported,async()=>{
 const root=await realpath(await mkdtemp(join(tmpdir(),'image-host-grant-')));
 const web=join(root,'web'),log=join(root,'init.jsonl'),fixture=join(root,'peer.mjs');
 await mkdir(web);await writeFile(join(web,'index.html'),'fixture');
 const grant={version:1,catalog:'provider-image-models',preservesChat:true,exclusive:true};
 await writeFile(fixture,`import readline from 'node:readline';import {appendFileSync} from 'node:fs';
 for await(const line of readline.createInterface({input:process.stdin})){
  const r=JSON.parse(line);let result;
  if(r.method==='initialize'){appendFileSync(${JSON.stringify(log)},JSON.stringify(r.params)+'\\n');result={protocolVersion:1,agentCapabilities:{loadSession:true,_meta:{'amplifier.dev/native':{version:1,admin:{version:1,...${JSON.stringify(supported?{imageSetup:grant}:{})}}}}}};}
  else if(r.method==='session/new')result={sessionId:'owned-image-session'};
  else if(r.method==='session/cancel')result={};
  else if(r.id===undefined)continue;
  else {process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:r.id,error:{code:-32601,message:'Fixture has no operation'}})+'\\n');continue;}
  process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:r.id,result})+'\\n');
 }`);
 let app,ws;const pending=new Map();let next=0;
 try{
  app=await createDistribution({account:'image-test',stateDirectory:join(root,'state'),webDirectory:web,defaultWorkspace:root,allowedWorkspaceRoots:[root],engines:[{id:'native',command:process.execPath,args:[fixture]}],nativeAdmin:{engine:'native'}});
  ws=new WebSocket(app.url.replace(/^http/,'ws')+'/ahp',{origin:app.url});await once(ws,'open');
  ws.on('message',raw=>{const r=JSON.parse(raw),p=pending.get(r.id);if(p){pending.delete(r.id);r.error?p.reject(Error(r.error.message)):p.resolve(r.result);}});
  const request=(method,params)=>new Promise((resolve,reject)=>{const id=++next;pending.set(id,{resolve,reject});ws.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});
  await request('initialize',{channel:'ahp-root://',clientId:'image-test',protocolVersions:['0.9.0']});
  await request('createSession',{channel:'ahp-session:/'+randomUUID(),provider:'native',workingDirectories:[pathToFileURL(root).href]});
  const inits=(await readFile(log,'utf8')).trim().split('\n').map(JSON.parse);
  const runtime=inits.find(row=>row.clientCapabilities?._meta?.['amplifier.dev/host']);
  assert.ok(runtime,'Actual runtime ACP initialization must be observed');
  assert.equal(runtime.clientCapabilities._meta['amplifier.dev/host'].features.imageGeneration,supported?true:undefined);
 }finally{ws?.terminate();await app?.close();await rm(root,{recursive:true,force:true});}
});
