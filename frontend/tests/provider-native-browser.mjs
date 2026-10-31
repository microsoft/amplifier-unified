// Ordinary native preparation + real rendered controls; only providers are synthetic.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,readFile,writeFile,appendFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {showComposerOption} from './composer-options-helpers.mjs';

const root=process.env.AMPLIFIER_TEST_ARTIFACTS;
assert.ok(root,'An owned artifact directory is required');
const evidence=root+'/provider1201-native';
await mkdir(evidence,{recursive:true});
const save=(name,value)=>writeFile(`${evidence}/${name}`,JSON.stringify(value,null,2)+'\n');
async function bounded(promise,ms,label){
 let timer;
 try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(label+' timed out')),ms)})])}
 finally{clearTimeout(timer)}
}
const fixturePath=fileURLToPath(new URL('../../tests/fixtures/provider_native_ui_server.py',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON,[fixturePath,evidence+'/fixture'],{stdio:['ignore','pipe','pipe']});
const exit=new Promise(resolve=>fixture.once('exit',(code,signal)=>resolve({code,signal})));
const drained=new Promise(resolve=>fixture.once('close',resolve));
let fixtureOutput='',fixtureErrors='',browser,page,primary,proof,flush,journey,discoveryRead;
const cleanupErrors=[];
let logWrites=Promise.resolve();
fixture.stdout.on('data',chunk=>{fixtureOutput+=chunk;logWrites=logWrites.then(()=>appendFile(evidence+'/fixture-stdout.log',chunk))});
fixture.stderr.on('data',chunk=>{fixtureErrors+=chunk;logWrites=logWrites.then(()=>appendFile(evidence+'/fixture-stderr.log',chunk))});
const pageErrors=[],evidenceErrors=[],actions=[],responses=[],assets=[],boundaries=[];
try{
 const url=await bounded(new Promise((resolve,reject)=>{
  fixture.once('error',reject);fixture.once('exit',code=>reject(Error('Fixture exited before readiness: '+code+'\n'+fixtureErrors)));
  fixture.stdout.on('data',()=>{for(const line of fixtureOutput.split('\n'))if(/^http:\/\/127\.0\.0\.1:\d+$/.test(line.trim()))resolve(line.trim())});
 }),60000,'Native fixture readiness');
 browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page=await context.newPage();page.setDefaultTimeout(10000);
 page.on('pageerror',error=>pageErrors.push(error.message));
 const requests=new Map();let eventWrites=Promise.resolve();
 function record(row){eventWrites=eventWrites.then(()=>appendFile(evidence+'/actions.jsonl',JSON.stringify(row)+'\n')).catch(error=>evidenceErrors.push(error.message))}
 page.on('request',request=>{
  if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
  const row={sequence:actions.length,command:request.postDataJSON(),clientId:request.headers()['x-amplifier-client'],phase:'sent'};
  requests.set(request,row);actions.push(row);record({...row});
 });
 page.on('response',response=>{
  const row=requests.get(response.request());if(!row)return;
  responses.push(bounded(response.json(),10000,'Action '+row.command.id).then(receipt=>{
   Object.assign(row,{phase:'received',status:response.status(),receipt});record({...row});
  }).catch(error=>{Object.assign(row,{phase:'receipt-unavailable',error:error.message});record({...row})}));
 });
 page.on('requestfailed',request=>{
  const row=requests.get(request);if(row){Object.assign(row,{phase:'transport-failed',error:request.failure()});record({...row})}
 });
 const staticManifest=JSON.parse(await readFile(process.env.PROVIDER_SELECTION_STATIC_MANIFEST,'utf8'));
 const assetPromises=[];let assetWrites=Promise.resolve();
 page.on('response',response=>{
  const address=new URL(response.url());if(address.origin!==new URL(url).origin)return;
  const path=address.pathname==='/'?'index.html':address.pathname.slice(1),expected=staticManifest['amplifier_web/static/'+path];
  if(!expected&&!path.startsWith('assets/')&&(path.startsWith('api/')||!['script','stylesheet','image','font','media'].includes(response.request().resourceType())))return;
  assetPromises.push((async()=>{
   assert.ok(expected,'Unmanifested static response '+path);assert.equal(response.status(),200);
   const sha256=createHash('sha256').update(await bounded(response.body(),10000,'Static '+path)).digest('hex');
   assert.equal(sha256,expected,path);return{path,sha256};
  })().catch(error=>({path,error:error.message})).then(row=>{
   assets.push(row);assetWrites=assetWrites.then(()=>appendFile(evidence+'/consumed-assets.jsonl',JSON.stringify(row)+'\n'));return row;
  }));
 });
 flush=async()=>{
  const pending=[...actions];
  await expect.poll(()=>pending.filter(row=>row.phase==='sent').length,{timeout:12000}).toBe(0);
  await bounded(Promise.all(responses),12000,'Observed action receipts');await eventWrites;
  await bounded(Promise.all(assetPromises),12000,'Consumed assets');await assetWrites;
  await save('actions.json',actions);await save('consumed-assets.json',assets);
 };
 proof=async()=>{
  const response=await page.request.get(url+'/fixture/native-proof',{timeout:10000});assert.equal(response.status(),200);return response.json();
 };
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const model=page.locator('.a-composer').getByRole('button',{name:'Model and reasoning settings',exact:true});
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 const dialog=page.getByRole('dialog',{name:'Chat options',exact:true});
 const A={instance:'native-a',model:'native-model',effort:'low'};
 const B={instance:'native-b',model:'native-model',effort:'high'};
 const label=instance=>`${instance==='native-a'?'Native A':'Native B'} (${instance})`;
 const managedKey=JSON.stringify(['','','managed']);
 const managedWorkspace=evidence+'/fixture/app';
 const discoverySnapshots=[];
 // Read the exact browser client's saved state, not its optimistic pending overlay.
 discoveryRead=async()=>{
  const client=(await state()).client;
  const response=await page.request.get(url+'/api/state?'+new URLSearchParams({clientId:client.id}),{timeout:10000});
  assert.equal(response.status(),200);
  const value=await response.json();assert.equal(value.client.id,client.id);
  assert.equal(value.client.hostInstanceId,client.hostInstanceId);
  const canonical=(await proof()).discovery;
  assert.equal(canonical.hostInstanceId,client.hostInstanceId);
  const row={client:value.client,revision:value.revision,configurationRevision:value.configurationRevision||0,
   selectedSessionId:value.selectedSessionId,view:{panel:value.view?.panel,setupJourney:value.view?.setupJourney,newSessionDraft:value.view?.newSessionDraft},
   setup:{...Object.fromEntries(['providers','providerCatalogs','providersWorkspace','providersRequestedWorkspace','providersLocation','providersLoadedAt'].map(key=>[key,value.setup?.[key]]))},
   defaults:value.draftDefaults?.[managedKey]||null,defaultsKey:managedKey,canonical};
  discoverySnapshots.push(row);await appendFile(evidence+'/discovery-state.jsonl',JSON.stringify(row)+'\n');
  return row;
 };
 function discoveryProblems(row,binding){
  const problems=[],draft=row.view?.newSessionDraft,setup=row.setup||{},defaults=row.defaults;
  if(row.client?.id!==binding.clientId||row.client?.hostInstanceId!==binding.hostInstanceId)problems.push('client/host');
  if(row.revision<binding.minimumRevision||row.configurationRevision!==binding.configurationRevision)problems.push('stale revision');
  if(row.selectedSessionId||draft?.workspace!==''||draft?.location?.kind!=='managed'||(draft.bundle||'')!=='')problems.push('draft scope');
  if(Object.keys(draft?.selection||{}).length)problems.push('explicit draft pin');
  if(row.defaultsKey!==managedKey||setup.providersWorkspace!==managedWorkspace||setup.providersRequestedWorkspace!==''||setup.providersLocation?.kind!=='managed')problems.push('catalog scope');
  if(!setup.providersLoadedAt||!['native-a','native-b'].every(id=>setup.providers?.some(p=>p.id===id&&p.enabled!==false)))problems.push('provider rows');
  if(defaults?.phase!=='ready'||!['native-a','native-b'].every(id=>defaults.providers?.some(p=>p.id===id)))problems.push('default rows');
  if(defaults?.globalOnly!==true||defaults?.bundle!==evidence+'/fixture/root.yaml')problems.push('default scope');
  if(defaults?.effective?.instance!==A.instance||defaults?.effective?.model!==A.model||defaults?.effective?.effort!==A.effort)problems.push('effective default');
  if(!['native-a','native-b'].every(id=>{
   const entry=setup.providerCatalogs?.[id];
   return entry?.phase==='ready'&&!entry.error&&!entry.modelsDeferred&&entry.sharedCatalogKey&&
    entry.metadata?.info&&entry.models?.some(model=>(typeof model==='string'?model:model.id)==='native-model');
  }))problems.push('catalog settlement');
  const canonical=row.canonical,operation=canonical?.setup?.operations?.['providers.list:'];
  if(canonical?.hostInstanceId!==binding.hostInstanceId||canonical?.configurationRevision!==binding.configurationRevision)problems.push('canonical revision');
  if(canonical?.setup?.configurationTarget!=null)problems.push('targeted settings snapshot');
  if(operation?.phase!=='ready'||operation?.error||operation?.commandId!==binding.listId)problems.push('list operation');
  return problems;
 }
 async function completionFor(id,name,args,receipt,client){
  assert.equal(receipt.accepted,true);
  let completion;
  await expect.poll(async()=>{
   const response=await page.request.get(url+'/api/state/detail?'+new URLSearchParams({clientId:client.id,path:'/managementResults/'+id}),{timeout:10000});
   const body=await response.json();
   completion={id,name,args,receipt,client,status:response.status(),body};
   await appendFile(evidence+'/discovery-completions.jsonl',JSON.stringify(completion)+'\n');
   if(response.status()===400&&body.accepted===false&&body.error==='Invalid request: That state path does not exist.')return 'pending';
   assert.equal(response.status(),200);
   return Object.fromEntries((body.items||[]).map(row=>[row.key,row.value])).phase;
  },{timeout:10000}).toMatch(/^(ready|error)$/);
  const result=Object.fromEntries(completion.body.items.map(row=>[row.key,row.value]));
  assert.equal(result.phase,'ready',name+' completion');assert.equal(result.error,null);
  return completion;
 }
 async function completedRead(name,args){
  const id=crypto.randomUUID(),receipt=await page.evaluate(([name,args,id])=>window.amplifier.dispatch(name,args,{id}),[name,args,id]);
  return completionFor(id,name,args,receipt,(await state()).client);
 }
 async function settleManagedDraft(){
  const before=await discoveryRead();
  assert.equal(before.view.setupJourney,'done','Visible first-connection flow must have completed');
  const completed=await Promise.all([
   completedRead('providers.list',{workspace:'',location:{kind:'managed'}}),
   completedRead('configuration.defaults',{workspace:'',bundle:'',location:{kind:'managed'}}),
  ]);
  const binding={clientId:before.client.id,hostInstanceId:before.client.hostInstanceId,configurationRevision:before.configurationRevision,
   minimumRevision:Math.max(before.revision,...completed.map(row=>row.body.revision)),listId:completed[0].id};
  let ready;
  await expect.poll(async()=>{ready=await discoveryRead();return discoveryProblems(ready,binding)},{timeout:10000}).toEqual([]);
  await expect(page.getByRole('dialog',{name:'Settings',exact:true})).toBeHidden();
  assert.equal(ready.view.setupJourney,'done');
  const checks=[];
  for(const [name,mutate,reason] of [
   ['old-snapshot',row=>{row.revision=binding.minimumRevision-1},'stale revision'],
   ['wrong-scope',row=>{row.setup.providersLocation={kind:'workspace'}},'catalog scope'],
   ['empty-provider-snapshot',row=>{row.setup.providers=[];row.setup.providerCatalogs={}},'provider rows'],
   ['wrong-operation',row=>{row.canonical.setup.operations['providers.list:'].commandId='old-operation'},'list operation'],
   ['old-configuration',row=>{row.configurationRevision--},'stale revision'],
  ]){
   const bad=structuredClone(ready);mutate(bad);const rejected=discoveryProblems(bad,binding);
   assert.ok(rejected.includes(reason),name);checks.push({name,rejected});
  }
  await save('discovery-readiness.json',{binding,completed,ready,negativeControls:checks});
  return ready;
 }
 async function close(){
  const current=await state(),baseline=actions.length;
  const matches=request=>{
   if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return false;
   const command=request.postDataJSON(),patch=command.args?.patch?.composerModel;
   return command.action==='view.update'&&patch?.open===false&&
    (patch.sessionId??null)===(current.selectedSessionId??null)&&request.headers()['x-amplifier-client']===current.client.id;
  };
  const receiptPromise=page.waitForResponse(response=>matches(response.request()),{timeout:15000});
  await dialog.getByRole('button',{name:'Close chat options',exact:true}).click();
  await expect(dialog).toBeHidden();await expect(model).toHaveAttribute('aria-expanded','false');
  const response=await receiptPromise,request=response.request();
  const row=requests.get(request);assert.ok(row&&row.sequence>=baseline);
  const receipt=await bounded(response.json(),10000,'Exact close receipt');
  assert.equal(response.status(),200);assert.equal(receipt.accepted,true);
  await appendFile(evidence+'/close-receipts.jsonl',JSON.stringify({command:request.postDataJSON(),receipt,clientId:current.client.id})+'\n');
 }
 async function visible(expected,timeout=10000){
  const row={expected};boundaries.push(row);await save('visible.json',boundaries);
  if(!await dialog.isVisible())await model.click();
  await showComposerOption(page,'provider');const provider=dialog.locator('#chat-provider');
  await expect(provider).toBeVisible();row.instance=await provider.inputValue();row.label=await provider.locator('option:checked').innerText();await save('visible.json',boundaries);
  await expect(provider,'Exact visible connection').toHaveValue(expected.instance,{timeout});
  await expect(provider.locator('option:checked')).toHaveText(label(expected.instance),{timeout});
  row.instance=await provider.inputValue();row.label=await provider.locator('option:checked').innerText();
  await showComposerOption(page,'model');const selectedModel=dialog.locator('#chat-model');
  await expect(selectedModel).toBeVisible();await expect(selectedModel).toHaveValue(expected.model,{timeout});
  row.model=await selectedModel.inputValue();
  await showComposerOption(page,'effort');const effort=dialog.locator('#chat-effort');
  await expect(effort).toBeVisible();await expect(effort).toHaveAttribute('aria-valuetext',expected.effort,{timeout});
  row.effort=await effort.getAttribute('aria-valuetext');await close();
  await expect(model.locator(':scope > span')).toHaveText(`${expected.model} (${expected.effort})`,{timeout});
  row.chip=await model.locator(':scope > span').innerText();row.dismissed=true;
  await save('visible.json',boundaries);return row;
 }
 const tuple=value=>({instance:value.instance||value.id,model:value.model,effort:value.effort});
 let sendIndex=0,firstNative=null;
 async function send(expected,existingId=null){
  const index=sendIndex++,text='Native provider boundary '+index;
  if(!existingId){
   // A manual draft title suppresses auxiliary Auto naming without pinning a model.
   const current=await state();
   const receipt=await action('view.update',{patch:{newSessionDraft:{...current.view.newSessionDraft,title:'Native boundary chat '+index}}});
   assert.equal(receipt.accepted,true);
  }
  await composer.fill(text);
  const before={index,text,display:await visible(expected),proof:await proof()};
  assert.equal(before.proof.calls.length,index);await expect(composer).toHaveText(text);
  await save(`send-${index}-before.json`,before);
  const sentPromise=page.waitForResponse(response=>{
   const request=response.request();if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return false;
   const command=request.postDataJSON();return command.action==='conversation.send'&&command.args?.text===text;
  },{timeout:60000});
  await composer.press('Enter');
  const response=await sentPromise;const command=response.request().postDataJSON(),receipt=await bounded(response.json(),10000,'Send receipt');
  await save(`send-${index}-receipt.json`,{command,status:response.status(),receipt});
  assert.equal(response.status(),200);assert.equal(receipt.accepted,true);assert.equal(receipt.delivery,'accepted');
  assert.equal(receipt.sessionId,command.args.sessionId);assert.ok(typeof receipt.inputId==='string'&&receipt.inputId);
  await expect.poll(async()=>{const current=await proof();await save('native-current.json',current);return current.calls.length},{timeout:90000}).toBe(index+1);
  let after;
  await expect.poll(async()=>{
   after=await proof();
   const terminal=after.lifecycle.find(row=>row.kind==='runtime.generation'&&row.event==='generation.finished'&&
    row.sessionId===receipt.sessionId&&(row.input_ids||[]).includes(receipt.inputId));
   return !!terminal&&after.sessions.find(row=>row.id===receipt.sessionId)?.status==='idle';
  },{timeout:30000}).toBe(true);
  await save(`send-${index}-after.json`,after);
  const call=after.calls[index];assert.deepEqual(tuple(call),expected);
  const session=after.sessions.find(row=>row.id===receipt.sessionId);assert.ok(session);
  assert.equal(session.controlsPresent,true);
  assert.equal(call.sessionId,session.nativeId);assert.ok(call.inputIds.includes(receipt.inputId),'Actual provider input identity must match admitted send');
  if(existingId)assert.equal(session.id,existingId);
  const worker=after.workers.find(row=>row.kind==='runtime_worker'&&row.pid===call.pid);assert.ok(worker);
  const ready=[...after.workerReady].reverse().find(row=>row.sessionId===session.id&&row.pid===call.pid&&row.atNs<call.atNs);assert.ok(ready);
  assert.equal(ready.report.session_id,session.nativeId);
  assert.equal(session.mountedReport.session_id,session.nativeId);
  assert.equal(session.mountedReport.contextIntelligence.enabled,true);
  // Existing-worker B may be applied through provider.select after its original A preparation.
  // Preserve that actual distinction instead of relabeling the initial mounted report as B.
  if(index!==1){
   assert.deepEqual(tuple(ready.report.effective_selection),expected);
   assert.deepEqual(tuple(session.mountedReport.effective_selection),expected);
   assert.equal(session.savedSelection,null);
  }else{
   assert.ok(firstNative,'A preparation evidence must precede existing-chat B');
   assert.equal(call.pid,firstNative.call.pid,'Warm B must use the original A worker PID');
   assert.equal(worker.startTicks,firstNative.worker.startTicks,'Worker PID start identity must not change');
   assert.equal(session.nativeId,firstNative.session.nativeId);
   assert.deepEqual(ready,firstNative.ready,'Warm B retains the actual original A-ready event');
   assert.deepEqual(session.mountedReport,firstNative.session.mountedReport,'Original A mounted report must remain the provenance');
   assert.deepEqual(tuple(ready.report.effective_selection),A);
   assert.equal(session.savedSelection.instance,expected.instance);
   assert.equal(session.savedSelection.model,expected.model);
   const applied=after.lifecycle.find(row=>row.kind==='runtime.nextMessageApplied'&&row.sessionId===session.id&&row.selection?.instance===expected.instance&&row.atNs<call.atNs);
   assert.ok(applied);
  }
  const controlsBefore=after.providerControls.length;
  const controlReceipt=await action('runtime.control',{sessionId:session.id,operation:'configuration.providers',args:{}});
  assert.equal(controlReceipt.accepted,true);
  await expect.poll(async()=>{
   after=await proof();
   return after.providerControls.slice(controlsBefore).some(row=>row.sessionId===session.id);
  },{timeout:30000}).toBe(true);
  const providerControl=after.providerControls.slice(controlsBefore).find(row=>row.sessionId===session.id);
  assert.equal(providerControl.pid,call.pid);
  assert.deepEqual(tuple(providerControl.result.effective),expected);
  assert.equal(providerControl.result.pinned,index===1);
  await save(`send-${index}-provider-control.json`,{receipt:controlReceipt,observation:providerControl});
  if(index===0)firstNative=structuredClone({call,worker,session,ready,providerControl});
  if(index===1)await save('warm-b-evidence.json',{originalA:firstNative,call,worker,session,ready,providerControl});
  await flush();return session.id;
 }
 await page.goto(url);
 await page.locator('.a-adaptive-home').getByRole('button',{name:'New chat',exact:true}).click();
 // Do not race first-use discovery with the measured composer journey.
 // In this fresh fixture the actual onboarding control, not a hidden view patch,
 // completes setup once a configured connection is visibly ready.
 const setupJourney=page.getByRole('region',{name:'Get started',exact:true});
 await expect(setupJourney.getByRole('heading',{name:'Your first connection is ready',exact:true})).toBeVisible();
 await discoveryRead();
 // Settings' optional routing catalog may fail under the audit guard. Retain
 // the real result; do not dispatch it again, install it, or treat admission as success.
 let routingRequest;
 await expect.poll(()=>{routingRequest=actions.find(row=>row.command.action==='routing.list'&&row.phase==='received');return !!routingRequest},{timeout:10000}).toBe(true);
 let routingProof;
 await expect.poll(async()=>{
  routingProof=await proof();
  return routingProof.routingDiscovery.some(row=>row.action==='routing.list'&&row.arguments.configurationTarget?.kind==='global'&&typeof row.result.routingCatalogError==='string');
 },{timeout:10000}).toBe(true);
 const routingRecords=routingProof.routingDiscovery.filter(row=>row.action==='routing.list'&&row.arguments.configurationTarget?.kind==='global');
 assert.equal(actions.filter(row=>row.command.action==='routing.list').length,1,'One observed optional routing request');
 assert.equal(routingRecords.length,1,'One actual optional routing result');
 const guard=routingProof.guardRefusals.find(row=>row.event==='subprocess.Popen'&&row.detail.includes('amplifier-bundle-routing-matrix'));
 assert.ok(guard,'Actual audit refusal retained');
 const routingCompletion=await completionFor(routingRequest.command.id,'routing.list',routingRequest.command.args,routingRequest.receipt,(await state()).client);
 await save('optional-routing-discovery.json',{request:routingRequest,completion:routingCompletion,result:routingRecords[0],guard,scope:'Optional Settings discovery; excluded from ordinary fixture root. Guard remains enforced.'});
 const setupClient=(await state()).client.id;
 const setupCompletion=page.waitForResponse(response=>{
  const request=response.request();if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return false;
  const command=request.postDataJSON();
  return command.action==='view.update'&&command.args?.patch?.setupJourney==='done'&&request.headers()['x-amplifier-client']===setupClient;
 },{timeout:10000});
 const setupDraft=page.waitForResponse(response=>{
  const request=response.request();return request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions'&&
   request.postDataJSON().action==='session.draft'&&request.headers()['x-amplifier-client']===setupClient;
 },{timeout:10000});
 await setupJourney.getByRole('button',{name:'Start a chat',exact:true}).click();
 const setupResponses=await Promise.all([setupCompletion,setupDraft]);
 const setupReceipts=await Promise.all(setupResponses.map(async response=>({command:response.request().postDataJSON(),status:response.status(),receipt:await bounded(response.json(),10000,'Visible setup receipt')})));
 await save('onboarding-receipts.json',setupReceipts);
 for(const row of setupReceipts){assert.equal(row.status,200);assert.equal(row.receipt.accepted,true)}
 await expect(setupJourney).toBeHidden();
 await settleManagedDraft();
 const initial=await proof();await save('native-initial.json',initial);assert.equal(initial.calls.length,0);assert.equal(initial.readiness.importsReady,true);
 const originalSession=await send(A);
 await model.click();await showComposerOption(page,'provider');
 const configurePromise=page.waitForResponse(response=>{
  const request=response.request();if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return false;
  const command=request.postDataJSON();return command.action==='conversation.configure'&&command.args.sessionId===originalSession&&command.args.selection?.instance==='native-b';
 },{timeout:15000});
 await dialog.locator('#chat-provider').selectOption('native-b');
 const configured=await configurePromise,configureCommand=configured.request().postDataJSON(),configureReceipt=await bounded(configured.json(),10000,'Configuration receipt');
 await save('existing-chat-choice.json',{command:configureCommand,status:configured.status(),receipt:configureReceipt});
 assert.equal(configured.status(),200);assert.equal(configureReceipt.accepted,true);
 assert.equal(configureReceipt.result.revision,configureCommand.args.expectedRevision+1);
 await close();await visible(B);
 // Same-model/effort negative uses the SAME visible observer; no extra send.
 const countBefore=(await proof()).calls.length;
 await assert.rejects(()=>visible({...B,instance:'native-a'},300),error=>error.matcherResult?.name==='toHaveValue'&&error.matcherResult.expected==='native-a'&&error.matcherResult.actual==='native-b');
 await close();const countAfter=(await proof()).calls.length;
 await save('wrong-connection-negative.json',{expected:{...B,instance:'native-a'},actual:B,rejected:true,callsBefore:countBefore,callsAfter:countAfter});assert.equal(countAfter,countBefore);
 await send(B,originalSession);
 const sessionIds=[originalSession];
 for(let n=0;n<2;n++){
  await flush();const receipt=await action('session.draft');assert.equal(receipt.accepted,true);
  await settleManagedDraft();
  const id=await send(A);assert.ok(!sessionIds.includes(id));sessionIds.push(id);
 }
 await flush();assert.deepEqual(pageErrors,[]);assert.deepEqual(evidenceErrors,[]);assert.deepEqual(assets.filter(row=>row.error),[]);
 const final=await proof();assert.equal(final.calls.length,4);
 assert.deepEqual(final.sharedSettings.current,final.sharedSettings.initial);
 await save('native-final.json',final);
 journey={outcome:'passed',sends:4,sessions:sessionIds,sequence:[A,B,A,A],pageErrors,evidenceErrors,scope:'Real ordinary preparation and synthetic providers, not literal unspecified CLI incident',staticResponses:assets.length};
 await save('journey.json',journey);
}catch(error){
 primary=error;const failure={primary:{name:error.name,message:error.message,stack:error.stack},pageErrors,evidenceErrors};
 const retainFailure=async()=>{try{await save('failure.json',failure)}catch(capture){cleanupErrors.push({stage:'failure-save',error:capture.message})}};
 await retainFailure();
 for(const [name,read] of [['native',()=>proof?.()],['discovery',()=>discoveryRead?.()],['receipts',async()=>{await flush?.();return actions}],['screenshot',async()=>{if(page)await page.screenshot({path:evidence+'/failure.png',timeout:3000});return 'failure.png'}]]){
  try{failure[name]=await bounded(Promise.resolve().then(read),15000,'Failure '+name)}catch(capture){failure[name]={error:capture.message}}
  await retainFailure();
 }
}finally{
 try{await bounded(browser?.close(),10000,'Browser close')}
 catch(error){primary??=error;cleanupErrors.push({stage:'browser-close',error:error.message})}
 finally{
  if(fixture.exitCode===null&&fixture.signalCode===null)fixture.kill('SIGTERM');
  let outcome;
  try{outcome=await bounded(exit,60000,'Owned fixture cleanup')}
  catch(error){
   primary??=error;outcome={unknown:true,error:error.message};fixture.kill('SIGKILL');
   try{outcome.forced=await bounded(exit,5000,'Owned fixture forced exit')}catch(forced){outcome.forcedError=forced.message}
  }
  try{
   await bounded(drained,5000,'Fixture streams');await logWrites;await save('fixture-exit.json',outcome);
   const cleanup=JSON.parse(await readFile(evidence+'/fixture/cleanup.json','utf8'));
   assert.equal(cleanup.complete,true);assert.deepEqual(cleanup.fallbackPids,[]);
   await save('cleanup-readback.json',cleanup);
  }
  catch(error){primary??=error;cleanupErrors.push({stage:'fixture-retention',error:error.message})}
  if(outcome.code!==0){primary??=Error('Fixture cleanup did not exit cleanly: '+JSON.stringify(outcome));}
 }
}
if(primary){console.error(JSON.stringify({primary:primary.message,cleanupErrors}));throw primary}
await save('result.json',{...journey,cleanupErrors,fixtureClosed:true});
console.log('Native provider boundary passed: A -> existing-chat B -> two inherited A chats; real prepare_manager, worker and synthetic provider entry.');
