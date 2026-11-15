// Real installed host/UI; synthetic runtime and populated Work, no live operation.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {startFixture} from './fixture-readiness.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {openChatAdvancedPage,showFixtureConversation} from './browser-chat-pages.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url)),output=process.env.AMPLIFIER_TEST_ARTIFACTS;
assert.ok(output&&process.env.AMPLIFIER_TEST_PACKAGE_ROOT);await mkdir(output,{recursive:true});
const fixture=await startFixture({command:process.env.AMPLIFIER_TEST_PYTHON,args:[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls','--voice-settings'],env:process.env,tag:'empty-host-ui',name:'Installed spacing',timeoutMs:60000});
let browser,page,originalError;const metrics=[],errors=[];
try{
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',e=>errors.push(e.message));await page.goto(fixture.base);await page.waitForFunction(()=>window.amplifier);
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const identity=await page.request.get(fixture.base+'/fixture').then(r=>r.json());assert.equal(identity.installedPackageVerified,true);
 const served={};for(const asset of await page.locator('script[src],link[rel=stylesheet][href]').evaluateAll(nodes=>nodes.map(n=>n.src||n.href))){const response=await page.request.get(asset),name=new URL(asset).pathname.slice(1);assert.equal(response.status(),200);served[name]=createHash('sha256').update(await response.body()).digest('hex');assert.equal(served[name],identity.staticSha256[name])}
 async function presentation(patch){
  const receipt=await page.evaluate(async patch=>{const shell=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:shell.revision,composition:{...shell.effectiveComposition,presentation:{...shell.effectiveComposition.presentation,...patch}}});if(!prepared.accepted)throw Error(prepared.error);const applied=await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:shell.revision,changeId:prepared.result.id});if(!applied.accepted)throw Error(applied.error);return {commandId:applied.result.commandId,baseRevision:prepared.result.baseRevision}},patch);
  await expect.poll(()=>page.evaluate(({commandId,baseRevision})=>{const shell=window.amplifier.getShellState();return shell.preferenceCommand?.id===commandId&&shell.preferenceCommand.status==='stored'&&shell.revision===baseRevision+1},receipt)).toBe(true);
 }
 const initial=await page.evaluate(()=>window.amplifier.getState());
 for(const width of process.env.LAYOUT_DEEP_ONLY?[]:[1440,768,420,390]){
  await page.setViewportSize({width,height:1000});
  for(const scheme of ['light','dark']){
   await presentation({scheme,interfaceDetail:scheme==='light'?'standard':'detailed',executionDetail:'detailed',density:scheme==='light'?'comfortable':'compact'});
   await openSettingsPage(page,'workspace-starters');const starters=page.getByRole('region',{name:'Workspace starters',exact:true});
   await action('view.update',{patch:{workspaceStarterEditor:{detailOpen:false}}});
   const search=starters.getByRole('searchbox',{name:'Find a starter'});await expect(search).toBeVisible();await search.focus();
   const geometry=await search.evaluate(input=>{
    const node=input.parentElement,icon=node.querySelector('svg'),style=getComputedStyle(node),r=node.getBoundingClientRect(),i=input.getBoundingClientRect(),s=icon.getBoundingClientRect();
    const ancestors=[];const extent=parseFloat(style.outlineWidth)+parseFloat(style.outlineOffset),paint={left:r.left-extent,right:r.right+extent,top:r.top-extent,bottom:r.bottom+extent};
    for(let p=node.parentElement;p;p=p.parentElement){const css=getComputedStyle(p),b=p.getBoundingClientRect();for(const [axis,low,high] of [['X','left','right'],['Y','top','bottom']])if(['auto','scroll','hidden','clip'].includes(css['overflow'+axis]))ancestors.push({axis,low:b[low]+(axis==='X'?p.clientLeft:p.clientTop),high:b[low]+(axis==='X'?p.clientLeft+p.clientWidth:p.clientTop+p.clientHeight)})}
    return {height:r.height,inputHeight:i.height,iconHeight:s.height,padding:parseFloat(style.paddingTop)+parseFloat(style.paddingBottom),borders:parseFloat(style.borderTopWidth)+parseFloat(style.borderBottomWidth),gap:i.left-s.right,vertical:Math.abs((i.top+i.height/2)-(s.top+s.height/2)),inputOutline:getComputedStyle(input).outlineStyle,outline:style.outlineStyle,paint,ancestors};
   });
   assert.ok(geometry.gap>=6&&geometry.vertical<4&&geometry.height<=Math.max(geometry.inputHeight,geometry.iconHeight)+geometry.padding+geometry.borders+1,JSON.stringify({width,scheme,...geometry}));assert.equal(geometry.inputOutline,'none');assert.equal(geometry.outline,'solid');
   for(const a of geometry.ancestors){assert.ok(geometry.paint[a.axis==='X'?'left':'top']>=a.low-.5);assert.ok(geometry.paint[a.axis==='X'?'right':'bottom']<=a.high+.5)}
   await search.fill('Amplifier');await expect(starters.locator('[data-collection-id]')).toHaveCount(1);await search.fill('');
   await starters.locator('[data-collection-id="amplifier-development"] button').click();const editor=page.getByRole('form',{name:'Starter editor'});
   for(const title of ['Guidance & working memory','Resource tracking','Local workspace Git']){const summary=editor.locator('summary').filter({hasText:title});if(!await summary.evaluate(node=>node.parentElement.open))await summary.click()}
   const choices=await editor.locator('label').filter({has:page.locator('input[type=checkbox]')}).evaluateAll(labels=>labels.map(label=>{const input=label.querySelector('input'),r=input.getBoundingClientRect(),walker=document.createTreeWalker(label,NodeFilter.SHOW_TEXT);let text;while(walker.nextNode())if(walker.currentNode.textContent.trim()){text=walker.currentNode;break}const range=document.createRange();range.selectNodeContents(text);const boxes=[...range.getClientRects()].filter(r=>r.width&&r.height);return {label:label.textContent.trim(),gaps:boxes.map(box=>box.left-r.right),offset:boxes.length?Math.abs(boxes[0].top-r.top):null}}));
   assert.ok(choices.length>=3);for(const choice of choices){assert.ok(choice.gaps.every(gap=>gap>=6),JSON.stringify(choice));assert.ok(choice.offset<=7)}
   const select=editor.getByLabel('Workspace bundle default',{exact:true});await expect(select).toBeVisible();
   const inset=await select.evaluate(node=>{const css=getComputedStyle(node);return {tag:node.tagName,paddingEnd:parseFloat(css.paddingInlineEnd),indicator:css.backgroundImage,appearance:css.appearance}});assert.equal(inset.tag,'SELECT');assert.ok(inset.paddingEnd>=36);assert.match(inset.indicator,/gradient/);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:output+`/starters-${width}-${scheme}.png`});
   await openSettingsPage(page,'routing');await page.screenshot({path:output+`/routing-${width}-${scheme}.png`});assert.ok(await page.locator('.a-dialog').evaluate(node=>node.scrollWidth<=node.clientWidth));
   await openSettingsPage(page,'appearance');await page.screenshot({path:output+`/appearance-${width}-${scheme}.png`});assert.ok(await page.locator('.a-dialog').evaluate(node=>node.scrollWidth<=node.clientWidth));
   metrics.push({width,scheme,search:geometry,choices,select:inset});
  }
 }
 // Deep, downpage changed-polish scenes: a bounded desktop/narrow pair, not a
 // Cartesian settings census. No voice call, upload, inspection or guest starts.
 const capture=async(locator,label)=>{
  await locator.scrollIntoViewIfNeeded();await expect(locator).toBeVisible();
  const geometry=await locator.evaluate(node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,overflow:node.scrollWidth>node.clientWidth+1}});
  assert.equal(geometry.overflow,false,label);metrics.push({label,...geometry});
  await page.screenshot({path:output+'/'+label+'.png'});
 };
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:1000});await presentation({scheme:width===1440?'light':'dark',density:width===1440?'comfortable':'compact',interfaceDetail:'detailed'});
  await openSettingsPage(page,'voice');const voice=page.locator('.a-voice-settings');await expect(voice.getByLabel('OpenAI API key',{exact:true})).toBeVisible();
  await capture(voice.locator('.a-voice-connection'),'voice-connection-'+width);
  const fallback=voice.locator('details').filter({has:page.locator('summary').filter({hasText:'If the preferred model is unavailable'})});if(!await fallback.evaluate(node=>node.open))await fallback.locator('summary').click();
  await fallback.getByLabel('Fallback model',{exact:true}).selectOption('gpt-live-1');await expect(fallback.getByLabel('Fallback speaking voice',{exact:true})).toBeVisible();await capture(fallback,'voice-fallback-'+width);
  await openSettingsPage(page,'appearance');const advanced=page.locator('.a-appearance-more');if(!await advanced.evaluate(node=>node.open))await advanced.locator(':scope>summary').click();
  const customization=page.locator('.a-appearance-advanced');if(!await customization.evaluate(node=>node.open))await customization.locator(':scope>summary').click();
  if(width===1440){
   await page.getByLabel('Appearance name',{exact:true}).fill('Spacing specimen');await page.getByLabel('Reference CSS delta',{exact:true}).fill('#amp-one{--a-accent:#137c8b}');
   await page.getByRole('button',{name:'Save as copy',exact:true}).click();
  }
  const saved=page.getByRole('group',{name:'Actions for Spacing specimen',exact:true});await expect(saved).toBeVisible();await capture(saved,'appearance-saved-actions-'+width);await capture(page.locator('.a-appearance-import'),'appearance-upload-'+width);
  await openSettingsPage(page,'routing');const routing=page.locator('[data-part=routing-settings]');
  if(!await routing.locator('[data-collection-id=general]>button').count())await routing.getByRole('button',{name:'New profile',exact:true}).click();
  await routing.locator('[data-collection-id=general]>button').click();await routing.locator('.a-routing-candidates [data-collection-id="0"]>button').click();
  if(!await routing.getByLabel('Choice configuration JSON',{exact:true}).isVisible())await routing.getByRole('button',{name:'Edit choice configuration',exact:true}).click();await capture(routing.getByLabel('Choice configuration JSON',{exact:true}),'routing-choice-json-'+width);
  if(width<960)for(let depth=0;await page.locator('.a-settings-experience').getAttribute('data-settings-detail')==='true';depth++){
   assert.ok(depth<3,'Back leaves the candidate and role detail levels');await page.locator('.a-settings-mobile-head button').first().click();
  }
  if(!await routing.getByLabel('Routing matrix JSON',{exact:true}).isVisible())await routing.getByRole('button',{name:'Show full profile JSON',exact:true}).click();await capture(routing.getByLabel('Routing matrix JSON',{exact:true}),'routing-profile-json-'+width);
  await openSettingsPage(page,'testing');
  for(const name of ['Advanced limits','Advanced execution limits']){
   const summary=page.locator('.a-settings-experience summary').filter({hasText:new RegExp('^'+name+'$')});await expect(summary).toHaveCount(1);if(!await summary.evaluate(node=>node.parentElement.open))await summary.click();await capture(summary.locator('..'),'testing-'+name.replaceAll(' ','-')+'-'+width);
  }
 }
 await page.getByRole('button',{name:/^Close (settings|panel)$/}).last().click();await action('session.create',{title:'Document action layout'});await showFixtureConversation(page,{navPinned:false});
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:1000});if(await page.getByRole('button',{name:'Close navigation',exact:true}).isVisible())await page.getByRole('button',{name:'Close navigation',exact:true}).click();
  await action('view.update',{patch:{panel:'chat-models'}});await expect(page.getByRole('dialog',{name:'Model & tools',exact:true})).toBeVisible();
  const first=page.getByRole('button',{name:'Inspect document tools',exact:true}),second=page.getByRole('button',{name:'Check library imports',exact:true});await expect(first).toBeVisible();await first.scrollIntoViewIfNeeded();
  const a=await first.boundingBox(),b=await second.boundingBox(),gap=Math.max(b.x-a.x-a.width,a.x-b.x-b.width,b.y-a.y-a.height,a.y-b.y-b.height);assert.ok(gap>=6);metrics.push({width,documentActions:{a,b,gap}});await capture(first.locator('..'),'document-actions-'+width);
  await page.getByRole('button',{name:/^Close (settings|panel)$/}).last().click();
 }
 assert.deepEqual(errors,[]);assert.deepEqual((await page.request.get(fixture.base+'/fixture').then(r=>r.json())).sent,[]);
 await writeFile(output+'/result.json',JSON.stringify({passed:true,servedAssets:served,metrics,scope:'Installed real host; synthetic runtime, unsent appearance library specimen, no paid voice/provider/inspection/test-environment operation',initialWorkspace:initial.selectedWorkspaceId},null,2)+'\n');
}catch(error){originalError=error;await writeFile(output+'/failure.json',JSON.stringify({error:String(error.stack||error),metrics},null,2));await page?.screenshot({path:output+'/failure.png'}).catch(()=>{});throw error}
finally{await finishFixture({label:'Installed spacing',stop:fixture.stop,close:[['browser',()=>browser?.close()]],originalError})}
