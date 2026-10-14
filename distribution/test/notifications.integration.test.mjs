import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';
import {once} from 'node:events';
import {WebSocket} from 'ws';
import {createDistribution} from '../src/index.js';
const python=process.env.UNIFIED_OWNERS_PYTHON;
const wait=async fn=>{for(let n=0;n<500;n++){const result=await fn();if(result)return result;await new Promise(resolve=>setTimeout(resolve,10));}throw Error('Expected notification boundary not reached');};

test('assembled completed turns and explicit schedule policy reach only the opted-in notification owner',{skip:!python,timeout:30000},async()=>{
 const directory=await mkdtemp(join(tmpdir(),'distribution-notifications-')),workspace=join(directory,'workspace'),web=join(directory,'web'),config=join(directory,'owner.json'),capture=join(directory,'sent.jsonl'),script=join(directory,'transport.py');
 await mkdir(workspace);await mkdir(web);await writeFile(join(web,'index.html'),'<html><head></head><body>Fixture</body></html>');
 await writeFile(config,JSON.stringify({stateDirectory:join(directory,'notifications')}));
 // The real installed owner persists policy and receipts. Only HTTPS is replaced
 // here; its separate owner suite exercises an actual local TLS endpoint.
 await writeFile(script,`import asyncio,json,sys\nfrom pathlib import Path\nfrom amplifier_unified_notifications.server import Peer\nasync def transport(value,title,message):\n with Path(sys.argv[2]).open('a') as out:out.write(json.dumps({'title':title,'message':message})+'\\n')\n return 202\nasync def main():\n peer=Peer(json.loads(Path(sys.argv[1]).read_text()));peer.owner.transport=transport;await peer.run()\nasyncio.run(main())\n`);
 let app,socket,context;const pending=new Map();let next=0;
 const rows=async()=>{try{return (await readFile(capture,'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse);}catch(error){if(error.code==='ENOENT')return [];throw error;}};
 try{
  app=await createDistribution({account:'notification-fixture',stateDirectory:join(directory,'state'),webDirectory:web,defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],engines:[{id:'fixture',command:process.execPath,args:[fileURLToPath(new URL('./fixtures/acp.mjs',import.meta.url))]}],notifications:{owner:{command:python,args:['-I',script,config,capture]}},quiescence:{instanceId:'notifications-instance',dataScope:'notifications-data'}},{createCapabilityOwners:async value=>{context=value;return [];}});
  assert.ok(app.quiescence.requiredOwners.includes('notifications'));assert.equal(app.host.diagnostics().activeAgents,0);
  socket=new WebSocket(app.url.replace(/^http/,'ws')+'/ahp',{origin:app.url});await once(socket,'open');
  socket.on('message',bytes=>{const row=JSON.parse(bytes),entry=pending.get(row.id);if(entry){pending.delete(row.id);clearTimeout(entry.timer);row.error?entry.reject(Error(row.error.message)):entry.resolve(row.result);}});
  const request=(method,params)=>new Promise((resolve,reject)=>{const id=++next,timer=setTimeout(()=>reject(Error('Timed out: '+method)),5000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});
  await request('initialize',{channel:'ahp-root://',clientId:'fixture-client',protocolVersions:['0.9.0']});
  const action=async(operation,args={},commandId=randomUUID())=>(await request('x-amplifier/capabilityAction',{channel:'ahp-root://',topic:'notifications',operation:'notifications.'+operation,version:1,args,commandId})).result;
  assert.equal((await action('get')).enabled,false);const session='ahp-session:/'+randomUUID();await request('createSession',{channel:session,provider:'fixture',workingDirectories:[pathToFileURL(workspace).href]});
  const turn=async commandId=>{await app.host.submitTurn(session,{commandId,text:'Complete fixture',clientId:'fixture-client'});assert.equal((await app.host.waitForTurn(session,commandId,5000)).status,'completed');};
  await turn('disabled-turn');await context.notifySchedule(session,{id:'disabled-schedule',notificationDecision:{notify:true},phase:'completed'});assert.deepEqual(await rows(),[]);
  const saved=await action('save',{expectedRevision:0,patch:{enabled:true,topic:'PRIVATE_TOPIC',token:'PRIVATE_TOKEN',preview:false}},'enable');assert.equal(JSON.stringify(saved).includes('PRIVATE_'),false);
  await turn('enabled-turn');await wait(async()=>(await rows()).length===1);assert.equal((await rows())[0].message,'Your Amplifier response is ready.');
  for(const decision of [undefined,{}, {notify:false},{notify:'true'}])await context.notifySchedule(session,{id:'suppressed',notificationDecision:decision,phase:'completed',detail:'Never send'});
  assert.equal((await rows()).length,1);
  const explicit={id:'chosen',notificationDecision:{notify:true},phase:'completed',detail:'Reviewed change'};
  const attention=await context.notifySchedule(session,explicit);await wait(async()=>(await action('receipt',{commandId:attention.commandId})).status==='server-accepted');
  assert.equal((await rows())[1].message,'Your Amplifier response is ready.');await context.notifySchedule(session,explicit);assert.equal((await rows()).length,2);
  await action('save',{expectedRevision:1,patch:{preview:true}},'enable-preview');
  const preview=await context.notifySchedule(session,{...explicit,id:'chosen-preview'});await wait(async()=>(await action('receipt',{commandId:preview.commandId})).status==='server-accepted');
  assert.equal((await rows())[2].message,'completed: Reviewed change');
  assert.equal((await action('receipt',{commandId:attention.commandId})).status,'server-accepted');
  assert.equal(app.quiescence.requiredOwners.length,app.quiescence.participants.length);
 }finally{socket?.terminate();for(const entry of pending.values())clearTimeout(entry.timer);await app?.close();await rm(directory,{recursive:true,force:true});}
});
