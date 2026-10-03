import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rm,symlink,readFile,realpath} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {once} from 'node:events';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {randomUUID,createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {WebSocket} from 'ws';
import {createDistribution,composeCapabilities} from '../src/index.js';
const ROOT='ahp-root://';
async function fixture(extra={}){
 const directory=await mkdtemp(join(tmpdir(),'unified-distribution-')),workspace=join(directory,'workspace'),web=join(directory,'web');await mkdir(workspace);await mkdir(web);
 await writeFile(join(web,'index.html'),'<!doctype html><html><head><title>Packaged client</title></head><body>Client</body></html>');await writeFile(join(web,'client.js'),'window.fixture=true;');
 const config={account:'owned-test',stateDirectory:join(directory,'state'),webDirectory:web,defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],engines:[{id:'fixture',label:'Independent fixture',command:process.execPath,args:[fileURLToPath(new URL('./fixtures/acp.mjs',import.meta.url))]}],...extra};
 const app=await createDistribution(config);return {app,directory,workspace,web,async close(){await app.close();await rm(directory,{recursive:true,force:true});}};
}
class Peer{
 constructor(ws){this.ws=ws;this.next=1;this.pending=new Map();ws.on('message',raw=>{const value=JSON.parse(raw);const pending=this.pending.get(value.id);if(pending){this.pending.delete(value.id);clearTimeout(pending.timer);value.error?pending.reject(Object.assign(Error(value.error.message),{code:value.error.code})):pending.resolve(value.result);}});}
 static async open(url,metadata){const socket=new WebSocket(url.replace(/^http/,'ws')+'/ahp',{origin:url});await once(socket,'open');const peer=new Peer(socket);peer.clientId=randomUUID();peer.init=await peer.request('initialize',{channel:ROOT,clientId:peer.clientId,protocolVersions:['0.9.0'],initialSubscriptions:[ROOT],_meta:metadata});return peer;}
 request(method,params){const id=this.next++;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{this.pending.delete(id);reject(Error('Request timed out: '+method));},5000);this.pending.set(id,{resolve,reject,timer});this.ws.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});}
 close(){this.ws.terminate();for(const p of this.pending.values()){clearTimeout(p.timer);p.reject(Error('Closed'));}this.pending.clear();}
}
test('public package composition serves account-scoped client and standard AHP resources',async()=>{
 const f=await fixture();let peer;try{
  const html=await fetch(f.app.url);assert.equal(html.status,200);assert.match(await html.text(),/connection.js/);assert.equal(html.headers.get('cache-control'),'no-store');
  const boot=await fetch(f.app.url+'/connection.js');assert.match(await boot.text(),/owned-test/);
  const js=await fetch(f.app.url+'/client.js');const cached=await fetch(f.app.url+'/client.js',{headers:{'If-None-Match':js.headers.get('etag')}});assert.equal(cached.status,304);
  peer=await Peer.open(f.app.url);assert.equal(peer.init.protocolVersion,'0.9.0');assert.ok(peer.init._meta['amplifier.dev/capabilities'].topics.canvas);
  const session='ahp-session:/'+randomUUID();await peer.request('createSession',{channel:session,provider:'fixture',workingDirectories:[pathToFileURL(f.workspace).href]});
  const created=await peer.request('x-amplifier/capabilityAction',{channel:session,topic:'canvas',operation:'canvas.show',version:1,args:{kind:'text',content:'Owned body'},commandId:randomUUID()});
  const body=await peer.request('resourceRead',{channel:ROOT,uri:created.result.artifact.bodyUri,encoding:'utf-8'});assert.equal(JSON.parse(body.data).content,'Owned body');
  assert.equal((await peer.request('listSessions',{channel:ROOT,limit:1})).items.length,1);
 }finally{peer?.close();await f.close();}
});
test('gateway rejects cross-origin browser access and assets outside its package',async()=>{
 const f=await fixture();try{
  const outside=join(f.directory,'secret.txt');await writeFile(outside,'not a public asset');await symlink(outside,join(f.web,'escape.txt'));
  assert.equal((await fetch(f.app.url+'/escape.txt')).status,403);
  assert.equal((await fetch(f.app.url+'/connection.js',{headers:{Origin:'https://untrusted.example'}})).status,403);
  const socket=new WebSocket(f.app.url.replace(/^http/,'ws')+'/ahp',{origin:'https://untrusted.example'});socket.on('error',()=>{});const response=await new Promise(resolve=>socket.once('unexpected-response',(_request,response)=>{resolve(response.statusCode);response.resume();socket.terminate();}));assert.equal(response,403);
 }finally{await f.close();}
});
test('capability owner collisions fail before any authority is ambiguous',()=>{
 const owner={manifest:{version:1,topics:{canvas:{uri:'test://canvas',version:1}},actions:{}},read:async()=>({}),action:async()=>({})};assert.throws(()=>composeCapabilities([owner,owner]),/Duplicate capability topic/);
});
test('composed media advertises selected state and rejects detached clients without starting devices',async()=>{
 const f=await fixture({media:{credentialEnvironment:'AMPLIFIER_TEST_NO_VOICE_CREDENTIAL'}});let peer;try{
  peer=await Peer.open(f.app.url);assert.ok(peer.init._meta['amplifier.dev/capabilities'].topics.voice);
  const session='ahp-session:/'+randomUUID();await peer.request('createSession',{channel:session,provider:'fixture',workingDirectories:[pathToFileURL(f.workspace).href]});
  const act=()=>peer.request('x-amplifier/capabilityAction',{channel:session,topic:'voice',operation:'media.attach',version:1,args:{},commandId:randomUUID()});
  await assert.rejects(act(),/Attach this client/);
  await peer.request('subscribe',{channel:session});await peer.request('dispatchAction',{channel:session,clientSeq:0,action:{type:'session/activeClientSet',activeClient:{clientId:peer.clientId,tools:[]}}});
  const attached=await act();assert.ok(attached.result.lease);assert.equal(attached.result.endpoint,'/media');
  const voice=await peer.request('resourceRead',{channel:ROOT,uri:'amplifier-media://configuration?scope='+encodeURIComponent(session),encoding:'utf-8'});
  const record=JSON.parse(voice.data);assert.equal(record.data.voiceConfiguration.available,false);assert.deepEqual(record.data.voiceTranscripts.items,[]);
  const response=await fetch(f.app.url+'/media/voice/config',{method:'POST',headers:{'X-Amplifier-Media-Lease':attached.result.lease}});assert.equal(response.status,200);
  await peer.request('unsubscribe',{channel:session});assert.equal((await fetch(f.app.url+'/media/voice/config',{method:'POST',headers:{'X-Amplifier-Media-Lease':attached.result.lease}})).status,409);
 }finally{peer?.close();await f.close();}
});
test('artifact JSON escaping fits the public gateway transport envelope',async()=>{
 const f=await fixture();let peer;try{
  peer=await Peer.open(f.app.url);const session='ahp-session:/'+randomUUID();await peer.request('createSession',{channel:session,provider:'fixture',workingDirectories:[pathToFileURL(f.workspace).href]});
  const content='"'.repeat(2.1*1024*1024);
  const created=await peer.request('x-amplifier/capabilityAction',{channel:session,topic:'canvas',operation:'canvas.show',version:1,args:{kind:'text',content},commandId:randomUUID()});
  const result=await peer.request('resourceRead',{channel:ROOT,uri:created.result.artifact.bodyUri,encoding:'utf-8'});assert.equal(JSON.parse(result.data).content,content);
 }finally{peer?.close();await f.close();}
});
test('installed MCP broker composes with host, immutable resources and durable app effects',{skip:!process.env.MCP_BROKER_PYTHON},async()=>{
 const python=process.env.MCP_BROKER_PYTHON,f=await fixture({mcp:{python}});let peer;try{
  peer=await Peer.open(f.app.url);const session='ahp-session:/'+randomUUID();await peer.request('createSession',{channel:session,provider:'fixture',workingDirectories:[pathToFileURL(f.workspace).href]});
  const schemas=await f.app.capabilities.getActionSchemas();assert.equal(schemas['smartTools.appCall'].schema.properties.expectedRevision.type,'integer');
  const action=(operation,args,commandId=randomUUID())=>peer.request('x-amplifier/capabilityAction',{channel:session,topic:'connectors',operation:'smartTools.'+operation,args,commandId,version:1});
  const log=join(f.directory,'effects.jsonl');assert.equal((await action('configure',{id:'counter',name:'Counter',command:python,args:[fileURLToPath(new URL('./fixtures/independent_mcp.py',import.meta.url)),log]})).result.status,'completed');
  const connection=await action('connect',{id:'counter'});assert.equal(connection.result.status,'completed',JSON.stringify(connection.result));
  const called=await action('call',{id:'counter',name:'increment',arguments:{amount:3}});assert.equal(called.result.status,'completed');
  const opened=await action('open',{id:'counter',tool:'increment',operationId:called.result.id});assert.equal(opened.result.status,'completed');assert.equal(opened.artifact.kind,'mcp-app');
  const read=uri=>peer.request('resourceRead',{channel:ROOT,uri,encoding:'utf-8'});
  assert.match((await read(opened.artifact.resourceUri)).data,/Independent saved app/);
  const body=JSON.parse((await read(opened.artifact.bodyUri)).data);assert.equal(body.mcp.toolArguments.amount,3);
  const commandId=randomUUID();await action('appCall',{canvasId:opened.artifact.id,expectedRevision:1,name:'increment',arguments:{amount:7}},commandId);
  await assert.rejects(action('appCall',{canvasId:opened.artifact.id,expectedRevision:1,name:'increment',arguments:{amount:7}},commandId),/already admitted/);
  assert.equal((await readFile(log,'utf8')).trim().split('\n').length,2);
  const launch=JSON.parse(await readFile(join(f.directory,'state/capabilities/mcp-launch.json'),'utf8'));assert.deepEqual(launch.server.public_origins,[f.app.url]);
 }finally{peer?.close();await f.close();}
});
test('installed optional owners compose scoped schedules, Git and reviewed local publication',{skip:!process.env.UNIFIED_OWNERS_PYTHON,timeout:60000},async()=>{
 const python=process.env.UNIFIED_OWNERS_PYTHON,f=await fixture({operations:{python},worktrees:{python},publishing:{python}});let peer;
 try{
  assert.equal(f.app.host.diagnostics().activeAgents,0);
  const schemas=await f.app.capabilities.getActionSchemas();assert.ok(schemas['schedule.create']);assert.ok(schemas['worktree.create']);assert.ok(schemas['publishing.build']);
  assert.equal(f.app.host.diagnostics().activeAgents,0,'Schema discovery does not start native execution');
  const git=(...args)=>execFileSync('git',['-c','core.hooksPath=/dev/null','-C',f.workspace,...args],{encoding:'utf8'});
  git('init','-q');git('config','user.name','Owned fixture');git('config','user.email','fixture@example.invalid');await writeFile(join(f.workspace,'source.txt'),'retained source');git('add','.');git('commit','-qm','fixture');
  peer=await Peer.open(f.app.url);const session='ahp-session:/'+randomUUID();await peer.request('createSession',{channel:session,provider:'fixture',workingDirectories:[pathToFileURL(f.workspace).href]});
  const action=async(topic,operation,args={})=>(await peer.request('x-amplifier/capabilityAction',{channel:session,topic,operation,version:1,args,commandId:randomUUID()})).result;
  const inspect=await action('worktrees','worktree.inspect');const checkout=await action('worktrees','worktree.create',{sourceRevision:inspect.repository.sourceRevision,mode:'clean'});
  assert.equal(checkout.status,'ready');assert.equal(await readFile(join(checkout.path,'source.txt'),'utf8'),'retained source');
  assert.equal((await action('worktrees','worktree.remove',{id:checkout.id,expectedRevision:checkout.revision})).status,'removed');
  const args={prompt:'Future explicitly reviewed fixture',kind:'monitor',destination:'new_task',spec:{kind:'interval',timezone:'UTC',startAt:new Date(Date.now()+3600000).toISOString(),intervalSeconds:3600},notificationPolicy:'changes',missedRunPolicy:'latest'};
  const preview=await action('schedules','schedule.preview',args);
  const scheduled=await action('schedules','schedule.create',{...args,expectedRevision:0,previewHash:preview.previewHash});
  assert.equal(scheduled.schedule.status,'active');await action('schedules','schedule.cancel',{id:scheduled.schedule.id,expectedRevision:scheduled.schedule.revision});
  assert.deepEqual((await action('operations','operations.list')).operations,[]);
  await mkdir(join(f.workspace,'site'));await writeFile(join(f.workspace,'site/index.html'),'<h1>Owned installed publication</h1>');
  const release=await action('publishing','publishing.build',{requestId:'capture',siteId:'site',sourcePath:'site'});
  await action('publishing','publishing.review',{requestId:'review',releaseId:release.id,note:'Owned fixture content checked'});
  await assert.rejects(f.app.host.invokeCapability({channel:session,topic:'publishing',operation:'publishing.deploy',version:1,args:{requestId:'agent-denied',siteId:'site',releaseId:release.id,expectedRevision:0},commandId:randomUUID()},{actorId:'fixture-agent',origin:'agent'}),/approval/);
  const deployed=await action('publishing','publishing.deploy',{requestId:'deploy',siteId:'site',releaseId:release.id,expectedRevision:0});
  assert.equal(await(await fetch(deployed.result.url)).text(),'<h1>Owned installed publication</h1>');
  const page=await action('publishing','publishing.list',{collection:'releases',limit:1});assert.equal(page.items.length,1);assert.equal(page.items[0].files,undefined);
  await action('publishing','publishing.stop',{siteId:'site',expectedRevision:1,requestId:'stop'});
  assert.equal((await f.app.host.inspectSession(session)).executionDirectory,await realpath(f.workspace));
 }finally{peer?.close();await f.close();}
});

test('committed uploads cross AHP as references and materialize only for the selected ACP prompt',async()=>{
 const logdir=await mkdtemp(join(tmpdir(),'attachment-prompts-')),log=join(logdir,'prompts.jsonl');
 const f=await fixture({engines:[{id:'fixture',label:'Independent fixture',command:process.execPath,args:[fileURLToPath(new URL('./fixtures/acp.mjs',import.meta.url))],env:{OWNED_PROMPT_LOG:log}}]});let peer;
 try{peer=await Peer.open(f.app.url);const session='ahp-session:/'+randomUUID();await peer.request('createSession',{channel:session,provider:'fixture',workingDirectories:[pathToFileURL(f.workspace).href]});
 const bytes=Buffer.from('Selected shared bytes'),sha256=createHash('sha256').update(bytes).digest('hex');
 const action=async(operation,args)=>(await peer.request('x-amplifier/capabilityAction',{channel:session,topic:'attachments',version:1,operation,args,commandId:randomUUID()})).result.attachment;
 const row=await action('attachments.create',{requestId:'owned-file',name:'shared.txt',contentType:'text/plain',size:bytes.length,sha256});
 const attachments=[{type:'resource',label:row.name,contentType:row.contentType,uri:row.resourceUri,_meta:{'amplifier.dev/attachment':{id:row.id,size:row.size,sha256}}}];
 await assert.rejects(f.app.host.submitTurn(session,{commandId:'uncommitted',text:'Read it',clientId:peer.clientId,attachments}),/committed/);
 const uri=new URL(row.uploadUri);uri.searchParams.set('offset','0');const params={channel:ROOT,uri:uri.href,encoding:'base64',mode:'append',data:bytes.toString('base64')};
 assert.deepEqual(await peer.request('resourceWrite',params),{});peer.close();peer=await Peer.open(f.app.url);assert.equal((await action('attachments.inspect',{requestId:'owned-file'})).receivedBytes,bytes.length);assert.deepEqual(await peer.request('resourceWrite',params),{});await action('attachments.commit',{id:row.id,requestId:'commit-file'});
 const commandId=randomUUID();await f.app.host.submitTurn(session,{commandId,text:'Read it',clientId:peer.clientId,attachments});assert.equal((await f.app.host.waitForTurn(session,commandId,5000)).status,'completed');
 const prompt=JSON.parse((await readFile(log,'utf8')).trim());assert.equal(prompt.find(block=>block.type==='resource').resource.text,bytes.toString());
 const history=await peer.request('subscribe',{channel:session.replace('ahp-session:','ahp-chat:'),view:{turns:5}});const message=history.snapshot.state.turns.find(turn=>turn.id===commandId)?.message??history.snapshot.state.turns.at(-1).message;assert.equal(message.attachments[0].uri,row.resourceUri);assert.equal(JSON.stringify(message).includes(bytes.toString()),false);
 const body=await peer.request('resourceRead',{channel:ROOT,uri:row.resourceUri,encoding:'base64'});assert.deepEqual(Buffer.from(body.data,'base64'),bytes);assert.equal(body.contentType,'text/plain');
 }finally{peer?.close();await f.close();await rm(logdir,{recursive:true,force:true});}
});
