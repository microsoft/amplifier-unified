// Focused presentation acceptance. Run only against the isolated fixture.
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';

const evidence=process.env.ARTIFACT_EVIDENCE_DIR||'/artifacts/one-surface';
await mkdir(evidence,{recursive:true});
const fixture=spawn(process.env.FIXTURE_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/inline_artifacts_server.py',import.meta.url))],{stdio:'inherit'});
let browser,page;const errors=[];
const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
try{
 for(let i=0;i<100;i++){try{if((await fetch('http://127.0.0.1:8958/api/health')).ok)break}catch{}if(fixture.exitCode!==null)throw Error('Fixture exited before readiness');await new Promise(resolve=>setTimeout(resolve,100));}
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS?['--single-process']:[]});
 page=await browser.newPage({viewport:{width:1400,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto('http://127.0.0.1:8958/login');
 await page.getByLabel('Username').fill('inline-fixture');await page.getByLabel('Password').fill('fixture-password');
 await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.waitForSelector('#amp-one');await showFixtureConversation(page);
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Review one saved artifact.');await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.waitForFunction(()=>{const s=window.amplifier.getState(),chat=s.sessions.find(r=>r.id===s.selectedSessionId);return chat?.status==='idle'&&chat.messages?.some(r=>r.role==='assistant'&&r.text?.startsWith('## Ready'))});
 await action('canvas.show',{kind:'html',title:'One surface example',content:'<h1>Exact saved example</h1><label>Local note<input aria-label="Local note"></label><button onclick="this.textContent=\'Clicked\'">Try it</button>'});
 const identity=await page.evaluate(()=>window.amplifier.getState().canvas.id);
 const card=page.locator(`.a-document-output[data-artifact-id="${identity}"]`);
 await card.scrollIntoViewIfNeeded();
 await page.locator('.a-canvas-workspace').frameLocator('iframe').getByRole('heading',{name:'Exact saved example'}).waitFor();
 await expect(card.getByRole('button',{name:'Preview in chat',exact:true})).toHaveAttribute('aria-expanded','false');
 await page.waitForFunction(()=>window.amplifier.getState().canvasWorkspace.views.some(v=>v.viewId==='primary'&&Object.values(v.renderReports||{}).some(r=>r.status==='ready')));
 const mounts=await page.evaluate(id=>({
  primary:document.querySelectorAll('.a-canvas-workspace iframe').length,
  inline:document.querySelectorAll('.a-chat-artifacts iframe').length,
  inlineLeases:window.amplifier.getState().canvasWorkspace.views.filter(v=>v.viewId.startsWith('inline-')&&v.resourceId===id).length
 }),identity);
 await writeFile(`${evidence}/default-mounts.json`,JSON.stringify(mounts,null,2));
 await page.screenshot({path:`${evidence}/default-desktop.png`});
 assert.equal(mounts.inline,0,'One canvas.show must not mount a second full HTML renderer inline');
 assert.equal(mounts.inlineLeases,0,'A compact card must not allocate an invisible full preview');
 assert.equal(mounts.primary,1);
 // Observe actual DOM instances, including hidden frames, at every mutation.
 await page.evaluate(id=>{
  window.surfaceCounts=[];
  const observe=()=>window.surfaceCounts.push(document.querySelectorAll(`iframe[src*="/api/canvas/${id}/document"]`).length);
  window.surfaceObserver=new MutationObserver(observe);window.surfaceObserver.observe(document.body,{childList:true,subtree:true});observe();
 },identity);
 const original=await page.locator('.a-canvas-workspace iframe').elementHandle();
 await page.locator('.a-canvas-workspace').frameLocator('iframe').getByLabel('Local note').fill('Keep local input');
 await card.getByRole('button',{name:'Preview in chat',exact:true}).click();
 await card.getByRole('button',{name:'Keep current preview',exact:true}).click();
 assert.ok(await original.evaluate(node=>node.isConnected),'Cancelled move must retain the exact frame');
 await expect(page.locator('.a-canvas-workspace').frameLocator('iframe').getByLabel('Local note')).toHaveValue('Keep local input');
 await card.getByRole('button',{name:'Preview in chat',exact:true}).click();
 await card.getByRole('button',{name:'Reset preview and preview in chat',exact:true}).click();
 await card.frameLocator('iframe').getByRole('heading',{name:'Exact saved example'}).waitFor();
 await expect(page.locator('.a-canvas-workspace iframe')).toHaveCount(0);
 assert.equal(await original.evaluate(node=>node.isConnected),false);
 await expect(card.getByRole('button',{name:'Hide preview',exact:true})).toHaveAttribute('aria-expanded','true');
 await card.getByRole('button',{name:'Open One surface example in canvas',exact:true}).click();
 await card.getByRole('button',{name:'Reset preview and open in Canvas',exact:true}).click();
 await page.locator('.a-canvas-workspace').frameLocator('iframe').getByRole('heading',{name:'Exact saved example'}).waitFor();
 await expect(card.locator('iframe')).toHaveCount(0);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.selectedVersion),1);
 // Second cycle, hide, reopen and reload preserve choice, not iframe-local state.
 await card.getByRole('button',{name:'Preview in chat',exact:true}).click();
 await card.getByRole('button',{name:'Reset preview and preview in chat',exact:true}).click();
 await card.locator('iframe').waitFor();
 await card.getByRole('button',{name:'Hide preview',exact:true}).click();
 await card.getByRole('button',{name:'Reset preview and hide',exact:true}).click();
 await expect(page.locator(`iframe[src*="/api/canvas/${identity}/document"]`)).toHaveCount(0);
 const observedCounts=await page.evaluate(()=>{window.surfaceObserver.disconnect();return window.surfaceCounts});
 assert.ok(observedCounts.every(count=>count<=1),`Simultaneous executing frames: ${observedCounts}`);
 await writeFile(`${evidence}/transition-mounts.json`,JSON.stringify(observedCounts));
 await page.reload();await page.waitForSelector('#amp-one');await showFixtureConversation(page);
 await expect(card.getByRole('button',{name:'Preview in chat',exact:true})).toHaveAttribute('aria-expanded','false');
 await card.getByRole('button',{name:'Preview in chat',exact:true}).click();
 await card.frameLocator('iframe').getByRole('heading',{name:'Exact saved example'}).waitFor();
 await page.reload();await page.waitForSelector('#amp-one');await showFixtureConversation(page);
 await card.scrollIntoViewIfNeeded();await card.locator('iframe').waitFor();
 await expect(page.locator('.a-canvas-workspace iframe')).toHaveCount(0);
 // A separate client changes its own choice, not this page's.
 const other=await page.context().newPage();await other.goto('http://127.0.0.1:8958/');
 await other.waitForSelector('#amp-one');await showFixtureConversation(other);
 const sid=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await other.evaluate(id=>window.amplifier.dispatch('session.select',{id}),sid);
 await other.evaluate(id=>window.amplifier.dispatch('canvas.select',{id,version:1}),identity);
 await other.locator('.a-canvas-workspace iframe').waitFor();
 await expect(card.locator('iframe')).toHaveCount(1);await other.close();
 await card.getByRole('button',{name:'Open One surface example in canvas',exact:true}).click();
 await card.getByRole('button',{name:'Reset preview and open in Canvas',exact:true}).click();
 await page.locator('.a-canvas-workspace iframe').waitFor();
 // Short document previews must preserve the unrelated dirty primary and draft.
 const retained=await page.locator('.a-canvas-workspace iframe').elementHandle();
 const primary=await page.evaluate(()=>{const v=window.amplifier.getState().canvasWorkspace.views.find(v=>v.viewId==='primary');return Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(k=>[k,v[k]]))});
 await action('canvas.views.dirty',{...primary,dirty:true});
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Preserve the unsent parent draft');
 const counts=await page.evaluate(()=>{const s=window.amplifier.getState();return {messages:s.sessions.find(v=>v.id===s.selectedSessionId).messages.length,artifacts:s.canvasArtifacts.length}});
 let lastCard;
 for(const [kind,content] of [['markdown','# Short review\n\nA **saved** review.'],['code','const result = 42;'],['json','{"answer":42}']]){
  const saved=await action('canvas.show',{kind,title:`Short ${kind}`,content,presentation:'inline'});
  const document=page.locator(`.a-document-output[data-artifact-id="${saved.result.id}"]`);
  await document.scrollIntoViewIfNeeded();await document.locator('.a-canvas-viewer').waitFor();
  assert.ok(await retained.evaluate(node=>node.isConnected));
  await expect(document.getByRole('button',{name:'Hide preview',exact:true})).toHaveAttribute('aria-expanded','true');
  await document.getByRole('button',{name:'Hide preview',exact:true}).focus();await page.keyboard.press('Enter');
  await expect(document.locator('.a-canvas-viewer')).toHaveCount(0);
  await expect(document.getByRole('button',{name:'Preview in chat',exact:true})).toBeFocused();
  await page.keyboard.press('Enter');await document.locator('.a-canvas-viewer').waitFor();
  lastCard=document;
 }
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveText('Preserve the unsent parent draft');
 assert.equal(await page.evaluate(()=>{const s=window.amplifier.getState();return s.sessions.find(v=>v.id===s.selectedSessionId).messages.length}),counts.messages);
 await action('canvas.views.dirty',{...primary,dirty:false});
 await page.screenshot({path:`${evidence}/documents-desktop.png`});
 await page.emulateMedia({reducedMotion:'reduce'});await action('canvas.close');
 await page.setViewportSize({width:390,height:844});await lastCard.scrollIntoViewIfNeeded();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Narrow shell overflow');
 assert.ok(await lastCard.evaluate(node=>node.scrollWidth<=node.clientWidth),'Narrow card overflow');
 await page.screenshot({path:`${evidence}/documents-narrow.png`});
 // No preview transition above generated another saved definition.
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvasArtifacts.length),counts.artifacts+3);
 await writeFile(`${evidence}/geometry.json`,JSON.stringify(await lastCard.evaluate(node=>({card:node.getBoundingClientRect().toJSON(),viewport:{width:innerWidth,height:innerHeight},controls:[...node.querySelectorAll('button')].map(button=>({label:button.getAttribute('aria-label')||button.textContent,rect:button.getBoundingClientRect().toJSON()}))})),null,2));
 assert.deepEqual(errors,[]);
 console.log('PASS one-surface default, explicit transitions/reset refusal, hide/reopen/reload/second client, exact version, shared document controls, dirty unrelated Canvas/draft, keyboard and narrow containment');
}catch(error){if(page)await page.screenshot({path:`${evidence}/failure.png`});throw error}
finally{await browser?.close();fixture.kill();}
