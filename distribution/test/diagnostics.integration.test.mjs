import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';
import {once} from 'node:events';
import {WebSocket} from 'ws';
import {createDistribution,composeCapabilities} from '../src/index.js';
const python=process.env.UNIFIED_OWNERS_PYTHON;

test('assembled diagnostics persist opt-in policy and capture supplied metadata without history or idle agents',{skip:!python,timeout:30000},async()=>{
 const directory=await mkdtemp(join(tmpdir(),'distribution-diagnostics-')),workspace=join(directory,'workspace'),web=join(directory,'web');
 await mkdir(workspace);await mkdir(web);await writeFile(join(web,'index.html'),'<html><head></head><body>Fixture</body></html>');
 const config={account:'diagnostics-fixture',stateDirectory:join(directory,'state'),webDirectory:web,defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],engines:[{id:'fixture',command:process.execPath,args:[fileURLToPath(new URL('./fixtures/acp.mjs',import.meta.url))]}],diagnostics:{python},quiescence:{instanceId:'instance',dataScope:'owned'}};
 let app,socket;const pending=new Map();let next=0;
 try{
  app=await createDistribution(config);assert.equal(app.host.diagnostics().activeAgents,0);assert.ok(app.quiescence.requiredOwners.includes('diagnostics'));
  socket=new WebSocket(app.url.replace(/^http/,'ws')+'/ahp',{origin:app.url});await once(socket,'open');
  socket.on('message',bytes=>{const row=JSON.parse(bytes),entry=pending.get(row.id);if(entry){pending.delete(row.id);clearTimeout(entry.timer);row.error?entry.reject(Error(row.error.message)):entry.resolve(row.result);}});
  const request=(method,params)=>new Promise((resolve,reject)=>{const id=++next,timer=setTimeout(()=>reject(Error('Timed out: '+method)),5000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});
  const init=await request('initialize',{channel:'ahp-root://',clientId:'fixture-client',protocolVersions:['0.9.0']});assert.ok(init._meta['amplifier.dev/capabilities'].topics.diagnostics);
  const action=async(operation,args={},commandId=randomUUID())=>(await request('x-amplifier/capabilityAction',{channel:'ahp-root://',topic:'diagnostics',operation:'diagnostics.'+operation,version:1,args,commandId})).result;
  const initial=await action('get');assert.equal(initial.config.enabled,false);assert.equal(initial.local.records,0);assert.equal(app.host.diagnostics().activeAgents,0);
  const enabled={...initial.config,enabled:true,streams:['app','canvas']};assert.equal((await action('configure',{config:enabled,expectedRevision:0},'enable')).status,'completed');
  const session='ahp-session:/'+randomUUID();await request('createSession',{channel:session,provider:'fixture',workingDirectories:[pathToFileURL(workspace).href]});
  await request('x-amplifier/capabilityAction',{channel:session,topic:'canvas',operation:'canvas.show',version:1,args:{kind:'text',content:'PRIVATE_CANVAS_BODY'},commandId:'canvas-change'});
  let page;for(let n=0;n<300;n++){page=await action('records',{stream:'canvas',limit:1});if(page.items.length)break;await new Promise(r=>setTimeout(r,10));}
  assert.equal(page.items.length,1);assert.equal(page.items[0].session,session);assert.equal(page.items[0].workspace,await import('node:fs/promises').then(fs=>fs.realpath(workspace)));assert.equal(page.items[0].data.action,'canvas.show');assert.ok(!JSON.stringify(page).includes('PRIVATE_CANVAS_BODY'));
  assert.equal((await action('get')).local.records,1,'Reading diagnostics must not create diagnostic events');
  assert.equal((await action('receipt',{commandId:'enable'})).status,'completed');
  const launch=JSON.parse(await readFile(join(directory,'state/capabilities/diagnostics-launch.json'),'utf8'));assert.deepEqual(Object.keys(launch),['stateDirectory']);
  socket.terminate();socket=undefined;await app.close();app=await createDistribution(config);
  assert.equal(app.host.diagnostics().activeAgents,0,'Reading retained diagnostics does not rehydrate native agents');
  const recovered=await app.capabilities.action({channel:'ahp-root://',topic:'diagnostics',operation:'diagnostics.get',version:1,args:{},commandId:randomUUID()},{origin:'ui'});
  assert.equal(recovered.result.config.enabled,true);assert.equal(recovered.result.local.records,1);
 }finally{socket?.terminate();for(const entry of pending.values())clearTimeout(entry.timer);await app?.close();await rm(directory,{recursive:true,force:true});}
});

test('optional action observations cannot alter completed or rejected business actions',async()=>{
 const owner={manifest:{version:1,topics:{fixture:{}},actions:{'fixture.run':{topic:'fixture',operation:'fixture.run'}}},action:async request=>{if(request.args.fail)throw Error('business failure');return {accepted:true}}};
 const capability=composeCapabilities([owner],{onAction:()=>{throw Error('observation failure')}}),request={topic:'fixture',operation:'fixture.run',args:{}};
 assert.deepEqual(await capability.action(request,{}),{accepted:true});
 await assert.rejects(capability.action({...request,args:{fail:true}},{}),/business failure/);
});
