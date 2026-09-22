import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from './browser-settings.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/publishing_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),30000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}});fixture.once('error',reject);fixture.once('exit',code=>reject(Error('Fixture exited '+code)))});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(ready.url);await openSettingsPage(page,'publishing');
 const panel=page.getByRole('region',{name:'Managed publishing'}),release=panel.getByLabel('Selected release',{exact:true}),status=panel.getByLabel('Site status',{exact:true});
 const capture=panel.getByRole('button',{name:'Capture release',exact:true});
 const list=()=>page.request.get(ready.url+'/fixture').then(response=>response.json());
 const content=async url=>{const response=await page.request.get(url);assert.equal(response.status(),200);return response.text()};
 const reviewed=async note=>{await panel.getByLabel('Review note for this exact release').fill(note);await panel.getByRole('button',{name:'Record review',exact:true}).click();await expect(release).toContainText('Reviewed: '+note)};
 await panel.getByLabel('Site ID',{exact:true}).fill('fixture-site');await panel.getByLabel('Built output folder').fill('dist');
 await capture.evaluate(button=>{button.click();button.click()});await expect(release).toBeVisible();
 let snapshot=await list();assert.equal(snapshot.publishing.releases.length,1);const first=snapshot.publishing.releases[0];assert.match(first.id,/^[a-f0-9]{64}$/);
 await expect(release).toContainText(first.manifestDigest);await expect(panel.getByRole('button',{name:'Deploy reviewed release'})).toBeDisabled();
 await panel.getByRole('button',{name:'Start preview',exact:true}).click();const preview=panel.getByRole('link',{name:'Open exact release preview'});await expect(preview).toBeVisible();assert.match(await content(await preview.getAttribute('href')),/First release/);
 await reviewed('Verified the first release preview.');
 // Drop a real deployment response after the server commits. Retry must retain
 // the exact request and resolve its receipt, without another revision change.
 let dropped=false;
 await page.route('**/api/actions*',async route=>{const body=route.request().postDataJSON();if(body?.action==='publishing.deploy'&&!dropped){dropped=true;await route.fetch();await route.abort('failed')}else await route.continue()});
 await panel.getByRole('button',{name:'Deploy reviewed release'}).click();const pending=panel.getByLabel('Unconfirmed publishing request');await expect(pending).toContainText('Request outcome unconfirmed');await expect(capture).toBeDisabled();
 const afterLost=await list(),pendingRequest=afterLost.calls.filter(row=>row.action==='publishing.deploy').at(-1).args;
 assert.equal(afterLost.publishing.sites[0].status,'running');
 await page.getByRole('button',{name:'Close panel',exact:true}).click();await openSettingsPage(page,'publishing');
 // On reopen only a read runs. An explicit retry keeps the submitted identity.
 await expect(pending).toBeVisible();await pending.getByRole('button',{name:'Retry exact request'}).click();await expect(pending).toHaveCount(0);await expect(status).toContainText('running');
 snapshot=await list();const deploys=snapshot.calls.filter(row=>row.action==='publishing.deploy');assert.equal(deploys.length,2);assert.deepEqual(deploys[1].args,pendingRequest);assert.equal(snapshot.publishing.sites[0].revision,afterLost.publishing.sites[0].revision);
 await page.unroute('**/api/actions*');
 let siteUrl=await panel.getByRole('link',{name:'Open deployed site'}).getAttribute('href');assert.match(await content(siteUrl),/First release/);
 await page.request.post(ready.url+'/fixture/change');await panel.getByLabel('Site ID',{exact:true}).fill('fixture-site');await panel.getByLabel('Built output folder').fill('dist');await capture.click();await expect(release).not.toContainText(first.id);await reviewed('Verified the second release files.');
 snapshot=await list();const second=snapshot.publishing.releases.find(row=>row.id!==first.id),beforeStale=snapshot.publishing.sites[0];
 await page.request.post(ready.url+'/api/actions',{data:{action:'publishing.stop',args:{sessionId:ready.sessionId,siteId:'fixture-site',expectedRevision:beforeStale.revision,requestId:crypto.randomUUID()}}});
 await panel.getByRole('button',{name:'Deploy reviewed release'}).click();await expect(panel.getByRole('alert')).toContainText(/revision|stale|changed/i);await expect(status).toContainText('stopped');await expect(pending).toHaveCount(0);
 await panel.getByRole('button',{name:'Deploy reviewed release'}).click();await expect(status).toContainText(second.id);siteUrl=await panel.getByRole('link',{name:'Open deployed site'}).getAttribute('href');assert.match(await content(siteUrl),/Second release/);
 await panel.getByRole('button',{name:'Read site logs'}).click();await expect(panel.getByLabel('Publishing inspection')).toBeVisible();
 await panel.getByRole('button',{name:'Roll back to previous release'}).click();await expect(status).toContainText('Deployed release'+first.id);siteUrl=await panel.getByRole('link',{name:'Open deployed site'}).getAttribute('href');assert.match(await content(siteUrl),/First release/);
 await panel.getByRole('button',{name:'Check site status'}).click();await expect(panel.getByLabel('Publishing inspection')).toContainText('running');
 // A durable unknown result is different from a missing response: acknowledge
 // it without replay, then explicitly stop the fenced site before resuming.
 await page.request.post(ready.url+'/fixture/fail-deploy');
 await panel.getByRole('button',{name:'Deploy reviewed release'}).click();await expect(pending).toBeVisible();
 await panel.getByRole('button',{name:'Inspect publishing',exact:true}).click();await expect(pending).toContainText('Recorded unknown outcome');
 snapshot=await list();const unknownCount=snapshot.calls.filter(row=>row.action==='publishing.deploy').length;assert.equal(snapshot.publishing.sites[0].status,'unknown');
 await pending.getByRole('button',{name:'Acknowledge unknown outcome'}).click();await expect(pending).toHaveCount(0);await expect(panel.getByRole('button',{name:'Deploy reviewed release'})).toBeDisabled();
 snapshot=await list();assert.equal(snapshot.calls.filter(row=>row.action==='publishing.deploy').length,unknownCount);
 await panel.getByRole('button',{name:'Stop site',exact:true}).click();await expect(status).toContainText('stopped');
 await panel.getByRole('button',{name:'Deploy reviewed release'}).click();await expect(status).toContainText('running');
 if(process.env.AMPLIFIER_PUBLISHING_SCREENSHOTS)await page.screenshot({path:process.env.AMPLIFIER_PUBLISHING_SCREENSHOTS+'-desktop.png',animations:'disabled'});
 await page.setViewportSize({width:390,height:844});await status.scrollIntoViewIfNeeded();assert.ok(await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth));
 if(process.env.AMPLIFIER_PUBLISHING_SCREENSHOTS)await page.screenshot({path:process.env.AMPLIFIER_PUBLISHING_SCREENSHOTS+'-mobile.png',animations:'disabled'});
 await panel.getByRole('button',{name:'Stop site',exact:true}).click();await expect(status).toContainText('stopped');await expect(panel.getByRole('link',{name:'Open deployed site'})).toHaveCount(0);
 await panel.getByRole('button',{name:'Remove site',exact:true}).click();await expect(status).toContainText('removed');
 await page.reload();await page.locator('.a-settings-experience[data-settings-page="publishing"]').waitFor();await expect(panel.getByRole('button',{name:/fixture-site.*removed/})).toBeVisible();
 snapshot=await list();assert.equal(snapshot.publishing.releases.length,2);assert.equal(snapshot.publishing.sites[0].status,'removed');assert.ok(snapshot.publishing.receipts.length>=10);assert.equal(snapshot.selected,ready.sessionId);assert.equal(snapshot.draft,'Keep publishing draft');assert.deepEqual(snapshot.sent,[]);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,immutableReleaseAndPreview:true,explicitReviewedDeployment:true,lostResponseExactRetry:true,confirmedUnknownAcknowledgedWithoutReplay:true,duplicateSubmissionSuppressed:true,staleRevisionRefresh:true,actualUpdateAndRollbackBytes:true,statusAndLogs:true,stopRemoveRetainHistory:true,reload:true,mobileBounds:true,draftAndSelectionPreserved:true,noModelInvocations:true}));
}finally{await browser?.close();fixture.kill()}
