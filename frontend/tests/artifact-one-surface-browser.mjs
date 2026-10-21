// Focused presentation acceptance. Run only against the isolated fixture.
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
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
 const card=page.locator('.a-chat-artifacts').filter({hasText:'One surface example'}).first();
 await card.scrollIntoViewIfNeeded();
 await page.locator('.a-canvas-workspace iframe').waitFor();
 // Barrier: two animation frames after the visible card and full view settle.
 // The old default already dispatches its inline mount when the card intersects.
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
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
 assert.deepEqual(errors,[]);
 console.log('PASS one present action: primary renderer only, no inline renderer/lease');
}finally{await browser?.close();fixture.kill();}
