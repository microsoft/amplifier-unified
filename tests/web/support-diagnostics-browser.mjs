import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,readdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import './composer-test-helpers.mjs';
const output=resolve(process.env.AMPLIFIER_TEST_ARTIFACTS||'output/support-diagnostics');
await mkdir(output,{recursive:true});assert.deepEqual(await readdir(output),[],'Use an empty evidence directory');
const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/session_health_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit'],env:{...process.env,BRIDGE_TIMEOUT_FIXTURE:'1'}});
const stop=ownFixture(fixture,{label:'Support diagnostics'});
let browser,page,originalError;
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}});fixture.once('exit',code=>reject(Error('Fixture exit '+code)))});
 browser=await chromium.launch({headless:true});const context=await browser.newContext({permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page=await context.newPage();const actions=[],errors=[];page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{if(request.url().endsWith('/api/actions')&&request.method()==='POST')actions.push(request.postDataJSON())});await page.goto(ready.url);await showFixtureConversation(page);
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Private unsent diagnostic question');
 const disclosure=page.locator('[data-part=conversation-trouble] details');
 await disclosure.locator(':scope>summary').click();
 await expect(disclosure).toHaveAttribute('open','');
 await disclosure.getByRole('button',{name:'Technical details',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'Error details',exact:true});
 await expect(dialog.getByText('Recorded problem',{exact:true})).toBeVisible();
 assert.equal(actions.filter(row=>row.action==='session.inspect').length,0,'Opening details does not scan history');
 const nextInspection=()=>{
  const pending=page.waitForResponse(response=>{
   const request=response.request();
   return request.method()==='POST'&&new URL(response.url()).pathname==='/api/actions'&&
    request.postDataJSON()?.action==='session.inspect'&&request.postDataJSON()?.args.id===ready.sessionId;
  });
  // Browser closure or an earlier assertion can reject this wait before its
  // explicit await. Observe it now; the original promise still rejects there.
  pending.catch(()=>{});
  return pending;
 };
 const clientMetadata=()=>page.evaluate(()=>({userAgent:navigator.userAgent.slice(0,512),
  language:navigator.language,online:navigator.onLine,visibility:document.visibilityState}));
 const firstResponse=nextInspection();
 await dialog.getByRole('button',{name:'Copy diagnostics',exact:true}).click();
 await expect(dialog.getByText('Diagnostics copied',{exact:true})).toBeVisible();
 const report=JSON.parse(await page.evaluate(()=>navigator.clipboard.readText()));
 const first=await firstResponse,firstReceipt=await first.json();
 assert.equal(first.ok(),true);assert.equal(firstReceipt.accepted,true);
 assert.deepEqual(report,{...firstReceipt.result,client:await clientMetadata()},'Copy preserves its exact fresh report and bounded browser metadata');
 assert.equal(report.diagnosticsSchema,2);assert.equal(report.failure.category,'app_bridge_timeout');
 assert.equal(report.failure.generationId,'bridge-generation');assert.deepEqual(report.failure.inputIds,['input-1']);
 assert.equal(report.sessionId,ready.sessionId);assert.equal(report.failure.source,'saved_provider_event');
 assert.equal(report.failure.recordedAt,1789926000);assert.equal(report.failure.evidenceRecordedAt,1789925990);
 assert.equal(report.reportedFailure.category,'unknown');assert.equal(report.workReplayed,false);
 assert.equal(report.failureEvidence[0].timeoutSeconds,60);assert.ok(report.environment.applicationVersion);
 // A retained failure report must not borrow unrelated current HTTP/action
 // timings. Its bounded causal timeline is the current diagnostic contract.
 assert.equal('recentRequests' in report,false);assert.equal('performance' in report,false);
 assert.equal('recentRequests' in report.client,false);
 assert.equal('recentActions' in report.client,false);
 assert.equal(report.timeline.length,1);
 assert.equal(report.timeline[0].event,'generation.started');
 assert.equal(report.timeline[0].generation_id,'bridge-generation');
 assert.equal(report.timeline[0].at,1789925800);
 assert.ok(Number.isFinite(report.capturedAt));assert.ok(report.host.hostInstanceId);
 await expect(dialog.getByText('Amplifier timed out waiting for an internal operation.',{exact:true})).toBeVisible();
 await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.attemptedDiagnosticCopy=text;throw Error('denied')}}}));
 const secondResponse=nextInspection();
 await dialog.getByRole('button',{name:'Copy diagnostics',exact:true}).click();
 await dialog.getByText('Copy diagnostic text manually',{exact:true}).click();
 await expect(dialog.getByRole('textbox',{name:'Diagnostic report'})).toHaveValue(/"diagnosticsSchema": 2/);
 const manual=JSON.parse(await dialog.getByRole('textbox',{name:'Diagnostic report'}).inputValue());
 assert.equal(await dialog.getByRole('textbox',{name:'Diagnostic report'}).inputValue(),await page.evaluate(()=>window.attemptedDiagnosticCopy),'Manual fallback retains the exact text denied by the clipboard');
 const second=await secondResponse,secondReceipt=await second.json();
 assert.equal(second.ok(),true);assert.equal(secondReceipt.accepted,true);
 assert.deepEqual(manual,{...secondReceipt.result,client:await clientMetadata()},'Fallback preserves its own fresh report, not a stale first capture');
 assert.equal(manual.failure.generationId,'bridge-generation');
 assert.deepEqual(manual.failure,report.failure,'Clipboard refusal cannot change the selected diagnosis');
 assert.deepEqual(manual.failureEvidence,report.failureEvidence);
 assert.deepEqual(manual.timeline,report.timeline);
 assert.equal('recentRequests' in manual,false);assert.equal('performance' in manual,false);
 assert.equal('recentRequests' in manual.client,false);
 assert.equal('recentActions' in manual.client,false);
 assert.ok(manual.capturedAt>=report.capturedAt);
 assert.ok(!JSON.stringify(report).includes('Private unsent diagnostic question'));
 assert.ok(!JSON.stringify(manual).includes('Private unsent diagnostic question'));
 assert.ok(JSON.stringify(report).length<200000,'Diagnostics stay bounded for this fixture');
 assert.deepEqual(actions.filter(row=>['conversation.stop','conversation.forceStop','runtime.control','session.recover','conversation.send','conversation.retry','session.warm','configuration.apply'].includes(row.action)),[],'Read/copy diagnostics must not start, stop, replay or continue a conversation');
 assert.equal(actions.filter(row=>row.action==='session.inspect').length,2,'Each explicit copy obtains one fresh diagnostic report');
 const preserved=await page.request.get(ready.url+'/fixture/check').then(response=>response.json());assert.equal(preserved.originalUnchanged,true);assert.equal(preserved.selected,ready.sessionId);assert.equal(preserved.sessions,1);assert.equal(preserved.namingCalls,0);
 await dialog.getByRole('button',{name:'Close panel',exact:true}).click();
 await expect(composer).toHaveDraft('Private unsent diagnostic question');
 assert.deepEqual(errors,[]);
 await writeFile(resolve(output,'result.json'),JSON.stringify({passed:true,report,manual,preserved,actions:actions.map(({action,args})=>({action,args}))},null,2)+'\n');
 console.log('Support diagnostics: exact retained bridge cause/timeline, no unrelated request history, private draft omitted and manual copy fidelity passed');
}catch(error){originalError=error;await writeFile(resolve(output,'failure.json'),JSON.stringify({error:String(error.stack||error)},null,2)).catch(()=>{});await page?.screenshot({path:resolve(output,'failure.png')}).catch(()=>{});throw error}
finally{await finishFixture({label:'Support diagnostics',close:[['browser',()=>browser?.close()]],stop,originalError})}
