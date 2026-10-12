import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium, expect} from '@playwright/test';

const fixture = spawn(fileURLToPath(new URL('../../.venv/bin/python', import.meta.url)),
 [fileURLToPath(new URL('../../tests/fixtures/companion_ui_server.py', import.meta.url))],
 {stdio: ['ignore', 'pipe', 'inherit']});
let browser;
try {
 const ready = await new Promise((resolve, reject) => {
  let output = '';
  const timer = setTimeout(() => reject(Error('Fixture timeout')), 15000);
  fixture.stdout.on('data', chunk => {
   output += chunk;
   for (const line of output.split('\n')) try {
    const value = JSON.parse(line);
    if (value.url) { clearTimeout(timer); resolve(value); }
   } catch {}
  });
  fixture.once('exit', code => reject(Error('Fixture exit ' + code)));
 });
 browser = await chromium.launch({headless: true, args: process.env.DTU_CHROMIUM_SINGLE_PROCESS === '1'
  ? ['--no-zygote', '--single-process', '--disable-gpu'] : []});
 const context = await browser.newContext({extraHTTPHeaders: {Authorization: 'Bearer fixture-browser-control-token'}});
 const ordinary = await context.newPage(), ordinaryActions = [], companionRequests = [];
 context.on('request', request => {
  if (new URL(request.url()).pathname === '/api/companion') companionRequests.push(request.url());
 });
 ordinary.on('request', request => {
  if (request.url().endsWith('/api/actions') && request.method() === 'POST') ordinaryActions.push(request.postDataJSON());
 });
 await ordinary.goto(ready.url + '/');
 await expect(ordinary.locator('[data-navigation-select][aria-current=page]').first()).toContainText('Settings test');
 assert.equal(ordinaryActions.filter(row => row.action === 'session.select').length, 0, 'Normal launch does not navigate');
 const page = await context.newPage(), errors = [], actions = [];
 page.on('pageerror', error => errors.push(error.message));
 page.on('request', request => {
  if (request.url().endsWith('/api/actions') && request.method() === 'POST') actions.push(request.postDataJSON());
 });
 await page.goto(ready.url + '/?chat=' + encodeURIComponent(ready.destination));
 await expect(page.locator('[data-navigation-select][aria-current=page]').first()).toContainText('Companion destination');
 await expect(page).toHaveURL(ready.url + '/');
 assert.equal(actions.filter(row => row.action === 'session.select' && row.args.id === ready.destination).length, 1);
 assert.equal(actions.filter(row => row.action === 'conversation.send').length, 0, 'Opening a notification cannot send a turn');
 await page.reload();
 await expect(page.locator('[data-navigation-select][aria-current=page]').first()).toContainText('Companion destination');
 assert.equal(actions.filter(row => row.action === 'session.select' && row.args.id === ready.destination).length, 1, 'Reload does not repeat navigation');
 await ordinary.reload();
 await expect(ordinary.locator('[data-navigation-select][aria-current=page]').first()).toContainText('Settings test');
 assert.equal(ordinaryActions.filter(row => row.action === 'conversation.send').length, 0);
 assert.deepEqual(companionRequests, [], 'Browser clients never poll Tempo summaries');
 assert.deepEqual(errors, []);
 console.log('Companion browser passed: exact chat, query removed, reload persistence, other browser unaffected, no turn sent, no Tempo polling.');
} finally {
 await browser?.close();
 fixture.kill();
}
