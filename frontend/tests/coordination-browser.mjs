import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const python=process.env.AMPLIFIER_TEST_PYTHON||root+'/.venv/bin/python';
const fixture=spawn(python,[root+'/tests/fixtures/coordination_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,page;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timeout=setTimeout(()=>reject(Error('startup')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timeout);resolve(row.url)}}catch{}});fixture.once('error',reject)});
 // Environment-specific Chromium setup belongs to the external NODE_OPTIONS hook.
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1100,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.setDefaultTimeout(10000);
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Preserve this unsent draft');
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'session-details'}}));
 await page.getByRole('button',{name:'Tasks and workers',exact:true}).click();
 const first=page.getByRole('region',{name:'Worker: First worker'}),second=page.getByRole('region',{name:'Worker: Second worker'});
 await first.getByRole('checkbox').check();
 await second.getByRole('checkbox').check();
 const current=await page.request.get(url+'/fixture').then(response=>response.json());
 const emit=data=>page.request.post(url+'/fixture/emit',{data});
 await emit({id:'worker-b',status:'idle',report:'Second worker finished its first report.',reportId:'b-report-1'});
 await expect(second.locator('[data-report-id="b-report-1"]')).toHaveCount(1);
 await first.getByText('Follow up',{exact:true}).click();
 await first.getByLabel('Follow-up for First worker').fill('Investigate the remaining check');
 await first.getByRole('button',{name:'Send follow-up',exact:true}).click();
 await expect(page.getByText('Follow-up accepted.',{exact:true})).toBeVisible();
 let observed=await page.request.get(url+'/fixture').then(response=>response.json());
 assert.deepEqual(observed.messages.map(({sessionId,workerId,text})=>({sessionId,workerId,text})),[{sessionId:current.other,workerId:'worker-a',text:'Investigate the remaining check'}]);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),current.selected);
 await expect(composer).toHaveValue('Preserve this unsent draft');
 // Reconnect with retained cursors: one stable DOM report, no repeated command.
 await page.reload();
 await expect(second.locator('[data-report-id="b-report-1"]')).toHaveCount(1);
 await expect(second.getByRole('checkbox')).toBeChecked();
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 let interruptedRead=false;
 await page.route('**/api/actions',async route=>{
  if(!interruptedRead&&route.request().method()==='POST'&&route.request().postDataJSON()?.action==='coordination.wait'){interruptedRead=true;await route.abort('connectionreset')}else await route.continue();
 });
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'session-details'}}));
 await page.getByRole('button',{name:'Tasks and workers',exact:true}).click();
 await expect(page.getByText(/Waiting to reconnect:/)).toBeVisible();
 await emit({id:'worker-b',status:'idle',report:'Second worker finished its first report.',reportId:'b-report-1'});
 await emit({id:'worker-a',status:'idle',report:'First worker follow-up report.',reportId:'a-report-2'});
 await expect(first.locator('[data-report-id="a-report-2"]')).toHaveCount(1);
 await expect(second.locator('[data-report-id="b-report-1"]')).toHaveCount(1);
 // An active long wait must not occupy the shared command queue.
 await first.getByRole('button',{name:'Interrupt',exact:true}).click();
 await expect(first.getByText('Outcome unknown · Needs attention',{exact:true})).toBeVisible();
 observed=await page.request.get(url+'/fixture').then(response=>response.json());
 assert.equal(observed.messages.length,1);assert.equal(observed.stops.length,1);
 await page.locator('.a-dialog').evaluate(element=>element.scrollTop=0);
 await page.screenshot({path:'/tmp/amplifier-coordination-desktop.png',animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.locator('.a-dialog').evaluate(element=>element.scrollWidth<=element.clientWidth),true);
 await page.screenshot({path:'/tmp/amplifier-coordination-mobile.png',animations:'disabled'});
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await expect(composer).toHaveValue('Preserve this unsent draft');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),current.selected);
 // Complete deterministic adapter loop through production DOM/service paths.
 // Scripted generations/anchors are NOT a native runtime or live model run.
 await page.setViewportSize({width:1100,height:900});
 const inspect=()=>page.request.get(url+'/fixture').then(response=>response.json());
 const readCandidate=()=>page.request.get(url+'/fixture/artifact').then(response=>response.json());
 const sha256=text=>createHash('sha256').update(text).digest('hex');
 const agent=(actor,action,args,id)=>page.request.post(url+'/fixture/peer',{data:{actor,action,args,id}}).then(async response=>{
  assert.equal(response.ok(),true,await response.text());return response.json();
 });
 const finish=()=>page.request.post(url+'/fixture/finish',{data:{}}).then(async response=>{
  assert.equal(response.ok(),true,await response.text());return response.json();
 });
 const natural='Coordinate with Other conversation on this task';
 await composer.fill(natural);
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(composer).toHaveValue('');
 await expect(page.getByRole('log',{name:'Conversation messages'}).getByText(natural,{exact:true})).toBeVisible();
 const approvals=page.locator('[data-part="approvals"]');
 await expect(approvals.getByRole('button',{name:'Allow',exact:true})).toHaveCount(1);
 let peerState=await inspect();
 assert.deepEqual(peerState.failures,[]);
 assert.equal(peerState.humanMessages.length,1);
 const human=peerState.humanMessages[0];
 assert.equal(human.text,natural);assert.equal(human.inputOrigin,'ui');
 assert.equal(human.hostAction,undefined);assert.equal(human.peerEnvelope,undefined);
 assert.equal(human.inputId,peerState.sent.find(row=>row.kind==='human').inputId);
 assert.deepEqual(peerState.contexts[current.selected].inputIds,[human.inputId]);
 assert.match(peerState.approvals[0].prompt,/"idleStart": true/);
 assert.match(peerState.approvals[0].prompt,/"allowCreate": true/);
 assert.match(peerState.approvals[0].prompt,/"steer"/);
 await approvals.getByRole('button',{name:'Allow',exact:true}).click();
 await expect.poll(async()=>{
  const state=await inspect();assert.deepEqual(state.failures,[]);return state.grant?.accepted;
 },{timeout:10000}).toBe(true);
 peerState=await inspect();
 const grant=peerState.grant.result.id;
 assert.equal(peerState.grant.delivery,'approved');
 assert.equal(peerState.grant.result.sourceMessageId,human.id);
 assert.equal(peerState.grant.result.sourceDigest,sha256(JSON.stringify(natural)));
 assert.equal(peerState.grant.result.mediation,'agent');
 assert.deepEqual(peerState.grant.result.participants,[current.selected,current.other]);
 assert.deepEqual(peerState.grant.result.modes,['notify','queue','steer']);
 assert.equal(peerState.grant.result.idleStart,true);assert.equal(peerState.grant.result.allowCreate,true);
 assert.equal(peerState.approvals.length,1);assert.equal(peerState.approvalResponses.length,1);
 assert.equal(peerState.approvalResponses[0].decision,'allow');
 const grantBinding=peerState.bridgeCalls.find(row=>row.action==='coordination.grant');
 assert.equal(grantBinding._runtimeSessionId,current.selected);
 assert.equal(grantBinding._generationId,peerState.contexts[current.selected].generationId);
 assert.deepEqual(grantBinding._inputBindings.map(row=>row.inputId),[human.inputId]);
 assert.equal(peerState.events.filter(row=>row.sessionId===current.selected&&row.event==='input.delivered'&&row.inputId===human.inputId).length,1);
 await composer.fill('Preserve this unsent draft');
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'session-details'}}));
 await page.getByRole('button',{name:'Tasks and workers',exact:true}).click();
 const related=page.getByRole('region',{name:'Related work'});
 await related.getByText('Authorize collaboration',{exact:true}).click();
 await expect(related.getByLabel('Allow necessary idle starts')).not.toBeChecked();
 await expect(related.getByLabel('Allow durable task chats')).toBeDisabled();
 await related.getByLabel('Allow necessary idle starts').check();
 await expect(related.getByLabel('Allow durable task chats')).toBeEnabled();
 await related.getByLabel('Allow durable task chats').check();
 await related.getByLabel('Allow necessary idle starts').uncheck();
 await expect(related.getByLabel('Allow durable task chats')).not.toBeChecked();
 await expect(related.getByLabel('Allow durable task chats')).toBeDisabled();
 await related.getByLabel('Peer conversation').selectOption(current.other);
 await related.getByLabel('Current collaboration grant').selectOption(grant);
 await related.getByLabel('Peer message',{exact:true}).fill('Consult the peer without a human relay');
 await related.getByRole('button',{name:'Send peer message'}).click();
 await expect(related.getByText('Peer message: notified',{exact:true})).toBeVisible();
 await related.getByText('Commission durable task',{exact:true}).click();
 await related.getByLabel('Task chat title').fill('Fixture implementation');
 await related.getByLabel('Peer message',{exact:true}).fill('Candidate version one');
 await related.getByRole('button',{name:'Create task chat'}).click();
 await expect(related.getByText('Task chat retained. Admission and final qualification are separate.',{exact:true})).toBeVisible();
 peerState=await inspect();
 await expect(related.getByText(/Fixture implementation · created by Selected conversation/)).toBeVisible();
 assert.equal(peerState.tasks.length,1);
 assert.equal((await readCandidate()).text,'Candidate version one');
 const task=peerState.tasks[0];
 assert.equal(task.collaboration.creatorSessionId,current.selected);
 assert.equal(task.collaboration.grantId,grant);
 const brief=task.collaboration.requestId+':brief';
 const taskGeneration=peerState.contexts[task.id].generationId;
 assert.equal(peerState.contexts[task.id].active,true);
 assert.equal(peerState.statuses[task.id],'working');
 assert.deepEqual(peerState.contexts[task.id].inputIds,[brief]);
 assert.equal(peerState.sent.filter(row=>row.inputId===brief).length,1);
 const correctionArgs={sessionId:task.id,grantId:grant,mode:'steer',text:'Candidate version two'};
 const correction=await agent('source','coordination.send',correctionArgs,'browser-correction');
 assert.equal(correction.delivery,'applied');
 assert.equal(correction.targetGenerationId,taskGeneration);
 assert.equal(correction.steering.input_id,'browser-correction');
 assert.equal(correction.steering.target_generation_id,taskGeneration);
 peerState=await inspect();
 assert.equal(peerState.contexts[task.id].generationId,taskGeneration);
 assert.deepEqual(peerState.contexts[task.id].inputIds,[brief,'browser-correction']);
 assert.equal(peerState.events.filter(row=>row.sessionId===task.id&&row.event==='generation.started').length,1);
 assert.equal(peerState.stops.length,1); // Only the earlier explicit worker interrupt.
 assert.match(peerState.sent.find(row=>row.inputId==='browser-correction').text,/not a new human instruction/);
 const candidate=await readCandidate();
 assert.equal(candidate.text,'Candidate version two');assert.equal(candidate.sha256,sha256(candidate.text));
 assert.ok(candidate.path.endsWith('/'+task.collaboration.outputNamespace+'/candidate.txt'));
 const references=[candidate.path+'@sha256:'+candidate.sha256];
 const declaration=await agent('recipient','coordination.reply',{
  requestId:'browser-correction',kind:'result',outcome:'success',
  text:'Deterministic candidate version two retained; independently check the file.',references,
 },'browser-reply');
 assert.equal(declaration.result.status,'staged');assert.equal(declaration.result.qualified,false);
 const replyBinding=(await inspect()).bridgeCalls.find(row=>row.id==='browser-reply');
 assert.equal(replyBinding._runtimeSessionId,task.id);
 assert.equal(replyBinding._generationId,taskGeneration);
 assert.deepEqual(replyBinding._inputBindings.map(row=>row.inputId),[brief,'browser-correction']);
 const wait=await agent('source','coordination.subscribe',{
  sessionId:current.selected,grantId:grant,requestId:'browser-correction',
 },'browser-wait');
 assert.equal(wait.accepted,true);assert.equal(wait.result.status,'waiting');
 const continuation=wait.result.continuationId;
 const staged=await agent('source','coordination.result',{requestId:'browser-correction'},'browser-result-staged');
 assert.equal(staged.result.qualified,false);assert.equal(staged.result.qualificationSupported,true);
 assert.equal((await inspect()).sent.filter(row=>row.inputId===continuation).length,0);
 const terminal=(await finish()).terminal;
 assert.equal(terminal.generation_id,taskGeneration);
 assert.deepEqual(terminal.input_ids,[brief,'browser-correction']);
 assert.equal(terminal.rootSessionId,task.id);assert.equal(terminal.sessionId,task.id);
 assert.equal(terminal.disposition,'manager_turn_finished');assert.deepEqual(terminal.active_job_ids,[]);
 const anchor=terminal.nativeTerminal;
 assert.equal(anchor.deterministicEmulation,true);
 assert.equal(anchor.rootSessionId,task.id);assert.equal(anchor.generationId,taskGeneration);
 assert.equal(anchor.textDigest,sha256(JSON.stringify(terminal.text)));
 const canonical='['+[task.id,anchor.nativeIndex,'assistant',terminal.text].map(value=>JSON.stringify(value)).join(', ')+']';
 assert.equal(anchor.messageId,sha256(canonical).slice(0,32));
 const qualified=await agent('source','coordination.result',{requestId:'browser-correction'},'browser-result');
 assert.equal(qualified.result.qualified,true);assert.equal(qualified.result.qualificationSupported,true);
 assert.equal(qualified.result.results.length,1);
 const exact=qualified.result.results[0];
 assert.equal(exact.sessionId,task.id);assert.equal(exact.inputId,'browser-correction');
 assert.equal(exact.messageId,anchor.messageId);
 assert.equal(exact.declaration.terminalMessageId,anchor.messageId);
 assert.equal(exact.declaration.generationId,taskGeneration);
 assert.equal(exact.declaration.status,'sealed');
 assert.deepEqual(exact.declaration.references,references);
 assert.equal(exact.declaration.independentArtifactVerification,false);
 await expect.poll(async()=>{
  const state=await inspect();
  return state.sent.filter(row=>row.inputId===continuation).length===1
   &&state.statuses[current.selected]==='idle'
   &&state.coordination.requests.find(row=>row.requestId===continuation)?.delivery==='accepted';
 },{timeout:10000}).toBe(true);
 peerState=await inspect();
 const resumed=peerState.sent.find(row=>row.inputId===continuation);
 assert.equal(resumed.sessionId,current.selected);assert.equal(resumed.kind,'peer');
 assert.equal(resumed.peerEnvelope.replyToRequestId,'browser-correction');
 assert.equal(resumed.peerEnvelope.grantId,grant);
 assert.deepEqual(resumed.peerEnvelope.references,[anchor.messageId,...references]);
 assert.match(resumed.text,/Independently check the referenced artifact/);
 assert.deepEqual(peerState.terminals[current.selected].input_ids,[continuation]);
 assert.equal(peerState.terminals[current.selected].disposition,'manager_turn_finished');
 assert.equal(peerState.contexts[current.selected].active,false);
 assert.equal(peerState.events.filter(row=>row.sessionId===current.selected&&row.event==='generation.started'&&row.generation_id==='fixture-generation:'+continuation).length,1);
 assert.equal(peerState.events.filter(row=>row.sessionId===current.selected&&row.event==='input.delivered'&&row.inputId===continuation).length,1);
 assert.deepEqual(await readCandidate(),candidate); // Independent read after qualification, not just declaration prose.
 await finish(); // A duplicated terminal event must not wake the sender twice.
 assert.equal((await inspect()).sent.filter(row=>row.inputId===continuation).length,1);
 await related.getByRole('button',{name:'Refresh related work',exact:true}).click();
 const correctionRow=related.locator('[data-request-id="browser-correction"]');
 await expect(correctionRow).toHaveCount(1);
 await correctionRow.getByRole('button',{name:'Inspect exact result',exact:true}).click();
 await expect(correctionRow.locator('[data-part="exact-result"]')).toContainText('Result: sealed · success · Qualified terminal evidence');
 await expect(correctionRow).toContainText('not independently verified correctness');
 await expect(correctionRow.getByRole('button',{name:'Open exact terminal message'})).toHaveAttribute('data-message-id',anchor.messageId);
 await correctionRow.getByRole('button',{name:'Open exact terminal message'}).click();
 await expect(page.getByRole('log',{name:'Conversation messages'}).getByText(terminal.text,{exact:true})).toBeVisible();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.messageFocus.messageId),anchor.messageId);
 await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),current.selected);
 await related.getByRole('button',{name:'Refresh related work',exact:true}).click();
 await page.reload();
 await expect(related.locator('[data-request-id="browser-correction"]')).toHaveCount(1);
 await expect(related.locator('[data-request-id="browser-correction"] [data-part="exact-result"]')).toContainText('Qualified terminal evidence');
 await expect(second.locator('[data-report-id="b-report-1"]')).toHaveCount(1);
 await expect(first.locator('[data-report-id="a-report-2"]')).toHaveCount(1);
 const retained=await agent('source','coordination.result',{requestId:'browser-correction'},'browser-result-reloaded');
 assert.deepEqual(retained.result.results,qualified.result.results);
 const duplicate=await agent('source','coordination.send',correctionArgs,'browser-correction');
 assert.equal(duplicate.duplicate,true);
 const adjacent=await agent('source','coordination.send',{
  sessionId:task.id,grantId:grant,mode:'queue',text:'Adjacent retained exchange',
 },'browser-adjacent');
 assert.equal(adjacent.delivery,'accepted');
 const adjacentCandidate=await readCandidate();
 assert.equal(adjacentCandidate.text,'Adjacent retained exchange');
 assert.equal(adjacentCandidate.sha256,sha256(adjacentCandidate.text));
 assert.equal(adjacentCandidate.path,candidate.path);
 const adjacentReferences=[adjacentCandidate.path+'@sha256:'+adjacentCandidate.sha256];
 const adjacentReply=await agent('recipient','coordination.reply',{
  requestId:'browser-adjacent',kind:'result',outcome:'success',
  text:'Deterministic adjacent exchange retained.',references:adjacentReferences,
 },'browser-adjacent-reply');
 assert.equal(adjacentReply.result.status,'staged');
 const adjacentWait=await agent('source','coordination.subscribe',{
  sessionId:current.selected,grantId:grant,requestId:'browser-adjacent',
 },'browser-adjacent-wait');
 assert.equal(adjacentWait.accepted,true);
 assert.notEqual(adjacentWait.result.continuationId,continuation);
 const adjacentTerminal=(await finish()).terminal;
 assert.deepEqual(adjacentTerminal.input_ids,['browser-adjacent']);
 assert.notEqual(adjacentTerminal.generation_id,taskGeneration);
 const adjacentResult=await agent('source','coordination.result',{requestId:'browser-adjacent'},'browser-adjacent-result');
 assert.equal(adjacentResult.result.qualified,true);
 assert.equal(adjacentResult.result.results.length,1);
 assert.equal(adjacentResult.result.results[0].messageId,adjacentTerminal.nativeTerminal.messageId);
 assert.equal(adjacentResult.result.results[0].sessionId,task.id);
 assert.equal(adjacentResult.result.results[0].inputId,'browser-adjacent');
 assert.deepEqual(adjacentResult.result.results[0].declaration.references,adjacentReferences);
 await expect.poll(async()=>{
  const state=await inspect();
  return state.sent.filter(row=>row.inputId===adjacentWait.result.continuationId).length===1
   &&state.statuses[current.selected]==='idle'
   &&state.coordination.requests.find(row=>row.requestId===adjacentWait.result.continuationId)?.delivery==='accepted';
 },{timeout:10000}).toBe(true);
 await finish();
 await related.getByRole('button',{name:'Refresh related work',exact:true}).click();
 await expect(related.locator('[data-request-id="browser-adjacent"]')).toHaveCount(1);
 await expect(correctionRow).toHaveCount(1);
 peerState=await inspect();
 assert.equal(peerState.sent.filter(row=>row.inputId==='browser-correction').length,1);
 assert.equal(peerState.sent.filter(row=>row.inputId===brief).length,1);
 assert.equal(peerState.sent.filter(row=>row.inputId==='browser-adjacent').length,1);
 assert.equal(peerState.sent.filter(row=>row.inputId===continuation).length,1);
 assert.equal(peerState.sent.filter(row=>row.inputId===adjacentWait.result.continuationId).length,1);
 assert.equal(peerState.sent.filter(row=>row.kind==='human').length,1);
 assert.equal(peerState.approvals.length,1);assert.equal(peerState.approvalResponses.length,1);
 assert.equal(peerState.coordination.grants.length,1);assert.equal(peerState.humanMessages.length,1);
 assert.equal(peerState.tasks.length,1);assert.equal(peerState.stops.length,1);
 assert.equal(peerState.messages.length,1);
 assert.equal(peerState.statuses[task.id],'idle');assert.equal(peerState.statuses[current.selected],'idle');
 const adjacentContinuation=peerState.sent.find(row=>row.inputId===adjacentWait.result.continuationId);
 assert.equal(adjacentContinuation.peerEnvelope.replyToRequestId,'browser-adjacent');
 assert.deepEqual(adjacentContinuation.peerEnvelope.references,[adjacentTerminal.nativeTerminal.messageId,...adjacentReferences]);
 assert.deepEqual(peerState.terminals[current.selected].input_ids,[adjacentWait.result.continuationId]);
 assert.deepEqual(peerState.failures,[]);
 assert.equal(peerState.nativeRuntime,false);assert.equal(peerState.providerCalls,false);
 assert.equal((await readCandidate()).text,'Adjacent retained exchange');
 const oldAgain=await agent('source','coordination.result',{requestId:'browser-correction'},'browser-result-after-adjacent');
 assert.deepEqual(oldAgain.result.results,qualified.result.results);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),current.selected);
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await expect(composer).toHaveValue('Preserve this unsent draft');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,actualService:true,twoTargets:true,followupExactTarget:true,cursorReconnect:true,failedReadReconnect:true,noDuplicateReports:true,noRepeatedSubmission:true,interruptWhileWaiting:true,selectionAndDraftPreserved:true,mobileNoOverflow:true,peerGrant:true,naturalComposerGrant:true,approvalOnce:true,runtimeOwnedBindings:true,durableTask:true,correction:true,inFlightSteer:true,exactResultLinks:true,artifactIndependentlyRead:true,adjacentReconnect:true,qualifiedFinal:true,automaticDependencyContinuation:true,continuationOnce:true,completeLoop:true,deterministicRuntime:true,emulatedTerminalAnchor:true,nativeRuntime:false,liveModelEvidence:false,providerCalls:false}));
}catch(error){await page?.screenshot({path:"/tmp/amplifier-coordination-failure.png"});console.error((await page?.locator("body").innerText())?.slice(-5000));throw error}finally{await browser?.close();fixture.kill()}
