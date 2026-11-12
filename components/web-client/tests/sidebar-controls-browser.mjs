// Real client rendering against synthetic HTTP/SSE state; no provider or history writes.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';
import './composer-test-helpers.mjs';

const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS||'output/sidebar-controls';
const baseline=process.env.AMPLIFIER_SIDEBAR_BASELINE==='1';
const results=[],errors=[],calls=[],regressions=[],menuRegressions=[],explicitStops=[],attachmentRequests=[],cleanupErrors=[];
let browser,vite,page,failure,activeMenuProof,menuPhase;

async function visibleGeometry(locator,{tolerance=1,header=false,record=()=>{}}={}){
 await expect(locator).toBeVisible();
 const geometry=await locator.evaluate((node,includeHeader)=>{
  const rect=node=>{const r=node.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}};
  const box=rect(node),hit=document.elementFromPoint(box.left+box.width/2,box.top+box.height/2);
  const header=includeHeader?node.closest('.a-work-header'):null;
  return {...box,viewportWidth:innerWidth,viewportHeight:innerHeight,opacity:getComputedStyle(node).opacity,
   hit:node.contains(hit),hitTarget:hit?{tag:hit.tagName,className:hit.getAttribute('class')}:null,
   // Record wrapped-header intersections for review; do not require a notice
   // anchored inside the header to sit outside the entire header rectangle.
   ...(includeHeader?{header:header?rect(header):null,headerContentOverlaps:header?
    [...header.querySelectorAll('button,[data-part="session-heading"]')].filter(target=>{
     const r=rect(target);return r.width>0&&r.height>0&&r.left<box.right&&r.right>box.left&&r.top<box.bottom&&r.bottom>box.top;
    }).map(target=>({label:target.getAttribute('aria-label')||target.textContent.trim(),rect:rect(target)})):[]}:{}),
  };
 },header);
 record(geometry);
 assert.ok(geometry.width>0&&geometry.height>0&&geometry.left>=-tolerance&&geometry.right<=geometry.viewportWidth+tolerance
  &&geometry.top>=-tolerance&&geometry.bottom<=geometry.viewportHeight+tolerance&&geometry.hit&&geometry.opacity!=='0',
 'Visible, in-viewport and unobscured: '+JSON.stringify(geometry));
 return geometry;
}

async function menuRegression({page,scenario,readState,emit}){
 const proof={scenario:scenario.name,passed:false,clipboard:'real browser grant; synthetic records',actionTrace:[],notices:[]};
 menuRegressions.push(proof);
 activeMenuProof=proof;menuPhase='app-disclosure';
 const start=calls.length,original={...readState().sessions[0]},originalView={...readState().view};
 const footer=page.locator('.a-work-footer'),app=footer.getByRole('button',{name:'App options',exact:true});
 const appItems=footer.getByRole('group',{name:'App options',exact:true});
 const labels=locator=>locator.evaluateAll(nodes=>nodes.map(node=>{
  const copy=node.cloneNode(true);copy.querySelectorAll('svg,.a-attention-badge').forEach(node=>node.remove());return copy.textContent.trim();
 }));
 const capture=label=>page.screenshot({path:`${artifacts}/${scenario.name}-${label}.png`});
 await expect(app.locator('.a-attention-badge')).toHaveText('3');
 await app.focus();await app.press('Enter');
 await expect(appItems.getByRole('button').first()).toBeFocused();
 assert.deepEqual(await labels(appItems.getByRole('button')),['Activity','Send feedback','Automations','What agents see','Settings']);
 await expect(appItems.locator('hr,[role="separator"]')).toHaveCount(0);
 await expect(appItems.getByRole('button',{name:/^Activity/}).locator('.a-attention-badge')).toHaveText('2');
 await expect(appItems.getByRole('button',{name:/^Settings/}).locator('.a-attention-badge')).toHaveText('3');
 for(const button of await appItems.getByRole('button').all()){
  const box=await button.boundingBox();assert.ok(box&&box.x>=0&&box.x+box.width<=scenario.width);
 }
 await capture('app-options');
 menuPhase='app-menu-escape';
 await page.keyboard.press('Escape');await expect(app).toBeFocused();await expect(appItems).toHaveCount(0);
 await expect(app.locator('.a-attention-badge')).toHaveText('3');
 // The mobile drawer owns focus/inertness until explicitly closed.
 if(scenario.touch){
  menuPhase='mobile-drawer-escape';
  await page.getByRole('slider',{name:'Filter conversations by age'}).focus();await page.keyboard.press('Escape');
  await expect(page.locator('#workspace-navigation')).toBeHidden();
  await expect.poll(()=>readState().view.navExpanded).toBe(false);
 }
 const copyStart=calls.length;
 menuPhase='chat-disclosure';
 const trigger=page.getByRole('button',{name:'Chat',exact:true});
 const items=page.getByRole('group',{name:'Chat',exact:true});
 const copy=()=>items.getByRole('button',{name:'Copy session ID',exact:true});
 const notice=page.locator('.a-session-copy-notice');
 const visibleNotice=async checkpoint=>{
  const geometry=await visibleGeometry(notice,{tolerance:0,header:true,
   record:geometry=>proof.notices.push({checkpoint,...geometry})});
  assert.deepEqual(geometry.headerContentOverlaps,[],'Copy feedback does not cover header controls or the chat title');
  assert.ok(geometry.top>=geometry.header.bottom,'Copy feedback sits below the complete wrapped header');
  return geometry;
 };
 await expect(trigger.locator('.lucide-info')).toHaveCount(1);
 await trigger.focus();await trigger.press('Enter');await expect(copy()).toBeFocused();
 assert.deepEqual(await labels(items.getByRole('button')),['Copy session ID','Rename','Pin','Share or save','Chat info','Help & diagnostics','Advanced','Archive']);
 await capture('chat-options');
 menuPhase='chat-menu-escape';
 await page.keyboard.press('Escape');await expect(trigger).toBeFocused();
 menuPhase='clipboard-granted';
 await trigger.press('Enter');await expect(copy()).toBeFocused();
 await page.context().grantPermissions(['clipboard-read','clipboard-write'],{origin:new URL(page.url()).origin});
 await copy().press('Enter');
 await expect(items).toHaveCount(0);await expect(trigger).toBeFocused();
 await expect(notice).toHaveText('Session ID copied.');await expect(notice).toBeVisible();
 assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),original.nativeIdentity);
 await expect(notice).toHaveAttribute('role','status');await expect(notice).toHaveAttribute('aria-live','polite');
 await expect(notice).toHaveAttribute('aria-atomic','true');
 await visibleNotice('copied');
 await capture('session-id-copied');
 if(['desktop-320','mobile-390'].includes(scenario.name)){
  // Deterministic pending/errors supplement the real granted write above.
  // The stub is client-local and records exact bytes; it is not host acceptance.
  await page.evaluate(()=>{
   window.fixtureClipboard={writes:[],pending:[],mode:'pending'};
   Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:text=>{
    const fixture=window.fixtureClipboard;fixture.writes.push(text);
    if(fixture.mode==='denied')return Promise.reject(new DOMException('Fixture denial','NotAllowedError'));
    if(fixture.mode==='failed')return Promise.reject(Error('Fixture failure'));
    return new Promise((resolve,reject)=>fixture.pending.push({resolve,reject}));
   }}});
  });
  menuPhase='clipboard-pending';
  await trigger.click();await copy().press('Space');
  await expect(items).toHaveCount(0);await expect(notice).toHaveText('Copying session ID…');
  await visibleNotice('pending');
  await capture('session-id-pending');
  await trigger.click();await expect(copy()).toBeDisabled();await page.keyboard.press('Escape');
  menuPhase='clipboard-stale-rejection';
  readState().sessions[0]={...original,runtimeSessionId:'runtime-'+scenario.name,location:{kind:'managed'}};
  await emit();await expect(notice).toBeEmpty();
  await page.evaluate(()=>window.fixtureClipboard.pending[0].reject(new DOMException('Old denial','NotAllowedError')));
  await expect(notice).toBeEmpty();
  menuPhase='clipboard-runtime-id';
  await trigger.click();
  assert.deepEqual(await labels(items.getByRole('button')),['Copy session ID','Rename','Pin','Share or save','Chat info','Help & diagnostics','Advanced','Archive','Delete']);
  await copy().press('Enter');await expect(notice).toHaveText('Copying session ID…');
  await page.evaluate(()=>window.fixtureClipboard.pending[1].resolve());
  await expect(notice).toHaveText('Session ID copied.');
  await visibleNotice('runtime-id-copied');
  assert.deepEqual(await page.evaluate(()=>window.fixtureClipboard.writes),[original.nativeIdentity,'runtime-'+scenario.name]);
  for(const [mode,message] of [
   ['denied','Clipboard access was denied. Session ID was not copied.'],
   ['failed','Session ID could not be copied.'],
   ['unavailable','Clipboard is unavailable in this browser. Session ID was not copied.'],
  ]){
   menuPhase='clipboard-'+mode;
   await page.evaluate(mode=>{window.fixtureClipboard.mode=mode;if(mode==='unavailable')Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined})},mode);
   await trigger.click();await copy().press('Enter');
   await expect(items).toHaveCount(0);await expect(notice).toHaveText(message);await expect(notice).toBeVisible();
   await visibleNotice(mode);
   if(mode==='denied')await capture('session-id-denied');
  }
  menuPhase='clipboard-owner-reset';
  await page.evaluate(()=>delete navigator.clipboard);
  readState().sessions[0]=original;await emit();await expect(notice).toBeEmpty();
  readState().selectedSessionId=null;await emit();await expect(trigger).toHaveCount(0);
  readState().selectedSessionId=original.id;await emit();await expect(trigger).toHaveCount(1);
  proof.syntheticClipboard=['pending','stale rejection','managed runtime ID','denied','failed','unavailable','new draft'];
 }
 assert.equal(readState().selectedSessionId,original.id);
 assert.deepEqual(readState().sessions[0],original);
 assert.deepEqual(readState().view.draft,originalView.draft);
 const actionEvidence=JSON.stringify({scenario:scenario.name,actionTrace:proof.actionTrace});
 // The diagnostic run observed shell/runtime.jsx's debounced render report
 // settling during disclosure. It is not an interactive menu action. Keep it
 // visible and validate its exact fixture identity rather than timing it away.
 const clientId=await page.evaluate(()=>window.amplifier.getState().client.id);
 for(const call of calls.slice(start).filter(call=>call.action==='shell.report')){
  assert.equal(call.args.clientId,clientId);
  assert.equal(call.args.revision,0);
  assert.equal(call.args.previewId,null);
  assert.equal(call.args.message,'');
  assert.deepEqual(call.args.instances,{
   workspaces:'ready',chats:'ready','core.app.actions':'ready','core.app.status':'ready',
   'core.conversation.header':'ready','core.composer.actions':'ready',
   'core.canvas.toolbar':'inactive','core.settings.appearance':'inactive',
  });
 }
 const interactive=rows=>rows.filter(call=>call.action!=='shell.report').map(({action,args})=>({action,args}));
 assert.deepEqual(interactive(calls.slice(copyStart)),[],
  'Chat menu disclosure/copy never dispatches an interactive action or changes selection/model/draft: '+actionEvidence);
 assert.deepEqual(interactive(calls.slice(start)),scenario.touch?[{action:'view.update',args:{patch:{navExpanded:false}}}]:[],
  'Only the explicit mobile drawer close may dispatch a view update: '+actionEvidence);
 if(scenario.touch){
  menuPhase='mobile-drawer-reopen';
  await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 }
 activeMenuProof=null;menuPhase=null;
 proof.passed=true;
}

// Two existing viewport cells exercise the real shell/skin, not a CSS-only
// specimen. All records are synthetic, inline and bounded; no saved detail,
// attachment, provider or history IO is needed to render them.
async function appearanceRegression({page,scenario,readState,emit,stopGuard}){
 const proof={scenario:scenario.name,passed:false,checkpoints:[]};
 regressions.push(proof);
 const mobile=scenario.name==='mobile-390',now=Math.floor(Date.now()/1000);
 const draft='Keep this unsent draft while inspecting the layout.';
 const file={id:'appearance-file',name:'long-retained-reference-'.repeat(12)+'.txt',
  source:'clipboard-text',preview:'A long retained clipboard excerpt with important original text. '.repeat(12),
  mime:'text/plain',size:4096,url:'/api/attachments/'+'a'.repeat(32)};
 const state=readState(),session=state.sessions[0];
 const input={id:'appearance-input',inputId:'appearance-turn',role:'user',
  text:'Inspect the synthetic work without submitting another message.',createdAt:now-25,delivery:{status:'accepted'}};
 session.messages=[
  ...Array.from({length:24},(_,i)=>({id:'appearance-history-'+i,role:i%2?'assistant':'user',
   text:`Saved synthetic paragraph ${i}.\n\n`+'Retained conversation text for independent scrolling. '.repeat(8),
   createdAt:now-500+i})),
  input,{id:'appearance-latest',role:'assistant',text:'Latest synthetic reply remains readable.',createdAt:now-1},
 ];
 session.status='working';session.workers=[];
 session.activity={phase:'tools',label:'Inspecting synthetic layout',startedAt:now-25,activeTools:[]};
 session.draftAttachments=[file];
 session.execution={currentTurnId:'appearance-turn',detailsDeferred:false,
  turns:[{id:'appearance-turn',inputId:input.inputId,phase:'running',startedAt:now-25}],
  nodes:[
   {id:'appearance-completed',turnId:'appearance-turn',sessionId:'chat',kind:'tool',label:'bash',
    phase:'completed',startedAt:now-24,endedAt:now-21,
    input:JSON.stringify({command:'printf "retained synthetic result"'}),
    output:JSON.stringify({stdout:'Retained synthetic tool result.',stderr:'',returncode:0})},
   {id:'appearance-running',turnId:'appearance-turn',sessionId:'chat',kind:'llm',label:'Synthetic model step',
    phase:'running',startedAt:now-20,provider:'fixture',model:'fixture-model',
    usage:{inputTokens:1234,outputTokens:56,totalTokens:1290,calls:1}},
  ]};
 const chats=Array.from({length:48},(_,i)=>({id:'appearance-chat-'+i,title:`Long sidebar synthetic chat ${i+1}`,
  sessionKind:'root',workspace:'/fixture',workspaceId:'project',status:'idle',recentActivityAt:now-i}));
 state.pinnedNavigation={items:chats.map(row=>({id:'chat:'+row.id,kind:'chat',row})),total:chats.length};
 state.view={...state.view,workSurface:'chat',navPinned:!mobile,navExpanded:!mobile,navAge:'all',
  navMode:'browse',navSectionsCollapsed:[],executionExpanded:[]};
 const messages=JSON.stringify(session.messages),attachments=JSON.stringify(session.draftAttachments);
 const start=calls.length;
 await emit();
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 const pane=page.getByRole('log',{name:'Conversation messages'});
 const selected=()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await expect.poll(selected).toBe('chat');
 await expect(pane).toHaveAttribute('aria-busy','false');
 await expect(composer).toBeVisible();await expect(composer).toBeEditable();
 await composer.fill(draft);await expect(composer).toHaveDraft(draft);
 await expect.poll(()=>readState().view.draft).toBe(draft);
 const complete=page.locator('[data-node-id="appearance-completed"] > .a-execution-action-line');
 const running=page.locator('[data-node-id="appearance-running"] > .a-execution-action-line');
 // splitWork groups these nodes after their input anchor. The disclosure owns
 // the rendered segment ID, not the original runtime turn ID. Open that actual
 // control and confirm its saved view patch before measuring the child rows.
 const group=pane.locator('[data-part="execution"][data-turn-id="appearance-turn"]');
 await expect(group).toHaveCount(1);
 await expect(group).toHaveAttribute('data-group-id','appearance-turn@appearance-input');
 const disclosure=group.locator(':scope > button.a-execution-turn-line');
 await expect(disclosure).toBeVisible();await expect(disclosure).toBeEnabled();
 await expect(disclosure).toHaveAttribute('aria-expanded','false');
 await expect(complete).toHaveCount(0);await expect(running).toHaveCount(0);
 await disclosure.click();
 await expect(disclosure).toHaveAttribute('aria-expanded','true');
 await expect.poll(()=>readState().view.executionExpanded).toEqual(['turn:appearance-turn@appearance-input']);
 await expect(complete).toBeVisible();await expect(running).toBeVisible();
 await expect(group.locator('[data-node-id]')).toHaveCount(2);
 proof.executionDisclosure={groupId:await group.getAttribute('data-group-id'),
  expanded:await disclosure.getAttribute('aria-expanded'),nodeCount:2};

 const scroll=locator=>locator.evaluate(node=>({
  top:node.scrollTop,height:node.scrollHeight,client:node.clientHeight,
  remaining:node.scrollHeight-node.clientHeight-node.scrollTop,
 }));
 const rect=node=>{const r=node.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}};
 async function usable(locator,{button=false}={}){
  if(button)await expect(locator).toBeEnabled();
  return visibleGeometry(locator);
 }
 async function capture(label){
  const prefix=`${artifacts}/${scenario.name}-${label}`;
  await writeFile(prefix+'.html',await page.locator('#amp-one').evaluate(node=>node.outerHTML));
  await page.screenshot({path:prefix+'.png'});
  proof.checkpoints.push(label);
 }
 async function retained(){
  await expect.poll(selected).toBe('chat');
  await expect(composer).toHaveDraft(draft);
  assert.equal(JSON.stringify(readState().sessions[0].messages),messages);
  assert.equal(JSON.stringify(readState().sessions[0].draftAttachments),attachments);
  assert.deepEqual(attachmentRequests,[],'No attachment is fetched merely to render a text chip');
  assert.deepEqual(calls.slice(start).filter(call=>['conversation.send','conversation.retry','message.edit',
   'attachment.add','attachment.remove','canvas.openAttachment','session.select','session.create',
   'session.delete','session.archive','session.history','worker.spawn','worker.stop','call.start','navigation.pinOrder'].includes(call.action)),[],
  'Layout, scrolling, and disclosure cannot send, select, delete or execute work');
 }

 if(!mobile){
  const sidebar=page.locator('#workspace-navigation .a-nav-content');
  const footer=page.locator('#workspace-navigation .a-work-footer');
  await expect(sidebar.locator('.a-pinned-chats [data-session-id]')).toHaveCount(48);
  const first=await scroll(sidebar);
  assert.ok(first.client>100&&first.height-first.client>600,'Long sidebar really overflows');
  const footerBefore=await usable(footer);
  await usable(footer.getByRole('button',{name:'App options',exact:true}),{button:true});
  assert.equal(await footer.evaluate(node=>!!node.closest('.a-nav-content')),false);
  const visibleRows=()=>sidebar.evaluate(node=>{
   const box=node.getBoundingClientRect();
   return [...node.querySelectorAll('.a-pinned-chats [data-session-id]')].filter(row=>{
    const r=row.getBoundingClientRect();return r.top>=box.top&&r.bottom<=box.bottom;
   }).map(row=>row.dataset.sessionId);
  });
  const beforeRows=await visibleRows(),transcriptBefore=await scroll(pane);
  await sidebar.evaluate(node=>{
   window.fixtureSidebarScrolls=[];
   node.addEventListener('scroll',event=>window.fixtureSidebarScrolls.push({trusted:event.isTrusted,top:node.scrollTop}),{passive:true});
  });
  const box=await usable(sidebar);
  await page.mouse.move(box.left+box.width/2,box.top+box.height/2);
  await page.mouse.wheel(0,600);
  await expect.poll(async()=>Math.abs((await scroll(sidebar)).top-Math.min(first.top+600,first.height-first.client))).toBeLessThan(2);
  const after=await scroll(sidebar),afterRows=await visibleRows();
  assert.ok(after.top>first.top+100&&afterRows.some(id=>!beforeRows.includes(id)),'Wheel reveals new lower rows');
  const events=await page.evaluate(()=>window.fixtureSidebarScrolls);
  assert.ok(events.some(event=>event.trusted&&event.top>first.top),'Native scroll event, not a fabricated handler call');
  assert.ok(Math.abs((await scroll(pane)).top-transcriptBefore.top)<2,'Sidebar wheel does not scroll transcript');
  readState().sessions[0].activity.label='Synthetic layout observation updated';await emit();
  await expect(page.locator('.a-live-activity strong')).toHaveText('Synthetic layout observation updated');
  assert.ok(Math.abs((await scroll(sidebar)).top-after.top)<2,'Unrelated SSE update retains sidebar scrollTop');
  const footerAfter=await usable(footer);
  assert.ok(Math.abs(footerAfter.top-footerBefore.top)<2&&Math.abs(footerAfter.bottom-footerBefore.bottom)<2);
  assert.ok((await sidebar.evaluate(rect)).bottom<=footerAfter.top+1,'Scrolling list cannot cover footer');
  await usable(footer.getByRole('button',{name:'App options',exact:true}),{button:true});
  proof.sidebar={before:first,after,beforeRows,afterRows,events,footerBefore,footerAfter};
  await retained();await capture('long-sidebar');
 }else await expect(page.locator('#workspace-navigation')).toBeHidden();

 // Use the canonical row status, not the presence of any decorative SVG.
 await expect(complete.locator('.a-execution-complete[aria-label="Completed"]')).toHaveCount(1);
 await expect(running.locator('.a-execution-complete')).toHaveCount(0);
 await expect(running.locator('.a-execution-dot[aria-label="running"]')).toHaveCount(1);
 proof.execution=[];
 for(const [name,line] of [['completed',complete],['running',running]]){
  await line.scrollIntoViewIfNeeded();await usable(line,{button:true});
  const geometry=await line.evaluate(node=>{
   const rect=child=>{const r=child.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}};
   const label=node.querySelector('.a-execution-label'),accounting=node.querySelector('.a-execution-accounting');
   const status=node.lastElementChild;
   return {line:rect(node),label:rect(label),accounting:rect(accounting),status:rect(status),
    statusLabel:status.getAttribute('aria-label'),statusOpacity:getComputedStyle(status).opacity,
    accountingText:accounting.textContent,scrollWidth:node.scrollWidth,clientWidth:node.clientWidth};
  });
  proof.execution.push({name,...geometry});
  assert.ok(geometry.status.width>0&&geometry.status.height>0&&geometry.statusOpacity!=='0');
  assert.ok(geometry.accountingText.trim()&&geometry.accounting.width>0);
  assert.ok(geometry.status.left>=geometry.label.right-1&&geometry.status.right<=geometry.line.right+1);
  assert.ok(geometry.line.right-geometry.status.right<32,'Status stays at the trailing edge');
  assert.ok(geometry.accounting.right<=geometry.line.right+1&&geometry.accounting.bottom<=geometry.line.bottom+1);
  if(mobile)assert.ok(geometry.accounting.top>=geometry.label.bottom-1,'Narrow accounting gets its own row');
  else assert.ok(geometry.accounting.left>=geometry.label.right-1&&geometry.accounting.right<=geometry.status.left,
   'Desktop accounting is right aligned without covering the label or status');
  assert.ok(geometry.scrollWidth<=geometry.clientWidth+1,'Execution header stays within its row');
 }
 await complete.click();
 await expect(complete).toHaveAttribute('aria-expanded','true');
 await expect(page.locator('[data-node-id="appearance-completed"] .a-execution-body')).toContainText('Retained synthetic tool result.');
 await complete.click();await expect(complete).toHaveAttribute('aria-expanded','false');
 await capture('execution-rows');

 const strip=page.locator('.a-composer .a-attachments');
 const attachment=strip.locator('[data-attachment-id="appearance-file"]');
 const link=attachment.locator('a'),remove=attachment.getByRole('button',{name:'Remove '+file.name,exact:true});
 await expect(strip).toHaveAttribute('data-private','true');
 await expect(link).toHaveAttribute('title',file.name);await expect(link).toContainText(file.preview);
 await usable(composer);
 const form=await page.locator('.a-composer').evaluate(rect),stripBox=await strip.evaluate(rect);
 const chip=await attachment.evaluate(rect),labelBox=await link.evaluate(rect),removeBox=await usable(remove,{button:true});
 const clip=await link.evaluate(node=>({width:node.clientWidth,scroll:node.scrollWidth,
  overflow:getComputedStyle(node).overflowX,ellipsis:getComputedStyle(node).textOverflow}));
 proof.attachment={form,strip:stripBox,chip,label:labelBox,remove:removeBox,clip};
 assert.ok(clip.width>20&&clip.scroll>clip.width,'The long text actually requires clipping');
 assert.equal(clip.overflow,'hidden');assert.equal(clip.ellipsis,'ellipsis');
 assert.ok(stripBox.left>=form.left&&stripBox.right<=form.right+1&&chip.right<=stripBox.right+1);
 assert.ok(labelBox.right<=removeBox.left-4&&removeBox.right<=chip.right+1,'Clipped text leaves Remove unobscured');
 proof.composer=await usable(page.locator('[data-part="composer-primary-action"]'),{button:true});
 await expect(page.locator('[data-part="composer-primary-action"]')).toHaveAttribute('data-action','conversation.send');
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No document horizontal overflow');
 await retained();

 // Real wheel input establishes scrollback; Latest must really move the pane.
 const paneBox=await usable(pane),extent=await scroll(pane);
 assert.ok(extent.height-extent.client>800,'Transcript is long enough to test Latest');
 await page.mouse.move(paneBox.left+paneBox.width/2,paneBox.top+paneBox.height/2);
 await page.mouse.wheel(0,extent.height);
 await expect.poll(async()=>Math.abs((await scroll(pane)).remaining)).toBeLessThan(2);
 const bottom=await scroll(pane);
 await page.mouse.wheel(0,-450);
 await expect.poll(async()=> (await scroll(pane)).remaining).toBeGreaterThan(200);
 const reading=await scroll(pane);
 assert.ok(reading.top<bottom.top-200);
 const latest=page.getByRole('button',{name:'Jump to latest messages',exact:true});
 const stop=page.getByRole('button',{name:'Stop response',exact:true});
 proof.controls={progress:await usable(page.locator('.a-live-activity')),
  stop:await usable(stop,{button:true}),latest:await usable(latest,{button:true})};
 await expect(page.locator('.a-live-activity strong')).not.toBeEmpty();
 readState().sessions[0].activity.label='Synthetic work remains active';await emit();
 await expect(page.locator('.a-live-activity strong')).toHaveText('Synthetic work remains active');
 await expect(composer).toHaveDraft(draft);
 await usable(stop,{button:true});await usable(latest,{button:true});
 await capture('progress-stop-latest');
 await latest.click();
 await expect.poll(async()=>Math.abs((await scroll(pane)).remaining)).toBeLessThan(2);
 const jumped=await scroll(pane);
 assert.ok(jumped.top>reading.top+200,'Latest changed the actual scrollTop');
 await expect(latest).toHaveCount(0);
 proof.transcript={bottom,reading,jumped};
 await retained();
 if(!mobile){
  assert.equal(explicitStops.length,0);
  stopGuard.armed=true;
  try{await stop.click();await expect.poll(()=>explicitStops.length).toBe(1)}
  finally{stopGuard.armed=false}
  assert.deepEqual(explicitStops[0].args,{sessionId:'chat'});
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().sessions.find(row=>row.id==='chat').status)).toBe('stopped');
  await expect(page.locator('.a-live-activity')).toHaveCount(0);await expect(stop).toHaveCount(0);
  await expect(running.locator('.a-execution-complete')).toHaveCount(0);
  await expect(running.locator('[aria-label="Stopped"]')).toHaveCount(1);
  proof.explicitStop={count:1,sessionId:'chat',syntheticOnly:true};
 }else proof.explicitStop={count:0};
 await composer.focus();await composer.press('ArrowLeft');await composer.press('ArrowRight');
 await expect(composer).toBeFocused();await retained();
 await capture('draft-and-attachment-retained');proof.passed=true;
}

await mkdir(artifacts,{recursive:true});
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false}});
 await vite.listen();
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 for(const scenario of [
  {name:'desktop-216',width:1280,navWidth:216},
  {name:'desktop-320',width:1280,navWidth:320},
  {name:'pinned-216',width:1280,navWidth:216,pinned:true},
  {name:'status-216',width:1280,navWidth:216,navMode:'status'},
  {name:'mobile-320',width:320,touch:true},
  {name:'mobile-390',width:390,touch:true},
  {name:'enlarged-text',width:1280,navWidth:216,enlarged:true},
  {name:'forced-colors',width:1280,navWidth:216,forced:true},
 ]){
  let state={revision:1,settings:{workspace:'/fixture',bundle:'anchors'},runtime:{available:true},
   view:{navPinned:true,navExpanded:true,navWidth:scenario.navWidth||216,navAge:'all',navMode:scenario.navMode||'browse'},
   sessions:[{id:'chat',nativeIdentity:'native-'+scenario.name,sessionKind:'root',title:'Saved synthetic chat',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[]}],
   workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'chat',selectedWorkspaceId:'project',
   setup:{providers:[{id:'fixture',module:'provider-fixture',enabled:true,config:{default_model:'fixture-model'}}],providersLoadedAt:1,providersWorkspace:'/fixture'},canvas:{open:false},attention:{badgeUnread:2,settingsUnread:3}};
  if(scenario.pinned)state.pinnedNavigation={items:[{id:'chat:chat',kind:'chat',row:state.sessions[0]}],total:1};
  const shell=createShellHttpFixture(()=>state),stopGuard={armed:false};
  page=await browser.newPage({viewport:{width:scenario.width,height:900},hasTouch:!!scenario.touch,isMobile:!!scenario.touch});
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(new URL(request.url()).pathname.startsWith('/api/attachments/'))attachmentRequests.push(request.url())});
  await page.addInitScript(installFixtureEvents);
  await page.route('**/api/**',async route=>{
   try{
   const request=route.request(),path=new URL(request.url()).pathname,response=shell.response(request);
   if(response!==undefined)return route.fulfill({json:response});
   if(path==='/api/state')return route.fulfill({json:shell.state()});
   if(path==='/api/actions'&&request.method()==='GET')return route.fulfill({json:[]});
   if(path==='/api/actions'){
    const envelope=request.postDataJSON(),body=envelope.action==='shell.command'?(envelope.args.command||envelope.args):envelope;calls.push(body);
    if(activeMenuProof)activeMenuProof.actionTrace.push({phase:menuPhase,callIndex:calls.length-1,call:body,envelope});
    if(body.action==='conversation.stop'){
     assert.ok(scenario.name==='desktop-320'&&stopGuard.armed&&explicitStops.length===0,'Only the explicit fixture Stop is allowed');
     assert.deepEqual(body.args,{sessionId:'chat'});stopGuard.armed=false;explicitStops.push(body);
     const session=state.sessions[0],endedAt=Date.now()/1000;
     session.status='stopped';session.execution.currentTurnId=null;
     session.execution.turns[0]={...session.execution.turns[0],phase:'stopped',endedAt};
     session.execution.nodes=session.execution.nodes.map(node=>node.id==='appearance-running'?{...node,phase:'stopped',endedAt}:node);
     state.revision++;
    }else if(/^(conversation\.|worker\.|call\.)/.test(body.action)||
     ['message.edit','session.delete','session.archive','attachment.add','attachment.remove','canvas.openAttachment','navigation.pinOrder'].includes(body.action)){
     errors.push('Unexpected effect: '+body.action);
     return route.fulfill({status:409,json:{accepted:false,error:'This rendering fixture refuses effects.'}});
    }
    if(body.action==='view.update')state={...state,revision:state.revision+1,view:{...state.view,...body.args.patch}};
    return route.fulfill({json:{accepted:true,state:shell.state(),result:{}}});
   }
   return route.fulfill({json:{ok:true}});
   }catch(error){errors.push('Fixture route: '+error.message);await route.abort().catch(()=>{})}
  });
  if(scenario.forced)await page.emulateMedia({forcedColors:'active'});
  await page.goto(vite.resolvedUrls.local[0]);
  await page.getByRole('slider',{name:'Filter conversations by age'}).waitFor();
  await page.locator('.a-sidebar-section').first().waitFor();
  if(scenario.enlarged)await page.addStyleTag({content:'#amp-one{--a-text-xs:24px;--a-text-sm:26px;--a-text-base:28px;font-size:28px}'});
  const slider=page.getByRole('slider',{name:'Filter conversations by age'});
  const metrics=await page.evaluate(()=>{
   const filter=document.querySelector('.a-chat-age-filter'),label=filter.querySelector('label'),range=filter.querySelector('input'),first=document.querySelector('.a-sidebar-section');
   const rect=node=>{const {x,y,width,height}=node.getBoundingClientRect();return {x,y,width,height}};
   const style=node=>{const s=getComputedStyle(node);return {fontSize:s.fontSize,lineHeight:s.lineHeight,color:s.color,marginTop:s.marginTop,marginBottom:s.marginBottom,height:s.height,padding:s.padding}};
   const buttons=[...document.querySelectorAll('.a-sidebar-modes button')].map(button=>({label:button.getAttribute('aria-label')||button.textContent,rect:rect(button),hit:button.contains(document.elementFromPoint(button.getBoundingClientRect().x+button.offsetWidth/2,button.getBoundingClientRect().y+button.offsetHeight/2))}));
   return {filter:rect(filter),label:rect(label),range:rect(range),first:rect(first),labelStyle:style(label),rangeStyle:style(range),firstStyle:style(first),gap:first.getBoundingClientRect().top-filter.getBoundingClientRect().bottom,sidebar:rect(document.querySelector('#workspace-navigation')),buttons,sectionMargins:[...document.querySelectorAll('.a-sidebar-section')].map(node=>getComputedStyle(node).marginTop)};
  });
  results.push({scenario:scenario.name,baseline,...metrics});
  const emit=async()=>{const revision=++state.revision;await page.evaluate(value=>window.emitFixtureState(value),shell.state());await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().revision)).toBeGreaterThanOrEqual(revision)};
  await page.screenshot({path:`${artifacts}/${scenario.name}.png`});
  assert.equal(await slider.getAttribute('aria-valuetext'),'All activity dates');
  assert.equal(await page.locator('.a-chat-age-stops>span').count(),6);
  assert.equal(await page.locator('#workspace-navigation').getByRole('button',{name:'Home',exact:true}).count(),baseline?1:0);
  if(!baseline){
   assert.equal(await page.locator('.a-sidebar-new .lucide-message-square-plus').count(),1);
   assert.ok(metrics.gap<=16,JSON.stringify(metrics));
   assert.ok(metrics.range.height>= (scenario.touch?44:24));
   assert.ok(metrics.filter.x>=metrics.sidebar.x&&metrics.filter.x+metrics.filter.width<=metrics.sidebar.x+metrics.sidebar.width);
   assert.equal(await page.locator('.a-chat-age-filter').evaluate(node=>node.scrollWidth<=node.clientWidth),true);
   assert.ok(parseFloat(metrics.labelStyle.fontSize)<=(scenario.enlarged?24:12));
   assert.ok(metrics.sectionMargins.slice(1).every(margin=>margin==='16px'),'Later section rhythm is unchanged');
   assert.equal(metrics.buttons.length,3);
   for(const button of metrics.buttons){
    assert.ok(button.hit,JSON.stringify(button));
    assert.ok(button.rect.x>=metrics.sidebar.x&&button.rect.x+button.rect.width<=metrics.sidebar.x+metrics.sidebar.width);
   }
  }
  if(!baseline)await menuRegression({page,scenario,readState:()=>state,emit});
  // Native keyboard semantics and meaningful values remain six-stop choices.
  await slider.focus();
  await slider.press('Home');
  await page.waitForFunction(()=>window.amplifier.getState().view.navAge==='24h');
  for(const age of ['7d','14d','30d','90d','all']){
   await slider.press('ArrowRight');
   await page.waitForFunction(age=>window.amplifier.getState().view.navAge===age,age);
  }
  assert.equal(await slider.evaluate(node=>document.activeElement===node),true,'Filter edits retain focus');
  await slider.press('Home');
  const box=await slider.boundingBox();
  if(scenario.touch)await page.touchscreen.tap(box.x+box.width-2,box.y+box.height/2);
  else await slider.click({position:{x:box.width-2,y:box.height/2}});
  await page.waitForFunction(()=>window.amplifier.getState().view.navAge==='all');
  // An API-saved year is shown truthfully until an explicit six-stop choice.
  state={...state,revision:state.revision+1,view:{...state.view,navAge:'1y'}};
  await page.evaluate(state=>window.emitFixtureState(state),shell.state());
  await page.waitForFunction(()=>document.querySelector('.a-chat-age-filter input').value==='9');
  assert.equal(await slider.getAttribute('aria-valuetext'),'Within the last year');
  assert.equal(await page.locator('.a-chat-age-filter strong').innerText(),'1y');
  await slider.press('End');
  await page.waitForFunction(()=>window.amplifier.getState().view.navAge==='all');
  if(scenario.touch){
   await slider.press('Escape');
   await page.waitForFunction(()=>!document.querySelector('#workspace-navigation').getClientRects().length);
   assert.equal(await page.getByRole('button',{name:'Home',exact:true}).isVisible(),true);
   assert.equal(await page.getByRole('button',{name:'Home',exact:true}).evaluate(node=>!!node.closest('[inert]')),false);
   await page.getByRole('button',{name:'Home',exact:true}).click();
   await page.waitForFunction(()=>window.amplifier.getState().view.workSurface==='home');
  }
  assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),'chat');
  const beforeDraft=calls.filter(call=>call.action==='session.draft').length;
  // Reopen the mobile drawer after Home, then use the same keyboard action.
  if(scenario.touch)await page.getByRole('button',{name:'Open navigation',exact:true}).click();
  const newChat=page.locator('.a-sidebar-new');
  const drafted=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='session.draft');
  await newChat.focus();await newChat.press('Enter');await drafted;
  assert.equal(calls.filter(call=>call.action==='session.draft').length,beforeDraft+1,'New chat dispatches exactly once');
  if(['desktop-320','mobile-390'].includes(scenario.name)){
   await appearanceRegression({page,scenario,readState:()=>state,emit,stopGuard});
  }
  await page.close();page=null;
 }
 assert.deepEqual(errors,[]);
 assert.ok(calls.every(call=>!['session.select','conversation.send','session.history','session.create'].includes(call.action)),'Passive controls never execute or reload history');
 assert.equal(explicitStops.length,1);assert.deepEqual(attachmentRequests,[]);
 assert.ok(regressions.length===2&&regressions.every(row=>row.passed));
 if(!baseline)assert.ok(menuRegressions.length===8&&menuRegressions.every(row=>row.passed));
 console.log(JSON.stringify({scope:'synthetic client browser',baseline,scenarios:results,regressions,menuRegressions,errors},null,2));
}catch(error){
 failure=error;
 if(page&&!page.isClosed()){
  try{
   await writeFile(`${artifacts}/failure.html`,await page.content());
   await page.screenshot({path:`${artifacts}/failure.png`});
  }catch(evidenceError){console.error('Failure capture failed:',evidenceError)}
 }
 throw error;
}finally{
 // Close both owned resources even if one cleanup or evidence write fails.
 for(const [name,close] of [['browser',()=>browser?.close()],['vite',()=>vite?.close()]]){
  try{await close()}catch(error){cleanupErrors.push({name,error:String(error)});}
 }
 try{await writeFile(`${artifacts}/metrics.json`,JSON.stringify({
  passed:!failure&&!errors.length&&!cleanupErrors.length,baseline,results,regressions,menuRegressions,errors,cleanupErrors,
  explicitStops:explicitStops.map(row=>({action:row.action,args:row.args})),attachmentRequests,
  scope:'Real client and stylesheet; synthetic HTTP/SSE only, no actual host/runtime or installed-package qualification.',
  ...(failure?{failure:String(failure.stack||failure),failureDetails:{
   name:failure.name,message:failure.message,stack:failure.stack,code:failure.code,
   operator:failure.operator,actual:failure.actual,expected:failure.expected,
  }}:{}),
 },null,2)+'\n')}catch(error){if(failure)console.error('Metrics write failed:',error);else throw error}
 if(cleanupErrors.length){if(failure)console.error('Fixture cleanup failed:',cleanupErrors);else throw Error('Fixture cleanup failed: '+JSON.stringify(cleanupErrors))}
}
