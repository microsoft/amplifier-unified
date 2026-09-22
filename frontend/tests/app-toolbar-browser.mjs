// Real packaged UI and actions, isolated storage, no model or external feedback.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser;
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/amplifier-toolbar';
try{
 const url=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Toolbar fixture startup timed out')),15000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Toolbar fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}}});
 });
 await mkdir(out,{recursive:true});browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[],calls=[];page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')calls.push(request.postDataJSON())});
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const more=page.getByRole('button',{name:'More app options',exact:true}),menu=page.locator('#app-toolbar-menu');
 const toggle=page.locator('.a-top .a-canvas-toggle'),canvas=page.getByRole('complementary',{name:'Agent canvas'});
 await expect(page.getByRole('button',{name:'Open workspace canvas'})).toHaveCount(0);
 await expect(toggle).toHaveAccessibleName('Open canvas');await expect(toggle).toHaveText('');await expect(toggle).toHaveAttribute('aria-pressed','false');
 await expect(toggle).toBeDisabled();await action('session.create');
 const initialBox=await toggle.boundingBox();await toggle.click();await expect(canvas).toBeVisible();
 await expect(canvas.getByText('Nothing in Canvas yet',{exact:true})).toBeVisible();await expect(canvas.getByRole('searchbox')).toHaveCount(0);await expect(canvas.getByRole('button',{name:/Saved artifacts/})).toHaveCount(0);await expect(canvas.getByRole('button',{name:'Open a website',exact:true})).toBeVisible();await page.screenshot({path:out+'/canvas-empty.png'});
 await expect(toggle).toHaveAccessibleName('Close canvas');await expect(toggle).toHaveAttribute('aria-pressed','true');assert.deepEqual(await toggle.boundingBox(),initialBox);
 await page.getByRole('button',{name:'Close canvas panel',exact:true}).click();await expect(page.locator('#workspace-canvas')).toBeHidden();await expect(toggle).toHaveAttribute('aria-pressed','false');assert.deepEqual(await toggle.boundingBox(),initialBox);
 await action('session.create');const session=(await state()).selectedSessionId;
 await action('view.update',{patch:{draft:'Keep this unsent message',navPinned:true}});
 await action('canvas.show',{kind:'markdown',title:'Workspace notes',content:'# Workspace notes\n\nA saved artifact beside the conversation.'});
 const artifact=(await state()).canvas.id;
 await expect(toggle).toHaveAttribute('aria-pressed','true');await toggle.click();await expect(page.locator('#workspace-canvas')).toBeHidden();await toggle.click();await expect(canvas).toBeVisible();
 assert.equal((await state()).canvas.id,artifact);assert.equal((await state()).selectedSessionId,session);await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveValue('Keep this unsent message');
 await action('canvas.close');await expect(toggle).toHaveAttribute('aria-pressed','false');await action('canvas.reopen');await expect(toggle).toHaveAttribute('aria-pressed','true');
 await page.screenshot({path:out+'/canvas-open.png'});
 await more.focus();await page.keyboard.press('Enter');await expect(menu).toBeVisible();await expect(menu.getByRole('button',{name:'Customize appearance'})).toBeFocused();
 await page.keyboard.press('Tab');await expect(menu.getByRole('button',{name:'What the agent sees'})).toBeFocused();await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);await expect(more).toBeFocused();
 await more.click();await page.getByRole('textbox',{name:'Message Amplifier'}).click();await expect(menu).toHaveCount(0);await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeFocused();
 for(const [label,panel] of [['Customize appearance','appearance'],['What the agent sees','agent']]){
  await more.click();await menu.getByRole('button',{name:label,exact:true}).click();await expect(page.getByRole('dialog')).toBeVisible();await expect(menu).toHaveCount(0);
  await expect.poll(async()=>(await state()).view.panel).toBe(panel);await page.getByRole('button',{name:'Close panel',exact:true}).click();await expect(more).toBeFocused();
 }
 const feedback=page.getByRole('button',{name:'Send feedback',exact:true});
 await feedback.click();await expect.poll(async()=>(await state()).view.panel).toBe('feedback');
 await page.getByRole('button',{name:'Close panel',exact:true}).click();await expect(feedback).toBeFocused();
 await more.click();await expect(menu.getByRole('button',{name:/Activity|Send feedback/})).toHaveCount(0);await page.keyboard.press('Escape');
 await action('view.update',{patch:{toolbarMenuOpen:true}});await expect(menu).toBeVisible();await action('view.update',{patch:{panel:'agent'}});await expect(menu).toHaveCount(0);assert.equal((await state()).view.toolbarMenuOpen,false);await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await action('view.update',{patch:{layout:'work'}});await expect(toggle.locator('.lucide-panel-left')).toHaveCount(1);await action('view.update',{patch:{layout:'balanced'}});await expect(toggle.locator('.lucide-panel-right')).toHaveCount(1);
 for(const scheme of ['light','dark']){
  await action('view.update',{patch:{scheme}});
  for(const width of [1280,800,600,390,320]){
   await page.setViewportSize({width,height:900});await expect(toggle).toBeVisible();await expect(feedback).toBeVisible();
   const before=await toggle.boundingBox();const moreBox=await more.boundingBox(),feedbackBox=await feedback.boundingBox();assert.ok(before.x>moreBox.x&&before.x>feedbackBox.x,'Canvas is the rightmost header action');assert.ok(before.x>=0&&before.x+before.width<=width);
   await more.click();await expect(menu).toBeVisible();const rect=await menu.boundingBox();assert.ok(rect.x>=0&&rect.x+rect.width<=width&&rect.y+rect.height<=900);
   await page.screenshot({path:out+`/toolbar-${scheme}-${width}.png`});await page.keyboard.press('Escape');
   await toggle.click();await expect(toggle).toHaveAttribute('aria-pressed','false');assert.deepEqual(await toggle.boundingBox(),before);
   await toggle.click();await expect(toggle).toHaveAttribute('aria-pressed','true');assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  }
 }
 const touch=await browser.newPage({viewport:{width:320,height:844},hasTouch:true,isMobile:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});await touch.goto(url);await touch.locator('.a-canvas-toggle').waitFor();
 assert.ok(await touch.locator('.a-top-end>button,.a-toolbar-more>button').evaluateAll(buttons=>buttons.filter(button=>button.getClientRects().length).every(button=>{const r=button.getBoundingClientRect();return r.width>=44&&r.height>=44})));assert.ok(await touch.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await touch.getByRole('button',{name:'More app options'}).tap();await expect(touch.getByRole('dialog',{name:'More app options'})).toBeVisible();
 assert.equal(calls.some(call=>['conversation.send','runtime.start','feedback.submit'].includes(call.action)),false);assert.deepEqual((await (await page.request.get(url+'/fixture')).json()).sent,[]);assert.deepEqual(errors,[]);
 console.log('Toolbar passed: stable toggle geometry, saved artifact/draft preservation, shared actions, More destinations, keyboard/focus/outside dismissal, both canvas sides, light/dark 320–1280px, touch targets; no model or feedback submissions.');
}finally{await browser?.close();fixture.kill()}
