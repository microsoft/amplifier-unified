import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const fixture=spawn(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/timeline_ui_server.py',import.meta.url))],{stdio:'inherit'});
for(let i=0;i<100;i++){try{if((await fetch('http://127.0.0.1:8958/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const order=()=>page.locator('.a-messages').evaluate(el=>[...el.children].map(n=>n.dataset.messageId||n.dataset.turnId).filter(Boolean));
const expected=['voice-user','voice:first','voice-ack','voice:second','voice:third','voice-result'];
try{
 await page.goto('http://127.0.0.1:8958/');await page.locator('[data-turn-id="voice:third"]').waitFor();assert.deepEqual(await order(),expected);
 const second=page.locator('[data-turn-id="voice:second"]');assert.match(await second.innerText(),/Working/);
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('A later question');await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.waitForFunction(()=>{const s=window.amplifier.getState(),c=s.sessions.find(x=>x.id===s.selectedSessionId);return c?.messages.length===5&&c.status==='idle'});
 assert.deepEqual((await order()).slice(0,6),expected);assert.match(await second.innerText(),/Worked for/);
 await second.getByRole('button').first().click();await second.getByText('Tool 1',{exact:true}).waitFor();assert.deepEqual((await order()).slice(0,6),expected);
 await page.reload();await page.locator('[data-turn-id="voice:third"]').waitFor();assert.deepEqual((await order()).slice(0,6),expected);
 assert.deepEqual(errors,[]);console.log('Voice work controls remain between their original messages during completion, later turns, expansion and reload; multiple controls can share one anchor.');
}finally{await browser.close();fixture.kill()}
