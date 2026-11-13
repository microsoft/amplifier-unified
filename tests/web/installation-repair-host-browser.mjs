// Actual host dispatcher/auth/static/HTTP/SSE. Native installation, helper and
// service effects are synthetic Python fixture seams, NOT native end-to-end.
import './composer-test-helpers.mjs';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {startFixture} from './fixture-readiness.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const output=resolve(process.env.AMPLIFIER_TEST_ARTIFACTS||root+'output/browser-checks/installation-repair-host');
const prefix='maintenance.installationRepair',token='Bearer fixture-browser-control-token';
// The selection record owns normal future runs. An integration owner can also
// pin a campaign to an exact reviewed client without editing this scenario.
const expectedClient=process.env.AMPLIFIER_TEST_CLIENT_REVISION;
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const readJSON=async name=>JSON.parse(await readFile(resolve(root,name),'utf8'));
const actions=[],errors=[],observerErrors=[],httpErrors=[],checkpoints=[],screenshots=[],assetReads=[];
const servedAssets={},cleanup={browser:'not-started',fixture:'not-started',errors:[]};
let owned,browser,page,base,failure,identity,source,component,build,initial,final,streamEvidence,phase='setup',passed=false;
const unwrap=body=>body.action==='shell.command'?(body.args.command||body.args):body;
const confirms=()=>actions.filter(row=>row.action===prefix+'Confirm');
const statuses=()=>actions.filter(row=>row.action===prefix+'Status');
const section=()=>page.locator('[data-part="installation-repair"]');
const healthy=()=>section().getByText('Installation repair verified healthy',{exact:true});
const noFallback=()=>{
 assert.deepEqual(actions.filter(row=>['maintenance.repair','maintenance.reset','maintenance.resetCleanup',
  'updates.install','updates.forceRestart','conversation.send','conversation.warm','session.warm',
  'runtime.start','runtime.control'].includes(row.action)),[],'No fallback, reset, update install, send or warm');
 assert.deepEqual(observerErrors,[]);
};
async function observe(){
 const response=await page.request.get(base+'/fixture/installation-repair',{headers:{Authorization:token}});
 assert.equal(response.status(),200);return response.json();
}
async function post(action,args){
 // Genuine same-origin browser fetch, with the attached client and cookie.
 // This deliberately bypasses only the UI gesture for invalid/idempotency probes.
 return page.evaluate(async({action,args})=>{
  const response=await fetch('/api/actions',{method:'POST',
   headers:{'Content-Type':'application/json','X-Amplifier-Client':window.amplifier.getState().client.id},
   body:JSON.stringify({action,args})});
  return {status:response.status,payload:await response.json()};
 },{action,args});
}
function accepted(response){assert.equal(response.status,200);assert.equal(response.payload.accepted,true);return response.payload.result}
async function rejected(action,args){
 const response=await post(action,args);
 assert.ok([400,409,422].includes(response.status));
 assert.equal(response.payload.accepted,false);
 assert.equal(typeof response.payload.error,'string');
 httpErrors.push({action,status:response.status,error:response.payload.error});
 return response;
}
async function capture(name){
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal viewport overflow');
 assert.ok(await section().evaluate(node=>node.scrollWidth<=node.clientWidth+1),'Repair section must reflow');
 const path=resolve(output,name+'.png');
 await page.screenshot({path,animations:'disabled'});screenshots.push({path,viewport:page.viewportSize()});
}
async function checkpoint(name,helpers,confirmations){
 const observed=await observe();
 assert.equal(observed.counters.helpers,helpers,name);
 assert.equal(confirms().length,confirmations,name);
 assert.deepEqual(observed.counters.runtimeCalls,[]);
 assert.equal(observed.backgroundUpdatesStopped,true);
 assert.equal(observed.privateCredentialProjected,false);
 assert.equal(observed.sessionId,initial.sessionId);
 assert.equal(observed.selectedSessionId,initial.selectedSessionId);
 assert.deepEqual(observed.messages,initial.messages,'Saved history preserved');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),initial.sessionId);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),'Keep my unsent installation repair draft');
 noFallback();checkpoints.push({name,helpers,confirmationPosts:confirmations,counters:observed.counters});
 return observed;
}
async function checkStatus(){
 const button=section().getByRole('button',{name:'Check repair status',exact:true});
 await expect(button).toBeEnabled();
 const response=page.waitForResponse(response=>{
  const request=response.request();
  return new URL(response.url()).pathname==='/api/actions'&&request.method()==='POST'&&
   unwrap(request.postDataJSON()).action===prefix+'Status';
 });
 await button.click();
 const receipt=await response;assert.equal(receipt.status(),200);
 const body=await receipt.json();assert.equal(body.accepted,true);assert.ok(Object.hasOwn(body.result,'operation'));
 await expect(button).toBeEnabled();return body.result.operation;
}
async function advance(mode,operationId){
 const response=await page.request.post(base+'/fixture/installation-repair',{
  headers:{Authorization:token},data:{mode,operationId}});
 assert.equal(response.status(),200);
 assert.deepEqual(await response.json(),{operationId,status:mode});
 await checkStatus();
}
async function wireOperation(operationId,status){
 await expect.poll(()=>page.evaluate(({operationId,status})=>
  installationRepairStreams.flatMap(s=>s.operations).some(row=>row.operationId===operationId&&row.status===status),
 {operationId,status})).toBe(true);
}

await mkdir(output,{recursive:true});
try{
 source=await readJSON('components/web-client.source.json');
 if(expectedClient)assert.equal(source.source.revision,expectedClient,'Exact selected campaign client');
 assert.deepEqual(await readJSON('.build/web-client/.client-source.json'),source);
 component=await readJSON('amplifier_web/static/client-component.json');
 build=await readJSON('amplifier_web/static/build.json');
 assert.deepEqual(component.source,source.source,'Build the selected client before pairing');
 assert.deepEqual(component.build,build);
 assert.equal(component.pairedServer.qualification,'unqualified','Shared-input provenance is not actual-host acceptance');
 owned=await startFixture({command:fixturePython(root+'.venv/bin/python'),
  args:['-u',root+'tests/fixtures/installation_repair_server.py'],env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},
  tag:'installation-repair-host',name:'Installation repair host'});
 base=owned.base;cleanup.fixture='running';
 const unauthorized=await fetch(base+'/api/state');assert.equal(unauthorized.status,401);
 const invalidToken=await fetch(base+'/api/state',{headers:{Authorization:'Bearer invalid-fixture-token'}});
 assert.equal(invalidToken.status,401,'Invalid bearer alone must not authenticate');
 browser=await chromium.launch({headless:true,
  ...(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}:{}),
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 cleanup.browser='running';
 const context=await browser.newContext({viewport:{width:1280,height:1000}});
 const cookie=await context.request.post(base+'/fixture/installation-repair-cookie',{headers:{Authorization:token}});
 assert.equal(cookie.status(),200);
 page=await context.newPage();
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{
  if(new URL(request.url()).origin!==base||request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
  try{const body=unwrap(request.postDataJSON());assert.equal(typeof body.action,'string');actions.push(body)}
  catch(error){observerErrors.push(String(error))}
 });
 page.on('response',response=>{
  const url=new URL(response.url());
  if(url.origin!==base||!response.ok())return;
  const name=url.pathname==='/'?'index.html':decodeURIComponent(url.pathname.slice(1));
  if(!Object.hasOwn(component.assets,name)&&name!=='client-component.json')return;
  assetReads.push(response.body().then(bytes=>{
   servedAssets[name]={sha256:sha(bytes),bytes:bytes.length};
  }).catch(error=>observerErrors.push(String(error))));
 });
 await page.addInitScript(()=>{
  const Native=window.EventSource;window.installationRepairStreams=[];
  window.EventSource=class extends Native{
   constructor(...args){
    super(...args);const probe={opens:0,states:0,deltas:0,operations:[]};installationRepairStreams.push(probe);
    this.addEventListener('open',()=>probe.opens++);
    for(const name of ['state','state-delta'])this.addEventListener(name,event=>{
     probe[name==='state'?'states':'deltas']++;
     // Count actual wire frames; do not replace them or inject application state.
     try{
      const walk=value=>{
       if(!value||typeof value!=='object')return;
       if(/^[a-f0-9]{32}$/.test(value.operationId)&&/^[a-f0-9]{32}$/.test(value.previewId)&&
        ['unknown','reinstalled','ready'].includes(value.status))
        probe.operations.push({operationId:value.operationId,previewId:value.previewId,status:value.status});
       for(const child of Object.values(value))if(child&&typeof child==='object')walk(child);
      };
      walk(JSON.parse(event.data));
     }catch{}
    });
   }
  };
 });
 await page.goto(base+'/');await showFixtureConversation(page);
 await expect.poll(()=>page.evaluate(()=>installationRepairStreams.reduce((n,s)=>n+s.states,0))).toBeGreaterThan(0);
 initial=await observe();identity=initial.identity;
 for(const [name,digest] of Object.entries(identity))assert.equal(sha(await readFile(resolve(root,name))),digest,name);
 assert.ok(initial.counters.sseConnections>0);
 assert.equal(initial.operation,null);assert.deepEqual(initial.counters.runtimeCalls,[]);
 assert.equal(initial.historySource,'state_records.load_owner/session_projection.hydrate');
 await expect(page.getByText('Keep this saved installation repair conversation.',{exact:true})).toBeVisible();
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await expect(composer).toBeEditable();await composer.fill('Keep my unsent installation repair draft');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.draft)).toBe('Keep my unsent installation repair draft');
 phase='public schemas';
 const catalogResponse=await page.request.get(base+'/api/actions');assert.equal(catalogResponse.status(),200);
 const catalog=await catalogResponse.json();
 const schemas=Object.fromEntries(['Preview','Confirm','Status'].map(suffix=>{
  const row=catalog.find(row=>row.name===prefix+suffix);assert.ok(row,'Real host advertises '+suffix);
  assert.equal(row.inputSchema.type,'object');assert.equal(row.inputSchema.additionalProperties,false);
  return [suffix,row.inputSchema];
 }));
 assert.deepEqual(schemas.Confirm.properties.confirm.enum,[true]);
 assert.deepEqual([...schemas.Confirm.required].sort(),['confirm','hostInstanceId','previewId']);
 // An extra property must fail public schema admission, without even binding.
 await rejected(prefix+'Preview',{unexpected:true});
 assert.equal((await observe()).counters.bindingReads,0);
 phase='open and passive status';
 await openSettingsPage(page,'repair',{activate:(_label,control,options)=>control.click(options)});
 await expect(section()).toBeVisible();await checkStatus();
 await checkpoint('opening and status are passive',0,0);
 phase='preview';
 const previewResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/actions'&&
  response.request().method()==='POST'&&unwrap(response.request().postDataJSON()).action===prefix+'Preview');
 await section().getByRole('button',{name:'Preview installation repair',exact:true}).click();
 const previewHTTP=await previewResponse;assert.equal(previewHTTP.status(),200);
 const previewBody=await previewHTTP.json();assert.equal(previewBody.accepted,true);
 const preview=previewBody.result;
 assert.equal(preview.supported,true);assert.match(preview.previewId,/^[a-f0-9]{32}$/);
 assert.equal(preview.hostInstanceId,initial.hostInstanceId);assert.equal(preview.dataDir,initial.dataDir);
 assert.equal(preview.scope,'application-installation');
 assert.deepEqual(preview.target,{version:'99.0.0',revision:'a'.repeat(40),
  source:'https://github.com/microsoft/amplifier-unified/releases/download/v99.0.0/amplifier_unified-99.0.0-py3-none-any.whl'});
 assert.deepEqual(preview.installedExtras,['native-desktop']);
 assert.deepEqual(actions.filter(row=>row.action===prefix+'Preview').at(-1).args,{});
 for(const text of ['Application installation only','Workspace files','Credentials','Downloaded source caches',
  'Operation and recovery receipts','Installed optional features','native-desktop','99.0.0',initial.hostInstanceId,initial.dataDir])
  await expect(section()).toContainText(text);
 const confirm=section().getByRole('button',{name:'Confirm installation repair',exact:true});
 await expect(confirm).toBeDisabled();
 await rejected(prefix+'Confirm',{previewId:preview.previewId,hostInstanceId:preview.hostInstanceId,confirm:false});
 // The invalid HTTP probe above is the sole pre-gesture confirm POST.
 await checkpoint('preview and invalid confirmation launch nothing',0,1);
 assert.equal((await observe()).counters.artifactPreparations,0);
 await section().getByRole('checkbox').scrollIntoViewIfNeeded();await capture('preview-desktop');
 await page.setViewportSize({width:390,height:844});
 await section().getByRole('checkbox').scrollIntoViewIfNeeded();await capture('preview-390');
 await expect(confirm).toBeDisabled();await section().getByRole('checkbox').check();await expect(confirm).toBeEnabled();
 phase='explicit confirmation';
 const confirmationArgs={previewId:preview.previewId,hostInstanceId:preview.hostInstanceId,confirm:true};
 const confirmationResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/actions'&&
  response.request().method()==='POST'&&unwrap(response.request().postDataJSON()).action===prefix+'Confirm');
 await confirm.click();
 const confirmed=await confirmationResponse;assert.equal(confirmed.status(),200);
 assert.deepEqual(unwrap(confirmed.request().postDataJSON()).args,confirmationArgs);
 const confirmedBody=await confirmed.json();assert.equal(confirmedBody.accepted,true);
 const operation=confirmedBody.result;
 assert.match(operation.operationId,/^[a-f0-9]{32}$/);assert.equal(operation.previewId,preview.previewId);
 assert.equal(operation.status,'unknown');assert.equal(operation.reinstalled,false);assert.equal(operation.healthVerified,false);
 await expect(section()).toContainText('Repair outcome unknown');await expect(healthy()).toHaveCount(0);
 await expect(section().getByRole('button',{name:'Preview installation repair',exact:true})).toBeDisabled();
 await section().locator('[data-part="installation-repair-operation"]').scrollIntoViewIfNeeded();await capture('unknown-390');
 assert.equal((await checkpoint('one explicit gesture, accepted unknown receipt',1,2)).counters.artifactPreparations,1);
 assert.deepEqual(await checkStatus(),operation);
 assert.deepEqual(statuses().at(-1).args,{previewId:preview.previewId});
 phase='invalid status cannot settle';
 await rejected(prefix+'Status',{previewId:preview.previewId,operationId:operation.operationId});
 const stillUnknown=await observe();assert.equal(stillUnknown.operation.status,'unknown');
 assert.equal(stillUnknown.projection.operation.status,'unknown');await expect(healthy()).toHaveCount(0);
 phase='explicit duplicate idempotency probe';
 const duplicate=accepted(await post(prefix+'Confirm',confirmationArgs));
 assert.deepEqual(duplicate,operation);
 await checkpoint('explicit duplicate HTTP confirmation returns same operation, no second helper',1,3);
 phase='reload reads only';
 const statusStart=statuses().length;
 await page.reload();await expect(section()).toBeVisible();
 await expect.poll(()=>statuses().length).toBeGreaterThan(statusStart);
 for(const row of statuses().slice(statusStart))assert.deepEqual(row.args,{previewId:preview.previewId});
 await expect(section()).toContainText('Repair outcome unknown');
 const journal=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.installation-repair.v1')));
 assert.equal(journal.previewId,preview.previewId);assert.equal(journal.hostInstanceId,preview.hostInstanceId);
 await checkpoint('reload recovers exact preview without replay',1,3);
 await wireOperation(operation.operationId,'unknown');
 phase='current host health is not repair health';
 const health=await page.request.get(base+'/api/health');assert.equal(health.status(),200);
 assert.equal(Object.hasOwn(await health.json(),'installationRepair'),false,'Browser cookie does not disclose repair health identity');
 for(const headers of [{},{Authorization:'Bearer invalid-fixture-token'}]){
  const response=await fetch(base+'/api/health',{headers});assert.equal(response.status,200);
  const body=await response.json();
  for(const key of ['installationRepair','dataIdentity','revision','instanceId','runtime'])
   assert.equal(Object.hasOwn(body,key),false,'Anonymous/invalid bearer health omits '+key);
 }
 const ownerHealth=await page.request.get(base+'/api/health',{headers:{Authorization:token}});
 assert.equal(ownerHealth.status(),200);
 const ownerHealthBody=await ownerHealth.json();
 assert.deepEqual(ownerHealthBody.installationRepair,{protocol:1,operationId:operation.operationId});
 assert.equal(Number.isInteger(ownerHealthBody.installationRepair.protocol),true);
 assert.equal(JSON.stringify(ownerHealthBody).includes('INSTALLATION_REPAIR_FIXTURE_CREDENTIAL'),false);
 await expect(healthy()).toHaveCount(0);
 phase='reinstalled is not healthy';
 await advance('reinstalled',operation.operationId);
 await expect(section()).toContainText('Application reinstalled — service health not verified');
 await expect(healthy()).toHaveCount(0);
 const reinstalled=await checkpoint('reinstalled and running service without health are not success',1,3);
 assert.equal(reinstalled.operation.reinstalled,true);assert.equal(reinstalled.operation.healthVerified,false);
 await wireOperation(operation.operationId,'reinstalled');
 phase='ready requires exact healthy receipt';
 await advance('ready',operation.operationId);await expect(healthy()).toBeVisible();
 final=await checkpoint('only exact ready receipt is verified healthy',1,3);
 assert.equal(final.operation.operationId,operation.operationId);assert.equal(final.operation.healthVerified,true);
 assert.equal(final.operation.serviceState,'running');assert.equal(final.operation.reinstalled,true);
 assert.deepEqual(final.projection.operation,final.operation);
 await wireOperation(operation.operationId,'ready');
 assert.ok(await page.evaluate(()=>installationRepairStreams.some(s=>s.deltas>0)),'Actual SSE deltas observed');
 streamEvidence=await page.evaluate(()=>installationRepairStreams);
 await section().locator('[data-part="installation-repair-operation"]').scrollIntoViewIfNeeded();await capture('verified-390');
 await page.getByRole('button',{name:/^Close (settings|panel)$/i}).click();
 await showFixtureConversation(page);
 await expect(composer).toHaveText('Keep my unsent installation repair draft');
 await expect(page.getByText('Keep this saved installation repair conversation.',{exact:true})).toBeVisible();
 await checkpoint('return to original history and unsent draft',1,3);
 // Fetch the exact metadata through ordinary static HTTP too; loaded JS/CSS
 // response bytes below prove the actual browser used this bundled build.
 for(const name of ['build.json','client-component.json']){
  const response=await page.request.get(base+'/'+name);assert.equal(response.status(),200);
  const bytes=await response.body();assert.equal(sha(bytes),identity['amplifier_web/static/'+name]);
  servedAssets[name]={sha256:sha(bytes),bytes:bytes.length};
 }
 await Promise.all(assetReads);
 assert.ok(Object.keys(servedAssets).some(name=>/^assets\/.*\.js$/.test(name)));
 assert.ok(Object.keys(servedAssets).some(name=>/^assets\/.*\.css$/.test(name)));
 assert.ok(servedAssets['index.html']);
 for(const [name,binding] of Object.entries(servedAssets)){
  if(name!=='client-component.json')assert.deepEqual(binding,component.assets[name],name+' served bytes');
 }
 assert.deepEqual(errors,[]);noFallback();passed=true;phase='complete';
}catch(error){
 failure=error;try{if(page)await page.screenshot({path:resolve(output,'failure.png'),animations:'disabled'})}catch{}
}finally{
 try{
  await finishFixture({label:'Installation repair actual-host pairing',originalError:failure,
   close:[['Installation repair browser',async()=>{
    try{await browser?.close();cleanup.browser=browser?'closed':'not-started'}
    catch(error){cleanup.browser='failed';cleanup.errors.push(String(error));throw error}
   }]],
   stop:async()=>{
    try{if(owned){const result=await owned.stop();cleanup.fixture={state:'closed',...result}}}
    catch(error){cleanup.fixture='failed';cleanup.errors.push(String(error));throw error}
   }});
 }catch(error){failure??=error}
 try{await Promise.all(assetReads)}catch(error){failure??=error}
 if(identity)try{
  for(const [name,digest] of Object.entries(identity))assert.equal(sha(await readFile(resolve(root,name))),digest,name+' unchanged after cleanup');
 }catch(error){failure??=error}
 passed=passed&&!failure&&cleanup.errors.length===0;
 const receipt={passed,phase,failure:failure?String(failure.stack||failure):null,
  scope:'actual AppService, public HTTP schemas/dispatch, cookie auth, bundled static and real SSE UI',
  limits:['Synthetic binding/release/artifact/helper/install/service effects only; NOT native end-to-end',
   'No real package download, installer, service stop/start, host replacement or provider/worker execution',
   'Fixture narrows real client transport advertisement to HTTP/SSE; no browser request interception or fulfillment',
   'Reload recovery exercised; actual transport disconnect/reconnect and native platform parity not exercised',
   'Source pairing, not official-wheel or published-release acceptance'],
  identity,sourceSelection:source,build,clientComponent:component?{source:component.source,
   build:component.build,sharedInputs:component.sharedInputs,pairedServer:component.pairedServer}:null,
  servedAssets,streamEvidence,checkpoints,screenshots,actions,httpErrors,pageErrors:errors,observerErrors,
  finalCounters:final?.counters,operation:final?.operation,cleanup};
 await writeFile(resolve(output,'receipt.json'),JSON.stringify(receipt,null,2)+'\n');
}
if(failure)throw failure;
assert.equal(passed,true,'Pairing or awaited cleanup failed');
console.log('PASS actual-host installation repair pairing; native helper/install/service effects synthetic, no native end-to-end claim.');