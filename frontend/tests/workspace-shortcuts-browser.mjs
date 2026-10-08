// Manager-run DTU fixture: production assets/actions, synthetic runtime, no model calls.
// Run from frontend after the manager builds this candidate: node tests/workspace-shortcuts-browser.mjs
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url)),'--chat-controls'],{stdio:['ignore','pipe','inherit']});
const out=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-workspace-shortcuts';
let browser,context,page;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 await mkdir(out,{recursive:true});
 browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS?{args:['--no-zygote','--single-process','--disable-gpu']}:{ }),...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 const headers={Authorization:'Bearer fixture-browser-control-token'},errors=[],calls=[];
 context=await browser.newContext({viewport:{width:1440,height:1000},extraHTTPHeaders:headers});
 page=await context.newPage();
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')calls.push(request.postDataJSON())});
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const saved=async target=>{
  const clientId=await target.evaluate(()=>window.amplifier.getState().client.id);
  const response=await target.request.get(url+'/api/state',{headers:{'X-Amplifier-Client':clientId}});
  assert.ok(response.ok());return response.json();
 };
 await page.goto(url);const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.waitFor();
 const expectComposer=async text=>{await expect(composer).toHaveDraft(text);await expect.poll(()=>composer.textContent()).toBe(text)};
 const home=path.dirname((await state()).workspaceDefaults.root),workspaces=[];
 for(const team of ['team-one','team-two']){
  const folder=path.join(home,team,'same-long-workspace-name-that-must-truncate-without-moving-actions');
  await mkdir(folder,{recursive:true});assert.equal((await action('workspace.add',{path:folder})).accepted,true);
  const rows=(await action('workspace.list',{query:folder})).result.items;
  workspaces.push(rows.find(row=>row.path===folder));assert.ok(workspaces.at(-1));
 }
 const [a,b]=workspaces,created=await action('session.create',{workspace:a.path,title:'Original chat'}),sid=created.result.sessionId;
 await action('session.draft',{workspace:a.path});
 const setup={workspace:a.path,location:{kind:'workspace'},bundle:'work',selection:{instance:'test-provider',model:'chosen-model',effort:'high'}};
 await action('view.update',{patch:{newSessionDraft:setup,draft:'Retained unsent chat'}});
 await action('attachment.add',{name:'unsent.txt',base64:'a2VlcA=='});
 await action('session.select',{id:sid});
 await action('view.update',{patch:{draft:'Original chat text',navPinned:true}});
 await action('attachment.add',{sessionId:sid,name:'original.txt',base64:'a2VlcA=='});
 await action('canvas.show',{kind:'text',title:'Original Canvas',content:'Retained original Canvas'});
 const canvasId=(await saved(page)).canvas.id;
 const other=await context.newPage();await other.setViewportSize({width:1280,height:900});await other.goto(url);await other.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const otherBefore=await saved(other),baseline=(await saved(page)).library.sessionCount;
 // A different workspace is visible in the main browser, and the active chat is in A.
 await action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:a.id}});
 const section=page.locator('[data-sidebar-section=workspaces]');
 const row=workspace=>section.locator('.a-workspace-row').filter({has:page.getByRole('button',{name:'New chat in '+workspace.path,exact:true})});
 const newChat=workspace=>row(workspace).getByRole('button',{name:'New chat in '+workspace.path,exact:true});
 const geometry=async workspace=>row(workspace).evaluate(node=>{
  const rect=el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}};
  const select=node.querySelector('.a-workspace-select'),draft=node.querySelector('.a-workspace-new-chat'),more=node.querySelector('.a-navigation-more'),label=node.querySelector('.a-workspace-label');
  if(node.querySelector('button button'))throw Error('Nested buttons');
  return {row:rect(node),select:rect(select),draft:rect(draft),more:rect(more),label:rect(label),overflow:node.scrollWidth>node.clientWidth+1};
 });
 for(const width of [1440,1024]){
  await page.setViewportSize({width,height:1000});await page.mouse.move(width-10,900);
  const before=await geometry(b);await row(b).hover();await expect(newChat(b)).toHaveCSS('opacity','1');
  assert.deepEqual(await geometry(b),before);assert.equal(before.overflow,false);
  assert.ok(before.draft.x+before.draft.width<=before.more.x+1);assert.ok(before.more.x+before.more.width<=before.row.x+before.row.width+1);
  await row(b).getByRole('button',{name:'Open chats in '+b.path,exact:true}).focus();await page.keyboard.press('Tab');
  await expect(newChat(b)).toBeFocused();await expect(newChat(b)).toHaveCSS('opacity','1');
  assert.deepEqual(await geometry(b),before);
 }
 const callStart=calls.length;
 await page.keyboard.press('Enter');await expect.poll(async()=>(await saved(page)).view.newSessionDraft.workspace).toBe(b.path);
 let current=await saved(page);assert.equal(current.selectedSessionId,null);assert.equal(current.library.sessionCount,baseline);
 assert.equal(current.view.draft,'Retained unsent chat');assert.deepEqual(current.view.newSessionDraft.selection,setup.selection);assert.equal(current.view.newSessionDraft.bundle,'work');
 await expectComposer('Retained unsent chat');
 await expect(page.getByRole('button',{name:'Remove unsent.txt',exact:true})).toBeVisible();
 const invoked=calls.slice(callStart).flatMap(call=>call.action==='shell.command'?[{action:call.args.action,args:call.args.args}]:[call]);
 assert.ok(invoked.some(call=>call.action==='session.draft'&&call.args.workspace===b.path&&call.args.workspaceId===b.id));
 assert.equal(invoked.some(call=>['session.create','conversation.send','workspace.select'].includes(call.action)),false);
 await action('session.select',{id:sid});await expectComposer('Original chat text');await expect(page.getByRole('button',{name:'Remove original.txt',exact:true})).toBeVisible();
 current=await saved(page);assert.equal(current.canvas.id,canvasId);assert.equal(current.canvas.content,'Retained original Canvas');
 const otherAfter=await saved(other);assert.equal(otherAfter.selectedSessionId,otherBefore.selectedSessionId);assert.equal(otherAfter.selectedWorkspaceId,otherBefore.selectedWorkspaceId);assert.deepEqual(otherAfter.view,otherBefore.view);assert.deepEqual(otherAfter.canvas,otherBefore.canvas);
 await page.screenshot({path:out+'/desktop.png'});
 // Touch uses always-visible, independent sibling targets and closes the modal on New chat.
 // Reuse the primary client for mobile; the independent second client remains
 // untouched. Native CDP input emulation must actually change pointer media.
 const touch=page,desktopViewport=page.viewportSize(),cdp=await context.newCDPSession(page);
 await cdp.send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});
 await cdp.send('Emulation.setEmitTouchEventsForMouse',{enabled:true,configuration:'mobile'});
 await touch.setViewportSize({width:390,height:844});
 assert.equal(await touch.evaluate(()=>matchMedia('(pointer:coarse)').matches&&matchMedia('(hover:none)').matches),true);
 const touchAction=(name,args={})=>touch.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 await touchAction('view.update',{patch:{navExpanded:true}});
 const touchNew=touch.locator('[data-sidebar-section=workspaces]').getByRole('button',{name:'New chat in '+b.path,exact:true});
 await expect(touchNew).toBeVisible();await expect(touchNew).toHaveCSS('opacity','1');
 const box=await touchNew.boundingBox();assert.ok(box.width>=44&&box.height>=44);
 const touchPoint=await touchNew.boundingBox();
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:touchPoint.x+touchPoint.width/2,y:touchPoint.y+touchPoint.height/2}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 await expect.poll(async()=>(await saved(touch)).view.newSessionDraft.workspace).toBe(b.path);
 assert.equal((await saved(touch)).library.sessionCount,baseline);assert.equal((await saved(touch)).view.navExpanded,false);
 await touch.screenshot({path:out+'/touch.png'});
 await cdp.send('Emulation.setEmitTouchEventsForMouse',{enabled:false});
 await cdp.send('Emulation.setTouchEmulationEnabled',{enabled:false});
 await page.setViewportSize(desktopViewport);
 await action('session.select',{id:sid});await expectComposer('Original chat text');
 assert.equal((await saved(page)).canvas.id,canvasId);
 // Pin B before its first chat, then apply enough activity to displace an unpinned shortcut.
 const scopeBefore=await saved(page);
 await row(b).getByRole('button',{name:'Details and actions for '+b.name,exact:true}).click();
 await page.getByRole('button',{name:'Pin workspace '+b.path,exact:true}).click();
 await expect.poll(async()=>(await saved(page)).pinnedWorkspaceIds).toEqual([b.id]);
 const scopeAfter=await saved(page);
 assert.equal(scopeAfter.selectedSessionId,scopeBefore.selectedSessionId);assert.equal(scopeAfter.selectedWorkspaceId,scopeBefore.selectedWorkspaceId);assert.deepEqual(scopeAfter.view,scopeBefore.view);assert.deepEqual(scopeAfter.canvas,scopeBefore.canvas);
 await expect(section.locator('.a-workspace-row').first().getByRole('button',{name:'New chat in '+b.path,exact:true})).toBeVisible();
 await page.reload();await composer.waitFor();await expect.poll(async()=>(await saved(page)).pinnedWorkspaceIds).toEqual([b.id]);
 for(let index=0;index<8;index++){
  const folder=path.join(home,'active-'+index);await mkdir(folder,{recursive:true});await action('workspace.add',{path:folder});
  await action('session.create',{workspace:folder,title:'Activity pressure '+index});
 }
 await expect(section.locator('.a-workspace-row').first().getByRole('button',{name:'New chat in '+b.path,exact:true})).toBeVisible();
 await row(b).getByRole('button',{name:'Details and actions for '+b.name,exact:true}).click();
 const details=page.getByRole('dialog',{name:'Details for '+b.name,exact:true});
 await expect(details.getByText('0',{exact:true})).toBeVisible();
 await page.keyboard.press('Escape');
 await action('workspace.rename',{id:b.id,name:'Pinned empty workspace'});
 await expect(row(b).getByRole('button',{name:'Details and actions for Pinned empty workspace',exact:true})).toBeVisible();
 await action('workspace.pin',{id:a.id,pinned:true});
 await row(b).getByRole('button',{name:'Details and actions for Pinned empty workspace',exact:true}).click();
 await page.getByRole('button',{name:'Move workspace down '+b.path,exact:true}).click();
 await expect.poll(async()=>(await saved(page)).pinnedWorkspaceIds).toEqual([a.id,b.id]);
 await page.reload();await composer.waitFor();
 await expect.poll(async()=>(await saved(page)).pinnedWorkspaceIds).toEqual([a.id,b.id]);
 assert.equal(await section.getByRole('button',{name:'New chat in '+b.path,exact:true}).count(),1);
 const afterPins=await saved(other);assert.equal(afterPins.selectedSessionId,otherBefore.selectedSessionId);assert.equal(afterPins.selectedWorkspaceId,otherBefore.selectedWorkspaceId);assert.deepEqual(afterPins.view,otherBefore.view);
 const runtime=await (await page.request.get(url+'/fixture')).json();assert.deepEqual(runtime.sent,[]);assert.deepEqual(runtime.started,[]);assert.deepEqual(runtime.stopped,[]);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({status:'passed',scenarios:['row path not active folder','no creation or model turn','retained draft text attachments model bundle','chat Canvas roundtrip','other client preserved','long equal names stable hover and focus geometry','keyboard Enter','touch target','empty workspace pin survives reload and activity pressure','rename keeps pin identity','complete ordered pins survive reload without duplicates'],screenshots:out}));
}catch(error){if(page)await page.screenshot({path:out+'/failure.png'});throw error}
finally{await context?.close();await browser?.close();fixture.kill('SIGTERM')}