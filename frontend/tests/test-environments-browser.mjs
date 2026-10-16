import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_OUTPUT_DIR||'/tmp/unified-test-environments';
await mkdir(output,{recursive:true});
const fixture=process.env.AMPLIFIER_TEST_BASE_URL?null:spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/test_environments_ui_server.py','--port','0','--backend','incus'],{stdio:['ignore','pipe','inherit']});
let browser,page,failure;
try{
 const url=process.env.AMPLIFIER_TEST_BASE_URL||await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),30000);fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const facts=async()=>{const response=await page.request.get(url+'/fixture/environments');assert.equal(response.status(),200);return response.json()};
 const openEnvironments=async(target,initialSetup=false)=>{
  const setup=target.getByRole('dialog',{name:'Settings',exact:true});
  if(initialSetup){await expect(setup).toBeVisible();await setup.getByRole('button',{name:'Close panel',exact:true}).click();await expect(setup).toHaveCount(0)}
  await target.locator('[data-part="header"] .a-work-crumb').click();
  await target.getByRole('group',{name:'Workspace view',exact:true}).getByRole('button',{name:'Details',exact:true}).click();
  await target.getByText('Managed resources',{exact:true}).click();
 };
 const initial=await facts();
 await openEnvironments(page,true);
 const panel=page.getByRole('region',{name:'Test environments',exact:true});
 await expect(panel.getByText('No test environment for this chat.',{exact:true})).toBeVisible();
 assert.equal((await facts()).calls.length,0,'Opening controls does not create a backend environment');
 await page.evaluate(()=>{window.fixtureStorageSetItem=Storage.prototype.setItem;Storage.prototype.setItem=()=>{throw Error('storage unavailable')}});
 await panel.getByRole('button',{name:'Start test environment',exact:true}).click();
 await expect(panel.getByRole('alert')).toContainText('Nothing was sent.');
 assert.equal((await facts()).callCounts.start||0,0,'Storage failure cannot admit an unrecoverable request');
 await page.evaluate(()=>{Storage.prototype.setItem=window.fixtureStorageSetItem;delete window.fixtureStorageSetItem});
 const writes=[],controls=[];let dropped=false;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(['workspace.environments.reconcile','workspace.environments.remove'].includes(body?.action))controls.push(body);
  if(body?.action==='workspace.environments.start'){
   writes.push(body);
   if(!dropped){dropped=true;await route.fetch();await route.abort('connectionreset');return}
  }
  await route.continue();
 });
 await panel.getByRole('button',{name:'Start test environment',exact:true}).click();
 await expect(panel.getByRole('button',{name:'Check the same environment request',exact:true})).toBeVisible();
 await page.reload();await page.getByText('Managed resources',{exact:true}).click();
 await panel.getByRole('button',{name:'Check the same environment request',exact:true}).click();
 await expect(panel.getByText('Environment: running',{exact:true})).toBeVisible();
 assert.equal(writes.length,2);assert.equal(writes[0].id,writes[1].id);assert.deepEqual(writes[0].args,writes[1].args);
 assert.equal((await facts()).callCounts.start,1,'Lost acknowledgement never starts a replacement');
 await panel.getByLabel('Check command',{exact:true}).fill('python check.py');
 await panel.getByRole('button',{name:'Run check',exact:true}).click();
 await expect.poll(async()=>(await facts()).callCounts.execute).toBe(1);
 await expect(panel.getByText('Last request: completed',{exact:true})).toBeVisible();
 await expect(panel.getByText('workspace check passed',{exact:true})).toBeVisible();
 await expect(panel.getByText('Exit code: 0',{exact:true})).toBeVisible();
 await page.request.post(url+'/fixture/environment-mode',{data:{executeTimeout:true}});
 const peer=await context.newPage();await peer.goto(url);await peer.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await openEnvironments(peer);
 const peerPanel=peer.getByRole('region',{name:'Test environments',exact:true});
 await peerPanel.getByLabel('Check command',{exact:true}).fill('python slow_check.py');
 await peerPanel.getByRole('button',{name:'Run check',exact:true}).click();
 await expect.poll(async()=>(await facts()).callCounts.execute).toBe(2);
 await expect(peerPanel.getByText('Last request: outcome unknown',{exact:true})).toBeVisible();
 const beforeRefresh=(await facts()).callCounts.execute;
 await panel.getByRole('button',{name:'Refresh test environments',exact:true}).click();
 await expect(panel.getByText('Last request: outcome unknown',{exact:true})).toBeVisible();
 await expect(panel.getByText('workspace check passed',{exact:true})).toHaveCount(0);
 await peer.close();
 assert.equal((await facts()).callCounts.execute,beforeRefresh,'Observation cannot replay an unknown command');
 await expect(panel.getByRole('button',{name:'Remove environment…',exact:true})).toBeDisabled();
  await page.request.post(url+'/fixture/environment-mode',{data:{backendAvailable:false}});
  await panel.getByRole('button',{name:'Refresh test environments',exact:true}).click();
  await expect(panel.getByRole('button',{name:'Run check',exact:true})).toBeDisabled();
  const recoveryOwner=(await facts()).resources[0];
  await panel.getByRole('button',{name:'Check and recover',exact:true}).click();
  await expect(panel.getByText('Last recovery check: completed.',{exact:false})).toBeVisible();
  await expect(panel.getByText('Last request: outcome unknown',{exact:true})).toBeVisible();
  const recovered=await facts(),recovery=controls.find(item=>item.action==='workspace.environments.reconcile');
  assert.equal(recovered.callCounts.execute,beforeRefresh,'Recovery preserves the original unknown result without replay');
  assert.equal(recovery.args.id,recoveryOwner.id);assert.equal(recovery.args.expectedRevision,recoveryOwner.revision);
  const recoveryCall=recovered.calls.find(item=>item.action==='reconcile');
  assert.equal(recoveryCall.resourceId,recoveryOwner.id);assert.equal(recoveryCall.backendKey,recoveryOwner.backendKey);
  assert.equal(recoveryCall.sessionId,initial.sessionId);assert.equal(recoveryCall.workspaceId,initial.workspaceId);
 await panel.getByRole('button',{name:'Stop and keep data',exact:true}).click();
 await expect(panel.getByText('Environment: stopped',{exact:true})).toBeVisible();
 await panel.getByRole('button',{name:'Remove environment…',exact:true}).click();
 await expect(panel.getByRole('group',{name:'Confirm environment removal'})).toBeVisible();
 assert.equal((await facts()).callCounts.remove||0,0,'Opening removal confirmation does not remove');
 await panel.getByText('Save more files',{exact:true}).click();
 const completeCopy=panel.getByRole('checkbox',{name:'Keep a complete copy of the environment',exact:true});
 await expect(completeCopy).not.toBeChecked();await completeCopy.check();await expect(completeCopy).toBeChecked();
 assert.equal((await facts()).callCounts.remove||0,0,'Choosing full copy sends no removal');
 await panel.getByRole('button',{name:'Keep environment',exact:true}).click();
 await expect(panel.getByRole('group',{name:'Confirm environment removal'})).toHaveCount(0);
 assert.equal(controls.filter(item=>item.action==='workspace.environments.remove').length,0,'Cancel sends no removal request');
 await page.screenshot({path:output+'/test-environments-desktop.png'});
 await page.setViewportSize({width:390,height:844});await panel.scrollIntoViewIfNeeded();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal clipping');
 await page.screenshot({path:output+'/test-environments-mobile.png'});
 await panel.getByRole('button',{name:'Remove environment…',exact:true}).click();
 await panel.getByText('Save more files',{exact:true}).click();
 await expect(completeCopy).not.toBeChecked();
 await completeCopy.check();await completeCopy.uncheck();await expect(completeCopy).not.toBeChecked();
 await completeCopy.check();const removalOwner=(await facts()).resources[0];
 await panel.getByRole('button',{name:'Save files and remove environment',exact:true}).click();
 await expect(panel.getByText('Environment: removed',{exact:true})).toBeVisible();
  await expect(panel.getByText('Latest saved copy: 2 KiB.',{exact:false})).toBeVisible();
 const manual=await facts(),removal=controls.find(item=>item.action==='workspace.environments.remove');
 assert.equal(removal.args.id,removalOwner.id);assert.equal(removal.args.expectedRevision,removalOwner.revision);assert.equal(removal.args.retentionMode,'full');
 const removed=manual.calls.find(item=>item.action==='remove');
 assert.equal(removed.retentionMode,'full');assert.equal(removed.backend,'incus');assert.equal(removed.backendKey,removalOwner.backendKey);
 assert.equal(removed.resourceId,removalOwner.id);assert.equal(removed.sessionId,initial.sessionId);assert.equal(removed.workspaceId,initial.workspaceId);
 await expect(panel.getByText('Includes a complete environment export.',{exact:false})).toBeVisible();
 assert.equal(manual.callCounts.start,1);assert.equal(manual.callCounts.execute,2);assert.equal(manual.callCounts.stop,1);assert.equal(manual.callCounts.remove,1);
  await page.request.post(url+'/fixture/environment-mode',{data:{executeTimeout:false,capacityAvailable:false,backendAvailable:true}});
  await panel.getByRole('button',{name:'Refresh test environments',exact:true}).click();
 await panel.getByRole('form',{name:'Queue isolated check'}).getByLabel('Check command').fill('python check.py');
 await panel.getByRole('button',{name:'Queue check',exact:true}).click();
 await expect(panel.getByText('Check: queued',{exact:true})).toBeVisible();
 await expect(panel.getByRole('button',{name:'Start test environment',exact:true})).toHaveCount(0);
 assert.equal((await facts()).callCounts.start,1,'Full capacity saves intent without starting another environment');
 await page.reload();await page.getByText('Managed resources',{exact:true}).click();
 await expect(panel.getByText('Check: queued',{exact:true})).toBeVisible();
 await panel.getByRole('button',{name:'Cancel check',exact:true}).click();
 await expect(panel.getByText('Check: cancelled',{exact:true})).toBeVisible();
 await expect(panel.getByText('Cancellation requested.',{exact:false})).toHaveCount(0);
 assert.equal((await facts()).callCounts.start,1,'Cancelling a waiting check starts no backend effects');
 await panel.getByRole('button',{name:'Queue check',exact:true}).click();
 await expect(panel.getByText('Check: queued',{exact:true})).toBeVisible();
 await page.request.post(url+'/fixture/environment-mode',{data:{capacityAvailable:true}});
 await expect.poll(async()=>(await facts()).callCounts.remove,{timeout:20000}).toBe(2);
 await panel.getByRole('button',{name:'Refresh test environments',exact:true}).click();
 await expect(panel.getByText('Check: passed',{exact:true})).toBeVisible();
 await panel.getByRole('button',{name:'Read check result',exact:true}).click();
 await expect(panel.getByLabel('Saved check result')).toContainText('workspace check passed');
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Queued checks fit mobile width');
 await page.screenshot({path:output+'/queued-check-mobile.png'});
 await page.setViewportSize({width:1280,height:1050});
 await page.screenshot({path:output+'/queued-check-desktop.png'});
 const final=await facts();assert.equal(final.callCounts.start,2);assert.equal(final.callCounts.execute,3);assert.equal(final.callCounts.stop,2);assert.equal(final.callCounts.remove,2);assert.deepEqual(errors,[]);
 assert.deepEqual(final.calls.filter(item=>item.action==='remove').map(item=>item.retentionMode),['full','files'],'Full copy applies only to its confirmed removal; later queued cleanup keeps its default');
 assert.equal(final.workspaceFile,initial.workspaceFile);assert.deepEqual(final.messages,initial.messages);assert.deepEqual(final.runtimeCalls,initial.runtimeCalls);
  await writeFile(output+'/test-environments-browser-report.json',JSON.stringify({passed:true,realHostActionsAndReceipts:true,backend:'controlled Incus capability and transport',docker:false,incusAllocation:false,publicWorkspaceDetailsEntry:true,fullCopyDefaultToggleCancelReset:true,exactRetentionModeAndOwner:true,fullCopyDoesNotChangeNextDefault:true,recoveryUsesRetainedBackend:true,providerCalls:false,lostAckReloadNoReplay:true,peerRequestReplacesStaleResult:true,unknownCommandNotReplayed:true,reconcilePreservesUnknownReceipt:true,cleanupWithUnavailableActiveBackend:true,verifiedRetentionSize:true,stopBeforeRemove:true,removalConfirmation:true,mobileNoOverflow:true,queuedWaitSurvivesReload:true,cancelledWaitNoEffects:true,automaticCheckExportRemoval:true,retainedCheckOutput:true,counts:final.callCounts,controlRequests:controls,backendCalls:final.calls,limits:['Controlled backend transport; no real container allocation, archive verification or provider execution.','Desktop and narrow pointer activation; no new physical-touch or complete keyboard-traversal claim.'],errors},null,2));
}catch(error){failure=error;if(page)await page.screenshot({path:output+'/test-environments-failure.png'}).catch(()=>{});throw error}
finally{
 const cleanup=[];try{await browser?.close()}catch(error){cleanup.push(error)}
 try{if(fixture){
  if(fixture.exitCode===null&&fixture.signalCode===null){
   const exited=once(fixture,'exit');let timer;fixture.kill('SIGTERM');
   try{await Promise.race([exited,new Promise((_,reject)=>{timer=setTimeout(()=>{fixture.kill('SIGKILL');reject(Error('Environment fixture did not finish cleanup within 5 seconds'))},5000)})])}finally{clearTimeout(timer)}
  }
  assert.equal(fixture.exitCode,0,'Environment fixture must finish cleanup');assert.equal(fixture.signalCode,null,'Environment fixture must exit normally');
 }}catch(error){cleanup.push(error)}
 if(cleanup.length)throw new AggregateError([...(failure?[failure]:[]),...cleanup],'Environment fixture cleanup failed');
}
