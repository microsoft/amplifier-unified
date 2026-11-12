import {spawn} from 'node:child_process';
import {prepareCanvasReaderInput,recordCanvasInputFailure} from './canvas-reader-input.mjs';
import {fileURLToPath} from 'node:url';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join,resolve,isAbsolute,basename} from 'node:path';
import {createHash} from 'node:crypto';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {openSettingsPage,settingsButton} from '../../.build/web-client/tests/browser-settings.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {startFixture} from './fixture-readiness.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {readComposerDraft} from './composer-test-helpers.mjs';

// The ordinary journey below remains the default. This focused installed-wheel
// mode adds host/packaged-entry evidence, not another source/Vite component run.
async function installedPr36(){
 const env=process.env;
 for(const key of ['AMPLIFIER_TEST_PYTHON','AMPLIFIER_TEST_INSTALLED_IDENTITY','AMPLIFIER_TEST_PACKAGE_ROOT','AMPLIFIER_TEST_ARTIFACTS'])
  assert.ok(env[key]&&isAbsolute(env[key]),key+' must select an absolute owned path');
 assert.equal(env.AMPLIFIER_TEST_INSTALLED,'1','PR36 never falls back to source mode');
 assert.ok(!env.AMPLIFIER_TEST_SHARED_WORK_HOME,'Fresh disposable fixture storage is required');
 if(env.AMPLIFIER_TEST_ROOT)assert.ok(isAbsolute(env.AMPLIFIER_TEST_ROOT),'AMPLIFIER_TEST_ROOT must be absolute');
 const root=resolve(env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url)));
 const fixturePath=env.AMPLIFIER_TEST_SERVER||join(root,'tests/fixtures/empty_host_ui_server.py');
 assert.ok(isAbsolute(fixturePath),'AMPLIFIER_TEST_SERVER must select an absolute fixture path');
 assert.equal(basename(fixturePath),'empty_host_ui_server.py','Use the maintained fixture, with configured_browser.py staged beside it');
 const output=env.AMPLIFIER_TEST_ARTIFACTS;
 await mkdir(output,{recursive:true});
 const selectedClient='739f57cd2228319178294652867f2fff4a8e8c3c';
 const identity=JSON.parse(await readFile(env.AMPLIFIER_TEST_INSTALLED_IDENTITY,'utf8'));
 const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
 const evidence={scope:'PR36 installed current wheel; real AppService, auth/actions/SSE and bundled entry; synthetic runtime/discovery and disposable chats only',
  selectedClient,wheelSha256:identity.wheelSha256,geometry:[],timing:[],appearance:{},actions:[],pageErrors:[],cleanup:{complete:false},
  limits:['No provider/native-runtime acceptance.','Hidden-page input is synthetic document visibility, not OS background-tab qualification.',
   'PNG files and numeric DOM observations are evidence, not a pixel-review claim.','Source 182 and temporal motion 6 are reused by the parent, not rerun here.']};
 const childEnv={...env,PYTHONDONTWRITEBYTECODE:'1'};
 delete childEnv.PYTHONPATH;delete childEnv.PYTHONHOME;
 let owned,browser,page,context,base,failure,releaseNavigation,logs='';
 let navigationGate=null;
 try{
  owned=await startFixture({command:env.AMPLIFIER_TEST_PYTHON,
   args:['-I','-B','-u',fixturePath,'--synthetic-discovery','--appearance-records'],
   env:childEnv,tag:'empty-host-ui',name:'PR36 installed archive',timeoutMs:60000});
  base=owned.base;
  owned.fixture.stdout.on('data',chunk=>{logs+=chunk});
  owned.fixture.stderr.on('data',chunk=>{logs+=chunk});
  browser=await chromium.launch({headless:true,
   ...(env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),
   args:JSON.parse(env.AMPLIFIER_CHROMIUM_ARGS||
    (env.CHROMIUM_SINGLE_PROCESS==='1'?'["--single-process","--no-zygote","--no-sandbox"]':'[]'))});
  context=await browser.newContext({viewport:{width:1280,height:900},reducedMotion:'no-preference',
   extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  context.on('page',p=>p.on('pageerror',error=>evidence.pageErrors.push({name:error.name,message:error.message,stack:error.stack})));
  page=await context.newPage();
  page.on('request',request=>{
   if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
   const raw=request.postDataJSON(),body=raw.action==='shell.command'?(raw.args.command||raw.args):raw;
   evidence.actions.push({action:body.action,args:body.args});
  });
  const get=async path=>{const response=await context.request.get(base+path);assert.ok(response.ok(),path);return response.json()};
  async function entryHashes(){
   const served={};
   const fetchBytes=async(path,name,asset)=>{
    const response=await context.request.get(base+path);assert.equal(response.status(),200,path);
    const bytes=await response.body(),sha256=digest(bytes);
    assert.equal(sha256,identity.files['amplifier_web/static/'+name],'Installed identity binds '+name);
    if(asset){assert.equal(bytes.length,asset.bytes,name);assert.equal(sha256,asset.sha256,name)}
    served[name]={bytes:bytes.length,sha256};return bytes;
   };
   const component=JSON.parse((await fetchBytes('/client-component.json','client-component.json')).toString('utf8'));
   assert.equal(component.source.revision,selectedClient,'Exercise the exact accepted client, not an older selected copy');
   const build=JSON.parse((await fetchBytes('/build.json','build.json',component.assets['build.json'])).toString('utf8'));
   assert.deepEqual(build,component.build);
   const html=(await fetchBytes('/','index.html',component.assets['index.html'])).toString('utf8');
   const paths=[...html.matchAll(/(?:src|href)=["'](\/[^"']+\.(?:js|css)(?:\?[^"']*)?)["']/g)].map(match=>match[1]);
   assert.ok(paths.some(path=>new URL(path,base).pathname.endsWith('.js')));
   assert.ok(paths.some(path=>new URL(path,base).pathname.endsWith('.css')));
   for(const path of new Set(paths)){
    const name=new URL(path,base).pathname.slice(1);
    assert.ok(component.assets[name],name+' belongs to the selected bundle');
    await fetchBytes(path,name,component.assets[name]);
   }
   return {source:component.source,build,served};
  }
  evidence.entryBefore=await entryHashes();
  const fixtureBefore=await get('/fixture'),recordsBefore=await get('/fixture/appearance-records');
  assert.equal(fixtureBefore.installedPackageVerified,true);
  assert.equal(fixtureBefore.installedIdentity.verified,true);
  assert.equal(recordsBefore.legacy.unchanged,true);
  evidence.fixtureBefore=fixtureBefore;
  await page.goto(base);
  await page.waitForFunction(()=>{const a=window.amplifier?.getState()?.appearance;return a?.availability==='connected'&&a.local?.reported===true&&!a.local.error});
  await expect(page.locator('#amp-one')).toBeVisible();
  const state=()=>page.evaluate(()=>window.amplifier.getState());
  const action=async(name,args={})=>{
   const receipt=await page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
   assert.equal(receipt.accepted,true,name+' accepted');return receipt.result;
  };
  const profiles=async()=>{
   const a=(await state()).appearance;
   return {activeScope:a.activeScope,shared:a.shared,local:{values:a.local.values,storageVersion:a.local.storageVersion},
    effective:a.effective,theme:a.theme};
  };
  const profilesBefore=await profiles();
  const workspace=(await state()).workspaces.find(row=>row.path);
  assert.ok(workspace,'Use only the fresh fixture workspace');
  const title='PR36 synthetic archived conversation with a long wrapping title '.repeat(3).trim();
  async function waitSelectedHistory(id){
   await page.waitForFunction(id=>{
    const value=window.amplifier.getState(),row=value.sessions.find(row=>row.id===id);
    return value.selectedSessionId===id&&row&&Array.isArray(row.messages)&&
     row.historyLoading!==true&&(!row.historyManaged||row.historyLoaded===true);
   },id);
   const value=await state(),row=value.sessions.find(row=>row.id===id);
   assert.equal(value.selectedSessionId,id);
   assert.ok(row,'Selected fixture history is resident: '+id);
   assert.ok(Array.isArray(row.messages),'Selected fixture messages are loaded: '+id);
   assert.ok(!row.historyError,'Selected fixture history has no read error: '+id);
   return row;
  }
  async function createOwnedChat(title){
   const receipt=await page.evaluate(args=>window.amplifier.dispatch('session.create',args),
    {title,workspace:workspace.path,location:{kind:'workspace'}});
   assert.equal(receipt.accepted,true,'Fixture chat creation accepted');
   assert.equal(typeof receipt.sessionId,'string','Creation receipt binds its exact chat');
   assert.ok(receipt.sessionId.length>0);
   await waitSelectedHistory(receipt.sessionId);
   return receipt.sessionId;
  }
  const sid=await createOwnedChat(title),other=await createOwnedChat('PR36 other synthetic chat');
  assert.notEqual(sid,other,'Create two distinct owned fixture chats');
  const ids=[sid,other];
  evidence.historyReads=[];
  const history=async()=>{
   // getState exposes a bounded resident working set, not every created chat.
   // Select only these owned fixture IDs and read each actual loaded row.
   const original=await state(),selected=original.selectedSessionId,draftBefore=original.view.draft;
   assert.ok(ids.includes(selected),'Restore only an owned fixture selection');
   const read={originalSelection:selected,selectedReads:[],restored:false};
   evidence.historyReads.push(read);
   const rows=[];
   try{
    for(const id of ids){
     await action('session.select',{id});
     const row=await waitSelectedHistory(id);
     rows.push({id,messages:row.messages});
     read.selectedReads.push(id);
    }
    return rows;
   }finally{
    await action('session.select',{id:selected});
    await waitSelectedHistory(selected);
    await expect.poll(async()=>(await state()).view.draft).toEqual(draftBefore);
    read.restored=true;read.draftPreserved=true;
   }
  };
  const historyBefore=await history();
  assert.ok(historyBefore.every(row=>row.messages.length===0),'No saved user history is imported or seeded');
  await action('session.select',{id:sid});await showFixtureConversation(page);
  const draft='PR36 synthetic unsent draft: preserve exactly, never submit.';
  const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
  await composer.fill(draft);
  await expect.poll(()=>readComposerDraft(composer)).toBe(draft);
  await expect.poll(async()=>(await state()).view.draft).toBe(draft);
  const notice=()=>page.locator('[data-archive-notice]:visible');
  const restoreActions=()=>evidence.actions.filter(row=>row.action==='session.restore');
  const archiveActions=()=>evidence.actions.filter(row=>row.action==='session.archive');
  const frames=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  async function archive(){
   await page.getByRole('button',{name:'Chat',exact:true}).click();
   await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Archive',exact:true}).click();
   await expect(notice()).toContainText('Chat archived');
  }
  async function selectRestored(){
   await action('session.restore',{id:sid});
   await action('session.select',{id:sid});await showFixtureConversation(page);
   await expect.poll(()=>readComposerDraft(composer)).toBe(draft);
  }
  async function geometry(width){
   await frames();
   // Measure after real navigation/focus settles, before any locator focuses or
   // scrolls a banner control. No CSS, root scrolling or geometry repairs.
   const value=await notice().evaluate(node=>{
    const rect=n=>{const r=n.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}};
    const header=document.querySelector('#amp-one > [data-part="header"]');
    if(!header)throw Error('The actual application header is absent');
    const text=node.querySelector(':scope > span'),range=document.createRange();range.selectNodeContents(text);
    return {viewport:{width:innerWidth,height:innerHeight},header:rect(header),notice:rect(node),text:rect(text),
     lineTops:[...new Set([...range.getClientRects()].map(r=>Math.round(r.top)))],
     buttons:[...node.querySelectorAll('button')].map(button=>{
      const r=button.getBoundingClientRect(),hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
      return {name:button.getAttribute('aria-label')||button.textContent.trim(),...rect(button),disabled:button.disabled,
       hit:hit===button||button.contains(hit)};
     }),paddingTop:parseFloat(getComputedStyle(node).paddingTop),scrollWidth:node.scrollWidth,clientWidth:node.clientWidth,
     documentWidth:document.documentElement.scrollWidth,scrollY,documentScrollTop:document.documentElement.scrollTop,
     rootTop:document.querySelector('#amp-one').getBoundingClientRect().top,
     active:{tag:document.activeElement?.tagName,text:document.activeElement?.textContent?.slice(0,160)}};
   });
   evidence.geometry.push({width,...value});
   for(const [name,r] of [['header',value.header],['notice',value.notice],...value.buttons.map(button=>[button.name,button])]){
    assert.ok(r.width>0&&r.height>0,width+' '+name+' has positive geometry');
    assert.ok(r.left>=0&&r.right<=value.viewport.width+0.5&&r.top>=0&&r.bottom<=value.viewport.height+0.5,width+' '+name+' is inside the viewport');
   }
   assert.ok(value.notice.top>=value.header.bottom-0.5,'Banner is below the actual app header');
   assert.ok(value.rootTop>=-0.5,'Focus must not scroll the locked app to negative Y');
   assert.ok(value.text.left-value.notice.left>=16&&value.text.top-value.notice.top>=12,'Text retains padding/insets');
   assert.ok(value.scrollWidth<=value.clientWidth&&value.documentWidth<=value.viewport.width,'No horizontal overflow');
   if(width<=390)assert.ok(value.lineTops.length>1,'Long title actually wraps');
   for(const button of value.buttons){
    assert.ok(button.height>=34&&button.width>=24&&!button.disabled&&button.hit,'Usable unobscured '+button.name);
    assert.ok(button.left>=value.notice.left&&button.right<=value.notice.right,'Actions remain inside banner');
    assert.ok(button.top>=value.notice.top+10&&button.bottom<=value.notice.bottom-10,'Actions retain vertical padding');
   }
   assert.deepEqual(value.buttons.map(button=>button.name),['Undo','Keep open','Dismiss archive notification']);
   await page.screenshot({path:join(output,`pr36-banner-${width}.png`)});
  }
  for(const width of [1280,390,320]){
   await page.setViewportSize({width,height:900});
   await archive();
   await page.waitForFunction(()=>window.amplifier.getState().view.workSurface==='workspace');
   await page.waitForFunction(()=>document.activeElement?.matches('[data-part="work-browser"] h1'));
   await geometry(width);
   const previous=restoreActions().length;
   await notice().getByRole('button',{name:'Undo',exact:true}).click();
   await expect(notice()).toContainText('Chat restored');
   assert.deepEqual(restoreActions().slice(previous),[{action:'session.restore',args:{id:sid}}],'Exactly the captured fixture chat is restored once');
   await notice().getByRole('button',{name:'Dismiss archive notification',exact:true}).click();
   await expect(notice()).toHaveCount(0);
   await action('session.select',{id:sid});await showFixtureConversation(page);
   await expect.poll(()=>readComposerDraft(composer)).toBe(draft);
  }
  await page.setViewportSize({width:1280,height:900});
  // Match the maintained component journey's browser clock; leave normal frame
  // delivery running so the actual host's navigation/focus can settle.
  await page.clock.install();
  const outside=()=>page.getByRole('button',{name:'Amplifier Home',exact:true});
  async function leaveNotice(){await page.mouse.move(0,0);await outside().focus()}
  async function advance(label,ms,present=true){
   await page.clock.runFor(ms);
   if(present)await expect(notice()).toBeVisible();else await expect(notice()).toHaveCount(0);
   evidence.timing.push({label,advancedMs:ms,browserNow:await page.evaluate(()=>performance.now()),
    present,archiveCalls:archiveActions().length,restoreCalls:restoreActions().length});
  }
  const clockNow=()=>page.evaluate(()=>performance.now());
  async function freezeClock(){
   await page.clock.pauseAt(new Date(await page.evaluate(()=>Date.now())+100));
  }
  async function expiryBoundaries(label,{earliestStart,latestStart,pausedMs=0}){
   // Timer eligibility is bracketed by the real gesture and its settled DOM,
   // never by replacing/intercepting the product timer. While frozen, UI/HTTP
   // processing costs no browser-clock time; only runFor advances this counter.
   const uncertainty=latestStart-earliestStart;
   assert.ok(uncertainty>=0&&uncertainty<=250,label+' eligibility bracket must be at most 250ms');
   const readable=async()=>{
    const now=await clockNow();
    return {now,minMs:now-latestStart-pausedMs,maxMs:now-earliestStart-pausedMs};
   };
   const elapsed=await readable();
   const beforeAdvance=14999-elapsed.maxMs;
   assert.ok(beforeAdvance>=0,label+' must reach the pre-deadline checkpoint without overshooting');
   await advance(label+'-visible-just-before-15s',beforeAdvance);
   const before=await readable();
   assert.equal(before.maxMs,14999);
   assert.ok(before.minMs>=14749,label+' even the latest possible timer start is beyond a wrong 12s lifetime');
   const afterAdvance=15001-before.minMs;
   await advance(label+'-absent-just-after-15s',afterAdvance,false);
   const after=await readable();
   assert.equal(after.minMs,15001);
   evidence.timing.push({label:label+'-readable-boundaries',earliestStart,latestStart,uncertaintyMs:uncertainty,pausedMs,
    before:{...before,present:true},after:{...after,present:false}});
  }
  // Pauses count only the readable part of the one 15-second confirmation.
  await freezeClock();
  const archiveEarliestStart=await clockNow();
  await archive();
  // HTTP/SSE and React continue while paused. Deliver the actual navigation's
  // animation frames explicitly, with a bounded clock span, before timing it.
  await expect.poll(async()=>{
   const settled=await page.evaluate(()=>document.activeElement?.matches('[data-part="work-browser"] h1')===true);
   if(!settled){
    assert.ok((await clockNow())-archiveEarliestStart<250,'Archive navigation must settle within the eligibility bracket');
    await page.clock.runFor(16);
   }
   return settled;
  }).toBe(true);
  await leaveNotice();
  const archiveLatestStart=await clockNow();
  const beforeExpiryRestores=restoreActions().length;
  await advance('routine-visible-before-deadline',3000);
  await notice().getByRole('button',{name:'Undo',exact:true}).hover();
  await advance('hover-pauses',20000);
  await notice().getByRole('button',{name:'Undo',exact:true}).focus();await page.mouse.move(0,0);
  await advance('focus-pauses',20000);
  await expect(notice().getByRole('button',{name:'Undo',exact:true})).toBeFocused();
  // Supply only this browser's visibility input; no app timer/state replacement.
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'))});
  await outside().focus();
  await advance('hidden-pauses',20000);
  await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))});
  assert.equal(await page.evaluate(()=>document.hidden),false);
  await advance('remaining-visible-budget-not-reset',6000);
  await expiryBoundaries('archive',{earliestStart:archiveEarliestStart,latestStart:archiveLatestStart,pausedMs:60000});
  assert.equal(restoreActions().length,beforeExpiryRestores,'Expiry never restores or retries');
  await page.clock.resume();
  await selectRestored();
  // Keep open belongs to this operation and survives the real top/dialog remount.
  await archive();
  await notice().getByRole('button',{name:'Keep open',exact:true}).click();
  await expect(notice().getByRole('button',{name:'Keeping open',exact:true})).toHaveAttribute('aria-pressed','true');
  const activate=(_label,control,options)=>control.click(options);
  await openSettingsPage(page,'appearance',{activate});
  await expect(page.locator('[data-part="dialog"] [data-archive-notice]')).toBeVisible();
  await page.mouse.move(0,0);
  await page.locator('.a-appearance-more > summary').focus();
  await advance('keep-open-survives-dialog-remount',30000);
  await expect(notice().getByRole('button',{name:'Keeping open',exact:true})).toHaveAttribute('aria-pressed','true');
  await page.locator('[data-part="dialog"]:visible').getByRole('button',{name:/^Close (panel|settings)$/i}).click();
  await expect(page.locator('#amp-one > [data-archive-notice]')).toBeVisible();
  await leaveNotice();await advance('keep-open-survives-top-remount',30000);
  const beforeKeptUndo=restoreActions().length;
  await freezeClock();
  const restoreEarliestStart=await clockNow();
  await notice().getByRole('button',{name:'Undo',exact:true}).click();
  await expect(notice()).toContainText('Chat restored');
  assert.deepEqual(restoreActions().slice(beforeKeptUndo),[{action:'session.restore',args:{id:sid}}]);
  await expect(notice().getByRole('button',{name:'Keep open',exact:true})).toHaveAttribute('aria-pressed','false');
  await leaveNotice();
  const restoreLatestStart=await clockNow();
  assert.equal(restoreLatestStart,restoreEarliestStart,'Restore settles without advancing the frozen browser clock');
  await expiryBoundaries('restore-keep-open-not-inherited',{earliestStart:restoreEarliestStart,latestStart:restoreLatestStart});
  assert.deepEqual(restoreActions().slice(beforeKeptUndo),[{action:'session.restore',args:{id:sid}}],'Restore expiry never replays Undo');
  await page.clock.resume();
  await action('session.select',{id:sid});await showFixtureConversation(page);
  // Hold only the navigation transport. Archive remains real and already saved.
  await page.route('**/api/actions',async route=>{
   const raw=route.request().postDataJSON(),body=raw.action==='shell.command'?(raw.args.command||raw.args):raw;
   if(navigationGate&&body.action==='view.update'&&body.args?.patch?.workSurface==='workspace'){
    const gate=navigationGate;navigationGate=null;gate.entered=true;
    await new Promise(resolve=>{releaseNavigation=resolve});
    if(gate.reject)await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({accepted:false,error:'Synthetic delayed fixture navigation rejection'})});
    else await route.continue();
    gate.done=true;releaseNavigation=null;return;
   }
   await route.continue();
  });
  const held={reject:true,entered:false,done:false};navigationGate=held;
  await archive();await expect.poll(()=>held.entered).toBe(true);
  await leaveNotice();await advance('unresolved-navigation-must-not-expire',20000);
  releaseNavigation();await expect.poll(()=>held.done).toBe(true);
  await expect(notice()).toContainText(/Chat archived\. (Your current view was kept\.|Could not open the chat list\.)/);
  await expect(notice().getByRole('button',{name:'Keep open',exact:true})).toHaveCount(0);
  await leaveNotice();await advance('late-warning-is-persistent-not-renewed-routine-expiry',60000);
  await page.screenshot({path:join(output,'pr36-late-navigation-warning.png')});
  await notice().getByRole('button',{name:'Dismiss archive notification',exact:true}).click();
  await selectRestored();
  const dismissed={reject:true,entered:false,done:false};navigationGate=dismissed;
  await archive();await expect.poll(()=>dismissed.entered).toBe(true);
  await notice().getByRole('button',{name:'Dismiss archive notification',exact:true}).click();
  await advance('dismissed-held-navigation',20000,false);
  releaseNavigation();await expect.poll(()=>dismissed.done).toBe(true);
  await advance('late-warning-cannot-revive-dismissed-notice',16000,false);
  await selectRestored();
  // Actual Appearance UI: no profile writes, library mutation or synthetic props.
  await openSettingsPage(page,'appearance',{activate});
  const settings=page.locator('.a-appearance-settings'),advanced=settings.locator('.a-appearance-more');
  const summary=advanced.locator(':scope > summary'),gallery=settings.getByRole('radiogroup',{name:'Available appearances',exact:true});
  await expect.poll(()=>gallery.getByRole('radio').count()).toBeGreaterThan(0);
  await expect(gallery.getByText('Loading appearances…',{exact:true})).toHaveCount(0);
  await expect(gallery).toBeVisible();await expect(advanced).toHaveJSProperty('open',false);
  await expect(settings.getByLabel('Density',{exact:true})).toBeVisible();
  await expect(settings.getByLabel('Layout',{exact:true})).toBeHidden();
  assert.equal(await gallery.evaluate(node=>node.closest('.a-appearance-more')===null),true);
  evidence.appearance.galleryCount=await gallery.getByRole('radio').count();
  await gallery.scrollIntoViewIfNeeded();await page.screenshot({path:join(output,'pr36-appearance-primary-gallery.png')});
  const motion=settings.locator('[data-appearance-motion]');
  await expect(motion).toContainText('Standard motion is on');
  await expect(motion.locator('input,select,button,[role="switch"],[role="checkbox"]')).toHaveCount(0);
  await motion.scrollIntoViewIfNeeded();await page.screenshot({path:join(output,'pr36-motion-standard.png')});
  await page.emulateMedia({reducedMotion:'reduce'});
  await expect(motion).toContainText('Reduced motion is on');
  await page.screenshot({path:join(output,'pr36-motion-reduced.png')});
  await page.emulateMedia({reducedMotion:'no-preference'});
  await expect(motion).toContainText('Standard motion is on');
  evidence.appearance.motion={readOnly:true,statuses:['Standard motion is on','Reduced motion is on','Standard motion is on']};
  evidence.appearance.responsive=[];
  async function appearanceRouteReady(){
   // Match the maintained helper's useSettingsCompact boundary, then observe
   // the actual Appearance route across layout frames; do not force view state.
   await page.waitForFunction(()=>{
    const panel=document.querySelector('.a-settings-experience');
    return panel&&panel.dataset.compact===String((panel.closest('.a-overlay')||panel).clientWidth<960)&&
     panel.dataset.settingsPage==='appearance'&&panel.dataset.settingsIndex==='false';
   });
   const route=await page.locator('.a-settings-experience').getAttribute('data-settings-route');
   await frames();
   assert.equal(await page.locator('.a-settings-experience').getAttribute('data-settings-route'),route,'Responsive Appearance route has settled');
   return route;
  }
  async function appearanceControl(label,control,width){
   await control.scrollIntoViewIfNeeded();await expect(control).toBeVisible();await frames();
   const sample=await control.evaluate(node=>{
    const r=node.getBoundingClientRect(),panel=node.closest('.a-settings-experience');
    const scroll=node.closest('.a-settings-content'),dialog=node.closest('.a-dialog');
    return {rect:{left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height},
     viewport:{width:innerWidth,height:innerHeight},compact:panel.dataset.compact,route:panel.dataset.settingsRoute,
     overflow:[node.closest('.a-appearance-settings'),scroll,dialog].map(el=>({
      className:el.className,scrollWidth:el.scrollWidth,clientWidth:el.clientWidth})),
     disabled:node.matches(':disabled')||node.getAttribute('aria-disabled')==='true'};
   });
   evidence.appearance.responsive.push({width,label,...sample});
   const r=sample.rect;
   assert.ok(r.width>0&&r.height>0&&!sample.disabled,label+' has usable positive geometry');
   assert.ok(r.left>=-0.5&&r.right<=sample.viewport.width+0.5&&r.top>=-0.5&&r.bottom<=sample.viewport.height+0.5,label+' is inside the viewport after ordinary scrolling');
   assert.ok(sample.overflow.every(row=>row.scrollWidth<=row.clientWidth+1),label+' has no horizontal overflow');
   await page.screenshot({path:join(output,`pr36-appearance-${width}-${label}.png`)});
  }
  // Reuse the loaded actual host catalog, not empty/synthetic gallery props.
  // Record only the scrolled controls in view, not a whole-page visibility claim.
  for(const width of [1280,390,320]){
   await page.setViewportSize({width,height:900});
   await openSettingsPage(page,'appearance',{activate});
   await appearanceRouteReady();
   await expect(advanced).toHaveJSProperty('open',false);
   await expect.poll(()=>gallery.getByRole('radio').count()).toBeGreaterThan(0);
   await expect(gallery.getByText('Loading appearances…',{exact:true})).toHaveCount(0);
   await expect(gallery).toBeVisible();
   assert.equal(await gallery.getByRole('radio').count(),evidence.appearance.galleryCount,'Loaded catalog survives responsive navigation');
   await appearanceControl('primary-gallery-card',gallery.locator('[data-appearance-card]').first(),width);
   await expect(motion).toContainText('Standard motion is on');
   await expect(motion.locator('input,select,button,[role="switch"],[role="checkbox"]')).toHaveCount(0);
   await appearanceControl('motion-read-only',motion,width);
   await expect(settings.getByLabel('Layout',{exact:true})).toBeHidden();
   await appearanceControl('advanced-closed',summary,width);
   await summary.focus();await summary.press('Enter');
   await expect(advanced).toHaveJSProperty('open',true);
   const layout=settings.getByLabel('Layout',{exact:true});
   assert.equal(await layout.evaluate(node=>!!node.closest('.a-appearance-more')),true);
   await appearanceControl('layout-inside-advanced',layout,width);
   await summary.scrollIntoViewIfNeeded();await summary.focus();await summary.press('Space');
   await expect(advanced).toHaveJSProperty('open',false);
   await expect(layout).toBeHidden();
   assert.deepEqual(await profiles(),profilesBefore,'Responsive disclosure retains both profiles and selected source');
   assert.deepEqual(evidence.pageErrors,[],'Responsive page/frame errors are not filtered');
  }
  await page.setViewportSize({width:1280,height:900});
  await openSettingsPage(page,'appearance',{activate});
  await appearanceRouteReady();await expect(advanced).toHaveJSProperty('open',false);
  const libraryBefore=await action('theme.list');
  await summary.focus();await summary.press('Enter');await expect(advanced).toHaveJSProperty('open',true);
  await expect(settings.getByLabel('Layout',{exact:true})).toBeVisible();
  assert.equal(await settings.getByLabel('Layout',{exact:true}).evaluate(node=>!!node.closest('.a-appearance-more')),true);
  await summary.press('Space');await expect(advanced).toHaveJSProperty('open',false);
  assert.deepEqual(await profiles(),profilesBefore,'Motion/disclosure/navigation do not write profiles or change source');
  await summary.press('Enter');await expect(advanced).toHaveJSProperty('open',true);
  await settings.getByText('Advanced customization',{exact:true}).click();
  const editorName='PR36 private preview draft',css='#amp-one[data-theme-scheme="light"]{--a-accent:#338855}\n#amp-one[data-theme-scheme="dark"]{--a-accent:#55bb88}';
  await settings.getByLabel('Appearance name',{exact:true}).fill(editorName);
  await settings.getByLabel('Reference CSS delta',{exact:true}).fill(css);
  const editorKey='amplifier.appearance-editor.v3:'+(await state()).appearance.namespace;
  const editorRecord=()=>page.evaluate(key=>sessionStorage.getItem(key),editorKey);
  const editorBefore=await editorRecord();assert.ok(editorBefore);
  const beforePreview=await profiles();
  await settings.getByRole('button',{name:'Preview draft',exact:true}).click();
  const preview=settings.locator('.a-appearance-preview');
  await expect(preview).toContainText(editorName);
  const scheme=await page.locator('#amp-one').getAttribute('data-theme-scheme');
  await expect.poll(()=>page.locator('#amp-one').evaluate(node=>getComputedStyle(node).getPropertyValue('--a-accent').trim())).toBe(scheme==='dark'?'#55bb88':'#338855');
  await summary.click();await expect(advanced).toHaveJSProperty('open',false);
  await preview.scrollIntoViewIfNeeded();await expect(preview).toBeInViewport();
  assert.equal(await preview.evaluate(node=>node.closest('.a-appearance-more')===null),true,'Active Preview is outside the Advanced fold');
  await expect(preview.getByRole('button',{name:'Cancel preview',exact:true})).toBeVisible();
  await page.screenshot({path:join(output,'pr36-active-preview-advanced-closed.png')});
  await preview.getByRole('button',{name:'Cancel preview',exact:true}).click();
  await expect(preview).toHaveCount(0);
  await expect.poll(async()=>!!(await state()).appearance.preview).toBe(false);
  assert.deepEqual(await profiles(),beforePreview,'Cancel restores selected source and both profiles');
  assert.equal(await editorRecord(),editorBefore,'Cancel retains the private editor draft');
  assert.deepEqual(await action('theme.list'),libraryBefore,'Preview never saves/updates/imports the library');
  await page.locator('[data-part="dialog"]:visible').getByRole('button',{name:/^Close (panel|settings)$/i}).click();
  await showFixtureConversation(page);
  await expect.poll(()=>readComposerDraft(composer)).toBe(draft);
  assert.deepEqual(await history(),historyBefore,'Archive, timing and Preview leave synthetic saved history unchanged');
  assert.deepEqual(await profiles(),profilesBefore);
  evidence.appearance.profilesPreserved=true;evidence.appearance.sourcePreserved=true;evidence.appearance.editorDraftPreserved=true;
  evidence.history={before:historyBefore,after:await history(),unsentDraftSha256:digest(draft),preserved:true};
  const fixtureAfter=await get('/fixture'),recordsAfter=await get('/fixture/appearance-records');
  for(const name of ['sent','started','stopped']){assert.deepEqual(fixtureBefore[name],[]);assert.deepEqual(fixtureAfter[name],[])}
  assert.equal(fixtureAfter.workerCount,0);
  assert.equal(recordsAfter.legacy.unchanged,true);
  assert.deepEqual(recordsAfter.production,recordsBefore.production);
  assert.deepEqual(recordsAfter.library,recordsBefore.library);
  assert.ok(!evidence.actions.some(row=>/^(conversation\.(send|retry)|runtime\.|theme\.(save|update|import|delete)|appearance\.preferences\.(update|select))/.test(row.action)),'No sends, runtime control or preference/library mutations');
  assert.ok(archiveActions().every(row=>row.args.id===sid)&&restoreActions().every(row=>row.args.id===sid));
  assert.equal(archiveActions().length,7,'Only the seven explicit fixture archive gestures execute');
  assert.equal(restoreActions().length,7,'Only three geometry Undos, one kept Undo and three explicit fixture resets execute');
  assert.equal(evidence.actions.filter(row=>row.action==='session.create').length,2);
  evidence.fixtureAfter=fixtureAfter;
  evidence.entryAfter=await entryHashes();assert.deepEqual(evidence.entryAfter,evidence.entryBefore);
  evidence.checksComplete=true;
 }catch(error){
  failure=error;evidence.failure={name:error.name,message:error.message,stack:error.stack};
  if(page&&!page.isClosed())try{await page.screenshot({path:join(output,'pr36-failure.png')})}catch(capture){evidence.screenshotFailure=String(capture)}
 }finally{
  try{
   releaseNavigation?.();
   if(owned){
    await finishFixture({label:'PR36 installed archive',close:[['PR36 browser',()=>browser?.close()]],
     stop:async()=>{evidence.cleanup.exit=await owned.stop()},originalError:failure});
    evidence.cleanup.complete=evidence.cleanup.exit?.code===0&&evidence.cleanup.exit?.signal===null;
   }else if(browser)await browser.close();
   if(evidence.checksComplete){
    assert.equal(evidence.cleanup.complete,true,'Fixture must exit 0 after full cleanup');
    const proof=JSON.parse(await readFile(join(output,'installed-host-normal.json'),'utf8'));
    assert.equal(proof.payloadAfterCleanup,true);
    assert.equal(proof.strictPreImport.verified,true);assert.deepEqual(proof.strictPreImport.modules,{});
    assert.equal(proof.strictBefore.verified,true);assert.equal(proof.strictAfter.verified,true);
    for(const [name,row] of Object.entries(proof.strictBefore.modules))
     assert.deepEqual(proof.strictAfter.modules[name],row,'Imported wheel origin remains bound: '+name);
    assert.equal(proof.installedIdentity.wheelSha256,identity.wheelSha256);
    evidence.installedHost=proof;
   }
   assert.deepEqual(evidence.pageErrors,[],'All browser page errors, including thumbnail frames, remain fatal');
  }catch(error){
   evidence.cleanup.error={name:error.name,message:error.message,stack:error.stack};
   failure=failure?new AggregateError([failure,error],'PR36 check and cleanup/evidence failures'):error;
  }finally{
   evidence.passed=!!evidence.checksComplete&&evidence.cleanup.complete&&!failure;
   await writeFile(join(output,'pr36-fixture.log'),logs);
   await writeFile(join(output,'pr36-result.json'),JSON.stringify(evidence,null,2)+'\n');
  }
 }
 if(failure)throw failure;
 assert.equal(evidence.passed,true);
 console.log(JSON.stringify({passed:true,scope:evidence.scope,evidence:join(output,'pr36-result.json'),wheelSha256:identity.wheelSha256,selectedClient}));
}

if(process.env.AMPLIFIER_TEST_PR36==='1'){
 await installedPr36();
}else{

const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/archive-feedback';
await mkdir(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'/.venv/bin/python',[root+'/tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'Archive feedback fixture'});
let browser,page,release,releaseNavigation,failure;
try{
 const url=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}});
  fixture.once('error',reject);fixture.once('exit',code=>reject(Error('Fixture exited '+code)));
 });
 browser=await chromium.launch({headless:true,args:JSON.parse(process.env.AMPLIFIER_CHROMIUM_ARGS||'[]')});
 const context=await browser.newContext({viewport:{width:1200,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page=await context.newPage();const peer=await context.newPage(),errors=[];
 for(const p of [page,peer]){p.on('pageerror',e=>errors.push(e.message));await p.goto(url);await p.waitForFunction(()=>window.amplifier?.dispatch)}
 const action=(name,args={},p=page)=>p.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const workspace=(await state()).workspaces.find(row=>row.path);
 await action('session.create',{title:'Archive example',workspace:workspace.path,location:{kind:'workspace'}});
 const sid=(await state()).selectedSessionId;
 await action('session.create',{title:'Other example',workspace:workspace.path,location:{kind:'workspace'}});
 const other=(await state()).selectedSessionId;
 await action('session.select',{id:sid});await action('session.select',{id:sid},peer);
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Draft is preserved');
 await action('attachment.add',{sessionId:sid,name:'draft.txt',base64:Buffer.from('Synthetic attachment').toString('base64')});
 await action('view.update',{patch:{navPinned:true}});
 const before=await page.request.get(url+'/fixture').then(r=>r.json());
 let mode='hold',archiveCalls=0,restoreCalls=0,holdNavigation=false,workspaceRequests=0;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='view.update'&&body.args?.patch?.workSurface==='workspace')workspaceRequests++;
  if(holdNavigation&&body?.action==='view.update'&&body.args?.patch?.workSurface==='chat'){
   holdNavigation=false;await new Promise(resolve=>{releaseNavigation=resolve});
  }
  if(body?.action==='session.restore')restoreCalls++;
  if(body?.action!=='session.archive'){await route.continue();return}
  archiveCalls++;
  if(mode==='reject'||mode==='reject200'){await route.fulfill({status:mode==='reject'?403:200,contentType:'application/json',body:JSON.stringify({accepted:false,error:'Synthetic archive rejection'})});return}
  if(mode==='unknown'){await route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({error:'Synthetic uncertain transport'})});return}
  const response=await route.fetch();
  if(mode==='hold')await new Promise(resolve=>{release=resolve});
  await route.fulfill({response});
 });
 async function headerArchive(){
  await page.getByRole('button',{name:'Chat',exact:true}).click();
  await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Archive',exact:true}).click();
 }
 await headerArchive();
 await expect(page.locator('[data-archive-notice]')).toContainText('Archiving…');
 assert.equal((await state()).view.workSurface||'chat','chat');
 await expect(page.getByRole('button',{name:'Chat',exact:true})).toBeFocused();
 await page.screenshot({path:output+'/pending.png'});
 await expect.poll(()=>!!release).toBe(true);release();release=null;mode='normal';
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat archived');
 await expect(page.locator('[data-part="work-browser"] h1')).toHaveText(workspace.name);
 await page.waitForFunction(()=>document.activeElement?.matches('[data-part="work-browser"] h1'));
 assert.equal((await state()).view.workSurface,'workspace');
 assert.equal((await state()).selectedSessionId,sid); // No replacement chat opens.
 const peerState=await peer.evaluate(()=>window.amplifier.getState());
 assert.equal(peerState.selectedSessionId,sid);assert.equal(peerState.view.workSurface||'chat','chat');
 await page.screenshot({path:output+'/archived-desktop.png'});
 await page.locator('[data-archive-notice]').getByRole('button',{name:'Undo',exact:true}).click();
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat restored');
 assert.equal(restoreCalls,1);assert.equal((await state()).view.workSurface,'workspace');
 await action('session.select',{id:sid});
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveText('Draft is preserved');
 await expect(page.locator('[data-part="composer"]')).toContainText('draft.txt');
 // A renderer's edit exists only in component memory, not a saved artifact.
 await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true}});
 await action('canvas.show',{kind:'markdown',title:'Editable archive example',content:'# Original source'});
 const manifest={id:'example.archive-dirty-editor',label:'Unsaved archive editor',version:'1.0.0',apiVersion:'1.0',profile:'trusted-native-renderer-v1',stateSchema:'canvas-view-v1',resourceKinds:['markdown'],capabilities:['canvas.resource.read','canvas.view.update']};
 const source='export default ({React})=>function Editor({host}){const [text,setText]=React.useState("");return React.createElement("label",null,"Unsaved archive edit",React.createElement("input",{value:text,onChange:event=>{setText(event.target.value);host.setDirty(true)}}))}';
 const digest=(await action('shell.packages.stage',{manifest,source})).result.digest;
 const qualification=(await action('shell.packages.validate',{digest})).result;assert.equal(qualification.status,'passed',JSON.stringify(qualification));
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(digest);
 const canvasEdit=page.locator('[data-canvas-view="primary"]').getByLabel('Unsaved archive edit');
 await prepareCanvasReaderInput(page,{target:canvasEdit,label:'archive-editor',outputDir:output,expectCovered:true});
 await canvasEdit.fill('UNSAVED CANVAS ARCHIVE INPUT');
 const inspectViews=async()=>(await action('canvas.views.inspect')).result.views;
 await expect.poll(async()=>(await inspectViews()).find(v=>v.viewId==='primary')?.dirty).toBe(true);
 const viewTarget=v=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,v[key]]));
 const originalViews=(await inspectViews()).map(viewTarget);
 await headerArchive();await page.waitForFunction(()=>window.amplifier.getState().view.workSurface==='workspace');
 await expect(canvasEdit).toBeHidden();
 await action('session.select',{id:sid});
 await expect(canvasEdit).toHaveValue('UNSAVED CANVAS ARCHIVE INPUT');
 assert.deepEqual((await inspectViews()).map(viewTarget),originalViews);
 assert.equal((await state()).canvas.content,'# Original source');
 await page.screenshot({path:output+'/unsaved-canvas.png'});
 // Explicit fixture cleanup only after proving the input survived the round trip.
 await action('session.restore',{id:sid});
 await action('canvas.views.recover',viewTarget((await inspectViews()).find(v=>v.viewId==='primary')));
 await action('canvas.close');
 mode='reject';await headerArchive();
 await expect(page.locator('[data-archive-notice]')).toContainText('Synthetic archive rejection');
 assert.equal((await state()).view.workSurface||'chat','chat');
 await expect(page.getByRole('button',{name:'Chat',exact:true})).toBeFocused();
 mode='reject200';await headerArchive();
 await expect(page.locator('[data-archive-notice][role="alert"]')).toContainText('Synthetic archive rejection');
 assert.equal((await state()).view.workSurface||'chat','chat');
 mode='unknown';const unknownBefore=archiveCalls;await headerArchive();
 await expect(page.locator('[data-archive-notice][role="status"]')).toContainText('Could not confirm the change.');
 assert.equal(archiveCalls,unknownBefore+1);assert.equal((await state()).view.workSurface||'chat','chat');
 await expect(page.locator('[data-archive-notice]').getByRole('button',{name:'Undo',exact:true})).toHaveCount(0);
 // Same A -> B -> A identity is not authority for an old archive to redirect.
 mode='hold';await headerArchive();await expect.poll(()=>!!release).toBe(true);
 await action('session.select',{id:other});await action('session.select',{id:sid});
 release();release=null;mode='normal';
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat archived');
 assert.equal((await state()).view.workSurface||'chat','chat');
 await action('session.restore',{id:sid});
 // A queued browse must recheck user intent before sending its request.
 holdNavigation=true;
 await page.evaluate(()=>{window.heldNavigation=window.amplifier.dispatch('view.update',{patch:{workSurface:'chat'}})});
 await expect.poll(()=>!!releaseNavigation).toBe(true);
 const workspaceBefore=workspaceRequests;
 await headerArchive();
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat archived');
 // The confirmed mutation cannot be locked behind a queued, stalled browse.
 const restoresBefore=restoreCalls;
 await page.locator('[data-archive-notice]').getByRole('button',{name:'Undo',exact:true}).click();
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat restored');
 assert.equal(restoreCalls,restoresBefore+1);
 await page.getByRole('button',{name:'Dismiss archive notification'}).click();
 await page.evaluate(()=>{window.newerNavigation=window.amplifier.dispatch('view.update',{patch:{workSurface:'chats'}})});
 releaseNavigation();releaseNavigation=null;
 await page.evaluate(()=>Promise.all([window.heldNavigation,window.newerNavigation]));
 assert.equal((await state()).view.workSurface,'chats');assert.equal(workspaceRequests,workspaceBefore);
 await action('session.restore',{id:sid});
 // A row action for another chat doesn't leave the current conversation.
 await action('session.select',{id:sid});await action('session.pin',{id:other,pinned:true});
 const otherRow=page.locator('[data-shell-instance=chats] [data-sidebar-section=pinned]').locator(`[data-session-id="${other}"]`);
 await otherRow.hover();
 await otherRow.getByRole('button',{name:'Details and actions for Other example',exact:true}).click();
 await page.getByRole('button',{name:'Archive Other example',exact:true}).click();
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat archived');
 assert.equal((await state()).selectedSessionId,sid);assert.equal((await state()).view.workSurface||'chat','chat');
 // Public integration dispatch never invokes interactive navigation.
 await action('session.archive',{id:sid});
 assert.equal((await state()).view.workSurface||'chat','chat');await action('session.restore',{id:sid});
 await page.setViewportSize({width:390,height:844});
 await headerArchive();
 await page.waitForFunction(()=>window.amplifier.getState().view.workSurface==='workspace');
 await expect(page.locator('[data-part="work-browser"] h1')).toBeVisible();
 assert.ok(await page.locator('[data-archive-notice]').evaluate(el=>el.scrollWidth<=el.clientWidth));
 await page.screenshot({path:output+'/archived-mobile.png'});
 // Managed chats land on the existing full chat list rather than their private folder.
 await action('session.create',{title:'Managed example',location:{kind:'managed'}});
 await headerArchive();await page.waitForFunction(()=>window.amplifier.getState().view.workSurface==='chats');
 const chatSearch=page.getByRole('region',{name:'Chat search',exact:true});
 await expect(page.locator('[data-landing="chats"]').getByRole('heading',{name:'All chats',exact:true})).toBeVisible();
 await expect(chatSearch.getByRole('searchbox',{name:'Search chats',exact:true})).toBeVisible();
 // An archive receipt arriving after Settings opens must preserve its private draft.
 // Settings is modal: clicking the covered chat header is not a real user path.
 const managed=(await state()).selectedSessionId;
 await action('session.select',{id:managed});await action('session.restore',{id:managed});
 await page.setViewportSize({width:1440,height:1050});
 await expect(settingsButton(page)).toBeVisible();
 mode='hold';await headerArchive();await expect.poll(()=>!!release).toBe(true);
 await openSettingsPage(page,'providers');
 const settings=page.locator('.a-settings-experience[data-settings-page="providers"]');
 await settings.getByRole('button',{name:'Add provider',exact:true}).click();
 await settings.getByRole('button',{name:'Show advanced configuration',exact:true}).click();
 const settingsEdit=settings.locator('[id$="provider-config"]');
 await settingsEdit.fill('{"unsaved-archive-input":');
 release();release=null;mode='normal';
 await expect(page.locator('[data-archive-notice]:visible')).toContainText('Chat archived');
 await expect(settings).toBeVisible();await expect(settingsEdit).toHaveValue('{"unsaved-archive-input":');
 assert.equal((await state()).view.workSurface||'chat','chat');
 assert.ok(!JSON.stringify(await state()).includes('{"unsaved-archive-input":'));
 await page.screenshot({path:output+'/unsaved-settings.png'});
 const after=await page.request.get(url+'/fixture').then(r=>r.json());
 assert.deepEqual(after.started,before.started);assert.deepEqual(after.stopped,before.stopped);assert.deepEqual(after.sent,before.sent);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,archiveCalls,restoreCalls,pending:true,workspaceAndManagedDestination:true,undo:true,explicitRejection:true,http200Rejection:true,unknownOutcome:true,roundtripRace:true,queuedNavigationRace:true,stalledNavigationUndo:true,unsavedCanvasRetained:true,unsavedSettingsRetained:true,noncurrentRow:true,rawApiUnchanged:true,peerUnchanged:true,draftAndAttachmentPreserved:true,narrow:true,noRuntimeLifecycleChanges:true}));
}catch(error){failure=await recordCanvasInputFailure(page,error,{outputDir:output,label:'archive'});throw failure}
finally{
 release?.();releaseNavigation?.();
 await finishFixture({label:'Archive feedback fixture',close:[['Archive feedback browser',()=>browser?.close()]],stop:stopFixture,originalError:failure});
}
}
