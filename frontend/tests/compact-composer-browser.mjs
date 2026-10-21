// Actual bundled application; synthetic host/runtime. No personal data or provider.
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {clearComposer} from './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/compact-composer';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],{stdio:['ignore','pipe','inherit']});
let browser;
const evidence={geometry:[],negativeControl:null,limits:['Synthetic runtime; no live provider or physical keyboard/audio','Multi-quote implementation is independently owned; Markdown quotes exercise the existing editor, not immutable-node provenance']};
try{
 const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 await mkdir(output,{recursive:true});browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1000,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[],sends=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/actions'&&r.postDataJSON()?.action==='conversation.send')sends.push(r.postDataJSON());});
 await page.goto(url);await showFixtureConversation(page,{navPinned:false});
 const input=page.getByRole('textbox',{name:'Message Amplifier'}),surface=page.locator('[data-part="composer-surface"]'),primary=page.locator('[data-part="composer-primary-action"]'),options=page.getByRole('button',{name:'Model and reasoning settings',exact:true});
 await input.waitFor();
 const presentation=async patch=>page.evaluate(async patch=>{const shell=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:shell.revision,composition:{...shell.effectiveComposition,presentation:{...shell.effectiveComposition.presentation,...patch}}});assertAccepted(prepared);const applied=await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:shell.revision,changeId:prepared.result.id});assertAccepted(applied);function assertAccepted(r){if(!r.accepted)throw Error(r.error||'Presentation refused');}},patch);
 const measure=()=>surface.evaluate(el=>{const rect=node=>{const {x,y,width,height}=node.getBoundingClientRect();return {x,y,width,height}};const input=el.querySelector('[role=textbox]'),style=getComputedStyle(el),form=el.closest('form');return {surface:rect(el),input:rect(input),options:rect(form.querySelector('.a-model-trigger')),form:rect(form),border:style.borderTopWidth,borderStyle:style.borderTopStyle,background:style.backgroundColor,overflow:el.scrollWidth>el.clientWidth+1,pageOverflow:document.documentElement.scrollWidth>innerWidth+1,buttons:[...el.querySelectorAll('button')].filter(b=>b.getClientRects().length).map(rect)};});
 const checkIdle=m=>{assert.ok(m.surface.height<=72,`Idle field is not compact: ${m.surface.height}`);assert.ok(m.surface.height>=44);assert.ok(m.options.y>=m.surface.y+m.surface.height-1,'Model control belongs below the writing field');assert.equal(m.border,'1px');assert.equal(m.borderStyle,'solid');assert.equal(m.overflow,false);assert.equal(m.pageOverflow,false);for(const box of m.buttons){assert.ok(box.width>=44&&box.height>=44,'Frequent actions retain touch hit areas');assert.ok(box.x>=m.surface.x&&box.x+box.width<=m.surface.x+m.surface.width+1);}};
 for(const width of [900,520,390,320]){
  await page.setViewportSize({width,height:900});await clearComposer(input);
  for(const scheme of ['light','dark'])for(const interfaceDetail of ['minimal','standard','detailed']){
   await presentation({scheme,interfaceDetail,executionDetail:interfaceDetail});await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);await expect(surface).toBeVisible();
   const m=await measure();checkIdle(m);evidence.geometry.push({width,scheme,interfaceDetail,...m});
  }
  await input.fill('Ready');await expect(primary).toHaveAccessibleName('Send message');await expect(input).toHaveDraft('Ready');await clearComposer(input);
  await page.screenshot({path:`${output}/compact-empty-${width}.png`});
 }
 // A deliberate return to the old tall framed input must fail the SAME user-boundary assertion.
 const previous=await surface.getAttribute('style');await surface.evaluate(el=>el.style.minHeight='160px');
 const broken=await measure();assert.throws(()=>checkIdle(broken));evidence.negativeControl={rejected:true,observed:broken};
 await surface.evaluate((el,value)=>value===null?el.removeAttribute('style'):el.setAttribute('style',value),previous);checkIdle(await measure());
 await input.fill('Long draft. '.repeat(150));await expect(surface).toHaveAttribute('data-expanded','true');await expect(primary).toBeInViewport();
 const long=await measure();assert.ok(long.input.width>=long.surface.width-40,'Long draft uses the full writing width');assert.ok(long.input.height<=181);assert.equal(long.overflow,false);
 await input.evaluate(el=>{el.scrollTop=el.scrollHeight});await page.screenshot({path:output+'/compact-long-320.png'});
 const retained=await input.innerText();await options.click();const menu=page.getByRole('dialog',{name:'Chat options',exact:true});await expect(menu).toBeVisible();await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);await expect(options).toBeFocused();assert.equal(await input.innerText(),retained);
 await clearComposer(input);await input.fill('Do not send during composition');
 await input.evaluate(el=>{el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,keyCode:229,bubbles:true,cancelable:true}));el.dispatchEvent(new InputEvent('beforeinput',{inputType:'insertParagraph',isComposing:true,bubbles:true,cancelable:true}));});assert.equal(sends.length,0);await expect(input).toHaveDraft('Do not send during composition');
 await input.press('Shift+Enter');await page.keyboard.type('Second line');assert.equal(sends.length,0);
 await expect(input).toHaveDraft('Do not send during composition\n\nSecond line');
 await page.getByLabel('Attach files',{exact:true}).setInputFiles([{name:'notes.txt',mimeType:'text/plain',buffer:Buffer.from('Synthetic notes')},{name:'budget.csv',mimeType:'text/csv',buffer:Buffer.from('item,cost\nroom,20')}]);await page.getByRole('button',{name:'Remove notes.txt'}).waitFor();await expect(surface).toHaveAttribute('data-expanded','true');assert.equal((await measure()).overflow,false);
 const observed=await page.evaluate(()=>window.amplifier.getState().renderedView);
 assert.ok(!JSON.stringify(observed).includes('Do not send during composition'));
 assert.ok(!JSON.stringify(observed).includes('notes.txt'),'Ordinary rendered readback excludes attachment draft names');
 assert.ok(!JSON.stringify(observed).includes('data-expanded'),'Layout-only attributes are not projected');
 await page.getByRole('button',{name:'Remove budget.csv'}).click();await expect(input).toHaveDraft('Do not send during composition\n\nSecond line');
 await presentation({scheme:'light',interfaceDetail:'minimal'});await expect(page.getByRole('button',{name:'Remove notes.txt'})).toBeVisible();await page.getByRole('button',{name:'Remove notes.txt'}).click();
 await clearComposer(input);await input.fill('Before\n\n> First reference\n\nBetween\n\n> Second reference\n\nAfter');
 await page.reload();await input.waitFor();await expect(input).toHaveDraft('Before\n\n> First reference\n\nBetween\n\n> Second reference\n\nAfter');await expect(primary).toBeInViewport();
 // CSS zoom is a layout stress check, not a claim about physical browser zoom shortcuts.
 await page.setViewportSize({width:800,height:1000});await page.locator('#amp-one').evaluate(el=>el.style.zoom='2');await expect(primary).toBeInViewport();assert.equal((await measure()).pageOverflow,false);await page.screenshot({path:output+'/compact-zoom2.png'});await page.locator('#amp-one').evaluate(el=>el.style.zoom='');
 await clearComposer(input);await input.fill('A retained first message');await primary.click();await page.getByText('Synthetic first response',{exact:true}).waitFor();assert.equal(sends.length,1);await expect(input).toHaveDraft('');
 assert.deepEqual(errors,[]);await writeFile(output+'/compact-composer.json',JSON.stringify(evidence,null,2));console.log('PASS compact composer: rendered geometry, modes, long text, attachments, quote-shaped Markdown, focus, composition, reload, synthetic send; limits retained.');
}finally{
 await browser?.close();
 if(fixture.exitCode===null&&fixture.signalCode===null){
  const exited=once(fixture,'exit');let timer;
  fixture.kill('SIGTERM');
  try{await Promise.race([exited,new Promise((_,reject)=>{timer=setTimeout(()=>{fixture.kill('SIGKILL');reject(Error('Fixture did not finish graceful shutdown within 5 seconds'));},5000)})]);}
  finally{clearTimeout(timer)}
 }
 assert.equal(fixture.exitCode,0,'Fixture must finish normal cleanup');
 assert.equal(fixture.signalCode,null,'Fixture must not require forced cleanup');
}
