import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {expandShell} from '../src/shell/wire.js';

// Production host/assets and client navigation, with two synthetic workspaces.
// No model calls, timing ceilings, or private application state are involved.
const output=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-scoped-navigation';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),
 [fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url)),'--chat-controls'],{stdio:['ignore','pipe','pipe']});
const base=['pinnedNavigation','recentNavigation','workspaceShortcuts'];
const requested=extra=>[...base,...extra].sort();
let browser,page,fixtureHome,releaseSearch=()=>{},releaseReplacement=()=>{},primaryError;
let stderr='';fixture.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-16384)});
const samples=[],checks=[],errors=[],responseTasks=[];
function shape(shell,resources){
 const builtin=(shell.resolvedInstances||[]).filter(row=>['builtin.chats','builtin.workspaces'].includes(row.package));
 assert.ok(builtin.length,'Built-in navigation must be present');
 for(const instance of builtin){
  const snapshot=shell.snapshots[instance.id];
  assert.deepEqual(snapshot.navigationResources,resources);
  for(const key of ['pinnedNavigation','recentNavigation','workspaceShortcuts','unpinnedWorkspaceShortcuts','recentShortcuts','recentScope'])assert.ok(Object.hasOwn(snapshot,key),key);
  for(const key of ['chatNavigation','homeNavigation','workspaceOverview'])assert.equal(Object.hasOwn(snapshot,key),false,key+' was not requested');
  assert.equal(Object.hasOwn(snapshot,'workspaceExplorer'),resources.includes('workspaceExplorer'));
  assert.ok(Object.hasOwn(snapshot.sidebarNavigation,'recentView'));
  for(const [key,resource] of [['search','sidebarSearch'],['workspace','sidebarWorkspace'],['recent','sidebarRecent'],['pinned','sidebarPinned']])
   assert.equal(Object.hasOwn(snapshot.sidebarNavigation,key),resources.includes(resource),key+' coverage');
 }
}
try{
 await mkdir(output,{recursive:true});
 const url=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(Error('Fixture startup timeout\n'+stderr)),20000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+'\n'+stderr))});
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}});
 });
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.message));
 page.on('response',response=>{
  const query=new URL(response.url());if(query.pathname!=='/api/shell'||!query.searchParams.has('navigationResources')||samples.length>=96)return;
  const resources=JSON.parse(query.searchParams.get('navigationResources'));
  const sample={resources,status:response.status()};samples.push(sample);
  responseTasks.push((async()=>{try{const shell=expandShell(await response.json());shape(shell,resources);sample.valid=true;sample.keys=Object.keys(shell.snapshots?.chats||{}).sort()}catch(error){sample.error=String(error)}})());
 });
 const act=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const shell=()=>page.evaluate(()=>window.amplifier.getShellState());
 const waitCoverage=async extra=>{
  const resources=requested(extra);
  await expect.poll(async()=>{const value=await shell();return (value?.resolvedInstances||[]).filter(row=>['builtin.chats','builtin.workspaces'].includes(row.package)).map(row=>value.snapshots[row.id]?.navigationResources)}).toEqual([resources,resources]);
  shape(await shell(),resources);checks.push({resources});
 };
 await page.goto(url);const composer=page.getByRole('textbox',{name:'Message Amplifier'});await expect(composer).toBeVisible();await waitCoverage([]);
 const home=path.dirname((await state()).workspaceDefaults.root),workspaces=[],chats=[];fixtureHome=home;
 for(const name of ['Scoped A','Scoped B']){
  const folder=path.join(home,name);await mkdir(folder);await writeFile(path.join(folder,name+'.txt'),'Synthetic file');
  await act('workspace.add',{path:folder,name});
  const workspace=(await act('workspace.list',{query:name})).result.items.find(row=>row.path===folder);assert.ok(workspace);workspaces.push(workspace);
  chats.push((await act('session.create',{workspace:folder,title:name+' chat'})).result.sessionId);
 }
 await act('session.select',{id:chats[0]});await act('session.pin',{id:chats[0],pinned:true});await act('view.update',{patch:{navPinned:true}});
 await composer.fill('Keep this unsent scoped-navigation draft');
 await act('canvas.show',{kind:'html',title:'Scoped navigation canvas',content:'<input aria-label="Local canvas draft">'});
 await page.frameLocator('.a-canvas-html').getByRole('textbox').fill('preserved canvas input');
 await page.evaluate(()=>{window.scopedDocument=document.querySelector('#amp-one');window.scopedFrame=document.querySelector('.a-canvas-html')});
 await waitCoverage([]);
 const body=page.locator('.a-work-browser');
 const browse=(surface,tab)=>act('view.update',{patch:{workSurface:surface,workWorkspaceId:workspaces[1].id,...(tab?{workWorkspaceTab:tab}:{})}});
 // Hold a real Search result, then hold its replacement. An old result cannot
 // publish coverage for a newer screen while the current result is pending.
 let searchHeld=false,replacementHeld=false,searchReleased=false,replacementSettled=false;
 const searchGate=new Promise(resolve=>{releaseSearch=()=>{searchReleased=true;resolve()}});
 const replacementGate=new Promise(resolve=>{releaseReplacement=resolve});
 const holdRoute=async route=>{
  const resources=JSON.parse(new URL(route.request().url()).searchParams.get('navigationResources')||'null');
  if(resources?.includes('sidebarSearch')&&!searchHeld){const response=await route.fetch();searchHeld=true;await searchGate;return route.fulfill({response})}
  if(searchReleased&&!replacementHeld&&resources?.length===base.length){const response=await route.fetch();replacementHeld=true;await replacementGate;await route.fulfill({response});replacementSettled=true;return}
  return route.continue();
 };
 const hold=route=>holdRoute(route).catch(error=>{errors.push('Held route: '+String(error))});
 await page.route('**/api/shell?*',hold);
 await page.getByRole('button',{name:'Search chats',exact:true}).click();await expect.poll(()=>searchHeld).toBe(true);
 await expect(body.getByRole('status').filter({hasText:/Searching|Loading/})).toBeVisible();
 await expect(body.getByText('No chats found.',{exact:true})).toHaveCount(0);
 await browse('workspace','files');await expect.poll(async()=>(await state()).view.workWorkspaceTab).toBe('files');
 releaseSearch();await expect.poll(()=>replacementHeld).toBe(true);
 shape(await shell(),requested([]));assert.equal((await state()).view.workSurface,'workspace');
 releaseReplacement();await expect.poll(()=>replacementSettled).toBe(true);await page.unroute('**/api/shell?*',hold);await waitCoverage([]);
 await expect(body.getByRole('button',{name:'Scoped B.txt',exact:true})).toBeVisible();
 checks.push({staleSearchIgnored:true,loadingBeforeFirstPage:true});
 await body.getByRole('button',{name:'Chats',exact:true}).click();await waitCoverage(['sidebarWorkspace']);
 await expect(body.locator('[data-session-id]').getByRole('button',{name:'Scoped B chat',exact:true})).toBeVisible();
 await expect(body.locator('[data-session-id]').getByRole('button',{name:'Scoped A chat',exact:true})).toHaveCount(0);
 await body.getByRole('button',{name:'Details',exact:true}).click();await waitCoverage([]);await expect(body.getByText(workspaces[1].path,{exact:true})).toBeVisible();
 await body.getByRole('button',{name:'Files',exact:true}).click();await waitCoverage([]);await expect(body.getByRole('button',{name:'Scoped B.txt',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Search chats',exact:true}).click();await waitCoverage(['sidebarSearch']);
 for(const title of ['Scoped A chat','Scoped B chat'])await expect(body.locator('[data-session-id]').getByRole('button',{name:title,exact:true})).toBeVisible();
 await browse('workspaces');await waitCoverage(['workspaceExplorer']);await expect(body.getByRole('heading',{name:'Your workspaces',exact:true})).toBeVisible();
 await act('session.select',{id:chats[0]});await waitCoverage([]);await expect(composer).toHaveDraft('Keep this unsent scoped-navigation draft');
 assert.equal(await page.evaluate(()=>window.scopedDocument===document.querySelector('#amp-one')&&window.scopedFrame===document.querySelector('.a-canvas-html')),true);
 await expect(page.frameLocator('.a-canvas-html').getByRole('textbox')).toHaveValue('preserved canvas input');
 await expect(body.getByRole('heading'),'Hidden WorkBrowser must not render an expensive page').toHaveCount(0);
 assert.deepEqual((await (await page.request.get(url+'/fixture')).json()).sent,[]);assert.deepEqual(errors,[]);
 await Promise.all(responseTasks);assert.deepEqual(samples.filter(row=>!row.valid),[]);assert.ok(samples.some(row=>row.resources.includes('sidebarSearch'))&&samples.some(row=>row.resources.includes('sidebarWorkspace'))&&samples.some(row=>row.resources.includes('workspaceExplorer')));
 await page.screenshot({path:path.join(output,'preserved-chat.png')});
 checks.push({draftAndCanvasPreserved:true,hiddenBrowserUnmountedBody:true,noModelCalls:true});
 console.log(JSON.stringify({passed:true,checks,requestCount:samples.length}));
}catch(error){primaryError=error;console.error(error);if(page)await page.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});throw error}
finally{
 releaseSearch();releaseReplacement();
 await Promise.allSettled(responseTasks);
 const cleanupErrors=[];
 try{await browser?.close()}catch(error){cleanupErrors.push(String(error));console.error('Browser cleanup failed',error)}
 try{if(fixture.exitCode===null&&fixture.signalCode===null){fixture.kill('SIGTERM');await new Promise(resolve=>{const timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve()},5000);fixture.once('exit',()=>{clearTimeout(timer);resolve()})})}}catch(error){cleanupErrors.push(String(error));console.error('Fixture cleanup failed',error)}
 if(cleanupErrors.length&&!primaryError)process.exitCode=1;
 await writeFile(path.join(output,'scoped-navigation.json'),JSON.stringify({passed:!primaryError&&!cleanupErrors.length,checks,samples,pageErrors:errors,error:primaryError?.stack,cleanupErrors,fixtureHome,fixturePid:fixture.pid,fixtureStderr:stderr},null,2)).catch(error=>{console.error('Evidence write failed',error);if(!primaryError)process.exitCode=1});
}
