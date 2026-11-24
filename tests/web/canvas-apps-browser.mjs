import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {appearanceSurface} from './fixtures/conversation-surface.mjs';
import {readAppearance,settledAppearance} from './settings-fixture.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||root+'output/canvas-proof';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'Canvas appearance app fixture'});
let browser,primaryError,releaseDirty;
try{
 const url=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);let output='';fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data.url)}}catch{}}})});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1500,height:1100},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[],surfaceRequests=[];page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{
  if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
  const raw=request.postDataJSON(),command=raw?.action==='shell.command'?(raw.args.command||raw.args):raw;
  if(command?.action==='canvas.apps.request')surfaceRequests.push(command);
 });
 await page.addInitScript(()=>{
  if(window!==top)return;
  window.fixtureSurfaceRequests=[];
  addEventListener('message',event=>{
   const frame=document.querySelector('[data-canvas-view="primary"] .a-canvas-app > iframe');
   if(event.source===frame?.contentWindow&&event.data?.type==='canvas-app'&&event.data.op==='request')
    window.fixtureSurfaceRequests.push(structuredClone(event.data));
  });
 });
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 await action('session.create',{});await action('view.update',{patch:{canvasControlsPinned:true,canvasWidth:650}});
 const draft='Keep this conversation draft through all theme changes';
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill(draft);await page.waitForFunction(draft=>window.amplifier.getState().view.draft===draft,draft);
 const initialAppearance=await settledAppearance(page);
 assert.equal(initialAppearance.activeScope,'shared');
 const source=appearanceSurface(initialAppearance.shared.revision),row=(await action('canvas.apps.create',source)).result,id=row.id;
 const inspect=()=>action('canvas.apps.inspect',{id}).then(r=>r.result);
 const cas=async()=>{const r=await inspect();return {id,sessionId:r.sessionId,expectedRevision:r.app.revision,expectedStateRevision:r.app.stateRevision}};
 const frame=()=>page.locator('[data-canvas-view="primary"]').frameLocator('iframe').frameLocator('iframe');
 await frame().getByRole('radio',{name:'Fern',exact:true}).check();
 await expect.poll(async()=>(await inspect()).app.state.selected).toBe('fern');
 await frame().getByRole('textbox').fill('Keep the green, soften the contrast');
 await expect.poll(async()=>(await inspect()).app.state.note).toBe('Keep the green, soften the contrast');
 // Same shared event path used by app_control; full caller-scope parity is covered in Python.
 await action('canvas.apps.event',{...await cas(),name:'choose',payload:{value:'ember'}});
 await expect(frame().getByRole('radio',{name:'Ember',exact:true})).toBeChecked();
 await action('canvas.apps.revise',{...await cas(),content:appearanceSurface(initialAppearance.shared.revision,true).content});
 await expect(frame().getByRole('heading',{name:'Your palette, refined.'})).toBeVisible();
 await expect(frame().getByRole('radio',{name:'Ember',exact:true})).toBeChecked();
 await expect(frame().getByRole('textbox')).toHaveValue('Keep the green, soften the contrast');
 assert.equal((await state()).canvasArtifacts.length,1);
 await page.locator('#workspace-canvas .a-open-documents').click();
 const documents=page.getByRole('dialog',{name:'Open documents',exact:true});
 await expect(documents.locator('li').filter({has:page.getByRole('button',{name:'Close tab Your custom themes',exact:true})})).toHaveCount(1);
 await documents.getByRole('button',{name:'Close open documents',exact:true}).click();
 const before=await settledAppearance(page),appearanceEvidence=[];
 const originalAccent=await page.locator('#amp-one').evaluate(el=>getComputedStyle(el).getPropertyValue('--a-accent').trim());
 await frame().getByRole('button',{name:'Preview',exact:true}).click();
 await page.getByRole('button',{name:'Review appearance change',exact:true}).click({timeout:10000}).catch(async e=>{console.log('host alerts',await page.getByRole('alert').allTextContents(),'frame status',await frame().locator('#status').textContent(),'requests',(await inspect()).app.requests);throw e});
 assert.equal(await page.locator('#amp-one').evaluate(el=>getComputedStyle(el).getPropertyValue('--a-accent').trim()),originalAccent);
 assert.deepEqual((await readAppearance(page)).shared,before.shared);assert.equal((await readAppearance(page)).preview,null);
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft(draft);
 const pendingPreview=(await inspect()).app.requests.find(r=>r.status==='pending');assert.ok(pendingPreview);
 await page.getByRole('region',{name:'Review requested appearance change',exact:true}).getByRole('button',{name:'Back',exact:true}).click();
 assert.equal((await inspect()).app.requests.find(r=>r.id===pendingPreview.id).status,'pending');
 assert.equal(await page.locator('#amp-one').evaluate(el=>getComputedStyle(el).getPropertyValue('--a-accent').trim()),originalAccent);
 await page.getByRole('button',{name:'Review appearance change',exact:true}).click();
 await page.getByRole('button',{name:'Approve appearance change',exact:true}).click();
 await expect.poll(async()=>(await inspect()).app.requests.find(r=>r.id===pendingPreview.id)?.status).toBe('accepted');
 const previewRequest=(await inspect()).app.requests.find(r=>r.id===pendingPreview.id);
 assert.equal(previewRequest.action,'appearance.preview');assert.ok(previewRequest.result.previewId);
 const preview=await settledAppearance(page);
 assert.equal(preview.preview.id,previewRequest.result.previewId);
 assert.deepEqual(preview.shared,before.shared);assert.deepEqual(preview.effective,before.effective);
 await expect.poll(()=>page.frames().find(f=>f.url().includes('/document')).evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue('--host-accent').trim())).toBe('#aa553c');
 assert.equal(await page.locator('#amp-one').evaluate(el=>getComputedStyle(el).getPropertyValue('--a-accent').trim()),'#aa553c');
 // Preview cancellation and undo of a saved preference are separate operations.
 await frame().getByRole('button',{name:'Cancel preview',exact:true}).click();
 await page.getByRole('button',{name:'Review appearance change',exact:true}).click();
 await page.getByRole('button',{name:'Approve appearance change',exact:true}).click();
 await expect.poll(async()=>(await readAppearance(page)).preview).toBe(null);
 const cancelled=await settledAppearance(page);assert.deepEqual(cancelled.shared,before.shared);
 assert.equal(await page.locator('#amp-one').evaluate(el=>getComputedStyle(el).getPropertyValue('--a-accent').trim()),originalAccent);
 await frame().getByRole('button',{name:'Apply accent',exact:true}).click();
 await page.getByRole('button',{name:'Review appearance change',exact:true}).click();
 const applyRequest=(await inspect()).app.requests.find(r=>r.status==='pending');
 assert.equal(applyRequest.action,'appearance.preferences.update');
 const requested=(await action('canvas.apps.inspect',{id,requestId:applyRequest.id})).result.requestInput;
 assert.deepEqual(requested,{scope:'shared',expectedRevision:before.shared.revision,patch:{accent:'#aa553c'}});
 await page.getByRole('button',{name:'Approve appearance change',exact:true}).click();
 await expect.poll(async()=>(await inspect()).app.requests.find(r=>r.id===applyRequest.id)?.status).toBe('accepted');
 const appliedRequest=(await inspect()).app.requests.find(r=>r.id===applyRequest.id);
 assert.equal(appliedRequest.result.status,'saved');assert.ok(appliedRequest.result.undoId);
 const applied=await settledAppearance(page);
 assert.equal(applied.shared.revision,before.shared.revision+1);
 assert.deepEqual(applied.shared.values,{...before.shared.values,accent:'#aa553c'});
 assert.equal(applied.preview,null);
 assert.equal(await page.locator('#amp-one').evaluate(el=>getComputedStyle(el).getPropertyValue('--a-accent').trim()),'#aa553c');
 appearanceEvidence.push({before,previewRequest,preview,cancelled,requested,appliedRequest,applied});
 // Forge an otherwise current, schema-valid envelope from the wrong Window.
 // A malformed retired payload could be rejected by the host after a bridge
 // leak, so require zero host requests as well as unchanged canonical state.
 const appBeforeForgery=(await inspect()).app,requestCount=surfaceRequests.length;
 assert.equal(requestCount,3);
 const captured=await page.evaluate(()=>window.fixtureSurfaceRequests.at(-1));
 assert.equal(captured.id,id);assert.equal(captured.op,'request');assert.equal(captured.args.name,'apply');
 assert.equal(typeof captured.channel,'string');assert.ok(captured.channel);
 const forged={...captured,requestId:'wrong-window-'+crypto.randomUUID(),
  revision:appBeforeForgery.revision,stateRevision:appBeforeForgery.stateRevision,
  args:{name:'apply',input:{scope:'shared',expectedRevision:applied.shared.revision,patch:{accent:'#123456'}}}};
 const forgedObserved=await page.evaluate(envelope=>new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>{removeEventListener('message',observe);reject(Error('Forged-message observation timed out'))},5000);
  function observe(event){
   if(event.data?.requestId!==envelope.requestId)return;
   clearTimeout(timer);removeEventListener('message',observe);
   const wrongSource=event.source===window;
   requestAnimationFrame(()=>requestAnimationFrame(()=>resolve({wrongSource,envelope:event.data})));
  }
  addEventListener('message',observe);postMessage(envelope,'*');
 }),forged);
 assert.equal(forgedObserved.wrongSource,true);assert.deepEqual(forgedObserved.envelope,forged);
 assert.deepEqual((await inspect()).app,appBeforeForgery);
 assert.equal(surfaceRequests.length,requestCount,'Wrong-window message must not issue a canvas.apps.request POST');
 assert.deepEqual((await readAppearance(page)).shared,applied.shared);
 appearanceEvidence.push({forgedObserved,hostRequestsBefore:requestCount,hostRequestsAfter:surfaceRequests.length});
 const inner=page.frames().find(f=>f.url().includes('/document'));
 assert.equal(await inner.evaluate(()=>{try{return parent.document.body.textContent}catch{return 'isolated'}}),'isolated');
 assert.equal(await inner.evaluate(async()=>{try{await fetch('/api/state');return 'accessible'}catch{return 'blocked'}}),'blocked');
 const effectsBeforeReload=(await inspect()).app.requests.map(r=>({id:r.id,status:r.status}));
 await page.reload();await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 assert.deepEqual((await inspect()).app.requests.map(r=>({id:r.id,status:r.status})),effectsBeforeReload);
 const reloadedAppearance=await settledAppearance(page);
 assert.deepEqual(reloadedAppearance.shared,applied.shared);assert.equal(reloadedAppearance.preview,null);
 await expect(frame().getByRole('radio',{name:'Ember',exact:true})).toBeChecked();
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft(draft);
 await frame().getByRole('button',{name:'Undo preference',exact:true}).click();
 await page.getByRole('button',{name:'Review appearance change',exact:true}).click();
 const undoRequest=(await inspect()).app.requests.find(r=>r.status==='pending');
 assert.equal(undoRequest.action,'appearance.preferences.undo');
 assert.deepEqual((await action('canvas.apps.inspect',{id,requestId:undoRequest.id})).result.requestInput,{undoId:appliedRequest.result.undoId});
 await page.getByRole('button',{name:'Approve appearance change',exact:true}).click();
 await expect.poll(async()=>(await inspect()).app.requests.find(r=>r.id===undoRequest.id)?.status).toBe('accepted');
 const undone=await settledAppearance(page);
 assert.deepEqual(undone.shared.values,before.shared.values);
 assert.equal(undone.shared.revision,applied.shared.revision+1);
 assert.equal(await page.locator('#amp-one').evaluate(el=>getComputedStyle(el).getPropertyValue('--a-accent').trim()),originalAccent);
 appearanceEvidence.push({reloadedAppearance,undoRequest,undoId:appliedRequest.result.undoId,undone});
 const effectsBeforeRestore=(await inspect()).app.requests.map(r=>({id:r.id,status:r.status}));
 await action('canvas.apps.restore',{...await cas(),version:1});
 assert.deepEqual((await inspect()).app.requests.map(r=>({id:r.id,status:r.status})),effectsBeforeRestore);
 await expect(frame().getByRole('heading',{name:'A different kind of atmosphere.'})).toBeVisible();
 await expect(frame().getByRole('radio',{name:'Ember',exact:true})).toBeChecked();
 assert.equal((await state()).canvasArtifacts.length,1);
 // Declining a request and revising its source cannot grant old approval authority.
 await frame().getByRole('button',{name:'Preview',exact:true}).click();
 await expect(page.getByRole('button',{name:'Decline',exact:true})).toBeVisible();
 const declined=(await inspect()).app.requests.find(r=>r.status==='pending');
 await page.getByRole('button',{name:'Decline',exact:true}).click();
 await expect.poll(async()=>(await inspect()).app.requests.find(r=>r.id===declined.id).status).toBe('rejected');
 await assert.rejects(()=>cas().then(c=>action('canvas.apps.resolve',{...c,requestId:declined.id,approve:true})),/resolved|superseded/);
 assert.deepEqual((await readAppearance(page)).shared,undone.shared);
 await frame().getByRole('button',{name:'Preview',exact:true}).click();
 await expect(page.getByRole('button',{name:'Review appearance change',exact:true})).toBeVisible();
 const superseded=(await inspect()).app.requests.find(r=>r.status==='pending');
 // Protect a form edit even when its dirty declaration races a remote revision.
 const pendingCas=await cas(),clientId=(await state()).client.id;
 const blocked=new Promise(resolve=>{releaseDirty=resolve});
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='canvas.views.dirty'&&body.args.dirty)await blocked;
  await route.continue().catch(error=>{if(!page.isClosed())throw error});
 });
 await frame().getByRole('textbox').fill('Unacknowledged local input');
 const response=await page.request.post(url+'/api/actions',{headers:{'X-Amplifier-Client':clientId},data:{action:'canvas.apps.revise',args:{...pendingCas,content:appearanceSurface(initialAppearance.shared.revision,true).content},id:'remote-race'}});
 assert.equal(response.status(),200);
 await expect(page.getByRole('button',{name:/Discard unfinished input and load revision/})).toBeVisible();
 await expect(frame().getByRole('textbox')).toHaveValue('Unacknowledged local input');
 await expect(frame().getByRole('heading',{name:'A different kind of atmosphere.'})).toBeVisible();
 releaseDirty();await page.unrouteAll({behavior:'wait'});
 await page.getByRole('button',{name:/Discard unfinished input and load revision/}).click();
 await expect(frame().getByRole('heading',{name:'Your palette, refined.'})).toBeVisible();
 await expect(frame().getByRole('textbox')).toHaveValue('Keep the green, soften the contrast');
 await expect.poll(async()=>(await inspect()).app.requests.find(r=>r.id===superseded.id).status).toBe('superseded');
 await expect(page.getByRole('button',{name:'Review appearance change',exact:true})).toHaveCount(0);
 await assert.rejects(()=>cas().then(c=>action('canvas.apps.resolve',{...c,requestId:superseded.id,approve:true})),/resolved|superseded/);
 assert.deepEqual((await readAppearance(page)).shared,undone.shared);await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft(draft);
 assert.deepEqual(errors,[]);
 await mkdir(output,{recursive:true});await page.screenshot({path:output+'/conversation-surface.png',fullPage:true});
 // A sandboxed document must not leak state by navigating its own frame.
 let navigationRequests=0;
 await page.route('https://canvas-network.invalid/**',async route=>{navigationRequests++;await route.fulfill({body:'unexpected navigation'})});
 const container=page.frames().find(f=>f.url().includes('/app-host'))||page.mainFrame();
 await container.evaluate(()=>{window.blockedSurfaceNavigation=0;addEventListener('securitypolicyviolation',()=>window.blockedSurfaceNavigation++)});
 const sandbox=page.frames().find(f=>f.url().includes('/document'));
 await sandbox.evaluate(()=>{location.href='https://canvas-network.invalid/probe'});
 await expect.poll(async()=>navigationRequests+await container.evaluate(()=>window.blockedSurfaceNavigation)).toBeGreaterThan(0);
 assert.equal(navigationRequests,0,'Sandbox self-navigation must be blocked before any network request');
 await action('canvas.apps.restore',{...await cas(),version:1});
 await expect(frame().getByRole('heading',{name:'A different kind of atmosphere.'})).toBeVisible();
 const sameOriginProbe=url+'/api/state?surface-probe=1';let hostNavigationRequests=0;
 await page.route(sameOriginProbe,async route=>{hostNavigationRequests++;await route.fulfill({body:'unexpected host navigation'})});
 const protectedHost=page.frames().find(f=>f.url().includes('/app-host'));
 await protectedHost.evaluate(()=>{window.blockedSurfaceNavigation=0;addEventListener('securitypolicyviolation',()=>window.blockedSurfaceNavigation++)});
 const protectedChild=page.frames().find(f=>f.url().includes('/document'));
 assert.equal(await protectedChild.evaluate(()=>{try{parent.location.href='/';return 'navigated'}catch{return 'blocked'}}),'blocked');
 await protectedChild.evaluate(url=>{location.href=url},sameOriginProbe);
 await expect.poll(()=>protectedHost.evaluate(()=>window.blockedSurfaceNavigation)).toBeGreaterThan(0);
 assert.equal(hostNavigationRequests,0,'Sandbox navigation cannot access other host endpoints');
 assert.deepEqual(errors,[]);
 await writeFile(output+'/conversation-surface.json',JSON.stringify({passed:true,appearanceEvidence,
  checks:['one open document','shared controls','input preservation','revision restore','preview and independent cancel','captured shared update and exact conditional undo across reload','refresh without request replay','composer draft','opaque parent and blocked external/same-origin iframe navigation'],
  limits:['Synthetic isolated surface; accent preference and named preview, not full-theme installation or power-loss durability.']},null,2));
 console.log('Conversation surface browser acceptance passed.');
}catch(error){primaryError=error;throw error}
finally{
 releaseDirty?.();
 await finishFixture({label:'Canvas appearance app fixture',close:[['Canvas app browser',()=>browser?.close()]],
  stop:stopFixture,originalError:primaryError});
}
