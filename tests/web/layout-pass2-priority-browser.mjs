// Installed presentation audit. Fixture data only; no provider, updater or recovery execution.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {startSettingsFixture,updateAppearance,settledAppearance} from './settings-fixture.mjs';
import {startFixture} from './fixture-readiness.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {assertUpdateSupportGeometry} from '../../.build/web-client/tests/update-support-geometry.mjs';
import {assertConversationErrorActionAlignment} from '../../.build/web-client/tests/conversation-error-geometry.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS;assert.ok(output&&process.env.AMPLIFIER_TEST_PACKAGE_ROOT);
await mkdir(output,{recursive:true});
const report={scope:'Installed package; synthetic updater diagnostics and error classification; no live operations',source:process.env.LAYOUT_SOURCE||'baseline-e7bc-ad9',checkpoints:[],findings:[],errors:[],actions:[],pixelReview:'pending independent image-bearing review'};
const save=()=>writeFile(output+'/result.json',JSON.stringify(report,null,2)+'\n');
const activate=async(label,control)=>{await expect(control).toBeVisible();await control.click();report.actions.push({type:'visible-control',label})};
async function capture(page,id,target){await target.scrollIntoViewIfNeeded();await page.screenshot({path:output+'/'+id+'.png'});report.checkpoints.push({id,screenshot:id+'.png',viewport:page.viewportSize(),route:await page.evaluate(()=>document.querySelector('.a-settings-experience')?.dataset.settingsRoute??null),appearance:await page.evaluate(()=>window.amplifier.getState().appearance?.effective),scroll:await target.evaluate(el=>{const a=[];for(let p=el;p;p=p.parentElement)if(p.scrollHeight>p.clientHeight)a.push({class:p.className,top:p.scrollTop});return a})});await save()}
async function paint(control){return control.evaluate(el=>{const rect=n=>{const r=n.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}},css=getComputedStyle(el),text=el.querySelector('span')||[...el.childNodes].find(n=>n.nodeType===Node.TEXT_NODE&&n.textContent.trim());let glyph=null;if(text){const range=document.createRange();range.selectNodeContents(text);const r=range.getBoundingClientRect();glyph={x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}}const icon=el.querySelector('svg');return{box:rect(el),glyph,icon:icon?rect(icon):null,paddingInlineStart:parseFloat(css.paddingInlineStart),paddingInlineEnd:parseFloat(css.paddingInlineEnd),columnGap:css.columnGap,lineHeight:css.lineHeight,font:css.font,border:css.border,background:css.backgroundColor,outline:css.outline,focusVisible:el.matches(':focus-visible'),overflow:el.scrollWidth>el.clientWidth+1,tag:el.tagName}})}
async function bindServed(page){const identity=JSON.parse(await(await import('node:fs/promises')).readFile(process.env.AMPLIFIER_TEST_WHEEL_IDENTITY,'utf8')),served={};for(const url of await page.locator('script[src],link[rel=stylesheet][href]').evaluateAll(ns=>ns.map(n=>n.src||n.href))){const name='amplifier_web/static/'+new URL(url).pathname.slice(1),response=await page.request.get(url);served[name]=createHash('sha256').update(await response.body()).digest('hex');assert.equal(served[name],identity.files[name],name)}return{wheelSha256:identity.wheelSha256,served}}
let fixture,browser,page,originalError;
try{
 if(process.env.LAYOUT_PHASE!=='error'){
 fixture=await startSettingsFixture({SETTINGS_RELEASE_FIXTURE:'1'});browser=await chromium.launch();page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'},acceptDownloads:true});
 await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:async()=>{throw Error('Synthetic clipboard denied')}},configurable:true}));
 await page.route('**/*',route=>{const req=route.request(),url=new URL(req.url());if(['http:','https:'].includes(url.protocol)&&url.hostname!=='127.0.0.1')return route.abort();if(url.pathname==='/api/actions'&&req.method()==='POST'){const b=req.postDataJSON();if(b.action==='updates.diagnostics')return route.fulfill({json:{accepted:true,result:{schema:1,synthetic:true,errors:['Synthetic update failed. Original installation kept.'],versions:{app:'fixture'}}}});if(/^(updates\.(install|check|rollback|restart)|session\.(recover|restart)|conversation\.send|feedback\.(submit|send))/.test(b.action))return route.fulfill({status:409,json:{accepted:false,error:'Presentation audit refuses effects.'}})}return route.continue()});
 await page.goto(fixture.base);report.installed=await bindServed(page);
 await openSettingsPage(page,'appearance',{activate});await expect(page.locator('.a-appearance-gallery')).toBeVisible();
 report.gallery=await page.locator('[data-appearance-card]').evaluateAll(ns=>ns.map(n=>({id:n.dataset.appearanceCard,name:n.querySelector('input')?.getAttribute('aria-label'),description:n.textContent.trim()})));
 report.purpleDisposition='Use actual observed builtin:default Amplifier (catalog describes blue/lilac); screenshot-specific appearance identity requires independent pixel comparison, not inferred from color.';
 const ids=['reference',...report.gallery.map(x=>x.id)];
 const variants=ids.map((id,i)=>({id,width:1440,scheme:i%2?'dark':'light',density:i%2?'compact':'comfortable'}));variants.push({id:'builtin:default',width:390,scheme:'dark',density:'compact'},{id:'reference',width:320,scheme:'light',density:'comfortable'});
 for(const [i,v] of variants.entries()){
  await page.setViewportSize({width:v.width,height:1000});await updateAppearance(page,{appearanceId:v.id,scheme:v.scheme,density:v.density});await settledAppearance(page);
  await openSettingsPage(page,'updates',{activate});const advanced=page.locator('[data-part=ecosystem-update-details]');if(!await advanced.evaluate(n=>n.open))await activate('Expand update Advanced details',advanced.locator(':scope>summary'));
  const support=page.locator('[data-part=update-support]');await support.scrollIntoViewIfNeeded();await page.keyboard.press('Tab');const copy=support.getByRole('button',{name:'Copy diagnostics',exact:true}),download=support.getByRole('button',{name:'Save diagnostics',exact:true});await copy.focus();
  const metrics={variant:v,copy:await paint(copy),save:await paint(download)};report.checkpoints.push({id:`update-padding-${i}`,type:'geometry',metrics});
  if(metrics.copy.paddingInlineStart<6||metrics.copy.paddingInlineEnd<6)report.findings.push({id:'updates-diagnostics-inline-padding',variant:v,evidence:`update-support-${i}.png`,measured:metrics.copy});
  if(process.env.LAYOUT_REQUIRE_FIXED==='1'){
   metrics.strictGeometry=await assertUpdateSupportGeometry(support,`installed ${v.id} ${v.width}`);
  }
  await capture(page,`update-support-${i}`,support);
 }
 await activate('Copy diagnostics with deliberately denied clipboard',page.getByRole('button',{name:'Copy diagnostics',exact:true}));await expect(page.getByRole('textbox',{name:'Update diagnostic report',exact:true})).toBeVisible();await capture(page,'update-clipboard-fallback',page.locator('[data-part=update-support]'));
 const downloaded=page.waitForEvent('download');await activate('Save synthetic diagnostic report',page.getByRole('button',{name:'Save diagnostics',exact:true}));const file=await downloaded;assert.equal(file.suggestedFilename(),'amplifier-update-diagnostics.json');await file.saveAs(output+'/diagnostics-download.json');report.download={filename:file.suggestedFilename(),synthetic:true};await capture(page,'update-saved-diagnostics',page.locator('[data-part=update-support]'));
 await browser.close();browser=null;await fixture.stop();fixture=null;
 }
 fixture=await startFixture({command:process.env.AMPLIFIER_TEST_PYTHON,args:[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],env:process.env,tag:'empty-host-ui',name:'Second-pass error'});browser=await chromium.launch();page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await page.goto(fixture.base);report.errorInstalled=await bindServed(page);await showFixtureConversation(page,{navPinned:false});await page.evaluate(()=>window.amplifier.dispatch('session.create',{title:'Synthetic second-pass error'}));const sid=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Preserve unsent audit draft');
 await page.route('**/api/actions',route=>{const b=route.request().postDataJSON();if(b.action==='session.recover'||b.action==='conversation.send'||b.action.startsWith('feedback.submit')){report.actions.push({refused:b.action});return route.fulfill({status:409,json:{accepted:false,error:'Synthetic audit refuses operation'}})}return route.continue()});
 assert.equal((await page.request.post(fixture.base+'/fixture/conversation-error',{data:{sessionId:sid}})).status(),200);const banner=page.locator('[data-part=conversation-trouble]');await expect(banner).toBeVisible();
 for(const width of [1440,768,390,320]){await page.setViewportSize({width,height:1000});const nav=page.getByRole('button',{name:'Close navigation',exact:true});if(await nav.isVisible())await nav.click();for(const open of [false,true]){const summary=banner.locator('summary');if(await banner.locator('details').evaluate(n=>n.open)!==open)await summary.click();await page.keyboard.press('Tab');await summary.focus();const recovery=banner.getByRole('button',{name:'Create recovery copy',exact:true});const values={summary:await paint(summary),recovery:await paint(recovery),text:await banner.innerText()};report.checkpoints.push({id:`error-alignment-${width}-${open}`,type:'geometry',metrics:values});await save();if(process.env.LAYOUT_REQUIRE_FIXED==='1')values.strictAlignment=await assertConversationErrorActionAlignment(banner,`installed ${width} ${open}`,{requireAdjacentSingleLine:width===1440&&!open});await capture(page,`error-${width}-${open?'open':'closed'}`,banner)}}
 await activate('Technical details',banner.getByRole('button',{name:'Technical details',exact:true}));await expect(page.getByRole('dialog',{name:'Error details',exact:true})).toBeVisible();await capture(page,'error-technical-details',page.getByRole('dialog',{name:'Error details',exact:true}));await page.getByRole('button',{name:/^Close (settings|panel)$/}).last().click();
 await activate('Report a problem (unsent)',banner.getByRole('button',{name:'Report a problem',exact:true}));await expect(page.getByRole('dialog')).toBeVisible();await capture(page,'error-unsent-report',page.getByRole('dialog'));await page.getByRole('button',{name:/^Close (settings|panel)$/}).last().click();
 report.complete=true;await save();
}catch(error){originalError=error;report.errors.push(String(error.stack||error));report.complete=false;await save();await page?.screenshot({path:output+'/failure.png'}).catch(()=>{});throw error}
finally{await finishFixture({label:'Second-pass priority',stop:()=>fixture?.stop(),close:[['browser',()=>browser?.close()]],originalError})}
