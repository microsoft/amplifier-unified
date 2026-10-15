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
let browser,page;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
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
 await action('session.select',{id:b});await action('session.select',{id:c});let release,entered;
 const gate=new Promise(resolve=>{release=resolve}),seen=new Promise(resolve=>{entered=resolve});
 await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(body?.action==='session.select'){entered();await gate}await route.continue()});
 await back.click();await seen;
 const newer=action('view.update',{patch:{workSurface:'chats'}});release();await newer;await page.unroute('**/api/actions');
 await expect.poll(async()=>(await state()).view.workSurface).toBe('chats');
 await expect.poll(async()=>(await saved()).entries.at(-1).kind).toBe('chats');
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
 console.log(JSON.stringify({status:'passed',checks:['traversal','draft and attachment retention','detached New chat','workspace tabs','file preview','reload','rejection','ordered navigation','keyboard and hold','1440/390 layout'],pageErrors:errors}));
}finally{await browser?.close();fixture.kill('SIGTERM')}
