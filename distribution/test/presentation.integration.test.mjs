import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm,realpath} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';
import {createRequire} from 'node:module';
import {createDistribution} from '../src/index.js';
import {startConfiguredDistribution} from '../src/launch.js';
import {StdioCatalog} from '@amplifier/unified-host';
import {presentationDiscoveryCatalog} from '../src/presentation.js';
const python=process.env.PRESENTATION_CATALOG_PYTHON,workspacePython=process.env.UNIFIED_OWNERS_PYTHON,nativePython=process.env.RECOVERY_NATIVE_PYTHON,require=createRequire(import.meta.url);
const hostPackage=dirname(dirname(require.resolve('@amplifier/unified-host')));
const actor={actorId:'fixture',clientId:'fixture',origin:'ui'};
const latch=()=>{let release;const promise=new Promise(resolve=>release=resolve);return {promise,release};};
async function scan(catalog){
 const previous=await catalog.status();await catalog.request('scan',{});
 for(let i=0;i<3000;i++){const result=await catalog.status();if(result.runId!==previous.runId&&result.phase==='complete'){assert.equal(result.issues,0);return;}if(result.phase==='failed')throw Error(JSON.stringify(result));await delay(10);}
 throw Error('Catalog scan timed out');
}
async function fixture(count,{engine=false,workspaceOwner=false,fullNative=false,launcher=false}={}){
 const directory=await realpath(await mkdtemp(join(tmpdir(),'distribution-presentation-'))),workspace=join(directory,'workspace'),web=join(directory,'web'),home=join(directory,'native'),index=join(directory,'catalog.sqlite'),log=join(directory,'prompts.jsonl'),history=join(directory,'history.json');
 for(const path of [workspace,web,home])await mkdir(path);
 await writeFile(join(web,'index.html'),'<html><body>Presentation fixture</body></html>');
 await writeFile(history,JSON.stringify([{id:'canonical-turn',state:'complete',updates:[{sessionUpdate:'agent_message_chunk',content:{type:'text',text:'Retained answer'},_meta:{'amplifier.dev/history':{messageId:'canonical-answer'}}}]}]));
 const guard=join(directory,'catalog-no-history.py');
 await writeFile(guard,`from pathlib import Path
original=Path.open
def guarded(self,*args,**kwargs):
    if self.name in ('transcript.jsonl','events.jsonl'):
        raise AssertionError('Presentation must not read canonical bodies')
    return original(self,*args,**kwargs)
Path.open=guarded
from amplifier_session_catalog.__main__ import main
main()
`);
 const records=[],files=new Map();
 for(let n=0;n<count;n++){
  const nativeSessionId='native-'+n,storagePath=join(home,'projects',workspace.replaceAll('/','-'),'sessions',nativeSessionId);
  await mkdir(storagePath,{recursive:true});
  for(const [name,body] of Object.entries({'metadata.json':JSON.stringify({session_id:nativeSessionId,working_dir:workspace,parent_id:null,title:'Retained '+n}),'transcript.jsonl':'{"role":"user","content":"retained input"}\n','events.jsonl':'opaque canonical evidence\n'})){const path=join(storagePath,name);await writeFile(path,body);files.set(path,body);}
  records.push({uri:'ahp-session:/stable-'+n,engineId:'amplifier',nativeSessionId,workingDirectory:workspace,storagePath,title:'Retained '+n,kind:'root',createdAt:'2026-01-01T00:00:00Z',modifiedAt:'2026-01-01T00:00:00Z',workspaceAvailability:'present'});
 }
 const catalogProcess={command:python,args:['-I',guard,'serve','--db',index,'--home',home,'--scan-interval','0','--workspace-check-interval','0']};
 const seed=new StdioCatalog(catalogProcess);try{for(const record of records)await seed.upsert(record);await scan(seed);}finally{await seed.close();}
 let unrelated=0,app;
 const config={account:'presentation-account',stateDirectory:join(directory,'state'),defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],webDirectory:web,engines:[{id:'amplifier',command:engine?process.execPath:'/must-not-start',...(engine?{args:[join(hostPackage,'fixtures/acp-peer.mjs')],env:{FIXTURE_LOG:log,FIXTURE_HISTORY:history}}:{})}],catalogProcess,conversationPresentation:{}};
 if(launcher)config.conversationPresentation.authorization='local-account';
 if(workspaceOwner)config.workspaces={python:workspacePython};
 if(fullNative){
  const nativeConfig=join(directory,'native.json');await writeFile(nativeConfig,JSON.stringify({home,appHome:join(directory,'native-app'),adminWorkspaceRoots:[workspace],adminMaintenance:true,maintenanceExternalWriters:'foundation-cooperative'}));
  config.engines=[{id:'amplifier',command:nativePython,args:['-I','-m','amplifier_acp','--config',nativeConfig]}];
  config.nativeAdmin={engine:'amplifier'};config.recovery={};config.quiescence={instanceId:'presentation-full-native',dataScope:'presentation-fixture-private'};
 }
 const options={authorizeRecovery:async caller=>{assert.equal(caller.account,config.account);return {accountId:config.account};},capabilityOwners:[{manifest:{version:1,topics:{unrelated:{uri:'fixture://unrelated',version:1,scope:'host'}},actions:{'unrelated.run':{topic:'unrelated',operation:'unrelated.run',method:'x-amplifier/capabilityAction'}}},action:async()=>({accepted:true,result:{count:++unrelated}})}]};
 const start=async()=>app=await (launcher?startConfiguredDistribution(config):createDistribution(config,options));await start();
 const call=async(operation,args={},commandId=randomUUID())=>(await app.host.invokeCapability({version:1,topic:'recovery',channel:'ahp-root://',operation,args,commandId},actor)).result;
 const settle=async id=>{for(let n=0;n<3000;n++){const job=await call('recovery.job',{jobId:id});if(['prepared','succeeded','refused','unknown'].includes(job.state))return job;await delay(10);}throw Error('Presentation job timed out');};
 const prepare=async(ids,operation='reset',resetJobId)=>settle((await call('recovery.presentation.prepare',{operation,sessions:ids,reviewed:true,...(resetJobId?{resetJobId}:{})})).id);
 const apply=async(review,commandId)=>settle((await call('recovery.presentation.apply',{preparedJobId:review.id,previewHash:review.previewHash},commandId)).id);
 return {directory,workspace,records,index,config,options,log,get app(){return app;},get catalog(){return app.host.config.catalog;},call,settle,prepare,apply,
  unrelated:async()=>(await app.host.invokeCapability({version:1,topic:'unrelated',channel:'ahp-root://',operation:'unrelated.run',args:{},commandId:randomUUID()},actor)).result,
  restart:async()=>{await app.close();await start();},
  stop:async()=>app.close(),start,
  verify:async()=>{for(const [path,body] of files)assert.equal(await readFile(path,'utf8'),body);},
  close:async()=>{await app?.close();await rm(directory,{recursive:true,force:true});}};
}

async function crashFactory(f,operation,args,commandId,stage){
 const input=join(f.directory,'crash.json');await writeFile(input,JSON.stringify({config:f.config,operation,args,commandId,stage}));
 const source=`
  import {readFile} from 'node:fs/promises';
  import {createDistribution} from ${JSON.stringify(new URL('../src/index.js',import.meta.url).href)};
  const {config,operation,args,commandId,stage}=JSON.parse(await readFile(process.argv[1],'utf8'));
  const app=await createDistribution(config,{authorizeRecovery:async()=>({accountId:config.account})});
  const catalog=app.host.config.catalog,project=catalog.projectSessionVisibility.bind(catalog);let n=0;
  catalog.projectSessionVisibility=async input=>{const selected=++n===stage.at;if(selected&&!stage.after)process.kill(process.pid,'SIGKILL');const result=await project(input);if(selected)process.kill(process.pid,'SIGKILL');return result;};
  await app.host.invokeCapability({version:1,topic:'recovery',channel:'ahp-root://',operation,args,commandId},{actorId:'fixture',clientId:'fixture',origin:'ui'});
  await new Promise(()=>{});
 `;
 const outcome=await new Promise((resolve,reject)=>{const child=spawn(process.execPath,['--input-type=module','-e',source,input],{stdio:['ignore','ignore','pipe']});let error='';child.stderr.on('data',bytes=>error+=bytes);child.on('error',reject);child.on('exit',(code,signal)=>resolve({code,signal,error}));});
 assert.equal(outcome.signal,'SIGKILL',outcome.error);
}

test('JSON presentation-only launcher binds explicit local account and performs visibility without native maintenance',{skip:!python,timeout:60000},async()=>{
 const f=await fixture(1,{launcher:true});try{
  assert.equal(f.app.quiescence,undefined);assert.equal(f.config.nativeAdmin,undefined);assert.equal(f.config.recovery,undefined);
  const review=await f.prepare([f.records[0].uri]),effect=await f.apply(review);
  assert.equal(effect.state,'succeeded');assert.equal(effect.result.receipt.effect.status,'completed');
  assert.equal(f.app.host.diagnostics().activeAgents,0);await f.verify();
 }finally{await f.close();}
});

test('actual distribution BYO ACP presentation has no native admin/global gate; disjoint undo preserves exact identities',{skip:!python,timeout:60000},async()=>{
 const f=await fixture(3);try{
  assert.equal(f.app.quiescence,undefined);
  const actions=Object.keys(f.app.capabilities.manifest.actions).filter(name=>name.startsWith('recovery.'));
  assert.ok(actions.includes('recovery.presentation.apply'));assert.ok(!actions.includes('recovery.snapshot'));assert.ok(!actions.includes('recovery.appReset.apply'));
  const a=await f.apply(await f.prepare([f.records[0].uri]),'reset-a'),b=await f.apply(await f.prepare([f.records[1].uri]),'reset-b');
  assert.equal(a.state,'succeeded');assert.equal(b.state,'succeeded');
  const original=structuredClone(a.result.receipt.effect);
  const undo=await f.apply(await f.prepare([f.records[0].uri],'restore',a.id),'undo-a');assert.equal(undo.state,'succeeded');
  assert.equal((await f.catalog.get(f.records[0].uri)).productHidden,false);assert.equal((await f.catalog.get(f.records[1].uri)).productHidden,true);
  const current=await f.call('recovery.reconcile',{jobId:a.id});assert.deepEqual(current.result.receipt.effect,original);
  assert.equal((await f.app.host.inspectSession(f.records[1].uri)).nativeSessionId,f.records[1].nativeSessionId);
  assert.equal((await f.app.host.listRecallSources({session:f.records[1].uri,scope:'task'})).items[0].id,f.records[1].uri);
  assert.equal((await f.unrelated()).count,1);assert.equal(f.app.host.diagnostics().activeAgents,0);await f.verify();
 }finally{await f.close();}
});

test('selected admission is atomic while unrelated work, hidden Recall, artifacts and deferred commands remain authorized',{skip:!python||!workspacePython,timeout:60000},async()=>{
 const f=await fixture(4,{engine:true,workspaceOwner:true}),entered=latch(),gate=latch();try{
  const session=f.records[0].uri,other=f.records[1].uri,review=await f.prepare([session,f.records[3].uri]),catalog=f.catalog;
  const get=catalog.get.bind(catalog),project=catalog.projectSessionVisibility.bind(catalog);let once=true;
  catalog.get=async id=>{if(id===session&&once){once=false;entered.release();await gate.promise;}return get(id);};
  catalog.projectSessionVisibility=async()=>{throw Error('Controlled failure before actual projection');};
  const hiding=f.apply(review,'hidden');await entered.promise;
  let admitted=false;const selected=f.app.host.submitTurn(session,{commandId:'selected',text:'exact-ID input',clientId:'fixture'}).then(value=>{admitted=true;return value;});
  assert.equal((await f.unrelated()).count,1);
  await f.app.host.submitTurn(other,{commandId:'other',text:'unrelated input',clientId:'fixture'});assert.equal((await f.app.host.waitForTurn(other,'other')).completed,true);
  assert.equal(admitted,false);gate.release();
  const result=await hiding;assert.equal(result.result.receipt.effect.status,'completed');assert.equal(result.result.receipt.projection.status,'unknown');
  assert.equal((await selected).accepted,true);assert.equal((await f.app.host.waitForTurn(session,'selected')).completed,true);
  const discovery=presentationDiscoveryCatalog(catalog,()=>f.app.host);
  await assert.rejects(discovery.list({connectionId:'workspace-session-list',limit:50,allowedWorkspaceRoots:[f.workspace]}),/unavailable/);
  await assert.rejects(discovery.listWorkspaces({connectionId:'workspace-list',limit:50,allowedWorkspaceRoots:[f.workspace]}),/unavailable/);
  const workspaceAction=async operation=>(await f.app.host.invokeCapability({version:1,topic:'workspaces',channel:'ahp-root://',operation,args:{},commandId:randomUUID()},actor)).result;
  await assert.rejects(workspaceAction('workspace.sessions'),/unavailable/i);
  await assert.rejects(workspaceAction('workspace.list'),/unavailable/i);
  const legacy=await catalog.request('resolveNative',{engineId:'amplifier',identities:[f.records[3].nativeSessionId],allowedWorkspaceRoots:[f.workspace]});assert.equal(legacy[f.records[3].nativeSessionId].uri,f.records[3].uri);
  assert.equal((await f.app.host.inspectSession(f.records[3].uri)).nativeSessionId,f.records[3].nativeSessionId);
  const source=await f.app.host.inspectRecallSource(session),body=await f.app.host.readRecallSource(session,{expectedRevision:source.revision});assert.ok(body.rows.some(row=>row.text==='Retained answer'));
  const context=await f.app.host.inspectSession(session);
  assert.equal((await f.app.host.submitScheduled(session,{commandId:'deferred',text:'retained deferred input',taskId:'saved-task',taskRevision:1,kind:'task',expectedExecutionRevision:context.executionRevision,expectedInterruptionRevision:context.interruptionRevision,expectedConfigurationHash:context.configurationHash})).accepted,true);
  assert.equal((await f.app.host.waitForTurn(session,'deferred')).completed,true);assert.equal((await f.app.host.readUserMessage(session,'deferred')).inputOrigin,'scheduled');
  const artifact=join(f.workspace,'artifact.txt');await writeFile(artifact,'retained artifact');
  await f.app.host.withSessionWorkspace(session,{},async captured=>assert.equal(await readFile(join(captured.executionDirectory,'artifact.txt'),'utf8'),'retained artifact'));
  catalog.get=get;catalog.projectSessionVisibility=project;
  const repaired=await f.settle((await f.call('recovery.presentation.rebuild',{expectedRevision:1,reviewed:true})).id);assert.equal(repaired.state,'succeeded',JSON.stringify(repaired));
  assert.equal((await discovery.list({connectionId:'repaired',limit:50,allowedWorkspaceRoots:[f.workspace]})).items.length,2);
  assert.equal((await workspaceAction('workspace.sessions')).items.length,2);
  const activeReview=await f.prepare([other]);await f.app.host.submitTurn(other,{commandId:'long',text:'queue-hold',clientId:'fixture'});
  const active=await f.apply(activeReview,'active-refusal');assert.equal(active.state,'refused');assert.equal(active.result.receipt.effect.executed,false);assert.match(active.result.receipt.effect.reason,/Active|admission/);
  await f.app.host.interruptSession(other,{commandId:'cancel',turnId:'long',clientId:'fixture'});await f.app.host.waitForTurn(other,'long');
  const prompts=(await readFile(f.log,'utf8')).trim().split('\n').map(line=>JSON.parse(line));assert.equal(prompts.filter(row=>row.args?.inputId==='deferred').length,1);
  await f.verify();
 }finally{gate.release();await f.close();}
});

for(const stage of [{at:1,after:false,name:'before first projection'},{at:2,after:false,name:'between projection pages'},{at:2,after:true,name:'after final commit before acknowledgement'}])test('actual factory SIGKILL retains original marker effect and repairs '+stage.name,{skip:!python,timeout:90000},async()=>{
 const f=await fixture(105);try{
  const prepared=await f.prepare(f.records.map(record=>record.uri));assert.equal(prepared.state,'prepared');
  await f.stop();
  await crashFactory(f,'recovery.presentation.apply',{preparedJobId:prepared.id,previewHash:prepared.previewHash},'killed-reset',stage);
  await f.start();
  const original=await f.call('recovery.command',{commandId:'killed-reset'}),observed=await f.call('recovery.reconcile',{jobId:original.id});
  assert.equal(observed.state,'succeeded');assert.equal(observed.result.receipt.effect.status,'completed');assert.equal(f.app.host.inspectConversationPresentation().ready,stage.after);
  const effect=structuredClone(observed.result.receipt.effect);
  const repair=await f.settle((await f.call('recovery.presentation.rebuild',{expectedRevision:1,reviewed:true})).id);assert.equal(repair.state,'succeeded',JSON.stringify(repair));
  const reconciled=await f.call('recovery.reconcile',{jobId:original.id});assert.deepEqual(reconciled.result.receipt.effect,effect);assert.equal(reconciled.result.receipt.projection.status,'ready');
  const rows=await f.app.host.withConversationCatalogRead(()=>f.catalog.list({connectionId:'repaired',limit:200,allowedWorkspaceRoots:[f.workspace]}));assert.equal(rows.items.length,0);
  assert.equal(f.app.host.diagnostics().activeAgents,0);await f.verify();
 }finally{await f.close();}
});

test('lost catalog index is explicitly reseeded before metadata scan and visibility reconstruction',{skip:!python,timeout:60000},async()=>{
 const f=await fixture(105);try{
  const reset=await f.apply(await f.prepare(f.records.map(record=>record.uri)),'reset');assert.equal(reset.state,'succeeded');
  const effect=structuredClone(reset.result.receipt.effect);await f.stop();for(const suffix of ['','-wal','-shm'])await rm(f.index+suffix,{force:true});await f.start();
  assert.equal((await f.app.host.inspectSession(f.records[0].uri)).nativeSessionId,f.records[0].nativeSessionId);
  const repair=await f.settle((await f.call('recovery.presentation.rebuild',{expectedRevision:1,reviewed:true})).id);assert.equal(repair.state,'succeeded',JSON.stringify(repair));
  for(const record of f.records){const actual=await f.catalog.get(record.uri);assert.equal(actual.nativeSessionId,record.nativeSessionId);assert.equal(actual.productHidden,true);}
  assert.deepEqual((await f.call('recovery.reconcile',{jobId:reset.id})).result.receipt.effect,effect);
  assert.equal(f.app.host.diagnostics().activeAgents,0);await f.verify();
 }finally{await f.close();}
});


test('SIGKILL between index-rebuild pages requires separate reviewed repair without rewriting original effects',{skip:!python,timeout:90000},async()=>{
 const f=await fixture(105);try{
  const reset=await f.apply(await f.prepare(f.records.map(record=>record.uri)),'reset');assert.equal(reset.state,'succeeded');
  const effect=structuredClone(reset.result.receipt.effect);await f.stop();for(const suffix of ['','-wal','-shm'])await rm(f.index+suffix,{force:true});
  await crashFactory(f,'recovery.presentation.rebuild',{expectedRevision:1,reviewed:true},'killed-rebuild',{at:2,after:false});await f.start();
  const original=await f.call('recovery.command',{commandId:'killed-rebuild'});
  const observed=await f.call('recovery.reconcile',{jobId:original.id});assert.equal(observed.state,'unknown');
  const retained=structuredClone(observed.result.receipt);
  assert.equal(f.app.host.inspectConversationPresentation().ready,false);
  assert.equal((await f.app.host.inspectSession(f.records[104].uri)).nativeSessionId,f.records[104].nativeSessionId);
  assert.equal((await f.unrelated()).count,1);
  const repair=await f.settle((await f.call('recovery.presentation.rebuild',{expectedRevision:1,reviewed:true},'separate-repair')).id);assert.equal(repair.state,'succeeded',JSON.stringify(repair));
  assert.deepEqual((await f.call('recovery.reconcile',{jobId:reset.id})).result.receipt.effect,effect);
  assert.deepEqual((await f.call('recovery.job',{jobId:original.id})).result.receipt,retained);
  for(const record of f.records)assert.equal((await f.catalog.get(record.uri)).productHidden,true);
  assert.equal(f.app.host.diagnostics().activeAgents,0);await f.verify();
 }finally{await f.close();}
});

test('full native recovery binds the same presentation port without acquiring its global maintenance fence',{skip:!python||!nativePython,timeout:60000},async()=>{
 const f=await fixture(2,{fullNative:true});try{
  assert.ok(f.app.capabilities.manifest.actions['recovery.snapshot']);
  assert.ok(f.app.capabilities.manifest.actions['recovery.presentation.apply']);
  assert.equal(Object.keys(f.app.capabilities.manifest.topics).filter(topic=>topic==='recovery').length,1);
  assert.equal(f.app.host.inspectQuiescence().intakeClosed,false);
  const reset=await f.apply(await f.prepare([f.records[0].uri]),'full-native-presentation');assert.equal(reset.state,'succeeded',JSON.stringify(reset));
  assert.equal(reset.fence,undefined);assert.equal(f.app.host.inspectQuiescence().intakeClosed,false);
  assert.equal((await f.unrelated()).count,1);assert.equal(f.app.host.diagnostics().activeAgents,0);await f.verify();
 }finally{await f.close();}
});
