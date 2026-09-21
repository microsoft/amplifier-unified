import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/active_client_performance_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser,terminal;
const stats=values=>{const s=[...values].sort((a,b)=>a-b);return {samples:s.length,medianMs:Math.round(s[Math.floor(s.length/2)]),p95Ms:Math.round(s[Math.min(s.length-1,Math.floor(s.length*.95))]),maxMs:Math.round(s.at(-1))}};
try{
 const url=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),45000);let output='';fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const r=JSON.parse(line);if(r.url){clearTimeout(timer);resolve(r.url)}}catch{}})});
 const headers={Authorization:'Bearer fixture-active-client-token','Content-Type':'application/json'};
 const api=async(path,body)=>{const response=await fetch(url+path,{headers,signal:AbortSignal.timeout(15000),...(body===undefined?{}:{method:'POST',body:JSON.stringify(body)})});assert.equal(response.status,200,path);return response.json()};
 const {sessions}=await api('/fixture/metrics');
 browser=await chromium.launch({headless:true});
 const pages=[],errors=[],viewCounts=[];
 for(let i=0;i<4;i++){
  const context=await browser.newContext({extraHTTPHeaders:{Authorization:headers.Authorization},viewport:{width:1280,height:900}}),page=await context.newPage();pages.push(page);
  page.on('pageerror',error=>errors.push(error.message));
  const counts={pending:0,max:0};viewCounts.push(counts);const views=new Set();
  page.on('request',r=>{if(new URL(r.url()).pathname==='/api/view'){views.add(r);counts.pending++;counts.max=Math.max(counts.max,counts.pending)}});
  const done=r=>{if(views.delete(r))counts.pending--};page.on('requestfinished',done);page.on('requestfailed',done);
  await page.goto(url);await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
  await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),sessions[i<2?0:1]);
  await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Retained draft '+i);
  await page.waitForFunction(text=>window.amplifier.getState().view.draft===text,'Retained draft '+i);
 }
 await api('/api/clients/attach',{clientId:'performance-terminal',kind:'tui',protocolVersion:1});
 terminal=new AbortController();
 const stream=await fetch(url+'/api/sessions/'+sessions[0]+'/events',{headers:{...headers,'X-Amplifier-Client':'performance-terminal'},signal:terminal.signal});assert.equal(stream.status,200);
 let terminalFrames=0;const drain=(async()=>{try{for await(const chunk of stream.body)terminalFrames+=new TextDecoder().decode(chunk).split('event: snapshot').length-1}catch(error){if(error.name!=='AbortError')throw error}})();
 assert.equal((await api('/fixture/metrics')).subscriptions,5);
 await api('/fixture/progress',{running:true});
 const health=[],admissions=[],actions=[];
 for(let i=0;i<12;i++){
  let start=performance.now();await api('/api/health');health.push(performance.now()-start);
  start=performance.now();assert.equal((await api('/fixture/admit',{id:'synthetic-admission-'+i})).allowed,true);admissions.push(performance.now()-start);
  start=performance.now();await pages[i%4].evaluate(n=>window.amplifier.dispatch('view.update',{patch:{navExpanded:n%2===0}}),i);actions.push(performance.now()-start);
  await new Promise(resolve=>setTimeout(resolve,100));
 }
 await api('/fixture/progress',{running:false});
 for(let i=0;i<pages.length;i++){
  await expect(pages[i].getByRole('textbox',{name:'Message Amplifier'})).toHaveValue('Retained draft '+i);
  assert.equal(await pages[i].evaluate(()=>window.amplifier.getState().selectedSessionId),sessions[i<2?0:1]);
 }
 const metrics=await api('/fixture/metrics');assert.deepEqual(metrics.runtimeCalls,[]);assert.ok(metrics.ticks>=5);assert.ok(terminalFrames>=3);assert.deepEqual(errors,[]);
 // Generous end-to-end ceilings catch seconds-long queues without fragile CPU microbenchmarks.
 const result={catalogSessions:22915,workspaces:3932,browsers:4,terminalStreams:1,health:stats(health),admission:stats(admissions),actions:stats(actions),viewMaxInFlight:viewCounts.map(r=>r.max),ticks:metrics.ticks,terminalFrames,draftsRetained:true,modelCalls:0};
 if(process.env.AMPLIFIER_PERF_EVIDENCE)await writeFile(process.env.AMPLIFIER_PERF_EVIDENCE,JSON.stringify(result,null,2)+'\n');
 console.log(JSON.stringify(result,null,2));
 assert.ok(result.health.p95Ms<1500,JSON.stringify(result));assert.ok(result.admission.p95Ms<1500,JSON.stringify(result));assert.ok(result.actions.p95Ms<2000,JSON.stringify(result));
 assert.ok(result.viewMaxInFlight.every(n=>n<=1),JSON.stringify(result));
 terminal.abort();await drain;
}finally{terminal?.abort();await browser?.close();fixture.kill();}
