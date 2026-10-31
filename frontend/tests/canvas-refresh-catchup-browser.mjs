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
const check=async(name,run)=>{if(process.env.CANVAS_LOAD_FEEDBACK_ONLY&&!name.startsWith('Load feedback:'))return;const row={name,status:'running'};report.checks.push(row);try{row.observed=await run(row);row.status='passed'}catch(error){row.status='failed';row.error=error.stack||String(error);row.surface=await page.evaluate(()=>({inspector:document.querySelector('#canvas-options')?.outerHTML,freshness:document.querySelector('.a-canvas-freshness')?.outerHTML,view:window.amplifier.getState().canvasWorkspace,layout:window.amplifier.getState().view.canvasControlsExpanded}));await capture('failure-'+report.checks.length)}};
const html=version=>`<h1>Executable version ${version}</h1><label>Local unfinished input <input id="edit"></label><output id="executed"></output><script>document.getElementById('executed').textContent='executed-v${version}-'+crypto.randomUUID()</script>`;
try{
 const url=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture startup timeout')),20000);fixture.once('error',error=>{clearTimeout(timer);reject(error)});fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{stdout+=chunk;for(const line of stdout.split('\n'))try{const v=JSON.parse(line);if(v.url){clearTimeout(timer);resolve(v.url)}}catch{}})});
 browser=await chromium.launch({headless:true});report.browser=browser.version();
 const context=await browser.newContext({viewport:{width:1500,height:950},hasTouch:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
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
 for(const kind of ['html','babylon'])await check(kind+' retains adjacent versions, exact explicit Load, dirty refusal and narrow controls',async row=>{
  held=false;await page.setViewportSize({width:1500,height:950});
  const id=(await action('canvas.show',{kind,title:kind+' adoption',content:html(1)})).result.id;
  await frame().getByRole('heading',{name:'Executable version 1'}).waitFor();
  await frame().getByLabel('Local unfinished input').fill('Keep until explicit Load');
  const original=await sample();
  await miss(id,2,html(2));await refresh().click();
  await expect.poll(async()=>(await view()).latestVersion).toBe(2);await reconnect();
  assert.equal((await sample()).executed,original.executed);assert.equal((await sample()).input,original.input);
  await expect(panel.locator('.a-canvas-freshness')).toContainText('Viewing v1');
  await expect(panel.locator('.a-canvas-freshness')).toContainText('v2 available · preview retained');
  const load=version=>panel.getByRole('button',{name:`Load v${version} — resets preview and runs its code`,exact:true});
  await load(2).focus();await expect(load(2)).toBeFocused();
  let release,loadHeld=false;const gate=new Promise(resolve=>{release=resolve});
  const hold=async route=>{if(unwrap(route.request().postDataJSON()).action==='canvas.views.loadVersion'){loadHeld=true;await gate}await route.continue()};
  await page.route('**/api/actions',hold);
  try{
   await page.keyboard.press('Enter');await expect.poll(()=>loadHeld).toBe(true);
   assert.equal((await publish('canvas.versions.revise',{id,sessionId,expectedRevision:2,content:html(3)})).accepted,true);
   await expect.poll(async()=>(await view()).latestVersion).toBe(3);
   assert.equal((await sample()).executed,original.executed);assert.equal((await sample()).input,original.input);
   release();await frame().getByRole('heading',{name:'Executable version 2'}).waitFor();
   await expect.poll(async()=>(await view()).runningVersion).toBe(2);
  }finally{release();await page.unroute('**/api/actions',hold)}
  const loaded=await sample();assert.match(loaded.executed,/^executed-v2-/);assert.notEqual(loaded.executed,original.executed);
  assert.equal(loaded.input,'');assert.equal(loaded.view.selectedVersion,null);assert.equal(loaded.view.latestVersion,3);
  assert.deepEqual(loaded.view.versions.map(v=>v.version),[1,2,3]);
  await frame().getByLabel('Local unfinished input').fill('Dirty v2 input');
  await action('canvas.views.dirty',{...target(await view()),dirty:true});const dirty=await sample();
  await load(3).click();await expect(panel.getByRole('alert')).toContainText(/edit/i);
  assert.equal((await sample()).executed,dirty.executed);assert.equal((await sample()).input,dirty.input);
  assert.equal((await view()).dirty,true);
  await action('canvas.views.dirty',{...target(await view()),dirty:false});
  for(const width of [390,320]){
   await page.setViewportSize({width,height:780});
   const box=await load(3).evaluate(el=>{const r=el.getBoundingClientRect();return {width:r.width,height:r.height,inside:r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight,hit:el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}});
   assert.ok(box.width>=44&&box.height>=44&&box.inside&&box.hit,JSON.stringify(box));
   await capture(kind+'-retained-'+width);
  }
  await load(3).tap();await frame().getByRole('heading',{name:'Executable version 3'}).waitFor();
  const third=await sample();assert.match(third.executed,/^executed-v3-/);
  await refresh().tap();await expect(panel.locator('.a-canvas-freshness')).toContainText('Checked');
  assert.equal((await sample()).executed,third.executed,'Same-version Refresh must not run code twice');
  await page.setViewportSize({width:1500,height:950});
  await panel.getByRole('button',{name:'Choose artifact version',exact:true}).click();
  await page.getByRole('combobox',{name:'Artifact version',exact:true}).selectOption('1');
  await page.getByRole('button',{name:'Keep current preview',exact:true}).click();
  assert.equal((await sample()).executed,third.executed);
  await page.getByRole('combobox',{name:'Artifact version',exact:true}).selectOption('1');
  await page.getByRole('button',{name:'Load v1 — resets preview and runs its code',exact:true}).click();
  await frame().getByRole('heading',{name:'Executable version 1'}).waitFor();
  await page.getByRole('button',{name:'Close canvas details',exact:true}).click();
  const pinned=await sample();assert.equal(pinned.view.selectedVersion,1);
  await action('canvas.select',{id,version:1});
  assert.equal((await sample()).executed,pinned.executed,'Same-version reselection does not rerun executable content');
  assert.equal((await publish('canvas.versions.revise',{id,sessionId,expectedRevision:3,content:html(4)})).accepted,true);
  await expect.poll(async()=>(await view()).latestVersion).toBe(4);
  await refresh().click();assert.equal((await sample()).executed,pinned.executed);
  const source=(await action('canvas.versions.inspect',{id,version:2,includeSource:true})).result.source.content;
  assert.equal(source,html(2));
  const download=await page.request.get(url+`/api/canvas/${id}/download?version=1`);
  assert.equal(download.status(),200);assert.ok((await download.text()).includes('Executable version 1'));
  const fresh=await publish('canvas.select',{id});assert.equal(fresh.accepted,true);
  await page.reload();await showFixtureConversation(page,{navPinned:false});
  await frame().getByRole('heading',{name:'Executable version 1'}).waitFor();
  assert.equal((await view()).selectedVersion,1,'Cold page retains explicit historical selection, not JS memory');
  assert.notEqual((await sample()).executed,pinned.executed);
  row.observed={original,loaded,third,pinned,versions:(await view()).versions.map(v=>v.version)};
  await capture(kind+'-pinned-retained');return row.observed;
 });
 for(const mode of ['accepted-toolbar','accepted-details','unknown','refused'])await check('Load feedback: '+mode,async row=>{
  held=false;await page.setViewportSize({width:mode.startsWith('accepted')?390:1500,height:950});
  const id=(await action('canvas.show',{kind:'html',title:'Load feedback '+mode,content:html(1)})).result.id;
  await frame().getByRole('heading',{name:'Executable version 1'}).waitFor();
  await frame().getByLabel('Local unfinished input').fill('Keep unless explicit Load is accepted');
  assert.equal((await publish('canvas.versions.revise',{id,sessionId,expectedRevision:1,content:html(2)})).accepted,true);
  await expect.poll(async()=>(await view()).latestVersion).toBe(2);
  if(mode==='refused')await action('canvas.views.dirty',{...target(await view()),dirty:true});
  const before=await sample();let receipt,loads=0,reads=0;
  const intercept=async route=>{
   if(unwrap(route.request().postDataJSON()).action!=='canvas.views.loadVersion')return route.continue();
   loads++;const response=await route.fetch();receipt=await response.json();
   if(mode==='unknown')return route.abort('failed');
   await route.fulfill({response});
  };
  const failRead=async route=>{reads++;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Synthetic readback unavailable'})})};
  await page.route('**/api/actions',intercept);
  try{
   if(mode==='accepted-details'){
    await panel.getByRole('button',{name:'Choose artifact version',exact:true}).click();
    await page.getByRole('combobox',{name:'Artifact version',exact:true}).selectOption('2');
   }
   // Opening Details is itself an asynchronous state action. Drain it before
   // withholding frames so its recovery cannot race this Load's readback.
   await action('canvas.views.inspect');
   await page.route('**/api/state',failRead);
   held=true;
   const load=mode==='accepted-details'
    ?page.getByRole('group',{name:'Confirm saved preview version'}).getByRole('button',{name:'Load v2 — resets preview and runs its code',exact:true})
    :panel.getByRole('button',{name:'Load v2 — resets preview and runs its code',exact:true});
   await load.click();
   await expect.poll(()=>!!receipt).toBe(true);
   row.observed={before,receipt};
   if(mode==='refused'){
    assert.equal(receipt.accepted,false);
    await expect(panel.getByRole('alert')).toContainText(/edit/i);
    const after=await sample();assert.equal(after.executed,before.executed);assert.equal(after.input,before.input);
    assert.equal(after.view.generation,before.view.generation);
   }else{
    assert.equal(receipt.accepted,true);
    assert.equal(receipt.result.runningVersion,2);assert.ok(receipt.result.generation>before.view.generation);
    if(mode.startsWith('accepted')){
     assert.equal(receipt.state,undefined,'Exercise actual delta receipt followed by a failed read, not a fabricated receipt');
     await expect.poll(()=>reads).toBeGreaterThan(0);
     const feedback=mode==='accepted-details'?page.locator('#canvas-options').getByRole('alert'):panel.getByRole('alert');
     await expect(feedback).toContainText('Load v2 was accepted');
     if(mode==='accepted-details'){
      await expect(feedback).toContainText('Close Canvas details to use Refresh');
      await expect(page.getByRole('combobox',{name:'Artifact version',exact:true})).toBeDisabled();
      assert.equal(await feedback.evaluate(el=>!!el.closest('[inert],[aria-hidden="true"]')),false,'Details feedback is accessible in the active narrow sheet');
     }
    }else await expect(panel.getByRole('alert')).toContainText('completion is unconfirmed');
    await expect(panel.getByRole('alert')).not.toContainText('current preview is retained');
    await expect(panel.locator('.a-canvas-freshness')).not.toContainText('preview retained');
    const observedBeforeRefresh=await sample();
    assert.equal(observedBeforeRefresh.executed,before.executed);assert.equal(observedBeforeRefresh.input,before.input);
    if(mode!=='accepted-details'){
     await expect(load).toHaveAttribute('aria-disabled','true');await load.click({force:true});
    }else await expect(page.getByRole('group',{name:'Confirm saved preview version'})).toHaveCount(0);
    assert.equal(loads,1,'No automatic or repeated Load while readback is unresolved');
   }
   await capture('load-feedback-'+mode);
  }catch(error){
   row.failureSurface=await page.evaluate(()=>({inspector:document.querySelector('#canvas-options')?.outerHTML,freshness:document.querySelector('.a-canvas-freshness')?.outerHTML,view:window.amplifier.getState().canvasWorkspace,expanded:window.amplifier.getState().view.canvasControlsExpanded}));
   await capture('before-cleanup-'+mode);throw error;
  }finally{
   await page.unroute('**/api/actions',intercept);await page.unroute('**/api/state',failRead);held=false;
   await reconnect();await action('canvas.views.inspect');
   if(mode==='accepted-details')await page.getByRole('button',{name:'Close canvas details',exact:true}).click();
  }
  if(mode==='refused')await action('canvas.views.dirty',{...target(await view()),dirty:false});
  else{
   await expect.poll(async()=>(await view()).runningVersion).toBe(2);
   const after=await sample();assert.match(after.executed,/^executed-v2-/);
   assert.equal(after.view.resourceRevision,receipt.result.resourceRevision);
   assert.equal(after.view.generation,receipt.result.generation);assert.equal(after.input,'');
   row.observed.after=after;
  }
  assert.equal(loads,1);row.observed.loads=loads;row.observed.failedReads=reads;return row.observed;
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
