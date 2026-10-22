// Real bundled browser + host fixture. No real account/history/model access.
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const installed=process.env.AMPLIFIER_TEST_INSTALLED_STATIC==='1';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/peer_preview_ui_server.py',import.meta.url)),fileURLToPath(new URL('../../',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser,page;const results=[];
try{
 const target=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture readiness timeout')),30000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}}})});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-peer-preview'}});
 await context.grantPermissions(['clipboard-read','clipboard-write']);
 page=await context.newPage();const errors=[],commands=[];page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/actions'){try{commands.push(r.postDataJSON().action)}catch{}}});
 await page.goto(target);await showFixtureConversation(page,{navPinned:false});
 const facts=await (await page.request.get(new URL('/api/fixture/peerFacts',target).href)).json();
 assert.equal(facts.installed,installed);
 const change=async kind=>{const response=await page.request.post(new URL('/api/fixture/change',target).href,{data:{kind},headers:{Origin:target}});assert.equal(response.ok(),true,await response.text())};
 const article=id=>page.locator(`[data-message-id="${id}"]`),panel=()=>page.getByRole('region',{name:'Read-only source chat preview'}).or(page.getByRole('dialog',{name:'Read-only source chat preview'}));
 const run=page.locator('.a-peer-run'),summary=run.locator('.a-peer-run-summary');
 await expect(summary).toContainText('3 messages');await expect(summary).toHaveAttribute('aria-expanded','false');
 await summary.press('Enter');
 assert.deepEqual(await run.locator('article[data-message-id]').evaluateAll(nodes=>nodes.map(n=>n.dataset.messageId)),facts.originals);
 const original=article(facts.originals[0]);await original.locator('.a-peer-summary').press('Enter');
 async function formatted(root){
  await expect(root.locator('h1')).toHaveText('Navigation review');await expect(root.locator('h2').first()).toHaveText('Ordered checks');
  await expect(root.locator('ol>li')).toHaveCount(2);await expect(root.locator('ul>li')).toHaveCount(2);
  await expect(root.locator('strong')).toContainText('strong');await expect(root.locator('em')).toContainText('emphasis');
  await expect(root.locator('blockquote')).toContainText('A peer report is not a human approval.');await expect(root.locator('table')).toHaveCount(1);
  assert.equal(await root.locator('pre code.language-python').textContent(),'def sample():\n    return "café α"\n\nprint(sample())\n');
  const paragraph=root.locator('p').filter({hasText:'First paragraph with'});
  assert.match(await paragraph.textContent(),/inline_code\.\nThis is a real soft line break\./);
  assert.equal(await paragraph.evaluate(n=>getComputedStyle(n).whiteSpace),'pre-wrap');
  await expect(root.locator('p').filter({hasText:'Literal backslash-n'})).toContainText('\\n, not a newline.');
  assert.equal(await root.locator('script,img[onerror],a[href^="javascript:"]').count(),0);
  assert.equal(await page.evaluate(()=>globalThis.peerXss),undefined);
 }
 await formatted(original.locator('.a-markdown'));
 // Discriminator: deliberately flatten DOM text; semantic line-break assertion must fail.
 const flattened=await original.locator('pre code').textContent();assert.notEqual(flattened.replace(/\s+/g,' '),flattened);
 if(process.env.PEER_NEGATIVE_FLATTEN==='1'){
  await original.locator('.a-markdown').evaluate(n=>{
   const walker=document.createTreeWalker(n,NodeFilter.SHOW_TEXT);let text;
   while(text=walker.nextNode())text.nodeValue=text.nodeValue.replace(/\s+/g,' ');
  });
  let detected;try{await formatted(original.locator('.a-markdown'))}catch(e){detected=e}
  assert.ok(detected,'Negative control incorrectly passed');
  throw Error('PEER_NEGATIVE_CONTROL_CONFIRMED: '+detected.message);
 }
 await original.getByRole('button',{name:'Copy peer message as Markdown'}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(facts.text);
 assert.equal(createHash('sha256').update(await page.evaluate(()=>navigator.clipboard.readText())).digest('hex'),facts.textSha256);
 const receivedLong=article(facts.originals[2]);await receivedLong.locator('.a-peer-summary').click();
 await receivedLong.getByRole('button',{name:'Show full text',exact:true}).click();
 await expect(receivedLong.locator('h2').last()).toHaveText('Beyond compact boundary');
 await receivedLong.getByRole('button',{name:'Copy peer message as Markdown'}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(facts.long);
 await receivedLong.locator('.a-peer-summary').click();
 await page.evaluate(async()=>{
  const s=window.amplifier.getState(),result=await window.amplifier.dispatch('attachment.add',{
   sessionId:s.selectedSessionId,name:'draft-proof.txt',base64:btoa('Synthetic attachment retained without sending.')});
  if(result.accepted!==true)throw Error('Synthetic draft attachment was not accepted');
 });
 const before=await page.evaluate(()=>{const s=window.amplifier.getState();window.peerDocument=crypto.randomUUID();return {id:s.selectedSessionId,draft:s.view.draft,attachments:s.sessions.find(x=>x.id===s.selectedSessionId)?.draftAttachments||s.draftAttachments||[],document:window.peerDocument}});
 assert.equal(before.attachments.length,1);
 const link=original.getByRole('button',{name:'Preview source chat: Navigation design'});
 await link.click();await expect(panel()).toBeVisible();
 assert.equal(await original.locator('.a-peer-summary').getAttribute('aria-expanded'),'true');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),facts.receiving);
 const sourceOriginal=panel().locator(`[data-preview-message-id="${facts.sourceText}"]`);
 await formatted(sourceOriginal.locator('.a-markdown'));
 await sourceOriginal.getByRole('button',{name:'Copy preview original as Markdown'}).click();await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(facts.text);
 const sourceLong=panel().locator(`[data-preview-message-id="${facts.sourceLong}"]`);await sourceLong.getByRole('button',{name:'Show full text',exact:true}).click();
 await expect(sourceLong.locator('h2').last()).toHaveText('Beyond compact boundary');
 await sourceLong.getByRole('button',{name:'Copy preview original as Markdown'}).click();await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(facts.long);
 await panel().getByRole('button',{name:'Load earlier messages',exact:true}).click();
 await formatted(panel().locator(`[data-preview-message-id="${facts.older}"] .a-markdown`));
 const longNode=await sourceLong.elementHandle();
 const readAnchor=()=>panel().locator('.a-peer-preview-scroll').evaluate(pane=>{
  const top=pane.getBoundingClientRect().top,node=[...pane.querySelectorAll('[data-preview-message-id]')].find(n=>n.getBoundingClientRect().bottom>top);
  return {id:node.dataset.previewMessageId,offset:node.getBoundingClientRect().top-top};
 });
 await panel().locator('.a-peer-preview-scroll').evaluate(n=>n.scrollTop=700);
 const anchor=await readAnchor();await change('source-arrive');
 await expect(panel().getByText('New captured note',{exact:true})).toHaveCount(0);
 await panel().getByRole('button',{name:'Refresh',exact:true}).click();
 await expect(panel().getByText('New captured note',{exact:true})).toBeVisible();
 assert.equal(await sourceLong.evaluate((node,old)=>node===old,longNode),true);
 const afterAnchor=await readAnchor();assert.equal(afterAnchor.id,anchor.id);assert.ok(Math.abs(afterAnchor.offset-anchor.offset)<2);
 await expect(sourceLong.locator('h2').last()).toHaveText('Beyond compact boundary');
 // Confirm no reload/current draft mutation through preview and an adjacent close/reopen cycle.
 assert.deepEqual(await page.evaluate(()=>{const s=window.amplifier.getState();return {id:s.selectedSessionId,draft:s.view.draft,attachments:s.sessions.find(x=>x.id===s.selectedSessionId)?.draftAttachments||s.draftAttachments||[],document:window.peerDocument}}),before);
 await panel().getByRole('button',{name:'Close source preview'}).click();await expect(link).toBeFocused();
 await change('arrive');await expect(summary).toContainText('4 messages');await expect(original.locator('.a-peer-summary')).toHaveAttribute('aria-expanded','true');
 for(const width of [390,320]){
  await page.setViewportSize({width,height:900});await link.click();await expect(panel()).toBeVisible();
  await expect(panel()).toHaveAttribute('aria-modal','true');
  const geometry=await panel().evaluate(n=>({width:n.getBoundingClientRect().width,overflow:n.scrollWidth>n.clientWidth}));assert.equal(geometry.overflow,false);assert.ok(geometry.width<=width);
  if(process.env.AMPLIFIER_PROOF_DIR){mkdirSync(process.env.AMPLIFIER_PROOF_DIR,{recursive:true});await page.screenshot({path:join(process.env.AMPLIFIER_PROOF_DIR,`peers-narrow-${width}.png`)});}
  await panel().getByRole('button',{name:'Close source preview'}).press('Escape');await expect(link).toBeFocused();
 }
 const longTitleLink=article(facts.originals[1]).getByRole('button',{name:/Preview source chat:/});
 await longTitleLink.click();await expect(panel().locator('.a-canvas-head strong')).toContainText('Accessibility review');
 await expect(panel().locator('[data-preview-message-id]')).toHaveCount(1);
 await panel().getByRole('button',{name:'Copy preview original as Markdown'}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(facts.crlf);
 assert.ok(await panel().locator('.a-canvas-head').evaluate(n=>n.scrollWidth<=n.clientWidth));
 await panel().getByRole('button',{name:'Close source preview'}).press('Shift+Tab');
 assert.equal(await panel().evaluate(n=>n.contains(document.activeElement)),true);
 await page.keyboard.press('Escape');await expect(longTitleLink).toBeFocused();
 await page.setViewportSize({width:1280,height:1000});
 // Existing Settings cancellation must prevent the exact preview request.
 await openSettingsPage(page,'workspaces');
 await page.locator('#default-workspace-root').fill('/synthetic-unsaved-folder');
 await expect(page.getByText('You have unsaved settings changes. They are kept while Settings stays open.',{exact:true})).toBeVisible();
 await page.evaluate(mid=>{
  const s=window.amplifier.getState(),peer=s.sessions.find(x=>x.id===s.selectedSessionId).messages.find(m=>m.id===mid).attribution.peer;
  window.peerGuardAttempt=window.amplifier.dispatch('peer.preview',{proof:peer.proof,sourceSessionId:s.selectedSessionId});
 },facts.originals[0]);
 const settingsGuard=page.getByRole('alertdialog',{name:'Unsaved settings changes'});
 await expect(settingsGuard).toBeVisible();await settingsGuard.getByRole('button',{name:'Keep settings open'}).click();
 assert.equal((await page.evaluate(()=>window.peerGuardAttempt)).accepted,false);
 await expect(page.locator('#default-workspace-root')).toHaveValue('/synthetic-unsaved-folder');
 await expect(panel()).toHaveCount(0);
 await page.getByRole('button',{name:/^Close (settings|panel)$/}).click();await settingsGuard.getByRole('button',{name:'Discard changes',exact:true}).click();
 // Exact existing Canvas dirty guard. The feature must not replace it.
 await page.evaluate(async()=>{await window.amplifier.dispatch('canvas.show',{kind:'html',content:'<input value="unfinished">'});const state=window.amplifier.getState(),v=state.canvasWorkspace.views.find(x=>x.viewId==='primary');await window.amplifier.dispatch('canvas.views.dirty',{viewId:v.viewId,resourceId:v.resourceId,resourceRevision:v.resourceRevision,generation:v.generation,dirty:true})});
 await link.click();await expect(panel()).toHaveCount(0);await expect(original.getByRole('alert')).toContainText('edit');
 await page.evaluate(async()=>{const v=window.amplifier.getState().canvasWorkspace.views.find(x=>x.viewId==='primary');await window.amplifier.dispatch('canvas.views.dirty',{viewId:v.viewId,resourceId:v.resourceId,resourceRevision:v.resourceRevision,generation:v.generation,dirty:false})});
 await link.click();await expect(panel()).toBeVisible();
 await page.route('**/api/peer/preview?**',async route=>{if(!new URL(route.request().url()).searchParams.has('metadata'))await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Synthetic read failure'})});else await route.continue()});
 await panel().getByRole('button',{name:'Refresh',exact:true}).click();await expect(panel().getByRole('alert')).toContainText('Previous snapshot retained');
 await page.unroute('**/api/peer/preview?**');await panel().getByRole('button',{name:'Refresh',exact:true}).click();
 await change('archive');await panel().getByRole('button',{name:'Refresh',exact:true}).click();
 if(process.env.AMPLIFIER_PROOF_DIR){mkdirSync(process.env.AMPLIFIER_PROOF_DIR,{recursive:true});await page.screenshot({path:join(process.env.AMPLIFIER_PROOF_DIR,'peers-desktop.png')});}
 await panel().getByRole('button',{name:'Open chat',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(facts.sourceA);assert.equal(await page.evaluate(()=>window.peerDocument),before.document);
 await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),facts.receiving);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(facts.receiving);
 assert.deepEqual(await page.evaluate(()=>{const s=window.amplifier.getState();return s.sessions.find(x=>x.id===s.selectedSessionId)?.draftAttachments||s.draftAttachments||[]}),before.attachments);
 await expect(summary).toBeVisible();if(await summary.getAttribute('aria-expanded')==='false')await summary.click();
 await link.click();await expect(panel()).toBeVisible();await change('revoke');
 await expect(panel().getByText('Source chat unavailable',{exact:true})).toBeVisible();await expect(panel().locator('[data-preview-message-id]')).toHaveCount(0);
 await expect(panel().getByRole('button',{name:'Open chat',exact:true})).toBeDisabled();
 const final=await (await page.request.get(new URL('/api/fixture/peerFacts',target).href)).json();assert.deepEqual(final.effects,[]);assert.deepEqual(final.subprocesses,[]);assert.deepEqual(errors,[]);
 assert.ok(!commands.some(x=>['conversation.send','worker.spawn','session.warm','approval.respond'].includes(x)));
 results.push({mode:installed?'installed-static':'built-static',semanticMarkdown:true,exactCopy:true,draftAttachmentsRetained:before.attachments.length,narrow:[320,390],noReload:true,noRuntime:true,negativeControl:'separate PEER_NEGATIVE_FLATTEN=1 run required',backend:final.backend});
 if(process.env.AMPLIFIER_PROOF_DIR)writeFileSync(join(process.env.AMPLIFIER_PROOF_DIR,'peer-browser-result.json'),JSON.stringify(results,null,2));
 console.log(JSON.stringify(results));
}catch(error){
 if(page&&process.env.AMPLIFIER_PROOF_DIR){
  mkdirSync(process.env.AMPLIFIER_PROOF_DIR,{recursive:true});
  await page.screenshot({path:join(process.env.AMPLIFIER_PROOF_DIR,'failure.png')}).catch(()=>{});
  writeFileSync(join(process.env.AMPLIFIER_PROOF_DIR,'failure.json'),JSON.stringify({error:String(error),body:await page.locator('body').innerText().catch(()=>''),url:page.url()},null,2));
 }
 throw error;
}finally{await browser?.close();fixture.kill();}
