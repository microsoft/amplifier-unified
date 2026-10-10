// Production UI, real saved JSONL, cold application cache and warm reopen.
// The OS file cache is warm from fixture creation; remote conditions are emulated.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(root+'.venv/bin/python',['-u',root+'tests/fixtures/history_loading_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
const result={scenario:'Synthetic saved JSONL; warm OS cache; production UI; no providers',cases:[]};
try{
 const {url}=await new Promise((resolve,reject)=>{let out='';const timer=setTimeout(()=>reject(Error('History fixture timeout')),60000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{out+=chunk;for(const line of out.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}})});
 browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
 const page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-history-loading'}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url+'/login');await page.getByLabel('Username').fill('history-fixture');await page.getByLabel('Password').fill('fixture-password');await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.waitForSelector('#amp-one');
 const metrics=async()=>{const response=await page.request.get(url+'/api/fixture/history-metrics');assert.equal(response.status(),200);return response.json()};
 const initial=await metrics();
 const cdp=await page.context().newCDPSession(page);await cdp.send('Network.enable');
 const responses=[];
 page.on('response',response=>{if(new URL(response.url()).pathname==='/api/view')responses.push(response)});
 for(const scenario of initial.cases){
  const network=scenario.profile==='remote'?{latency:100,downloadThroughput:1250000,uploadThroughput:250000}:{latency:0,downloadThroughput:-1,uploadThroughput:-1};
  await cdp.send('Network.emulateNetworkConditions',{offline:false,...network});
  for(const cache of ['cold-app','warm','warm']){
   await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),initial.home);
   await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id,initial.home);
   const before=(await metrics()).loads.length;responses.length=0;
   const started=performance.now();
   const action=page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),scenario.id);
   await page.waitForFunction(({id,marker})=>window.amplifier.getState().selectedSessionId===id && Array.from(document.querySelectorAll('[data-message-id]')).some(n=>n.textContent.includes(marker)),{id:scenario.id,marker:'END-'+scenario.identity},{timeout:30000});
   await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
   const paintedMs=performance.now()-started;
   await action;
   const viewBytes=[];
   for(const response of [...responses])try{viewBytes.push((await response.body()).length)}catch{}
   const after=await metrics();
   const domRows=await page.locator('[data-message-id]').count();
   const row={...scenario,cache,network,paintedMs,domRows,viewBytes,serverLoads:after.loads.slice(before)};
   result.cases.push(row);console.log(JSON.stringify(row));
   assert.ok(domRows<=100,'Initial rendering must remain bounded');
   assert.ok(viewBytes.every(n=>n<2000000),'Transcript projection must remain bounded');
   assert.deepEqual(after.runtimeCalls,[]);assert.equal(after.unchanged,true);
  }
 }
 assert.deepEqual(errors,[]);
}finally{
 if(process.env.AMPLIFIER_PERF_EVIDENCE)await writeFile(process.env.AMPLIFIER_PERF_EVIDENCE,JSON.stringify(result,null,2)+'\n');
 await browser?.close();fixture.kill('SIGTERM');
}
