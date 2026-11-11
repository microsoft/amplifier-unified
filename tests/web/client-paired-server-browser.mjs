// Integration: actual source server + installed independent web artifact, or
// explicit installed-wheel mode. Only the Python host gets a Node-free PATH.
// Only runtime/providers are synthetic. Fixture selection forces the real SSE
// path; server build/version metadata, auth, actions and history are untouched.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {isAbsolute,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {startFixture} from './fixture-readiness.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';

const [serverRoot,recordPath,stateParent]=process.argv.slice(2);
if(!serverRoot||!recordPath||!stateParent||!process.env.AMPLIFIER_TEST_PYTHON)throw Error('Supply server root, assembly record, state parent and AMPLIFIER_TEST_PYTHON');
const record=JSON.parse(await readFile(recordPath)),manifest=record.client.manifest;
const installedPackage=process.env.AMPLIFIER_TEST_INSTALLED_PACKAGE;
const qualification=installedPackage?'actual-installed-wheel-with-bundled-web-assets':
 'actual-source-server-with-installed-independent-web-artifact';
const hostEnv={...process.env,PYTHONDONTWRITEBYTECODE:'1'};
if(installedPackage){
 assert.ok(isAbsolute(installedPackage),'Installed package must be explicitly absolute');
 assert.ok(isAbsolute(process.env.AMPLIFIER_TEST_PYTHON),'Installed fixture requires its exact Python executable');
 assert.ok(process.env.AMPLIFIER_TEST_HOST_PATH&&isAbsolute(process.env.AMPLIFIER_TEST_HOST_PATH),
  'Installed fixture requires an explicit Node-free host PATH directory');
 for(const key of Object.keys(hostEnv))if(['PATH','PYTHONPATH'].includes(key.toUpperCase()))delete hostEnv[key];
 hostEnv.PATH=process.env.AMPLIFIER_TEST_HOST_PATH;
}
if(process.env.AMPLIFIER_TEST_CLIENT_REVISION)
 assert.equal(manifest.source.revision,process.env.AMPLIFIER_TEST_CLIENT_REVISION,'Qualify the exact selected client, not a later merge');
const artifact=process.env.AMPLIFIER_TEST_ARTIFACTS||join(stateParent,'paired-browser');
await mkdir(artifact,{recursive:true});
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');

async function qualifyQuietRetainedCopies({page,composer,history,actions,privateRequests,screenshot,base}){
 const payloadKey='amplifier.messageOutbox.v1',metadataKey='amplifier.legacyOutboxNotice.v1';
 const oldNotices=[
  'This tab retains earlier message copies with no verified host identity. Their delivery is unknown. Nothing was sent again.',
  'Earlier pending messages have no verified host identity. They remain saved in this browser and were not sent again.',
 ];
 const draft='Unfinished paired-server draft';
 const original=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 const action=async(name,args)=>{const result=await page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);assert.equal(result.accepted,true);return result};
 // Create/select through the actual host, never a substituted state response.
 // This extension checks quiet retention, not a replacement recovery surface.
 // Existing send/reconnect checks and message-delivery-browser keep their own
 // scoped delivery/retry acceptance; nothing here adopts an unbound copy.
 await action('session.create',{title:'Retained-copy navigation control'});
 const other=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 assert.notEqual(other,original);
 await action('session.select',{id:original});
 await expect(composer).toHaveText(draft);
 const before=await history(),actionStart=actions.length;
 const retained=[
  {id:'paired-unbound-unknown',commandId:'paired-unbound-input',sessionId:original,createdAt:123,
   text:'paired-private-retained-unknown',status:'unknown',attachmentIds:['paired-private-retained-attachment'],
   attachments:[{id:'paired-private-retained-attachment',name:'paired-private-retained-reference.txt',
    url:'/api/attachments/paired-private-retained-attachment',size:19}],
   document:{version:2,segments:[{type:'text',text:'paired-private-retained-document'}]}},
  {id:'paired-unbound-failed',commandId:'paired-unbound-failure',sessionId:other,createdAt:124,
   text:'paired-private-retained-failed',status:'failed',attachmentIds:[]},
 ];
 const serialize=rows=>'\n'+JSON.stringify(rows,null,2)+'\n';
 let raw=serialize(retained);
 const stored=()=>page.evaluate(([payload,metadata])=>({
  raw:sessionStorage.getItem(payload),metadata:sessionStorage.getItem(metadata),
 }),[payloadKey,metadataKey]);
 assert.deepEqual(JSON.parse((await stored()).raw||'[]'),[],'Accepted sends left no pending local input');
 // Fixture-only seed in this fresh owned context. There is intentionally no
 // scope: matching a real chat ID does not establish a verified host binding.
 await page.evaluate(([payload,metadata,value])=>{
  sessionStorage.setItem(payload,value);sessionStorage.removeItem(metadata);
 },[payloadKey,metadataKey,raw]);
 const checkpoints=[];
 async function check(label,selected=original,expectedDraft=draft){
  // Positive readiness first: an absent banner in an unmounted/failed app is
  // not evidence. Observe the real selected view and usable composer each time.
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(selected);
  await expect(composer).toBeVisible();await expect(composer).toBeEditable();
  await expect(composer).toHaveText(expectedDraft);
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.draft||'')).toBe(expectedDraft);
  await expect(page.locator('[data-part="legacy-outbox-notice"]')).toHaveCount(0);
  for(const text of oldNotices)await expect(page.getByText(text,{exact:true})).toHaveCount(0);
  await expect(page.getByText(/^Retained copies(?: \(\d+\))?$/)).toHaveCount(0);
  await expect(page.getByRole('button',{name:/^Retained copies(?: \(\d+\))?$/})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Hide notice',exact:true})).toHaveCount(0);
  assert.equal((await stored()).raw,raw,'Quiet retention preserves the exact raw payload, including whitespace');
  assert.equal((await stored()).metadata,null,'No legacy notice preference is created');
  assert.equal(await page.locator('body').evaluate(node=>node.outerHTML.includes('paired-private-retained-')),false);
  for(const row of retained)await expect(page.locator(`[data-input-id="${row.commandId}"]`)).toHaveCount(0);
  assert.deepEqual(privateRequests,[],'No retained message, attachment or document is fetched or posted');
  assert.deepEqual(actions.slice(actionStart).filter(row=>row.action.startsWith('conversation.')||
   ['message.edit','session.create','session.delete','session.archive','attachment.add','attachment.remove'].includes(row.action)),[],
  'Retained copies cause no check, resend, edit, delete or conversation creation');
  assert.deepEqual(await history(),before,'Quiet retention and selection preserve canonical messages and runtime counters');
  checkpoints.push(label);
 }
 async function reload(label,selected=original,expectedDraft=draft){
  await page.reload();await showFixtureConversation(page);
  await check(label,selected,expectedDraft);
 }
 await reload('mount-retained-copies');await screenshot('retained-copies-quiet-desktop');
 await action('view.update',{patch:{navWidth:418}});
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.navWidth)).toBe(418);
 await check('unrelated-view-update');
 await action('session.select',{id:other});await check('other-chat',other,'');
 await action('session.draft',{});await check('new-chat',null,'');
 await reload('new-chat-reload',null,'');
 await action('session.select',{id:original});await check('original-draft-restored');

 // Exercise the same owned real SSE disconnect route with the retained rows
 // loaded. This is a fresh passive connection check, never a repeated input.
 const transport=await page.evaluate(()=>({
  opens:streamProbe.reduce((n,source)=>n+source.opens,0),
  baselines:streamProbe.reduce((n,source)=>n+source.frames.state,0),
  clientId:window.amplifier.getState().client.id,
 }));
 const response=await page.request.post(base+'/fixture/disconnect',{data:{clientId:transport.clientId}});
 assert.equal(response.status(),200);assert.equal((await response.json()).disconnected,true);
 await expect.poll(()=>page.evaluate(()=>streamProbe.reduce((n,source)=>n+source.opens,0)),{timeout:15000}).toBeGreaterThan(transport.opens);
 await expect.poll(()=>page.evaluate(()=>streamProbe.reduce((n,source)=>n+source.frames.state,0)),{timeout:15000}).toBeGreaterThan(transport.baselines);
 await check('real-sse-reconnect');

 retained[0]={...retained[0],id:'paired-new-unbound-identity'};
 raw=serialize(retained); // Explicit fixture injection only; no dismissal is needed.
 await page.evaluate(([key,value])=>sessionStorage.setItem(key,value),[payloadKey,raw]);
 await reload('new-unbound-id-remains-quiet');

 const viewport=page.viewportSize();
 try{
  await page.setViewportSize({width:390,height:844});
  await showFixtureConversation(page,{navPinned:false});
  await check('mobile-quiet');
  await expect(composer).toBeInViewport();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),
   'Quiet retained copies do not introduce horizontal overflow on mobile');
  // No removed notice control remains to activate. Keyboard focus/navigation in
  // the existing composer must remain usable without changing or submitting it.
  await composer.focus();await page.keyboard.press('ArrowLeft');await page.keyboard.press('ArrowRight');
  await expect(composer).toBeFocused();await check('composer-keyboard-draft-preserved');
  await screenshot('retained-copies-quiet-mobile');
 }finally{await page.setViewportSize(viewport)}
 return {passed:true,retainedCount:2,payloadBytesPreserved:true,privatePayloadNeverRequested:true,
  warningAbsent:true,badgeAbsent:true,checkpoints,newChatRemainedUncreated:true,
  chatSwitchPreserved:true,sameCountNewIdentityStayedQuiet:true,realSSEReconnectPreserved:true,
  mobileWidth:390,composerKeyboardFocusPreserved:true,
  draftPreserved:true,extraSendsRetriesEditsDeletes:0,canonicalHistoryPreserved:true};
}

async function qualifyAppearance({page,composer,history,actions,screenshot,base}){
 const proof={passed:false,viewports:[],domSnapshots:[],stopInvocations:0,
  stateOrigin:'Authenticated one-shot synthetic seed; actual installed host projection/HTTP/SSE'};
 const prior=await history(),clientId=await page.evaluate(()=>window.amplifier.getState().client.id);
 const request=page.context().request;
 const unauthorized=await fetch(base+'/fixture/appearance',{method:'POST',redirect:'manual',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({clientId})});
 assert.equal(unauthorized.status,307,'The actual host auth gate rejects unauthenticated fixture setup');
 assert.ok(unauthorized.headers.get('location').startsWith('/login'));
 const invalid=await request.post(base+'/fixture/appearance',{
  headers:{Authorization:'Bearer deliberately-invalid-fixture-token'},data:{clientId},maxRedirects:0});
 assert.equal(invalid.status(),307);assert.ok(invalid.headers().location.startsWith('/login'));
 const malformed=await request.post(base+'/fixture/appearance',{data:{clientId,css:'not permitted'}});
 assert.equal(malformed.status(),400,'The setup route accepts no arbitrary state, CSS or paths');
 const frames=()=>page.evaluate(()=>streamProbe.reduce((n,source)=>n+source.frames['state-delta'],0));
 const deltaBefore=await frames();
 const attachmentRequests=[];
 const observeAttachment=request=>{
  if(new URL(request.url()).pathname.startsWith('/api/attachments/'))attachmentRequests.push(request.url());
 };
 page.on('request',observeAttachment);
 const response=await request.post(base+'/fixture/appearance',{data:{clientId}});
 assert.equal(response.status(),200);
 const seed=await response.json();
 assert.equal(seed.pinnedSessionIds.length,48);assert.equal(new Set(seed.pinnedSessionIds).size,48);
 assert.equal(seed.messages,26);assert.match(seed.runtime,/no runtime start, send or native execution/);
 const repeated=await request.post(base+'/fixture/appearance',{data:{clientId}});
 assert.equal(repeated.status(),409,'A seed is not replayable');
 const action=async(name,args)=>{
  const receipt=await page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
  assert.equal(receipt.accepted,true);return receipt;
 };
 await action('session.select',{id:seed.sessionId});
 await action('view.update',{patch:{workSurface:'chat',navPinned:true,navExpanded:true,navWidth:320,
  navSectionsCollapsed:[],navPinnedPage:0}});
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(seed.sessionId);
 await expect.poll(frames).toBeGreaterThan(deltaBefore);
 const selected=()=>page.evaluate(id=>window.amplifier.getState().sessions.find(row=>row.id===id),seed.sessionId);
 await expect.poll(async()=>(await selected())?.messages?.length).toBe(26);
 await expect.poll(async()=>(await selected())?.draftAttachments).toEqual([seed.attachment]);
 const seededHistory=await history(),ids=new Set(seed.pinnedSessionIds);
 assert.deepEqual(seededHistory.sessions.filter(row=>!ids.has(row.id)),prior.sessions,
  'Appearance setup cannot rewrite the earlier accepted conversation history');
 assert.deepEqual(seededHistory.sends,prior.sends);assert.deepEqual(seededHistory.edits,prior.edits);
 assert.deepEqual(seededHistory.pins,[...prior.pins,...seed.pinnedSessionIds]);
 const draft='Keep this paired appearance draft while inspecting the layout.';
 await expect(composer).toBeEditable();await composer.fill(draft);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.draft)).toBe(draft);
 const actionStart=actions.length;
 const pane=page.getByRole('log',{name:'Conversation messages'});
 const scroll=locator=>locator.evaluate(node=>({top:node.scrollTop,height:node.scrollHeight,
  client:node.clientHeight,remaining:node.scrollHeight-node.clientHeight-node.scrollTop}));
 const rect=node=>{const r=node.getBoundingClientRect();return {left:r.left,right:r.right,
  top:r.top,bottom:r.bottom,width:r.width,height:r.height}};
 async function usable(locator,button=false){
  await expect(locator).toBeVisible();if(button)await expect(locator).toBeEnabled();
  const value=await locator.evaluate(node=>{
   const r=node.getBoundingClientRect();
   return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height,
    viewportWidth:innerWidth,viewportHeight:innerHeight,opacity:getComputedStyle(node).opacity,
    hit:node.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2))};
  });
  assert.ok(value.width>0&&value.height>0&&value.left>=-1&&value.right<=value.viewportWidth+1&&
   value.top>=-1&&value.bottom<=value.viewportHeight+1&&value.hit&&value.opacity!=='0',
  'Installed control must be visible, in viewport and unobscured: '+JSON.stringify(value));
  return value;
 }
 async function capture(label){
  const name='appearance-'+label,path=join(artifact,name+'.html');
  await writeFile(path,await page.locator('#amp-one').evaluate(node=>node.outerHTML));
  proof.domSnapshots.push(path);await screenshot(name);
 }
 async function retained(){
  await expect(composer).toHaveText(draft);
  assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),seed.sessionId);
  assert.deepEqual((await selected()).draftAttachments,[seed.attachment]);
  assert.deepEqual(await history(),seededHistory,'Layout gestures cannot change canonical history or runtime counters');
  assert.deepEqual(attachmentRequests,[],'A text attachment chip does not fetch its private content');
  assert.deepEqual(actions.slice(actionStart).filter(row=>/^(conversation\.|worker\.|call\.)/.test(row.action)||
   ['message.edit','session.select','session.create','session.delete','session.archive','attachment.add',
    'attachment.remove','canvas.openAttachment','navigation.pinOrder'].includes(row.action)),[],
  'No execution, history mutation, selection or attachment mutation during appearance checks');
 }
 const viewport=page.viewportSize();
 try{
  for(const width of [1280,390]){
   const cell={width,execution:[]};proof.viewports.push(cell);
   await page.setViewportSize({width,height:width===1280?900:844});
   await showFixtureConversation(page,{navPinned:width===1280});
   if(width===390)await action('view.update',{patch:{navExpanded:false}});
   await expect(pane).toHaveAttribute('aria-busy','false');
   await expect(composer).toBeEditable();
   if(width===1280){
    const sidebar=page.locator('#workspace-navigation .a-nav-content'),footer=page.locator('#workspace-navigation .a-work-footer');
    await expect(sidebar.locator('.a-pinned-chats [data-session-id]')).toHaveCount(48);
    const before=await scroll(sidebar),footerBefore=await usable(footer);
    assert.ok(before.client>100&&before.height-before.client>600,'Installed sidebar genuinely overflows');
    assert.equal(await footer.evaluate(node=>!!node.closest('.a-nav-content')),false);
    const visibleRows=()=>sidebar.evaluate(node=>{
     const box=node.getBoundingClientRect();
     return [...node.querySelectorAll('.a-pinned-chats [data-session-id]')].filter(row=>{
      const r=row.getBoundingClientRect();return r.top>=box.top&&r.bottom<=box.bottom;
     }).map(row=>row.dataset.sessionId);
    });
    const beforeRows=await visibleRows(),transcriptBefore=await scroll(pane),box=await usable(sidebar);
    assert.ok(beforeRows.length>0&&beforeRows.every(id=>ids.has(id)));
    await sidebar.evaluate(node=>{
     window.pairedSidebarScrolls=[];
     node.addEventListener('scroll',event=>pairedSidebarScrolls.push({trusted:event.isTrusted,top:node.scrollTop}),{passive:true});
    });
    await page.mouse.move(box.left+box.width/2,box.top+box.height/2);await page.mouse.wheel(0,600);
    await expect.poll(async()=>(await scroll(sidebar)).top).toBeGreaterThan(before.top+100);
    const after=await scroll(sidebar),afterRows=await visibleRows();
    assert.ok(afterRows.some(id=>!beforeRows.includes(id))&&afterRows.every(id=>ids.has(id)));
    assert.ok(Math.abs((await scroll(pane)).top-transcriptBefore.top)<2,'Sidebar wheel does not scroll the transcript');
    const events=await page.evaluate(()=>pairedSidebarScrolls);
    assert.ok(events.some(row=>row.trusted&&row.top>before.top),'Real wheel-driven scroll events required');
    const footerAfter=await usable(footer);
    assert.ok(Math.abs(footerAfter.top-footerBefore.top)<2);
    assert.ok((await sidebar.evaluate(rect)).bottom<=footerAfter.top+1);
    cell.sidebar={before,after,beforeRows,afterRows,events,footerBefore,footerAfter,
     footerButton:await usable(footer.getByRole('button',{name:'App options',exact:true}),true)};
    await retained();await capture('desktop-sidebar');
   }else await expect(page.locator('#workspace-navigation')).toBeHidden();

   // Open the observed host-projected group. The real host uses deferred work
   // pages and segment IDs, not the inline nodes of a synthetic HTTP fixture.
   const summary=page.locator(`[data-part="execution"][data-turn-id="${seed.turnId}"] > button.a-execution-turn-line`);
   await expect(summary).toHaveCount(1);
   if(await summary.getAttribute('aria-expanded')==='false')await summary.click();
   await expect(summary).toHaveAttribute('aria-expanded','true');
   const complete=page.locator(`[data-node-id="${seed.completedNodeId}"] > .a-execution-action-line`);
   const running=page.locator(`[data-node-id="${seed.runningNodeId}"] > .a-execution-action-line`);
   await expect(complete.locator('.a-execution-complete[aria-label="Completed"]')).toHaveCount(1);
   await expect(running.locator('.a-execution-complete')).toHaveCount(0);
   await expect(running.locator('.a-execution-dot[aria-label="running"]')).toHaveCount(1);
   for(const [name,line] of [['completed',complete],['running',running]]){
    await line.scrollIntoViewIfNeeded();await usable(line,true);
    const geometry=await line.evaluate(node=>{
     const r=child=>{const b=child.getBoundingClientRect();return {left:b.left,right:b.right,top:b.top,bottom:b.bottom,width:b.width,height:b.height}};
     const label=node.querySelector('.a-execution-label'),accounting=node.querySelector('.a-execution-accounting'),status=node.lastElementChild;
     return {line:r(node),label:r(label),accounting:r(accounting),status:r(status),
      accountingText:accounting.textContent,statusLabel:status.getAttribute('aria-label'),
      opacity:getComputedStyle(status).opacity,scrollWidth:node.scrollWidth,clientWidth:node.clientWidth};
    });
    assert.ok(geometry.status.width>0&&geometry.status.height>0&&geometry.opacity!=='0');
    assert.ok(geometry.accountingText.trim()&&geometry.accounting.width>0);
    assert.ok(geometry.status.left>=geometry.label.right-1&&geometry.status.right<=geometry.line.right+1);
    assert.ok(geometry.line.right-geometry.status.right<32,'Status is at the trailing edge');
    assert.ok(geometry.accounting.right<=geometry.line.right+1&&geometry.accounting.bottom<=geometry.line.bottom+1);
    if(width===390)assert.ok(geometry.accounting.top>=geometry.label.bottom-1);
    else assert.ok(geometry.accounting.left>=geometry.label.right-1&&geometry.accounting.right<=geometry.status.left);
    assert.ok(geometry.scrollWidth<=geometry.clientWidth+1);
    cell.execution.push({name,...geometry});
   }
   await capture(width+'-execution');
   const strip=page.locator('.a-composer .a-attachments'),chip=strip.locator(`[data-attachment-id="${seed.attachment.id}"]`);
   const link=chip.locator('a'),remove=chip.getByRole('button',{name:'Remove '+seed.attachment.name,exact:true});
   await expect(strip).toHaveAttribute('data-private','true');
   await expect(link).toHaveAttribute('title',seed.attachment.name);await expect(link).toContainText(seed.attachment.preview);
   const form=await page.locator('.a-composer').evaluate(rect),stripBox=await strip.evaluate(rect),
    chipBox=await chip.evaluate(rect),labelBox=await link.evaluate(rect),removeBox=await usable(remove,true);
   const clip=await link.evaluate(node=>({width:node.clientWidth,scroll:node.scrollWidth,
    overflow:getComputedStyle(node).overflowX,ellipsis:getComputedStyle(node).textOverflow}));
   assert.ok(clip.width>20&&clip.scroll>clip.width,'Real projected attachment text needs clipping');
   assert.equal(clip.overflow,'hidden');assert.equal(clip.ellipsis,'ellipsis');
   assert.ok(stripBox.left>=form.left&&stripBox.right<=form.right+1&&chipBox.right<=stripBox.right+1);
   assert.ok(labelBox.right<=removeBox.left-4&&removeBox.right<=chipBox.right+1);
   cell.attachment={form,strip:stripBox,chip:chipBox,label:labelBox,remove:removeBox,clip};
   await usable(composer);cell.primary=await usable(page.locator('[data-part="composer-primary-action"]'),true);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));

   const paneBox=await usable(pane),extent=await scroll(pane);
   assert.ok(extent.height-extent.client>800,'Long real host transcript is required');
   await page.mouse.move(paneBox.left+paneBox.width/2,paneBox.top+paneBox.height/2);
   await page.mouse.wheel(0,extent.height);
   await expect.poll(async()=>Math.abs((await scroll(pane)).remaining)).toBeLessThan(2);
   const bottom=await scroll(pane);await page.mouse.wheel(0,-450);
   await expect.poll(async()=>(await scroll(pane)).remaining).toBeGreaterThan(200);
   const reading=await scroll(pane),latest=page.getByRole('button',{name:'Jump to latest messages',exact:true}),
    stop=page.getByRole('button',{name:'Stop response',exact:true});
   cell.controls={progress:await usable(page.locator('.a-live-activity')),
    stop:await usable(stop,true),latest:await usable(latest,true)};
   await expect(page.locator('.a-live-activity strong')).toHaveText('Inspecting paired synthetic layout');
   await retained();await capture(width+'-progress-stop-latest');
   await latest.click();
   await expect.poll(async()=>Math.abs((await scroll(pane)).remaining)).toBeLessThan(2);
   const jumped=await scroll(pane);assert.ok(jumped.top>reading.top+200,'Latest must change real scrollTop');
   await expect(latest).toHaveCount(0);cell.transcript={bottom,reading,jumped};
   await composer.focus();await composer.press('ArrowLeft');await composer.press('ArrowRight');
   await expect(composer).toBeFocused();await retained();await capture(width+'-draft-attachment');
  }
  proof.passed=true;proof.syntheticSessionIds=seed.pinnedSessionIds;
  proof.historyAndRuntimeCountersPreserved=true;proof.attachmentContentNotFetched=true;
  proof.limits=['Synthetic work state is not native execution', 'Stop and Remove are enabled/hit-tested, not invoked'];
  return proof;
 }finally{page.off('request',observeAttachment);await page.setViewportSize(viewport)}
}

const {base,stop}=await startFixture({command:process.env.AMPLIFIER_TEST_PYTHON,
 args:[...(installedPackage?['-I','-B']:[]),fileURLToPath(new URL('../../tests/fixtures/client_paired_server.py',import.meta.url)),
  '--server-root',serverRoot,'--assembly-record',recordPath,'--state-parent',stateParent],
 env:hostEnv,tag:'client-paired-server',name:'Paired web server'});
let browser,failure,page,result;
const screenshots=[];
const screenshot=async name=>{const path=join(artifact,name+'.png');await page.screenshot({path});screenshots.push(path)};
try{
 const unauthenticated=await fetch(base+'/api/state');assert.equal(unauthenticated.status,401);
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page=await context.newPage();
 const errors=[],sends=[],actions=[],privateRequests=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{
  if((request.url()+(request.postData()||'')).includes('paired-private-retained-'))privateRequests.push('retained-payload-request');
  if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions'){
   const envelope=request.postDataJSON(),body=envelope.action==='shell.command'?(envelope.args.command||envelope.args):envelope;
   actions.push(body);if(body.action==='conversation.send')sends.push(body);
  }
 });
 await page.route('**/api/clients/attach',async route=>{
  const response=await route.fetch(),attachment=await response.json();
  await route.fulfill({response,json:{...attachment,transports:['sse']}});
 });
 await page.addInitScript(()=>{
  const Native=window.EventSource;window.streamProbe=[];
  window.EventSource=class extends Native{
   constructor(...args){super(...args);this.frames={state:0,'state-delta':0};this.opens=0;streamProbe.push(this);
    this.addEventListener('open',()=>this.opens++);
    for(const name of Object.keys(this.frames))this.addEventListener(name,()=>this.frames[name]++);
   }
  };
 });
 const identity=await context.request.get(base+'/fixture/assembly').then(response=>response.json());
 assert.equal(identity.serverRevision,record.server.revision);
 assert.equal(identity.serverVersion,record.server.version);
 if(installedPackage){
  assert.equal(identity.installedWheel,true);assert.equal(identity.nodeUnavailable,true);
  assert.equal(identity.installedPackage,resolve(installedPackage));
  assert.equal(identity.staticFilesVerified,Object.keys(manifest.assets).length+1);
  const component=await context.request.get(base+'/client-component.json');
  assert.equal(component.status(),200);
  const bytes=await component.body();
  assert.equal(bytes.length,record.client.component.bytes);
  assert.equal(hash(bytes),record.client.component.sha256);
  assert.deepEqual(JSON.parse(bytes.toString('utf8')),manifest,'Serve the exact installed component receipt');
 }else assert.equal(identity.serverRevision,manifest.pairedServer.revision);
 assert.deepEqual(identity.clientManifestPeer,manifest.pairedServer,'Unqualified shared-input peer is not the actual server identity');
 assert.deepEqual(identity.clientSource,manifest.source);
 for(const name of ['index.html','build.json']){
  const response=await context.request.get(base+(name==='index.html'?'/':'/'+name));assert.equal(response.status(),200);
  assert.equal(hash(await response.body()),manifest.assets[name].sha256,name+' is served from the installed artifact');
 }
 const badToken=await context.request.get(base+'/api/state',{headers:{Authorization:'Bearer deliberately-invalid-fixture-token'}});assert.equal(badToken.status(),401);
 await page.goto(base);await showFixtureConversation(page);
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeVisible();
 const build=await page.evaluate(()=>window.amplifier.getState().client.build);
 assert.deepEqual(build,{serverVersion:identity.serverVersion,frontend:manifest.build});
 const history=async()=>{const response=await context.request.get(base+'/fixture/message-source');assert.equal(response.status(),200);return response.json()};
 const assistantRendered=async count=>{
  await expect(page.getByRole('heading',{name:'Ready',exact:true})).toHaveCount(count);
  const replies=page.getByText('I received your message and attachments.',{exact:true});
  await expect(replies).toHaveCount(count);await expect(replies.last()).toBeVisible();
 };
 const send=async(text,count)=>{
  await composer.fill(text);await page.locator('[data-part="composer-primary-action"]').click();
  await expect.poll(async()=> (await history()).sends.length).toBe(count);
  await expect.poll(async()=> (await history()).sessions.flatMap(row=>row.messages||[]).filter(row=>row.role==='assistant').length).toBe(count);
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().sessions.find(row=>row.id===window.amplifier.getState().selectedSessionId).status)).toBe('idle');
  await expect(page.getByText(text,{exact:true})).toBeVisible();
  await assistantRendered(count);
 };
 await send('History through the paired server',1);
 await page.reload();await showFixtureConversation(page);
 await expect(page.getByText('History through the paired server',{exact:true})).toBeVisible();
 await assistantRendered(1);
 await send('A second accepted message',2);
 await composer.fill('Unfinished paired-server draft');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.draft)).toBe('Unfinished paired-server draft');
 const before=await history(),transport=await page.evaluate(()=>({
  opens:streamProbe.reduce((n,source)=>n+source.opens,0),baselines:streamProbe.reduce((n,source)=>n+source.frames.state,0)}));
 const clientId=await page.evaluate(()=>window.amplifier.getState().client.id);
 const disconnected=await context.request.post(base+'/fixture/disconnect',{data:{clientId}});
 assert.equal(disconnected.status(),200);assert.equal((await disconnected.json()).disconnected,true);
 // Native EventSource retry reuses its JS object. Observe the actual reopened
 // connection and fresh baseline, not allocation of another wrapper object.
 await expect.poll(()=>page.evaluate(()=>streamProbe.reduce((n,source)=>n+source.opens,0)),{timeout:15000}).toBeGreaterThan(transport.opens);
 await expect.poll(()=>page.evaluate(()=>streamProbe.reduce((n,source)=>n+source.frames.state,0)),{timeout:15000}).toBeGreaterThan(transport.baselines);
 await expect(composer).toHaveText('Unfinished paired-server draft');
 const deltas=await page.evaluate(()=>streamProbe.at(-1).frames['state-delta']);
 const response=await context.request.post(base+'/api/actions',{headers:{'X-Amplifier-Client':clientId},data:{action:'view.update',args:{patch:{navWidth:417}}}});
 assert.equal(response.status(),200);assert.equal((await response.json()).accepted,true);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.navWidth)).toBe(417);
 await expect.poll(()=>page.evaluate(()=>streamProbe.at(-1).frames['state-delta'])).toBeGreaterThan(deltas);
 await page.reload();await showFixtureConversation(page);await expect(composer).toHaveText('Unfinished paired-server draft');
 await assistantRendered(2);
 assert.notEqual(await page.evaluate(()=>window.amplifier.getState().client.id),clientId,'Reload creates a new writable client identity');
 assert.deepEqual(await history(),before,'Reconnect/reload preserve canonical history and do not execute work again');
 assert.equal(sends.length,2);assert.equal(new Set(sends.map(row=>row.id)).size,2);
 const quietRetainedCopies=await qualifyQuietRetainedCopies({page,composer,history,actions,privateRequests,screenshot,base});
 assert.equal(sends.length,2,'Retained copies did not add a send');
 const appearance=await qualifyAppearance({page,composer,history,actions,screenshot,base});
 assert.equal(sends.length,2,'Appearance checks did not add a send');
 assert.deepEqual(privateRequests,[],'Appearance checks did not expose retained copies');
 assert.deepEqual(errors,[]);
 result={passed:true,qualification,identity,build,
  authRejectsMissingAndInvalidCredential:true,sseSelectedByFixture:true,realSSEDeltaObserved:true,
  sends:2,distinctAcceptedCommandIds:2,historyDigest:hash(JSON.stringify(before.sessions)),historyPreserved:true,
  assistantRenderedAfterSendAndReload:true,draftSurvivedReconnectAndReload:true,quietRetainedCopies,appearance,screenshots,
  limits:['Synthetic runtime/providers, not native execution or model calls',
   installedPackage?'Node-free Python host; Node remains the browser test driver. Server revision is externally declared and bound to installed Python hashes; dependencies are not requalified.':
    'Source server composition, not an unmodified published wheel',
   'The component manifest peer is unqualified shared-input provenance, not the actual server revision',
   'No hardware, live adoption, cross-version or AHP qualification']};
}catch(error){
 failure=error;
 if(page)try{await screenshot('failure')}catch{}
 result={passed:false,qualification,screenshots,failure:String(error.stack||error)};
 throw error;
}finally{
 try{
  await finishFixture({label:'Paired web server',close:[['Paired web browser',()=>browser?.close()]],stop,originalError:failure});
  if(result?.passed)result.bytesVerifiedAfterShutdown=true;
 }
 catch(error){
  failure??=error;
  result={...result,passed:false,cleanupFailed:true};
  throw error;
 }finally{
  if(result){
   try{await writeFile(join(artifact,'browser-result.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result))}
   catch(error){if(failure)console.error('Paired browser evidence write failed:',error);else throw error}
  }
 }
}
