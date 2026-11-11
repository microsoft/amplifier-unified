import {fixturePython} from './fixture-python.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFile,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
const output=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!output)throw Error('Owned evidence folder required');
const installed=process.env.AMPLIFIER_TEST_INSTALLED_PACKAGE;
const hostEnv={...process.env,PYTHONDONTWRITEBYTECODE:'1'};
delete hostEnv.PYTHONPATH;delete hostEnv.PYTHONHOME;
if(installed)hostEnv.PATH=process.env.AMPLIFIER_TEST_HOST_PATH;
const child=spawn(fixturePython(),['-I','-B','-u',fileURLToPath(new URL('../../tests/fixtures/update_current_health_server.py',import.meta.url))],{env:hostEnv});
const stop=ownFixture(child,{label:'Update current health fixture'});
let log='',browser,failure;child.stderr.on('data',x=>log=(log+x).slice(-8000));
const result={status:'running',paidCalls:0,liveInstallCalls:0,syntheticRecordedGraph:true,capturedLegacyAttempts:true};
try{
 const boot=await new Promise((resolve,reject)=>{
  let pending='';
  const cleanup=()=>{clearTimeout(timer);child.off('error',failed);child.off('exit',exited);child.stdout.off('data',data)};
  const failed=error=>{cleanup();reject(error)};
  const exited=(code,signal)=>failed(Error('fixture exit '+(code??signal)+log));
  const data=chunk=>{
   pending+=chunk;
   for(let end;(end=pending.indexOf('\n'))>=0;){
    const line=pending.slice(0,end);pending=pending.slice(end+1);
    let value;try{value=JSON.parse(line)}catch{continue}
    if(value.url){cleanup();resolve(value);return}
   }
   if(pending.length>8000)failed(Error('fixture readiness output exceeded its bound'));
  };
  const timer=setTimeout(()=>failed(Error(log||'boot timeout')),60000);
  child.once('error',failed);child.once('exit',exited);child.stdout.on('data',data);
 });
 browser=await chromium.launch({headless:true,channel:'chromium',args:['--disable-gpu','--no-zygote']});
 const page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 if(installed){
  const record=JSON.parse(await readFile(process.env.AMPLIFIER_TEST_ASSEMBLY_RECORD,'utf8'));
  const verifiedBody=async(path,expected)=>{
   assert.ok(expected,'Assembly must bind '+path);
   const response=await page.request.get(boot.url+path);assert.ok(response.ok(),path);
   const bytes=await response.body();assert.equal(bytes.length,expected.bytes,path);
   assert.equal(createHash('sha256').update(bytes).digest('hex'),expected.sha256,path);
   return bytes;
  };
  const component=JSON.parse((await verifiedBody('/client-component.json',record.client.component)).toString('utf8'));
  assert.deepEqual(component,record.client.manifest);
  assert.equal(component.source.revision,process.env.AMPLIFIER_TEST_CLIENT_REVISION);
  const identity=JSON.parse((await verifiedBody('/build.json',component.assets['build.json'])).toString('utf8'));
  assert.deepEqual(identity,component.build);
  const html=(await verifiedBody('/',component.assets['index.html'])).toString('utf8');
  const paths=[...html.matchAll(/(?:src|href)="(\/[^"]+\.(?:js|css))"/g)].map(x=>x[1]);
  assert.ok(paths.some(path=>path.endsWith('.js')),'Served index must reference JavaScript');
  assert.ok(paths.some(path=>path.endsWith('.css')),'Served index must reference CSS');
  for(const path of new Set(paths))await verifiedBody(path,component.assets[path.slice(1)]);
  result.servedEntrypointAssetHashes=true;result.selectedClient=component.source;result.clientBuild=component.build;
 }
 const errors=result.pageErrors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(boot.url);await page.waitForSelector('#amp-one');await openSettingsPage(page,'updates');
 const panel=page.locator('[data-part="updates"]'),status=page.locator('[data-part="application-update"]').first();
 await expect(status).toContainText('You’re up to date');await expect(panel.locator('.a-update-current-action')).toHaveCount(0);
 assert.equal((await (await page.request.get(boot.url+'/fixture/current-update')).json()).originalHistoryUnchanged,true);
 await page.screenshot({path:output+'/current-desktop.png'});
 await page.setViewportSize({width:390,height:844});await expect(status).toContainText('You’re up to date');await page.screenshot({path:output+'/current-mobile.png'});
 await panel.locator('[data-part="ecosystem-update-details"]>summary').click();await panel.locator('[data-part="update-attempt-history"]>summary').click();await expect(panel).toContainText('No earlier attempt is rewritten as successful.');await expect(panel).toContainText('amplifier-foundation');
 await page.reload();await page.waitForSelector('#amp-one');if(!await status.isVisible())await openSettingsPage(page,'updates');await expect(status).toContainText('You’re up to date');
 const transition=async mode=>{const response=await page.request.post(boot.url+'/fixture/current-update',{data:{mode}});assert.ok(response.ok());return response.json()};
 const failed=await transition('failure');assert.equal(failed.historyPreserved,true);await expect(status).toContainText('Component source needs review');await expect(panel.getByRole('button',{name:'Review source details'})).toBeVisible();await expect(panel).not.toContainText('Open Updates to review');
 await expect(status.locator('[data-part="confirmed-application-result"]')).toContainText('Application updated successfully');
 await expect(status).toContainText('Unchanged automatic attempts are paused');
 await expect(panel.locator('.a-update-current-action')).toContainText('amplifier-foundation');
 const advanced=panel.locator('[data-part="ecosystem-update-details"]'),review=panel.getByRole('button',{name:'Review source details',exact:true});
 for(const width of [390,1280]){
  await page.setViewportSize({width,height:950});
  for(let repeat=0;repeat<2;repeat++){
   if(await advanced.getAttribute('open')!==null)await advanced.locator(':scope > summary').click();
   await expect(advanced).toHaveJSProperty('open',false);
   await review.click();
   await expect(advanced).toHaveJSProperty('open',true);
   await expect(advanced.locator(':scope > summary')).toBeFocused();
   // This fixture's conflict is in the recorded graph receipt, not an invented
   // cached-source row. The action must reveal that existing evidence.
   await expect(advanced.locator('[data-part="update-diagnostics"] > .a-update-failure p')).toBeVisible();
   await expect(advanced.locator('[data-part="update-diagnostics"] > .a-update-failure p')).toContainText('A protected package source declaration conflicts');
   await expect(panel.getByText('Source details opened below.',{exact:true})).toBeVisible();
  }
 }
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:output+'/blocked-mobile.png'});await page.setViewportSize({width:1280,height:950});await page.screenshot({path:output+'/blocked-desktop.png'});
 await transition('uncertain');await expect(status).toContainText('Installation needs verification');
 await transition('current');await expect(status).toContainText('You’re up to date');
 const summary=panel.locator('[data-part="update-release-summary"]');
 await transition('notes-ready');
 await expect(summary).toContainText('Exact installed-version notes arrived.');
 // Ordinary demand supplies notes over real HTTP/SSE. This does not diagnose
 // the historical screenshot's missing data; missing/error states are injected.
 await page.reload();await page.waitForSelector('#amp-one');if(!await status.isVisible())await openSettingsPage(page,'updates');
 await expect(summary).toContainText('Exact installed-version notes arrived.');
 await transition('notes-missing');
 await expect(summary).toContainText('Loading release notes');
 await expect(summary).toContainText('Release notes have not arrived',{timeout:8000});
 await expect(summary.getByRole('link',{name:/View published release/})).toHaveAttribute('href',/\/v0\.60\.0$/);
 await page.screenshot({path:output+'/notes-not-arrived.png'});
 await transition('notes-next');await expect(summary).toContainText('Loading release notes');
 await transition('notes-ready');await expect(summary).toContainText('Exact installed-version notes arrived.');
 await expect(summary).toContainText('0.60.1');
 await transition('notes-empty');await expect(summary).toContainText('Release notes for this version are unavailable.');
 await transition('notes-error');await expect(summary).toContainText('Release notes could not be refreshed.');
 await expect(summary).not.toContainText('Loading');
 // Malformed wire values are covered at the consumer by component tests.
 // Canonical server state does not admit that shape; do not corrupt its
 // attention producer merely to inject a browser-only negative.
 await transition('notes-ready');await expect(summary).toContainText('Exact installed-version notes arrived.');
 await page.screenshot({path:output+'/notes-arrived.png'});
 await transition('partial');
 const outcomes=panel.locator('[data-part="component-update-results"]');
 await expect(status).toContainText('Some components kept their working versions');
 await expect(outcomes).toContainText('Fixture A');
 await expect(outcomes).toContainText('Failed — working version retained');
 await expect(outcomes).toContainText('Fixture B');
 await expect(outcomes).toContainText('Updated');
 await expect(outcomes).toContainText('Fixture C');
 await expect(outcomes).toContainText('Dependency-held — working version retained');
 for(const width of [390,1280]){
  await page.setViewportSize({width,height:950});
  await expect(status).not.toContainText('You’re up to date');
  await page.screenshot({path:output+`/partial-${width}.png`});
 }
 await page.reload();await page.waitForSelector('#amp-one');
 if(!await status.isVisible())await openSettingsPage(page,'updates');
 await expect(outcomes).toContainText('Dependency-held — working version retained');
 // Real host Check/projection/Install owner, not a synthetic client peer.
 const retryButton=panel.getByRole('button',{name:'Retry installation',exact:true});
 const observeRetry=async()=>{
  const response=await page.request.get(boot.url+'/fixture/current-update');assert.ok(response.ok());
  return (await response.json()).retry;
 };
 result.retryTransitions=[];
 for(const mode of ['known','unknown','missing','stale','live-configuration']){
  const receipt=await transition('retry-'+mode);
  result.retryTransitions.push({mode,...receipt});
  assert.equal(receipt.status.action,mode==='known'?'install':receipt.status.action,
   'The real host must certify a known retained retry before checking its button');
  if(mode==='known')await expect(retryButton).toBeVisible();
  else await expect(retryButton).toHaveCount(0);
  const observation=await observeRetry();
  assert.equal(observation.available,0);
  if(mode==='known')assert.equal(observation.status.action,'install');
  else assert.notEqual(observation.status.action,'install');
  assert.equal(observation.liveSelectionChanged,mode==='live-configuration');
  assert.equal(observation.candidateCount,0);assert.equal(observation.fetches,0);
  assert.equal(observation.prepareCalls,0);assert.equal(observation.installActions,0);
 }
 await transition('retry-known');
 for(const width of [1280,390]){
  await page.setViewportSize({width,height:950});await expect(retryButton).toBeVisible();await expect(retryButton).toBeEnabled();
  await page.screenshot({path:output+`/retained-retry-${width}.png`});
 }
 const beforeCheck=await observeRetry();
 await panel.locator('[data-action="updates.check"]').click();
 await expect.poll(async()=>{
  const observation=await observeRetry();
  return {actions:observation.checkActions-beforeCheck.checkActions,
   completed:observation.checkActionsCompleted-beforeCheck.checkActionsCompleted,
   inventories:observation.checks-beforeCheck.checks,done:observation.checkTaskDone,
   phase:observation.phase,stage:observation.sequenceStage,locked:observation.ownerLocked};
 },{timeout:30000}).toEqual({actions:1,completed:1,inventories:1,done:true,phase:'checked',stage:'complete',locked:false});
 await expect(retryButton).toBeEnabled();
 let observation=await observeRetry();
 assert.equal(observation.available,0);assert.equal(observation.status.action,'install');
 assert.equal(observation.candidateCount,0);assert.equal(observation.installActions,0);
 assert.equal(observation.fetches,0);assert.equal(observation.prepareCalls,0);
 await page.reload();await page.waitForSelector('#amp-one');await openSettingsPage(page,'updates');
 await expect(retryButton).toBeVisible();
 observation=await observeRetry();
 assert.equal(observation.candidateCount,0);assert.equal(observation.installActions,0);
 assert.equal(observation.fetches,0);assert.equal(observation.prepareCalls,0);
 assert.equal(observation.checkActionsCompleted,beforeCheck.checkActionsCompleted+1);
 await retryButton.click();
 await expect.poll(async()=>{
  const observation=await observeRetry();
  return {actions:observation.installActions,completed:observation.installActionsCompleted,
   stops:observation.prepareCalls,phase:observation.phase,locked:observation.ownerLocked,
   failure:observation.failure};
 },{timeout:30000}).toEqual({actions:1,completed:1,stops:1,phase:'error',locked:false,
  failure:{phase:'ecosystem-validation',status:'failed',errorType:'RuntimeError'}});
 observation=await observeRetry();
 assert.equal(observation.candidateCount,1);assert.match(observation.controlledStopCandidate,/^[a-f0-9]{32}$/);
 assert.equal(observation.fetches,1);assert.equal(observation.activePointerUnchanged,true);
 assert.equal(observation.servingSourceUnchanged,true);
 assert.equal(observation.pendingRelease,null);assert.equal(observation.candidateValidated,false);
 Object.assign(result,{retainedOnlyCheckNoPreparation:true,explicitRetryVisibleDesktopMobile:true,
  unknownMissingStaleNoRetry:true,liveSelectionChangeNoRetry:true,checkOwnerCompleted:true,
  controlledInstallTerminal:true,oneGuardedCandidate:true,oneSyntheticAcquisition:true,
  runtimeQualificationNotRun:true,installed:!!installed});
 assert.deepEqual(errors,[]);Object.assign(result,{package:boot.package,currentDesktop:true,currentMobile:true,historyRetained:true,reload:true,currentFailureHasAction:true,sourceActionReopensAndReveals:true,uncertainPreserved:true,notesDemand:true,notesMissingBounded:true,notesLateArrival:true,notesVersionSwitch:true,notesEmptyAndError:true,partialResults:true,partialReload:true});
} catch(error){failure=error;Object.assign(result,{status:'failed',error:String(error),fixtureLog:log});throw error}
finally{
 try{
  await finishFixture({label:'Update current health',close:[['Browser',()=>browser?.close()]],stop});
  result.status=failure?'failed':'passed';
 }catch(error){
  Object.assign(result,{status:'failed',cleanupError:String(error),fixtureLog:log});
  if(!failure)throw error;
  console.error(error);
 }finally{
  result.fixtureExit={code:child.exitCode,signal:child.signalCode};
  await writeFile(output+'/browser-result.json',JSON.stringify(result,null,2));
 }
}
