// Real host/actions and production assets; temporary files, no provider calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url)),'--chat-controls'],{stdio:['ignore','pipe','inherit']});
const out=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-workspace-switching';
let browser,page;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 await mkdir(out,{recursive:true});
 browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 await page.goto(url);const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.waitFor();
 const home=path.dirname((await state()).workspaceDefaults.root),workspaces=[],chats=[];
 for(const name of ['Project A','Project B']){
  const folder=path.join(home,name);await mkdir(folder);await writeFile(path.join(folder,name+'.txt'),name+' contents');
  await action('workspace.add',{path:folder,name});
  const row=(await action('workspace.list',{query:name})).result.items.find(row=>row.path===folder);assert.ok(row);workspaces.push(row);
  const created=await action('session.create',{workspace:folder,title:name+' chat'});assert.equal(created.accepted,true);chats.push(created.result.sessionId);
 }
 const [a,b]=workspaces;
 const [aChat,bChat]=chats;
 await action('session.select',{id:aChat});
 await action('view.update',{patch:{navPinned:true}});
 await composer.fill('Unsent draft belongs to Project A');
 await action('attachment.add',{sessionId:aChat,name:'draft.txt',base64:'cHJlc2VydmU='});
 await page.request.post(url+'/fixture/activity',{data:{sessionId:aChat,status:'working'}});
 const body=page.locator('.a-work-browser'),header=page.locator('.a-work-heading');
 const shortcut=workspace=>page.getByRole('button',{name:'Open chats in '+workspace.path,exact:true});
 async function expectWorkspace(workspace,other){
  await expect(header).toHaveText(workspace.name);
  await expect(body.getByRole('heading',{level:1,name:workspace.name,exact:true})).toBeVisible();
  await expect(shortcut(workspace)).toHaveAttribute('aria-pressed','true');
  await expect(shortcut(other)).toHaveAttribute('aria-pressed','false');
  await expect(body.getByText(workspace.name+' chat',{exact:true})).toBeVisible();
  await expect(body.getByText(other.name+' chat',{exact:true})).toHaveCount(0);
  const current=await state();assert.equal(current.selectedSessionId,aChat);assert.equal(current.selectedWorkspaceId,a.id);
  await expect(page.getByRole('status').filter({hasText:'Project A chat · Working…'})).toBeVisible();
 }
 await shortcut(a).click();await expectWorkspace(a,b);
 // Force optimistic UI to lead the persisted action, as on a busy host.
 await page.route('**/api/actions',async route=>{const request=route.request().postDataJSON();if(request?.action==='view.update'&&request.args?.patch?.workSurface==='workspace')await new Promise(resolve=>setTimeout(resolve,250));await route.continue()});
 await shortcut(b).click();await expectWorkspace(b,a);
 await shortcut(a).click();await expectWorkspace(a,b);
 // Agent-visible shared action uses the same navigation state as sidebar clicks.
 await action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:b.id,workWorkspaceTab:'chats'}});await expectWorkspace(b,a);
 await body.getByRole('button',{name:'Details',exact:true}).click();await expect(body.getByText(b.path,{exact:true})).toBeVisible();
 await body.getByRole('button',{name:'Files',exact:true}).click();await expect(body.getByRole('button',{name:'Project B.txt',exact:true})).toBeVisible();await expect(body.getByRole('button',{name:'Project A.txt',exact:true})).toHaveCount(0);
 await shortcut(a).click();await expectWorkspace(a,b);
 await shortcut(b).click();await expectWorkspace(b,a);
 await page.screenshot({path:out+'/project-b-while-a-working.png'});
 await action('view.update',{patch:{workSurface:'chat',workWorkspaceId:null}});
 await expect(composer).toHaveValue('Unsent draft belongs to Project A');
 await expect(page.getByRole('button',{name:'Remove draft.txt',exact:true})).toBeVisible();
 await shortcut(b).click();await expectWorkspace(b,a);
 await body.getByRole('button',{name:'New chat',exact:true}).click();
 await expect.poll(async()=>(await state()).selectedSessionId).toBeNull();
 const draft=await state();assert.equal(draft.selectedSessionId,null);assert.equal(draft.view.newSessionDraft.workspace,b.path);
 await action('session.select',{id:aChat});await expect(composer).toHaveValue('Unsent draft belongs to Project A');await expect(page.getByRole('button',{name:'Remove draft.txt',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'All chats',exact:true}).click();await expect(body.getByText('Project A chat',{exact:true})).toBeVisible();await expect(body.getByText('Project B chat',{exact:true})).toBeVisible();
 const fixtureState=await (await page.request.get(url+'/fixture')).json();assert.deepEqual(fixtureState.sent,[]);assert.deepEqual(fixtureState.stopped,[]);
 assert.equal(fixtureState.registeredWorkspaces.length,3);assert.notEqual(aChat,bChat);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({status:'passed',scenarios:['repeated workspace clicks','shared navigation action','header/body/sidebar/list coherence','details/files scope','active chat preserved','draft and attachment preserved','new chat destination','all chats scope','no send or stop'],screenshots:out}));
}catch(error){if(page)await page.screenshot({path:out+'/failure.png'});throw error}
finally{await browser?.close();fixture.kill('SIGTERM')}
