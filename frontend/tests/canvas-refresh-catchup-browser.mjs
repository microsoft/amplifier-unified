// Production host/build, real transport frames and synthetic documents only.
// Dropped incoming frames simulate a missed publication; no fake state/receipt.
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url));
const base=process.env.AMPLIFIER_TEST_ARTIFACTS||join(root,'output/browser-checks');
await mkdir(base,{recursive:true});const output=await mkdtemp(join(base,'canvas-catchup-'));
const report={status:'running',checks:[],errors:[],screenshots:[],actions:[],limits:['Synthetic runtime, no model or tool executions.','Lost incoming WebSocket frames are test-controlled; HTTP reads remain live.','Screenshots are rendered evidence, not an automatic visual-review claim.']};
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','pipe']});
let browser,page,stdout='',stderr='',fatal,held=false,dropped=0,connections=0,sockets=[];
fixture.stderr.on('data',chunk=>stderr+=chunk);
const capture=async name=>{await page.screenshot({path:join(output,name+'.png')});report.screenshots.push(name+'.png')};
const check=async(name,run)=>{const row={name,status:'running'};report.checks.push(row);try{row.observed=await run(row);row.status='passed'}catch(error){row.status='failed';row.error=error.stack||String(error);await capture('failure-'+report.checks.length)}};
const html=version=>`<h1>Executable version ${version}</h1><label>Local unfinished input <input id="edit"></label><output id="executed"></output><script>document.getElementById('executed').textContent='executed-v${version}-'+crypto.randomUUID()</script>`;
try{
 const url=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture startup timeout')),20000);fixture.once('error',error=>{clearTimeout(timer);reject(error)});fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{stdout+=chunk;for(const line of stdout.split('\n'))try{const v=JSON.parse(line);if(v.url){clearTimeout(timer);resolve(v.url)}}catch{}})});
 browser=await chromium.launch({headless:true});report.browser=browser.version();
 const context=await browser.newContext({viewport:{width:1500,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await context.routeWebSocket(/\/api\/events\/ws(?:\?|$)/,route=>{
  const server=route.connectToServer();connections++;sockets.push({route,server});
  server.onMessage(message=>{if(held){dropped++;return}route.send(message)});
 });
 page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',error=>report.errors.push(error.message));
 const unwrap=body=>body?.action==='shell.command'?(body.args.command||body.args):body;
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')report.actions.push(unwrap(request.postDataJSON()).action)});
 await page.goto(url);await showFixtureConversation(page,{navPinned:false});await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=async(name,args={})=>{const result=await page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);assert.equal(result.accepted,true,JSON.stringify(result));return result};
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const view=()=>state().then(s=>s.canvasWorkspace.views.find(v=>v.viewId==='primary'));
 const target=v=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(k=>[k,v[k]]));
 await action('session.create');const sessionId=(await state()).selectedSessionId;
 await action('view.update',{patch:{canvasWidth:800,canvasControlsExpanded:false,draft:'Catchup must retain the composer'}});
 const publisher='catchup-publisher';
 assert.equal((await page.request.post(url+'/api/clients/attach',{data:{clientId:publisher,kind:'web',protocolVersion:1}})).status(),200);
 const publish=async(action,args)=>{const response=await page.request.post(url+'/api/actions',{headers:{'X-Amplifier-Client':publisher},data:{action,args}});return response.json()};
 assert.equal((await publish('session.select',{id:sessionId})).accepted,true);
 const panel=page.locator('#workspace-canvas'),refresh=()=>panel.getByRole('button',{name:'Refresh document',exact:true});
 const frame=()=>page.frameLocator('[data-canvas-view="primary"] iframe');
 const sample=async()=>({view:await view(),executed:await frame().locator('#executed').textContent(),input:await frame().getByLabel('Local unfinished input').inputValue()});
 const miss=async(id,revision,content)=>{
  held=true;const previous=dropped;
  const receipt=await publish('canvas.versions.revise',{id,sessionId,expectedRevision:revision-1,content});
  assert.equal(receipt.accepted,true,JSON.stringify(receipt));
  await expect.poll(()=>dropped).toBeGreaterThan(previous);
  assert.equal((await view()).latestVersion,revision-1,'Client must actually miss the new saved revision');
  return receipt;
 };
 const reconnect=async()=>{
  const prior=connections;held=false;const old=sockets;sockets=[];
  for(const {route,server} of old){server.close();route.close()}
  await expect.poll(()=>connections,{timeout:10000}).toBeGreaterThan(prior);
 };
 await check('Markdown Latest catches first and adjacent missed updates via Refresh',async()=>{
  const id=(await action('canvas.show',{kind:'markdown',title:'Missed Markdown',content:'# Plain v1'})).result.id;
  for(const revision of [2,3]){
   await miss(id,revision,'# Plain v'+revision);
   await expect(panel.getByRole('heading',{name:'Plain v'+(revision-1),exact:true})).toBeVisible();
   await refresh().click();await expect(panel.getByRole('heading',{name:'Plain v'+revision,exact:true})).toBeVisible();
   assert.equal((await view()).versions.length,revision);assert.equal((await view()).selectedVersion,null);
   await reconnect();
  }
  await capture('markdown-caught-up');return {versions:(await view()).versions.map(v=>v.version)};
 });
 await check('Pinned HTML discovers missed v2 without executing it or replacing input',async row=>{
  held=false;const id=(await action('canvas.show',{kind:'html',title:'Pinned executable',content:html(1)})).result.id;
  await action('canvas.select',{id,version:1});await frame().getByRole('heading',{name:'Executable version 1'}).waitFor();
  await frame().getByLabel('Local unfinished input').fill('Keep pinned input');const before=await sample();
  await miss(id,2,html(2));await refresh().click();await expect.poll(async()=>(await view()).latestVersion).toBe(2);
  const after=await sample();row.observed={before,after,dropped};
  assert.equal(after.executed,before.executed);assert.equal(after.input,before.input);assert.equal(after.view.selectedVersion,1);
  assert.deepEqual(after.view.versions.map(v=>v.version),[1,2]);await reconnect();await capture('pinned-retained');return row.observed;
 });
 await check('Latest HTML Refresh discovers v2 without executing new code or losing input',async row=>{
  held=false;const id=(await action('canvas.show',{kind:'html',title:'Latest executable Refresh',content:html(1)})).result.id;
  await frame().getByRole('heading',{name:'Executable version 1'}).waitFor();
  await frame().getByLabel('Local unfinished input').fill('Keep latest input');const before=await sample();
  await miss(id,2,html(2));await refresh().click();await expect.poll(async()=>(await view()).latestVersion).toBe(2);
  await frame().locator('#executed').waitFor();const after=await sample();row.observed={before,after,dropped};
  // A generation/DOM token and actual field value discriminate discovery from remount.
  assert.equal(after.executed,before.executed,'Refresh must not execute the new saved HTML definition');
  assert.equal(after.input,before.input,'Refresh must not discard local HTML input');
  assert.equal(after.view.selectedVersion,null);assert.deepEqual(after.view.versions.map(v=>v.version),[1,2]);return row.observed;
 });
 await reconnect();
 await check('Latest HTML reconnect discovers v2 without executing new code or losing input',async row=>{
  const id=(await action('canvas.show',{kind:'html',title:'Latest executable reconnect',content:html(1)})).result.id;
  await frame().getByRole('heading',{name:'Executable version 1'}).waitFor();
  await frame().getByLabel('Local unfinished input').fill('Keep reconnect input');const before=await sample();
  await miss(id,2,html(2));await reconnect();await expect.poll(async()=>(await view()).latestVersion).toBe(2);
  await frame().locator('#executed').waitFor();const after=await sample();row.observed={before,after,connections};
  assert.equal(after.executed,before.executed,'Transport recovery must not execute a new HTML definition');
  assert.equal(after.input,before.input,'Transport recovery must not discard local HTML input');return row.observed;
 });
 await check('Declared dirty view blocks publication; same-definition Refresh retains it',async row=>{
  const id=(await action('canvas.show',{kind:'html',title:'Declared dirty executable',content:html(1)})).result.id;
  await frame().getByRole('heading',{name:'Executable version 1'}).waitFor();
  await frame().getByLabel('Local unfinished input').fill('Keep declared dirty input');
  await action('canvas.views.dirty',{...target(await view()),dirty:true});const before=await sample();
  const rejected=await publish('canvas.versions.revise',{id,sessionId,expectedRevision:1,content:html(2)});
  assert.equal(rejected.accepted,false);assert.match(rejected.error,/artifact edit/);
  await refresh().click();await expect(panel.locator('.a-canvas-freshness')).toContainText('Checked');
  const after=await sample();row.observed={before,after,rejected};
  assert.equal(after.executed,before.executed);assert.equal(after.input,before.input);assert.equal(after.view.dirty,true);
  assert.deepEqual(target(after.view),target(before.view));return row.observed;
 });
 assert.equal((await state()).view.draft,'Catchup must retain the composer');assert.deepEqual(report.errors,[]);
 report.droppedFrames=dropped;report.connections=connections;report.status=report.checks.every(c=>c.status==='passed')?'passed':'failed';
}catch(error){fatal=error;report.status='failed';report.error=error.stack||String(error);if(page)try{await capture('fatal')}catch{}}
finally{
 held=false;await browser?.close();if(fixture.exitCode===null){const exited=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await exited}finally{clearTimeout(timer)}}
 await writeFile(join(output,'report.json'),JSON.stringify(report,null,2));await writeFile(join(output,'fixture.stdout.log'),stdout);await writeFile(join(output,'fixture.stderr.log'),stderr);
 console.log(JSON.stringify({output,status:report.status,checks:report.checks.map(({name,status,error,observed})=>({name,status,error,observed:observed?{beforeToken:observed.before?.executed,afterToken:observed.after?.executed,beforeInput:observed.before?.input,afterInput:observed.after?.input}:null})),error:report.error}));
}
if(fatal||report.status!=='passed')process.exitCode=1;
