// Production assets, real HTTP/SSE and a disposable Spark-sized catalog.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {dirname} from 'node:path';
import {availableParallelism,loadavg} from 'node:os';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const evidencePath=process.env.AMPLIFIER_PERF_EVIDENCE||root+'output/state-transport-browser/evidence.json';
const tracePath=evidencePath.replace(/\.json$/,'')+'.trace.zip';
const errors=[],responses=[],frames=[],paint=[],pending=new Map();
const evidence={status:'running',stage:'fixture',thresholdMs:250,paint,diagnostics:[],rendererUnits:'Seconds except LayoutCount and RecalcStyleCount',errors,modelCalls:0,
 runner:{platform:process.platform,arch:process.arch,node:process.version,parallelism:availableParallelism(),ci:process.env.CI==='true',loadAverage:loadavg()}};
const persist=async()=>{await mkdir(dirname(evidencePath),{recursive:true});await writeFile(evidencePath,JSON.stringify(evidence,null,2)+'\n')};
const pendingActions=()=>[...pending.values()].slice(0,32);
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/active_client_performance_server.py'],{stdio:['ignore','pipe','inherit'],env:{...process.env,AMPLIFIER_TRANSPORT_FIXTURE:'1'}});
let browser,context,page,cdp,tracing=false,failed=false,measured=false,heldPrepares=0,persistenceReleased=false;
let releasePersistence;const persistenceGate=new Promise(resolve=>{releasePersistence=()=>{persistenceReleased=true;resolve()}});
const metricNames=['Timestamp','TaskDuration','ScriptDuration','LayoutDuration','RecalcStyleDuration','LayoutCount','RecalcStyleCount'];
const rendererMetrics=async()=>Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.filter(row=>metricNames.includes(row.name)).map(row=>[row.name,row.value]));
async function measurePaint(kind){
 // Runner/renderer reads and JSON writes stay outside the measured interaction.
 const before=await rendererMetrics(),pendingBefore=pendingActions(),loadBefore=loadavg();
 const sample=await page.evaluate(async kind=>{
  const root=document.getElementById('amp-one'),button=kind==='appearance'?[...document.querySelectorAll('button')].find(node=>node.textContent.trim()==='Dark'):null;
  const gallery={cards:document.querySelectorAll('.a-appearance-card').length,loading:!![...document.querySelectorAll('[role="status"]')].find(node=>node.textContent==='Loading appearances…')};
  let start,schemeChangeMs=null,firstFrameMs,secondFrameMs;
  const observer=new MutationObserver(()=>{if(schemeChangeMs===null&&root.style.colorScheme==='dark')schemeChangeMs=performance.now()-start});
  if(kind==='appearance')observer.observe(root,{attributes:true,attributeFilter:['style']});
  start=performance.now();
  if(kind==='settings')window.pendingSettings=window.amplifier.dispatch('view.update',{patch:{panel:'settings'}});else button.click();
  const clickMs=performance.now()-start;
  await new Promise(resolve=>requestAnimationFrame(()=>{firstFrameMs=performance.now()-start;requestAnimationFrame(()=>{secondFrameMs=performance.now()-start;resolve()})}));
  const ms=performance.now()-start,visible=kind==='settings'?!!document.querySelector('[role="dialog"]'):root.style.colorScheme==='dark';
  observer.disconnect();
  window.transportLongTasks.record(window.transportLongTasks.observer.takeRecords());
  const longTasks=window.transportLongTasks.rows.filter(row=>row.start<=start+ms&&row.start+row.duration>=start).map(row=>({startMs:row.start-start,durationMs:row.duration}));
  return {kind,ms,visible,clickMs,firstFrameMs,secondFrameMs,schemeChangeMs,gallery,longTasks};
 },kind);
 paint.push(sample);
 const after=await rendererMetrics();
 evidence.diagnostics.push({kind,pendingBefore,pendingAfter:pendingActions(),loadBefore,loadAfter:loadavg(),
  rendererDelta:Object.fromEntries(metricNames.map(name=>[name,after[name]-before[name]]))});
 await persist();
}
try{
 await persist();
 const url=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),45000);let output='';fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});evidence.runner.browser=browser.version();
 context=await browser.newContext({extraHTTPHeaders:{Authorization:'Bearer fixture-active-client-token'},viewport:{width:1440,height:1000}});page=await context.newPage();
 // Keep network/action/DOM evidence for failures without continuous screenshots.
 await context.tracing.start({screenshots:false,snapshots:true,sources:false});tracing=true;
 await page.addInitScript(()=>{
  const rows=[],record=entries=>{for(const row of entries)rows.push({start:row.startTime,duration:row.duration});if(rows.length>64)rows.splice(0,rows.length-64)};
  const observer=new PerformanceObserver(list=>record(list.getEntries()));observer.observe({type:'longtask',buffered:true});
  window.transportLongTasks={rows,record,observer};
 });
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')pending.set(request,{action:String(request.postDataJSON()?.action||'unknown').slice(0,120)})});
 page.on('requestfinished',request=>pending.delete(request));page.on('requestfailed',request=>pending.delete(request));
 page.on('response',response=>{if(measured&&response.request().method()==='POST'&&new URL(response.url()).pathname==='/api/actions')responses.push(response.body().then(body=>({action:response.request().postDataJSON().action,bytes:body.length})))});
 cdp=await context.newCDPSession(page);await cdp.send('Network.enable');await cdp.send('Performance.enable');
 cdp.on('Network.eventSourceMessageReceived',event=>{if(measured)frames.push({kind:event.eventName,bytes:Buffer.byteLength(event.data)})});
 await page.goto(url);await page.getByRole('button',{name:'Settings',exact:true}).waitFor();
 evidence.stage='baseline';const initialBytes=await page.evaluate(()=>JSON.stringify(window.amplifier.getState()).length);evidence.initialBytes=initialBytes;await persist();
 assert.ok(initialBytes>2_000_000);
 assert.equal(await page.evaluate(()=>!!window.amplifier.getState().sessions.find(row=>row.id===window.amplifier.getState().selectedSessionId).execution.retiredUsageNodes),false);
 measured=true;
 for(let i=0;i<12;i++)await page.evaluate(value=>window.amplifier.dispatch('view.update',{patch:{navWidth:300+value}}),i);
 // Preserve simulated transport latency; the explicit gate proves persistence is still blocked.
 await page.route('**/api/actions',async route=>{
  const action=route.request().postDataJSON()?.action;
  if(['view.update','shell.changes.prepare','shell.changes.apply'].includes(action))await new Promise(resolve=>setTimeout(resolve,500));
  if(action==='shell.changes.prepare'&&!persistenceReleased){heldPrepares++;await persistenceGate}
  await route.continue();
 });
 evidence.stage='settings paint';await measurePaint('settings');
 await page.evaluate(()=>window.pendingSettings);
 await page.locator('[data-settings-section="appearance"]').click();
 const dark=page.getByRole('button',{name:'Dark',exact:true});await dark.waitFor();
 // Do not wait for gallery/fonts/network idle: keep the existing cold-opening coverage.
 evidence.stage='appearance paint';await measurePaint('appearance');
 evidence.stage='held persistence';await expect.poll(()=>heldPrepares).toBe(1);
 evidence.optimisticProof={heldPrepares,persistenceReleased,...await page.evaluate(()=>({localScheme:document.getElementById('amp-one').style.colorScheme,savedScheme:window.amplifier.getShellState().effectiveComposition.presentation.scheme??null}))};
 await persist();assert.ok(!evidence.optimisticProof.persistenceReleased&&evidence.optimisticProof.localScheme==='dark',JSON.stringify(evidence.optimisticProof));
 releasePersistence();
 await expect(dark).toBeEnabled();
 assert.equal(await page.evaluate(()=>window.amplifier.getShellState().effectiveComposition.presentation.scheme),'dark');
 // Failed persistence rolls back the preview and retains the saved appearance.
 evidence.stage='rejection rollback';await page.unroute('**/api/actions');
 await page.route('**/api/actions',async route=>{if(route.request().postDataJSON()?.action==='shell.changes.apply'){await new Promise(resolve=>setTimeout(resolve,300));return route.fulfill({status:409,json:{accepted:false,error:'Fixture rejected appearance'}})}await route.continue()});
 await page.getByRole('button',{name:'Light',exact:true}).click();
 await expect(page.getByText('Fixture rejected appearance',{exact:true})).toBeVisible();
 assert.equal(await page.locator('#amp-one').evaluate(node=>node.style.colorScheme),'dark');evidence.rejectionRollback=true;
 await page.unroute('**/api/actions');
 measured=false;
 const receipts=await Promise.all(responses),deltas=frames.filter(row=>row.kind==='state-delta');
 Object.assign(evidence,{stage:'transport and paint assertions',actionCount:receipts.length,maxActionBytes:Math.max(...receipts.map(row=>row.bytes)),deltaCount:deltas.length,maxDeltaBytes:Math.max(...deltas.map(row=>row.bytes))});await persist();
 assert.ok(deltas.length>=12);assert.ok(receipts.every(row=>row.bytes<16_000),JSON.stringify(receipts));
 assert.ok(deltas.every(row=>row.bytes<30_000),JSON.stringify(deltas));
 assert.ok(paint.every(row=>row.visible&&row.ms<250),JSON.stringify(paint));
 // A fresh page gets an authoritative baseline after reconnect.
 evidence.stage='reconnect';await page.reload();await page.getByRole('button',{name:'Settings',exact:true}).waitFor();
 assert.equal(await page.locator('#amp-one').evaluate(node=>node.style.colorScheme),'dark');
 assert.deepEqual(errors,[]);
 Object.assign(evidence,{status:'passed',stage:'complete',reconnect:true});await persist();console.log(JSON.stringify(evidence,null,2));
}catch(error){failed=true;evidence.status='failed';evidence.failure={name:error.name,message:error.message.slice(0,8000)};throw error}
finally{
 releasePersistence();
 try{
  if(tracing){await context.tracing.stop(failed?{path:tracePath}:{});if(failed)evidence.failureTrace=tracePath}
 }catch(error){evidence.traceError=error.message}
 try{await persist()}finally{try{await browser?.close()}finally{fixture.kill()}}
}
