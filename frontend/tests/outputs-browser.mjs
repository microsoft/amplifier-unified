import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from './browser-settings.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/outputs_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}});fixture.once('exit',code=>reject(Error('Fixture exited '+code)))});
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(ready.url);const writing=page.getByRole('region',{name:'Reusable writing'});
 await writing.getByRole('button',{name:'Edit copy',exact:true}).click();await writing.getByLabel('Edit writing copy').fill('Corrected writing copy.');await writing.getByRole('button',{name:'Save writing version'}).click();await expect(writing.getByRole('status')).toHaveText('Saved a writing version.');
 await openSettingsPage(page,'outputs');const outputs=page.getByRole('region',{name:'Output relationships'});
 await outputs.getByLabel('Output title',{exact:true}).fill('Saved report');await outputs.getByLabel('File in this workspace',{exact:true}).fill('report.txt');await outputs.getByRole('button',{name:'Attach output',exact:true}).click();
 const report=outputs.locator('.a-selection-card').filter({has:page.getByText('Saved report',{exact:true})}).first();await report.getByRole('button',{name:'Read saved output'}).click();const detail=outputs.getByLabel('Saved output detail');await expect(detail).toContainText('Original report bytes.');
 await page.request.post(ready.url+'/fixture/change');await report.getByRole('button',{name:'Read saved output'}).click();await expect(detail).toContainText('Original report bytes.');
 const downloadUrl=await detail.getByRole('link',{name:'Download exact version'}).getAttribute('href');assert.equal(await page.request.get(ready.url+downloadUrl).then(r=>r.text()),'Original report bytes.');
 await outputs.getByRole('button',{name:'Save review snapshot'}).click();const review=outputs.locator('.a-selection-card').filter({has:page.getByText('Saved Git review',{exact:true})}).first();await review.getByRole('button',{name:'Read saved output'}).click();await expect(detail).toContainText('+after');
 await detail.getByLabel('Review note',{exact:true}).fill('Confirmed changed line.');await detail.getByLabel('Diff file (optional)').fill('design.txt');await detail.getByLabel('Displayed line (optional)').fill('1');await detail.getByRole('button',{name:'Save local review note'}).click();await expect(detail).toContainText('design.txt:1 (right) · Confirmed changed line.');
 await page.screenshot({path:'/tmp/amplifier-outputs-desktop.png',animations:'disabled'});await page.setViewportSize({width:390,height:844});await detail.scrollIntoViewIfNeeded();assert.ok(await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth));await page.screenshot({path:'/tmp/amplifier-outputs-mobile.png',animations:'disabled'});
 await report.getByRole('button',{name:'Unlink',exact:true}).click();await outputs.getByLabel('Include unlinked outputs').check();await report.getByRole('button',{name:'Restore link'}).click();
 await page.reload();await page.locator('.a-settings-experience[data-settings-page="outputs"]').waitFor();await outputs.getByRole('button',{name:'Inspect outputs'}).click();await expect(outputs.getByText('Saved report',{exact:true})).toBeVisible();
 const state=await page.request.get(ready.url+'/fixture').then(r=>r.json());assert.equal(state.selected,ready.sessionId);assert.equal(state.draft,'Keep output draft');assert.equal(state.message,ready.original);assert.equal(state.file,'Later original-file edit.');assert.deepEqual(state.sent,[]);assert.deepEqual(errors,[]);assert.equal(state.outputs.length,3);
 const saved=state.outputs.find(r=>r.kind==='writing');const read=await page.request.post(ready.url+'/api/actions',{data:{action:'outputs.read',args:{sessionId:ready.sessionId,id:saved.id}}}).then(r=>r.json());assert.equal(read.result.text,'Corrected writing copy.');assert.ok(saved.origin.messageId);
 console.log(JSON.stringify({passed:true,writingCopyPreservesOriginal:true,immutableFileAndDownload:true,realGitReviewAndAnchoredNote:true,unlinkRestore:true,reload:true,mobileBounds:true,draftSelectionPreserved:true,noModelInvocations:true}));
}finally{await browser?.close();fixture.kill()}
