// Actual production TLS eligibility and UI; NOT native repair/reconciliation.
// Run only in the parent's prepared owned environment, from the Unified root:
// AMPLIFIER_TEST_PYTHON=/owned/python node tests/web/installation-repair-tls-browser.mjs
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
const output=resolve(process.env.AMPLIFIER_TEST_ARTIFACTS||root+'output/browser-checks/installation-repair-tls');
const prefix='maintenance.installationRepair',token='Bearer fixture-browser-control-token';
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const json=async name=>JSON.parse(await readFile(resolve(root,name),'utf8'));
const actions=[],pageErrors=[],observerErrors=[],externalRequests=[],checkpoints=[],screenshots=[],assetReads=[];
const servedAssets={},cleanup={browser:'not-started',fixture:'not-started'};
let owned,browser,page,base,identity,source,component,build,failure,phase='setup',passed=false;
const section=()=>page.locator('[data-part="installation-repair"]');
const unwrap=body=>body.action==='shell.command'?(body.args.command||body.args):body;
async function observe(){
 const response=await page.request.get(base+'/fixture/repair-tls',{headers:{Authorization:token}});
 assert.equal(response.status(),200);return response.json();
}
async function checkpoint(name,{supported,previews,releaseReads,healthReads}){
 const state=await observe();
 assert.equal(state.preview?.supported,supported);
 assert.equal(state.previewFiles,previews);
 assert.equal(state.admissionsCreated,false);
 assert.equal(state.operationFiles,0);assert.equal(state.operation,null);
 assert.deepEqual(state.admissions,{schema:1,current:null,previews:{}});
 for(const key of ['confirmPosts','artifactPreparations','helpers','authenticatedHTTPHealth'])
  assert.equal(state.counters[key],0,name+': '+key);
 assert.equal(state.counters.authenticatedHTTPSHealth,healthReads);
 assert.equal(state.counters.releaseReads,releaseReads);assert.equal(state.counters.tagReads,releaseReads);
 assert.deepEqual(state.counters.runtimeCalls,[]);
 assert.deepEqual(actions.filter(row=>row.action.startsWith(prefix)&&row.action!==prefix+'Preview'&&row.action!==prefix+'Status'),[]);
 assert.deepEqual(actions.filter(row=>['maintenance.repair','maintenance.reset','updates.install',
  'updates.forceRestart','conversation.send','conversation.warm','runtime.start','runtime.stop'].includes(row.action)),[]);
 assert.deepEqual(externalRequests,[]);assert.deepEqual(observerErrors,[]);
 checkpoints.push({name,state});return state;
}
async function preview(){
 const waiting=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/actions'&&
  response.request().method()==='POST'&&unwrap(response.request().postDataJSON()).action===prefix+'Preview');
 await section().getByRole('button',{name:'Preview installation repair',exact:true}).click();
 const response=await waiting;assert.equal(response.status(),200);
 const body=await response.json();assert.equal(body.accepted,true);return body.result;
}
async function capture(name){
 const path=resolve(output,name+'.png');
 await section().scrollIntoViewIfNeeded();await page.screenshot({path,animations:'disabled'});
 screenshots.push(path);
}

await mkdir(output,{recursive:true});
try{
 source=await json('components/web-client.source.json');
 if(process.env.AMPLIFIER_TEST_CLIENT_REVISION)assert.equal(source.source.revision,process.env.AMPLIFIER_TEST_CLIENT_REVISION);
 assert.deepEqual(await json('.build/web-client/.client-source.json'),source);
 component=await json('amplifier_web/static/client-component.json');
 build=await json('amplifier_web/static/build.json');
 assert.deepEqual(component.source,source.source);assert.deepEqual(component.build,build);
 owned=await startFixture({command:fixturePython(root+'.venv/bin/python'),
  args:['-u',root+'tests/fixtures/installation_repair_tls_server.py'],
  env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},tag:'installation-repair-tls',name:'Repair TLS eligibility'});
 base=owned.base;cleanup.fixture='running';
 browser=await chromium.launch({headless:true,
  ...(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}:{}),
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 cleanup.browser='running';
 const context=await browser.newContext({viewport:{width:1280,height:1000}});
 // No mocked/fulfilled app traffic. Fail closed on attempted external egress.
 await context.route('**/*',async route=>{
  const url=new URL(route.request().url());
  if(url.origin===base)return route.continue();
  externalRequests.push(url.origin);return route.abort('blockedbyclient');
 });
 assert.equal((await context.request.get(base+'/api/state')).status(),401);
 assert.equal((await context.request.get(base+'/api/state',{headers:{Authorization:'Bearer invalid'}})).status(),401);
 const cookie=await context.request.post(base+'/fixture/repair-tls-cookie',{headers:{Authorization:token}});
 assert.equal(cookie.status(),200);
 page=await context.newPage();page.setDefaultTimeout(15000);
 page.on('pageerror',error=>pageErrors.push(error.message));
 page.on('request',request=>{
  if(new URL(request.url()).pathname!=='/api/actions'||request.method()!=='POST')return;
  try{actions.push(unwrap(request.postDataJSON()))}catch(error){observerErrors.push(String(error))}
 });
 page.on('response',response=>{
  const url=new URL(response.url()),name=url.pathname==='/'?'index.html':decodeURIComponent(url.pathname.slice(1));
  if(url.origin!==base||!response.ok()||!Object.hasOwn(component.assets,name))return;
  assetReads.push(response.body().then(bytes=>{servedAssets[name]={sha256:sha(bytes),bytes:bytes.length}})
   .catch(error=>observerErrors.push(String(error))));
 });
 await page.goto(base+'/');await showFixtureConversation(page);
 const initial=await observe();identity=initial.identity;
 for(const [name,digest] of Object.entries(identity))assert.equal(sha(await readFile(resolve(root,name))),digest,name);
 assert.equal(initial.tls,'none');assert.equal(initial.pin,null);assert.equal(initial.health,null);
 assert.deepEqual(initial.probeTargets,[base+'/api/health']);assert.equal(initial.counters.eligibilityReads,0);
 await openSettingsPage(page,'repair',{activate:(_label,control,options)=>control.click(options)});
 await expect(section()).toBeVisible();
 phase='DEFAULT_SERVER tls=none unsupported';
 const unsupported=await preview();
 assert.equal(unsupported.supported,false);assert.match(unsupported.reason,/pinned HTTPS loopback listener/);
 assert.match(unsupported.reason,/Configure TLS before repair; no control credential was sent/);
 await expect(section().getByRole('status').filter({hasText:'Configure TLS before repair'})).toBeVisible();
 await expect(section()).toContainText(unsupported.reason);
 const confirm=section().getByRole('button',{name:'Confirm installation repair',exact:true});
 await expect(confirm).toHaveCount(0);
 const negative=await checkpoint('real default TLS filter refuses before release/admission',{
  supported:false,previews:0,releaseReads:0,healthReads:0});
 assert.equal(negative.counters.eligibilityReads,1);
 await capture('default-no-tls-unsupported');
 phase='configured HTTPS actual pinned health';
 // One-shot fixture setup only, not a production action or synthetic health.
 const configured=await page.request.post(base+'/fixture/repair-tls-enable',{headers:{Authorization:token}});
 assert.equal(configured.status(),200);const health=await configured.json();
 assert.equal(health.wrongPinRejected,true);assert.equal(health.hostInstanceId,initial.hostInstanceId);
 assert.equal(health.operationId,null);assert.match(health.url,/^https:\/\/127\.0\.0\.1:\d+\/api\/health$/);
 assert.match(health.pin,/^[a-f0-9]{64}$/);
 phase='configured HTTPS eligible preview';
 const eligible=await preview();
 assert.equal(eligible.supported,true);assert.match(eligible.previewId,/^[a-f0-9]{32}$/);
 assert.equal(eligible.hostInstanceId,initial.hostInstanceId);assert.equal(eligible.dataDir,initial.dataDir);
 assert.equal(eligible.scope,'application-installation');
 assert.deepEqual(eligible.target,{version:'99.0.0',revision:'a'.repeat(40),
  source:'https://github.com/microsoft/amplifier-unified/releases/download/v99.0.0/amplifier_unified-99.0.0-py3-none-any.whl'});
 await expect(section().getByText('Review this host and target',{exact:true})).toBeVisible();
 await expect(section()).toContainText('Application installation only');
 await expect(section()).toContainText(initial.hostInstanceId);await expect(section()).toContainText(initial.dataDir);
 await expect(confirm).toBeDisabled();await expect(section().getByRole('checkbox')).not.toBeChecked();
 const positive=await checkpoint('real TLS eligibility permits preview, never Confirm',{
  supported:true,previews:1,releaseReads:1,healthReads:1});
 assert.equal(positive.tls,'ca');assert.equal(positive.pin,health.pin);
 assert.deepEqual(positive.probeTargets,[health.url]);assert.deepEqual(positive.health,health);
 assert.equal(positive.counters.eligibilityReads,3,'negative preview, real positive binding, positive preview');
 assert.equal(actions.filter(row=>row.action===prefix+'Preview').length,2);
 assert.equal(actions.filter(row=>row.action===prefix+'Confirm').length,0);
 await capture('pinned-https-eligible-preview');
 await Promise.all(assetReads);
 assert.ok(servedAssets['index.html']);
 assert.ok(Object.keys(servedAssets).some(name=>/^assets\/.*\.js$/.test(name)));
 assert.ok(Object.keys(servedAssets).some(name=>/^assets\/.*\.css$/.test(name)));
 for(const [name,record] of Object.entries(servedAssets))assert.deepEqual(record,component.assets[name],name);
 assert.deepEqual(pageErrors,[]);assert.deepEqual(observerErrors,[]);passed=true;phase='complete';
}catch(error){
 failure=error;
 try{if(page)await page.screenshot({path:resolve(output,'failure.png'),animations:'disabled'})}catch{}
}finally{
 try{
  await finishFixture({label:'TLS eligibility',originalError:failure,
   close:[['TLS browser',async()=>{
    try{await browser?.close();cleanup.browser=browser?'closed':'not-started'}
    catch(error){cleanup.browser='failed';throw error}
   }]],stop:async()=>{
    try{if(owned)cleanup.fixture={state:'closed',...await owned.stop()}}
    catch(error){cleanup.fixture='failed';throw error}
   }});
 }catch(error){failure??=error}
 await Promise.all(assetReads);
 if(identity)try{
  for(const [name,digest] of Object.entries(identity))assert.equal(sha(await readFile(resolve(root,name))),digest,name+' unchanged');
 }catch(error){failure??=error}
 passed=passed&&!failure&&cleanup.browser==='closed'&&cleanup.fixture?.state==='closed';
 await writeFile(resolve(output,'receipt.json'),JSON.stringify({
  passed,phase,failure:failure?String(failure.stack||failure):null,identity,
  scope:'Real production binding/probe_targets, AppService auth/dispatcher/UI and pinned HTTPS health; no Confirm',
  limits:checkpoints[0]?.state.limits||['No native/recovery/readiness acceptance; inspect fixture scope.'],
  sourceSelection:source,build,servedAssets,actions,checkpoints,screenshots,pageErrors,observerErrors,externalRequests,cleanup,
 },null,2)+'\n');
}
if(failure)throw failure;
assert.equal(passed,true,'TLS eligibility assertions or cleanup failed');
console.log('PASS bounded real TLS eligibility/UI/health; synthetic ownership/release only, no Confirm or native recovery claim.');