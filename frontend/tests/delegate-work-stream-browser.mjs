// Presentation-only red regression for the approved interleaved Work layout.
// This deliberately uses body-bearing fixture nodes. It does NOT qualify capture,
// canonical detail resolution or cold reopen; test_delegate_work_stream.py owns
// that independent boundary. Run against built assets in the owned test instance:
// node tests/delegate-work-stream-browser.mjs (from frontend/).
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium, expect} from '@playwright/test';
import {mkdirSync,writeFileSync} from 'node:fs';
import {showFixtureConversation} from './browser-chat-pages.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
// Word ranges detect the one-letter-per-line regression that an element box
// inside the viewport cannot detect. Hit tests also reject floating overlays.
function measureOwner(element) {
  const button=element.closest('button'),bounds=button.getBoundingClientRect();
  const words=[],problems=[],walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT);
  for(let text=walker.nextNode();text;text=walker.nextNode()) {
    for(const match of text.textContent.matchAll(/[\p{L}\p{N}]+/gu)) {
      const range=document.createRange();
      range.setStart(text,match.index);range.setEnd(text,match.index+match[0].length);
      const rects=[...range.getClientRects()].filter(r=>r.width&&r.height);
      const lines=new Set(rects.map(r=>Math.round(r.top)));
      if(lines.size!==1)problems.push(`split word: ${match[0]}`);
      for(const rect of rects) {
        if(rect.left<Math.max(0,bounds.left)-1||rect.right>Math.min(innerWidth,bounds.right)+1||
           rect.top<Math.max(0,bounds.top)-1||rect.bottom>Math.min(innerHeight,bounds.bottom)+1)
          problems.push(`clipped word: ${match[0]}`);
        for(const fraction of [0.2,0.5,0.8]) {
          const hit=document.elementFromPoint(rect.left+rect.width*fraction,rect.top+rect.height/2);
          if(!hit||!button.contains(hit))problems.push(`covered word: ${match[0]}`);
        }
      }
      words.push({text:match[0],lines:lines.size,rects:rects.map(r=>({x:r.x,y:r.y,width:r.width,height:r.height}))});
    }
  }
  if(!words.length)problems.push('No owner words measured');
  return {text:element.textContent,words,problems};
}
const fixture = spawn(root + '.venv/bin/python', ['-u', root + 'tests/fixtures/browser_detail_server.py', root],
  {stdio: ['ignore', 'pipe', 'inherit']});
let browser;
try {
  const ready = await new Promise((resolve, reject) => {
    let text = '';
    const timer = setTimeout(() => reject(Error('Fixture timeout')), 20000);
    fixture.once('error', error => {clearTimeout(timer); reject(error)});
    fixture.once('exit', code => {clearTimeout(timer); reject(Error(`Fixture exited: ${code}`))});
    fixture.stdout.on('data', chunk => {
      text += chunk;
      for (const line of text.split('\n')) {
        try {const value = JSON.parse(line); if (value.port) {clearTimeout(timer); resolve(value)}} catch {}
      }
    });
  });
  const url = `http://127.0.0.1:${ready.port}`;
  const headers = {Authorization: 'Bearer fixture-detail-token', 'content-type': 'application/json'};
  const control = async body => {
    const result = await fetch(url + '/api/fixture/control', {method: 'POST', headers, body: JSON.stringify(body)});
    assert.equal(result.status, 200, await result.clone().text());
    return result.json();
  };
  const {alpha} = await control({op: 'heavy', messages: 2, otherMessages: 2, chars: 50, nodes: 0});
  const start = 1791409080, turnId = 'stream-turn';
  const worker = {id: 'stream-worker', kind: 'worker', sessionId: 'review', parentId: 'stream-request',
    turnId, label: 'Review', phase: 'completed', startedAt: start + 1, endedAt: start + 36,
    summary: 'SUMMARY-DECOY must not replace actual messages'};
  const common = {turnId, rootSessionId: alpha, phase: 'completed'};
  const request = {...common, id: 'stream-request', sessionId: alpha, kind: 'tool', label: 'delegate',
    startedAt: start + 1, endedAt: start + 20,
    input: JSON.stringify({agent: 'review', instruction: 'STREAM REQUEST café'}),
    output: JSON.stringify({success: true, output: {response: 'RECEIPT-DECOY', session_id: 'review'}})};
  const read = {...common, id: 'stream-read', parentId: worker.id, sessionId: 'review', kind: 'tool',
    label: 'read_file', startedAt: start + 12, endedAt: start + 13,
    input: JSON.stringify({file_path: '/fixture/checklist.txt'}), output: 'CHILD READ RESULT'};
  const search = {...common, id: 'stream-search', parentId: worker.id, sessionId: 'review', kind: 'tool',
    label: 'grep', startedAt: start + 14, endedAt: start + 15,
    input: JSON.stringify({pattern: 'check', path: '/fixture'}), output: 'CHILD SEARCH RESULT'};
  const mainModel = {...common, id: 'stream-main-model', sessionId: alpha, kind: 'llm', label: 'Model call',
    provider: 'fixture', model: 'main-model', startedAt: start + 24, endedAt: start + 26};
  const followup = {...common, id: 'stream-followup', sessionId: alpha, kind: 'tool', label: 'live_worker',
    exchangeOwnerId: worker.id, startedAt: start + 28, endedAt: start + 29,
    input: JSON.stringify({action: 'message', session_id: 'review', text: 'STREAM FOLLOW-UP'}),
    output: JSON.stringify({accepted: true, completed: false})};
  const childModel = {...common, id: 'stream-child-model', parentId: worker.id, sessionId: 'review',
    kind: 'llm', label: 'Model call', provider: 'fixture', model: 'review-model',
    startedAt: start + 32, endedAt: start + 34};
  const report = (id, at, text) => ({...common, id, parentId: worker.id, sessionId: 'review',
    kind: 'exchange', startedAt: start + at, exchange: {id, delegateResponse: text, report: true}});
  // Scrambled storage order is intentional; presentation follows recorded order.
  const nodes = [request, worker, followup, mainModel, childModel,
    report('stream-reply-two', 36, 'STREAM SECOND REPLY'), search, read,
    report('stream-reply-one', 20, 'STREAM FIRST REPLY')];
  await control({op: 'patch', sessions: {[alpha]: {
    messages: [{id: 'question', role: 'user', text: 'Review this checklist.', createdAt: start},
      {id: 'answer', role: 'assistant', text: 'Review finished.', createdAt: start + 40}],
    execution: {turns: [{id: turnId, anchorMessageId: 'question', phase: 'completed',
      startedAt: start, endedAt: start + 40}], nodes}
  }}});
  browser = await chromium.launch({headless: true, args: ['--no-zygote', '--single-process', '--disable-gpu']});
  const page = await browser.newPage({extraHTTPHeaders: headers});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  mkdirSync(root + 'test-results', {recursive: true});
  for (const [name, viewport] of [['desktop', {width: 1280, height: 1000}], ['mobile', {width: 390, height: 844}]]) {
    await page.setViewportSize(viewport);
    await page.goto(url);
    await showFixtureConversation(page);
    await page.getByRole('textbox', {name: 'Message Amplifier'}).waitFor();
    const work = page.locator(`[data-group-id="${turnId}@question"]`);
    const toggle = work.locator(':scope > button');
    await expect(toggle).toHaveCount(1);
    if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
    await expect(work.locator('[data-node-id="stream-main-model"]')).toBeVisible();
    // No intervening Delegate or Worker activity drawer is needed to read it.
    await expect(work.getByRole('button', {name: /Worker activity/})).toHaveCount(0);
    await expect(work.locator('[data-kind="worker"] > button')).toHaveCount(0);
    const rows = [work.getByText('STREAM REQUEST café', {exact: true}),
      work.locator('[data-node-id="stream-read"]'), work.locator('[data-node-id="stream-search"]'),
      work.getByText('STREAM FIRST REPLY', {exact: true}), work.locator('[data-node-id="stream-main-model"]'),
      work.getByText('STREAM FOLLOW-UP', {exact: true}), work.locator('[data-node-id="stream-child-model"]'),
      work.getByText('STREAM SECOND REPLY', {exact: true})];
    for (const row of rows) {
      await expect(row).toHaveCount(1);
      await expect(row).toBeVisible();
    }
    // Compare one layout frame: chat scroll restoration can move viewport
    // coordinates between separate boundingBox requests without any overlap.
    const handles = await Promise.all(rows.map(row => row.elementHandle()));
    const boxes = await page.evaluate(elements => elements.map(element =>
      element?.isConnected ? element.getBoundingClientRect().toJSON() : null), handles);
    await Promise.all(handles.map(handle => handle?.dispose()));
    let bottom = -Infinity;
    for (const box of boxes) {
      assert.ok(box && box.y >= bottom - 1, 'Exchange and operations must be vertically ordered without overlap');
      bottom = box.y + box.height;
    }
    await expect(work).toContainText('Main chat → Delegate');
    await expect(work).toContainText('Delegate → Main chat');
    await expect(work).not.toContainText('SUMMARY-DECOY');
    await expect(work).not.toContainText('RECEIPT-DECOY');
    const operations = [rows[1], rows[2], rows[4], rows[6]];
    await expect(operations[2].locator(':scope > button')).toContainText('Main chat');
    for (const operation of [operations[0], operations[1], operations[3]])
      await expect(operation.locator(':scope > button')).toContainText('Delegate');
    for (const operation of operations)
      await expect(operation.locator(':scope > button')).toHaveAttribute('aria-expanded', 'false');
    await operations[0].locator(':scope > button').click();
    await expect(operations[0]).toContainText('CHILD READ RESULT');
    for (const operation of operations.slice(1))
      await expect(operation.locator(':scope > button')).toHaveAttribute('aria-expanded', 'false');
    await operations[0].locator(':scope > button').click();
    await toggle.click();
    await expect(rows[0]).toBeHidden();
    await toggle.click();
    await expect(rows[0]).toBeVisible();
    await expect(operations[0].locator(':scope > button')).toHaveAttribute('aria-expanded', 'false');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'No horizontal overflow');
    const readability=[];
    for(const operation of operations) {
      const owner=operation.locator(':scope > button > .a-work-actor');
      await owner.evaluate(element=>element.scrollIntoView({block:'center',inline:'nearest'}));
      // Wait for layout/scroll settlement without relying on a fixed sleep.
      await expect.poll(async()=> (await owner.evaluate(measureOwner)).problems).toEqual([]);
      readability.push(await owner.evaluate(measureOwner));
    }
    if(name==='mobile') {
      const owner=operations[0].locator(':scope > button > .a-work-actor');
      await owner.evaluate(element=>element.scrollIntoView({block:'center',inline:'nearest'}));
      await expect.poll(async()=> (await owner.evaluate(measureOwner)).problems).toEqual([]);
      // Negative controls: the same oracle must reject both the old narrow
      // column symptom and text covered by another UI element.
      const style=await owner.getAttribute('style');
      await owner.evaluate(element=>{element.style.maxWidth='8px';element.style.overflowWrap='anywhere'});
      const split=await owner.evaluate(measureOwner);
      assert.ok(split.problems.some(value=>value.startsWith('split word:')),'Readability check must detect split owner words');
      await owner.evaluate((element,style)=>style===null?element.removeAttribute('style'):element.setAttribute('style',style),style);
      await owner.evaluate(element=>element.scrollIntoView({block:'center',inline:'nearest'}));
      await expect.poll(async()=> (await owner.evaluate(measureOwner)).problems).toEqual([]);
      await owner.evaluate(element=>{
        const rect=element.getBoundingClientRect(),cover=document.createElement('div');
        cover.id='readability-negative-cover';
        Object.assign(cover.style,{position:'fixed',left:rect.left+'px',top:rect.top+'px',width:rect.width+'px',height:rect.height+'px',zIndex:'2147483647',background:'white'});
        document.body.append(cover);
      });
      const covered=await owner.evaluate(measureOwner);
      assert.ok(covered.problems.some(value=>value.startsWith('covered word:')),'Readability check must detect obscured owner text');
      await page.locator('#readability-negative-cover').evaluate(element=>element.remove());
      await expect.poll(async()=> (await owner.evaluate(measureOwner)).problems).toEqual([]);
      writeFileSync(root+'test-results/delegate-work-stream-negative-controls.json',JSON.stringify({split,covered},null,2));
      await page.screenshot({path:root+'test-results/delegate-work-stream-mobile-owner.png'});
    }
    writeFileSync(root+`test-results/delegate-work-stream-${name}-readability.json`,JSON.stringify(readability,null,2));
    await work.scrollIntoViewIfNeeded();
    await page.screenshot({path: root + `test-results/delegate-work-stream-${name}.png`, fullPage: true});
  }
  // The separate live scenario drives actual metadata-only observer hooks and
  // UUID-stamped canonical records through the registered-browser UI. The
  // earlier scene alone is only presentation proof; this is not cold placement.
  const instruction='OBSERVER REQUEST café\n'+('request body 日本語\n'.repeat(1200))+'REQUEST-END';
  const reply='OBSERVER REPLY\n'+('reply body café\n'.repeat(1500))+'REPLY-END';
  const fieldRequests=[];
  page.on('request',request=>{
    if(request.url().includes('/api/conversation/detail?'))fieldRequests.push({
      query:new URL(request.url()).searchParams,client:request.headers()['x-amplifier-client']});
  });
  // The already-open page can read published observer records before control
  // returns; observe that boundary before triggering it, including warm reads.
  await control({op:'delegate-observer',instruction,reply,olderRecords:true});
  await page.setViewportSize({width:1280,height:1000});
  await page.reload();
  await showFixtureConversation(page);
  const observed=page.locator('[data-group-id="observer-turn@observer-question"]');
  const observedToggle=observed.locator(':scope > button');
  await expect(observedToggle).toBeVisible();
  if(await observedToggle.getAttribute('aria-expanded')!=='true')await observedToggle.click();
  // This fixture deliberately places the stamped exchange beyond the recent
  // window. Follow its explicit bounded lookup before copying full fields.
  const earlier=observed.getByRole('button',{name:'Search older saved details',exact:true});
  await expect(earlier).toHaveCount(1);
  await earlier.click();
  await expect(observed.getByRole('region',{name:'Request',exact:true})).toContainText('OBSERVER REQUEST café');
  await expect(observed.getByRole('region',{name:'Reply',exact:true})).toContainText('OBSERVER REPLY');
  await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{
    writeText:async text=>{window.copied=text}
  }}));
  await observed.getByRole('button',{name:'Copy request',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>window.copied)).toBe(instruction);
  await observed.getByRole('button',{name:'Copy reply',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>window.copied)).toBe(reply);
  assert.ok(fieldRequests.some(item=>item.query.get('part')==='tool'&&item.client),'Real browser must follow saved-tool lookup');
  assert.ok(fieldRequests.some(item=>item.query.get('field')==='delegateResponse'&&Number(item.query.get('offset'))>0),'Long reply must use more than one field chunk');
  const observerInspection=await control({op:'inspect-work-source'});
  const source=observerInspection.sessions.find(row=>row.id===alpha);
  assert.ok(source.execution.nodes.every(row=>!Object.hasOwn(row,'input')&&!Object.hasOwn(row,'output')),
    'UI inspection must not inject canonical bodies into live observer metadata');
  // The original V3 requires real actor labels, not a Main chat guess when
  // only a nested recipient is loaded. These are synthetic projection inputs,
  // independent of the actual observer/copy case above.
  const orphanRequest={...common,id:'orphan-request',kind:'tool',label:'delegate',
    sessionId:'outside-loaded-parent',startedAt:start+2,endedAt:start+7,
    input:JSON.stringify({instruction:'NESTED REQUEST'}),output:JSON.stringify({success:true,output:{response:'NESTED REPLY'}})};
  const orphanWorker={...worker,id:'orphan-worker',sessionId:'inner-review',parentId:orphanRequest.id,
    label:'Inner review',startedAt:start+2,endedAt:start+7};
  const orphanOperation={...read,id:'orphan-read',sessionId:orphanWorker.sessionId,parentId:orphanWorker.id,
    startedAt:start+3,endedAt:start+4};
  await control({op:'patch',sessions:{[alpha]:{
    messages:[{id:'question',role:'user',text:'Inspect a nested delegate.',createdAt:start}],
    execution:{turns:[{id:turnId,anchorMessageId:'question',phase:'completed',startedAt:start,endedAt:start+8}],
      nodes:[orphanRequest,orphanWorker,orphanOperation]}
  }}});
  for(const [name,viewport] of [['desktop',{width:1280,height:1000}],['mobile',{width:390,height:844}]]) {
    await page.setViewportSize(viewport);
    await page.reload();await showFixtureConversation(page);
    const work=page.locator(`[data-group-id="${turnId}@question"]`),toggle=work.locator(':scope > button');
    await expect(toggle).toBeVisible();
    if(await toggle.getAttribute('aria-expanded')!=='true')await toggle.click();
    const request=work.getByRole('region',{name:'Request',exact:true});
    const reply=work.getByRole('region',{name:'Reply',exact:true});
    await expect(request).toContainText('NESTED REQUEST');
    await expect(reply).toContainText('NESTED REPLY');
    await request.scrollIntoViewIfNeeded();
    await page.screenshot({path:root+`test-results/delegate-work-stream-attribution-${name}.png`});
    await expect(request.locator('header')).toContainText('Worker (owner unavailable) → Delegate');
    await expect(reply.locator('header')).toContainText('Delegate → Worker (owner unavailable)');
    await expect(request.locator('header')).not.toContainText('Main chat');
    await expect(reply.locator('header')).not.toContainText('Main chat');
    const operation=work.locator('[data-node-id="orphan-read"]');
    await expect(operation.locator(':scope > button')).toHaveAttribute('aria-expanded','false');
    await expect(operation.locator('.a-work-actor')).toContainText('Delegate · Inner review');
    await request.scrollIntoViewIfNeeded();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Nested attribution must not overflow');
    await page.screenshot({path:root+`test-results/delegate-work-stream-attribution-${name}.png`});
    await toggle.click();
    await expect(request).toBeHidden();await expect(reply).toBeHidden();
  }
  assert.deepEqual(errors, []);
  const inspection = await control({op: 'inspect-work-source'});
  assert.equal(inspection.runtimeStarts, 0);
  assert.equal(inspection.runtimeSends, 0);
  console.log('PASS: synthetic interleaving/layout/actor attribution plus registered-browser stamped-observer long exchange reads; NOT cold-turn association or real-provider acceptance.');
} finally {
  await browser?.close();
  fixture.kill();
}
