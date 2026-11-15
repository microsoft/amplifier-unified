// Real built client/host, deterministic retained state and controlled runtime.
// No native/provider performance claim. Pair separate fresh fixture homes.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdir, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium, expect} from '@playwright/test';
import {showFixtureConversation} from './browser-chat-pages.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const output = process.env.AMPLIFIER_TEST_ARTIFACTS;
const home = process.env.AMPLIFIER_TEST_FIXTURE_HOME;
assert.ok(output && home, 'Explicit owned artifacts and fresh fixture home are required');
const python = process.env.AMPLIFIER_TEST_PYTHON || join(root, '.venv/bin/python');
const sha = text => createHash('sha256').update(text).digest('hex');
const report = {status:'running', phases:[], requests:[], errors:[],
 scope:'Built-client/actual host synthetic switching under controlled runtime receipts; not native performance',
 limits:['One process pair, fixed order; no statistical speed guarantee.',
 'Backend request clocks include queueing; browser clocks include transport/render and are not added together.',
 'Seed is host-owned synthetic history, not native history or model output.']};
let fixture, browser, page, context, ready, clientHeaders, primaryError, stderr = '', phase = 'startup';

async function start(mode='measure') {
 fixture=spawn(python,[join(root,'tests/fixtures/execution_switching_server.py'),'--home',home,
  ...(mode==='restart'?['--restart']:mode==='seed'?['--seed-only']:[])],
  {stdio:['ignore','pipe','pipe'],env:{...process.env,AMPLIFIER_TEST_PACKAGE_ROOT:join(root,'amplifier_web')}});
 fixture.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-65536)});
 return new Promise((resolve,reject)=>{
  let buffer='';const timer=setTimeout(()=>reject(Error('Fixture startup timeout: '+stderr)),120000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+stderr))});
  fixture.once('error',error=>{clearTimeout(timer);reject(error)});
  fixture.stdout.on('data',chunk=>{buffer+=chunk;
   if(buffer.length>=262144){clearTimeout(timer);reject(Error('Bounded startup output exceeded'));return}
   let end;while((end=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,end);buffer=buffer.slice(end+1);
    let value;try{value=JSON.parse(line)}catch{continue}if(value.url||value.seeded){clearTimeout(timer);resolve(value)}}});
 });
}
async function stop() {
 if(!fixture)return;
 if(fixture.exitCode!==null){assert.equal(fixture.exitCode,0);return}
 const child=fixture;
 await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>{child.kill('SIGKILL');reject(Error('Fixture did not close in 20s'))},20000);
  child.once('exit',code=>{clearTimeout(timer);code===0?resolve():reject(Error('Fixture cleanup exit '+code))});
  child.kill('SIGTERM');
 });
}
const checkedJSON=async(response,label)=>{
 if(response.status()!==200)assert.fail(label+' HTTP '+response.status()+': '+(await response.text()).slice(0,4096));
 return response.json();
};
const facts=async()=>checkedJSON(await context.request.get(ready.url+'/fixture/execution-switching'),'Fixture facts');
const operate=async operation=>{const response=await context.request.post(ready.url+'/fixture/execution-switching',{data:{operation}});
 return checkedJSON(response,'Fixture '+operation)};
const action=async(action,args)=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
const twoFrames=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
const composer=()=>page.getByRole('textbox',{name:'Message Amplifier'});
try {
 await mkdir(output,{recursive:true});
 report.seed=await start('seed');
 await new Promise((resolve,reject)=>{if(fixture.exitCode!==null){fixture.exitCode===0?resolve():reject(Error('Seed failed'));return}
  const timer=setTimeout(()=>reject(Error('Seed process failed to close')),20000);
  fixture.once('exit',code=>{clearTimeout(timer);code===0?resolve():reject(Error('Seed exit '+code))})});
 ready=await start();report.ready=ready;
 browser=await chromium.launch({headless:true,
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),
  args:JSON.parse(process.env.AMPLIFIER_CHROMIUM_ARGS||'[]')});
 context=await browser.newContext({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-execution-switching'}});
 page=await context.newPage();page.setDefaultTimeout(20000);
 page.on('pageerror',error=>{if(report.errors.length<20)report.errors.push(error.message)});
 const starts=new WeakMap();
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')starts.set(request,{at:performance.now(),phase})});
 page.on('response',response=>{const request=response.request(),start=starts.get(request);if(!start)return;
  let payload;try{payload=request.postDataJSON()}catch(error){report.errors.push('Action request observation: '+error.message);return}
  if(!payload||typeof payload!=='object'){report.errors.push('Action POST has no JSON object');return}
  report.requests.push({phase:start.phase,action:payload.action,
   innerAction:payload.args?.action,status:response.status(),headersMs:performance.now()-start.at})});
 const initial=performance.now();await page.goto(ready.url);await showFixtureConversation(page);await composer().waitFor();
 clientHeaders={'X-Amplifier-Client':await page.evaluate(()=>window.amplifier.getState().client.id),
  'X-Amplifier-State-Transport':'delta-v1'};
 await expect(page.locator('[data-message-id="message-02900"]')).toBeVisible();await twoFrames();
 report.phases.push({label:'initial-document-and-selected-render',wallMs:performance.now()-initial});
 const initialFacts=await facts();report.initial=initialFacts;
 assert.deepEqual(initialFacts.counts,{messages:2901,nodes:23195,receipts:11582,workers:506});
 assert.equal(initialFacts.sends,0);assert.equal(initialFacts.stops,0);
 await operate('begin-measurement');

 phase='input';const sendStart=performance.now();
 await composer().fill('One controlled switching input');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 // A first indexed conversion is measured from sendStart, not promised to
 // finish inside Playwright's default 5s assertion wait. Keep the run bounded.
 await expect.poll(async()=>(await facts()).sends,{timeout:30000}).toBe(1);
 await expect.poll(async()=>(await facts()).controlledRuntimeReady,{timeout:30000}).toBe(true);
 await expect(page.getByRole('button',{name:'Stop response',exact:true})).toBeVisible();
 report.phases.push({label:'input-to-runtime-admission-and-stop-control',wallMs:performance.now()-sendStart});

 // Public Work-page admission and authenticated HTTP detail reads, not a UI
 // expansion claim. Navigation releases interest, so reopen on the last return.
 const oldTool=initialFacts.oldTool;
 const admitOld=async()=>{
  try{
  // Explicit /api/state?sessionId is the full compatibility projection and
  // hydrates history. The attached client's stream owns bounded Work pages.
  const revision=(await facts()).revision;
  const usable=await page.waitForFunction(([sid,revision,turnId])=>{
   const state=window.amplifier.getState();
   const selected=state.sessions.find(row=>row.id===sid),work=selected?.workProjection,workWindow=selected?.executionWindow;
   const groups=(selected?.execution?.segments||[]).map(({id,turnId})=>({id,turnId}));
   const earlier=workWindow?.mode==='cursor'&&workWindow.hasEarlier===true&&typeof workWindow.before==='string'&&workWindow.before.length>0;
   // Selection/revision can arrive while Work is still loading. Wait for the
   // accepted projection and either the actual group or its public cursor.
   if(state.selectedSessionId===sid&&!state.navigationPending&&state.revision>=revision&&
      work?.status==='ready'&&work.current===true&&(groups.some(row=>row.turnId===turnId)||earlier))
    return {groups,before:earlier?workWindow.before:null};
   return false;
  },[ready.ids[0],revision,oldTool.turnId]);
  let selected;try{selected=await usable.jsonValue()}finally{await usable.dispose()}
  let group=selected.groups.find(row=>row.turnId===oldTool.turnId);
  let before=selected.before;
  // The fixed eight appended nodes move this old group just beyond the tail.
  // Use at most two real discovery pages; never invent a group or lease token.
  for(let count=0;!group&&before&&count<2;count++){
   const groups=await checkedJSON(await context.request.get(ready.url+'/api/conversation/detail?'+new URLSearchParams({
    sessionId:ready.ids[0],part:'groups',before}),{headers:clientHeaders}),'Earlier Work groups');
   group=groups.items.find(row=>row.turnId===oldTool.turnId);before=groups.before;
  }
  assert.ok(group?.id,'Public Work discovery must expose the exact old group');
  const detail=await checkedJSON(await context.request.get(ready.url+'/api/conversation/detail?'+new URLSearchParams({
   sessionId:ready.ids[0],part:'nodes',group:group.id}),{headers:clientHeaders}),'Admit old Work steps');
  const row=detail.items.find(row=>row.id===oldTool.id);
  assert.ok(row?.outputDetail?.windowKey?.startsWith('resident-work:'));
  return {group:group.id,page:detail,reference:row.outputDetail,detailVersion:row.detailVersion};
  }catch(error){
   try{report.workDiscoveryFailure=await page.evaluate(sid=>{
    const state=window.amplifier.getState(),selected=state.sessions.find(row=>row.id===sid);
    const work=selected?.workProjection,workWindow=selected?.executionWindow;
    return {selectedSessionId:state.selectedSessionId,revision:state.revision,navigationPending:state.navigationPending,
     work:{status:work?.status??null,current:work?.current??null,reason:work?.reason??null},
     window:{mode:workWindow?.mode??null,before:workWindow?.before??null,hasEarlier:workWindow?.hasEarlier??null},
     groups:(selected?.execution?.segments||[]).map(({id,turnId})=>({id,turnId}))};
   },ready.ids[0])}catch(observationError){report.workDiscoveryObservationError=observationError.message}
   throw error;
  }
 };
 const readOld=async reference=>{
  const query=new URLSearchParams(reference);
  const response=await context.request.get(ready.url+'/api/conversation/detail?'+query,{headers:clientHeaders});
  const value=await checkedJSON(response,'Read admitted old field');assert.equal(value.value,oldTool.output);return sha(value.value);
 };
 const firstAdmission=await admitOld();
 report.oldFieldAdmission={transport:'authenticated HTTP Work page/detail',group:firstAdmission.group,
  reference:firstAdmission.reference,detailVersion:firstAdmission.detailVersion};
 report.oldFieldBefore=await readOld(firstAdmission.reference);
 const targets=[1,0,2,0,1,0];
 for(let index=0;index<targets.length;index++){
  const target=targets[index],title=['Retained manager','Switch destination','Second destination'][target];
  phase='switch-'+index;const beforeFacts=await facts();
  const beforeCounts=beforeFacts.sourceCounters;
  const pulse=await operate('pulse');const start=performance.now();
  await page.getByRole('button',{name:title,exact:true}).first().click();
  await expect(page.getByRole('button',{name:title,exact:true}).first()).toHaveAttribute('aria-current','page');
  const selectedPaintMs=performance.now()-start;
  await composer().fill('Draft remains interactive');
  const draftInteractiveMs=performance.now()-start;
  await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id&&!window.amplifier.getState().navigationPending,ready.ids[target]);
  if(target===0)await expect(page.getByText('One controlled switching input',{exact:true})).toBeVisible();
  else await expect(page.getByText('Small conversation '+target,{exact:true})).toBeVisible();
  await twoFrames();const renderedMs=performance.now()-start;
  await composer().fill('');
  await expect.poll(async()=>(await facts()).pulsesDone,{timeout:30000}).toBe(pulse.pulse);
  const afterFacts=await facts();
  report.phases.push({label:phase,target,selectedPaintMs,draftInteractiveMs,renderedMs,
   sourceCountersBefore:beforeCounts,sourceCountersAfter:afterFacts.sourceCounters,
   residentBefore:beforeFacts.residentExecution,residentAfter:afterFacts.residentExecution});
 }
 phase='reopen-old-detail';
 const reopened=await admitOld();
 report.oldFieldReopened={transport:'authenticated HTTP Work page/detail after navigation',group:reopened.group,
  reference:reopened.reference,detailVersion:reopened.detailVersion};
 report.oldFieldAfterNavigation=await readOld(reopened.reference);
 assert.equal(report.oldFieldBefore,report.oldFieldAfterNavigation);
 phase='stop';const stopStart=performance.now();
 await page.getByRole('button',{name:'Stop response',exact:true}).click();
 await expect.poll(async()=>(await facts()).stops).toBe(1);
 await expect(page.getByRole('button',{name:'Stop response',exact:true})).toHaveCount(0);
 report.phases.push({label:'stop-to-runtime-acknowledgement-and-control-settlement',wallMs:performance.now()-stopStart});
 report.ordinary=await operate('end-measurement');
 report.lateReceipt=await operate('late-receipt');
 const syncResponse=await context.request.post(ready.url+'/api/conversation/work-sync',{headers:clientHeaders,
  data:{sessionId:ready.ids[0],group:reopened.group,known:Object.fromEntries(reopened.page.items.map(row=>[row.id,row.detailVersion]))}});
 const synced=await checkedJSON(syncResponse,'Refresh reopened Work steps after Stop/late receipt');
 assert.ok(synced.order.includes(oldTool.id));
 report.oldFieldAfter=await readOld(reopened.reference);
 assert.equal(report.oldFieldBefore,report.oldFieldAfter);
 // One actual preceding page, using the selected web client's signed cursor.
 // Each seeded text is below TranscriptWindows._wire's 4096-char limit.
 phase='earlier-messages';
 const messageRevision=(await facts()).revision;
 await page.waitForFunction(([sid,revision])=>{
  const state=window.amplifier.getState();
  return state.selectedSessionId===sid&&!state.navigationPending&&state.revision>=revision;
 },[ready.ids[0],messageRevision]);
 const messageWindow=await page.evaluate(sid=>window.amplifier.getState().sessions.find(row=>row.id===sid)?.messageWindow,ready.ids[0]);
 assert.equal(messageWindow?.mode,'cursor');assert.equal(messageWindow.hasEarlier,true);
 assert.ok(typeof messageWindow.before==='string'&&messageWindow.before.length>0);
 const messages=await context.request.get(ready.url+'/api/conversation/detail?'+new URLSearchParams({
  sessionId:ready.ids[0],part:'messages',before:messageWindow.before}),{headers:clientHeaders});
 const older=await checkedJSON(messages,'Read earlier messages');
 assert.equal(older.mode,'cursor');assert.ok(older.items.length>0&&older.items.length<=100);
 assert.ok(!older.hasEarlier||typeof older.before==='string'&&older.before!==messageWindow.before);
 let previous=-1;
 for(const row of older.items){
  assert.match(row.id,/^message-\d{5}$/);const index=Number(row.id.slice(8));
  assert.ok(index>previous&&index<2901);previous=index;
  assert.equal(row.role,index%2===0?'user':'assistant');
  assert.equal(row.text,`Saved synthetic message ${String(index).padStart(5,'0')}. `+'Retained project evidence. '.repeat(72));
  assert.ok(row.text.length<4096);assert.equal(row.textDetail,undefined);
 }
 report.olderMessageRead={transport:'authenticated HTTP detail',requestedCursor:messageWindow.before,
  count:older.items.length,first:older.items[0].id,last:older.items.at(-1).id,
  exactReturnedSeedRows:true,rowsSha256:sha(JSON.stringify(older.items.map(({id,role,text})=>({id,role,text}))))};
 phase='final-oracles';
 report.final=await facts();report.readback=await operate('verify');
 assert.equal(report.final.sends,1);assert.equal(report.final.stops,1);
 assert.equal(report.final.modelAdmissions.length,1);
 const admission=report.final.modelAdmissions[0];
 assert.equal(admission.result.allowed,true);
 assert.equal(admission.callId,'fixture-current-llm');
 assert.ok(Number.isFinite(admission.result.admittedAt));
 assert.equal(admission.observedBinding.generationId,admission.generationId);
 assert.deepEqual(admission.observedBinding.inputIds,[admission.inputId]);
 assert.equal(admission.observedBinding.inputStatusGenerationId,admission.generationId);
 assert.ok(admission.observedBinding.runtimeOwnerId);
 assert.equal(report.final.eventCosts.filter(row=>row.kind==='llm-usage').length,18);
 assert.ok(report.final.eventCosts.filter(row=>row.kind==='llm-usage').every(row=>
  row.callId===admission.callId&&row.admissionInputId===admission.inputId));
 assert.equal(report.final.eventCosts.filter(row=>row.kind==='new-completion').length,6);
 await context.close();context=null;await stop();
 phase='restart';ready=await start('restart');report.restart=ready;
 assert.deepEqual(ready.restartReadback,report.readback);
 assert.equal(ready.loaded.staticSha256,report.ready.loaded.staticSha256);
 assert.deepEqual(report.errors,[]);
 report.status='passed';report.browser=browser.version();
 console.log(JSON.stringify({passed:true,phases:report.phases,exactRestartReadback:true,noEffectReplay:true}));
} catch(error){primaryError=error;report.status='failed';report.failure={phase,message:error.message};throw error}
finally {
 try{await browser?.close();await stop()}
 catch(error){report.status='failed';report.cleanupError=error.message;if(!primaryError)throw error}
 finally{await writeFile(join(output,'report.json'),JSON.stringify(report,null,2)+'\n');await writeFile(join(output,'fixture.log'),stderr)}
}
