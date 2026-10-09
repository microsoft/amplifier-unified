// Real installed-package HTTP/SSE regression. One fixture and browser context.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/recent_visibility_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser,context,page;
const receipts=[];
const evidence=process.env.AMPLIFIER_RECENT_EVIDENCE;
try{
 const boot=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Recent fixture first-state timeout')),45000);
  fixture.once('error',error=>{clearTimeout(timer);reject(error)});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Recent fixture exited '+code))});
  let output='';
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}});
 });
 const url=boot.url;
 browser=await chromium.launch({headless:true});
 context=await browser.newContext({extraHTTPHeaders:{Authorization:'Bearer fixture-recent-visibility-token'},viewport:{width:1280,height:1000},hasTouch:true});
 page=await context.newPage();
 const other=await context.newPage(),errors=[];
 for(const target of [page,other]){
  target.on('pageerror',error=>errors.push(error.message));
  target.on('response',async response=>{
   if(new URL(response.url()).pathname==='/api/actions'){
    try{receipts.push({request:response.request().postDataJSON(),status:response.status(),receipt:await response.json()})}catch{}
   }
  });
 }
 const dispatch=(target,action,args)=>target.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const shell=target=>target.evaluate(()=>window.amplifier.getShellState());
 const sidebar=(target=page,instance='chats')=>target.locator(`[data-shell-instance="${instance}"] .a-quiet-sidebar [data-sidebar-section="recent"]`);
 const rows=(target=page,instance='chats')=>sidebar(target,instance).locator('.a-nav-chat[data-session-id]');
 const order=(target=page,instance='chats')=>rows(target,instance).evaluateAll(nodes=>nodes.map(node=>node.dataset.sessionId));
 const metrics=async()=>{const response=await page.request.get(url+'/fixture/metrics');assert.equal(response.status(),200);return response.json()};
 const expectDraft=async(target,text)=>{
  const composer=target.getByRole('textbox',{name:'Message Amplifier',exact:true});
  await expect.poll(()=>target.evaluate(()=>window.amplifier.getState().view.draft)).toBe(text);
  await expect.poll(()=>composer.textContent()).toBe(text);
  const identity=await target.evaluate(()=>window.amplifier.getState().client.id);
  const sid=await target.evaluate(()=>window.amplifier.getState().selectedSessionId);
  await expect.poll(async()=>(await metrics()).durableClients[identity]?.drafts?.[sid]).toBe(text);
 };
 const control=async data=>{const response=await page.request.post(url+'/fixture/control',{data});assert.equal(response.status(),200)};
 const projection=async(target=page,instance='chats')=>(await shell(target)).snapshots[instance].recentNavigation;
 const waitForRows=async(count,target=page,instance='chats')=>expect(rows(target,instance)).toHaveCount(count);
 await page.goto(url);
 await page.waitForFunction(()=>window.amplifier?.getShellState()?.snapshots?.chats?.recentNavigation);
 await waitForRows(20);
 const initial=await metrics(),firstOrder=await order();
 if(evidence)await writeFile(evidence+'.initial-metrics.json',JSON.stringify(initial,null,2)+'\n');
 assert.equal(initial.sessionCount,136);assert.equal(initial.sessionsUnchanged,true);
 assert.ok(!firstOrder.includes(initial.commissioned));assert.ok(!firstOrder.includes(initial.pinned));
 assert.ok(firstOrder.includes(initial.fork)&&firstOrder.includes(initial.legacy));
 assert.ok(initial.excluded.every(id=>!firstOrder.includes(id)));
 await expect(page.locator(`[data-shell-instance="chats"] [data-sidebar-section="pinned"] [data-session-id="${initial.pinned}"]`)).toHaveCount(1);
 const total=(await projection()).total;assert.equal(total,132);
 await sidebar().getByRole('button',{name:'Show agent-created',exact:true}).click();
 await expect(sidebar().getByRole('button',{name:'Show agent-created',exact:true})).toHaveAttribute('aria-pressed','true');
 await expect.poll(async()=>(await projection()).total).toBe(133);
 await waitForRows(20);
 await sidebar().getByRole('button',{name:'Show agent-created',exact:true}).click();
 await expect.poll(async()=>(await projection()).total).toBe(total);
 assert.deepEqual(await order(),firstOrder);
 // Passive browse setup: real current draft, attachment, Canvas and model/bundle.
 await page.getByRole('textbox',{name:'Message Amplifier',exact:true}).fill('Keep original draft');
 await expectDraft(page,'Keep original draft');
 await dispatch(page,'attachment.add',{sessionId:initial.selected,name:'retained.txt',base64:'a2VlcA=='});
 await dispatch(page,'canvas.show',{kind:'text',title:'Kept fixture Canvas',content:'Keep Canvas'});
 const canvasId=await page.evaluate(()=>window.amplifier.getState().canvas.id);
 const canonicalBefore=(await dispatch(page,'canvas.versions.inspect',{id:canvasId,version:1,includeSource:true})).result;
 await dispatch(page,'canvas.visibility',{open:false,sessionId:initial.selected,canvasId});
 const preservation=target=>target.evaluate(()=>{
  const state=window.amplifier.getState(),current=state.sessions.find(row=>row.id===state.selectedSessionId);
  return {selected:state.selectedSessionId,workspace:state.selectedWorkspaceId,draft:state.view.draft,
   attachments:current?.draftAttachments,canvas:state.canvas,bundle:current?.bundle,selection:current?.selection,
   messages:current?.messages,newSessionDraft:state.view.newSessionDraft};
 });
 const before=await preservation(page);
 assert.equal(before.draft,'Keep original draft');
 await other.goto(url);await other.waitForFunction(()=>window.amplifier?.getShellState()?.snapshots?.chats?.recentNavigation);
 await waitForRows(20,other);
 await other.getByRole('textbox',{name:'Message Amplifier',exact:true}).fill('Keep second client');
 await expectDraft(other,'Keep second client');
 const otherBefore=await preservation(other);
 assert.equal(otherBefore.draft,'Keep second client');
 for(const count of [40,60,80,100]){
  const load=sidebar().getByRole('button',{name:'Load more',exact:true});
  await load.scrollIntoViewIfNeeded();await load.focus();await page.keyboard.press('Enter');
  await waitForRows(count);
  const more=sidebar().getByRole('button',{name:count===100?'View all chats':'Load more',exact:true});
  await expect(more).toBeFocused();
  assert.equal((await projection()).remaining,total-count);
 }
 await expect(sidebar().getByRole('button',{name:'Load more',exact:true})).toHaveCount(0);
 await expect(sidebar().getByRole('button',{name:'View all chats',exact:true})).toBeVisible();
 await expect(sidebar().getByRole('button',{name:'All chats',exact:true})).toHaveCount(1);
 await sidebar().getByRole('button',{name:'View all chats',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().view.workSurface==='chats');
 assert.deepEqual(await preservation(page),before);
 await dispatch(page,'view.update',{patch:{workSurface:'chat'}});
 await page.waitForFunction(()=>window.amplifier.getState().view.workSurface==='chat');
 const capToggle=sidebar().getByRole('button',{name:'Show agent-created',exact:true});
 await capToggle.scrollIntoViewIfNeeded();await capToggle.focus();
 const capScroll=await page.locator('.a-nav-content').evaluate(node=>node.scrollTop);
 await page.keyboard.press('Space');
 await expect.poll(async()=>(await projection()).scope.showAgentCreated).toBe(true);
 await waitForRows(100);
 await expect(capToggle).toBeFocused();
 assert.equal(await page.locator('.a-nav-content').evaluate(node=>node.scrollTop),capScroll);
 assert.ok((await order()).includes(initial.commissioned));
 await sidebar().getByRole('button',{name:'Show agent-created',exact:true}).click();
 await expect.poll(async()=>(await projection()).scope.showAgentCreated).toBe(false);
 await waitForRows(100);assert.ok(!(await order()).includes(initial.commissioned));
 assert.deepEqual(await preservation(page),before);
 assert.deepEqual(await preservation(other),otherBefore);
 await waitForRows(20,other);
 // Add another real mounted module through the existing guarded composition.
 const inspected=await shell(page),clientId=inspected.clientId;
 const composition=structuredClone(inspected.composition);
 composition.instances.push({id:'recent-second',package:'builtin.chats',slot:'navigation'});
 const prepared=await dispatch(page,'shell.changes.prepare',{clientId,expectedRevision:inspected.revision,composition});
 await dispatch(page,'shell.changes.apply',{clientId,expectedRevision:inspected.revision,changeId:prepared.result.id});
 await waitForRows(20,page,'recent-second');await waitForRows(100);
 await sidebar(page,'recent-second').getByRole('button',{name:'Show agent-created',exact:true}).click();
 await expect.poll(async()=>(await projection(page,'recent-second')).scope.showAgentCreated).toBe(true);
 assert.equal((await projection()).scope.showAgentCreated,false);
 await page.reload();await page.waitForFunction(()=>window.amplifier?.getShellState()?.snapshots?.['recent-second']?.recentNavigation);
 await waitForRows(100);await waitForRows(20,page,'recent-second');
 await expect(sidebar(page,'recent-second').getByRole('button',{name:'Show agent-created',exact:true})).toHaveAttribute('aria-pressed','true');
 assert.deepEqual(await preservation(page),before);
 // Pin/current exceptions are eligibility only: current commissioned rank 25
 // stays open at limit 20 without stealing a row; Load more reveals it.
 const preColdView=await page.evaluate(()=>window.amplifier.getState().canvasWorkspace.views.find(row=>row.viewId==='primary'));
 await dispatch(page,'session.select',{id:initial.commissioned});
 await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id,initial.commissioned);
 await dispatch(page,'shell.view.update',{clientId:(await shell(page)).clientId,instanceId:'chats',patch:{navRecentLimit:20}});
 await waitForRows(20);assert.ok(!(await order()).includes(initial.commissioned));
 await sidebar().getByRole('button',{name:'Load more',exact:true}).click();
 await waitForRows(40);assert.ok((await order()).includes(initial.commissioned));
 await dispatch(page,'session.select',{id:initial.selected});
 await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id,initial.selected);
 await expectDraft(page,'Keep original draft');
 // A cold scope restore must discard only the old mount's render report.
 const coldExpected=structuredClone(before);coldExpected.canvas.renderReports={};
 assert.deepEqual(await preservation(page),coldExpected);
 assert.deepEqual((await dispatch(page,'canvas.versions.inspect',{id:canvasId,version:1,includeSource:true})).result,canonicalBefore);
 const currentView=await page.evaluate(()=>window.amplifier.getState().canvasWorkspace.views.find(row=>row.viewId==='primary'));
 await dispatch(page,'canvas.visibility',{open:true,sessionId:initial.selected,canvasId});
 await page.waitForFunction(id=>window.amplifier.getState().canvas.id===id&&window.amplifier.getState().canvas.renderReports?.preview?.status==='ready',canvasId);
 assert.deepEqual((await dispatch(page,'canvas.versions.inspect',{id:canvasId,version:1,includeSource:true})).result,canonicalBefore);
 const freshView=await page.evaluate(()=>window.amplifier.getState().canvasWorkspace.views.find(row=>row.viewId==='primary'));
 assert.ok(currentView.generation>preColdView.generation);
 assert.equal(freshView.generation,currentView.generation);
 await assert.rejects(async()=>dispatch(page,'canvas.views.command',{clientId:(await shell(page)).clientId,
  viewId:'primary',resourceId:preColdView.resourceId,resourceRevision:preColdView.resourceRevision,
  generation:preColdView.generation,action:'canvas.report',args:{id:canvasId,part:'stale-fixture',status:'ready',message:'Must not be accepted'}}));
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.renderReports?.['stale-fixture']),undefined);
 await dispatch(page,'canvas.visibility',{open:false,sessionId:initial.selected,canvasId});
 // Ordinary query failure: keep previous 40 rows and explicitly read on Retry.
 const kept=await order();let failReads=true;
 await page.route('**/api/shell?**',route=>failReads?route.abort('failed'):route.continue());
 await sidebar().getByRole('button',{name:'Load more',exact:true}).click();
 await expect(sidebar().getByRole('alert')).toContainText('previous chats are kept');
 assert.deepEqual(await order(),kept);
 const countBeforeRetry=(await metrics()).mutations.filter(row=>row.action==='shell.view.update').length;
 await sidebar().getByRole('button',{name:'Retry',exact:true}).click();
 await waitForRows(60);
 assert.equal((await metrics()).mutations.filter(row=>row.action==='shell.view.update').length,countBeforeRetry);
 failReads=false;await page.unroute('**/api/shell?**');
 // An unknown write is read back, never automatically replayed.
 let lost=true;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(lost&&body?.action==='shell.view.update'&&body.args?.instanceId==='chats'&&body.args.patch?.navRecentLimit===80){
   lost=false;await route.fetch();await route.abort('failed');
  }else await route.continue();
 });
 await sidebar().getByRole('button',{name:'Load more',exact:true}).click();
 // The independent SSE query may already have reconciled to 80. Either way,
 // only an explicit read is permitted; check server mutation identities.
 await expect.poll(async()=>(await projection()).limit).toBe(80);
 if(await sidebar().getByRole('button',{name:'Retry',exact:true}).count())await sidebar().getByRole('button',{name:'Retry',exact:true}).click();
 await waitForRows(80);await page.unroute('**/api/actions');
 const writes80=(await metrics()).mutations.filter(row=>row.action==='shell.view.update'&&row.args?.instanceId==='chats'&&row.args.patch?.navRecentLimit===80);
 assert.equal(writes80.length,2); // One initial 60->80, one later reconciled 60->80.
 // Steady progress retains the loaded order, scroll and active control.
 const steady=await order(),toggle=sidebar().getByRole('button',{name:'Show agent-created',exact:true});
 await toggle.scrollIntoViewIfNeeded();await toggle.focus();
 const scrollBefore=await page.locator('.a-nav-content').evaluate(node=>node.scrollTop);
 await control({progress:true});
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().sessions.find(row=>row.id===window.amplifier.getState().selectedSessionId)?.status)).toBe('working');
 assert.deepEqual(await order(),steady);await expect(toggle).toBeFocused();
 assert.equal(await page.locator('.a-nav-content').evaluate(node=>node.scrollTop),scrollBefore);
 await control({ready:true});await control({attention:true});
 await expect.poll(async()=>(await order())[0]).toBe(initial.selected);
 assert.deepEqual((await metrics()).runtimeCalls,[]);
 // Narrow/coarse touch: unchanged finite limit, 44px controls, same one context.
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>matchMedia('(pointer:coarse)').matches),true);
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await waitForRows(80);
 const target=sidebar().getByRole('button',{name:'Show agent-created',exact:true});
 await target.scrollIntoViewIfNeeded();const box=await target.boundingBox();assert.ok(box.height>=44);
 await target.tap();await expect(target).toHaveAttribute('aria-pressed','true');await waitForRows(80);
 if(evidence)await page.screenshot({path:evidence+'.narrow.png'});
 await page.setViewportSize({width:1280,height:1000});
 await sidebar().getByRole('button',{name:'Load more',exact:true}).focus();
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await composer.focus();
 await control({shrink:true});await expect(rows()).toHaveCount(6);
 await expect(composer).toBeFocused();
 await expect(sidebar().getByRole('button',{name:'Load more',exact:true})).toHaveCount(0);
 await expect(sidebar().getByRole('button',{name:'All chats',exact:true})).toHaveCount(1);
 assert.equal((await projection()).limit,80);
 const finalMetrics=await metrics();
 assert.deepEqual(finalMetrics.runtimeCalls,[]);
 assert.equal(finalMetrics.mutations.filter(row=>row.action==='conversation.send').length,0);
 assert.deepEqual(errors,[]);
 const result={applicationModule:boot.applicationModule,python:boot.python,expectedPackageChecked:boot.expectedPackageChecked,
  limits:[20,40,60,80,100],capViewAll:true,visibilityAt20And100:true,currentRank25NotForced:true,
  pinsDistinct:true,legacyForkVisible:true,queryFailureRetainedRows:true,retryReadOnly:true,unknownWriteNotReplayed:true,
  twoClientsTwoModulesReload:true,keyboardFocus:true,narrowCoarseTouch:true,progressAnchor:true,countShrink:true,
  draftAttachmentsCanvasModelBundleRetained:true,modelCalls:0};
 if(evidence){
  await page.screenshot({path:evidence+'.png',fullPage:true});
  await writeFile(evidence+'.receipts.json',JSON.stringify(receipts,null,2)+'\n');
  await writeFile(evidence+'.dom.html',await page.content());
  await writeFile(evidence,JSON.stringify(result,null,2)+'\n');
  await writeFile(evidence+'.junit.xml','<testsuite name="recent-visibility" tests="1" failures="0"><testcase name="http-sse-recent"/></testsuite>\n');
 }
 console.log(JSON.stringify(result,null,2));
}catch(error){
 if(evidence){
  await writeFile(evidence+'.receipts.json',JSON.stringify(receipts,null,2)+'\n');
  if(page){
   await writeFile(evidence+'.dom.html',await page.content()).catch(()=>{});
   await page.screenshot({path:evidence+'.failure.png',fullPage:true}).catch(()=>{});
  }
  await writeFile(evidence+'.junit.xml','<testsuite name="recent-visibility" tests="1" failures="1"><testcase name="http-sse-recent"><failure>Browser regression failed; see process stderr and retained evidence.</failure></testcase></testsuite>\n');
 }
 throw error;
}finally{
 await context?.close();await browser?.close();fixture.kill('SIGTERM');
}