// Installed production projection/history status rows; explicitly synthetic progress.
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import {startFixture} from './fixture-readiness.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url)),output=process.env.AMPLIFIER_TEST_ARTIFACTS;
assert.ok(output&&process.env.AMPLIFIER_TEST_PACKAGE_ROOT);await mkdir(output,{recursive:true});
const fixture=await startFixture({command:process.env.AMPLIFIER_TEST_PYTHON,args:[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],env:process.env,tag:'empty-host-ui',name:'Installed work status'});
let browser,page,originalError;const metrics=[],errors=[],actions=[];
try{
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce',extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page.on('pageerror',e=>errors.push(e.message));
 await page.goto(fixture.base);await showFixtureConversation(page,{navPinned:false});await page.evaluate(()=>window.amplifier.dispatch('session.create',{title:'Synthetic status rows'}));
 const sid=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 const seed=async status=>{const response=await page.request.post(fixture.base+'/fixture/layout-status',{data:{sessionId:sid,status}});assert.equal(response.status(),200)};
 await seed('loading');const loading=page.locator('[data-part=work-projection-status]');await expect(loading).toHaveText('Loading work details…');
 async function measure(locator,label,width){
  await locator.scrollIntoViewIfNeeded();const geometry=await locator.evaluate(row=>{const css=getComputedStyle(row),box=row.getBoundingClientRect(),icon=row.children[0]?.getBoundingClientRect(),text=row.children[1]?.getBoundingClientRect();return {display:css.display,gap:parseFloat(css.columnGap),overflow:row.scrollWidth>row.clientWidth+1,box:{x:box.x,y:box.y,width:box.width,height:box.height},icon:icon?{left:icon.left,right:icon.right,top:icon.top,bottom:icon.bottom}:null,text:text?{left:text.left,right:text.right,top:text.top,bottom:text.bottom}:null}});
  metrics.push({label,width,...geometry});assert.ok(['flex','inline-flex'].includes(geometry.display),JSON.stringify(geometry));assert.ok(geometry.gap>=6);assert.equal(geometry.overflow,false);if(geometry.icon&&geometry.text)assert.ok(Math.max(geometry.text.left-geometry.icon.right,geometry.icon.left-geometry.text.right,geometry.text.top-geometry.icon.bottom,geometry.icon.top-geometry.text.bottom)>=6,JSON.stringify({label,width,...geometry}));await page.screenshot({path:output+`/${label}-${width}.png`});
 }
 for(const width of [1440,768,420,390]){await page.setViewportSize({width,height:1000});if(await page.getByRole('button',{name:'Close navigation',exact:true}).isVisible())await page.getByRole('button',{name:'Close navigation',exact:true}).click();await measure(loading,'loading',width)}
 await seed('retrying');const alert=page.locator('.a-session-history-error');await expect(alert).toContainText('Synthetic history read is unavailable. Original files are unchanged.');const retry=alert.getByRole('button',{name:'Try again',exact:true});await expect(retry).toBeVisible();
 for(const width of [1440,768,420,390]){await page.setViewportSize({width,height:1000});await measure(alert.locator('.a-history-status'),'warning-text',width);await measure(alert,'warning-retry',width)}
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')actions.push(request.postDataJSON())});
 await retry.click();await expect.poll(()=>actions.filter(row=>row.action==='session.select').length).toBe(1);assert.equal(actions.find(row=>row.action==='session.select').args.id,sid);
 await seed('ready');await expect(loading).toHaveCount(0);await expect(alert).toHaveCount(0);
 assert.deepEqual(errors,[]);assert.deepEqual((await page.request.get(fixture.base+'/fixture').then(r=>r.json())).sent,[]);
 await writeFile(output+'/result.json',JSON.stringify({passed:true,scope:'Installed host/assets; synthetic projection/history failure; actual Retry selects existing chat, no send/runtime effect',metrics,actions},null,2)+'\n');
}catch(error){originalError=error;await writeFile(output+'/failure.json',JSON.stringify({error:String(error.stack||error),metrics},null,2));await page?.screenshot({path:output+'/failure.png'}).catch(()=>{});throw error}
finally{await finishFixture({label:'Installed work status',stop:fixture.stop,close:[['browser',()=>browser?.close()]],originalError})}
