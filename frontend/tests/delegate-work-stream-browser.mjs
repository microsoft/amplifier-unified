// Presentation-only red regression for the approved interleaved Work layout.
// This deliberately uses body-bearing fixture nodes. It does NOT qualify capture,
// canonical detail resolution or cold reopen; test_delegate_work_stream.py owns
// that independent boundary. Run against built assets in the owned test instance:
// node tests/delegate-work-stream-browser.mjs (from frontend/).
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium, expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';

const root = fileURLToPath(new URL('../../', import.meta.url));
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
    let bottom = -Infinity;
    for (const row of rows) {
      await expect(row).toHaveCount(1);
      await expect(row).toBeVisible();
      const box = await row.boundingBox();
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
    await work.scrollIntoViewIfNeeded();
    await page.screenshot({path: root + `test-results/delegate-work-stream-${name}.png`, fullPage: true});
  }
  assert.deepEqual(errors, []);
  const inspection = await control({op: 'inspect-work-source'});
  assert.equal(inspection.runtimeStarts, 0);
  assert.equal(inspection.runtimeSends, 0);
  console.log('PASS presentation-only: interleaving, independent operation disclosure, narrow layout; NOT capture/persistence acceptance.');
} finally {
  await browser?.close();
  fixture.kill();
}
