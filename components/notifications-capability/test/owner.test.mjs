import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm,access} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {AhpClient} from '@microsoft/agent-host-protocol/client';
import {WebSocketTransport} from '@microsoft/agent-host-protocol/ws';
const {createNotificationsCapability,notificationActions}=await import(process.env.NOTIFICATIONS_MODULE?pathToFileURL(resolve(process.env.NOTIFICATIONS_MODULE)).href:new URL('../src/index.js',import.meta.url).href);
const python=process.env.NOTIFICATIONS_PYTHON??fileURLToPath(new URL('../python/.venv/bin/python',import.meta.url));
const fence={fenceId:'fence',commandId:'update',purpose:'distribution-update',instanceId:'prior',dataScope:'owned'};
const proof={verified:true,...fence,outcome:'unchanged',receiptId:'exact-proof'};
const wait=async fn=>{for(let i=0;i<500;i++){const result=await fn();if(result)return result;await new Promise(r=>setTimeout(r,10))}throw Error('Fixture did not reach boundary')};
const exists=async path=>{try{await access(path);return true}catch{return false}};
const action=(cap,operation,args={},commandId='command',context={})=>cap.action({channel:'ahp-root://',topic:'notifications',version:1,operation:'notifications.'+operation,args,commandId},context);
async function fixture(t){
 const root=await mkdtemp(join(tmpdir(),'notification-public-')),config=join(root,'config.json'),script=join(root,'transport.py'),capture=join(root,'capture.jsonl'),release=join(root,'release');
 await writeFile(config,JSON.stringify({stateDirectory:join(root,'owner')}));
 await writeFile(script,`import asyncio,json,sys\nfrom pathlib import Path\nfrom amplifier_unified_notifications.server import Peer\nasync def transport(value,title,message):\n with Path(sys.argv[2]).open('a') as log:log.write(json.dumps({'title':title,'message':message,'server':value['server'],'topic':value['topic'],'token':value['token']})+'\\n')\n while not Path(sys.argv[3]).exists():await asyncio.sleep(.01)\n if Path(sys.argv[3]).read_text()=='unknown':raise ConnectionResetError('private error')\n return 202\nasync def main():\n peer=Peer(json.loads(Path(sys.argv[1]).read_text()));peer.owner.transport=transport;await peer.run()\nasyncio.run(main())\n`);
 const options={owner:{command:python,args:['-I',script,config,capture,release]},inspectSession:async session=>({session,title:'Selected fixture'}),waitForTurn:async()=>({completed:true,text:'PRIVATE RESPONSE',workContinues:false})};
 t.after(async()=>{await writeFile(release,'finish').catch(()=>{});await rm(root,{recursive:true,force:true})});
 return {root,config,capture,release,options};
}

test('disabled completion does not inspect sessions/results; public settings redact secrets, CAS and blanks preserve',async t=>{
 const fixtureData=await fixture(t);let inspected=0,read=0;
 const cap=createNotificationsCapability({...fixtureData.options,inspectSession:async()=>{inspected++;throw Error('unneeded')},waitForTurn:async()=>{read++;throw Error('unneeded')}});
 try{
  assert.deepEqual(Object.keys(await cap.actionSchemas()),Object.keys(notificationActions));
  assert.equal((await cap.turnSettled({session:'ahp-session:/a',commandId:'turn',status:'completed',inputOrigin:'ui'})).disabled,true);
  assert.equal((await cap.notifyAttention({session:'ahp-session:/a',eventId:'schedule:one',text:'unchanged'})).disabled,true);
  assert.deepEqual([inspected,read],[0,0]);assert.equal(await exists(fixtureData.capture),false);
  const saved=await action(cap,'save',{expectedRevision:0,patch:{enabled:true,topic:'PRIVATE_TOPIC',token:'PRIVATE_TOKEN',server:'https://self.example.test/ntfy',desktop:false}},'save');
  assert.equal(saved.accepted,true);assert.equal(saved.result.revision,1);assert.equal(JSON.stringify(saved).includes('PRIVATE_'),false);
  const stale=await action(cap,'save',{expectedRevision:0,patch:{preview:true}},'stale');assert.equal(stale.accepted,false);assert.equal(stale.result.executed,false);
  const blank=await action(cap,'save',{expectedRevision:1,patch:{topic:'',token:''}},'blank');assert.equal(blank.updates[0].data.notificationSettings.tokenConfigured,true);
  const settings=(await cap.read({topic:'notifications',scope:'host',uri:cap.manifest.topics.notifications.uri})).data.notificationSettings;
  assert.equal(settings.revision,2);assert.equal(settings.server,'https://self.example.test/ntfy');assert.equal('desktop' in settings,false);
  await assert.rejects(cap.action({version:1,topic:'notifications',operation:'notifications.get'},{}),/scope/);
  await assert.rejects(action(cap,'test',{},'no-generic-fetch'),/Unadvertised/);
 }finally{await cap.close()}
});

test('accepted completion returns before network; background lease, private preview, exact receipts, restart unknown never replay',async t=>{
 const {options,capture,release}=await fixture(t);let bodyReads=0,idle=0;
 let cap=createNotificationsCapability({...options,onMayBeIdle:()=>idle++,waitForTurn:async()=>{bodyReads++;return {completed:true,text:'PRIVATE RESPONSE',workContinues:false}}});
 const event={session:'ahp-session:/a',commandId:'turn',status:'completed',inputOrigin:'ui'};
 try{
  await action(cap,'save',{expectedRevision:0,patch:{enabled:true,topic:'private',token:'secret'}},'save');
  const result=await cap.turnSettled(event);assert.equal(result.status,'accepted');assert.equal(bodyReads,0);
  await wait(()=>exists(capture));assert.equal((await readFile(capture,'utf8')).split('\n').filter(Boolean).length,1);
  assert.equal(JSON.parse((await readFile(capture,'utf8')).trim()).message,'Your Amplifier response is ready.');
  assert.equal(await cap.quiescenceParticipant.acquire(fence),null);
  assert.equal((await cap.turnSettled(event)).replayed,false);
  await writeFile(release,'unknown');await wait(async()=>((await action(cap,'receipt',{commandId:result.commandId},'read')).result.status==='unknown'));
  const lease=await cap.quiescenceParticipant.acquire(fence);assert.equal(lease.fenceId,fence.fenceId);
  assert.equal((await action(cap,'receipt',{commandId:result.commandId},'read-held')).result.status,'unknown');
  assert.equal((await cap.notifyAttention({session:event.session,eventId:'schedule:held',text:'held'})).executed,false);
  await cap.close();cap=createNotificationsCapability(options);
  assert.equal((await cap.inspectQuiescence()).intakeClosed,true);
  await cap.quiescenceParticipant.reconcileRelease({...fence,outcome:'unchanged',proof});
  assert.equal((await cap.turnSettled(event)).status,'unknown');
  await cap.close();cap=createNotificationsCapability(options);
  await cap.quiescenceParticipant.reconcileRelease({...fence,outcome:'unchanged',proof});
  await assert.rejects(cap.quiescenceParticipant.reconcileRelease({...fence,outcome:'unchanged',proof:{...proof,receiptId:'changed'}}),/exact retained/i);
  assert.equal((await readFile(capture,'utf8')).split('\n').filter(Boolean).length,1);assert.ok(idle>0);
 }finally{await writeFile(release,'finish');await cap.close()}
});

test('schedule attention remains explicit, completion errors cannot fail finished work, preview fetched only on opt-in',async t=>{
 const {options,release,capture}=await fixture(t);await writeFile(release,'finish');let reads=0;
 const cap=createNotificationsCapability({...options,waitForTurn:async()=>{reads++;return {completed:true,text:'💡'.repeat(25000),workContinues:false}}});
 try{
  await action(cap,'save',{expectedRevision:0,patch:{enabled:true,topic:'private',preview:true}},'save');
  const skipped=await cap.turnSettled({session:'ahp-session:/a',commandId:'turn',status:'completed',inputOrigin:'scheduled'});assert.equal(skipped.executed,false);assert.equal(reads,0);
  const result=await cap.turnSettled({session:'ahp-session:/a',commandId:'turn',status:'completed',inputOrigin:'ui'});assert.equal(result.status,'accepted');assert.equal(reads,1);
  const attention=await cap.notifyAttention({session:'ahp-session:/a',eventId:'schedule:one',text:'Explicit policy decision'});assert.equal(attention.kind,'schedule-attention');
  await wait(async()=>((await action(cap,'receipt',{commandId:attention.commandId},'read')).result.status==='server-accepted'));
  const rows=(await readFile(capture,'utf8')).trim().split('\n').map(JSON.parse);assert.deepEqual(rows.map(r=>r.message),['💡'.repeat(1000),'Explicit policy decision']);
  const unconfirmed=await cap.notifyAttention({session:'ahp-session:/a',eventId:'schedule:one',text:'changed must never send'});assert.equal(unconfirmed.status,'unknown');
  assert.equal((await readFile(capture,'utf8')).trim().split('\n').length,2);
 }finally{await cap.close()}
});

test('two independent standard AHP clients share settings/CAS and exact receipts; notifications stay optional and native remains cold',{skip:!process.env.HOST_MODULE},async t=>{
 const {createHost}=await import(pathToFileURL(resolve(process.env.HOST_MODULE)).href);
 const {root,options,release}=await fixture(t);await writeFile(release,'finish');
 const cap=createNotificationsCapability(options);
 let resourceReads=0;const read=cap.read;cap.read=async request=>{resourceReads++;return read(request)};
 const host=await createHost({stateDirectory:join(root,'host'),allowedWorkspaceRoots:[root],engines:[{id:'unused',command:'/must-never-start'}],capabilities:cap,quiescence:{instanceId:'instance',dataScope:'fixture',requiredOwners:['notifications'],coverage:{capabilities:{notifications:'notifications'}},participants:[cap.quiescenceParticipant],verifyRelease:async input=>({verified:true,fenceId:input.fenceId,commandId:input.commandId,outcome:'unchanged',instanceId:'instance',dataScope:'fixture',receiptId:'no-change'})}});
 const one=new AhpClient(await WebSocketTransport.connect(host.url)),two=new AhpClient(await WebSocketTransport.connect(host.url));one.connect();two.connect();
 await one.initialize({clientId:'one',protocolVersions:['0.9.0']});await two.initialize({clientId:'two',protocolVersions:['0.9.0']});
 const invoke=(peer,op,args,id)=>peer.request('x-amplifier/capabilityAction',{channel:'ahp-root://',topic:'notifications',version:1,operation:'notifications.'+op,args,commandId:id});
 try{
  const saved=await invoke(one,'save',{expectedRevision:0,patch:{enabled:true,topic:'PRIVATE_TOPIC',token:'PRIVATE_TOKEN'}},'one-save');assert.equal(saved.accepted,true);
  const base=cap.manifest.topics.notifications.uri;
  for(const suffix of ['', '?scope=', '?scope=host&scope=host', '?scope=ahp-session%3A%2Fforeign']){
   await assert.rejects(two.request('resourceRead',{channel:'ahp-root://',uri:base+suffix,encoding:'utf-8'}),error=>error.code===-32602);
  }
  assert.equal(resourceReads,0,'invalid resource scopes must be refused before owner reads');
  const scoped=new URL(base);scoped.searchParams.set('scope','host');
  const other=await two.request('resourceRead',{channel:'ahp-root://',uri:scoped.href,encoding:'utf-8'});assert.ok(JSON.stringify(other).includes('topicConfigured'));assert.equal(JSON.stringify(other).includes('PRIVATE_'),false);
  assert.equal(resourceReads,1);
  assert.equal((await invoke(two,'save',{expectedRevision:0,patch:{preview:true}},'stale')).accepted,false);
  assert.equal((await two.request('x-commandReceipt',{channel:'ahp-root://',commandId:'stale'})).status,'failed');
  const held=await host.admitQuiescence({commandId:'update',purpose:'distribution-update'});assert.equal(held.admitted,true);
  assert.equal((await invoke(one,'receipt',{commandId:'one-save'},'held-read')).result.status,'completed');
  await assert.rejects(invoke(two,'save',{expectedRevision:1,patch:{enabled:false}},'held-save'),/intake|quiescen|maintenance/i);
  await host.releaseQuiescence({fenceId:held.fenceId,commandId:held.commandId,outcome:'unchanged',evidence:{known:true}});
  assert.equal(host.diagnostics().activeAgents,0);
  console.log(JSON.stringify({receipt:'notifications-two-client',nativeAgents:0,secretsRedacted:true,casRefusal:true,heldReceiptReadable:true,deviceDelivery:'unverified',accountSends:0}));
 }finally{await one.shutdown();await two.shutdown();await cap.close();await host.close()}
});
