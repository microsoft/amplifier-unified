import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/chat_ui_server.py',import.meta.url))],{stdio:'inherit'});
for(let i=0;i<100;i++){try{if((await fetch('http://127.0.0.1:8958/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
const browser=await chromium.launch({headless:true}),context=await browser.newContext({viewport:{width:1280,height:900},permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),page=await context.newPage(),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const state=()=>page.evaluate(()=>window.amplifier.getState());
const idle=()=>page.waitForFunction(()=>{const s=window.amplifier.getState();return s.sessions.find(x=>x.id===s.selectedSessionId)?.status==='idle'});
async function send(text){await page.getByRole('textbox',{name:'Message Amplifier'}).fill(text);await page.getByRole('button',{name:'Send message',exact:true}).click();await page.waitForFunction(text=>{const s=window.amplifier.getState(),c=s.sessions.find(x=>x.id===s.selectedSessionId);return c?.status==='idle'&&c.messages.at(-1)?.role==='assistant'&&c.messages.filter(m=>m.role==='user').at(-1)?.text===text},text)}
try{
 await page.goto('http://127.0.0.1:8958/');await page.waitForSelector('#amp-one');
 await send('First question');await send('Second question');
 const original=(await state()).selectedSessionId;
 const markdown=(await state()).sessions.find(s=>s.id===original).messages.at(-1).text;
 await page.locator('.a-assistant').last().getByRole('button',{name:'Copy message as Markdown'}).click();await page.getByText('Copied Markdown',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),markdown);assert.match(markdown,/\*\*Markdown\*\*/);
 assert.equal(await page.getByRole('button',{name:/Fork conversation after turn/}).count(),2);
 await page.getByRole('button',{name:'Fork conversation after turn 1',exact:true}).click();
 await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId!==id,original);
 let s=await state(),fork=s.sessions.find(x=>x.id===s.selectedSessionId);assert.equal(fork.messages.length,2);assert.equal(fork.parentId,original);assert.equal(fork.messages[0].text,'First question');
 await page.getByRole('button',{name:'Open original chat',exact:true}).click();await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id,original);
 await page.locator('.a-user').first().getByRole('button',{name:'Edit message',exact:true}).click();await page.getByRole('textbox',{name:'Edit your message',exact:true}).fill('Edited first question');
 await page.getByRole('button',{name:'Save & regenerate',exact:true}).click();
 await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId!==id,original);await idle();
 s=await state();const edited=s.sessions.find(x=>x.id===s.selectedSessionId);assert.equal(edited.messages.length,2);assert.equal(edited.messages[0].text,'Edited first question');assert.equal(edited.editOrigin.sessionId,original);
 const retained=await page.evaluate(async id=>(await (await fetch('/api/state?sessionId='+encodeURIComponent(id))).json()).sessions.find(row=>row.id===id),original);assert.equal(retained.messages.length,4);assert.equal(await page.getByText('Second question',{exact:true}).count(),0);
 await page.getByRole('button',{name:'Edit message',exact:true}).click();await page.getByRole('textbox',{name:'Edit your message',exact:true}).fill('');assert.ok(await page.getByRole('button',{name:'Save & regenerate',exact:true}).isDisabled());await page.getByRole('button',{name:'Cancel',exact:true}).click();
 await page.screenshot({path:'/tmp/amplifier-message-actions.png'});
 await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'Edit message',exact:true}).click();await page.getByRole('textbox',{name:'Edit your message',exact:true}).waitFor();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight));await page.screenshot({path:'/tmp/amplifier-message-edit-mobile.png'});
 assert.deepEqual(errors,[]);console.log('Message controls verified: raw Markdown clipboard, per-turn fork boundaries, original preservation, edit/regenerate, empty edit, cancel, mobile layout.');
}finally{await browser.close();fixture.kill()}
