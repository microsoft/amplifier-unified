// Real DOM + real app actions, synthetic runtime; no provider or user history.
import {spawn} from 'node:child_process';
import {mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {createServer} from 'node:net';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {showFixtureConversation} from './browser-chat-pages.mjs';
const reservation=createServer();await new Promise(r=>reservation.listen(0,'127.0.0.1',r));const port=reservation.address().port;await new Promise(r=>reservation.close(r));
const url=`http://127.0.0.1:${port}`,proof=process.env.AMPLIFIER_PROOF_DIR;
if(proof)mkdirSync(proof,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/message_interactions_ui_server.py',import.meta.url)),String(port)],{stdio:'inherit'});
const browser=await chromium.launch({headless:true}),context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),page=await context.newPage(),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const documentValue=()=>page.evaluate(()=>window.amplifier.getState().view.draftDocument);
const commentary=doc=>doc.segments.filter(s=>s.type==='text').map(s=>s.text);
const references=doc=>doc.segments.filter(s=>s.type==='quote');
try{
 for(let i=0;i<100;i++){try{if((await fetch(url+'/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
 await page.goto(url);await page.waitForSelector('#amp-one');await showFixtureConversation(page);
 const editor=page.getByRole('textbox',{name:'Message Amplifier'}),quotes=editor.locator('[data-quote-instance]');
 await editor.fill('Before the references');
 await page.locator('.a-file-input').setInputFiles({name:'synthetic.txt',mimeType:'text/plain',buffer:Buffer.from('Attachment kept')});
 await page.getByText('synthetic.txt',{exact:true}).first().waitFor();
 const a=page.locator('.a-message').filter({hasText:'Quote fixture source A'}).first(),b=page.locator('.a-message').filter({hasText:'Quote fixture source B'}).first();
 const sourceA=await a.getAttribute('data-message-id'),sourceB=await b.getAttribute('data-message-id');
 async function passage(startMarker,endMarker){
  await a.getByRole('button',{name:'Choose passage',exact:true}).click();
  const area=page.getByRole('textbox',{name:'Message source for quotation'}),text=await area.inputValue(),start=text.indexOf(startMarker),end=endMarker?text.indexOf(endMarker,start):text.length;
  assert.ok(start>=0&&end>start);
  await area.evaluate((node,{start,end})=>{node.focus();node.setSelectionRange(start,end);node.dispatchEvent(new Event('select',{bubbles:true}));},{start,end});
  await page.getByRole('button',{name:'Add selected passage',exact:true}).click();
  await page.locator('.a-quote-picker').waitFor({state:'hidden'});
  return {text:text.slice(start,end),start:[...text.slice(0,start)].length,end:[...text.slice(0,end)].length};
 }
 const first=await passage('  café','\n\n* first');await expect(quotes).toHaveCount(1);
 // Actual keyboard text entry after a quote, not a patched saved document.
 await editor.click();await editor.press('ControlOrMeta+End');await editor.pressSequentially('Between the passages');
 const second=await passage('```py','\n\nlast passage');await expect(quotes).toHaveCount(2);
 await editor.click();await editor.press('ControlOrMeta+End');await editor.pressSequentially('Before the whole message');
 await b.scrollIntoViewIfNeeded();const beforeScroll=await page.locator('.a-messages').evaluate(n=>n.scrollTop);
 await b.getByRole('button',{name:'Reply to message',exact:true}).click();await expect(quotes).toHaveCount(3);
 assert.ok(Math.abs(await page.locator('.a-messages').evaluate(n=>n.scrollTop)-beforeScroll)<=1,'Collecting moved the reading position');
 assert.notEqual(await page.evaluate(()=>document.activeElement?.getAttribute('aria-label')),'Message Amplifier');
 await editor.click();await editor.press('ControlOrMeta+End');await editor.pressSequentially('After all references');
 let doc=await documentValue(),initial=references(doc),comments=commentary(doc);
 assert.equal(initial[0].quote.excerpt,first.text);assert.equal(initial[1].quote.excerpt,second.text);
 assert.deepEqual(initial[0].quote.spans,[{start:first.start,end:first.end}]);assert.deepEqual(initial[1].quote.spans,[{start:second.start,end:second.end}]);
 assert.deepEqual(initial.map(q=>q.quote.messageId),[sourceA,sourceA,sourceB]);
 assert.equal(new Set(initial.map(q=>q.instanceId)).size,3);
 assert.equal(await page.evaluate(()=>typeof window.untrusted),'undefined');
 await page.reload();await expect(quotes).toHaveCount(3);assert.deepEqual(await documentValue(),doc);
 // Same action remains focused when valid; disabled edge action gets an enabled fallback.
 await quotes.nth(1).getByRole('button',{name:'Move quote earlier',exact:true}).focus();await page.keyboard.press('Enter');
 assert.equal(await page.evaluate(()=>document.activeElement?.disabled),false);
 let moved=await documentValue();assert.deepEqual(references(moved).map(q=>q.instanceId),[initial[1].instanceId,initial[0].instanceId,initial[2].instanceId]);assert.deepEqual(commentary(moved),comments);
 await quotes.nth(1).getByRole('button',{name:'Remove quote',exact:true}).click();await expect(quotes).toHaveCount(2);
 doc=await documentValue();assert.deepEqual(commentary(doc),comments);assert.deepEqual(references(doc).map(q=>q.quote.excerpt),[second.text,initial[2].quote.excerpt]);
 await page.getByText('synthetic.txt',{exact:true}).first().waitFor();
 // Held source receipt: moving the caret/mode alone must not retarget the insertion.
 let release,started;const held=new Promise(resolve=>release=resolve),waiting=new Promise(resolve=>started=resolve);
 await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(body?.action==='message.reply'){const response=await route.fetch();started();await held;await route.fulfill({response});}else await route.continue();});
 await page.getByRole('combobox',{name:'Quote insertion'}).selectOption('append');
 const adding=b.getByRole('button',{name:'Reply to message',exact:true}).click();await waiting;
 await page.getByRole('combobox',{name:'Quote insertion'}).selectOption('caret');await editor.click();await editor.press('ControlOrMeta+Home');
 release();await adding;await expect(quotes).toHaveCount(3);await page.unroute('**/api/actions');
 const delayed=await documentValue();assert.equal(delayed.segments.filter(s=>s.type==='quote').at(-1).quote.messageId,sourceB);assert.deepEqual(commentary(delayed).filter(Boolean),comments.filter(Boolean));
 if(proof)await page.locator('.a-composer').screenshot({path:join(proof,'quotes-desktop.png')});
 await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 if(proof)await page.locator('.a-composer').screenshot({path:join(proof,'quotes-narrow.png')});
 const submitted=await documentValue();await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.waitForFunction(()=>document.querySelectorAll('.a-message [data-sent-quote]').length===3);
 await expect(quotes).toHaveCount(0);
 const state=await page.evaluate(()=>{const s=window.amplifier.getState();return s.sessions.find(c=>c.id===s.selectedSessionId);});
 const sent=state.messages.find(m=>m.replyTo?.version===2);assert.ok(sent);assert.deepEqual(sent.replyTo.segments,submitted.segments);
 assert.equal(await page.locator('.a-message [data-sent-quote] .a-composer-quote-text').first().textContent(),second.text);
 await editor.fill('Adjacent cycle');await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.waitForFunction(()=>{const s=window.amplifier.getState();return s.sessions.find(c=>c.id===s.selectedSessionId)?.messages.some(m=>m.text==='Adjacent cycle');});
 await page.reload();await expect(quotes).toHaveCount(0);
 assert.deepEqual(errors,[]);
 if(proof)writeFileSync(join(proof,'observed-document.json'),JSON.stringify({sourceA,sourceB,first,second,submitted,sent,initialScroll:beforeScroll},null,2));
 console.log('PASS exact Unicode/multiline passages + whole message, interleaved commentary, attachment, reload, keyboard reorder, removal, captured delayed target, narrow rendering, sent projection and adjacent cycle.');
}finally{await browser.close();fixture.kill();await new Promise(resolve=>fixture.exitCode!==null?resolve():fixture.once('exit',resolve));}
