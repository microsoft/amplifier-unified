import {spawn} from 'node:child_process';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {finishFixture,ownFixture} from './fixture-lifecycle.mjs';
import {openSettingsPage} from './browser-settings.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const outputBase=resolve(process.env.AMPLIFIER_TEST_OUTPUT_DIR||join(root,'working-files'));
await mkdir(outputBase,{recursive:true});
const outputDir=await mkdtemp(join(outputBase,'storage-cleanup-browser-'));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||join(root,'.venv/bin/python'),
 ['-u',join(root,'tests/fixtures/storage_cleanup_ui_server.py')],
 {stdio:['ignore','pipe','pipe'],env:process.env});
const stop=ownFixture(fixture,{label:'Storage cleanup settings fixture'});
let browser,failure,stderr='';
fixture.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-8000)});
try{
 const base=await new Promise((resolve,reject)=>{
  let pending='';
  const cleanup=()=>{clearTimeout(timer);fixture.stdout.off('data',data);fixture.off('exit',exited);fixture.off('error',failed)};
  const failed=error=>{cleanup();reject(error)};
  const exited=(code,signal)=>failed(Error(`Fixture exited before readiness (${code??signal}): ${stderr}`));
  const data=chunk=>{
   pending+=chunk;
   for(let end;(end=pending.indexOf('\n'))>=0;){
    const line=pending.slice(0,end);pending=pending.slice(end+1);
    let ready;try{ready=JSON.parse(line)}catch{continue}
    if(ready.fixture!=='storage-cleanup-ui')continue;
    if(ready.pid!==fixture.pid||!/^http:\/\/127\.0\.0\.1:[1-9]\d{0,4}$/.test(ready.base)){
     failed(Error('Invalid owned fixture readiness'));return;
    }
    cleanup();resolve(ready.base);return;
   }
   if(pending.length>8000)failed(Error('Fixture readiness output exceeded bound'));
  };
  const timer=setTimeout(()=>failed(Error('Fixture startup timed out: '+stderr)),30000);
  fixture.stdout.on('data',data);fixture.once('exit',exited);fixture.once('error',failed);
 });
 browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}: {})});
 const context=await browser.newContext({viewport:{width:1100,height:900},
  extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage();page.setDefaultTimeout(10000);
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const observe=async()=>{const response=await page.request.get(base+'/fixture/storage-cleanup-observations');assert.equal(response.ok(),true);return response.json()};
 await page.goto(base);await page.waitForSelector('#amp-one');
 const before=await observe();
 await openSettingsPage(page,'storage');
 const section=page.getByRole('region',{name:'Automatic cleanup',exact:true});
 await section.getByText('Retention and size',{exact:true}).click();
 const days=section.getByLabel('Keep unused copies for days',{exact:true});
 const size=section.getByLabel('Size target for unused copies (GiB)',{exact:true});
 const save=section.getByRole('button',{name:'Save cleanup settings',exact:true});
 await expect(days).toHaveValue('7');await expect(size).toHaveValue('10');
 await days.fill('3');await size.fill('0');await save.click();
 await expect(section.getByRole('status').filter({hasText:'saved for all workspaces'})).toBeVisible();
 assert.deepEqual((await observe()).policy,{revision:1,enabled:true,keepDays:3,maxUnusedBytes:0});
 await days.fill('4');
 const other=await context.newPage();await other.goto(base);await other.waitForSelector('#amp-one');
 const concurrent=await other.request.post(base+'/fixture/storage-cleanup-concurrent-save',{data:{keepDays:2}});
 assert.equal(concurrent.ok(),true);
 await save.click();await expect(section.getByRole('alert').filter({hasText:'changed'})).toBeVisible();
 await expect(days).toHaveValue('4');assert.equal((await observe()).policy.keepDays,2);
 await section.getByRole('button',{name:'Reload cleanup settings',exact:true}).click();
 await expect(days).toHaveValue('4');await expect(save).toBeDisabled();
 await section.getByRole('button',{name:'Discard edits and use saved settings',exact:true}).click();
 await expect(days).toHaveValue('2');await other.close();
 const attempts=[];let lose=true;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='storage.cleanup.save'){attempts.push(body);if(lose){lose=false;await route.fetch();await route.abort('connectionreset');return}}
  await route.continue();
 });
 await days.fill('0');await save.click();
 await expect(section.getByRole('button',{name:'Check the same request',exact:true})).toBeEnabled();
 assert.equal((await observe()).policy.revision,3);
 await page.reload();await page.waitForSelector('#amp-one');await openSettingsPage(page,'storage');
 await section.getByRole('button',{name:'Check the same request',exact:true}).click();
 await expect(section.getByRole('status').filter({hasText:'saved for all workspaces'})).toBeVisible();
 assert.equal(attempts.length,2);assert.deepEqual(attempts[0],attempts[1]);
 assert.equal((await observe()).writes.length,3);
 assert.equal((await observe()).unusedExists,true);
 await section.getByRole('button',{name:'Review unused copies',exact:true}).click();
 await expect(section.getByLabel('Cleanup preview')).toBeVisible();
 await expect(section.getByRole('button',{name:'Clean unused copies now',exact:true})).toBeEnabled();
 assert.equal((await observe()).unusedExists,true);
 const cleanupAttempts=[];let loseCleanup=true;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='storage.cleanup.run'){cleanupAttempts.push(body);if(loseCleanup){loseCleanup=false;await route.fetch();await route.abort('connectionreset');return}}
  await route.fallback();
 });
 await section.getByRole('button',{name:'Clean unused copies now',exact:true}).click();
 await expect(section.getByRole('button',{name:'Check the same request',exact:true})).toBeEnabled();
 assert.equal((await observe()).unusedExists,false);
 await page.reload();await page.waitForSelector('#amp-one');await openSettingsPage(page,'storage');
 await section.getByRole('button',{name:'Check the same request',exact:true}).click();
 await expect(section.getByRole('status').filter({hasText:'Cleanup completed'})).toBeVisible();
 assert.equal(cleanupAttempts.length,2);assert.deepEqual(cleanupAttempts[0],cleanupAttempts[1]);
 const after=await observe();assert.equal(after.savedWorkIntact,true);
 assert.equal(after.lastCleanup.status,'completed');assert.ok(after.lastCleanup.removedFileBytes>=4096);
 await expect(section.getByRole('status').filter({hasText:'Cleanup completed'})).toContainText('4.2 KB');
 assert.deepEqual(after.runtimeCalls,before.runtimeCalls);assert.equal(after.selectedSessionId,before.selectedSessionId);
 await page.screenshot({path:join(outputDir,'storage-cleanup-desktop.png'),animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 await section.getByText('Retention and size',{exact:true}).click();
 await days.fill('5');await page.getByRole('button',{name:'Close settings',exact:true}).click();
 await expect(page.getByRole('alertdialog')).toBeVisible();
 await page.getByRole('button',{name:'Keep settings open',exact:true}).click();await expect(days).toHaveValue('5');
 await section.getByRole('button',{name:'Discard edits and use saved settings',exact:true}).click();
 await expect(days).toHaveValue('0');await days.scrollIntoViewIfNeeded();
 assert.equal(await section.evaluate(element=>element.scrollWidth<=element.clientWidth),true);
 await page.screenshot({path:join(outputDir,'storage-cleanup-mobile.png'),animations:'disabled'});
 assert.deepEqual(errors,[]);
 const result={passed:true,productionBuiltUI:true,productionService:true,syntheticRuntime:true,syntheticInstalledGraph:true,
  providerCalls:false,removedFileBytes:after.lastCleanup.removedFileBytes,checks:[
   'global retention and size settings','stale save retains private draft','lost save acknowledgment exact retry after reopen',
   'read-only preview keeps files','actual canonical unused generation retirement','lost cleanup acknowledgment reobserves same receipt',
   'source/result archives and project work remain','no worker starts or stops','desktop and narrow draft protection']};
 await writeFile(join(outputDir,'result.json'),JSON.stringify(result,null,2));
 console.log(JSON.stringify({outputDir,...result}));
}catch(error){failure=error;await writeFile(join(outputDir,'fixture-stderr.log'),stderr);console.error(stderr);throw error}
finally{await finishFixture({label:'Storage cleanup settings fixture',
 close:[['Storage cleanup browser',()=>browser?.close()]],stop,originalError:failure})}
