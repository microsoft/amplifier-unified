// Built UI, real action receipt/rejection, no installation or restart effects.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/update_restart_server.py',import.meta.url))]);
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/update-restart';
let browser,page,log='';const result={status:'pending',realHTTP:true,activationEffects:false};
fixture.stderr.on('data',data=>log+=data);
try{
 await mkdir(out,{recursive:true});
 const boot=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error(log||'Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error(`Fixture exited ${code}: ${log}`))});fixture.stdout.on('data',data=>{text+=data;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}})});
 assert.equal(boot.package,fileURLToPath(new URL('../../amplifier_web/__init__.py',import.meta.url)));
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(boot.url);await page.waitForSelector('#amp-one');await openSettingsPage(page,'updates');
 const blockers=page.locator('[data-part="update-blockers"]'),restart=page.locator('.a-update-force-restart'),entry=restart.locator('[data-action="updates.forceRestart"]');
 await expect(entry).toHaveText('Interrupt work and restart');await expect(entry).toBeVisible();
 assert.equal(await entry.evaluate(button=>!!button.closest('[data-part="update-blockers"]')),false);
 await expect(restart).toContainText('Restart into prepared Amplifier 99.0.0.');
 await expect(restart).toContainText('Amplifier 99.0.1 is available, but this action uses the prepared version above.');
 await expect(restart).toContainText('will not be replayed automatically');
 await expect(entry).toHaveAccessibleDescription(/Running chats, workers, tools, and calls will be interrupted/);
 await expect(page.getByRole('alertdialog',{name:'Confirm update restart'})).toHaveCount(0);
 const evidence=async()=>{const response=await page.request.get(boot.url+'/fixture/update-restart');assert.ok(response.ok());return response.json()};
 await blockers.locator('summary').click();await expect(blockers).toContainText('Restarting does not recover held settings');
 await blockers.locator('summary').click();assert.equal((await evidence()).calls.length,0,'Inspect-only disclosure has no restart effect');
 // A real public stale-target request must be refused before activation.
 const oldTarget=(await evidence()).target;
 assert.ok((await page.request.post(boot.url+'/fixture/update-restart')).ok());
 const stale=await page.request.post(boot.url+'/api/actions',{data:{id:'stale-prepared-target',action:'updates.forceRestart',args:{...oldTarget,confirm:true}}});
 assert.equal(stale.status(),409);assert.match((await stale.json()).error,/staged update or host changed/);
 assert.equal((await evidence()).calls.length,0);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().updates.forceRestartTarget.attemptId)).toBe('qualified-two');
 for(const width of [1280,390]){
  await page.setViewportSize({width,height:900});
  await expect(entry).toBeEnabled();await expect(entry).toHaveText('Interrupt work and restart');
  const target=(await evidence()).target;
  let release;const held=new Promise(resolve=>release=resolve),requests=[];
  const hold=async route=>{const body=route.request().postDataJSON();if(body?.action==='updates.forceRestart'){requests.push(body);await held}await route.continue()};
  await page.route('**/api/actions',hold);
  const response=page.waitForResponse(r=>r.url().endsWith('/api/actions')&&r.request().method()==='POST'&&r.request().postDataJSON()?.action==='updates.forceRestart');
  try{
   // Same-task repeated events exercise the synchronous ref fence, not only
   // React's subsequently committed disabled state. Narrow view uses a real click.
   if(width===1280)await entry.evaluate(button=>{button.click();button.click();button.click()});else await entry.click();
   await expect.poll(()=>requests.length).toBe(1);await expect(entry).toBeDisabled();
  }finally{release();await page.unroute('**/api/actions',hold)}
  const receipt=await(await response).json();assert.equal(receipt.accepted,true);assert.ok(receipt.requestId);
  await expect(restart.getByRole('alert')).toContainText('update operation');await expect(entry).toBeEnabled();
  const observed=await evidence();const last=observed.calls.at(-1);
  assert.deepEqual(last.args,{...target,confirm:true});assert.equal(last.requestId,receipt.requestId);assert.equal(observed.unknown,true);
  assert.equal(requests[0].id,receipt.requestId);assert.deepEqual(observed.result,{requestId:receipt.requestId,status:'rejected',detail:'An update operation is already running. Review it before confirming again.'});
  assert.ok(await restart.evaluate(element=>element.scrollWidth<=element.clientWidth+1),'Restart warning and action fit the viewport');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No page overflow');
  await page.screenshot({path:`${out}/confirmed-${width}.png`});
 }
 assert.equal((await evidence()).calls.length,2);assert.deepEqual(errors,[]);
 result.status='passed';result.checks={oneActionPlacement:true,inspectNoEffect:true,preparedNotLatest:true,synchronousBusyLatch:true,staleTargetBackendRefusal:true,exactReceipt:true,asyncRejectionRecovery:true,desktopAndMobile:true,unknownFencePreserved:true};
}catch(error){result.status='failed';result.error=String(error);if(page)await page.screenshot({path:out+'/failed.png'});throw error}
finally{
 try{await browser?.close()}
 finally{
  fixture.kill('SIGTERM');
  await new Promise(resolve=>{if(fixture.exitCode!==null)return resolve();const timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve()},5000);fixture.once('exit',()=>{clearTimeout(timer);resolve()})});
  result.fixtureLog=log;await writeFile(out+'/result.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }
}
