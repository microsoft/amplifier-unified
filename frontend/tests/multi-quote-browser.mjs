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
const checkpoints=[],compactGeometry=[];
const cardObservations=[];
async function observeCards(stage){
 const rendered=await page.getByRole('textbox',{name:'Message Amplifier'}).evaluate(editor=>{
  const bounds=node=>{const r=node.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height};};
  const viewport=editor.getBoundingClientRect();
  const inspect=node=>{
   const rect=node.getBoundingClientRect(),style=getComputedStyle(node);
   return {tag:node.tagName,className:node.className,bounds:bounds(node),display:style.display,visibility:style.visibility,
    opacity:style.opacity,position:style.position,insideEditor:rect.top>=viewport.top&&rect.bottom<=viewport.bottom,
    intersectsEditor:rect.bottom>viewport.top&&rect.top<viewport.bottom};
  };
  return {editor:{...inspect(editor),scrollTop:editor.scrollTop,scrollHeight:editor.scrollHeight,clientHeight:editor.clientHeight},
   active:document.activeElement?.getAttribute('aria-label'),cards:[...editor.querySelectorAll('[data-quote-instance]')].map(card=>({
    ...inspect(card),instanceId:card.dataset.quoteInstance,contentEditable:card.contentEditable,
    header:card.querySelector('.a-composer-quote-head')?inspect(card.querySelector('.a-composer-quote-head')):null,
    buttons:[...card.querySelectorAll('button')].map(button=>({...inspect(button),label:button.getAttribute('aria-label')||button.textContent,disabled:button.disabled})),
    excerpt:card.querySelector('pre')?.textContent,caption:card.querySelector('small')?.textContent,
   })),assets:[...document.scripts].map(node=>node.src).filter(Boolean)};
 });
 cardObservations.push({stage,rendered,document:await documentValue(),canonical:await canonicalDraft()});
 if(proof){
  writeFileSync(join(proof,'card-observations.json'),JSON.stringify(cardObservations,null,2));
  await page.locator('.a-composer').screenshot({path:join(proof,`cards-${cardObservations.length}.png`)});
 }
}
async function visibleCardControls(stage,card){
 await card.evaluate(card=>{
  const editor=card.closest('[role=textbox]'),header=card.querySelector('.a-composer-quote-head');
  editor.scrollTop=0;
  // Deliberately show the excerpt after the natural header position has
  // scrolled away. A merely present or Playwright-"visible" button is not enough.
  editor.scrollTop=card.getBoundingClientRect().top-editor.getBoundingClientRect().top+header.offsetHeight/2;
 });
 await page.evaluate(()=>new Promise(requestAnimationFrame));
 const actual=await card.evaluate(card=>{
  const editor=card.closest('[role=textbox]'),viewport=editor.getBoundingClientRect(),body=card.querySelector('pre').getBoundingClientRect();
  return {visibleExcerpt:body.bottom>viewport.top&&body.top<viewport.bottom,
   controls:[...card.querySelectorAll('.a-composer-quote-head button')].map(button=>{
    const rect=button.getBoundingClientRect(),hit=document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2);
    return {label:button.getAttribute('aria-label'),inside:rect.top>=viewport.top-1&&rect.bottom<=viewport.bottom+1,hit:button.contains(hit),disabled:button.disabled};
   })};
 });
 checkpoints.push({stage,controls:actual});
 if(proof)writeFileSync(join(proof,'commentary-checkpoints.json'),JSON.stringify(checkpoints,null,2));
 assert.ok(actual.visibleExcerpt,stage+': excerpt is visible');
 assert.equal(actual.controls.length,4,stage+': source and three quote controls exist');
 assert.ok(actual.controls.every(row=>row.inside&&row.hit),stage+': visible quote has reachable source/move/remove controls');
}
const BEFORE='Before the references',BETWEEN='Between the passages',BEFORE_WHOLE='Before the whole message',AFTER='After all references';
const UNICODE='  café e\u0301 東京  ';
const projectDocument=doc=>doc.segments.map(part=>part.type==='text'?part.text:part);
const canonicalDraft=async()=>{
 const id=await page.evaluate(()=>window.amplifier.getState().client.id);
 const response=await context.request.get(url+'/fixture/quote-draft?'+new URLSearchParams({clientId:id}));
 assert.equal(response.status(),200);return response.json();
};
async function enteredCommentary(text,{canonicalReady=true}={}){
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
 if(canonicalReady)await expect.poll(async()=>commentary((await canonicalDraft()).document||{segments:[]}).some(value=>value.includes(text))).toBe(true);
}
async function expectLayout(stage,expected,{canonicalReady=true}={}){
 const dom=await page.getByRole('textbox',{name:'Message Amplifier'}).evaluate(editor=>[...editor.children].map(node=>node.hasAttribute('data-quote-instance')?
  {instanceId:node.dataset.quoteInstance,excerpt:node.querySelector('pre').textContent}:node.textContent));
 const document=await documentValue();
 const expectedDom=expected.map(part=>typeof part==='string'?part:{instanceId:part.instanceId,excerpt:part.quote.excerpt});
 checkpoints.push({stage,expected,dom,document});
 if(proof)writeFileSync(join(proof,'commentary-checkpoints.json'),JSON.stringify(checkpoints,null,2));
 assert.deepEqual(dom,expectedDom,stage+': exact commentary/quote DOM order');
 assert.deepEqual(projectDocument(document),expected,stage+': independently expected editor order');
 if(canonicalReady)await expect.poll(async()=>projectDocument((await canonicalDraft()).document||{segments:[]})).toEqual(expected);
 else{
  // The deliberately held prior action blocks the existing ordered save
  // queue. The journal must own these exact bytes until receipt settlement.
  const pending=await page.evaluate(()=>{
   const state=window.amplifier.getState();
   return JSON.parse(sessionStorage.getItem('amplifier.pendingDrafts.v1')).rows.find(row=>row.sessionId===state.selectedSessionId);
  });
  assert.deepEqual(projectDocument(pending.document),expected,stage+': recovery journal owns the exact newer draft');
  checkpoints.at(-1).pendingJournal=pending;
 }
 const canonical=await canonicalDraft();
 checkpoints.at(-1).canonical=canonical;
 if(proof)writeFileSync(join(proof,'commentary-checkpoints.json'),JSON.stringify(checkpoints,null,2));
}
async function expectCompact(stage){
 const surface=page.locator('.a-compact-composer [data-part="composer-surface"]');
 await expect(surface).toHaveAttribute('data-expanded','true');
 const geometry=await surface.evaluate(node=>{
  const rect=el=>{const {x,y,width,height}=el.getBoundingClientRect();return {x,y,width,height};};
  const editor=node.querySelector('[role=textbox]'),writing=node.querySelector('.a-compose-writing');
  return {surface:rect(node),editor:rect(editor),writing:rect(writing),
   quoteCount:writing.querySelectorAll('[data-quote-instance]').length,
   overflow:node.scrollWidth>node.clientWidth+1,pageOverflow:document.documentElement.scrollWidth>innerWidth+1,
   theme:document.querySelector('#amp-one').dataset.themeScheme,
   primary:rect(node.querySelector('[data-part="composer-primary-action"]'))};
 });
 compactGeometry.push({stage,...geometry});
 if(proof)writeFileSync(join(proof,'compact-quote-geometry.json'),JSON.stringify(compactGeometry,null,2));
 assert.ok(geometry.quoteCount>=2,'Real immutable quotes belong inside the compact writing wrapper');
 assert.ok(geometry.editor.width>=geometry.surface.width-40,'Quotes use the full compact writing width');
 assert.ok(geometry.editor.height<=181,'Long quote drafts retain bounded editor height');
 assert.equal(geometry.overflow,false,'Compact quote surface must not overflow');
 assert.equal(geometry.pageOverflow,false,'Compact quote page must not overflow');
 assert.ok(geometry.primary.width>=44&&geometry.primary.height>=44,'Primary action retains its touch target');
 await expect(page.locator('[data-part="composer-primary-action"]')).toBeInViewport();
}
try{
 for(let i=0;i<100;i++){try{if((await fetch(url+'/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
 await page.goto(url);await page.waitForSelector('#amp-one');await showFixtureConversation(page);
 const editor=page.getByRole('textbox',{name:'Message Amplifier'}),quotes=editor.locator('[data-quote-instance]');
 await editor.fill(BEFORE);
 await expect(editor).toHaveText(BEFORE);
 await expect.poll(async()=>(await canonicalDraft()).draft).toBe(BEFORE);
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
 const firstQuote=references(await documentValue())[0];
 assert.equal(firstQuote.quote.excerpt,first.text);assert.equal(firstQuote.quote.messageId,sourceA);
 assert.deepEqual(firstQuote.quote.spans,[{start:first.start,end:first.end}]);
 await expectLayout('first quote',[BEFORE,firstQuote,'']);
 await observeCards('first quote after canonical autosave');
 await visibleCardControls('first quote scrolled',quotes.first());
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
 await expectLayout('between typed',[BEFORE,firstQuote,BETWEEN]);
 const second=await passage('```py','\n\nlast passage');await expect(quotes).toHaveCount(2);
 const secondQuote=references(await documentValue())[1];
 assert.equal(secondQuote.quote.excerpt,second.text);assert.equal(secondQuote.quote.messageId,sourceA);
 assert.deepEqual(secondQuote.quote.spans,[{start:second.start,end:second.end}]);
 await expectLayout('second quote',[BEFORE,firstQuote,BETWEEN,secondQuote,'']);
 await observeCards('second quote after canonical autosave');
 await expectCompact('two immutable references');
 await editor.click();await editor.press('ControlOrMeta+End');await editor.pressSequentially('Before the whole message');
 await enteredCommentary('Before the whole message');
 await expectLayout('before whole typed',[BEFORE,firstQuote,BETWEEN,secondQuote,BEFORE_WHOLE]);
 const wholeButton=b.getByRole('button',{name:'Reply to message',exact:true});
 await expect(b.locator('.a-detail-text')).toContainText('END OF EXACT LONG SOURCE');
 await wholeButton.scrollIntoViewIfNeeded();const beforeScroll=await page.locator('.a-messages').evaluate(n=>n.scrollTop);
 await wholeButton.evaluate(button=>{
  window.quoteScroll=[];window.beforeQuoteFocus=document.activeElement;
  window.quoteGeometry=()=>{const pane=document.querySelector('.a-messages');return {top:pane.scrollTop,height:pane.clientHeight,scrollHeight:pane.scrollHeight,buttonTop:button.getBoundingClientRect().top,composerHeight:document.querySelector('.a-composer').clientHeight};};
  for(const event of ['pointerdown','mousedown','focus','click'])button.addEventListener(event,()=>window.quoteScroll.push({event,...window.quoteGeometry()}),{capture:true,once:true});
 });
 await wholeButton.click();await expect(quotes).toHaveCount(3);
 const wholeQuote=references(await documentValue())[2];
 const originalWhole=(await observe()).sessions.flatMap(s=>s.messages).find(m=>m.id===sourceB).text;
 assert.equal(wholeQuote.quote.excerpt,originalWhole);assert.equal(wholeQuote.quote.messageId,sourceB);
 await expectLayout('whole quote',[BEFORE,firstQuote,BETWEEN,secondQuote,BEFORE_WHOLE,wholeQuote,'']);
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
 let expected=[BEFORE,firstQuote,BETWEEN,secondQuote,BEFORE_WHOLE,wholeQuote,AFTER];
 await expectLayout('after typed',expected);
 await editor.pressSequentially(UNICODE);
 await enteredCommentary(AFTER+UNICODE);
 expected=[BEFORE,firstQuote,BETWEEN,secondQuote,BEFORE_WHOLE,wholeQuote,AFTER+UNICODE];
 await expectLayout('Unicode whitespace typed',expected);
 assert.equal((await observe()).sends.length,0,'Collecting must not send');
 await editor.dispatchEvent('keydown',{key:'Enter',code:'Enter',isComposing:true,keyCode:229});
 assert.equal((await observe()).sends.length,0,'IME Enter must not send');
 await expectLayout('IME key leaves exact document unchanged',expected);
 let doc=await documentValue(),initial=[firstQuote,secondQuote,wholeQuote],comments=[BEFORE,BETWEEN,BEFORE_WHOLE,AFTER+UNICODE];
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
 await expectLayout('reload',expected);
 await observeCards('three quotes reconstructed after reload');
 await visibleCardControls('reload scrolled quote',quotes.first());
 // Same action remains focused when valid; disabled edge action gets an enabled fallback.
 await quotes.nth(1).getByRole('button',{name:'Move quote earlier',exact:true}).focus();await page.keyboard.press('Enter');
 assert.equal(await page.evaluate(()=>document.activeElement?.disabled),false);
 let moved=await documentValue();assert.deepEqual(references(moved).map(q=>q.instanceId),[initial[1].instanceId,initial[0].instanceId,initial[2].instanceId]);assert.deepEqual(commentary(moved),comments);
 expected=[BEFORE,secondQuote,BETWEEN,firstQuote,BEFORE_WHOLE,wholeQuote,AFTER+UNICODE];
 await expectLayout('reordered',expected);
 await quotes.nth(1).getByRole('button',{name:'Remove quote',exact:true}).click();await expect(quotes).toHaveCount(2);
 doc=await documentValue();assert.deepEqual(commentary(doc),comments);assert.deepEqual(references(doc).map(q=>q.quote.excerpt),[second.text,initial[2].quote.excerpt]);
 expected=[BEFORE,secondQuote,BETWEEN,BEFORE_WHOLE,wholeQuote,AFTER+UNICODE];
 await expectLayout('removed only first quote',expected);
 await page.getByRole('button',{name:'Remove synthetic.txt',exact:true}).waitFor();
 // Held source receipt: moving the caret/mode alone must not retarget the insertion.
 let release,started;const held=new Promise(resolve=>release=resolve),waiting=new Promise(resolve=>started=resolve);
 await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(body?.action==='message.reply'){const response=await route.fetch();started();await held;await route.fulfill({response});}else await route.continue();});
 await page.getByRole('combobox',{name:'Quote insertion'}).selectOption('append');
 const adding=b.getByRole('button',{name:'Reply to message',exact:true}).click();await waiting;
 await page.getByRole('combobox',{name:'Quote insertion'}).selectOption('caret');await editor.click();await editor.press('ControlOrMeta+Home');
 release();await adding;await expect(quotes).toHaveCount(3);await page.unroute('**/api/actions');
 const delayed=await documentValue(),priorIds=new Set([secondQuote.instanceId,wholeQuote.instanceId]);
 const added=references(delayed).filter(part=>!priorIds.has(part.instanceId));
 assert.equal(added.length,1,'exactly one new quote instance');
 const appendedQuote=added[0];assert.equal(appendedQuote.quote.messageId,sourceB);
 assert.deepEqual(appendedQuote.quote,wholeQuote.quote);
 expected=[BEFORE,secondQuote,BETWEEN,BEFORE_WHOLE,wholeQuote,AFTER+UNICODE,appendedQuote,''];
 await expectLayout('held addition appended after all existing commentary',expected);
 // A changed draft rejects a late snapshot; a moved caret above did not.
 let changedRelease,changedStarted;
 const changedHeld=new Promise(r=>changedRelease=r),changedWaiting=new Promise(r=>changedStarted=r);
 await page.route('**/api/actions',async route=>{if(route.request().postDataJSON()?.action==='message.reply'){const response=await route.fetch();changedStarted();await changedHeld;await route.fulfill({response});}else await route.continue();});
 await b.getByRole('button',{name:'Reply to message',exact:true}).click();await changedWaiting;
 await editor.focus();await editor.press('ControlOrMeta+End');await editor.pressSequentially('Newer commentary');
 await enteredCommentary('Newer commentary',{canonicalReady:false});
 expected=[BEFORE,secondQuote,BETWEEN,BEFORE_WHOLE,wholeQuote,AFTER+UNICODE,appendedQuote,'Newer commentary'];
 await expectLayout('new text while source receipt held',expected,{canonicalReady:false});
 changedRelease();await page.getByText('The draft changed. Your text was kept; add the quote again.',{exact:true}).first().waitFor();
 await expect(quotes).toHaveCount(3);await page.unroute('**/api/actions');
 await expectLayout('late changed receipt rejected',expected);
 const sourceLink=quotes.first().getByRole('button',{name:'Go to original quoted message'});
 await sourceLink.focus();await page.keyboard.press('Enter');
 await page.getByRole('button',{name:'Return to reading position'}).click();
 await expect(sourceLink).toBeFocused();
 const options=page.getByRole('button',{name:'Model and reasoning settings',exact:true});
 await options.click();await page.getByRole('dialog',{name:'Chat options',exact:true}).waitFor();
 await page.keyboard.press('Escape');await expect(options).toBeFocused();
 await expectLayout('compact options preserve interleaving',expected);
 await expectCompact('desktop retained quote draft');
 if(proof)await page.locator('.a-composer').screenshot({path:join(proof,'quotes-desktop.png')});
 for(const width of [390,320]){
  await page.setViewportSize({width,height:844});
  for(const colorScheme of ['light','dark']){
   await page.emulateMedia({colorScheme});
   const revision=await page.evaluate(async scheme=>{
    const shell=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;
    const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:shell.revision,
     composition:{...shell.effectiveComposition,presentation:{...shell.effectiveComposition.presentation,scheme}}});
    if(!prepared.accepted)throw Error(prepared.error||'Fixture appearance refused');
    const applied=await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:shell.revision,changeId:prepared.result.id});
    if(!applied.accepted)throw Error(applied.error||'Fixture appearance refused');return applied.result.revision;
   },colorScheme);
   await expect.poll(()=>page.evaluate(()=>window.amplifier.getShellState()?.revision)).toBe(revision);
   await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',colorScheme);
   await expectCompact(`${width}px ${colorScheme}`);
   await visibleCardControls(`${width}px ${colorScheme}`,quotes.first());
   await expectLayout(`${width}px ${colorScheme} preserves interleaving`,expected);
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
 assert.deepEqual(projectDocument(submitted),expected,'submitted independently expected interleaving');
 assert.deepEqual(projectDocument(sent.replyTo),expected,'saved sent independently expected interleaving');
 for(const text of [BEFORE,BETWEEN,BEFORE_WHOLE,AFTER+UNICODE,'Newer commentary'])assert.ok(sent.text.includes(text),'saved readable message missing '+text);
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
 await page.getByRole('button',{name:'Quote selection',exact:true}).click();await expect(quotes).toHaveCount(1);
 assert.equal(references(await documentValue())[0].quote.excerpt,'second line');
 await observeCards('direct rendered selection');
 await quotes.first().getByRole('button',{name:'Remove quote',exact:true}).click();await expect(quotes).toHaveCount(0);
 const c=page.locator('.a-message').filter({hasText:'Quote fixture source C'}).first();
 const selectContents=async(selector,first=true)=>{
  const node=c.locator('.a-detail-text '+selector);
  await node.scrollIntoViewIfNeeded();
  await node.evaluate((node,first)=>{
   const range=document.createRange();range.selectNodeContents(node);
   if(!first){range.setStart(node.lastChild,0);range.setEnd(node.lastChild,node.lastChild.childNodes.length);}
   const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);document.dispatchEvent(new Event('selectionchange'));
  },first);
 };
 for(const [selector,text] of [['p:nth-of-type(2)','A bold inside and label end.'],['ul','first\nsecond'],['p:nth-of-type(3)','Third paragraph café 😀 e\u0301 東京.'],['p:nth-of-type(4)','inline code'],['pre code','  code\tvalue\n'],['p:nth-of-type(5)','Repeated same same.']]){
  await selectContents(selector);
  const quick=page.getByRole('button',{name:'Quote selection',exact:true});await expect(quick).toBeVisible();
  const before=await editor.textContent();
  const geometry=await quick.evaluate(button=>{const r=button.getBoundingClientRect(),s=window.getSelection().getRangeAt(0).getBoundingClientRect();return {distance:r.top-s.bottom,selected:window.getSelection().toString()};});
  assert.ok(geometry.distance>=0&&geometry.distance<56,'Quote selection is next to the selected passage');
  await quick.click();await expect(quotes).toHaveCount(1);
  const collected=references(await documentValue())[0];
  assert.equal(collected.quote.excerpt,text,'rendered selection keeps exact independently expected text');
  assert.ok(collected.quote.spans.length>=1);await visibleCardControls('rendered '+selector,quotes.first());
  await quotes.first().getByRole('button',{name:'Remove quote',exact:true}).click();await expect(quotes).toHaveCount(0);
 }
 // A transformed entity cannot silently turn into decoded or broader source.
 await selectContents('p:last-child');
 await expect(page.locator('.a-quote-selection').getByRole('button',{name:'Choose passage',exact:true})).toBeVisible();
 await expect(quotes).toHaveCount(0);
 await page.keyboard.press('Escape');await expect(page.locator('.a-quote-selection')).toHaveCount(0);
 assert.equal((await observe()).sends.length,2,'Quote/remove must not execute another turn');
 assert.deepEqual(errors,[]);
 if(proof)writeFileSync(join(proof,'observed-document.json'),JSON.stringify({sourceA,sourceB,first,second,expected,submitted,sent,initialScroll:beforeScroll},null,2));
 console.log('PASS exact Unicode/multiline passages + whole message, interleaved commentary, attachment, reload, keyboard reorder, removal, captured delayed target, narrow rendering, sent projection and adjacent cycle.');
}catch(error){
 if(proof){writeFileSync(join(proof,'failure.json'),JSON.stringify({message:error.message,errors},null,2));await page.screenshot({path:join(proof,'failure.png')}).catch(()=>{});}
 throw error;
}finally{await browser.close();fixture.kill();await new Promise(resolve=>fixture.exitCode!==null?resolve():fixture.once('exit',resolve));}
