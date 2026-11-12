import {showFixtureConversation} from './browser-chat-pages.mjs';
import {readComposerDraft} from './composer-test-helpers.mjs';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {chromium,expect} from '@playwright/test';
import {fixturePython} from './fixture-python.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const navigationSeam=process.env.AMPLIFIER_TEST_NAVIGATION_SEAM==='1';
assert.ok(process.env.AMPLIFIER_TEST_NAVIGATION_SEAM===undefined||navigationSeam,'Unknown navigation-seam selection');
const installed=process.env.AMPLIFIER_TEST_INSTALLED_PACKAGE;
assert.ok(!navigationSeam||installed,'Navigation seam requires installed-package qualification');
const positiveBudget=name=>{
 const raw=process.env[name],value=Number(raw);
 assert.ok(typeof raw==='string'&&/^\d+(?:\.\d+)?$/.test(raw)&&Number.isFinite(value)&&value>0,
  name+' must explicitly bind a positive pixel budget from the accepted canonical contract');
 return value;
};
const seamBudget=navigationSeam?{
 standardNavigation:positiveBudget('AMPLIFIER_TEST_NAV_STANDARD_WIDTH'),
 minimumConversation:positiveBudget('AMPLIFIER_TEST_NAV_MIN_CONVERSATION'),
}:null;
const out=process.env.AMPLIFIER_TEST_ARTIFACTS;if(!out)throw Error('Owned artifacts required');
await mkdir(out,{recursive:true});
if(installed&&process.env.AMPLIFIER_TEST_DEV)throw Error('Installed qualification cannot use Vite/source assets');
const hostEnv={...process.env,PYTHONDONTWRITEBYTECODE:'1'};
delete hostEnv.PYTHONPATH;delete hostEnv.PYTHONHOME;
if(installed)hostEnv.PATH=process.env.AMPLIFIER_TEST_HOST_PATH;
const fixture=spawn(fixturePython(),['-I','-B','-u',root+'tests/fixtures/navigation_waiting_ui_server.py'],
 {stdio:['ignore','pipe','pipe'],env:hostEnv});
const stop=ownFixture(fixture,{label:'Navigation waiting fixture'});
let browser,page,vite,logs='',failure,inspect;const observations=[],errors=[];
const result={status:'running',syntheticDurableReceipts:true,installed:!!installed,observations,errors};
if(navigationSeam)Object.assign(result,{family:'installed-nav-seam',inputBudget:seamBudget});
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
 if(navigationSeam){
  // A single current appearance and actual host projections, not the historical
  // appearance/status matrix. Product budgets must be bound by the caller.
  const initial=await state(),draftBefore=await readComposerDraft(composer),themeBefore=JSON.stringify(initial.theme);
  const originalView={navPinned:initial.view.navPinned,navExpanded:initial.view.navExpanded??false,navWidth:initial.view.navWidth};
  const idsBefore={session:initial.selectedSessionId,workspace:initial.selectedWorkspaceId,client:initial.client.id,
   sessions:initial.sessions.map(row=>row.id).sort()};
  assert.equal(idsBefore.session,ready.ids[0]);assert.equal(originalView.navPinned,true);
  const toggle=page.locator('#navigation-toggle'),navigation=page.locator('.a-nav-slot#workspace-navigation');
  const drawer=page.getByRole('dialog',{name:'Workspaces and chats',exact:true});
  const scrim=page.locator('.a-navigation-scrim');
  const patch=async patch=>{
   const receipt=await page.evaluate(patch=>window.amplifier.dispatch('view.update',{patch}),patch);
   assert.equal(receipt.accepted,true);
  };
  const settle=async()=>{
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   if(page.viewportSize().width>640&&await navigation.isVisible()){
    await expect.poll(()=>navigation.evaluate(node=>Math.abs(
     node.querySelector('.a-nav-rail').getBoundingClientRect().width-node.getBoundingClientRect().width))).toBeLessThan(1);
   }
  };
  const layoutGeometry=()=>page.locator('.a-layout').evaluate(layout=>{
   const nav=document.querySelector('.a-nav-slot#workspace-navigation'),chat=document.querySelector('.a-conversation');
   const box=node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}};
   const style=getComputedStyle(layout),pixels=value=>value==='normal'?0:parseFloat(value);
   const padding={left:pixels(style.paddingLeft),right:pixels(style.paddingRight),
    top:pixels(style.paddingTop),bottom:pixels(style.paddingBottom)};
   const border={left:pixels(style.borderLeftWidth),right:pixels(style.borderRightWidth)};
   const rect=box(layout),contentWidth=rect.width-padding.left-padding.right-border.left-border.right;
   return {viewport:{width:innerWidth,height:innerHeight},layout:rect,padding,border,
    gap:{column:pixels(style.columnGap),row:pixels(style.rowGap)},contentWidth,
    contentLeft:rect.x+padding.left+border.left,contentRight:rect.right-padding.right-border.right,
    header:box(document.querySelector('.a-work-header')),nav:box(nav),rail:box(nav.querySelector('.a-nav-rail')),
    navHidden:nav.hidden,navPosition:getComputedStyle(nav).position,navRole:nav.getAttribute('role'),
    navModal:nav.getAttribute('aria-modal'),chat:box(chat),chatInert:chat.inert||!!chat.closest('[inert]'),
    chatOverflow:chat.scrollWidth>chat.clientWidth+1,
    documentWidth:document.documentElement.scrollWidth,bodyWidth:document.body.scrollWidth,
    focus:{inDrawer:nav.contains(document.activeElement),atToggle:document.activeElement.id==='navigation-toggle',
     inDetails:!!document.activeElement.closest('.a-navigation-flyout'),inert:!!document.activeElement.closest('[inert]')}};
  });
  // Fixture projection changes intentionally replace synthetic receipts. Every
  // navigation/focus/resize step must retain the receipt of its current case.
  let projectionReceipt=(await inspect()).receiptSha256;
  // These automatic reads/reports describe the selected browser's current
  // presentation; they are not preference mutations or execution admission.
  const passivePresentationActions=new Set([
   'appearance.preferences.read','theme.list','appearance.client.report','shell.report',
  ]);
  const observe=async(phase,detail={})=>{
   result.phase='navigation-seam-'+phase;await settle();
   const measured=await layoutGeometry(),current=await state(),host=await inspect();
   const preservation={historyUnchanged:host.historyUnchanged,runtimeStarts:host.runtimeStarts,runtimeSends:host.runtimeSends,
    receiptUnchanged:host.receiptSha256===projectionReceipt,fixtureIdsUnchanged:JSON.stringify(host.ids)===JSON.stringify(ready.ids),
    selectedChatUnchanged:current.selectedSessionId===idsBefore.session,workspaceUnchanged:current.selectedWorkspaceId===idsBefore.workspace,
    clientUnchanged:current.client.id===idsBefore.client,
    sessionIdsUnchanged:JSON.stringify(current.sessions.map(row=>row.id).sort())===JSON.stringify(idsBefore.sessions),
    draftUnchanged:(await readComposerDraft(composer))===draftBefore,themeUnchanged:JSON.stringify(current.theme)===themeBefore};
   const requiredWidth=seamBudget.standardNavigation+seamBudget.minimumConversation+measured.gap.column;
   const sample={id:result.phase,projectionCase:host.mode,inputBudget:seamBudget,geometry:measured,
    fit:{requiredWidth,availableWidth:measured.contentWidth,fits:measured.contentWidth>=requiredWidth},
    actions:actions.map(row=>({action:row.action})),preservation,...detail};
   observations.push(sample);result.lastObservation={...host,seam:sample};
   assert.deepEqual(preservation,{historyUnchanged:true,runtimeStarts:0,runtimeSends:0,
    receiptUnchanged:true,fixtureIdsUnchanged:true,selectedChatUnchanged:true,workspaceUnchanged:true,
    clientUnchanged:true,sessionIdsUnchanged:true,draftUnchanged:true,themeUnchanged:true});
   assert.deepEqual(actions.filter(row=>row.action!=='view.update'&&!passivePresentationActions.has(row.action)),[],
    'Only private view changes and named passive presentation reads/reports are permitted');
   return measured;
  };
  const usable=async measured=>{
   assert.equal(measured.chatInert,false);assert.equal(measured.chatOverflow,false);
   assert.ok(measured.documentWidth<=measured.viewport.width+1&&measured.bodyWidth<=measured.viewport.width+1,
    'No body/document horizontal overflow: '+JSON.stringify(measured));
   await expect(composer).toBeEditable();await expect(composer).toHaveDraft(draftBefore);
  };
  const normalSidebar=async measured=>{
   const required=seamBudget.standardNavigation+seamBudget.minimumConversation+measured.gap.column;
   assert.ok(Number.isFinite(required)&&measured.gap.column>=0&&measured.contentWidth>=required,
    'Measured layout padding/gap must leave the canonical sidebar + conversation budget: '+JSON.stringify({required,measured}));
   assert.equal(measured.navHidden,false);assert.notEqual(measured.navPosition,'fixed');assert.notEqual(measured.navPosition,'absolute');
   assert.notEqual(measured.navRole,'dialog');assert.notEqual(measured.navModal,'true');
   near(measured.nav.width,seamBudget.standardNavigation,'Standard sidebar width');
   near(measured.rail.width,seamBudget.standardNavigation,'Standard rail width');
   near(measured.nav.x,measured.contentLeft,'Sidebar at layout content left');
   near(measured.chat.x-measured.nav.right,measured.gap.column,'Sidebar beside chat with measured gap');
   near(measured.chat.right,measured.contentRight,'Chat at layout content right');
   assert.ok(measured.chat.width>=seamBudget.minimumConversation-1,'Conversation retains canonical minimum');
   await expect(drawer).toHaveCount(0);await expect(scrim).toHaveCount(0);await usable(measured);
  };
  const closed=async measured=>{
   assert.equal(measured.navHidden,true);near(measured.chat.x,measured.contentLeft,'Closed chat left');
   near(measured.chat.width,measured.contentWidth,'Closed chat recovers layout width');
   await expect(drawer).not.toBeVisible();await expect(scrim).toHaveCount(0);await usable(measured);
  };
  const clickToggle=async open=>{
   result.phase='navigation-seam-'+page.viewportSize().width+'-native-'+(open?'open':'close');
   await expect(toggle).toHaveAttribute('aria-label',open?'Open navigation':'Close navigation');
   await expect(toggle).toHaveAttribute('data-action','view.update');
   const before=actions.length;
   const saved=page.waitForResponse(response=>{
    const request=response.request();
    if(new URL(request.url()).pathname!=='/api/actions'||request.method()!=='POST')return false;
    const raw=request.postDataJSON(),body=raw.action==='shell.command'?(raw.args.command||raw.args):raw;
    return body.action==='view.update'&&body.args?.patch?.navExpanded===open;
   });
   const [response]=await Promise.all([saved,toggle.click()]);assert.equal(response.status(),200);
   assert.equal((await response.json()).accepted,true,'Native header view.update is accepted');
   await expect(toggle).toHaveAttribute('aria-label',open?'Close navigation':'Open navigation');
   await expect(toggle).toHaveAttribute('aria-expanded',String(open));
   assert.ok(actions.length>before);assert.ok(actions.slice(before).every(row=>row.action==='view.update'));
  };
  const rows=async label=>{
   await mode('thinking');result.phase='navigation-seam-'+label+'-thinking';await expect(waiting).toHaveCount(0);
   const live=page.locator('.a-live-activity');
   await expect(live.locator('strong')).toHaveText('Thinking');await expect(live.locator('.a-activity-pulse')).toBeVisible();
   projectionReceipt=(await inspect()).receiptSha256;await settle();const thinking=await geometry('live');
   const thinkingLayout=await observe(label+'-thinking',{thinking});await usable(thinkingLayout);
   await mode('long-deferred');result.phase='navigation-seam-'+label+'-long-deferred';await expect(live).toHaveCount(0);
   await expect(waiting).toContainText('Waiting on');await expect(waiting).not.toContainText('Dependency deferred');
   const link=waiting.getByRole('button',{name:ready.longTitle,exact:true});
   await expect(link).toBeVisible();await expect(link).toHaveAttribute('aria-label',ready.longTitle);
   projectionReceipt=(await inspect()).receiptSha256;await settle();const dependency=await geometry('waiting');
   const name=await link.evaluate(node=>({width:node.clientWidth,scroll:node.scrollWidth,
    ellipsis:getComputedStyle(node).textOverflow,whiteSpace:getComputedStyle(node).whiteSpace}));
   const dependencyLayout=await observe(label+'-long-deferred',{thinking,dependency,ellipsis:name});await usable(dependencyLayout);
   assert.deepEqual(dependencyLayout.viewport,thinkingLayout.viewport,'Both projections use the same viewport');
   near(dependency.row.x,thinking.row.x,'Activity/wait left');near(dependency.row.width,thinking.row.width,'Activity/wait width');
   near(dependency.composer.x,thinking.composer.x,'Composer left across projections');
   near(dependency.composer.width,thinking.composer.width,'Composer width across projections');
   for(const [kind,value] of [['Thinking',thinking],['Waiting',dependency]]){
    near(value.row.x,value.composer.x,kind+'/composer left');near(value.row.width,value.composer.width,kind+'/composer width');
    near(value.row.x+value.row.width/2,value.conversation.x+value.conversation.width/2,kind+' centered column');
    assert.ok(Number.isFinite(value.readingWidth)&&value.row.width<=value.readingWidth+1);
    assert.equal(value.overflow,false);
   }
   near(dependency.icon.x,thinking.icon.x,'Clock/pulse left');near(dependency.text.x,thinking.text.x,'Waiting/Thinking text left');
   assert.equal(dependency.color,thinking.color);assert.ok(dependency.mutedToken&&dependency.accentToken);
   assert.equal(dependency.mutedToken,thinking.mutedToken);
   assert.ok(name.scroll>name.width,'Long saved title actually truncates');assert.equal(name.ellipsis,'ellipsis');assert.equal(name.whiteSpace,'nowrap');
   const before=actions.length;await link.hover();await page.keyboard.press('Tab');await link.focus();await expect(link).toBeFocused();
   const tooltip=page.getByRole('tooltip');await expect(tooltip).toHaveText(ready.longTitle);
   const tooltipId=await tooltip.getAttribute('id');assert.ok(tooltipId);
   await expect(link).toHaveAttribute('aria-describedby',tooltipId);
   const focus=await link.evaluate(node=>({style:getComputedStyle(node).outlineStyle,width:getComputedStyle(node).outlineWidth}));
   assert.notEqual(focus.style,'none');assert.ok(parseFloat(focus.width)>0);
   assert.deepEqual(actions.slice(before).filter(row=>!passivePresentationActions.has(row.action)),[],
    'Dependency focus/tooltip never selects, mutates preferences or sends');
   await observe(label+'-tooltip',{fullNameARIA:true,tooltipDescribedBy:true,focus});
  };
  await page.emulateMedia({colorScheme:'light'});await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme','light');
  for(const width of [641,690,750,820]){
   await page.setViewportSize({width,height:1000});await patch({navPinned:false,navExpanded:false,navWidth:seamBudget.standardNavigation});
   const before=await observe('normal-'+width+'-closed');await closed(before);
   await clickToggle(true);await normalSidebar(await observe('normal-'+width+'-open'));
   await rows('normal-'+width);
   await clickToggle(false);const after=await observe('normal-'+width+'-closed-again');await closed(after);
   near(after.chat.x,before.chat.x,'Closing restores conversation left');near(after.chat.width,before.chat.width,'Closing restores conversation width');
  }
  for(const width of [640,390]){
   await page.setViewportSize({width,height:844});
   await patch({navPinned:originalView.navPinned,navExpanded:false});
   const before=await observe('phone-'+width+'-closed');await closed(before);await rows('phone-'+width);
   await clickToggle(true);await expect(drawer).toBeVisible();await expect(drawer).toHaveAttribute('aria-modal','true');
   await expect(scrim).toBeVisible();
   const firstControl=drawer.locator('button:not([disabled]):visible,input:not([disabled]):visible,summary:visible,a[href]:visible,[tabindex="0"]:visible').first();
   await expect(firstControl).toBeFocused();
   const opened=await observe('phone-'+width+'-modal');
   assert.equal(opened.chatInert,true);assert.equal(opened.navRole,'dialog');assert.equal(opened.focus.inert,false);
   assert.ok(opened.header.bottom>0&&opened.header.bottom<opened.viewport.height);
   near(opened.nav.x,0,'Phone drawer left');near(opened.nav.y,opened.header.bottom,'Phone drawer below measured header');
   near(opened.nav.width,opened.viewport.width,'Phone drawer full viewport width');
   near(opened.rail.width,opened.viewport.width,'Phone rail full viewport width');
   near(opened.nav.height,opened.viewport.height-opened.header.bottom,'Phone drawer remaining viewport height');
   assert.ok(opened.documentWidth<=width+1&&opened.bodyWidth<=width+1);
   for(const [index,key] of ['Shift+Tab','Shift+Tab','Tab','Tab'].entries()){
    await page.keyboard.press(key);
    if(index===0||index===2)await expect(toggle).toBeFocused();
    else if(index===3)await expect(firstControl).toBeFocused();
    else assert.equal(await drawer.evaluate(node=>node.contains(document.activeElement)),true);
    const focused=await observe('phone-'+width+'-tab-'+index);
    assert.ok(focused.focus.inDrawer||focused.focus.atToggle);assert.equal(focused.focus.inert,false);assert.equal(focused.chatInert,true);
   }
   const row=drawer.locator('[data-sidebar-section=pinned] .a-nav-chat[data-session-id="'+ready.ids[0]+'"]');
   const more=row.getByRole('button',{name:'Details and actions for Release manager',exact:true});
   await row.hover();await more.focus();await more.click();
   const flyout=page.getByRole('dialog',{name:'Details for Release manager',exact:true});await expect(flyout).toBeVisible();
   await expect.poll(()=>flyout.evaluate(node=>node.contains(document.activeElement))).toBe(true);
   const nested=await observe('phone-'+width+'-details');
   assert.equal(nested.focus.inDetails,true);assert.equal(nested.focus.inert,false);assert.equal(nested.chatInert,true);
   await page.keyboard.press('Escape');await expect(flyout).toHaveCount(0);await expect(drawer).toBeVisible();await expect(more).toBeFocused();
   assert.equal((await observe('phone-'+width+'-child-escape')).chatInert,true);
   await page.keyboard.press('Escape');await expect(navigation).toBeHidden();await expect(toggle).toBeFocused();
   const escaped=await observe('phone-'+width+'-parent-escape');await closed(escaped);
   assert.equal((await state()).view.navPinned,originalView.navPinned,'Modal Escape preserves pinned intent');
   near(escaped.chat.width,before.chat.width,'Escape recovers conversation width');
   await clickToggle(true);await expect(firstControl).toBeFocused();await clickToggle(false);await expect(toggle).toBeFocused();
   await closed(await observe('phone-'+width+'-repeat-close'));
   await clickToggle(true);await expect(firstControl).toBeFocused();
   await page.setViewportSize({width:820,height:1000});
   await expect.poll(()=>page.locator('.a-conversation').evaluate(node=>node.inert||!!node.closest('[inert]'))).toBe(false);
   await normalSidebar(await observe('phone-'+width+'-modal-to-820'));
   assert.equal((await state()).view.navPinned,originalView.navPinned,'Resize releases inertness without changing pinned intent');
  }
  await patch({navPinned:originalView.navPinned,navExpanded:originalView.navExpanded});
  await normalSidebar(await observe('restored-original-intent'));
  const finalState=await state();
  assert.equal(finalState.view.navPinned,originalView.navPinned);assert.equal(finalState.view.navExpanded,originalView.navExpanded);
  assert.equal(JSON.stringify(finalState.theme),themeBefore);
  assert.equal(finalState.selectedSessionId,idsBefore.session);assert.equal(finalState.selectedWorkspaceId,idsBefore.workspace);
  assert.equal(finalState.client.id,idsBefore.client);assert.deepEqual(finalState.sessions.map(row=>row.id).sort(),idsBefore.sessions);
  await expect(composer).toHaveDraft(draftBefore);
  assert.deepEqual(actions.filter(row=>row.action!=='view.update'&&!passivePresentationActions.has(row.action)),[]);
  const final=await inspect();assert.equal(final.receiptSha256,projectionReceipt,'Final saved receipt unchanged since the last projection');
  assert.deepEqual(final.ids,ready.ids);
  observations.push({id:'installed-nav-seam-complete',widths:[641,690,750,820,640,390],scheme:'light',
   actions:actions.map(row=>({action:row.action})),historyUnchanged:final.historyUnchanged,
   runtimeStarts:final.runtimeStarts,runtimeSends:final.runtimeSends,receiptUnchanged:true,draftPreserved:true,pinnedIntentPreserved:true});
 }else{
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
    await expect(link).toBeVisible();await expect(link).toHaveAttribute('aria-label',ready.longTitle);
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
    await expect(page.getByRole('tooltip')).toHaveText(ready.longTitle);
    await expect(link).toHaveAttribute('aria-describedby',await page.getByRole('tooltip').getAttribute('id'));
    const focus=await link.evaluate(node=>({style:getComputedStyle(node).outlineStyle,width:getComputedStyle(node).outlineWidth}));
    assert.notEqual(focus.style,'none');assert.ok(parseFloat(focus.width)>0);
    await passive(before,receipt);
    if(width>=1280){
     const more=nav.locator('.a-nav-chat[data-session-id="'+ready.ids[0]+'"]')
      .getByRole('button',{name:'Details and actions for Release manager',exact:true});
     await nav.locator('.a-nav-chat[data-session-id="'+ready.ids[0]+'"]').hover();
     await more.click();
     const flyout=page.getByRole('dialog',{name:'Details for Release manager',exact:true});
     const flyoutContent=flyout.locator('.a-dependency-content');
     await expect(flyoutContent).toContainText('Waiting on');await expect(flyoutContent).not.toContainText('Dependency deferred');
     const flyoutLink=flyoutContent.getByRole('button',{name:ready.longTitle,exact:true});
     // Flyout focus activates the shared tooltip, which consumes native title.
     // Its full accessible name and associated tooltip remain the real promise.
     await expect(flyoutLink).toHaveAttribute('aria-label',ready.longTitle);
     await flyoutLink.focus();await expect(flyoutLink).toBeFocused();
     await expect(page.getByRole('tooltip')).toHaveText(ready.longTitle);
     await expect(flyoutLink).toHaveAttribute('aria-describedby',await page.getByRole('tooltip').getAttribute('id'));
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
  await nav.locator('.a-nav-chat[data-session-id="'+ready.ids[0]+'"]').hover();
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
 }
 const final=await inspect();assert.equal(final.historyUnchanged,true);assert.equal(final.runtimeStarts,0);assert.equal(final.runtimeSends,0);
 assert.deepEqual(actions.filter(row=>row.action.startsWith('conversation.')||row.action.startsWith('worker.')),[]);
 assert.deepEqual(errors,[]);observations.push({id:navigationSeam?'installed-nav-seam-preservation':'selection-draft-keyboard-and-status',passed:true});
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
   mode:navigationSeam?'installed-nav-seam':installed?'installed':process.env.AMPLIFIER_TEST_DEV?'dev':'built'});
  await writeFile(out+'/fixture.log',logs);
  await writeFile(out+'/observations.json',JSON.stringify(result,null,2)+'\n');
 }
}