import {fixturePython} from './fixture-python.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
const output=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!output)throw Error('Owned evidence folder required');
const installed=process.env.AMPLIFIER_TEST_INSTALLED_PACKAGE;
const hostEnv={...process.env,PYTHONDONTWRITEBYTECODE:'1'};
delete hostEnv.PYTHONPATH;delete hostEnv.PYTHONHOME;
if(installed)hostEnv.PATH=process.env.AMPLIFIER_TEST_HOST_PATH;
const child=spawn(fixturePython(),['-I','-B','-u',fileURLToPath(new URL('../../tests/fixtures/update_current_health_server.py',import.meta.url))],{env:hostEnv});
let log='',browser;child.stderr.on('data',x=>log+=x);
const result={paidCalls:0,liveInstallCalls:0,syntheticRecordedGraph:true,capturedLegacyAttempts:true};
try{
 const boot=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error(log||'boot timeout')),60000);child.once('exit',code=>{clearTimeout(timer);reject(Error('fixture exit '+code+log))});child.stdout.on('data',x=>{text+=x;for(const line of text.split('\n'))try{const v=JSON.parse(line);if(v.url){clearTimeout(timer);resolve(v)}}catch{}})});
 browser=await chromium.launch({headless:true,channel:'chromium',args:['--disable-gpu','--no-zygote']});
 const page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 if(installed){
  const component=await (await page.request.get(boot.url+'/client-component.json')).json();
  assert.equal(component.source.revision,process.env.AMPLIFIER_TEST_CLIENT_REVISION);
  const identity=await(await page.request.get(boot.url+'/build.json')).json();
  assert.deepEqual(identity,component.build);
  const html=await(await page.request.get(boot.url)).text();
  const paths=[...html.matchAll(/(?:src|href)="(\/[^"]+\.(?:js|css))"/g)].map(x=>x[1]);
  assert.ok(paths.length);
  for(const path of paths){
   const response=await page.request.get(boot.url+path);assert.ok(response.ok());
   const bytes=await response.body(),expected=component.assets[path.slice(1)];
   assert.ok(expected,path);assert.equal(bytes.length,expected.bytes);
   assert.equal(createHash('sha256').update(bytes).digest('hex'),expected.sha256);
  }
  result.servedEntrypointAssetHashes=true;result.selectedClient=component.source;result.clientBuild=component.build;
 }
 const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(boot.url);await page.waitForSelector('#amp-one');await openSettingsPage(page,'updates');
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
 // Real host Check/projection/Install owner, not a synthetic client peer.
 const retryButton=panel.getByRole('button',{name:'Retry installation',exact:true});
 for(const mode of ['known','unknown','missing','stale']){
  await transition('retry-'+mode);
  if(mode==='known')await expect(retryButton).toBeVisible();
  else await expect(retryButton).toHaveCount(0);
  const observation=await(await page.request.get(boot.url+'/fixture/current-update')).json();
  assert.equal(observation.retry.candidateCount,0);assert.equal(observation.retry.fetches,0);
  assert.equal(observation.retry.prepareCalls,0);assert.equal(observation.retry.installActions,0);
 }
 await transition('retry-known');
 for(const width of [1280,390]){
  await page.setViewportSize({width,height:950});await expect(retryButton).toBeVisible();await expect(retryButton).toBeEnabled();
  await page.screenshot({path:output+`/retained-retry-${width}.png`});
 }
 await panel.getByRole('button',{name:/^Check/}).first().click();
 await expect(retryButton).toBeEnabled();
 await page.reload();await page.waitForSelector('#amp-one');await openSettingsPage(page,'updates');
 await expect(retryButton).toBeVisible();
 let observation=await(await page.request.get(boot.url+'/fixture/current-update')).json();
 assert.equal(observation.retry.candidateCount,0);assert.equal(observation.retry.installActions,0);
 await retryButton.click();
 await expect.poll(async()=> (await(await page.request.get(boot.url+'/fixture/current-update')).json()).retry.prepareCalls).toBe(1);
 observation=await(await page.request.get(boot.url+'/fixture/current-update')).json();
 assert.equal(observation.retry.installActions,1);assert.equal(observation.retry.candidateCount,1);
 assert.equal(observation.retry.fetches,1);assert.equal(observation.retry.activePointerUnchanged,true);
 assert.equal(observation.retry.servingSourceUnchanged,true);
 Object.assign(result,{retainedOnlyCheckNoPreparation:true,explicitRetryVisibleDesktopMobile:true,
  unknownMissingStaleNoRetry:true,oneGuardedCandidate:true,oneSyntheticAcquisition:true,
  runtimeQualificationNotRun:true,installed:!!installed});
 for(const width of [390,1280]){
  await page.setViewportSize({width,height:950});
  await expect(status).not.toContainText('You’re up to date');
  await page.screenshot({path:output+`/partial-${width}.png`});
 }
 await page.reload();await page.waitForSelector('#amp-one');
 if(!await status.isVisible())await openSettingsPage(page,'updates');
 await expect(outcomes).toContainText('Dependency-held — working version retained');
 assert.deepEqual(errors,[]);Object.assign(result,{status:'passed',package:boot.package,currentDesktop:true,currentMobile:true,historyRetained:true,reload:true,currentFailureHasAction:true,sourceActionReopensAndReveals:true,uncertainPreserved:true,notesDemand:true,notesMissingBounded:true,notesLateArrival:true,notesVersionSwitch:true,notesEmptyAndError:true,partialResults:true,partialReload:true});
} catch(error){Object.assign(result,{status:'failed',error:String(error),fixtureLog:log});throw error}
finally{await browser?.close();child.kill('SIGTERM');await new Promise(resolve=>{if(child.exitCode!==null)return resolve();child.once('exit',resolve);setTimeout(resolve,5000)});result.fixtureExit={code:child.exitCode,signal:child.signalCode};await writeFile(output+'/browser-result.json',JSON.stringify(result,null,2));assert.equal(child.exitCode,0);assert.equal(child.signalCode,null)}
