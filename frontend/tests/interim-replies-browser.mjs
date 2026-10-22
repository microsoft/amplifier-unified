import {showFixtureConversation} from './browser-chat-pages.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {chromium,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',
 ['-u',root+'tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','pipe']});
let log='';fixture.stderr.on('data',chunk=>log+=chunk);
const started=await new Promise((resolve,reject)=>{
 let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout: '+log)),30000);
 fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line){clearTimeout(timer);resolve(JSON.parse(line))}});
 fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+log))});
});
const base=`http://127.0.0.1:${started.port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
const control=async body=>{const r=await fetch(base+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(r.ok,true,await r.clone().text());return r.json()};
const launchOptions={headless:true,...(process.env.CHROMIUM_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}:{})};
const browser=await chromium.launch(launchOptions);
let otherBrowser,debugPage,debugErrors=[];
try{
 const context=await browser.newContext({viewport:{width:1280,height:1000},extraHTTPHeaders:headers,permissions:['clipboard-read','clipboard-write']});
 await context.addCookies([{name:'amplifier_unified_session',value:started.cookie,url:base,httpOnly:true,sameSite:'Strict'}]);
 const page=await context.newPage(),errors=[];debugPage=page;debugErrors=errors;page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(15000);
 await control({op:'interim-start'});await page.goto(base);await showFixtureConversation(page);
 await expect(page.getByText('Checking the first detail.',{exact:true})).toBeVisible();
 await control({op:'interim-finish'});
 await expect(page.getByText('The final next step.',{exact:true})).toBeVisible();
 assert.equal(await page.locator('[data-interim-group]').count(),0,'OFF is the existing layout');
 const saved=await page.evaluate(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId));
 const progress=saved.messages.find(m=>m.text==='Checking the first detail.');
 const exportText=()=>page.evaluate(async id=>{const r=await window.amplifier.dispatch('session.export',{id,format:'markdown',destination:'none'});return (await (await fetch(r.result.url)).json()).content},started.alpha);
 const originalExport=await exportText();
 await openSettingsPage(page,'appearance');
 const toggle=page.getByRole('checkbox',{name:/Collapse interim replies/});
 await expect(toggle).not.toBeChecked();await toggle.check();
 await expect(toggle).toBeChecked();await page.getByRole('button',{name:'Close panel',exact:true}).click();
 const group=page.locator('[data-interim-group]'),header=group.locator('button.a-execution-turn-line').first();
 await expect(group).toHaveCount(1);await expect(header).toHaveAttribute('aria-expanded','false');
 await expect(page.getByText('Checking the first detail.',{exact:true})).toHaveCount(0);
 await expect(page.getByText('The final conclusion.',{exact:true})).toBeVisible();
 if(process.env.EVIDENCE_DIR){await mkdir(process.env.EVIDENCE_DIR,{recursive:true});await page.locator('.a-messages').screenshot({path:process.env.EVIDENCE_DIR+'/interim-desktop-collapsed.png'});}
 assert.equal(await exportText(),originalExport,'folding never changes export');
 await header.focus();await page.keyboard.press('Enter');
 await expect(header).toHaveAttribute('aria-expanded','true');
 await expect(page.getByText('Checking the first detail.',{exact:true})).toBeVisible();
 assert.equal(await page.locator(`[data-message-id="${progress.id}"]`).count(),1);
 await page.locator(`[data-message-id="${progress.id}"]`).getByRole('button',{name:'Copy message as Markdown'}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(progress.text);
 await header.click();await expect(header).toHaveAttribute('aria-expanded','false');
 await page.evaluate(({sid,id})=>window.amplifier.dispatch('message.reveal',{sessionId:sid,messageId:id}),{sid:started.alpha,id:progress.id});
 await expect(header).toHaveAttribute('aria-expanded','true');
 await expect(page.locator(`[data-message-id="${progress.id}"]`)).toBeFocused();
 await header.click();
 await openSettingsPage(page,'appearance');await page.getByRole('radio',{name:/^Everything /}).check();
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await expect(header).toHaveAttribute('aria-expanded','false');
 await page.reload();await expect(header).toHaveAttribute('aria-expanded','false');
 // Another browser has its own presentation choice.
 otherBrowser=await chromium.launch(launchOptions);
 const other=await otherBrowser.newContext({extraHTTPHeaders:headers});
 await other.addCookies([{name:'amplifier_unified_session',value:started.cookie,url:base,httpOnly:true,sameSite:'Strict'}]);
 const second=await other.newPage();await second.goto(base);await showFixtureConversation(second);
 await expect(second.getByText('Checking the first detail.',{exact:true})).toBeVisible();
 await otherBrowser.close();otherBrowser=null;
 // Exact host-observed tools stay interleaved with prose/model rows after the
 // saved-view transform. Full service recreation is covered by the HTTP test.
 const detailed=await control({op:'interim-start',details:true});
 const detailSession=detailed.state.sessions.find(row=>row.id===started.alpha);
 const detailGeneration=detailSession.fixtureGeneration;
 const detailProgress=detailSession.messages.filter(row=>row.role==='assistant');
 assert.equal(detailProgress.length,2);
 await page.getByRole('textbox',{name:'Message Amplifier'}).focus();
 const detailFinish=await control({op:'interim-finish'});
 const detailFinal=detailFinish.state.sessions.find(row=>row.id===started.alpha).messages.find(row=>row.text==='The final next step.');
 await expect(page.locator(`[data-message-id="${detailFinal.id}"]`)).toBeAttached();
 await page.locator('.a-messages').evaluate(el=>{el.scrollTop=0});
 await page.locator('.a-messages').evaluate(el=>{el.scrollTop=el.scrollHeight});
 await expect(group).toHaveCount(1);
 await control({op:'interim-saved-view'});
 const reloadNavigation=[];
 const observeNavigation=request=>{
  const url=new URL(request.url());
  if(url.pathname==='/api/conversation/navigation'&&reloadNavigation.length<20)
   reloadNavigation.push({sessionId:url.searchParams.get('sessionId'),messageId:url.searchParams.get('messageId')});
 };
 page.on('request',observeNavigation);
 await page.reload();await showFixtureConversation(page);
 await expect(page.locator(`[data-message-id="${detailFinal.id}"]`)).toBeAttached();
 const readingEvidence=await page.evaluate(({sid,generation})=>{
  const state=window.amplifier.getState(),pane=document.querySelector('.a-messages'),selection=document.getSelection();
  const messages=state.sessions.find(row=>row.id===sid)?.messages||[];
  let saved;try{saved=JSON.parse(sessionStorage.getItem('amplifier.chat-reading.v1')||'{}')[sid]}catch{}
  return {messageFocus:state.view.messageFocus||null,readingAnchor:saved?.messageId||null,
   distance:pane.scrollHeight-pane.scrollTop-pane.clientHeight,focused:pane.contains(document.activeElement),
   selected:!!(selection&&!selection.isCollapsed&&pane.contains(selection.anchorNode)),
   responses:messages.filter(row=>row.generationId===generation&&row.response?.interim).map(row=>
    ({id:row.id,status:row.response.status,generationId:row.response.generationId,finalMessageIds:row.response.finalMessageIds}))};
 },{sid:started.alpha,generation:detailGeneration});
 console.log('INTERIM-RELOAD-BOUNDARY',JSON.stringify({readingEvidence,reloadNavigation}));
 assert.equal(readingEvidence.messageFocus,null,'Removed fixture messages cannot remain reveal targets');
 // Reconnect restores reading position independently of completion. Explicitly
 // return to the latest rendered frame, without changing the reading guard.
 await page.getByRole('textbox',{name:'Message Amplifier'}).focus();
 await page.locator('.a-messages').evaluate(el=>{el.scrollTop=0});
 await page.locator('.a-messages').evaluate(el=>{el.scrollTop=el.scrollHeight});
 await expect(page.getByText('Invalid request: This message is no longer available. Refresh the chat navigator.',{exact:true})).toHaveCount(0);
 await expect(header).toBeVisible();
 page.off('request',observeNavigation);
 if(await header.getAttribute('aria-expanded')==='false')await header.click();
 const expectedOrder=detailProgress.flatMap((message,index)=>[
  message.id,`tool:${started.alpha}:${detailGeneration}-call-${index+1}`,`${detailGeneration}-model-${index+1}`]);
 await expect(group.locator('[data-kind="tool"]')).toHaveCount(2);
 await expect(group.locator('[data-kind="llm"]')).toHaveCount(2);
 assert.deepEqual(await group.locator('[data-message-id],[data-node-id]').evaluateAll(nodes=>
  nodes.map(node=>node.getAttribute('data-message-id')||node.getAttribute('data-node-id'))),expectedOrder);
 assert.equal(await group.locator(`[data-message-id="${detailFinal.id}"]`).count(),0);
 for(let index=1;index<=2;index++){
  const node=group.locator(`[data-node-id="tool:${started.alpha}:${detailGeneration}-call-${index}"]`);
  const button=node.locator(':scope > button.a-execution-action-line');
  if(await button.getAttribute('aria-expanded')==='false')await button.click();
  const copyOutput=node.getByRole('button',{name:'Copy file content',exact:true});
  await expect(copyOutput).toBeEnabled();
  await copyOutput.click();
  const output='雪𝄞'.repeat(10000)+`:OUTPUT-${index}`;
  await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(output);
  const copyInput=node.getByRole('button',{name:'Copy arguments',exact:true});
  await expect(copyInput).toBeEnabled();await copyInput.click();
  assert.deepEqual(JSON.parse(await page.evaluate(()=>navigator.clipboard.readText())),
   {note:'界𝄞'.repeat(10000)+`:INPUT-${index}`});
  const contents=node.locator('.a-execution-payload').filter({has:page.getByRole('button',{name:'Copy file content',exact:true})});
  await expect(contents).toHaveCount(1);
  await contents.getByRole('button',{name:/^Show all /}).click();
  await expect(contents.locator('pre code')).toHaveText(output);
  await expect(node.locator('.a-execution-load-error')).toHaveCount(0);
 }
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 if(process.env.EVIDENCE_DIR)await page.screenshot({path:process.env.EVIDENCE_DIR+'/interim-exact-tools-mobile.png',fullPage:true});
 await page.setViewportSize({width:1280,height:1000});
 await control({op:'interim-start'});await expect(group).toHaveCount(0);
 await expect(page.getByText('Checking the second detail.',{exact:true})).toBeVisible();
 await control({op:'interim-error'});await expect(group).toHaveCount(0);
 await expect(page.getByText('Checking the first detail.',{exact:true})).toBeVisible();
 await control({op:'interim-start'});await page.locator('.a-messages').evaluate(el=>{el.scrollTop=el.scrollHeight;document.activeElement?.blur()});
 await control({op:'interim-finish'});await expect(group).toHaveCount(1);
 await expect(header).toHaveAttribute('aria-expanded','false');
 // Completion must not remove a keyboard user's focused progress controls.
 await control({op:'interim-start'});
 const currentProgress=page.locator('.a-assistant').filter({hasText:'Checking the first detail.'});
 await currentProgress.getByRole('button',{name:'Copy message as Markdown'}).focus();
 await control({op:'interim-finish'});
 await expect(page.getByText('The final next step.',{exact:true})).toBeVisible();
 await expect(group).toHaveCount(0);
 await page.keyboard.press('Tab');await expect(group).toHaveCount(0);
 await page.getByRole('textbox',{name:'Message Amplifier'}).focus();
 await page.locator('.a-messages').evaluate(el=>{el.scrollTop=el.scrollHeight});
 await expect(group).toHaveCount(1);
 // Actual retained media is visible once, outside collapsed prose and in order when opened.
 await control({op:'interim-media'});
 await expect(page.getByText('Retained interim animation',{exact:true})).toBeVisible();
 await expect(page.locator('[data-media-id]')).toHaveCount(1);
 await header.click();await expect(header).toHaveAttribute('aria-expanded','true');
 await expect(page.locator('[data-media-id]')).toHaveCount(1);
 const contentOrder=await group.locator('[data-message-id],[data-media-id]').evaluateAll(nodes=>nodes.map(node=>node.hasAttribute('data-media-id')?'media':node.querySelector('p')?.textContent));
 assert.deepEqual(contentOrder,['Checking the first detail.','media','Checking the second detail.']);
 await header.click();
 await page.setViewportSize({width:390,height:844});
 await expect(header).toBeVisible();await header.click();
 await expect(page.getByText('Checking the first detail.',{exact:true})).toBeVisible();
 await expect(group.locator('[data-kind="tool"]')).toHaveCount(2);
 if(process.env.EVIDENCE_DIR){await mkdir(process.env.EVIDENCE_DIR,{recursive:true});await page.screenshot({path:process.env.EVIDENCE_DIR+'/interim-mobile-expanded.png',fullPage:true});}
 await page.setViewportSize({width:1280,height:1000});
 const longStart=await control({op:'interim-start',long:true});
 const longProgress=longStart.state.sessions.find(row=>row.id===started.alpha).messages.find(message=>message.text.includes('A retained progress paragraph.'));
 assert.ok(longProgress?.generationId);
 await expect(page.locator(`[data-message-id="${longProgress.id}"]`)).toContainText('A retained progress paragraph.');
 await page.getByRole('textbox',{name:'Message Amplifier'}).focus();
 await page.locator('.a-messages').evaluate(el=>{el.scrollTop=0});
 const completed=await control({op:'interim-finish'});
 const finalMessage=completed.state.sessions.find(row=>row.id===started.alpha).messages.find(message=>message.generationId===longProgress.generationId&&message.text==='The final next step.');
 assert.ok(finalMessage?.id);
 // HTTP completion does not mean the browser has rendered the terminal frame.
 // Bind to this exact generation before testing the user's scroll transition.
 await expect(page.locator(`[data-message-id="${finalMessage.id}"]`)).toBeAttached();
 await expect.poll(()=>page.evaluate(({sid,generation,id})=>window.amplifier.getState().sessions.find(row=>row.id===sid)?.messages.some(message=>message.generationId===generation&&message.response?.status==='completed'&&message.response.finalMessageIds?.includes(id)),{sid:started.alpha,generation:longProgress.generationId,id:finalMessage.id})).toBe(true);
 await expect(group,'scrollback defers automatic folding after terminal render').toHaveCount(0);
 await page.getByRole('button',{name:'Jump to latest messages',exact:true}).click();
 await expect(group).toHaveCount(1);
 await openSettingsPage(page,'appearance');
 const rejectSave=async route=>{
  if(route.request().postDataJSON()?.action==='shell.changes.prepare')
   return route.fulfill({status:409,json:{error:'Synthetic preference save rejected.'}});
  return route.continue();
 };
 await page.route('**/api/actions',rejectSave);await toggle.click();
 await expect(page.getByText('Synthetic preference save rejected.',{exact:true})).toBeVisible();
 await expect(toggle).toBeChecked();
 await page.unroute('**/api/actions',rejectSave);await toggle.uncheck();
 await expect(toggle).not.toBeChecked();
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await expect(group).toHaveCount(0);
 const finalState=await control({op:'patch'});
 assert.equal(finalState.runtimeSends,0,'presentation never sends model input');
 assert.deepEqual(errors,[]);
 console.log('PASS: off/on/rejected save, final-only, expand/copy/reveal, export equality, reload/Everything, two-client isolation, active/error, focus/scroll deferral, media once/in order, mobile, zero model sends.');
}catch(error){
 console.error('Browser errors:',debugErrors,'Visible:',await debugPage?.locator('body').innerText().catch(()=>'<page unavailable>'));
 if(process.env.EVIDENCE_DIR&&debugPage){
  try{await mkdir(process.env.EVIDENCE_DIR,{recursive:true});await debugPage.screenshot({path:process.env.EVIDENCE_DIR+'/interim-failure.png',fullPage:true})}
  catch(captureError){console.error('Failure screenshot unavailable:',captureError.name)}
 }
 console.error('Fixture:',log);throw error;
}finally{await otherBrowser?.close();await browser.close();fixture.kill('SIGTERM');}
