// Real built client, HTTP and durable host owner; synthetic runtime boundaries.
// Native checkpoint/restore and external effects require their own qualification.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {openSettingsPage} from './browser-settings.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/maintenance_controls_server.py',import.meta.url))]);
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/maintenance-controls';
const report={status:'pending',realHTTP:true,nativeExecution:false,checks:[],timings:{}};
let browser,page,log='',started=Date.now();const errors=[],newWorkRequests=[];
fixture.stderr.on('data',data=>log+=data);
try{
 await mkdir(out,{recursive:true});
 const boot=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error(log||'Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error(`Fixture exited ${code}: ${log}`))});fixture.stdout.on('data',data=>{text+=data;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}})});
 assert.equal(boot.package,fileURLToPath(new URL('../../amplifier_web/__init__.py',import.meta.url)));
 const launchOptions={headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]};
 browser=await chromium.launch(launchOptions);
 page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.method()==='POST'&&request.url().includes('/api/actions')){const action=request.postDataJSON()?.action;if(['conversation.send','call.start'].includes(action))newWorkRequests.push(action)}});
 const evidence=async()=>{const response=await page.request.get(boot.url+'/fixture/maintenance');assert.ok(response.ok());return response.json()};
 const control=async operation=>{const response=await page.request.post(boot.url+'/fixture/maintenance',{data:{operation}});assert.ok(response.ok());return response.json()};
 const controls=()=>page.locator('[data-part="host-maintenance"]:visible');
 const activity=async()=>{
  const close=page.getByRole('button',{name:'Close panel',exact:true});
  if(await close.isVisible())await close.click();
  const entry=page.getByRole('button',{name:/^Activity(?: \d+ unread items)?$/});
  if(!await entry.isVisible())await page.getByRole('button',{name:'Open navigation',exact:true}).click();
  await entry.click();await expect(controls()).toBeVisible();
 };
 await page.goto(boot.url);await page.waitForSelector('#amp-one');await showFixtureConversation(page);await activity();
 await expect(controls().getByRole('button',{name:'Pause all',exact:true})).toBeVisible();
 const pauseAt=Date.now();await controls().getByRole('button',{name:'Pause all',exact:true}).click();
 await expect(controls()).toContainText('New work is held');
 report.timings.pauseHoldVisibleMs=Date.now()-pauseAt;
 assert.equal((await evidence()).maintenance.held,true);
 // A second browser also works with the ARM single-process launch adapter,
 // whose Chromium process cannot safely host a second incognito context.
 const peerBrowser=await chromium.launch(launchOptions);
 try{
  const peer=await peerBrowser.newPage({viewport:{width:850,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  await peer.goto(boot.url);await peer.waitForSelector('#amp-one');await showFixtureConversation(peer);
  const peerActivity=peer.getByRole('button',{name:/^Activity(?: \d+ unread items)?$/});
  if(!await peerActivity.isVisible())await peer.getByRole('button',{name:'Open navigation',exact:true}).click();
  await peerActivity.click();await expect(peer.locator('[data-part="host-maintenance"]:visible')).toContainText('New work is held');
  await peer.reload();await expect(peer.locator('[data-part="host-maintenance"]:visible')).toContainText('New work is held');
  assert.equal((await evidence()).calls.filter(row=>row.operation==='pause').length,1);
 }finally{await peerBrowser.close()}
 report.checks.push('A separate browser observes the same durable hold after reconnect without replay');
 await expect(controls()).not.toContainText('All tracked work is paused');
 await expect(controls().getByRole('button',{name:'Resume paused work',exact:true})).toBeDisabled();
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 const composer=page.getByRole('textbox',{name:'Message Amplifier'}),draft='Keep my maintenance draft';
 await expect(composer).toBeEditable();await composer.fill(draft);
 await expect(page.getByRole('button',{name:'Send message',exact:true})).toBeDisabled();
 await composer.press('Enter');await expect(composer).toHaveText(draft);
 await page.getByRole('button',{name:'Open pause controls',exact:true}).click();await expect(controls()).toBeVisible();
 assert.deepEqual(newWorkRequests,[]);
 report.checks.push('Held conversation keeps an editable draft, blocks Send and opens shared pause controls');
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await openSettingsPage(page,'updates');
 await expect(controls()).toContainText('New work is held');
 await expect(page.getByRole('button',{name:'Force restart…',exact:true})).toHaveCount(0);
 await control('settle');await expect(controls()).toContainText('1 chat needs inspection');
 await expect(controls()).not.toContainText('All tracked work is paused');
 report.checks.push('Activity and Updates share a durable hold; waiting/unsupported is not safe readiness');
 await controls().locator('summary').click();await expect(controls().locator('li')).toHaveCount(25);
 await controls().getByRole('button',{name:'Next work',exact:true}).click();await expect(controls().locator('li')).toHaveCount(2);
 await expect(controls()).toContainText('Cannot pause safely');
 await expect(controls().getByRole('button',{name:'Resume paused work',exact:true})).toBeDisabled();
 await control('settle-unsupported');await expect(controls().getByRole('button',{name:'Resume paused work',exact:true})).toBeEnabled();
 await controls().getByRole('button',{name:'Resume paused work',exact:true}).click();
 await expect(controls()).toContainText('Work can run');
 const resumed=await evidence();assert.equal(resumed.effects.length,26);assert.equal(resumed.calls.filter(row=>row.operation==='resume').length,1);
 assert.equal(resumed.maintenance.counts.stopped,1);
 report.checks.push('Paged unsupported work blocks Resume until positively stopped; only 26 paused owners resume');

 await control('prepare-supported');await controls().getByRole('button',{name:'Pause all',exact:true}).click();
 await control('settle');await expect(controls()).toContainText('All tracked work is paused');
 await control('auxiliary-start');await expect(controls()).toContainText('A voice call is active');
 await expect(controls()).toContainText('Remote audio is not paused');
 await expect(controls().getByRole('button',{name:'Resume paused work',exact:true})).toBeDisabled();
 assert.equal((await evidence()).maintenance.readyForRestart,false);
 await control('auxiliary-settle');await expect(controls()).toContainText('All tracked work is paused');
 await expect(controls().getByRole('button',{name:'Resume paused work',exact:true})).toBeEnabled();
 report.checks.push('Auxiliary activity disables safe readiness and Resume; canonical settlement refreshes both');
 await page.reload();await expect(controls()).toContainText('All tracked work is paused');
 const epoch=(await evidence()).maintenance.epoch;
 await controls().getByRole('button',{name:'Resume paused work',exact:true}).click();
 await expect(controls()).toContainText('Work can run');
 assert.equal((await evidence()).calls.filter(row=>row.operation==='resume').at(-1).epoch,epoch);
 report.checks.push('Ready state and exact resume epoch survive reload');

 await activity();
 const entry=controls().getByRole('button',{name:'Stop now…',exact:true});
 const dialog=page.getByRole('alertdialog',{name:'Confirm Stop now'}),cancel=dialog.getByRole('button',{name:'Cancel',exact:true}),confirm=dialog.getByRole('button',{name:'Stop owned work',exact:true});
 for(const width of [1280,390]){
  await page.setViewportSize({width,height:900});await entry.click();await expect(cancel).toBeFocused();
  await page.keyboard.press('Shift+Tab');await expect(confirm).toBeFocused();
  await page.keyboard.press('Tab');await expect(cancel).toBeFocused();
  await expect(dialog).toContainText('No update or restart is requested');
  const bounds=await dialog.boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width+1);
  await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);await expect(entry).toBeFocused();
  await expect(controls()).toBeVisible();assert.equal((await evidence()).calls.filter(row=>row.operation==='stop').length,0);
 }
 await entry.click();await control('change-confirmation');await expect(confirm).toBeDisabled();
 await cancel.click();await expect(entry).toBeFocused();
 await entry.click();await confirm.click();await expect(dialog).toHaveCount(0);
 await expect(controls()).toContainText('Work has stopped');
 assert.equal((await evidence()).calls.filter(row=>row.operation==='stop').length,1);
 await expect(controls().getByRole('button',{name:'Resume paused work',exact:true})).toBeEnabled();
 await controls().getByRole('button',{name:'Resume paused work',exact:true}).click();await expect(controls()).toContainText('Work can run');
 report.checks.push('Desktop/phone modal focus, Escape, stale confirmation and independent emergency Stop');

 await control('prepare-supported');
 let lost=false;
 await page.route('**/api/actions*',async route=>{
  if(!lost&&route.request().postDataJSON()?.action==='maintenance.pause'){
   lost=true;await route.fetch();await route.abort('failed');return;
  }
  await route.continue();
 });
 await controls().getByRole('button',{name:'Pause all',exact:true}).click();
 await expect(controls().getByRole('button',{name:'Retry same request',exact:true})).toBeVisible();
 const before=await evidence(),lastPause=before.commands.filter(row=>row.action==='maintenance.pause').at(-1);
 const count=before.calls.filter(row=>row.operation==='pause').length;
 const retryReceipt=page.waitForResponse(response=>response.request().method()==='POST'
  &&new URL(response.url()).pathname==='/api/actions'
  &&response.request().postDataJSON()?.action==='maintenance.pause'
  &&response.request().postDataJSON()?.id===lastPause.id);
 await controls().getByRole('button',{name:'Retry same request',exact:true}).click();
 const retryResponse=await retryReceipt;assert.ok(retryResponse.ok());
 assert.equal((await retryResponse.json()).duplicate,true);
 await expect(controls().getByRole('button',{name:'Retry same request',exact:true})).toHaveCount(0);
 const after=await evidence(),retried=after.commands.filter(row=>row.action==='maintenance.pause').at(-1);
 assert.equal(retried.id,lastPause.id);assert.equal(retried.duplicate,true);
 assert.equal(after.calls.filter(row=>row.operation==='pause').length,count);
 await control('settle');
 report.checks.push('Lost acknowledgement retries the same durable receipt without a second pause effect');
 assert.deepEqual(errors,[]);await page.screenshot({path:out+'/phone-final.png'});
 report.status='passed';report.timings.totalMs=Date.now()-started;
}catch(error){report.status='failed';report.error=String(error);if(page)await page.screenshot({path:out+'/failed.png'}).catch(()=>{});throw error}
finally{
 try{await browser?.close()}
 finally{
  fixture.kill('SIGTERM');
  await new Promise(resolve=>{if(fixture.exitCode!==null)return resolve();const timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve()},5000);fixture.once('exit',()=>{clearTimeout(timer);resolve()})});
  report.fixtureExit=fixture.exitCode;report.javascriptErrors=errors;
  await mkdir(out,{recursive:true});await writeFile(out+'/result.json',JSON.stringify(report,null,2));await writeFile(out+'/fixture.log',log);
 }
}
