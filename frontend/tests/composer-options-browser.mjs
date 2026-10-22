import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import './composer-test-helpers.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],{stdio:['ignore','pipe','inherit']});
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp';await mkdir(output,{recursive:true});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({viewport:{width:390,height:844},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);await showFixtureConversation(page);const composer=page.getByRole('textbox',{name:'Message Amplifier'}),options=page.getByRole('button',{name:'Model and reasoning settings',exact:true}),menu=page.getByRole('dialog',{name:'Chat options',exact:true});
 const closeMenu=async()=>{
  const saved=page.waitForResponse(response=>{
   if(response.request().method()!=='POST'||new URL(response.url()).pathname!=='/api/actions')return false;
   const body=response.request().postDataJSON();
   return body?.action==='view.update'&&body.args?.patch?.composerModel?.open===false;
  });
  await menu.getByRole('button',{name:'Done',exact:true}).click();
  const response=await saved;assert.equal(response.ok(),true);assert.equal((await response.json()).accepted,true);
  await expect(menu).toHaveCount(0);
 };
 await expect(options).toBeVisible();await composer.fill('Use my selected model');
 // A saved custom ID can be valid without appearing in a provider's catalog.
 // Seed that prerequisite, then prove ordinary Options never silently replaces it.
 await page.evaluate(async()=>{const setup=window.amplifier.getState().view.newSessionDraft||{};await window.amplifier.dispatch('view.update',{patch:{newSessionDraft:{...setup,selection:{instance:'test-provider',model:'owned-custom-model',effort:'high'}}}})});
 await options.click();await expect(menu).toBeVisible();await expect(menu.locator('#chat-model option[value="chosen-model"]')).toBeAttached();await expect(menu.locator('#chat-model')).toHaveValue('owned-custom-model');await expect(menu.locator('#chat-model option[value="owned-custom-model"]')).toHaveText('owned-custom-model');
 await closeMenu();await page.reload();await options.click();await expect(menu.locator('#chat-model')).toHaveValue('owned-custom-model');await expect(composer).toHaveDraft('Use my selected model');
 const unsent=await(await page.request.get(url+'/fixture')).json();assert.deepEqual(unsent.started,[]);assert.deepEqual(unsent.sent,[]);
 await expect(menu.locator('#chat-provider')).toBeFocused();
 const bundle=menu.getByRole('combobox',{name:'Registered root bundles',exact:true});
 await bundle.selectOption('work');await menu.locator('#chat-model').selectOption('chosen-model');
 // Optimistic draft state is visible before its save is acknowledged. Reload
 // only after this exact selection has been accepted by the server.
 const effortSaved=page.waitForResponse(response=>{
  if(response.request().method()!=='POST'||new URL(response.url()).pathname!=='/api/actions')return false;
  const body=response.request().postDataJSON();
  return body?.action==='view.update'&&body.args?.patch?.newSessionDraft?.selection?.effort==='high';
 });
 await menu.locator('#chat-effort').press('End');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.newSessionDraft.selection.effort)).toBe('high');
 await expect(menu.locator('#chat-effort')).toHaveAttribute('aria-valuetext','high');
 await closeMenu();await expect(composer).toHaveDraft('Use my selected model');
 const saved=await effortSaved;assert.equal(saved.ok(),true);assert.equal((await saved.json()).accepted,true);
 await page.reload();await expect(options).toBeVisible();await options.click();await expect(menu.locator('#chat-model')).toHaveValue('chosen-model');await expect(bundle).toHaveValue('work');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.newSessionDraft.selection.effort)).toBe('high');
 await expect(menu.getByRole('button',{name:'Refresh models',exact:true})).toBeVisible();
 await closeMenu();
 // The same controls remain mounted across widths; only their popup position changes.
 await options.click();await page.setViewportSize({width:1280,height:900});await expect(menu).toBeVisible();await expect(options).toHaveCount(1);
 await expect(options).toContainText('chosen-model');await expect(menu.locator('#chat-model')).toHaveValue('chosen-model');
 await page.setViewportSize({width:320,height:700});await expect(menu).toBeVisible();await expect(menu.locator('#chat-model')).toHaveValue('chosen-model');
 const touchTargets=await menu.locator('button,select,input[type=range]').evaluateAll(nodes=>nodes.filter(el=>el.getClientRects().length).map(el=>({name:el.getAttribute('aria-label')||el.id||el.textContent,width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height})));
 for(const target of touchTargets)assert.ok(target.width>=44&&target.height>=44,`Phone control is too small: ${JSON.stringify(target)}`);
 await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);await expect(options).toBeFocused();
 await options.click();await page.screenshot({path:output+'/readiness107-options-mobile.png'});await closeMenu();
 await page.getByRole('button',{name:'Send message',exact:true}).click();await page.getByText('Synthetic first response',{exact:true}).waitFor();
 const data=await(await page.request.get(url+'/fixture')).json();assert.equal(data.sent.length,1);assert.equal(data.sent[0].selection.instance,'test-provider');assert.equal(data.sent[0].bundle,'work');assert.equal(data.sent[0].selection.model,'chosen-model');assert.equal(data.sent[0].selection.effort,'high');
 // A running conversation keeps readable output while configuration controls
 // refuse changes. This state is synthetic; no external provider is running.
 const sid=data.sent[0].sessionId;await page.request.post(url+'/fixture/activity',{data:{sessionId:sid,status:'working'}});
 await options.click();await expect(menu.locator('#chat-provider')).toBeDisabled();await expect(menu.locator('#chat-model')).toBeDisabled();await expect(bundle).toBeDisabled();
 await expect(menu.getByRole('button',{name:'Close chat options'})).toBeFocused();
 await expect(page.getByText('Synthetic first response',{exact:true})).toBeVisible();
 await closeMenu();await page.request.post(url+'/fixture/activity',{data:{sessionId:sid,status:'idle'}});
 const menuSaved=page.waitForResponse(response=>{
  if(response.request().method()!=='POST'||new URL(response.url()).pathname!=='/api/actions')return false;
  const body=response.request().postDataJSON();
  return body?.action==='view.update'&&body.args?.patch?.composerModel?.open===true&&body.args.patch.composerModel.sessionId===sid;
 });
 await options.click();await expect(menu).toBeVisible();
 const menuReceipt=await menuSaved;assert.equal(menuReceipt.ok(),true);assert.equal((await menuReceipt.json()).accepted,true);
 await page.reload();
 // Persisted presentation may reopen, but reload is not a local request to steal focus.
 await composer.waitFor();
 assert.equal(await page.evaluate(()=>!!document.activeElement?.closest('.a-composer-options-panel')),false);
 await expect(menu).toBeVisible();
 await expect.poll(()=>menu.evaluate(el=>{const r=el.getBoundingClientRect();return r.top>=0&&r.left>=0&&r.bottom<=innerHeight+1&&r.right<=innerWidth+1})).toBe(true);
 await page.screenshot({path:output+'/restored-options-mobile.png'});
 await closeMenu();
 await composer.fill('Keep this draft while managing connections');
 await options.click();await expect(menu).toBeVisible();
 await menu.getByRole('button',{name:'Manage connections'}).click();
 await expect(menu).toHaveCount(0);
 // Settings is a canvas surface, not the old generic panel. Use its own
 // accessible close control, which also unwinds compact Settings history.
 const settings=page.locator('[data-part="settings-experience"]');
 await expect(settings).toBeVisible();
 await expect(settings).toHaveAttribute('data-settings-page','ai-connections');
 await settings.getByRole('button',{name:'Close settings',exact:true}).click();
 await expect(settings).toBeHidden();
 await expect.poll(()=>page.evaluate(()=>{
  const state=window.amplifier.getState();
  return [state.view.panel??null,state.canvas?.kind==='settings'&&state.canvas.open===true,!!history.state?.amplifierSettings];
 })).toEqual([null,false,false]);
 await expect(composer).toBeVisible();
 await expect(composer).toHaveDraft('Keep this draft while managing connections');
 assert.equal((await(await page.request.get(url+'/fixture')).json()).sent.length,1,'Opening and closing settings must not send again');
 // Model controls, Chat actions and navigation More are different layers.
 // Exercise ordinary clicks/Escape under reduced motion without losing a draft.
 await page.emulateMedia({reducedMotion:'reduce'});
 const chat=page.getByRole('button',{name:'Chat',exact:true}),chatActions=page.getByRole('group',{name:'Chat',exact:true});
 await options.click();await expect(menu.locator('#chat-provider')).toBeFocused();
 await chat.click();await expect(menu).toHaveCount(0);await expect(chatActions).toBeVisible();
 assert.ok(await chatActions.evaluate(el=>el.contains(document.activeElement)));
 await page.keyboard.press('Escape');await expect(chatActions).toBeHidden();await expect(chat).toBeFocused();
 await options.click();await expect(menu.locator('#chat-provider')).toBeFocused();
 await expect(menu.locator('#chat-effort')).toHaveAttribute('aria-valuetext','high');
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 const drawer=page.getByRole('dialog',{name:'Workspaces and chats',exact:true});
 await expect(drawer).toBeVisible();await expect(menu).toHaveCount(0);
 assert.equal(await page.locator('.a-conversation').evaluate(el=>el.inert),true);
 assert.equal(await drawer.locator('.a-nav-rail').evaluate(el=>getComputedStyle(el).transitionDuration),'0s');
 const more=drawer.locator('.a-navigation-more').first();
 await more.focus();await more.click();await expect(page.locator('.a-navigation-flyout')).toBeVisible();
 await page.keyboard.press('Escape');await expect(page.locator('.a-navigation-flyout')).toBeHidden();await expect(drawer).toBeVisible();
 await page.keyboard.press('Escape');await expect(drawer).toBeHidden();
 await expect(composer).toHaveDraft('Keep this draft while managing connections');
 await options.click();await expect(menu.locator('#chat-provider')).toBeFocused();
 const optionsGeometry=await options.boundingBox();assert.ok(optionsGeometry.width>=44&&optionsGeometry.height>=44);
 await page.keyboard.press('Escape');await expect(options).toBeFocused();
 assert.equal((await(await page.request.get(url+'/fixture')).json()).sent.length,1);
 await writeFile(output+'/layering.json',JSON.stringify({width:320,reducedMotion:true,optionsGeometry,modelAndEffortRetained:true,chatAndMoreDismissedIndependently:true,draftRetained:true,noExtraSubmission:true},null,2));
 for(const scheme of ['light','dark']){await page.evaluate(scheme=>window.amplifier.dispatch('view.update',{patch:{scheme}}),scheme);await expect(options).toBeVisible();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:`${output}/readiness107-composer-${scheme}.png`});}
 assert.deepEqual(errors,[]);console.log('PASS: one compact model/options menu; real model/bundle/effort selection, preserved draft and reload, viewport resize, exact first submission, light/dark 320px.');
}finally{await browser?.close();fixture.kill()}
