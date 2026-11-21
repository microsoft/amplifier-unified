// Real host projection and selected built client; no update/recovery effects.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/update-configuration-hold';
await mkdir(out,{recursive:true});
const results=[];
for(const kind of ['component','tool']){
 const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON,
  ['-u',fileURLToPath(new URL('../fixtures/update_restart_server.py',import.meta.url))],
  {env:{...process.env,AMPLIFIER_TEST_UPDATE_HOLDS:kind}});
 const stop=ownFixture(fixture,{label:`${kind} update hold fixture`});
 let browser,failure,log='';
 fixture.stderr.on('data',data=>log=(log+data).slice(-8000));
 try{
  const boot=await new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>reject(Error(log||'Fixture timeout')),15000);
   fixture.once('error',error=>{clearTimeout(timer);reject(error)});
   fixture.once('exit',code=>{clearTimeout(timer);reject(Error(`Fixture exited ${code}: ${log}`))});
   let data='';
   fixture.stdout.on('data',chunk=>{
    data+=chunk;
    for(let end;(end=data.indexOf('\n'))>=0;){
     const line=data.slice(0,end);data=data.slice(end+1);
     let row;try{row=JSON.parse(line)}catch{continue}
     if(row.url){clearTimeout(timer);resolve(row)}
    }
   });
  });
  assert.equal(boot.pid,fixture.pid);
  browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1280,height:950},
   extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>{
   const url=new URL(route.request().url());
   return ['http:','https:'].includes(url.protocol)&&url.origin!==boot.url?route.abort():route.continue();
  });
  const evidence=async()=>{const response=await page.request.get(boot.url+'/fixture/update-restart');assert.ok(response.ok());return response.json()};
  await page.goto(boot.url);await page.waitForSelector('#amp-one');await openSettingsPage(page,'updates');
  const blockers=page.locator('[data-part="update-blockers"]');
  const before=await evidence();
  assert.equal(before.pair.historicalIds.length,4);
  assert.equal(before.pair.componentBusy,false);
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().updates.blockers)).toEqual([]);
  await expect(blockers).toHaveCount(0);
  await page.screenshot({path:`${out}/${kind}-four-stopped-clear.png`});
  for(const mode of ['active','settled']){
   const response=await page.request.post(boot.url+'/fixture/update-restart',{data:{mode}});
   assert.ok(response.ok());const observed=await response.json();
   assert.equal(observed.pair.committedOwnerSha256,before.pair.committedOwnerSha256);
   assert.equal(observed.pair.savedOwnerFilesUnchanged,true);
   assert.deepEqual(observed.pair.runtimeCalls,[]);assert.deepEqual(observed.calls,[]);
   assert.equal(observed.pair.componentBusy,mode==='active');
   await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().updates.blockers)).toEqual(observed.blockers);
   if(mode==='active'){
    assert.ok(observed.blockers.some(row=>row.kind==='conversation'&&row.sessionId===observed.pair.activeId));
    assert.ok(observed.blockers.every(row=>!before.pair.historicalIds.includes(row.sessionId)));
    await blockers.locator('summary').click();
    await expect(blockers).toContainText('Genuine active work');
    await expect(blockers).not.toContainText('Stopped saved owner');
   }else await expect(blockers).toHaveCount(0);
   await page.screenshot({path:`${out}/${kind}-${mode}.png`});
  }
  assert.deepEqual(errors,[]);
  results.push({kind,passed:true,fourRetained:true,realHostProjection:true,syntheticActivity:true,updateEffects:0});
 }catch(error){failure=error}
 await finishFixture({originalError:failure,close:[['browser',()=>browser?.close()]],stop});
 if(failure)throw failure;
}
await writeFile(out+'/update-configuration-hold.json',JSON.stringify(results,null,2)+'\n');
console.log(JSON.stringify(results));