import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {openChatAdvancedPage} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,readdir,writeFile} from 'node:fs/promises';
import {resolve,sep} from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {readAppearance,settledAppearance,updateAppearance} from './settings-fixture.mjs';
import {assertConversationErrorGeometry,assertConversationErrorFocus} from '../../.build/web-client/tests/conversation-error-geometry.mjs';
import './composer-test-helpers.mjs';
const moduleFailure=process.argv.includes('--module-failure');
const contextLimit=process.argv.includes('--context-limit');
const activeWorker=process.argv.includes('--active-worker');
const lifecycleMode=process.argv.includes('--request-failure')?'request-failure':process.argv.includes('--completed-runtime-fault')?'completed-runtime-fault':null;
const output=process.env.AMPLIFIER_TEST_ARTIFACTS?resolve(process.env.AMPLIFIER_TEST_ARTIFACTS)+sep:fileURLToPath(new URL('../../output/conversation-details-proof/',import.meta.url));
await mkdir(output,{recursive:true});
if(process.env.AMPLIFIER_TEST_ARTIFACTS)assert.deepEqual(await readdir(output),[],'AMPLIFIER_TEST_ARTIFACTS must be a new or empty directory; existing evidence must not be overwritten');
const pythonArgs=process.env.AMPLIFIER_TEST_INSTALLED_PACKAGE?['-I','-B','-u']:[];
const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[...pythonArgs,fileURLToPath(new URL('../../tests/fixtures/session_health_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit'],env:{...process.env,...(moduleFailure?{MODULE_FAILURE_FIXTURE:'1'}:{}),...(contextLimit?{CONTEXT_LIMIT_FIXTURE:'1'}:{}),...(activeWorker?{ACTIVE_WORKER_FIXTURE:'1'}:{}),...(lifecycleMode?{LIFECYCLE_FIXTURE:lifecycleMode}:{})}});
const stop=ownFixture(fixture,{label:'Session health fixture'});
let browser,page,originalError;
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}});fixture.once('exit',code=>reject(Error('Fixture exit '+code)))});
 browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1280,height:900},permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));await page.goto(ready.url);
 // Health geometry uses an unpinned chat column at every width. Do not race a
 // desktop "Close navigation" label that disappears on responsive collapse.
 await showFixtureConversation(page,lifecycleMode?{}:{navPinned:false});
 if(lifecycleMode){
  await lifecycleCheck(page,ready,output);
 }else{
 const actions=[],metrics=[];
 page.on('request',request=>{if(new URL(request.url()).pathname==='/api/actions'&&request.method()==='POST')actions.push(request.postDataJSON())});
 const facts=()=>page.request.get(ready.url+'/fixture/check').then(response=>response.json());
 const action=async(name,args={})=>{
  const result=await page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
  assert.equal(result.accepted,true,result.error||name);return result;
 };
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 const draft='Private unsent recovery inspection draft';
 await expect(composer).toBeEditable();await composer.fill(draft);
 const original=await facts();
 const banner=page.locator('[data-part=conversation-trouble]');
 const summary=contextLimit?'The conversation exceeded the model context limit.':moduleFailure?'The conversation worker could not start.':'Something interrupted this chat.';
 await expect(banner).toBeVisible();await expect(banner.locator('strong')).toHaveText(summary);
 await expect(banner).not.toContainText('provider payload');
 if(contextLimit){
  await expect(banner).toContainText('Choose a model with more context or start a new conversation with a summary.');
  await expect(banner.getByRole('button',{name:'Create recovery copy',exact:true})).toBeVisible();
  if(activeWorker)await expect(banner.getByRole('button',{name:'Create recovery copy',exact:true})).toBeDisabled();
 }else{
  assert.ok((await banner.boundingBox()).height<120);
  await expect(banner.getByRole('button',{name:'Create recovery copy',exact:true})).toHaveCount(0);
 }
 await expect(page.getByRole('button',{name:'Continue conversation',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Conversation details',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Copy session ID',exact:true})).toHaveCount(0);
 await page.screenshot({path:output+'compact-error.png'});
 // Current complete profiles select Reference deltas; retired whole-theme CSS
 // is inert, not an alternate styling input to restore for this regression.
 const originalAppearance=await settledAppearance(page);
 const delta='#amp-one{--a-accent:light-dark(#137c8b,#64c6d2)}';
 const savedTheme=(await action('theme.save',{name:'Recovery inspection delta',css:delta})).result;
 assert.equal(savedTheme.packageVersion,2);assert.equal(savedTheme.base,'reference-v1');
 assert.deepEqual((await readAppearance(page)).effective,originalAppearance.effective,'Saving does not select an appearance');
 for(const theme of [{id:originalAppearance.effective.appearanceId,css:originalAppearance.theme.css,label:'current'},{id:savedTheme.id,css:delta,label:'saved'}]){
 const applied=await updateAppearance(page,{appearanceId:theme.id});
 assert.equal(applied.effective.appearanceId,theme.id);
 if(theme.id===savedTheme.id)assert.equal(applied.theme.fingerprint,savedTheme.fingerprint);
 for(const width of [390,320]){
  await page.setViewportSize({width,height:844});
  await expect(page.locator('#navigation-toggle')).toHaveAttribute('aria-expanded','false');
  await expect(banner).toBeVisible();
  for(const open of [false,true]){
   const disclosure=banner.locator('details');
   if(await disclosure.evaluate(node=>node.open)!==open)await disclosure.locator(':scope>summary').click();
   if(contextLimit&&open)await banner.evaluate(node=>node.scrollTo(0,0));
   metrics.push(await assertConversationErrorGeometry({banner,composer:page.locator('form.a-composer'),label:`${theme.label}-${width}-${open}`}));
   const measure=()=>banner.evaluate(element=>{
    const box=element.getBoundingClientRect(),style=getComputedStyle(element);
    const content=element.querySelector('.a-conversation-trouble-content').getBoundingClientRect();
    const text=element.querySelector('.a-conversation-trouble-content>span').getBoundingClientRect();
    const actions=element.querySelector('.a-conversation-trouble-actions').getBoundingClientRect();
    const dismiss=element.querySelector('.a-conversation-trouble-dismiss')?.getBoundingClientRect();
    // Closed native details descendants can have layout boxes without being
    // painted. Visibility, not rectangle containment, chooses the controls.
    const controls=[...element.querySelectorAll('.a-conversation-trouble-actions button,.a-conversation-trouble-actions summary')].filter(node=>node.checkVisibility({visibilityProperty:true})&&node.getClientRects().length).map(node=>{
     const r=node.getBoundingClientRect();return {text:node.textContent,tag:node.tagName,
      visible:node.checkVisibility({visibilityProperty:true}),
      closedDisclosure:!!node.closest('details:not([open])'),
      left:r.left,right:r.right,top:r.top,bottom:r.bottom};
    });
    const innerWidth=element.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight);
    return {left:box.left,right:box.right,bottom:box.bottom,contentWidth:content.width,expectedContentWidth:innerWidth-(dismiss?dismiss.width+parseFloat(style.columnGap):0),
     textWidth:text.width,textBottom:text.bottom,actionsTop:actions.top,controls,scrollHeight:element.scrollHeight,clientHeight:element.clientHeight};
   });
   const bounds=await measure();
   await writeFile(output+`geometry-${theme.label}-${width}-${open?'open':'closed'}.json`,JSON.stringify(bounds,null,2)+'\n');
   assert.ok(Math.abs(bounds.contentWidth-bounds.expectedContentWidth)<=1,'content fills the available width minus the separate dismiss column');
   assert.ok(Math.abs(bounds.textWidth-bounds.contentWidth)<=1,'explanation keeps its full column, separate from dismissal');
   assert.ok(bounds.actionsTop>=bounds.textBottom,'actions follow the explanation');
   const expectedControls=[...(contextLimit?['Create recovery copy']:[]),'Details & reporting',...(open?['Technical details','Report a problem']:[])];
   assert.deepEqual(bounds.controls.map(control=>control.text),expectedControls,'Every visible action, including disabled recovery, must be measured');
   const assertContained=value=>{
    const bottom=contextLimit&&open?flowBottom:value.bottom;
    for(const control of value.controls)assert.ok(control.left>=value.left&&control.right<=value.right+1&&control.top>=value.textBottom&&control.bottom<=bottom+1,'actions follow text and remain inside banner');
   };
   // Expanded long notices have an intentional height cap and scrollport.
   // Their controls must stay in normal flow, then be reachable individually.
   const flowBottom=contextLimit&&open?await banner.locator('.a-conversation-trouble-actions').evaluate(node=>node.getBoundingClientRect().bottom):bounds.bottom;
   assertContained(bounds);
   if(contextLimit&&open)metrics.push(await assertContextNoticeReach(page,banner,`${theme.label}-${width}`,activeWorker,theme.label==='current'&&width===390));
   else assert.ok(bounds.scrollHeight<=bounds.clientHeight+1,'failure explanation and actions are not clipped');
   if(theme.label==='current'&&width===390){
    const label=open?'Technical details':'Details & reporting';
    const control=open?disclosure.getByRole('button',{name:label,exact:true}):disclosure.locator(':scope>summary');
    const originalStyle=await control.getAttribute('style');
    try{
     // A visible control below the banner must remain in the measurement and
     // fail the same oracle; filtering only already-fitting boxes would hide it.
     await control.evaluate(node=>{node.style.transform='translateY(1000px)'});
     const outside=await measure();
     assert.deepEqual(outside.controls.map(row=>row.text),expectedControls);
     assert.ok(outside.controls.find(row=>row.text===label).visible);
     assert.throws(()=>assertContained(outside),/actions follow text and remain inside banner/);
     await writeFile(output+`geometry-negative-${open?'open':'closed'}.json`,JSON.stringify(outside,null,2)+'\n');
    }finally{
     await control.evaluate((node,style)=>{if(style===null)node.removeAttribute('style');else node.setAttribute('style',style)},originalStyle);
    }
    assertContained(await measure());
   }
   await page.screenshot({path:output+`error-${theme.label}-${width}-${open?'open':'closed'}.png`});
  }
 }
 assert.equal((await readAppearance(page)).theme.css,theme.css,'responsive layout leaves selected appearance bytes unchanged');
 if(theme.id===savedTheme.id){
  const retained=(await action('theme.read',{id:savedTheme.id})).result;
  assert.equal(retained.css,delta);assert.equal(retained.fingerprint,savedTheme.fingerprint);
 }
 }
 await updateAppearance(page,originalAppearance.effective);
 assert.deepEqual((await readAppearance(page)).effective,originalAppearance.effective);
 await page.setViewportSize({width:1280,height:900});
 async function openRecovery(){
  await openChatAdvancedPage(page,'Context & recovery');
  const recovery=page.getByRole('dialog',{name:'Context & recovery',exact:true});
  const disclosure=recovery.locator('details').filter({has:page.locator('summary',{hasText:'Recovery & recorded errors'})});
  if(!await disclosure.evaluate(el=>el.open))await disclosure.locator(':scope > summary').click();
  return recovery;
 }
 const disclosure=banner.locator('details');
 if(!await disclosure.evaluate(node=>node.open))await disclosure.locator(':scope>summary').click();
 await disclosure.getByRole('button',{name:'Technical details',exact:true}).click();
 let dialog=page.getByRole('dialog',{name:'Error details',exact:true});
 await expect(dialog).toBeVisible();
 assert.equal(actions.filter(row=>row.action==='session.inspect').length,0,'Opening focused details is passive; copying requests fresh diagnostics');
 await expect(dialog.getByRole('button',{name:'Hide details',exact:true})).toHaveCount(0);
 if(moduleFailure){
  await expect(dialog.getByText('Some parts of this chat could not load',{exact:true})).toBeVisible();
  await expect(dialog.getByText('hook-fixture',{exact:true})).toBeVisible();
  await expect(dialog.getByRole('listitem').filter({hasText:'Check the declared module type and metadata'})).toBeVisible();
  await expect(dialog.locator('summary').filter({hasText:'Runtime message'})).toHaveCount(0);
 }else if(contextLimit) await expect(dialog.getByText('The conversation exceeded the model context limit.',{exact:true})).toBeVisible();
 else await expect(dialog.getByText('Recorded problem',{exact:true})).toBeVisible();
 const copy=dialog.getByRole('button',{name:'Copy session ID',exact:true});
 assert.equal(await copy.textContent(),'');await copy.click();assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),ready.sessionId);
 await dialog.getByRole('button',{name:'Copy diagnostics',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toContain('"capturedAt"');
 if(!moduleFailure&&!contextLimit)await expect(dialog.getByText('The provider rejected an image or computer-tool result in the conversation context.',{exact:true})).toBeVisible();
 const diagnostics=JSON.parse(await page.evaluate(()=>navigator.clipboard.readText()));if(moduleFailure)assert.equal(diagnostics.moduleFailures[0].reason_code,'invalid_module_metadata');else assert.equal(diagnostics.failure.category,contextLimit?'context_limit':'invalid_image');
 assert.equal(diagnostics.sessionId,ready.sessionId);
 assert.equal(actions.filter(row=>row.action==='session.inspect').length,1,'One deliberate diagnostic read');
 await expect(composer).toHaveDraft(draft);
 const inspected=await facts();assert.equal(inspected.originalUnchanged,true);assert.equal(inspected.sessions,1);
 assert.deepEqual(inspected.calls,original.calls,'Read-only inspection must not prepare or submit runtime work');
 assert.deepEqual(inspected.source.selection,original.source.selection);assert.equal(inspected.source.bundle,original.source.bundle);
 // Naming is its own everyday chat page; it no longer lives in diagnostics.
 await dialog.getByRole('button',{name:'Close panel',exact:true}).click();await expect(dialog).toBeHidden();
 await page.getByRole('button',{name:'Chat',exact:true}).click();
 await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Rename',exact:true}).click();
 dialog=page.getByRole('dialog',{name:'Rename',exact:true});
 const automatic=dialog.getByRole('checkbox',{name:'Automatic chat naming'});
 const refresh=dialog.getByRole('button',{name:'Regenerate chat name'});
 await automatic.check();await expect(automatic).toBeChecked();
 await refresh.click();await expect(refresh).toBeDisabled();
 await expect(dialog.getByLabel('Chat name',{exact:true})).toHaveValue('Browser investigation');
 await expect(automatic).toBeChecked();
 await dialog.getByLabel('Chat name',{exact:true}).fill('My investigation');
 await dialog.getByRole('button',{name:'Save name',exact:true}).click();
 await expect(automatic).not.toBeChecked();
 const afterNaming=await facts();assert.equal(afterNaming.namingCalls,1);
 assert.deepEqual(afterNaming.source.selection,original.source.selection);assert.equal(afterNaming.source.bundle,original.source.bundle);
 await dialog.getByRole('button',{name:'Close panel',exact:true}).click();await expect(dialog).toBeHidden();
 dialog=await openRecovery();
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:output+'mobile-details.png'});
 const recoveryDetails=dialog.locator('.a-conversation-details:visible');
 assert.ok(await recoveryDetails.evaluate(element=>element.scrollWidth<=element.clientWidth));
 const recoveryButton=dialog.getByRole('button',{name:'Create recovery copy',exact:true});
 if(activeWorker){
  await recoveryButton.scrollIntoViewIfNeeded();await expect(recoveryButton).toBeDisabled();
  const refused=await page.evaluate(async id=>{
   try{return await window.amplifier.dispatch('session.recover',{id})}
   catch(error){return {accepted:false,status:error.status,message:error.message}}
  },ready.sessionId);
  assert.equal(refused.accepted,false,'Host also refuses recovery while a worker remains active');
  assert.equal(refused.status,409);
  assert.equal(refused.message,'Wait for delegated work to stop before creating a recovery copy.');
  await dialog.getByRole('button',{name:'Close panel',exact:true}).click();
 }else if(moduleFailure){
  // A startup/module problem does not make readable-history copying a remedy.
  await expect(recoveryButton).toHaveCount(0);
  await dialog.getByRole('button',{name:'Close panel',exact:true}).click();
 }else if(contextLimit){
  await expect(recoveryButton).toBeEnabled();
  await dialog.getByRole('button',{name:'Close panel',exact:true}).click();
  await banner.getByRole('button',{name:'Create recovery copy',exact:true}).click();
 }else{
  await recoveryButton.scrollIntoViewIfNeeded();await expect(recoveryButton).toBeEnabled();
  await recoveryButton.click();
  await dialog.getByRole('button',{name:'Close panel',exact:true}).click();
 }
 if(!activeWorker&&!moduleFailure){
  await expect(page.getByText(/Recovery copy · Readable history retained/)).toBeVisible();
  const state=await page.evaluate(()=>window.amplifier.getState());assert.notEqual(state.selectedSessionId,ready.sessionId);
  const check=await (await page.request.get(ready.url+'/fixture/check')).json();assert.equal(check.originalUnchanged,true);assert.equal(check.sessions,2);
  await page.reload();await expect(page.getByText(/Recovery copy · Readable history retained/)).toBeVisible();
  const copied=await facts();assert.equal(copied.recovered.length,1);
  assert.equal(copied.recovered[0].status,'idle');assert.equal(copied.recovered[0].sourceSessionId,ready.sessionId);
  assert.equal(copied.recovered[0].workReplayed,false);assert.ok(copied.recovered[0].messageCount>0);
  assert.equal(copied.recovered[0].hasExecutableToolHistory,false);
  assert.equal(copied.recovered[0].hasSafetyReference,true);
  assert.equal(copied.recovered[0].bundle,original.source.bundle);
  assert.deepEqual(copied.recovered[0].selection,original.source.selection);
  await action('session.select',{id:ready.sessionId});await expect(composer).toHaveDraft(draft);
 }else{
  const check=await facts();assert.equal(check.sessions,1);assert.equal(check.originalUnchanged,true);
  assert.equal(check.selected,ready.sessionId);
  assert.deepEqual(check.recovered,[]);await expect(composer).toHaveDraft(draft);
  await page.reload();await showFixtureConversation(page);
  await expect(composer).toHaveDraft(draft);
  if(moduleFailure)await expect(banner.getByRole('button',{name:'Create recovery copy',exact:true})).toHaveCount(0);
  else await expect(banner.getByRole('button',{name:'Create recovery copy',exact:true})).toBeDisabled();
 }
 const final=await facts();
 assert.equal(final.originalUnchanged,true);assert.equal(final.namingCalls,1);
 assert.deepEqual(final.calls,afterNaming.calls,'Recovery inspection/copy/reload never starts or controls a runtime');
 assert.deepEqual(final.source.selection,original.source.selection);assert.equal(final.source.bundle,original.source.bundle);
 assert.equal(final.calls.some(row=>row.operation==='send'),false,'No automatic input, including after reload/recovery');
 assert.deepEqual(actions.filter(row=>['conversation.send','conversation.retry','conversation.stop','conversation.forceStop','runtime.control','configuration.apply','bundle.switch','worker.spawn','call.start'].includes(row.action)),[],'Review, naming and recovery-copy creation submit no execution or context-control actions');
 assert.ok(final.calls.every(row=>row.sessionId===ready.sessionId&&['start','session.naming','configuration.inspect','configuration.providers'].includes(row.operation)),'Only naming preparation and read-only runtime controls are permitted');
 assert.equal(actions.filter(row=>row.action==='session.recover').length,moduleFailure?0:1);
 await writeFile(output+'health-facts.json',JSON.stringify({variant:moduleFailure?'module-failure':contextLimit?(activeWorker?'context-limit-active-worker':'context-limit'):'default',metrics,original,final,actions:actions.map(({action,args})=>({action,args}))},null,2)+'\n');
 assert.deepEqual(errors,[]);
 console.log('Session health browser passed: compact error, inline copy, Auto naming, one regeneration, manual name precedence, mobile bounds, recovery and original transcript preservation.');
 }
 assert.deepEqual(errors,[]);
}catch(error){
 originalError=error;
 await writeFile(output+'failure.json',JSON.stringify({error:String(error.stack||error)},null,2)+'\n').catch(()=>{});
 await page?.screenshot({path:output+'failure.png',fullPage:true}).catch(()=>{});
 throw error;
}
finally{await finishFixture({label:'Session health browser',close:[['browser',()=>browser?.close()]],stop,originalError});}

async function assertContextNoticeReach(page,banner,label,disabledRecovery,negativeControls){
 const readReach=()=>banner.evaluate(node=>{
  const box=element=>{const r=element.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom}};
  const clip=element=>{
   let r={left:0,right:innerWidth,top:0,bottom:innerHeight};
   for(let p=element;p;p=p.parentElement){
    const s=getComputedStyle(p),b=p.getBoundingClientRect();
    if(/auto|scroll|hidden|clip/.test(s.overflowX)){r.left=Math.max(r.left,b.left+p.clientLeft);r.right=Math.min(r.right,b.left+p.clientLeft+p.clientWidth)}
    if(/auto|scroll|hidden|clip/.test(s.overflowY)){r.top=Math.max(r.top,b.top+p.clientTop);r.bottom=Math.min(r.bottom,b.top+p.clientTop+p.clientHeight)}
   }
   return r;
  };
  const contained=(r,c)=>r.left>=c.left-1&&r.right<=c.right+1&&r.top>=c.top-1&&r.bottom<=c.bottom+1;
  node.scrollTop=0;
  const original=box(node),style=getComputedStyle(node),range=node.scrollHeight-node.clientHeight;
  node.scrollTop=node.scrollHeight;
  const bottomScroll=node.scrollTop;
  node.scrollTop=0;
  const lines=[];
  for(const parent of node.querySelectorAll('.a-conversation-trouble-content>span,details[open]>p')){
   const walker=document.createTreeWalker(parent,NodeFilter.SHOW_TEXT);
   for(let text=walker.nextNode();text;text=walker.nextNode()){
    if(!text.textContent.trim()||!text.parentElement.checkVisibility({visibilityProperty:true}))continue;
    const selection=document.createRange();selection.selectNodeContents(text);
    const count=selection.getClientRects().length;
    for(let index=0;index<count;index++){
     node.scrollTop=0;
     const first=selection.getClientRects()[index],viewport=clip(text.parentElement);
     node.scrollTop=(first.top+first.bottom-viewport.top-viewport.bottom)/2;
     const current=selection.getClientRects()[index],port=clip(text.parentElement);
     lines.push({top:current.top,bottom:current.bottom,left:current.left,right:current.right,scrollTop:node.scrollTop,clip:port,reachable:contained(current,port)});
    }
   }
  }
  const controls=[...node.querySelectorAll('button,summary')].filter(el=>el.checkVisibility({visibilityProperty:true})).map(el=>{
   el.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});
   const r=box(el),c=clip(el.parentElement),hit=document.elementFromPoint((r.left+r.right)/2,(r.top+r.bottom)/2);
   return {label:el.getAttribute('aria-label')||el.textContent,disabled:el.disabled===true,box:r,clip:c,reachable:contained(r,c),hit:!!hit&&(hit===el||el.contains(hit))};
  });
  node.scrollTop=0;
  return {range,bottomScroll,restoredScroll:node.scrollTop,overflowY:style.overflowY,maxHeight:style.maxHeight,
   height:original.bottom-original.top,expectedCap:Math.min(innerHeight*.4,parseFloat(getComputedStyle(document.documentElement).fontSize)*20),lines,controls};
 });
 const assertReach=value=>{
  assert.ok(value.height<=value.expectedCap+1,'Long notice preserves its shared height cap');
  assert.ok(['auto','scroll'].includes(value.overflowY),'Expanded notice keeps usable scrolling');
  assert.ok(Math.abs(value.bottomScroll-value.range)<=1&&value.restoredScroll===0,'Notice reaches its actual scroll end and returns');
  assert.ok(value.lines.length>0&&value.lines.every(line=>line.reachable),'Every explanation line is reachable within ancestor client clips');
  assert.deepEqual(value.controls.map(control=>control.label),['Create recovery copy','Details & reporting','Technical details','Report a problem','Dismiss conversation error']);
  assert.ok(value.controls.every(control=>control.reachable),'Every control is reachable within ancestor client clips');
  assert.ok(value.controls.every(control=>control.hit),'Every control has an unobstructed hit target');
  assert.equal(value.controls[0].disabled,disabledRecovery);
 };
 const measured=await readReach();
 await writeFile(output+`reach-${label}.json`,JSON.stringify(measured,null,2)+'\n');
 assertReach(measured);
 if(negativeControls){
  const originalStyle=await banner.getAttribute('style');
  try{
   await banner.evaluate(node=>{node.style.maxHeight='80px';node.style.overflowY='clip'});
   const clipped=await readReach();
   assert.ok(clipped.range>0);
   assert.throws(()=>assertReach(clipped),/Expanded notice keeps usable scrolling/);
   await writeFile(output+'reach-negative-clipped.json',JSON.stringify(clipped,null,2)+'\n');
  }finally{
   await banner.evaluate((node,style)=>{if(style===null)node.removeAttribute('style');else node.setAttribute('style',style)},originalStyle);
  }
  assertReach(await readReach());
  const paragraph=banner.locator('details[open]>p'),paragraphStyle=await paragraph.getAttribute('style');
  try{
   await paragraph.evaluate(node=>{node.style.maxHeight='1px';node.style.overflow='hidden'});
   const clippedText=await readReach();
   assert.throws(()=>assertReach(clippedText),/Every explanation line is reachable within ancestor client clips/);
   await writeFile(output+'reach-negative-inner-text.json',JSON.stringify(clippedText,null,2)+'\n');
  }finally{
   await paragraph.evaluate((node,style)=>{if(style===null)node.removeAttribute('style');else node.setAttribute('style',style)},paragraphStyle);
  }
  assertReach(await readReach());
  try{
   await page.evaluate(()=>{
    const cover=document.createElement('div');cover.id='health-test-occluder';
    cover.style.cssText='position:fixed;inset:0;z-index:2147483647;pointer-events:auto';
    document.body.append(cover);
   });
   const occluded=await readReach();
   assert.throws(()=>assertReach(occluded),/Every control has an unobstructed hit target/);
   await writeFile(output+'reach-negative-occluded.json',JSON.stringify(occluded,null,2)+'\n');
  }finally{await page.evaluate(()=>document.getElementById('health-test-occluder')?.remove())}
  assertReach(await readReach());
 }
 // Native keyboard traversal, not synthetic clicks, must reach each enabled
 // disclosure action without recovery, reporting or dismissal.
 await page.keyboard.press('Tab');
 const summary=banner.locator('summary'),technical=banner.getByRole('button',{name:'Technical details',exact:true});
 const report=banner.getByRole('button',{name:'Report a problem',exact:true}),dismiss=banner.getByRole('button',{name:'Dismiss conversation error',exact:true});
 for(const control of [summary,technical,report,dismiss,...(!disabledRecovery?[banner.getByRole('button',{name:'Create recovery copy',exact:true})]:[])])await assertConversationErrorFocus(control,label);
 const keyboardReach=async control=>{
  await expect(control).toBeFocused();
  const actual=await control.evaluate(node=>{
   const r=node.getBoundingClientRect();let top=0,bottom=innerHeight,left=0,right=innerWidth;
   for(let p=node.parentElement;p;p=p.parentElement){
    const s=getComputedStyle(p),b=p.getBoundingClientRect();
    if(/auto|scroll|hidden|clip/.test(s.overflowY)){top=Math.max(top,b.top+p.clientTop);bottom=Math.min(bottom,b.top+p.clientTop+p.clientHeight)}
    if(/auto|scroll|hidden|clip/.test(s.overflowX)){left=Math.max(left,b.left+p.clientLeft);right=Math.min(right,b.left+p.clientLeft+p.clientWidth)}
   }
   const hit=document.elementFromPoint((r.left+r.right)/2,(r.top+r.bottom)/2);
   return {reachable:r.top>=top-1&&r.bottom<=bottom+1&&r.left>=left-1&&r.right<=right+1,hit:!!hit&&(node===hit||node.contains(hit))};
  });
  assert.deepEqual(actual,{reachable:true,hit:true},'Native Tab reaches a fully usable control without corrective scrolling');
 };
 await summary.focus();
 for(const control of [technical,report,dismiss]){await page.keyboard.press('Tab');await keyboardReach(control)}
 await page.keyboard.press('Shift+Tab');await keyboardReach(report);
 await banner.evaluate(node=>node.scrollTo(0,0));
 assertReach(await readReach());
 return {label,reach:measured};
}

async function lifecycleCheck(page,ready,output){
 const actions=[];
 page.on('request',request=>{if(request.url().endsWith('/api/actions')&&request.method()==='POST')actions.push(request.postDataJSON())});
 const facts=()=>page.request.get(ready.url+'/fixture/check').then(response=>response.json());
 const input=(state,id)=>state.messages.find(row=>row.inputId===id&&row.role==='user');
 const before=await facts(),completed=input(before,'completed-input');
 assert.equal(completed.inputStatus.phase,'done');
 assert.equal(before.messages.find(row=>row.role==='assistant').response.status,'completed');
 const completedRow=page.locator(`[data-message-id="${completed.id}"]`);
 await expect(completedRow).toBeVisible();
 await expect(page.getByText('The saved answer is complete.',{exact:true})).toBeVisible();
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await expect(composer).toBeEditable();
 await composer.fill('Private unsent lifecycle draft');
 if(lifecycleMode==='completed-runtime-fault'){
  const response=await page.request.post(ready.url+'/fixture/late-runtime-fault');
  assert.equal(response.status(),200);
  await expect.poll(async()=>(await facts()).runtimeFaults.length).toBe(1);
  await expect(page.locator('[data-part=conversation-trouble]')).toHaveCount(0);
  await expect(page.locator('[data-input-phase=failed]')).toHaveCount(0);
  await expect(page.getByText('Work stopped',{exact:true})).toHaveCount(0);
  assert.notEqual((await facts()).status,'stopped','native close is not a human Stop');
  assert.equal((await facts()).interruptionRevision,0,'process close is not a human Stop');
 }else{
  const failed=input(before,'failed-input');
  assert.equal(failed.inputStatus.phase,'failed');
  assert.equal(failed.inputStatus.failure.category,'provider_unavailable');
  const failedRow=page.locator(`[data-message-id="${failed.id}"]`);
  await expect(failedRow.locator('[data-input-phase=failed]')).toBeVisible();
  await expect(page.locator('[data-part=conversation-trouble]')).toBeVisible();
  await expect(completedRow.locator('[data-input-phase=failed]')).toHaveCount(0);
 }
 for(const width of [390,320]){
  await page.setViewportSize({width,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'failure UX fits narrow viewport');
  await page.screenshot({path:output+`lifecycle-${lifecycleMode}-${width}.png`});
 }
 await expect(composer).toHaveDraft('Private unsent lifecycle draft');
 await expect(page.getByRole('button',{name:'Continue conversation',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Check readiness',exact:true})).toHaveCount(0);
 let check=await facts();
 assert.equal(check.completedUnchanged,true);assert.equal(check.originalUnchanged,true);
 assert.equal(check.sessions,1);assert.equal(check.selected,ready.sessionId);assert.deepEqual(check.calls,[]);
 assert.equal(input(check,'completed-input').inputStatus.phase,'done');
 assert.deepEqual(actions.filter(row=>['conversation.send','session.recover','runtime.restart','runtime.stop'].includes(row.action)),[]);
 await page.reload();await showFixtureConversation(page);
 await expect(page.getByText('The saved answer is complete.',{exact:true})).toBeVisible();
 await expect(composer).toHaveDraft('Private unsent lifecycle draft');
 if(lifecycleMode==='completed-runtime-fault')await expect(page.locator('[data-part=conversation-trouble]')).toHaveCount(0);
 // One new explicit message is the normal continuation; diagnostics/reload sent nothing.
 await composer.fill('A new explicit request');
 await composer.press('Enter');
 await expect(page.getByText('A fresh explicit message worked.',{exact:true})).toBeVisible();
 await expect(page.locator('[data-part=conversation-trouble]')).toHaveCount(0);
 check=await facts();
 assert.equal(check.calls.filter(row=>row.operation==='send').length,1);
 assert.equal(check.completedUnchanged,true);
 assert.equal(input(check,'completed-input').inputStatus.phase,'done');
 if(lifecycleMode==='request-failure')assert.equal(input(check,'failed-input').inputStatus.phase,'failed','older request outcome remains local history');
 assert.equal(check.sessions,1);assert.equal(check.selected,ready.sessionId);
 if(lifecycleMode==='completed-runtime-fault'){
  assert.equal((await page.request.post(ready.url+'/fixture/failed-save')).status(),200);
  await expect.poll(async()=>(await facts()).failure?.category).toBe('persistence');
  await expect(page.locator('[data-part=conversation-trouble]')).toBeVisible();
  assert.equal((await facts()).status,'error','closure must not suppress a failed save');
  assert.equal((await facts()).interruptionRevision,0);
  assert.equal(input(await facts(),'completed-input').inputStatus.phase,'done');
  await page.screenshot({path:output+'lifecycle-detached-failed-save.png'});
  assert.equal((await page.request.post(ready.url+'/fixture/human-stop')).status(),200);
  await expect.poll(async()=>(await facts()).status).toBe('stopped');
  assert.equal((await facts()).interruptionRevision,1);
  assert.equal((await facts()).calls.filter(row=>row.operation==='send').length,1,'late callbacks never revive actual Stop');
  assert.equal(input(await facts(),'completed-input').inputStatus.phase,'done');
 }
 if(lifecycleMode==='request-failure'){
  // A later, different failure must not replace the diagnosis selected from history.
  assert.equal((await page.request.post(ready.url+'/fixture/newer-failure')).status(),200);
  await expect(page.locator('[data-part=conversation-trouble]')).toContainText('credentials');
  const failedRow=page.locator(`[data-message-id="${input(check,'failed-input').id}"]`);
  const details=failedRow.locator('[data-input-phase=failed]');
  await details.locator(':scope > summary').click();
  await expect(details).toContainText('The selected provider is unavailable.');
  await details.getByRole('button',{name:'Report this problem',exact:true}).click();
  const feedback=page.locator('[data-part=feedback]');
  await expect(feedback).toContainText('Reporting the recorded request you selected.');
  await expect(feedback).toContainText('microsoft/amplifier-unified');
  const include=feedback.getByRole('checkbox',{name:'Include reproduction diagnostics'});
  await expect(include).not.toBeChecked();
  await feedback.getByLabel('Title',{exact:true}).fill('Historical lifecycle request');
  await feedback.getByLabel('Details',{exact:true}).fill('Deliberately entered nonprivate reproduction steps.');
  await include.check();
  const preview=feedback.locator('[data-part=feedback-diagnostics]');
  await preview.locator(':scope > summary').click();
  await expect(preview.locator('pre')).toContainText('provider_unavailable');
  await expect(preview.locator('pre')).not.toContainText('authentication');
  const diagnostic=JSON.parse(await preview.locator('pre').innerText());
  assert.equal(diagnostic.failureReport.requestOutcome,'generation.failed');
  assert.equal(diagnostic.troubleshooting.recordedFailure.inputRelation,'earlier');
  assert.ok(!JSON.stringify(diagnostic).includes('PRIVATE'));
  assert.ok(!JSON.stringify(diagnostic).includes('synthetic/workspace'));
  assert.ok(!JSON.stringify(diagnostic).includes('Private unsent lifecycle draft'));
  assert.ok(!JSON.stringify(diagnostic).includes('failed-input'),'shared report hashes private correlation IDs');
  await page.screenshot({path:output+'lifecycle-historical-report-optin.png'});
  await feedback.getByRole('button',{name:'Prepare local report',exact:true}).click();
  const local=feedback.getByRole('textbox',{name:'Local feedback report preview',exact:true});
  await expect(local).toHaveValue(/provider_unavailable/);
  assert.doesNotMatch(await local.inputValue(),/authentication|PRIVATE|synthetic\/workspace/);
  await page.screenshot({path:output+'lifecycle-historical-local-report.png'});
  assert.equal((await facts()).calls.filter(row=>row.operation==='send').length,2,'only the two explicit requests ran; reporting never sends another input');
 }
 await writeFile(output+`lifecycle-${lifecycleMode}-facts.json`,JSON.stringify(await facts(),null,2)+'\n');
 console.log(`Session health ${lifecycleMode} passed: truthful exact-input outcome, saved answer/draft retained, narrow/reload safe, no automatic work and one ordinary explicit send.`);
}
