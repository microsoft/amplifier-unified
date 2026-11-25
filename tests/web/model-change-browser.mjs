// Reopen the persisted native chat created by next_message_native_probe.py --model-divider.
import assert from 'node:assert/strict';
import {execFile, spawn} from 'node:child_process';
import {promisify} from 'node:util';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {readFile, mkdir, mkdtemp, rm, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createInterface} from 'node:readline';
import {chromium, expect} from '@playwright/test';
import {ownFixture, finishFixture} from './fixture-lifecycle.mjs';

const checkout = fileURLToPath(new URL('../../', import.meta.url));
const python = process.env.AMPLIFIER_TEST_PYTHON || checkout + '.venv/bin/python';
const probe = checkout + 'tests/fixtures/next_message_native_probe.py';
const scratch = process.argv[2] ? null : await mkdtemp(join(tmpdir(),'model-divider-'));
const seed = process.argv[2] || join(scratch,'chat');
try {
  if (scratch) await promisify(execFile)(python,[probe,seed,'--model-divider'],{timeout:180000});
  await verifyReopenedChat(seed);
} finally {
  if (scratch) await rm(scratch,{recursive:true,force:true});
}

async function verifyReopenedChat(seed) {
  const result = JSON.parse(await readFile(seed + '/result.json', 'utf8'));
  assert.ok(result.passed && result.cleanupConfirmed);
  const server = spawn(python,[probe, seed, '--serve-model-divider'], {stdio:['ignore','pipe','inherit']});
  const stop = ownFixture(server);
  const lines = createInterface({input:server.stdout});
  const evidence = process.env.AMPLIFIER_TEST_ARTIFACTS;
  const receipts = [];
  let browser, originalError;
  try {
    const handshake = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Reopened host timed out')), 30000);
      server.once('exit', () => { clearTimeout(timer); reject(new Error('Reopened host exited')); });
      lines.on('line', line => {
        if (!line.startsWith('{')) return;
        const record = JSON.parse(line);
        if (record.url && record.cookieValue) { clearTimeout(timer); resolve(record); }
      });
    });
    assert.equal(handshake.sessionId, result.sessionId);
    const expected = [
      ['changed-model', 'Model changed from first to second'],
      ['changed-connection', 'Connection changed from fixture to alternate'],
      ['changed-effort', 'Reasoning effort changed from low to high']
    ];
    async function openBrowser(width) {
      browser = await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined});
      const page = await browser.newPage({locale:'en-US',isMobile:width<500,hasTouch:width<500,viewport:{width,height:width<500?844:900}});
      await page.context().addCookies([{name:handshake.cookieName,value:handshake.cookieValue,url:handshake.url}]);
      await page.goto(handshake.url);
      await openChat(page);
      return page;
    }
    async function openChat(page) {
      await page.waitForFunction(() => window.amplifier?.getState()?.client?.id);
      const receipt = await page.evaluate(id => window.amplifier.dispatch('session.select',{id}), result.sessionId);
      assert.ok(receipt.accepted);
      await page.evaluate(() => window.amplifier.dispatch('view.update',{patch:{workSurface:'chat'}}));
      await expect(page.getByRole('log',{name:'Conversation messages'})).toHaveAttribute('data-session-id', result.sessionId);
    }
    async function checkDividers(page, phase) {
      const dividers = page.locator('[data-part="model-change"]');
      await expect(dividers).toHaveCount(expected.length);
      const observed = await dividers.evaluateAll(elements => elements.map(el => [
        el.nextElementSibling?.dataset.inputId,
        (el.querySelector('summary') || el).innerText.trim()
      ]));
      assert.deepEqual(observed, expected);
      receipts.push({phase, dividers:observed});
    }
    const options = p => p.getByRole('button',{name:'Model and reasoning settings',exact:true});
    const dialog = p => p.getByRole('dialog',{name:'Model for this chat',exact:true});
    const restore = p => dialog(p).getByRole('button',{name:/^Keep .+ · second$/});
    let page = await openBrowser(1200);
    await checkDividers(page,'restarted host');
    if (evidence) {
      await mkdir(evidence,{recursive:true});
      await page.locator('[data-part="model-change"]').first().evaluate(el => el.scrollIntoView({block:'center'}));
      await page.screenshot({path:evidence+'/model-change.png'});
    }
    await page.evaluate(() => window.amplifier.dispatch('session.draft',{}));
    await openChat(page);
    await checkDividers(page,'returned to chat');
    await options(page).click();
    const panel = dialog(page);
    await expect(panel.locator('#chat-provider')).toBeVisible();
    await expect(panel.locator('details.a-picker-advanced')).not.toHaveAttribute('open','');
    await expect(panel.getByRole('button',{name:'Refresh this connection'})).toBeHidden();
    const hint = p => p.locator('[data-next-message-impact="selection"]:visible');
    async function checkMeasured(phase, p) {
      const estimate = hint(p);
      await expect(estimate).toHaveCount(1);
      await expect(estimate).toContainText('reread earlier messages');
      await expect(estimate).toContainText('cost more');
      receipts.push({phase, estimate:await estimate.innerText()});
      if (evidence) await p.screenshot({path:evidence+'/'+phase+'.png'});
    }
    const revision = () => page.evaluate(id => window.amplifier.getState().sessions.find(s=>s.id===id).nextMessageRevision,result.sessionId);
    const effortControl=panel.locator('#chat-effort');
    await expect(effortControl).toBeVisible();
    await effortControl.focus();
    await page.keyboard.press('Home');
    await expect.poll(()=>page.evaluate(id=>window.amplifier.getState().sessions.find(s=>s.id===id).pendingModelSelection?.model,result.sessionId)).toBe('second');
    await expect(restore(page)).toHaveCount(0);

    const beforePick = await revision();
    const modelSelect = panel.locator('select#chat-model');
    await expect(panel.locator('#chat-provider option[value="fixture"]')).not.toContainText('May cost more');
    await expect(modelSelect.locator('option[value="first"]')).not.toContainText('May cost more');
    await panel.locator('#chat-provider').selectOption('fixture');
    await checkMeasured('picker-draft',page);
    assert.equal(await revision(),beforePick,'Choosing a connection before a model stays local');
    await modelSelect.selectOption('first');
    await expect(restore(page)).toBeVisible();
    await checkMeasured('picker-pending',page);
    await checkDividers(page,'pending choice');
    await panel.getByRole('button',{name:'Close chat options'}).click();
    await expect(hint(page)).toHaveCount(0);
    await browser.close();
    page = await openBrowser(390);
    await checkDividers(page,'new browser');
    await expect(hint(page)).toHaveCount(0);
    if (!await dialog(page).isVisible()) await options(page).click();
    await expect(restore(page)).toBeVisible();
    await checkMeasured('reopened-picker',page);
    // A separate bundle change can require rebuilding the prefix either way.
    const bundle=await page.evaluate(id=>window.amplifier.getState().sessions.find(s=>s.id===id).bundle,result.sessionId);
    async function setBundle(value){
      const response=await page.evaluate(([id,bundle])=>{
        const session=window.amplifier.getState().sessions.find(s=>s.id===id);
        return window.amplifier.dispatch('conversation.configure',{sessionId:id,expectedRevision:session.nextMessageRevision,bundle});
      },[result.sessionId,value]);
      assert.ok(response.accepted);
    }
    await setBundle(bundle.replace('bundle-a.yaml','bundle-b.yaml'));
    await expect(restore(page)).toHaveCount(0);
    await setBundle(bundle);
    await expect(restore(page)).toBeVisible();
    await restore(page).focus();
    await page.keyboard.press('Enter');
    await expect(restore(page)).toHaveCount(0);
    await expect(dialog(page).locator('select#chat-model')).toHaveValue('second');
    await expect(dialog(page).locator('#chat-provider')).toHaveValue('alternate');
    await dialog(page).locator('details.a-picker-advanced > summary').focus();
    await page.keyboard.press('Enter');
    const effort = dialog(page).locator('[data-composer-page="effort"]');
    await expect(effort).toBeVisible();
    await expect(effort).toContainText(/(?:Saved effort: |Effort\s*)low/i);
    await page.reload();
    await openChat(page);
    await checkDividers(page,'after restore and reload');
    const calls = (await readFile(seed+'/calls.jsonl','utf8')).trim().split('\n').length;
    assert.equal(calls, result.calls.length, 'Picking, reopening, and restoring should make zero model calls');
    if (evidence) {
      const client = await page.evaluate(async () => (await (await fetch('/client-component.json')).json()).source);
      await writeFile(evidence+'/browser-result.json',JSON.stringify({
        sessionId:result.sessionId, client, receipts, extraModelCalls:calls-result.calls.length,
      },null,2));
    }
    console.log('Model changes persisted at the three expected inputs. Pending settings restored from a new browser.');
  } catch (error) {
    originalError = error;
    throw error;
  } finally {
    lines.close();
    await finishFixture({label:'Model-change reopen',close:browser?[['Browser',() => browser.close()]]:[],stop,originalError});
  }
}
