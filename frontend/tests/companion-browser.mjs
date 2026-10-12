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
 // Unavailable links report failure without clearing the target or selecting a substitute.
 for (const target of ['unavailable-link-target', '00000000-0000-4000-8000-000000000001']) {
  await page.goto(ready.url + '/?chat=' + target);
  await expect(page.getByRole('alert')).toContainText('Select or create a conversation first.');
  await expect(page).toHaveURL(ready.url + '/?chat=' + target);
  assert.equal(await page.evaluate(() => window.amplifier.getState().selectedSessionId), ready.destination);
 }
 // A fresh browser also works with the DTU's single-process Chromium mode.
 await browser.close();
 browser = await chromium.launch({headless: true, args: process.env.DTU_CHROMIUM_SINGLE_PROCESS === '1'
  ? ['--no-zygote', '--single-process', '--disable-gpu'] : []});
 const loginContext = await browser.newContext();
 const login = await loginContext.newPage();
 const submissions = [];
 login.on('request', request => {
  if (request.method() === 'POST' && request.url().endsWith('/api/actions')) submissions.push(request.postDataJSON());
 });
 await login.goto(ready.url + '/?chat=' + ready.destination);
 await expect(login).toHaveURL(/\/login\?next=/);
 await login.getByLabel('Username').fill('fixture-user');
 await login.getByLabel('Password').fill('fixture-only');
 await login.getByRole('button', {name:'Sign in', exact:true}).click();
 await expect(login.locator('[data-navigation-select][aria-current=page]').first()).toContainText('Companion destination');
 await expect(login).toHaveURL(ready.url + '/');
 assert.equal(submissions.filter(row => row.action === 'conversation.send').length, 0);
 assert.equal(actions.filter(row => row.action === 'conversation.send').length, 0);
 console.log('Chat links passed: exact target, URL cleanup, reload, independent windows, unavailable/deleted targets, login round trip, no turn or polling.');
} finally {
 await browser?.close();
 fixture.kill();
}
