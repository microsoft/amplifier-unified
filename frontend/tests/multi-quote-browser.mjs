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
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[process.env.AMPLIFIER_QUOTE_FIXTURE||fileURLToPath(new URL('../../tests/fixtures/message_interactions_ui_server.py',import.meta.url)),String(port)],{stdio:'inherit',cwd:process.env.AMPLIFIER_FIXTURE_CWD||process.cwd()});
const browser=await chromium.launch({headless:true}),context=await browser.newContext({viewport:{width:1280,height:900},hasTouch:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),page=await context.newPage(),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const documentValue=()=>page.evaluate(()=>window.amplifier.getState().view.draftDocument);
const commentary=doc=>doc.segments.filter(s=>s.type==='text').map(s=>s.text);
const references=doc=>doc.segments.filter(s=>s.type==='quote');
const observe=async()=>{const response=await context.request.get(url+'/fixture/message-source');assert.equal(response.status(),200);return response.json();};
const checkpoints=[];
const canonicalDraft=async()=>{
 const id=await page.evaluate(()=>window.amplifier.getState().client.id);
 const response=await context.request.get(url+'/fixture/quote-draft?'+new URLSearchParams({clientId:id}));
 assert.equal(response.status(),200);return response.json();
};
async function enteredCommentary(text){
 const observed=await page.getByRole('textbox',{name:'Message Amplifier'}).evaluate(editor=>{
  const state=window.amplifier.getState(),selection=window.getSelection();
  return {paragraphs:[...editor.children].filter(node=>!node.hasAttribute('data-quote-instance')).map(node=>node.textContent),
   document:state.view.draftDocument,draft:state.view.draft,active:document.activeElement?.outerHTML.slice(0,300),
   selectionParent:selection?.anchorNode?.parentElement?.outerHTML.slice(0,300),events:window.quoteTypingEvents||[]};
 });
 const canonical=await canonicalDraft();checkpoints.push({stage:'typed '+text,expectedText:text,observed,canonical});
 if(proof)writeFileSync(join(proof,'commentary-checkpoints.json'),JSON.stringify(checkpoints,null,2));
 assert.ok(observed.paragraphs.some(value=>value.includes(text)),`Typed commentary missing from DOM immediately: ${text}`);
 assert.ok(commentary(observed.document).some(value=>value.includes(text)),`Typed commentary missing from editor draft: ${text}`);
 await expect.poll(async()=>commentary((await canonicalDraft()).document)).toContain(text);
}
try{
 for(let i=0;i<100;i++){try{if((await fetch(url+'/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
 await page.goto(url);await page.waitForSelector('#amp-one');await showFixtureConversation(page);
 const editor=page.getByRole('textbox',{name:'Message Amplifier'}),quotes=editor.locator('[data-quote-instance]');
 await editor.fill('Before the references');
 await page.locator('.a-file-input').setInputFiles({name:'synthetic.txt',mimeType:'text/plain',buffer:Buffer.from('Attachment kept')});
 await page.getByRole('button',{name:'Remove synthetic.txt',exact:true}).waitFor();
 const a=page.locator('.a-message').filter({hasText:'Quote fixture source A'}).first(),b=page.locator('.a-message').filter({hasText:'Quote fixture source B'}).first();
 const sourceA=await a.getAttribute('data-message-id'),sourceB=await b.getAttribute('data-message-id');
 async function passage(startMarker,endMarker){
  await a.getByRole('button',{name:'Choose passage',exact:true}).click();
  const area=page.getByRole('textbox',{name:'Message source for quotation'}),text=await area.inputValue(),start=text.indexOf(startMarker),end=endMarker?text.indexOf(endMarker,start):text.length;
  assert.ok(start>=0&&end>start);
  await area.evaluate((node,{start,end})=>{node.focus();node.setSelectionRange(start,end);node.dispatchEvent(new Event('select',{bubbles:true}));},{start,end});
  // React's selection plugin observes native keyboard/mouse events rather
  // than a synthetic DOM `select` event alone.
  await area.press('Shift');
  await expect(page.getByRole('button',{name:'Add selected passage',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Add selected passage',exact:true}).click();
  await page.locator('.a-quote-picker').waitFor({state:'hidden'});
  return {text:text.slice(start,end),start:[...text.slice(0,start)].length,end:[...text.slice(0,end)].length};
 }
 const first=await passage('  café','\n\n* first');await expect(quotes).toHaveCount(1);
 await page.evaluate(()=>{
  window.quoteTypingEvents=[];
  for(const name of ['keydown','beforeinput','input'])document.addEventListener(name,event=>{
   if(window.quoteTypingEvents.length<30)window.quoteTypingEvents.push({type:name,key:event.key,inputType:event.inputType,
    target:event.target.outerHTML?.slice(0,240),active:document.activeElement?.outerHTML.slice(0,240)});
  },true);
 });
 // Actual keyboard text entry after a quote, not a patched saved document.
 await editor.click();await editor.press('ControlOrMeta+End');await editor.pressSequentially('Between the passages');
 await enteredCommentary('Between the passages');
 const second=await passage('```py','\n\nlast passage');await expect(quotes).toHaveCount(2);
 await editor.click();await editor.press('ControlOrMeta+End');await editor.pressSequentially('Before the whole message');
 await enteredCommentary('Before the whole message');
 const wholeButton=b.getByRole('button',{name:'Reply to message',exact:true});
 await expect(b.locator('.a-detail-text')).toContainText('END OF EXACT LONG SOURCE');
 await wholeButton.scrollIntoViewIfNeeded();const beforeScroll=await page.locator('.a-messages').evaluate(n=>n.scrollTop);
 await wholeButton.evaluate(button=>{
  window.quoteScroll=[];window.beforeQuoteFocus=document.activeElement;
  window.quoteGeometry=()=>{const pane=document.querySelector('.a-messages');return {top:pane.scrollTop,height:pane.clientHeight,scrollHeight:pane.scrollHeight,buttonTop:button.getBoundingClientRect().top,composerHeight:document.querySelector('.a-composer').clientHeight};};
  for(const event of ['pointerdown','mousedown','focus','click'])button.addEventListener(event,()=>window.quoteScroll.push({event,...window.quoteGeometry()}),{capture:true,once:true});
 });
 await wholeButton.click();await expect(quotes).toHaveCount(3);
 const afterScroll=await page.locator('.a-messages').evaluate(n=>n.scrollTop);
 const scrollEvents=await page.evaluate(()=>[...window.quoteScroll,{event:'after',...window.quoteGeometry()}]);
 if(proof)writeFileSync(join(proof,'collect-scroll.json'),JSON.stringify({beforeScroll,afterScroll,scrollEvents},null,2));
 // Lazy content above the viewport can change scrollTop while the existing
 // reader-anchor controller correctly keeps the source at the same pixel.
 // The visible anchor, not an absolute scroll counter, is the promised result.
 assert.ok(Math.abs(scrollEvents.at(-1).buttonTop-scrollEvents[0].buttonTop)<=1,`Collecting moved the visible source: ${JSON.stringify(scrollEvents)}`);
 assert.ok(await page.evaluate(()=>document.activeElement===window.beforeQuoteFocus),'Collecting must not move focus');
 await editor.click();await editor.press('ControlOrMeta+End');await editor.pressSequentially('After all references');
 await enteredCommentary('After all references');
 assert.equal((await observe()).sends.length,0,'Collecting must not send');
 await editor.dispatchEvent('keydown',{key:'Enter',code:'Enter',isComposing:true,keyCode:229});
 assert.equal((await observe()).sends.length,0,'IME Enter must not send');
 let doc=await documentValue(),initial=references(doc),comments=commentary(doc);
 assert.equal(initial[0].quote.excerpt,first.text);assert.equal(initial[1].quote.excerpt,second.text);
 assert.deepEqual(initial[0].quote.spans,[{start:first.start,end:first.end}]);assert.deepEqual(initial[1].quote.spans,[{start:second.start,end:second.end}]);
 assert.deepEqual(initial.map(q=>q.quote.messageId),[sourceA,sourceA,sourceB]);
 assert.equal(new Set(initial.map(q=>q.instanceId)).size,3);
 assert.ok(initial[2].quote.excerpt.length>4096,'Whole-message detail must include all text');
 assert.equal(await quotes.nth(2).locator('pre').textContent(),initial[2].quote.excerpt);
 const expand=quotes.nth(2).getByRole('button',{name:/Show all/});
 await expand.click();await expect(quotes.nth(2).getByRole('button',{name:'Collapse quote'})).toHaveAttribute('aria-expanded','true');
 await quotes.nth(2).getByRole('button',{name:'Collapse quote'}).click();
 assert.equal(await page.evaluate(()=>typeof window.untrusted),'undefined');
 await page.reload();await expect(quotes).toHaveCount(3);assert.deepEqual(await documentValue(),doc);
 // Same action remains focused when valid; disabled edge action gets an enabled fallback.
 await quotes.nth(1).getByRole('button',{name:'Move quote earlier',exact:true}).focus();await page.keyboard.press('Enter');
 assert.equal(await page.evaluate(()=>document.activeElement?.disabled),false);
 let moved=await documentValue();assert.deepEqual(references(moved).map(q=>q.instanceId),[initial[1].instanceId,initial[0].instanceId,initial[2].instanceId]);assert.deepEqual(commentary(moved),comments);
 await quotes.nth(1).getByRole('button',{name:'Remove quote',exact:true}).click();await expect(quotes).toHaveCount(2);
 doc=await documentValue();assert.deepEqual(commentary(doc),comments);assert.deepEqual(references(doc).map(q=>q.quote.excerpt),[second.text,initial[2].quote.excerpt]);
 await page.getByRole('button',{name:'Remove synthetic.txt',exact:true}).waitFor();
 // Held source receipt: moving the caret/mode alone must not retarget the insertion.
 let release,started;const held=new Promise(resolve=>release=resolve),waiting=new Promise(resolve=>started=resolve);
 await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(body?.action==='message.reply'){const response=await route.fetch();started();await held;await route.fulfill({response});}else await route.continue();});
 await page.getByRole('combobox',{name:'Quote insertion'}).selectOption('append');
 const adding=b.getByRole('button',{name:'Reply to message',exact:true}).click();await waiting;
 await page.getByRole('combobox',{name:'Quote insertion'}).selectOption('caret');await editor.click();await editor.press('ControlOrMeta+Home');
 release();await adding;await expect(quotes).toHaveCount(3);await page.unroute('**/api/actions');
 const delayed=await documentValue();assert.equal(delayed.segments.filter(s=>s.type==='quote').at(-1).quote.messageId,sourceB);assert.deepEqual(commentary(delayed).filter(Boolean),comments.filter(Boolean));
 // A changed draft rejects a late snapshot; a moved caret above did not.
 let changedRelease,changedStarted;
 const changedHeld=new Promise(r=>changedRelease=r),changedWaiting=new Promise(r=>changedStarted=r);
 await page.route('**/api/actions',async route=>{if(route.request().postDataJSON()?.action==='message.reply'){const response=await route.fetch();changedStarted();await changedHeld;await route.fulfill({response});}else await route.continue();});
 await b.getByRole('button',{name:'Reply to message',exact:true}).click();await changedWaiting;
 await editor.focus();await editor.press('ControlOrMeta+End');await editor.pressSequentially('Newer commentary');
 changedRelease();await page.getByText('The draft changed. Your text was kept; add the quote again.',{exact:true}).first().waitFor();
 await expect(quotes).toHaveCount(3);await page.unroute('**/api/actions');
 assert.ok(commentary(await documentValue()).join('').includes('Newer commentary'));
 const sourceLink=quotes.first().getByRole('button',{name:'Go to original quoted message'});
 await sourceLink.focus();await page.keyboard.press('Enter');
 await page.getByRole('button',{name:'Return to reading position'}).click();
 await expect(sourceLink).toBeFocused();
 if(proof)await page.locator('.a-composer').screenshot({path:join(proof,'quotes-desktop.png')});
 for(const width of [390,320]){
  await page.setViewportSize({width,height:844});
  for(const colorScheme of ['light','dark']){
   await page.emulateMedia({colorScheme});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   if(proof)await page.locator('.a-composer').screenshot({path:join(proof,`quotes-${width}-${colorScheme}.png`)});
  }
 }
 await quotes.nth(1).getByRole('button',{name:/Show all/}).tap();
 await expect(quotes.nth(1).getByRole('button',{name:'Collapse quote'})).toBeVisible();
 const submitted=await documentValue();await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.waitForFunction(()=>document.querySelectorAll('.a-message [data-sent-quote]').length===3);
 await expect(quotes).toHaveCount(0);
 await expect.poll(async()=>(await observe()).sends.length).toBe(1);
 const observed=await observe(),sent=observed.sessions.flatMap(s=>s.messages).find(m=>m.replyTo?.version===2);
 assert.ok(sent);assert.deepEqual(sent.replyTo.segments,submitted.segments);
 assert.equal(sent.attachments[0].name,'synthetic.txt');
 const sentArticle=page.locator('[data-message-id="'+sent.id+'"]');
 await expect(sentArticle.getByRole('button',{name:'Edit message',exact:true})).toBeDisabled();
 await page.evaluate(()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.fixtureCopied=text;}}});});
 await sentArticle.getByRole('button',{name:'Copy message as Markdown'}).click();
 await expect.poll(()=>page.evaluate(()=>window.fixtureCopied)).toBe(sent.text);
 assert.equal(await page.locator('.a-message [data-sent-quote] .a-composer-quote-text').first().textContent(),second.text);
 await editor.fill('Adjacent cycle');await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.waitForFunction(()=>{const s=window.amplifier.getState();return s.sessions.find(c=>c.id===s.selectedSessionId)?.messages.some(m=>m.text==='Adjacent cycle');});
 await page.reload();await expect(quotes).toHaveCount(0);
 // Native transcript selection, not just the source picker.
 await page.setViewportSize({width:1280,height:900});
 await a.locator('.a-detail-text').evaluate(node=>{
  const walker=document.createTreeWalker(node,NodeFilter.SHOW_TEXT);let text;
  while((text=walker.nextNode())){const start=text.textContent.indexOf('second line');if(start<0)continue;
   const range=document.createRange();range.setStart(text,start);range.setEnd(text,start+'second line'.length);
   const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);document.dispatchEvent(new Event('selectionchange'));return;
  }throw Error('Native selection fixture text is missing');
 });
 await a.getByRole('button',{name:'Quote selection',exact:true}).click();await expect(quotes).toHaveCount(1);
 assert.equal(references(await documentValue())[0].quote.excerpt,'second line');
 await quotes.first().getByRole('button',{name:'Remove quote',exact:true}).click();await expect(quotes).toHaveCount(0);
 assert.equal((await observe()).sends.length,2,'Quote/remove must not execute another turn');
 assert.deepEqual(errors,[]);
 if(proof)writeFileSync(join(proof,'observed-document.json'),JSON.stringify({sourceA,sourceB,first,second,submitted,sent,initialScroll:beforeScroll},null,2));
 console.log('PASS exact Unicode/multiline passages + whole message, interleaved commentary, attachment, reload, keyboard reorder, removal, captured delayed target, narrow rendering, sent projection and adjacent cycle.');
}catch(error){
 if(proof){writeFileSync(join(proof,'failure.json'),JSON.stringify({message:error.message,errors},null,2));await page.screenshot({path:join(proof,'failure.png')}).catch(()=>{});}
 throw error;
}finally{await browser.close();fixture.kill();await new Promise(resolve=>fixture.exitCode!==null?resolve():fixture.once('exit',resolve));}
