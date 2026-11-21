import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {chromium,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),
 ['-u',fileURLToPath(new URL('../../tests/fixtures/browser_detail_server.py',import.meta.url)),fileURLToPath(new URL('../../',import.meta.url))],
 {stdio:['ignore','pipe','pipe']});
let browser,log='',fixtureCookie;fixture.stderr.on('data',chunk=>log+=chunk);
try{
 const port=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+log)),30000);fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line){clearTimeout(timer);const result=JSON.parse(line);fixtureCookie=result.cookie;resolve(result.port)}});fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+log))})});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const page=await browser.newPage({viewport:{width:1280,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-detail-token'}}),errors=[];
 page.on('pageerror',error=>{errors.push(error.message);console.error('Page error:',error.message)});
 await page.context().addCookies([{name:'amplifier_unified_session',value:fixtureCookie,url:`http://127.0.0.1:${port}`,httpOnly:true,sameSite:'Strict'}]);
 await page.goto(`http://127.0.0.1:${port}`);
 await page.waitForSelector('#amp-one');
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'settings',settingsExpanded:['diagnostics']}}));
 await openSettingsPage(page,'diagnostics');
 const recording=page.getByLabel('Record provider requests and responses for troubleshooting',{exact:true});
 await expect(recording).not.toBeChecked();
 await expect(page.getByText(/Earlier calls cannot be recovered/)).toBeVisible();
 await recording.check();
 await page.waitForFunction(()=>window.amplifier.getState().diagnostics.config.providerRequests===true);
 assert.deepEqual(await page.evaluate(()=>window.amplifier.getState().diagnostics.config.destinations),[]);
 await page.reload();
 // The saved Settings panel can restore after the initial shell state.
 // Wait for that restored overlay before trying to navigate through it.
 await page.locator('.a-settings-experience').waitFor({state:'visible'});
 await openSettingsPage(page,'diagnostics');
 await expect(recording).toBeChecked();
 await page.screenshot({path:'/tmp/unified-provider-recording-desktop.png'});
 await page.setViewportSize({width:390,height:844});
 await openSettingsPage(page,'diagnostics');
 await expect(recording).toBeChecked();
 await recording.uncheck();
 await page.waitForFunction(()=>window.amplifier.getState().diagnostics.config.providerRequests===false);
 await page.reload();
 await page.locator('.a-settings-experience').waitFor({state:'visible'});
 await openSettingsPage(page,'diagnostics');
 await expect(recording).not.toBeChecked();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await recording.scrollIntoViewIfNeeded();
 await page.screenshot({path:'/tmp/unified-provider-recording-mobile.png'});
 // The real paged browser flow must work without the complete event-tree
 // projection that older canonical fixtures called before opening the UI.
 await page.setViewportSize({width:1280,height:1000});
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:null,executionExpanded:[]}}));
 const seeded=await page.request.post(`http://127.0.0.1:${port}/api/fixture/control`,{data:{op:'bounded-provider-records',delayCapture:true}});
 assert.equal(seeded.ok(),true,await seeded.text());
 await page.reload();
 await showFixtureConversation(page);
 const live=page.locator('[data-group-id$="@recording-question"]');
 await live.locator('button.a-execution-turn-line').click();
 await expect(live.getByRole('button',{name:'Load earlier steps',exact:true})).toBeVisible();
 await live.getByRole('button',{name:'Load earlier steps',exact:true}).click();
 await expect(live.locator('[data-kind="tool"]')).toHaveCount(105);
 const reads=[];
 page.on('request',r=>{if(r.url().includes('/api/conversation/detail?')&&new URL(r.url()).searchParams.has('field'))reads.push(r.url())});
 const inspector=page.locator('#work-step-inspector');
 const closeInspector=()=>inspector.getByRole('button',{name:'Close step details'}).click();
 const model=live.locator('[data-kind="llm"]').filter({hasText:'bounded-recording'});
 await model.locator('button.a-execution-action-line').click();
 assert.equal(reads.length,0,'Opening model facts must not disclose raw payloads');
 await expect(inspector).toContainText('This call’s request recording isn’t available here.');
 await expect(inspector.getByRole('button',{name:/^View request/})).toHaveCount(0);
 const flushed=await page.request.post(`http://127.0.0.1:${port}/api/fixture/control`,{data:{op:'flush-provider-records'}});
 assert.equal(flushed.ok(),true,await flushed.text());
 // No reload, toggle, or new execution event: the open completed model must
 // subscribe again when its accepted canonical recording arrives.
 await expect(inspector.getByRole('button',{name:/^View request/})).toBeVisible();
 await inspector.getByRole('button',{name:/^View request/}).click();
 await inspector.getByRole('button',{name:/^Request Object/}).click();
 await expect(inspector).toContainText('RECORDED-REQUEST');
 await inspector.getByRole('button',{name:/^View response/}).click();
 await inspector.getByRole('button',{name:/^Response Object/}).click();
 await expect(inspector).toContainText('RECORDED-RESPONSE');
 const fullDownload=async container=>{
  const pending=page.waitForEvent('download');
  await container.getByRole('link',{name:'Download full recorded request (JSON)',exact:true}).click();
  const download=await pending;
  const body=JSON.parse(await readFile(await download.path(),'utf8'));
  assert.equal(body.payload.end,'FULL-RECORDING-END');
  assert.equal(body.capture.truncated,false);
 };
 await inspector.getByRole('button',{name:'Show full recorded request',exact:true}).click();
 const fullTree=inspector.getByRole('group',{name:'Recorded request payload'});
 await fullTree.getByRole('button',{name:/Recorded request Object/}).click();
 await expect(fullTree).toContainText('FULL-RECORDING-END');
 await fullDownload(inspector);
 const legacy=live.locator('[data-node-id="llm:legacy"]');
 await legacy.locator('button.a-execution-action-line').click();
 await expect(inspector).toContainText('This call’s request recording isn’t available here.');
 await expect(inspector).toContainText('Recording settings save automatically');
 assert.ok(!(await inspector.innerText()).includes('Save capture settings'));
 const savedParent=page.locator('.a-work-group').filter({has:page.locator(':scope > button', {hasText:'Saved work pages'})});
 const savedToggle=savedParent.locator(':scope > button');
 // The cursor can first return earlier resident groups before the older
 // event-log page. Advance while an earlier cursor is actually available.
 const earlier=page.getByRole('button',{name:'Load earlier conversation',exact:true});
 for(let count=0;count<8&&!await savedToggle.isVisible()&&await earlier.isVisible();count++){
  await Promise.all([page.waitForResponse(response=>{const url=new URL(response.url());return url.pathname==='/api/conversation/detail'&&url.searchParams.has('before')}),earlier.click()]);
  await expect(page.getByRole('button',{name:/Loading earlier conversation/})).toHaveCount(0);
 }
 await expect(savedToggle).toBeVisible();
 if(await savedToggle.getAttribute('aria-expanded')==='true')await savedToggle.click();
 await expect(page.locator('[data-group-id^="event-window:"]')).toHaveCount(0);
 await inspector.getByRole('button',{name:'Show saved recordings',exact:true}).click();
 await expect(savedToggle).toHaveAttribute('aria-expanded','true');
 await expect(savedParent.locator('[data-group-id^="event-window:"]')).toHaveCount(2);
 const saved=savedParent.locator('[data-group-id^="event-window:"]').filter({hasText:'legacy-recording'});
 await expect(saved).toBeInViewport();
 const request=saved.locator('[data-kind="llm"]').filter({hasText:'legacy-recording'}).filter({hasText:'Provider request'});
 await request.locator('button.a-execution-action-line').click();
 await expect(inspector).toContainText('Recorded');
 await expect(inspector).toContainText(new Date().getFullYear().toString());
 await inspector.getByRole('button',{name:/^View request/}).click();
 await inspector.getByRole('button',{name:/^Request Object/}).click();
 await expect(inspector).toContainText('LEGACY-REQUEST');
 // The same full download must work from an independent saved observation.
 const canonical=saved.locator('[data-kind="llm"]').filter({hasText:'bounded-recording'}).filter({hasText:'Provider request'});
 await canonical.locator('button.a-execution-action-line').click();
 await fullDownload(inspector);
 await closeInspector();
 await page.setViewportSize({width:390,height:844});
 await savedToggle.click();
 await expect(page.locator('[data-group-id^="event-window:"]')).toHaveCount(0);
 await legacy.locator('button.a-execution-action-line').click();
 await inspector.getByRole('button',{name:'Show saved recordings',exact:true}).click();
 await expect(savedToggle).toHaveAttribute('aria-expanded','true');
 await expect(saved).toBeInViewport();
 // Runtime diagnostics separately retain a summary-only completion event.
 // Open both valid observations; only the provider attempt has raw bytes.
 const responses=saved.locator('[data-kind="llm"]').filter({hasText:'legacy-recording'}).filter({hasText:'Provider response'});
 await expect(responses).toHaveCount(2);
 let summaries=0,recordings=0;
 for(const row of await responses.all()){
  await row.locator('button.a-execution-action-line').click();
  await expect(inspector).toBeVisible();
  const view=inspector.getByRole('button',{name:/^View response/});
  if(await view.count()){
   await view.click();
   await inspector.getByRole('button',{name:/^Response Object/}).click();
   await expect(inspector).toContainText('LEGACY-RESPONSE');
   recordings++;
  }else{
   await expect(inspector).toContainText('This event has no response payload.');
   summaries++;
  }
  await closeInspector();
 }
 assert.equal(recordings,1);
 assert.equal(summaries,1);
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:'/tmp/unified-provider-recording-window-mobile.png'});
 assert.deepEqual(errors,[]);
 console.log('Provider recording: autosave, 107 paged live steps, delayed exact capture without execution events, explicit disclosure, full downloads from linked and independent windows, legacy request/response navigation through two collapsed saved pages with timestamps, and narrow viewport passed.');
}finally{
 await browser?.close();fixture.kill();
}
