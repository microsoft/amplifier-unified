// Run inside an owned DTU against device_pairing_ui_server.py only.
import { chromium } from 'playwright';
import { createHash, randomBytes } from 'node:crypto';
import assert from 'node:assert/strict';
const origin = process.env.TEMPO_PAIRING_FIXTURE_URL ?? 'http://127.0.0.1:8995';
assert.equal(new URL(origin).hostname, '127.0.0.1');
const browser = await chromium.launch({headless:true, args: ['--no-sandbox', '--single-process']});
const context = await browser.newContext();
const page = await context.newPage();
const post = async (path, body, headers = {}) => {
  const response = await fetch(origin + path, {method:'POST',headers:{'Content-Type':'application/json',...headers},body:JSON.stringify(body)});
  assert.equal(response.status, 200, `${path}: ${response.status}`); return response.json();
};
try {
  const verifier = randomBytes(32).toString('base64url');
  const challenge = createHash('sha256').update(verifier).digest('base64url');
  const request = await post('/api/devices/pair', {name:'Tempo browser acceptance', challenge});
  await page.goto(origin + request.verificationPath);
  assert.ok(page.url().includes('/login'));
  await page.locator('input[name=username]').fill('fixture');
  await page.locator('input[name=password]').fill('synthetic-only');
  await page.locator('button[type=submit]').click();
  await page.getByRole('heading', {name:'Connect a companion'}).waitFor();
  assert.ok((await page.locator('code').textContent()).includes(request.code));
  assert.deepEqual(await post('/api/devices/pair/token', {id:request.id,verifier}), {status:'pending'});
  await page.getByRole('button', {name:'Allow connection'}).click();
  await page.getByRole('heading', {name:'Connection approved'}).waitFor();
  const approved = await post('/api/devices/pair/token', {id:request.id,verifier});
  assert.equal(approved.scope,'companion-v1');
  const headers = {Authorization:'Bearer '+approved.token,'X-Amplifier-Client':'tempo-browser-acceptance','X-Amplifier-State-Transport':'delta-v1'};
  await post('/api/clients/attach',{clientId:'tempo-browser-acceptance',kind:'native',protocolVersion:1},headers);
  const denied = await fetch(origin+'/api/state',{headers}); assert.equal(denied.status,403);
  await page.getByRole('link', {name:'Manage connected devices'}).click();
  await page.getByRole('heading', {name:'Connected devices'}).waitFor();
  const row = page.locator('li').filter({hasText:'Tempo browser acceptance'});
  assert.equal(await row.count(),1);
  await page.screenshot({path:process.env.TEMPO_PAIRING_SCREENSHOT ?? '/tmp/tempo-pairing-devices.png'});
  await row.getByRole('button', {name:'Remove access'}).click();
  await page.waitForURL(origin+'/devices');
  await page.getByText('No connected devices.').waitFor();
  assert.equal((await fetch(origin+'/api/companion',{headers})).status,401);
  assert.equal((await post('/api/devices/pair/token',{id:request.id,verifier})).status,'revoked');
  console.log('PASS: browser sign-in, code comparison, explicit approval, restricted access, device listing, revocation');
} finally { await context.close(); await browser.close(); }
