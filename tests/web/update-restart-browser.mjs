// Built UI, real action receipt/rejection, no installation or restart effects.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,readFile,realpath,writeFile} from 'node:fs/promises';
import {isAbsolute,relative,resolve,sep} from 'node:path';
import {createHash} from 'node:crypto';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
const pair=process.env.AMPLIFIER_TEST_UPDATE_RESTART_PAIR==='1';
const fixturePath=fileURLToPath(new URL('../../tests/fixtures/update_restart_server.py',import.meta.url));
const hostEnv={...process.env},fixtureHash=createHash('sha256').update(await readFile(fixturePath)).digest('hex');
let assembly,wheelIdentity;
if(pair){
 for(const name of ['AMPLIFIER_TEST_PYTHON','AMPLIFIER_TEST_INSTALLED_PACKAGE','AMPLIFIER_TEST_HOST_PATH',
  'AMPLIFIER_TEST_ASSEMBLY_RECORD','AMPLIFIER_TEST_INSTALLED_IDENTITY','AMPLIFIER_TEST_SOURCE_ROOT','AMPLIFIER_TEST_PAIR_CWD'])
  assert.ok(process.env[name]&&isAbsolute(process.env[name]),'Installed pair requires absolute '+name);
 assert.equal(process.env.AMPLIFIER_TEST_CLIENT_REVISION,'cd7d636249575f2f5c3753afcb35fcc72d87dbc1');
 assert.match(process.env.AMPLIFIER_TEST_SERVER_REVISION||'',/^[a-f0-9]{40}$/);
 const checkout=await realpath(process.env.AMPLIFIER_TEST_SOURCE_ROOT);
 const cwd=await realpath(process.env.AMPLIFIER_TEST_PAIR_CWD),rel=relative(checkout,cwd);
 assert.ok(rel.startsWith('..'+sep)||isAbsolute(rel),'Installed fixture CWD must be outside checkout');
 assembly=JSON.parse(await readFile(process.env.AMPLIFIER_TEST_ASSEMBLY_RECORD,'utf8'));
 wheelIdentity=JSON.parse(await readFile(process.env.AMPLIFIER_TEST_INSTALLED_IDENTITY,'utf8'));
 assert.equal(assembly.server.revision,process.env.AMPLIFIER_TEST_SERVER_REVISION);
 for(const key of Object.keys(hostEnv))if(['PATH','PYTHONPATH','PYTHONHOME'].includes(key.toUpperCase()))delete hostEnv[key];
 hostEnv.PATH=process.env.AMPLIFIER_TEST_HOST_PATH;hostEnv.PYTHONDONTWRITEBYTECODE='1';
 hostEnv.AMPLIFIER_TEST_FIXTURE_SHA256=fixtureHash;
}
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),
 [...(pair?['-I','-B']:[]),'-u',fixturePath],{env:hostEnv,...(pair?{cwd:process.env.AMPLIFIER_TEST_PAIR_CWD}:{})});
const stopFixture=ownFixture(fixture,{label:'Update restart fixture'});
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/update-restart';
let browser,page,failure,finishServedEntry,log='';const result={status:'pending',realHTTP:true,activationEffects:false,installedPair:pair,fixtureSha256:fixtureHash};
fixture.stderr.on('data',data=>log=(log+data).slice(-8000));
try{
 await mkdir(out,{recursive:true});
 const boot=await new Promise((resolve,reject)=>{
  let pending='';
  const cleanup=()=>{clearTimeout(timer);fixture.off('error',failed);fixture.off('exit',exited);fixture.stdout.off('data',data)};
  const failed=error=>{cleanup();reject(error)},exited=code=>failed(Error(`Fixture exited ${code}: ${log}`));
  const data=chunk=>{
   pending+=chunk;
   for(let end;(end=pending.indexOf('\n'))>=0;){
    const line=pending.slice(0,end);pending=pending.slice(end+1);
    let row;try{row=JSON.parse(line)}catch{continue}
    if(row.url){cleanup();resolve(row);return}
   }
   if(pending.length>8000)failed(Error('Fixture readiness exceeded bound'));
  };
  const timer=setTimeout(()=>failed(Error(log||'Fixture timeout')),pair?60000:15000);
  fixture.once('error',failed);fixture.once('exit',exited);fixture.stdout.on('data',data);
 });
 assert.equal(boot.pid,fixture.pid);assert.match(boot.url,/^http:\/\/127\.0\.0\.1:[1-9]\d{0,4}$/);
 assert.equal(boot.package,pair?resolve(process.env.AMPLIFIER_TEST_INSTALLED_PACKAGE,'__init__.py'):fileURLToPath(new URL('../../amplifier_web/__init__.py',import.meta.url)));
 const checkProvenance=value=>{
  assert.equal(value.selectedServerRevision,assembly.server.revision);
  const stamped=Object.hasOwn(wheelIdentity.files,'amplifier_web/release-source.json');
  assert.equal(value.sourceStampPresent,stamped);
  if(stamped)assert.deepEqual(value.sourceStamp,{schema:1,repository:'microsoft/amplifier-unified',
   revision:assembly.server.revision,version:assembly.server.version});
  else assert.equal(value.sourceStamp,null,'Ordinary local wheel absence must be explicit, never fabricated');
 };
 if(pair){
  checkProvenance(boot.pair);
  assert.equal(boot.pair.fixtureSha256,fixtureHash);result.provenance=boot.pair;
 }
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const external=[],actions=[];
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')actions.push(request.postDataJSON())});
 await page.route('**/*',route=>{
  const url=new URL(route.request().url());
  if(['http:','https:'].includes(url.protocol)&&url.origin!==boot.url){external.push(url.origin);return route.abort()}
  return route.continue();
 });
 const evidence=async()=>{const response=await page.request.get(boot.url+'/fixture/update-restart');assert.ok(response.ok());return response.json()};
 if(pair){
  const verifiedBody=async(path,expected)=>{
   assert.ok(expected,'Assembly must bind '+path);
   const response=await page.request.get(boot.url+path,{maxRedirects:0});assert.equal(response.status(),200,path);
   assert.equal(response.url(),boot.url+path);
   const bytes=await response.body();assert.equal(bytes.length,expected.bytes,path);
   assert.equal(createHash('sha256').update(bytes).digest('hex'),expected.sha256,path);return bytes;
  };
  const component=JSON.parse((await verifiedBody('/client-component.json',assembly.client.component)).toString('utf8'));
  assert.deepEqual(component,assembly.client.manifest);
  assert.deepEqual(component.source,boot.pair.clientSource);
  const build=JSON.parse((await verifiedBody('/build.json',component.assets['build.json'])).toString('utf8'));
  assert.deepEqual(build,component.build);
  const html=(await verifiedBody('/',component.assets['index.html'])).toString('utf8');
  const entryPaths=[...html.matchAll(/(?:src|href)="(\/[^"]+\.(?:js|css))"/g)].map(match=>match[1]);
  assert.ok(entryPaths.some(path=>path.endsWith('.js')));assert.ok(entryPaths.some(path=>path.endsWith('.css')));
  for(const path of new Set(entryPaths))await verifiedBody(path,component.assets[path.slice(1)]);
  // Pin navigation's actual entry responses too, not merely a prior API fetch.
  const served=[],servedReads=[],servedErrors=[];
  page.on('response',response=>{
   const url=new URL(response.url());
   if(url.origin!==boot.url||!['/',...entryPaths].includes(url.pathname))return;
   servedReads.push((async()=>{
    assert.equal(response.status(),200);
    const body=await response.body(),expected=component.assets[url.pathname==='/'?'index.html':url.pathname.slice(1)];
    assert.equal(body.length,expected.bytes);assert.equal(createHash('sha256').update(body).digest('hex'),expected.sha256);
    served.push(url.pathname);
   })().catch(error=>servedErrors.push(String(error))));
  });
  result.servedEntry={paths:entryPaths,build,verified:true};
  finishServedEntry=async()=>{
   await Promise.all(servedReads);
   assert.deepEqual(servedErrors,[],'Actual browser entry responses must match pinned bytes');
   for(const path of ['/',...entryPaths])assert.ok(served.includes(path),'Browser must load pinned entry '+path);
   result.servedEntry.browserResponses=[...new Set(served)];
  };
 }
 await page.goto(boot.url);await page.waitForSelector('#amp-one');await openSettingsPage(page,'updates');
 const blockers=page.locator('[data-part="update-blockers"]'),restart=page.locator('.a-update-force-restart'),entry=restart.locator('[data-action="updates.forceRestart"]');
 const overview=page.locator('[data-part="application-update"]').first();
 const flatRows=async realRows=>{
  assert.ok(realRows.length,'A positive host row prevents vacuous flat-list coverage');
  await expect(blockers.locator(':scope>summary')).toHaveText('View work delaying the update');
  await expect(blockers.locator(':scope>ul>li')).toHaveText(realRows.map(item=>item.label+(item.sessionId?' — '+(item.title||'Open conversation'):'')));
  await expect(blockers.locator('h4,section,ul ul,details')).toHaveCount(0);
  await expect(blockers).not.toContainText('Restarting does not recover held settings');
 };
 if(pair){
  const checkpoints=[];
  let initialOwnerHash;
  const unchanged=observed=>{
   const value=observed.pair;assert.ok(value);
   assert.equal(value.historicalIds.length,4);assert.equal(new Set(value.historicalIds).size,4);
   for(const owner of value.historicalStates){
    assert.equal(owner.status,'stopped');assert.equal(owner.configurationBusy,true);assert.equal(owner.configurationPending,true);
    assert.equal(owner.configurationPendingRevision,23);assert.equal(owner.pendingConfiguration.phase,'error');
    assert.equal(owner.pendingConfiguration.effect,'saved_not_mounted');
   }
   assert.equal(value.historyAndConfigurationUnchanged,true);assert.equal(value.savedOwnerFilesUnchanged,true);
   assert.equal(value.committedOwnerReadbacksUnchanged,true);assert.equal(value.committedOwnerCount,4);
   assert.equal(Object.keys(value.savedFilesPerOwner).length,4);
   assert.ok(Object.values(value.savedFilesPerOwner).every(count=>count>0));
   assert.match(value.committedOwnerSha256,/^[a-f0-9]{64}$/);
   assert.equal(value.committedOwnerSha256,initialOwnerHash);
   assert.deepEqual(value.runtimeCalls,[]);assert.equal(value.componentBusy,true);
   assert.equal(observed.calls.length,0);assert.equal(value.provenance.wheelIdentitySha256,boot.pair.wheelIdentitySha256);
   checkProvenance(value.provenance);
   checkpoints.push(value);
  };
  const initial=await evidence();initialOwnerHash=initial.pair.committedOwnerSha256;unchanged(initial);
  assert.equal(initial.pair.mode,'historical');assert.equal(initial.pair.appBusy,false);
  assert.equal(initial.pair.activationGate.blocked,false);assert.deepEqual(initial.pair.blockers,[]);
  assert.deepEqual(initial.pair.activationGate.configurationReasonCounts,{'idle-configuration-hold':4});
  // Positive mounted Updates/readback first; historical rows do not create UI blockers.
  await expect(overview).toBeVisible();await expect(entry).toBeEnabled();
  await expect(blockers).toHaveCount(0);
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().updates.blockers)).toEqual([]);
  await page.screenshot({path:out+'/four-stopped-clear.png'});
  const transition=async mode=>{
   const response=await page.request.post(boot.url+'/fixture/update-restart',{data:{mode}});
   assert.equal(response.status(),200);const value=await response.json();unchanged(value);return value;
  };
  const work=await transition('active');
  assert.equal(work.pair.appBusy,true);assert.equal(work.pair.activationGate.blocked,true);
  assert.ok(work.pair.blockers.some(row=>row.kind==='conversation'&&row.sessionId===work.pair.activeId));
  assert.ok(work.pair.blockers.every(row=>!work.pair.historicalIds.includes(row.sessionId)));
  await expect(overview).toContainText('Waiting for work to finish');
  await blockers.locator('summary').click();await flatRows(work.pair.blockers);
  await page.screenshot({path:out+'/genuine-work-flat.png'});
  await blockers.locator('summary').click();
  const unknown=await transition('unknown');
  assert.equal(unknown.currentStatus.status,'action-required');assert.equal(unknown.currentStatus.title,'Update needs attention');
  assert.ok(unknown.pair.blockers.some(row=>row.kind==='checkout-handoff'));
  assert.ok(unknown.pair.blockers.every(row=>!unknown.pair.historicalIds.includes(row.sessionId)));
  result.heldConfiguration={checkpoints,realAppService:true,realApplicationGate:true,syntheticRuntimeObservation:true,
   fourStoppedNotWork:true,genuineWorkVisible:true,unknownAttention:true,installationAndRestartEffects:0};
 }
 await expect(entry).toHaveText('Interrupt work and restart');await expect(entry).toBeVisible();
 assert.equal(await entry.evaluate(button=>!!button.closest('[data-part="update-blockers"]')),false);
 await expect(restart).toContainText('Restart into prepared Amplifier 99.0.0.');
 await expect(restart).toContainText('Amplifier 99.0.1 is available, but this action uses the prepared version above.');
 await expect(restart).toContainText('will not be replayed automatically');
 await expect(entry).toHaveAccessibleDescription(/Running chats, workers, tools, and calls will be interrupted/);
 await expect(page.getByRole('alertdialog',{name:'Confirm update restart'})).toHaveCount(0);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().updates.currentStatus.status)).toBe('action-required');
 await expect(overview).toContainText('Update needs attention');
 await blockers.locator('summary').click();await flatRows((await evidence()).blockers);
 await expect(restart).toContainText('You can wait for work to finish instead.');
 await blockers.locator('summary').click();assert.equal((await evidence()).calls.length,0,'Inspect-only disclosure has no restart effect');
 // A real public stale-target request must be refused before activation.
 const oldTarget=(await evidence()).target;
 assert.ok((await page.request.post(boot.url+'/fixture/update-restart')).ok());
 const stale=await page.request.post(boot.url+'/api/actions',{data:{id:'stale-prepared-target',action:'updates.forceRestart',args:{...oldTarget,confirm:true}}});
 assert.equal(stale.status(),409);assert.match((await stale.json()).error,/staged update or host changed/);
 assert.equal((await evidence()).calls.length,0);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().updates.forceRestartTarget.attemptId)).toBe('qualified-two');
 for(const width of [1280,390]){
  await page.setViewportSize({width,height:900});
  await expect(entry).toBeEnabled();await expect(entry).toHaveText('Interrupt work and restart');
  const target=(await evidence()).target;
  let release;const held=new Promise(resolve=>release=resolve),requests=[],active=new Set(),routeErrors=[];
  const hold=route=>{
   // Own every handler's completion and faults. Playwright's event emitter does
   // not await rejected route callbacks on our behalf.
   const handling=(async()=>{const body=route.request().postDataJSON();if(body?.action==='updates.forceRestart'){requests.push(body);await held}await route.continue()})();
   const owned=handling.catch(error=>routeErrors.push(error)).finally(()=>active.delete(owned));
   active.add(owned);return owned;
  };
  await page.route('**/api/actions',hold);
  let responseValue,responseError;
  const response=page.waitForResponse(r=>r.url().endsWith('/api/actions')&&r.request().method()==='POST'&&r.request().postDataJSON()?.action==='updates.forceRestart').then(value=>{responseValue=value},error=>{responseError=error});
  try{
   // Same-task repeated events exercise the synchronous ref fence, not only
   // React's subsequently committed disabled state. Narrow view uses a real click.
   if(width===1280)await entry.evaluate(button=>{button.click();button.click();button.click()});else await entry.click();
   await expect.poll(()=>requests.length).toBe(1);await expect(entry).toBeDisabled();
  }finally{
   release();
   // Removing a still-held handler can auto-handle its Route before continue().
   // Drain first, then unregister; handler faults are awaited failures.
   await expect.poll(()=>active.size).toBe(0);await page.unroute('**/api/actions',hold);
   if(routeErrors.length)throw new AggregateError(routeErrors,'Update restart route handling failed');
  }
  await response;if(responseError)throw responseError;
  const receipt=await responseValue.json();assert.equal(receipt.accepted,true);assert.ok(receipt.requestId);
  await expect(restart.getByRole('alert')).toContainText('update operation');await expect(entry).toBeEnabled();
  const observed=await evidence();const last=observed.calls.at(-1);
  assert.deepEqual(last.args,{...target,confirm:true});assert.equal(last.requestId,receipt.requestId);assert.equal(observed.unknown,true);
  assert.equal(requests[0].id,receipt.requestId);assert.deepEqual(observed.result,{requestId:receipt.requestId,status:'rejected',detail:'An update operation is already running. Review it before confirming again.'});
  assert.ok(await restart.evaluate(element=>element.scrollWidth<=element.clientWidth+1),'Restart warning and action fit the viewport');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No page overflow');
  await page.screenshot({path:`${out}/confirmed-${width}.png`});
 }
 assert.equal((await evidence()).calls.length,2);assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 assert.deepEqual(actions.filter(row=>['updates.install','updates.check','conversation.send','conversation.retry','session.warm'].includes(row.action)),[]);
 if(pair){await finishServedEntry();result.finalPair=(await evidence()).pair}
 result.status='passed';result.checks={oneActionPlacement:true,inspectNoEffect:true,preparedNotLatest:true,synchronousBusyLatch:true,staleTargetBackendRefusal:true,exactReceipt:true,asyncRejectionRecovery:true,desktopAndMobile:true,unknownFencePreserved:true};
}catch(error){failure=error;result.status='failed';result.error=String(error);if(page)try{await page.screenshot({path:out+'/failed.png'})}catch(screenshotError){console.error('Failure screenshot:',screenshotError)}throw error}
finally{
 try{
  await finishFixture({label:'Update restart fixture',originalError:failure,
   close:[['Update restart browser',()=>browser?.close()]],
   stop:async()=>{result.fixtureExit=await stopFixture()}});
 }catch(error){result.status='failed';result.cleanupError=String(error);throw error}
 finally{
  result.fixtureLog=log;await writeFile(out+'/result.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }
}
