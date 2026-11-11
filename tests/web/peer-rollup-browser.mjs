// Focused actual-host regression: older history must not erase an adjacent peer run.
import './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_PROOF_DIR;
assert.ok(output,'An owned evidence directory is required');mkdirSync(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/peer_preview_ui_server.py',root],{stdio:['ignore','pipe','inherit'],env:process.env});
const partial=process.env.PEER_PREVIEW_PARTIAL_RECEIVER==='1';
let browser,page;const observations=[],errors=[],commands=[];
try{
 const target=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture readiness timeout')),30000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}}})});
 browser=await chromium.launch({headless:true});
 for(const width of [1280,390]){
  const context=await browser.newContext({viewport:{width,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-peer-preview'}});
  await context.grantPermissions(['clipboard-read','clipboard-write']);
  page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/actions')commands.push(r.postDataJSON().action)});
  await page.goto(target);await showFixtureConversation(page,{navPinned:false});
  const facts=await (await page.request.get(new URL('/api/fixture/peerFacts',target).href)).json();
  const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
  // Drafts belong to each browser. A second context must not inherit the
  // fixture's one-time legacy draft migration from the first browser.
  const draft=`Unsent synthetic draft α at ${width}`;
  await composer.fill(draft);
  await expect(composer).toHaveDraft(draft);
  const before=await page.evaluate(()=>{const s=window.amplifier.getState();return {id:s.selectedSessionId,draft:s.view.draft}});
  const sourceRows=await page.evaluate(ids=>{const s=window.amplifier.getState(),session=s.sessions.find(x=>x.id===s.selectedSessionId);return {window:session.messageWindow,rows:session.messages.filter(row=>ids.includes(row.id)).map(row=>({id:row.id,inputId:row.inputId,via:row.via,inputOrigin:row.inputOrigin,attribution:row.attribution||null}))}},facts.originals);
  assert.deepEqual(sourceRows.rows.map(row=>row.id),facts.originals);
  const run=page.locator('.a-peer-run');
  const runCount=await run.count();
  if(runCount===1)await run.locator('.a-peer-run-summary').press('Enter');
  const original=page.locator(`[data-message-id="${facts.originals[0]}"]`);
  await expect(original).toBeVisible();
  await original.locator('.a-peer-summary').press('Enter');
  await expect(original.locator('h1')).toHaveText('Navigation review');
  assert.equal(await original.locator('pre code.language-python').textContent(),'def sample():\n    return "café α"\n\nprint(sample())\n');
  await original.getByRole('button',{name:'Copy peer message as Markdown',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(facts.text);
  assert.deepEqual(await page.evaluate(()=>{const s=window.amplifier.getState();return {id:s.selectedSessionId,draft:s.view.draft}}),before);
  await original.locator('.a-peer-summary').press('Enter');
  await original.scrollIntoViewIfNeeded();
  const ids=await page.locator('article.a-peer-message').evaluateAll(nodes=>nodes.map(n=>n.dataset.messageId));
  assert.deepEqual(ids,facts.originals);
  const geometry=await page.locator('article.a-peer-message').evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return {id:n.dataset.messageId,x:r.x,y:r.y,width:r.width,height:r.height}}));
  const sourceButtons=await page.getByRole('button',{name:/^Preview source chat:/}).count();
  await page.screenshot({path:join(output,`peer-rollup-${width}.png`)});
  const after=await (await page.request.get(new URL('/api/fixture/peerFacts',target).href)).json();
  assert.deepEqual(after.effects,[]);assert.deepEqual(after.subprocesses,[]);
  observations.push({width,partial,runCount,sourceButtons,ids,sourceRows,geometry,exactCopy:true,draftUnchanged:true,noRuntime:true});
  await context.close();page=null;
 }
 // No runtime action is implied by reading, disclosure or Copy.
 assert.ok(!commands.some(name=>['conversation.send','worker.spawn','session.warm','approval.respond','peer.preview','session.select'].includes(name)));
 assert.deepEqual(errors,[]);
 writeFileSync(join(output,'peer-rollup-result.json'),JSON.stringify({observations,commands,errors},null,2)+'\n');
 assert.ok(observations.every(row=>row.runCount===1&&row.sourceButtons===3),'PEER_ROLLUP_BOUNDARY_FAILURE: unchanged adjacent originals lost their grouped/source presentation');
 console.log(JSON.stringify({observations,passed:true}));
}catch(error){
 writeFileSync(join(output,'peer-rollup-failure.json'),JSON.stringify({error:String(error),observations,commands,errors},null,2)+'\n');
 if(page)await page.screenshot({path:join(output,'failure.png')}).catch(()=>{});
 throw error;
}finally{await browser?.close();fixture.kill();}
