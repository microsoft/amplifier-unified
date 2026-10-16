// Real public intent and retention; installer/rollback effects are explicitly synthetic.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from './browser-settings.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/update_effects_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const boot=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}})});
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),actions=[],errors=[];
 page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{if(request.url().endsWith('/api/actions')&&request.method()==='POST')actions.push(request.postDataJSON())});
 const receipt=async mode=>{const response=mode?await page.request.post(boot.url+'/fixture/update-effects',{data:{mode}}):await page.request.get(boot.url+'/fixture/update-effects');assert.ok(response.ok());return response.json()};
 await page.goto(boot.url);const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeEditable();await composer.fill('Keep my unsent update draft');const before=await receipt();
 await openSettingsPage(page,'updates');const updates=page.locator('[data-part="updates"]'),overview=page.locator('[data-part="application-update"]').first();
 const identity=overview.locator('.a-update-version-line');
 await expect(identity).toHaveText('Amplifier 0.20.165 · Latest 99.0.0');
 await updates.getByRole('button',{name:'Update Amplifier',exact:true}).click();
 await expect(overview).toContainText('Waiting for 1 chat to finish');const staged=await receipt();assert.equal(staged.calls.length,1);assert.equal(staged.calls[0].application.latest,'99.0.0');assert.equal(staged.generation.active,'original-generation');assert.deepEqual(staged.updates.pendingApp,staged.target);
 await updates.locator('[data-part="update-blockers"]>summary').click();await expect(updates.locator('[data-part="update-blockers"]')).toContainText('Settings test');
 assert.equal(actions.filter(row=>row.action==='updates.forceRestart').length,0);assert.equal(staged.updates.application.current,'0.20.165');
 await expect(identity).toHaveText('Amplifier 0.20.165 · Latest 99.0.0');await expect(overview).not.toContainText('Latest release installed');
 await receipt('unknown');await expect(overview).toContainText('Updates need attention');await expect(updates).toContainText('installation needs verification');await expect(updates.locator('[data-action="updates.check"]')).toBeDisabled();await expect(updates.locator('[data-action="updates.install"]')).toHaveCount(0);assert.equal((await receipt()).calls.length,1);
 await expect(identity).toHaveText('Amplifier 0.20.165 · Latest 99.0.0');assert.equal((await receipt()).generation.active,'original-generation');
 await receipt('known-failure');await expect(updates.getByRole('button',{name:'Try update again',exact:true})).toBeEnabled();assert.equal((await receipt()).generation.active,'original-generation');
 await expect(identity).toHaveText('Amplifier 0.20.165 · Latest 99.0.0');await expect(updates).toContainText('Synthetic staging failed; original generation retained.');
 await updates.getByRole('button',{name:'Try update again',exact:true}).click();await expect(overview).toContainText('Waiting for 1 chat to finish');assert.equal((await receipt()).calls.length,2);
 await receipt('rollback-ready');
 // The only rollback control is within Advanced details; this is supplemental
 // coverage of a public entry, not ordinary-path credit for hidden internals.
 const restore=updates.getByRole('button',{name:'Restore previous components',exact:true});
 if(!await restore.isVisible())await updates.locator('[data-part="ecosystem-update-details"]>summary').click();
 await restore.click();await expect(restore).toHaveCount(0);const after=await receipt();assert.equal(after.generation.active,'previous-generation');assert.deepEqual(after.calls.at(-1),{operation:'rollback',target:'previous-generation'});assert.deepEqual(after.messages,before.originalMessages);
 await page.getByRole('button',{name:'Close panel',exact:true}).click();await expect(composer).toHaveDraft('Keep my unsent update draft');assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),before.sessionId);assert.deepEqual(errors,[]);
 assert.equal(actions.filter(row=>['conversation.send','session.warm','updates.forceRestart'].includes(row.action)).length,0);
 console.log(JSON.stringify({passed:true,syntheticEffects:true,exactStage:true,unknownPreventsRetry:true,explicitKnownRetry:true,rollbackIntent:true,historyAndDraftRetained:true,hostRestarts:0,limits:['Real installer and generation activation are not exercised.','Rollback target is verified at effect boundary; UI target presentation is not asserted.']}));
}finally{try{await browser?.close()}finally{if(fixture.exitCode===null){const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await done}catch{}finally{clearTimeout(timer)}}}}
