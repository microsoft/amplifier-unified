import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {chromium,expect} from '@playwright/test';
import {fixturePython} from './fixture-python.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const out=process.env.AMPLIFIER_TEST_ARTIFACTS;if(!out)throw Error('Owned artifacts required');
await mkdir(out,{recursive:true});
const installed=process.env.AMPLIFIER_TEST_INSTALLED_PACKAGE;
if(installed&&process.env.AMPLIFIER_TEST_DEV)throw Error('Installed qualification cannot use Vite/source assets');
const hostEnv={...process.env,PYTHONDONTWRITEBYTECODE:'1'};
delete hostEnv.PYTHONPATH;delete hostEnv.PYTHONHOME;
if(installed)hostEnv.PATH=process.env.AMPLIFIER_TEST_HOST_PATH;
const fixture=spawn(fixturePython(),['-I','-B','-u',root+'tests/fixtures/navigation_waiting_ui_server.py'],
 {stdio:['ignore','pipe','pipe'],env:hostEnv});
const stop=ownFixture(fixture,{label:'Navigation waiting fixture'});
let browser,page,vite,logs='',failure,inspect;const observations=[],errors=[];
const result={status:'running',syntheticDurableReceipts:true,installed:!!installed,observations,errors};
fixture.stderr.on('data',data=>logs=(logs+data).slice(-8000));
try{
 const ready=await new Promise((resolve,reject)=>{
  let pending='';
  const cleanup=()=>{clearTimeout(timer);fixture.off('error',failed);fixture.off('exit',exited);fixture.stdout.off('data',data)};
  const failed=error=>{cleanup();reject(error)};
  const exited=(code,signal)=>failed(Error('Fixture exited '+(code??signal)+': '+logs));
  const data=chunk=>{
   pending+=chunk;
   for(let end;(end=pending.indexOf('\n'))>=0;){
    const line=pending.slice(0,end);pending=pending.slice(end+1);
    let row;try{row=JSON.parse(line)}catch{continue}
    if(row.fixture==='navigation-waiting'){
     if(row.pid!==fixture.pid||!/^http:\/\/127\.0\.0\.1:[1-9]\d{0,4}$/.test(row.url)){
      failed(Error('Invalid navigation waiting readiness receipt'));return;
     }
     cleanup();resolve(row);return;
    }
   }
   if(pending.length>8000)failed(Error('Fixture readiness exceeded bound'));
  };
  const timer=setTimeout(()=>failed(Error('Fixture timeout')),60000);
  fixture.once('error',failed);fixture.once('exit',exited);fixture.stdout.on('data',data);
 });
 if(process.env.AMPLIFIER_TEST_DEV){
  const {createServer}=await import('vite');
  const target=ready.url;vite=await createServer({configFile:false,root:process.env.AMPLIFIER_TEST_CLIENT||root+'.build/web-client',server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,r=>r.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
  await vite.listen();
 }
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1280,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));
 if(installed){
  const record=JSON.parse(await readFile(process.env.AMPLIFIER_TEST_ASSEMBLY_RECORD,'utf8'));
  const verifiedBody=async(path,expected)=>{
   assert.ok(expected,'Assembly must bind '+path);
   const response=await page.request.get(ready.url+path);assert.ok(response.ok(),path);
   const bytes=await response.body();assert.equal(bytes.length,expected.bytes,path);
   assert.equal(createHash('sha256').update(bytes).digest('hex'),expected.sha256,path);
   return bytes;
  };
  const component=JSON.parse((await verifiedBody('/client-component.json',record.client.component)).toString('utf8'));
  assert.deepEqual(component,record.client.manifest);
  assert.equal(component.source.revision,process.env.AMPLIFIER_TEST_CLIENT_REVISION);
  const build=JSON.parse((await verifiedBody('/build.json',component.assets['build.json'])).toString('utf8'));
  assert.deepEqual(build,component.build);
  const html=(await verifiedBody('/',component.assets['index.html'])).toString('utf8');
  const paths=[...html.matchAll(/(?:src|href)="(\/[^"]+\.(?:js|css))"/g)].map(match=>match[1]);
  assert.ok(paths.some(path=>path.endsWith('.js')));assert.ok(paths.some(path=>path.endsWith('.css')));
  for(const path of new Set(paths))await verifiedBody(path,component.assets[path.slice(1)]);
  Object.assign(result,{servedEntrypointAssetHashes:true,selectedClient:component.source,clientBuild:build});
 }
 const actions=[];
 page.on('request',request=>{
  if(new URL(request.url()).pathname!=='/api/actions'||request.method()!=='POST')return;
  const raw=request.postDataJSON(),body=raw.action==='shell.command'?(raw.args.command||raw.args):raw;
  actions.push({action:body.action,...(body.action==='session.select'?{id:body.args.id}:{})});
 });
 inspect=async()=>{
  const response=await page.request.get(ready.url+'/fixture/wait',{timeout:5000});assert.equal(response.status(),200);
  const observed=await response.json();result.lastObservation=observed;return observed;
 };
 const metadata=await inspect();Object.assign(ready,metadata);
 if(installed){assert.equal(metadata.identity.installed,true);assert.equal(metadata.identity.nodeUnavailable,true);assert.deepEqual(metadata.identity.client,result.selectedClient)}
 await page.goto(vite?.resolvedUrls.local[0]||ready.url);await showFixtureConversation(page,{navPinned:true});
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await expect(composer).toBeEditable();await composer.fill('Keep this draft while inspecting waits');
 const waiting=page.getByRole('region',{name:'Chat dependencies'});
 const mode=async value=>{result.phase=value;const response=await page.request.post(ready.url+'/fixture/wait',{data:{mode:value}});assert.equal(response.status(),200)};
 const select=id=>page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),id);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 await expect(waiting).toContainText('Waiting on Unified: Readiness');
 await expect(waiting.getByRole('button',{name:'Unified: Readiness',exact:true})).toBeVisible();
 const nav=page.locator('[data-sidebar-section=pinned]');
 await expect(nav.locator('.a-nav-chat[data-session-id="'+ready.ids[0]+'"] [data-kind=waiting]')).toHaveCount(1);
 assert.equal(await waiting.locator('.a-progress-spinner').count(),0);
 await page.screenshot({path:out+'/one-desktop.png'});
 const columns=await nav.evaluate(el=>{
  const measure=selector=>{const node=el.querySelector(selector),r=node.getBoundingClientRect();return {x:r.x,width:r.width,height:r.height}};
  return {chat:measure('.a-nav-chat .a-nav-chat-label'),workspace:measure('.a-workspace-label'),
          chatIcon:measure('.a-nav-chat .a-navigation-status'),workspaceIcon:measure('.a-workspace-row .a-navigation-status')};
 });
 observations.push({id:'shared-columns',columns});
 // The semantic follow-up does not change the separately evolved workspace
 // badge layout. Keep the original alignment qualification as its own scope.
 if(!process.env.AMPLIFIER_TEST_DEPENDENCY_SEMANTICS){
  assert.ok(Math.abs(columns.chat.x-columns.workspace.x)<1,JSON.stringify(columns));
  assert.ok(Math.abs(columns.chatIcon.x-columns.workspaceIcon.x)<1,JSON.stringify(columns));
 }
 assert.equal(await page.locator('.a-workspace-pin-mark').count(),0);
 const passive=async(before,receipt)=>{
  assert.deepEqual(actions.slice(before),[],'Inspection/focus/expansion must not dispatch actions');
  const observed=await inspect();
  assert.equal(observed.historyUnchanged,true);assert.equal(observed.runtimeStarts,0);assert.equal(observed.runtimeSends,0);
  assert.equal((await state()).selectedSessionId,ready.ids[0]);
  if(receipt)assert.equal(observed.receiptSha256,receipt);
  await expect(composer).toHaveDraft('Keep this draft while inspecting waits');
 };
 // Same actual selected chat, sequential source observations: Thinking and
 // dependency waiting are deliberately not fabricated simultaneous client state.
 const themeBefore=JSON.stringify((await state()).theme);
 const presented=await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{scheme:'system'}}));
 assert.equal(presented.accepted,true);
 const geometry=async(kind)=>page.locator('.a-conversation').evaluate((node,kind)=>{
  const rect=selector=>{const element=selector===':scope'?node:node.querySelector(selector),r=element.getBoundingClientRect();
   return {x:r.x,width:r.width,right:r.right,height:r.height}};
  const style=getComputedStyle(node.closest('#amp-one'));
  return {row:rect(kind==='live'?'.a-live-activity':'.a-dependency-waiting'),
   icon:rect(kind==='live'?'.a-activity-pulse':'.a-dependency-content>svg'),
   text:rect(kind==='live'?'.a-live-activity>div':'.a-dependency-single'),
   composer:rect('.a-composer'),conversation:rect(':scope'),
   // The live status container and dependency prose share the muted role;
   // Thinking's strong title intentionally uses the separate ink role.
   color:getComputedStyle(node.querySelector(kind==='live'?'.a-live-activity':'.a-dependency-content')).color,
   mutedToken:style.getPropertyValue('--a-muted').trim(),accentToken:style.getPropertyValue('--a-accent').trim(),
   readingWidth:parseFloat(style.getPropertyValue('--a-reading-width')),
   overflow:node.scrollWidth>node.clientWidth+1};
 },kind);
 const near=(a,b,label)=>assert.ok(Math.abs(a-b)<=1,label+': '+a+' vs '+b);
 for(const scheme of ['light','dark']){
  await page.emulateMedia({colorScheme:scheme});
  await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);
  for(const width of [3480,1280,390,320]){
   await page.setViewportSize({width,height:width<500?844:1000});
   const view=await page.evaluate(navPinned=>window.amplifier.dispatch('view.update',{patch:{navPinned}}),width>=1280);
   assert.equal(view.accepted,true);
   await mode('thinking');await expect(waiting).toHaveCount(0);
   const live=page.locator('.a-live-activity');
   await expect(live.locator('strong')).toHaveText('Thinking');
   await expect(live.locator('.a-activity-pulse')).toBeVisible();
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   const thinking=await geometry('live');
   if(width===3480){
    assert.ok(Number.isFinite(thinking.readingWidth)&&thinking.readingWidth>0,'A finite current reading cap is observed');
    assert.ok(thinking.conversation.width>thinking.readingWidth+400,
     'Wide production conversation must genuinely exceed its reading cap: '+JSON.stringify(thinking));
    assert.ok(thinking.row.width<thinking.conversation.width-400,
     'Wide production activity is a capped column, not a full-pane fixture');
   }
   for(const value of ['long','long-deferred']){
    await mode(value);await expect(live).toHaveCount(0);await expect(waiting).toContainText('Waiting on');
    await expect(waiting).not.toContainText('Dependency deferred');
    await expect(waiting).not.toContainText('Waiting for');
    const link=waiting.getByRole('button',{name:ready.longTitle,exact:true});
    await expect(link).toBeVisible();await expect(link).toHaveAttribute('title',ready.longTitle);
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const dependency=await geometry('waiting');
    observations.push({id:'host-layout-'+scheme+'-'+width+'-'+value,
     narrative:'Sequential real host Thinking then waiting, same selected chat/viewport/current theme',thinking,dependency});
    near(dependency.row.x,thinking.row.x,'Activity/wait left');
    near(dependency.row.width,thinking.row.width,'Activity/wait width');
    near(dependency.row.x,dependency.composer.x,'Composer reading-column left');
    near(dependency.row.width,dependency.composer.width,'Composer reading-column width');
    assert.ok(Number.isFinite(dependency.readingWidth)&&dependency.row.width<=dependency.readingWidth+1,'Current theme reading-width budget');
    if(width===3480){
     assert.ok(dependency.conversation.width>dependency.readingWidth+400,'Waiting exercises the same genuinely wide production pane');
     assert.ok(dependency.row.x>dependency.conversation.x+200,'Waiting is visibly inset from the full-pane left edge');
    }
    near(dependency.row.x+dependency.row.width/2,dependency.conversation.x+dependency.conversation.width/2,'Centered column');
    near(dependency.icon.x,thinking.icon.x,'Clock/pulse left');
    near(dependency.text.x,thinking.text.x,'Waiting/Thinking text left');
    assert.equal(dependency.overflow,false);assert.equal(dependency.color,thinking.color);
    assert.ok(dependency.mutedToken&&dependency.accentToken);assert.equal(dependency.mutedToken,thinking.mutedToken);
    const name=await link.evaluate(node=>({width:node.clientWidth,scroll:node.scrollWidth,
     ellipsis:getComputedStyle(node).textOverflow,whiteSpace:getComputedStyle(node).whiteSpace}));
    observations.push({id:'ellipsis-'+scheme+'-'+width+'-'+value,name});
    assert.ok(name.scroll>name.width,'Long saved title actually truncates');assert.equal(name.ellipsis,'ellipsis');
    assert.equal(name.whiteSpace,'nowrap');
    const receipt=(await inspect()).receiptSha256,before=actions.length;
    await link.hover();await page.keyboard.press('Tab');await link.focus();await expect(link).toBeFocused();
    const focus=await link.evaluate(node=>({style:getComputedStyle(node).outlineStyle,width:getComputedStyle(node).outlineWidth}));
    assert.notEqual(focus.style,'none');assert.ok(parseFloat(focus.width)>0);
    await passive(before,receipt);
    if(width>=1280){
     const more=nav.locator('.a-nav-chat[data-session-id="'+ready.ids[0]+'"]')
      .getByRole('button',{name:'Details and actions for Release manager',exact:true});
     await more.click();
     const flyout=page.getByRole('dialog',{name:'Details for Release manager',exact:true});
     const flyoutContent=flyout.locator('.a-dependency-content');
     await expect(flyoutContent).toContainText('Waiting on');await expect(flyoutContent).not.toContainText('Dependency deferred');
     const flyoutLink=flyoutContent.getByRole('button',{name:ready.longTitle,exact:true});
     await expect(flyoutLink).toHaveAttribute('title',ready.longTitle);
     await flyoutLink.focus();await expect(flyoutLink).toBeFocused();
     assert.ok(await flyoutLink.evaluate(node=>node.scrollWidth>node.clientWidth),'Flyout retains ellipsis');
     await page.keyboard.press('Escape');await expect(flyout).toHaveCount(0);await expect(more).toBeFocused();
     await passive(before,receipt);
    }
    await page.screenshot({path:out+`/waiting-${scheme}-${width}-${value}.png`});
   }
  }
 }
 assert.equal(JSON.stringify((await state()).theme),themeBefore,'Media changes retain selected theme bytes');
 await page.setViewportSize({width:1280,height:1000});
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{navPinned:true}}));
 await mode('long-deferred');await expect(waiting.getByRole('button',{name:ready.longTitle,exact:true})).toBeVisible();
 const deferredBefore=actions.length,deferredReceipt=(await inspect()).receiptSha256;
 const deferredLink=waiting.getByRole('button',{name:ready.longTitle,exact:true});
 await deferredLink.focus();await deferredLink.press('Enter');
 await expect.poll(async()=>(await state()).selectedSessionId).toBe(ready.ids[1]);
 assert.deepEqual(actions.slice(deferredBefore),[{action:'session.select',id:ready.ids[1]}],'Deferred keyboard link only selects its target');
 assert.equal((await inspect()).receiptSha256,deferredReceipt);
 await select(ready.ids[0]);await expect(composer).toHaveDraft('Keep this draft while inspecting waits');
 await mode('one');await expect(waiting).toContainText('Waiting on Unified: Readiness');
 const options=page.locator('.a-work-footer').getByRole('button',{name:'App options',exact:true});
 const beforeMenu=actions.length,menuReceipt=(await inspect()).receiptSha256;
 await options.click();
 const menu=page.getByRole('group',{name:'App options',exact:true});
 const menuLabels=['Activity','Send feedback','Automations','What agents see','Settings'];
 await expect(menu.getByRole('button')).toHaveCount(menuLabels.length);
 for(const [index,label] of menuLabels.entries())await expect(menu.getByRole('button').nth(index)).toContainText(label);
 await expect(menu.getByRole('button',{name:'What agents see',exact:true})).toHaveCount(1);
 await expect(menu.getByRole('button',{name:/^(Agent view|Agent context|Agent state|Retry)$/})).toHaveCount(0);
 await expect(menu.locator('hr,[role=separator]')).toHaveCount(0);
 await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);await expect(options).toBeFocused();
 await passive(beforeMenu,menuReceipt);observations.push({id:'app-options-label-order',labels:menuLabels,passive:true});
 const workspace=nav.locator('.a-workspace-row');
 await workspace.hover();
 await workspace.getByRole('button',{name:'Details and actions for Unified: Launch Readiness',exact:true}).click();
 await expect(page.getByRole('dialog',{name:'Details for Unified: Launch Readiness',exact:true}).getByRole('button',{name:'Unpin workspace '+ready.path,exact:true})).toBeVisible();
 await page.keyboard.press('Escape');
 await mode('many');await expect(waiting.locator('summary')).toHaveText('Waiting on 2 chats');
 await expect(waiting.locator('details')).not.toHaveAttribute('open');
 const beforeExpand=actions.length,expandReceipt=(await inspect()).receiptSha256;
 await waiting.locator('summary').focus();await page.keyboard.press('Enter');
 await expect(waiting.getByRole('button',{name:'Second worker with a long descriptive title',exact:true})).toBeVisible();
 await passive(beforeExpand,expandReceipt);
 assert.equal((await state()).selectedSessionId,ready.ids[0]);await expect(composer).toHaveDraft('Keep this draft while inspecting waits');
 await page.screenshot({path:out+'/many-desktop.png'});
 const beforeSelect=actions.length,selectionReceipt=(await inspect()).receiptSha256;
 await waiting.getByRole('button',{name:'Unified: Readiness',exact:true}).click();
 await expect.poll(async()=>(await state()).selectedSessionId).toBe(ready.ids[1]);
 assert.deepEqual(actions.slice(beforeSelect),[{action:'session.select',id:ready.ids[1]}],'Target click only selects its exact chat');
 assert.equal((await inspect()).receiptSha256,selectionReceipt);
 await expect(waiting).toHaveCount(0);await select(ready.ids[0]);await expect(composer).toHaveDraft('Keep this draft while inspecting waits');
 for(const [value,kind,label] of [
  ['ready','waiting','Dependency result reported'],
  ['returned','waiting','Dependency result ready'],
  ['explicit-return','waiting','Dependency result ready'],
  ['backpressured','waiting','Waiting for dependency delivery'],
  ['deferred','waiting','Dependency deferred'],
  ['failed','attention','Dependency failed'],
  ['declined','attention','Dependency declined'],
  ['unknown','attention','Dependency outcome unknown'],
  ['decision','attention','Dependency decision requested'],
 ]){
  await mode(value);await expect(waiting).toBeVisible();
  await expect.poll(async()=>(await state()).selectedActivity?.label).toBe(label);
  const marker=nav.locator('.a-nav-chat[data-session-id="'+ready.ids[0]+'"] .a-navigation-status');
  await expect(marker).toHaveAttribute('data-kind',kind);
  await expect(marker).toHaveAttribute('aria-label',value==='deferred'?'Waiting on Unified: Readiness':label);
  if(value==='deferred'){await expect(waiting).toContainText('Waiting on Unified: Readiness');await expect(waiting).not.toContainText(label)}
  else await expect(waiting).toContainText(label);
  assert.equal(await marker.locator('.a-progress-spinner').count(),0);
  if(kind==='waiting')await expect(marker.locator('polyline[points="12 6 12 12 16.5 12"]')).toHaveCount(1);
  else await expect(marker.locator(value==='decision'?'.lucide-circle-help':'.lucide-circle-alert')).toHaveCount(1);
  await expect(composer).toHaveDraft('Keep this draft while inspecting waits');
  observations.push({id:value,selectedActivity:(await state()).selectedActivity});
  if(['ready','explicit-return','failed'].includes(value))await page.screenshot({path:out+'/'+value+'-desktop.png'});
 }
 for(const [value,label] of [['failed','Dependency failed'],['unknown','Dependency outcome unknown'],
  ['decision','Dependency decision requested'],['returned','Dependency result ready']]){
  await mode('one');await expect(waiting.locator('.a-dependency-single')).toBeVisible();
  await mode(value+'-many');
  const details=waiting.locator('details');
  await expect(details).not.toHaveAttribute('open');
  await expect(details.locator('summary')).toContainText(label);
  await expect(details.locator('summary')).not.toContainText('Waiting on');
  const receipt=(await inspect()).receiptSha256,before=actions.length;
  const more=nav.locator('.a-nav-chat[data-session-id="'+ready.ids[0]+'"]')
   .getByRole('button',{name:'Details and actions for Release manager',exact:true});
  await more.click();
  const flyout=page.getByRole('dialog',{name:'Details for Release manager',exact:true});
  await expect(flyout.locator('.a-dependency-content details')).not.toHaveAttribute('open');
  await expect(flyout.locator('.a-dependency-content summary')).toContainText(label);
  await expect(flyout.locator('.a-dependency-content summary')).not.toContainText('Waiting on');
  await page.keyboard.press('Escape');await expect(flyout).toHaveCount(0);
  await details.locator('summary').focus();await page.keyboard.press('Enter');
  await expect(waiting.getByRole('button',{name:'Unified: Readiness',exact:true})).toBeVisible();
  await expect(waiting.getByRole('button',{name:'Second worker with a long descriptive title',exact:true})).toBeVisible();
  await passive(before,receipt);observations.push({id:value+'-collapsed-and-flyout',label,preserved:true});
 }
 for(const value of ['paused','stopped','question','new-input','unclassified']){
  await mode(value);await expect(waiting).toHaveCount(0);
  observations.push({id:value,selectedActivity:(await state()).selectedActivity});
  if(['new-input','unclassified'].includes(value)){
   const marker=nav.locator('.a-nav-chat[data-session-id="'+ready.ids[0]+'"] .a-navigation-status');
   await expect(marker).toHaveAttribute('data-kind','idle');
   await expect(marker).toHaveAttribute('title','Blocked — details unavailable');
   assert.equal(await marker.locator('.lucide-circle-alert').count(),0);
  }
 }
 await mode('recipient-error');await expect(waiting).toContainText('error');
 await mode('many');await expect(waiting.locator('summary')).toBeVisible();
 for(const width of [390,320]){
  await page.setViewportSize({width,height:844});
  await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{navPinned:false}}));
  if(!await waiting.getByRole('button',{name:'Unified: Readiness',exact:true}).isVisible())await waiting.locator('summary').click();
  await expect(waiting.getByRole('button',{name:'Unified: Readiness',exact:true})).toBeVisible();
  const geometry=await waiting.evaluate(el=>({width:el.clientWidth,scroll:el.scrollWidth,height:el.getBoundingClientRect().height}));
  assert.ok(geometry.scroll<=geometry.width+1,JSON.stringify(geometry));
  await expect(composer).toHaveDraft('Keep this draft while inspecting waits');
  observations.push({id:'mobile-'+width,geometry});
  await page.screenshot({path:out+'/many-mobile-'+width+'.png'});
 }
 const final=await inspect();assert.equal(final.historyUnchanged,true);assert.equal(final.runtimeStarts,0);assert.equal(final.runtimeSends,0);
 assert.deepEqual(actions.filter(row=>row.action.startsWith('conversation.')||row.action.startsWith('worker.')),[]);
 assert.deepEqual(errors,[]);observations.push({id:'selection-draft-keyboard-and-status',passed:true});
}catch(error){
 failure=error;
 if(inspect)try{await inspect()}catch(observationError){result.observationError=String(observationError)}
 await page?.screenshot({path:out+'/failure.png'}).catch(()=>{});throw error;
}
finally{
 try{
  await finishFixture({label:'Navigation waiting',close:[['Browser',()=>browser?.close()],['Vite',()=>vite?.close()]],stop});
  result.status=failure?'failed':'passed';
 }catch(error){result.status='failed';result.cleanupError=String(error);if(!failure)throw error}
 finally{
  Object.assign(result,{failure:failure?.message,fixtureExit:{code:fixture.exitCode,signal:fixture.signalCode},
   mode:installed?'installed':process.env.AMPLIFIER_TEST_DEV?'dev':'built'});
  await writeFile(out+'/fixture.log',logs);
  await writeFile(out+'/observations.json',JSON.stringify(result,null,2)+'\n');
 }
}