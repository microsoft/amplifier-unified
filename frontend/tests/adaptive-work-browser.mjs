import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

// Production host, built client, private fixture state and synthetic runtime.
// No provider account, existing service, or user history is involved.
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||join(root,'output/browser-checks/adaptive-work');
await mkdir(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||join(root,'.venv/bin/python'),
 [join(root,'tests/fixtures/empty_host_ui_server.py'),'--chat-controls'],{stdio:['ignore','pipe','pipe']});
let stderr='',browser,page,failure;
fixture.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-12000)});
const report={checks:[],errors:[],runtime:'synthetic',liveProvider:false};
const check=name=>report.checks.push(name);
try{
 const url=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(Error('Fixture startup timeout: '+stderr)),20000);
  fixture.once('error',error=>{clearTimeout(timer);reject(error)});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+stderr))});
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}});
 });
 browser=await chromium.launch({headless:true,args:JSON.parse(process.env.AMPLIFIER_CHROMIUM_ARGS||'[]')});
 const context=await browser.newContext({viewport:{width:1440,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page=await context.newPage();page.setDefaultTimeout(15000);page.on('pageerror',error=>report.errors.push(error.message));
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const act=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const ready=async()=>{await expect(page.locator('.a-home-filters')).toBeVisible();await expect(page.locator('.a-adaptive-home')).not.toContainText('Loading your work…')};
 const home=async()=>{await page.getByRole('navigation',{name:'Breadcrumb'}).getByRole('button',{name:'Home',exact:true}).click();await ready()};
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await page.goto(url);await ready();
 await expect(page.locator('.a-work-heading')).toHaveText('Home');
 await expect(page.locator('#workspace-navigation').getByRole('button',{name:'Home',exact:true})).toHaveCount(0);
 check('Fresh clients arrive at Home; Home has one header entry and no sidebar entry');

 const fixtureHome=dirname((await state()).workspaceDefaults.root),projects=[],chats=[];
 for(const [name,title] of [['Launch','Home launch notes'],['Offsite','Other project chat']]){
  const path=join(fixtureHome,'home-'+name.toLowerCase());await mkdir(path);
  await act('workspace.add',{path,name});
  projects.push((await act('workspace.list',{query:name})).result.items.find(row=>row.path===path));
  const created=await act('session.create',{workspace:path,title,select:false});chats.push(created.sessionId||created.result.sessionId);
 }
 await act('view.update',{patch:{workSurface:'home'}});await ready();
 await page.getByRole('group',{name:'Work view'}).getByRole('button',{name:'Studio',exact:true}).click();
 await page.getByLabel('Work scope',{exact:true}).selectOption(projects[0].id);await ready();
  await expect(page.locator('.a-work-card h3')).toHaveText('Home launch notes');
 await page.getByLabel('Search your work',{exact:true}).fill('launch');
 await page.getByRole('button',{name:'Open project',exact:true}).click();
 await expect(page.getByRole('group',{name:'Workspace view',exact:true})).toBeVisible();
 await home();await expect(page.getByLabel('Search your work',{exact:true})).toHaveValue('launch');
 check('Immediate search blur keeps Open project usable and preserves the pending search on return');
 await page.getByLabel('Search your work',{exact:true}).fill('');
 await expect.poll(async()=>(await state()).view.workHomeQuery).toBe('');await ready();
 await page.getByLabel('Search your work',{exact:true}).fill('launch');
 await page.getByRole('button',{name:'New chat',exact:true}).click();await expect(composer).toBeVisible();
 await expect.poll(async()=>(await state()).view.workHomeQuery).toBe('launch');
 assert.equal((await state()).view.newSessionDraft.workspace,projects[0].path);
 await home();await expect(page.getByLabel('Search your work',{exact:true})).toHaveValue('launch');
 await expect(page.getByLabel('Work scope',{exact:true})).toHaveValue(projects[0].id);
 await expect(page.locator('.a-adaptive-home')).toHaveAttribute('data-view','studio');
 check('Immediate search departure is saved; new chat uses the confirmed project; Home restores its choices');

 await page.locator('.a-work-card-open').click();await expect(composer).toBeVisible();
 await composer.fill('Keep this Home draft');
 await act('canvas.show',{kind:'markdown',title:'Home saved result',content:'# Home saved result\n\nRetain this exact output.'});
 const canvas=(await state()).canvas.id;
 await act('session.pin',{id:chats[0],pinned:true});
 await page.reload();await expect(composer).toHaveDraft('Keep this Home draft');
 assert.equal((await state()).selectedSessionId,chats[0]);assert.equal((await state()).canvas.id,canvas);
 assert.equal((await state()).view.workSurface,'chat');
 const freshContext=await browser.newContext({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 try{const fresh=await freshContext.newPage();await fresh.goto(url);await expect(fresh.locator('.a-home-filters')).toBeVisible();await expect(fresh.getByLabel('Search your work',{exact:true})).toHaveValue('')}finally{await freshContext.close()}
 check('Reload resumes the chat with its draft and saved Canvas; another client still starts at Home');

 await home();await page.getByLabel('Work scope',{exact:true}).selectOption('all');await ready();
 const original=(await page.evaluate(()=>window.amplifier.getShellState())).composition;
 const pin=async workspaceId=>{
  const current=await page.evaluate(()=>window.amplifier.getShellState());
  const composition=structuredClone(original);
  if(workspaceId)composition.instances.find(row=>row.package==='builtin.chats').scope={mode:'pinned',workspaceId};
  const clientId=(await state()).client.id;
  const prepared=await act('shell.changes.prepare',{clientId,expectedRevision:current.revision,composition});
  const result=await act('shell.changes.apply',{clientId,expectedRevision:current.revision,changeId:prepared.result.id});
  assert.equal(result.accepted,true);
  await page.waitForFunction(revision=>window.amplifier.getShellState()?.revision===revision,current.revision+1);
 };
 await pin(projects[1].id);await ready();
 await expect(page.getByLabel('Work scope',{exact:true})).toBeDisabled();
 await expect(page.getByLabel('Work scope',{exact:true})).toHaveValue(projects[1].id);
 await expect(page.locator('.a-work-card h3')).toHaveText('Other project chat');
 await act('view.update',{patch:{workHomeScope:'managed'}});await ready();
 await expect(page.locator('.a-work-card h3')).toHaveText('Other project chat');
 await page.getByRole('button',{name:'New chat',exact:true}).click();await expect(composer).toBeVisible();
 assert.equal((await state()).view.newSessionDraft.workspace,projects[1].path);await home();
 await pin(projects[0].id);await ready();await expect(page.locator('.a-work-card h3')).toHaveText('Home launch notes');
 await pin('home-missing-project');
 await expect(page.getByRole('alert')).toContainText('This project is unavailable.');
 await expect(page.getByRole('button',{name:'Show all work',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'New chat',exact:true})).toBeDisabled();
 await expect(page.locator('.a-adaptive-home')).not.toContainText('Loading your work…');
 check('Pinned project scopes settle, constrain creation, switch safely, and never widen when unavailable');
 await pin(null);await page.getByLabel('Work scope',{exact:true}).selectOption('all');await ready();

 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await expect(page.locator('#workspace-navigation')).toHaveAttribute('aria-modal','true');
 await page.locator('.a-pinned-chats .a-nav-chat-select').filter({hasText:'Home launch notes'}).click();
 await expect(page.locator('#workspace-canvas')).toContainText('Retain this exact output.');
 assert.equal(await page.locator('#workspace-canvas').evaluate(element=>!!element.closest('[inert]')),false);
 await page.getByRole('button',{name:'Back to chat',exact:true}).click();
 await expect(composer).toBeVisible();await expect(composer).toHaveDraft('Keep this Home draft');
 assert.equal((await state()).canvas.id,canvas);
 await home();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 check('Phone drawer selection reveals an interactive saved Canvas and preserves the chat draft');
 assert.deepEqual(report.errors,[]);report.build=JSON.parse(await readFile(join(root,'amplifier_web/static/build.json'),'utf8'));report.passed=true;
 await page.screenshot({path:join(output,'phone-home.png')});
}catch(error){failure=error;report.passed=false;report.failure=error.message;await page?.screenshot({path:join(output,'failure.png')}).catch(()=>{});report.text=await page?.locator('body').innerText().catch(()=>null)}
finally{
 await browser?.close();
 if(fixture.exitCode===null&&fixture.signalCode===null){
  const exited=once(fixture,'exit');fixture.kill('SIGTERM');let timer;
  try{await Promise.race([exited,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Fixture did not retire gracefully')),10000)})]);report.fixtureClosed=true}
  catch(error){fixture.kill('SIGKILL');await exited;failure ||= error;report.passed=false;report.cleanupError=error.message}
  finally{clearTimeout(timer)}
 }
 await writeFile(join(output,'report.json'),JSON.stringify(report,null,2));
 console.log(JSON.stringify(report));
}
if(failure)throw failure;
