import {spawn} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_OUTPUT_DIR||'/tmp/unified-test-environments';
await mkdir(output,{recursive:true});
const fixture=process.env.AMPLIFIER_TEST_BASE_URL?null:spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/test_environments_ui_server.py','--port','0'],{stdio:['ignore','pipe','inherit']});
let browser,page;
try{
 const url=process.env.AMPLIFIER_TEST_BASE_URL||await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),30000);fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const facts=()=>page.request.get(url+'/fixture/environments').then(result=>result.json());
 const initial=await facts();
 await page.evaluate(workspaceId=>window.amplifier.dispatch('view.update',{patch:{panel:null,setupJourney:'done',workSurface:'workspace',workWorkspaceId:workspaceId,workWorkspaceTab:'details'}}),initial.workspaceId);
 await page.getByText('Managed resources',{exact:true}).click();
 const panel=page.getByRole('region',{name:'Test environments',exact:true});
 await expect(panel.getByText('No test environment for this chat.',{exact:true})).toBeVisible();
 assert.equal((await facts()).calls.length,0,'Opening controls does not create a backend environment');
 await page.evaluate(()=>{window.fixtureStorageSetItem=Storage.prototype.setItem;Storage.prototype.setItem=()=>{throw Error('storage unavailable')}});
 await panel.getByRole('button',{name:'Start test environment',exact:true}).click();
 await expect(panel.getByRole('alert')).toContainText('Nothing was sent.');
 assert.equal((await facts()).callCounts.start||0,0,'Storage failure cannot admit an unrecoverable request');
 await page.evaluate(()=>{Storage.prototype.setItem=window.fixtureStorageSetItem;delete window.fixtureStorageSetItem});
 const writes=[];let dropped=false;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
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
 await peer.evaluate(workspaceId=>window.amplifier.dispatch('view.update',{patch:{panel:null,setupJourney:'done',workSurface:'workspace',workWorkspaceId:workspaceId,workWorkspaceTab:'details'}}),initial.workspaceId);
 await peer.getByText('Managed resources',{exact:true}).click();
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
 await panel.getByRole('button',{name:'Stop and keep data',exact:true}).click();
 await expect(panel.getByText('Environment: stopped',{exact:true})).toBeVisible();
 await panel.getByRole('button',{name:'Remove environment…',exact:true}).click();
 await expect(panel.getByRole('group',{name:'Confirm environment removal'})).toBeVisible();
 assert.equal((await facts()).callCounts.remove||0,0,'Opening removal confirmation does not remove');
 await panel.getByRole('button',{name:'Keep environment',exact:true}).click();
 await page.screenshot({path:output+'/test-environments-desktop.png'});
 await page.setViewportSize({width:390,height:844});await panel.scrollIntoViewIfNeeded();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal clipping');
 await page.screenshot({path:output+'/test-environments-mobile.png'});
 await panel.getByRole('button',{name:'Remove environment…',exact:true}).click();
 await panel.getByRole('button',{name:'Save files and remove environment',exact:true}).click();
 await expect(panel.getByText('Environment: removed',{exact:true})).toBeVisible();
 const final=await facts();assert.equal(final.callCounts.start,1);assert.equal(final.callCounts.execute,2);assert.equal(final.callCounts.stop,1);assert.equal(final.callCounts.remove,1);assert.deepEqual(errors,[]);
 await writeFile(output+'/test-environments-browser-report.json',JSON.stringify({passed:true,realHostActionsAndReceipts:true,backend:'controlled fixture',docker:false,providerCalls:false,lostAckReloadNoReplay:true,peerRequestReplacesStaleResult:true,unknownCommandNotReplayed:true,stopBeforeRemove:true,removalConfirmation:true,mobileNoOverflow:true,counts:final.callCounts,errors},null,2));
}catch(error){if(page)await page.screenshot({path:output+'/test-environments-failure.png'}).catch(()=>{});throw error}
finally{await browser?.close();fixture?.kill()}
