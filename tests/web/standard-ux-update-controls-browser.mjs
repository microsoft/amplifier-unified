// Four input contexts, real action admission, synthetic busy-lock activation refusal.
// No installation, promotion, restart, or real provider effects.
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!out)throw Error('Owned AMPLIFIER_TEST_ARTIFACTS is required');
await mkdir(out,{recursive:true});
const variants=[['desktop-pointer',1280,950,'pointer'],['desktop-keyboard',1280,950,'keyboard'],['narrow-touch',390,844,'touch'],['narrow-keyboard',390,844,'keyboard']];
const results=[];
for(const [id,width,height,mode] of variants){
 let fixture,browser,page,logs='';const row={id,status:'pending',context:'synthetic-update-refusal',activationEffects:false,limits:['Keyboard activation uses explicit focus plus Enter; full sequential Tab traversal not claimed.','No actual installation/restart; real manager rejects at held operation lock.']},errors=[],external=[],actions=[];
 try{
  fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/update_restart_server.py']);
  fixture.stderr.on('data',chunk=>logs+=chunk);
  const boot=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data)}}catch{}})});
  assert.equal(boot.package,root+'amplifier_web/__init__.py');
  browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
  page=await browser.newPage({viewport:{width,height},hasTouch:mode==='touch',isMobile:mode==='touch',extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page.setDefaultTimeout(6000);
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')actions.push(request.postDataJSON())});
  await page.route('**/*',route=>{const url=new URL(route.request().url());if(['http:','https:'].includes(url.protocol)&&url.hostname!=='127.0.0.1'){external.push(url.origin);return route.abort()}return route.continue()});
  const activate=async control=>{await expect(control).toBeVisible();await expect(control).toBeEnabled();if(mode==='keyboard'){await control.focus();await expect(control).toBeFocused();await control.press('Enter')}else if(mode==='touch')await control.tap();else await control.click()};
  await page.goto(boot.url);await page.waitForSelector('#amp-one');
  // Use the selected client's maintained App options/Settings entry, with every
  // gesture still owned by this pointer, keyboard or touch variant (no dispatch).
  await openSettingsPage(page,'updates',{activate:(_label,control)=>activate(control)});
  await expect(page.locator('.a-settings-experience')).toHaveAttribute('data-settings-page','updates');
  const state=async()=>{const response=await page.request.get(boot.url+'/fixture/update-restart');assert.ok(response.ok());return response.json()};
  const blockers=page.locator('[data-part="update-blockers"]'),restart=page.locator('.a-update-force-restart'),entry=restart.locator('[data-action="updates.forceRestart"]');
  await expect(entry).toHaveText('Interrupt work and restart');await expect(entry).toBeVisible();
  assert.equal(await entry.evaluate(button=>!!button.closest('[data-part="update-blockers"]')),false);
  await expect(restart).toContainText('Restart into prepared Amplifier 99.0.0.');
  await expect(restart).toContainText('Amplifier 99.0.1 is available, but this action uses the prepared version above.');
  await expect(entry).toHaveAccessibleDescription(/interrupted work will not be replayed automatically/);
  await expect(page.getByRole('alertdialog',{name:'Confirm update restart'})).toHaveCount(0);
  await activate(blockers.locator('summary'));
  await expect(blockers.locator(':scope>summary')).toHaveText('View work delaying the update');
  const realRows=await page.evaluate(()=>window.amplifier.getState().updates.blockers);
  assert.ok(realRows.length,'Positive real host rows precede flat-disclosure assertions');
  await expect(blockers.locator(':scope>ul>li')).toHaveText(realRows.map(item=>item.label+(item.sessionId?' — '+(item.title||'Open conversation'):'')));
  await expect(blockers.locator('h4,section,ul ul,details')).toHaveCount(0);
  await expect(blockers).not.toContainText('Restarting does not recover held settings');
  await expect(restart).toContainText('You can wait for work to finish instead.');
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().updates.currentStatus.status)).toBe('action-required');
  await expect(page.locator('[data-part="application-update"]').first()).toContainText('Update needs attention');
  await activate(blockers.locator('summary'));assert.equal((await state()).calls.length,0,'Inspecting blockers cannot restart');
  const target=(await state()).target;
  const accepted=page.waitForResponse(response=>response.request().method()==='POST'&&response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='updates.forceRestart');
  await activate(entry);const response=await accepted;assert.ok(response.ok());const receipt=await response.json();assert.equal(receipt.accepted,true);assert.ok(receipt.requestId);
  await expect(restart.getByRole('alert')).toContainText('update operation');await expect(entry).toBeEnabled();
  const actual=await state();assert.equal(actual.calls.length,1);assert.deepEqual(actual.calls[0].args,{...target,confirm:true});assert.equal(actual.calls[0].requestId,receipt.requestId);assert.equal(actual.unknown,true);
  assert.equal(actual.result.requestId,receipt.requestId);assert.equal(actual.result.status,'rejected');
  // A confirmed rejection re-enables an explicit gesture, never an automatic retry.
  await activate(blockers.locator('summary'));await activate(blockers.locator('summary'));assert.equal((await state()).calls.length,1);
  assert.ok(await restart.evaluate(element=>element.scrollWidth<=element.clientWidth+1),'Restart warning and action fit');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No page overflow');
  assert.deepEqual(actions.filter(value=>['updates.install','updates.check','conversation.send','session.warm'].includes(value.action)),[]);
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  await page.screenshot({path:out+'/'+id+'.png'});
  row.status='passed';row.claims=[{workflow:'updates.force',expectedIndices:[0,1,2,3]}];row.receipt={requestId:receipt.requestId,unknownFenceRetained:true,calls:1};
 }catch(error){row.status='failed';row.error=String(error);if(page)try{await page.screenshot({path:out+'/'+id+'-failure.png'})}catch{}}
 finally{
  try{await browser?.close()}catch(error){row.cleanupError=String(error);row.status='failed'}
  finally{if(fixture&&fixture.exitCode===null){const exited=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await exited}finally{clearTimeout(timer)}}await writeFile(out+'/'+id+'-fixture.log',logs);results.push(row);await writeFile(out+'/results.json',JSON.stringify({schemaVersion:1,results},null,2))}
 }
}
console.log(JSON.stringify({results}));if(results.some(row=>row.status!=='passed'))process.exitCode=1;
