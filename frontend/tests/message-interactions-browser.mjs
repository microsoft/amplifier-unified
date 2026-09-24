import {spawn} from 'node:child_process';
import {mkdirSync} from 'node:fs';
import {join} from 'node:path';
import {createServer} from 'node:net';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const reservation=createServer();await new Promise(r=>reservation.listen(0,'127.0.0.1',r));const port=reservation.address().port;await new Promise(r=>reservation.close(r));
const url=`http://127.0.0.1:${port}`;
const proof=process.env.AMPLIFIER_PROOF_DIR;if(proof)mkdirSync(proof,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/message_interactions_ui_server.py',import.meta.url)),String(port)],{stdio:'inherit'});
const browser=await chromium.launch({headless:true}),context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),page=await context.newPage(),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const state=()=>page.evaluate(()=>window.amplifier.getState());
try{
 for(let i=0;i<100;i++){try{if((await fetch(url+'/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
 await page.goto(url);await page.waitForSelector('#amp-one');
 const draft=page.getByRole('textbox',{name:'Message Amplifier'});await draft.fill('Preserve this draft');
 const original=page.locator('.a-message').filter({hasText:'Saved message 20:'});
 await original.getByRole('button',{name:'Reply to message',exact:true}).click();
 await page.getByLabel('Quoted reply',{exact:true}).waitFor();assert.equal(await draft.inputValue(),'Preserve this draft');
 await page.reload();await page.getByLabel('Quoted reply',{exact:true}).waitFor();assert.equal(await draft.inputValue(),'Preserve this draft');
 await page.getByRole('button',{name:'Remove quoted reply'}).click();await page.getByLabel('Quoted reply',{exact:true}).waitFor({state:'hidden'});assert.equal(await draft.inputValue(),'Preserve this draft');
 await original.getByRole('button',{name:'Reply to message',exact:true}).click();
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.waitForFunction(()=>{const s=window.amplifier.getState(),c=s.sessions.find(x=>x.id===s.selectedSessionId);return c?.status==='idle'&&c.messages.some(m=>m.text==='Preserve this draft'&&m.replyTo)});
 const sent=page.locator('.a-message').filter({hasText:'Preserve this draft'});await sent.locator('.a-reply-quote').waitFor();
 if(proof)await sent.screenshot({path:join(proof,'desktop-reply.png')});
 await page.getByLabel('Quoted reply',{exact:true}).waitFor({state:'hidden'});
 await sent.getByRole('button',{name:'React to message',exact:true}).click();await sent.getByRole('button',{name:'Heart',exact:true}).click();await sent.getByRole('button',{name:'Remove Heart reaction',exact:true}).waitFor();
 // A separate client receives the same reaction, but not another client's draft.
 const second=await context.newPage();await second.goto(url);await second.waitForSelector('#amp-one');
 const sent2=second.locator('.a-message').filter({hasText:'Preserve this draft'});await sent2.getByRole('button',{name:'Remove Heart reaction',exact:true}).waitFor();
 await sent2.getByRole('button',{name:'Remove Heart reaction',exact:true}).click();await sent.getByRole('button',{name:'Remove Heart reaction',exact:true}).waitFor({state:'hidden'});
 for(let i=0;i<4;i++){await draft.fill('Later message '+i);await page.getByRole('button',{name:'Send message',exact:true}).click();await page.waitForFunction(text=>{const s=window.amplifier.getState(),c=s.sessions.find(x=>x.id===s.selectedSessionId);return c?.status==='idle'&&c.messages.some(m=>m.text===text)},'Later message '+i)}
 // Reload discards locally expanded history. The quote opens its original by loading older presentation pages.
 await page.reload();await page.locator('.a-reply-quote').waitFor();await sent.locator('.a-quoted-source').click();
 await page.waitForFunction(()=>document.activeElement?.dataset?.messageId&&document.activeElement.textContent.includes('Saved message 20:'));
 // Quote an assistant message at phone width and verify touch picker and draft removal.
 await page.setViewportSize({width:390,height:844});const assistant=page.locator('.a-assistant').last();
 await assistant.getByRole('button',{name:'Reply to message',exact:true}).click();await page.getByLabel('Quoted reply',{exact:true}).waitFor();
 if(proof)await page.locator('.a-composer').screenshot({path:join(proof,'mobile-reply-preview.png')});
 await page.getByRole('button',{name:'Remove quoted reply',exact:true}).click();
 await assistant.getByRole('button',{name:'React to message',exact:true}).click();await assistant.getByRole('button',{name:'Celebrate',exact:true}).click();await assistant.getByRole('button',{name:'Remove Celebrate reaction',exact:true}).waitFor();
 if(proof)await assistant.locator('.a-message-actions').screenshot({path:join(proof,'mobile-reactions.png')});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 assert.deepEqual(errors,[]);console.log('Verified: quoted draft preservation/reload/removal/send, source navigation, assistant replies, persistent cross-client emoji add/remove, mobile affordances.');
}finally{await browser.close();fixture.kill();}
