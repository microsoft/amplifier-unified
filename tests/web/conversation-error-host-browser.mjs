// Installed production host/assets; synthetic failure seed, no recovery side effects.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {startFixture} from './fixture-readiness.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {assertConversationErrorGeometry,assertConversationErrorFocus} from '../../.build/web-client/tests/conversation-error-geometry.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url)),output=process.env.AMPLIFIER_TEST_ARTIFACTS;
assert.ok(output&&process.env.AMPLIFIER_TEST_PACKAGE_ROOT);await mkdir(output,{recursive:true});
const fixture=await startFixture({command:process.env.AMPLIFIER_TEST_PYTHON,args:[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],env:process.env,tag:'empty-host-ui',name:'Installed error'});
let browser,page,originalError;const metrics=[],calls=[],errors=[];
try{
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page.on('pageerror',e=>errors.push(e.message));
 await page.goto(fixture.base);await showFixtureConversation(page,{navPinned:false});
 const input=page.getByRole('textbox',{name:'Message Amplifier'}),composer=page.locator('form.a-composer');await input.fill('Unsent draft must survive the banner.');
 await page.getByRole('button',{name:'Send message',exact:true}).waitFor();
 // Create a synthetic chat without submitting it so the fixture seed has a
 // durable selected conversation; ordinary host creation does not run a model.
 await page.evaluate(()=>window.amplifier.dispatch('session.create',{title:'Synthetic error geometry'}));
 await input.fill('Unsent draft must survive the banner.');
 const sid=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 const identity=await page.request.get(fixture.base+'/fixture').then(r=>r.json());assert.equal(identity.installedPackageVerified,true);
 const served={};for(const asset of await page.locator('script[src],link[rel=stylesheet][href]').evaluateAll(nodes=>nodes.map(n=>n.src||n.href))){const name=new URL(asset).pathname.slice(1),response=await page.request.get(asset);served[name]=createHash('sha256').update(await response.body()).digest('hex');assert.equal(served[name],identity.staticSha256[name])}
 assert.equal((await page.request.post(fixture.base+'/fixture/conversation-error',{data:{sessionId:sid}})).status(),200);
 const banner=page.locator('[data-part=conversation-trouble]');await expect(banner).toContainText('Synthetic conversation reached its context limit.');
 // Real actions are observed, never replayed. Recovery is refused before host
 // dispatch; reporting opens its ordinary unsent editor and sends no report.
 const noRecovery=async route=>{const body=route.request().postDataJSON();if(body.action==='session.recover'){calls.push(body);return route.fulfill({status:409,json:{accepted:false,error:'Synthetic fixture refuses recovery creation.'}})}return route.continue()};
 await page.route('**/api/actions',noRecovery);
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions'){const body=request.postDataJSON();if(body.action!=='session.recover')calls.push(body)}});
 for(const width of [1440,768,420,390]){
  await page.setViewportSize({width,height:1000});
  if(await page.getByRole('button',{name:'Close navigation',exact:true}).isVisible())await page.getByRole('button',{name:'Close navigation',exact:true}).click();
  const summary=banner.locator('summary');
  for(const open of [false,true]){
   if(await banner.locator('details').evaluate(node=>node.open)!==open)await summary.click();
   metrics.push(await assertConversationErrorGeometry({banner,composer,label:`installed-${width}-${open?'open':'closed'}`}));
   await page.keyboard.press('Tab'); // Establish keyboard modality before testing :focus-visible.
   for(const control of [summary,banner.getByRole('button',{name:'Dismiss conversation error'}),banner.getByRole('button',{name:'Create recovery copy',exact:true}),...(open?[banner.getByRole('button',{name:'Technical details',exact:true}),banner.getByRole('button',{name:'Report a problem',exact:true})]:[])])await assertConversationErrorFocus(control,'installed-'+width);
   await page.screenshot({path:output+`/error-${width}-${open?'open':'closed'}.png`});
  }
 }
 await banner.getByRole('button',{name:'Create recovery copy',exact:true}).click();await expect(banner.getByRole('alert')).toHaveText('Could not create the recovery copy.');
 assert.equal(calls.filter(call=>call.action==='session.recover').length,1);
 await banner.getByRole('button',{name:'Technical details',exact:true}).click();await expect(page.getByRole('dialog',{name:'Error details',exact:true})).toBeVisible();
 await page.getByRole('button',{name:/^Close (settings|panel)$/}).last().click();
 await banner.getByRole('button',{name:'Report a problem',exact:true}).click();await expect(page.getByRole('dialog')).toBeVisible();
 assert.equal(calls.filter(call=>call.action.startsWith('feedback.')||call.action==='conversation.send').length,0,'Opening report submits nothing');
 await page.getByRole('button',{name:/^Close (settings|panel)$/}).last().click();
 await banner.getByRole('button',{name:'Dismiss conversation error'}).click();await expect(banner).toHaveCount(0);
 await expect(input).toHaveText('Unsent draft must survive the banner.');
 assert.deepEqual((await page.request.get(fixture.base+'/fixture').then(r=>r.json())).sent,[]);assert.deepEqual(errors,[]);
 await writeFile(output+'/result.json',JSON.stringify({passed:true,scope:'Installed host, synthetic classified error and refused recovery; no live incident/recovery/report submit',metrics,servedAssets:served,actions:calls.map(({action,args})=>({action,args}))},null,2)+'\n');
}catch(error){originalError=error;await writeFile(output+'/failure.json',JSON.stringify({error:String(error.stack||error),metrics,calls,errors},null,2));await page?.screenshot({path:output+'/failure.png'}).catch(()=>{});throw error}
finally{await finishFixture({label:'Installed error',stop:fixture.stop,close:[['browser',()=>browser?.close()]],originalError})}
