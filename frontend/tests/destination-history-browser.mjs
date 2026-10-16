// Owned synthetic host + production bundle. No model calls or shared application.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const out=process.env.AMPLIFIER_TEST_OUTPUT;
if(!out)throw Error('Set AMPLIFIER_TEST_OUTPUT to an owned evidence directory.');
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url)),'--chat-controls'],{stdio:['ignore','pipe','inherit']});
let browser,page,scenarioFailure;
const pendingGates=new Set();
function gate(){
 let resolve;const wait=new Promise(done=>{resolve=done});
 const release=()=>{pendingGates.delete(release);resolve()};
 pendingGates.add(release);return {wait,release};
}
function stopFixture(){
 if(fixture.exitCode!==null||fixture.signalCode!==null||!fixture.pid)return Promise.resolve();
 return new Promise((resolve,reject)=>{
  let escalation,deadline,settled=false;
  const finish=error=>{
   if(settled)return;settled=true;
   clearTimeout(escalation);clearTimeout(deadline);
   fixture.off('exit',exited);fixture.off('error',failed);
   if(error)reject(error);else resolve();
  };
  const exited=()=>finish(),failed=error=>finish(error);
  const signal=value=>{try{fixture.kill(value)}catch(error){finish(error)}};
  fixture.once('exit',exited);fixture.once('error',failed);
  console.error(`Stopping owned fixture PID ${fixture.pid}`);
  escalation=setTimeout(()=>{
   if(fixture.exitCode!==null||fixture.signalCode!==null){exited();return}
   console.error(`Escalating owned fixture PID ${fixture.pid} to SIGKILL`);
   deadline=setTimeout(()=>finish(Error(`Could not confirm exit of owned fixture PID ${fixture.pid}`)),5000);
   signal('SIGKILL');
  },5000);
  signal('SIGTERM');
 });
}
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('error',error=>{clearTimeout(timer);reject(error)});fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 await mkdir(out,{recursive:true});
 browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 const context=await browser.newContext({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const saved=()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.destinationHistory')));
 const back=page.getByRole('button',{name:'Back in navigation history',exact:true});
 const forward=page.getByRole('button',{name:'Forward in navigation history',exact:true});
 const menu=page.getByRole('button',{name:'Navigation history',exact:true});
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await page.goto(url);await composer.waitFor();
 const home=path.dirname((await state()).workspaceDefaults.root),folder=path.join(home,'History project');await mkdir(folder);await writeFile(path.join(folder,'preview.txt'),'Synthetic preview');
 await action('workspace.add',{path:folder,name:'History project'});
 const workspace=(await action('workspace.list',{query:'History project'})).result.items.find(row=>row.path===folder);assert.ok(workspace);
 const ids=[];for(const title of ['Alpha','Beta','Gamma'])ids.push((await action('session.create',{workspace:folder,title})).result.sessionId);
 const [a,b,c]=ids;await action('session.select',{id:a});
 await composer.fill('Unsent Alpha draft');await action('attachment.add',{sessionId:a,name:'note.txt',base64:'cHJlc2VydmU='});
 await action('session.select',{id:b});await action('session.select',{id:c});
 const baseline=await saved();await back.click();await expect.poll(async()=>(await state()).selectedSessionId).toBe(b);
 await expect.poll(async()=>(await saved()).cursor).toBe(baseline.cursor-1);
 await expect(back).toBeEnabled();await expect(back).toBeFocused();
 await forward.click();await expect.poll(async()=>(await state()).selectedSessionId).toBe(c);
 assert.equal((await saved()).entries.length,baseline.entries.length);
 // One accessible menu, repeated names disambiguated by workspace/ID, Escape restores focus.
 await menu.focus();await page.keyboard.press('ArrowDown');await expect(page.getByRole('menu',{name:'Visited destinations'})).toBeVisible();
 assert.equal(await page.locator('[role=menuitem]:focus').count(),1);
 await page.keyboard.press('End');await page.keyboard.press('Home');await page.keyboard.press('Escape');await expect(menu).toBeFocused();
 // Hold opens the same menu; pointer release must not also execute Back.
 const beforeHold=(await saved()).cursor;
 await back.dispatchEvent('pointerdown',{pointerType:'mouse',button:0,clientX:10,clientY:10});
 await expect(page.getByRole('menu',{name:'Visited destinations'})).toBeVisible();
 await back.dispatchEvent('pointerup',{pointerType:'mouse',button:0});await back.dispatchEvent('click');
 assert.equal((await saved()).cursor,beforeHold);await page.keyboard.press('Escape');
 // Select the saved Alpha visit; all draft ownership and attachments remain intact.
 await menu.click();await page.getByRole('menuitem').filter({hasText:'Alpha'}).last().click();
 await expect(composer).toHaveText('Unsent Alpha draft');await expect(page.getByRole('button',{name:'Remove note.txt',exact:true})).toBeVisible();
 const alpha=await saved();await action('session.draft',{});await expect.poll(async()=>(await state()).selectedSessionId).toBeNull();
 assert.equal((await saved()).entries.length,alpha.entries.length);await back.click();await expect.poll(async()=>(await state()).selectedSessionId).toBe(a);
 await expect(composer).toHaveText('Unsent Alpha draft');
 // Browsing retains chat identity but creates a workspace destination; tabs update it.
 await action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:workspace.id,workWorkspaceTab:'chats'}});
 const workspaceVisit=await saved();await action('view.update',{patch:{workWorkspaceTab:'files'}});
 assert.equal((await saved()).entries.length,workspaceVisit.entries.length);
 await expect.poll(async()=>(await saved()).entries.at(-1).tab).toBe('files');
 await page.getByRole('button',{name:'preview.txt Open in “Alpha”',exact:true}).click();
 await expect.poll(async()=>(await state()).view.workSurface).toBe('chat');
 await back.click();await expect.poll(async()=>(await state()).view.workSurface).toBe('workspace');
 assert.equal((await state()).view.workWorkspaceTab,'files');
 // Reload retains the exact trail but does not navigate or append on bootstrap.
 const preReload=await saved();await page.reload();await expect(menu).toBeEnabled();
 await expect.poll(async()=>JSON.stringify(await saved())).toBe(JSON.stringify(preReload));
 // Explicit traversal rejection leaves cursor and entries unchanged; no missing-target fallback.
 const preReject=await saved();let rejected=false;
 await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(!rejected&&body?.action==='session.select'){rejected=true;await route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({error:'Synthetic destination unavailable'})})}else await route.continue()});
 await back.click();await expect(page.getByRole('alert').filter({hasText:'Synthetic destination unavailable'})).toBeVisible();
 assert.deepEqual(await saved(),preReject);await page.unroute('**/api/actions');
 // A held selection cannot beat newer browsing: the existing shared intent queue owns order.
 await action('session.select',{id:b});await action('session.select',{id:c});let entered=false;
 const selectionGate=gate();
 await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(body?.action==='session.select'){entered=true;await selectionGate.wait}await route.continue()});
 await back.click();await expect.poll(()=>entered).toBe(true);
 const newer=action('view.update',{patch:{workSurface:'chats'}});selectionGate.release();await newer;await page.unroute('**/api/actions');
 await expect.poll(async()=>(await state()).view.workSurface).toBe('chats');
 await expect.poll(async()=>(await saved()).entries.at(-1).kind).toBe('chats');
 // Optimistic selections are not visits, even during unrelated state publication.
 await action('session.select',{id:c});const beforeOptimistic=await saved(),optimisticGate=gate();let optimisticHeld=false;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='session.select'&&body.args.id===b){optimisticHeld=true;await optimisticGate.wait;await route.fulfill({status:409,contentType:'application/json',body:'{"error":"Synthetic selection rejection"}'})}
  else await route.continue();
 });
 await page.evaluate(id=>{window.optimisticSelection=window.amplifier.dispatch('session.select',{id}).catch(error=>({error:error.message}))},b);
 await expect.poll(()=>optimisticHeld).toBe(true);
 await action('session.pin',{id:c,pinned:true});
 assert.deepEqual(await saved(),beforeOptimistic,'Unrelated authoritative state must not record an optimistic selection');
 optimisticGate.release();await page.evaluate(()=>window.optimisticSelection);await page.unroute('**/api/actions');
 assert.deepEqual(await saved(),beforeOptimistic);
 // Back alone supersedes an older archive browse; Undo/Dismiss must not mask this race.
 await action('session.select',{id:b});await action('session.select',{id:c});
 const beforeArchive=await saved(),archiveGate=gate();let archiveHeld=false,workspaceRequests=0;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='view.update'&&body.args.patch?.workSurface==='workspace')workspaceRequests++;
  if(body?.action==='view.update'&&body.args.patch?.workSurface==='chat'){archiveHeld=true;await archiveGate.wait}
  await route.continue();
 });
 await page.evaluate(()=>{window.archiveQueueBarrier=window.amplifier.dispatch('view.update',{patch:{workSurface:'chat'}})});
 await expect.poll(()=>archiveHeld).toBe(true);
 await page.getByRole('button',{name:'Chat',exact:true}).click();
 await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Archive',exact:true}).click();
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat archived');
 await expect(page.locator('#amp-one')).toHaveAttribute('data-work-surface','workspace');
 await back.click();archiveGate.release();await page.evaluate(()=>window.archiveQueueBarrier);
 await expect.poll(async()=>(await saved()).cursor).toBe(beforeArchive.cursor-1);
 await expect.poll(async()=>(await state()).selectedSessionId).toBe(b);
 assert.equal(workspaceRequests,0);assert.deepEqual((await saved()).entries,beforeArchive.entries);
 await page.unroute('**/api/actions');
 await page.locator('[data-archive-notice]').getByRole('button',{name:'Undo',exact:true}).click();
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat restored');
 assert.equal((await state()).selectedSessionId,b);
 // Raw archive changes organization only, not selection, workspace or visits.
 const rawState=await state(),rawTrail=await saved(),rawArchive=await action('session.archive',{id:b});
 assert.equal(rawArchive.accepted,true);assert.equal(rawArchive.result.id,b);assert.equal(rawArchive.result.archived,true);
 assert.equal((await state()).selectedSessionId,rawState.selectedSessionId);
 assert.equal((await state()).selectedWorkspaceId,rawState.selectedWorkspaceId);
 assert.equal((await state()).view.workSurface,rawState.view.workSurface);assert.deepEqual(await saved(),rawTrail);
 await action('session.restore',{id:b});
 // Dirty Canvas must reject actual history traversal, keeping the exact mounted editor.
 await action('session.select',{id:a});await action('session.select',{id:b});
 await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true}});
 await action('canvas.show',{kind:'markdown',title:'History dirty guard',content:'# Retained source'});
 const manifest={id:'example.history-dirty',label:'History unsaved editor',version:'1.0.0',apiVersion:'1.0',profile:'trusted-native-renderer-v1',stateSchema:'canvas-view-v1',resourceKinds:['markdown'],capabilities:['canvas.resource.read','canvas.view.update']};
 const source='export default ({React})=>function Editor({host}){const [text,setText]=React.useState("");const [mount]=React.useState(()=>crypto.randomUUID());return React.createElement("label",null,"History unsaved input",React.createElement("input",{"data-mount":mount,value:text,onChange:event=>{setText(event.target.value);host.setDirty(true)}}))}';
 const digest=(await action('shell.packages.stage',{manifest,source})).result.digest;
 assert.equal((await action('shell.packages.validate',{digest})).result.status,'passed');
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(digest);
 const editor=page.getByLabel('History unsaved input');await editor.fill('UNSAVED HISTORY EDIT');
 const inspect=async()=>(await action('canvas.views.inspect')).result.views.find(row=>row.viewId==='primary');
 await expect.poll(async()=>(await inspect()).dirty).toBe(true);
 const mount=await editor.getAttribute('data-mount'),dirtyTrail=await saved();
 await back.click();await expect(page.getByRole('alert').filter({hasText:/viewer edit/})).toBeVisible();
 assert.equal((await state()).selectedSessionId,b);assert.deepEqual(await saved(),dirtyTrail);
 await expect(editor).toHaveValue('UNSAVED HISTORY EDIT');assert.equal(await editor.getAttribute('data-mount'),mount);
 await action('view.update',{patch:{workSurface:'workspaces'}});await expect(editor).toBeHidden();
 const browsedDirtyTrail=await saved();
 await back.click();await expect(editor).toBeVisible();await expect(editor).toHaveValue('UNSAVED HISTORY EDIT');
 await expect.poll(async()=>(await saved()).cursor).toBe(dirtyTrail.cursor);
 assert.deepEqual((await saved()).entries,browsedDirtyTrail.entries,'Returning must keep the workspace visit available through Forward');
 assert.equal(await editor.getAttribute('data-mount'),mount);
 const recovering=await inspect();
 await action('canvas.views.recover',Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,recovering[key]])));
 await action('canvas.close');
 // Settings in Canvas leaves the header usable; cancelling Back preserves private edits.
 await action('session.select',{id:a});await action('session.select',{id:b});
 await action('canvas.show',{kind:'settings'});
 await action('view.update',{patch:{settingsExpanded:['providers'],settingsCanvas:true,panel:null}});
 const settings=page.locator('.a-settings-experience[data-settings-page="providers"]');
 await expect(settings).toBeVisible();await settings.getByRole('button',{name:'Add provider',exact:true}).click();
 await settings.getByRole('button',{name:'Show advanced configuration',exact:true}).click();
 const privateEdit=settings.locator('[id$="provider-config"]');await privateEdit.fill('{"unsaved-history-input":');
 const settingsTrail=await saved();await back.click();
 await expect(page.getByRole('alertdialog')).toBeVisible();
 await page.getByRole('button',{name:'Keep settings open',exact:true}).click();
 assert.equal((await state()).selectedSessionId,b);assert.deepEqual(await saved(),settingsTrail);
 await expect(privateEdit).toHaveValue('{"unsaved-history-input":');
 await page.getByRole('button',{name:'Close tab Settings',exact:true}).click();
 await page.getByRole('button',{name:'Discard changes',exact:true}).click();
 await action('canvas.visibility',{open:false});
 // Narrow target dimensions and no reintroduced remembered-chat strip.
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:900});await menu.click();await expect(page.getByRole('menu')).toBeVisible();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  if(width===390)for(const control of [back,forward,menu]){const box=await control.boundingBox();assert.ok(box.width>=44&&box.height>=44)}
  await page.screenshot({path:path.join(out,`history-${width}.png`)});await page.keyboard.press('Escape');
 }
 await expect(page.getByRole('button',{name:'Back to chat',exact:true})).toHaveCount(0);
 const captured=await state();assert.equal(Object.hasOwn(captured,'destinationHistory'),false);
 const serialized=JSON.stringify(await saved());assert.doesNotMatch(serialized,/Unsent Alpha draft|Synthetic preview|cHJlc2VydmU=/);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({status:'passed',checks:['traversal','draft and attachment retention','detached New chat','workspace tabs','file preview','reload','rejection','ordered navigation','optimistic isolation','Back supersedes archive','raw archive unchanged','dirty Canvas and Settings','keyboard and hold','1440/390 layout'],pageErrors:errors}));
}catch(error){scenarioFailure=error;throw error}finally{
 for(const release of pendingGates)release();
 // Start fixture termination even if browser cleanup itself stalls. Keep the
 // original assertion and a separate cleanup failure instead of replacing it.
 const cleanup=await Promise.allSettled([Promise.resolve().then(()=>browser?.close()),stopFixture()]);
 const failures=cleanup.filter(row=>row.status==='rejected').map(row=>row.reason);
 if(failures.length){
  if(scenarioFailure)for(const error of failures)console.error('Cleanup also failed:',error);
  else throw new AggregateError(failures,'Owned browser fixture cleanup failed');
 }
}
