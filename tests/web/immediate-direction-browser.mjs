import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdirSync,writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

// Production built assets + real subprocess Worker/Core/loop. Only external
// transports are scripted. Neither send nor steering uses window.dispatch.
const output=process.env.AMPLIFIER_DIRECTION_EVIDENCE;
if(output)mkdirSync(output,{recursive:true});
const results=[];
for(const scenario of (process.argv.length>2?process.argv.slice(2):['native','ordinary','tool','completion','stale-config','stop','native-unknown','native-stop'])){
 const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),['-I',fileURLToPath(new URL('../../tests/fixtures/immediate_direction_ui_server.py',import.meta.url))],{env:{...process.env,DIRECTION_SCENARIO:scenario},stdio:['ignore','pipe','inherit']});
 let browser,page,inspect,ready,stdout='';fixture.stdout.on('data',data=>stdout+=data);
 try{
  ready=await new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>reject(Error('Fixture timeout: '+stdout)),45000);
   const inspect=()=>{for(const line of stdout.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}};
   fixture.stdout.on('data',inspect);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code+': '+stdout))});inspect();
  });
  browser=await chromium.launch({headless:true});
  console.log('Running '+scenario+' composer scenario at '+ready.url);
  const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  page=await context.newPage();const errors=[],submissions=[],receipts=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.url().endsWith('/api/actions')){const data=request.postDataJSON();if(data?.action==='conversation.send')submissions.push(data)}});
  page.on('response',async response=>{
   if(!response.url().endsWith('/api/actions'))return;
   const data=response.request().postDataJSON();
   if(data?.action==='conversation.send')receipts.push({id:data.id,status:response.status(),body:await response.json()});
  });
  inspect=async()=>await (await page.request.get(ready.url+'/fixture')).json();
  const release=async stage=>{const response=await page.request.post(ready.url+'/fixture/release',{data:{stage}});assert.equal(response.status(),200)};
  await page.goto(ready.url);await showFixtureConversation(page);
  const composer=page.getByRole('textbox',{name:'Message Amplifier'});
  await composer.fill('ORIGINAL_HELD_MODEL');
  await page.getByRole('button',{name:'Send message',exact:true}).click();
  await expect.poll(async()=>{const v=await inspect();if(v.session.status==='error')throw Error(JSON.stringify(v.session.failure||v.session.error));return scenario==='ordinary'?v.wire.some(r=>r.kind==='ordinary-request'):v.wire.some(r=>r.kind==='wire'&&r.payload.type==='response.create')},{timeout:45000}).toBe(true);
  if(scenario==='tool')await expect.poll(async()=>(await inspect()).toolRunning,{timeout:15000}).toBe(true);
  await expect.poll(async()=>(await inspect()).session.collaborationGeneration?.terminal).toBe(false);
  const original=await inspect(),parent=original.session.collaborationGeneration.id;
  const originalEpoch=original.session.interruptionRevision??0;
  let intercepted,releaseSend;
  const stale=['stale-config','stop'].includes(scenario),crossesCompletion=scenario==='completion';
  if(stale||crossesCompletion){
   const held=new Promise(resolve=>releaseSend=resolve);
   intercepted=new Promise(resolve=>page.route('**/api/actions',async route=>{
    const data=route.request().postDataJSON();
    if(data?.action==='conversation.send'&&data.args.text==='DIRECTION_FROM_NORMAL_COMPOSER'){resolve();await held}
    await route.continue();
   }));
  }
  await composer.fill('DIRECTION_FROM_NORMAL_COMPOSER');
  await page.getByRole('button',{name:'Send message',exact:true}).click();
  const message=page.locator('.a-user').filter({hasText:'DIRECTION_FROM_NORMAL_COMPOSER'});
  await expect(message).toHaveCount(1);
  if(stale){
   await intercepted;
   // Keep the actual pre-admission Stop race. A route-free message still
   // carries its original cancellation epoch; never relax this into a new turn.
   if(scenario==='stop')await page.getByRole('button',{name:'Stop response',exact:true}).click();
   else await release('complete');
   await expect.poll(async()=>(await inspect()).session.collaborationGeneration.terminal).toBe(true);
   if(scenario==='stale-config'){
    const configured=await page.evaluate(async()=>{
     const state=window.amplifier.getState(),session=state.sessions.find(s=>s.id===state.selectedSessionId);
     return await window.amplifier.dispatch('conversation.configure',{sessionId:session.id,expectedRevision:0,bundle:session.bundle});
    });
    assert.equal(configured.accepted,true);
   }
   releaseSend();
   await expect(message).toContainText('Not sent');
   await expect(message).toContainText(scenario==='stop'?'prepared before Stop':'settings changed');
   if(scenario==='stop')await expect.poll(()=>receipts.some(row=>row.id===submissions[1].id&&row.body.code==='input_stopped')).toBe(true);
   else assert.equal((await inspect()).session.interruptionRevision??0,originalEpoch,'Configuration refusal is independent of Stop');
   assert.equal((await inspect()).wire.filter(r=>r.kind==='wire'&&r.payload.type==='response.steer').length,0);
  }else if(crossesCompletion){
   await intercepted;
   await release('complete');
   await expect.poll(async()=>(await inspect()).session.collaborationGeneration.terminal).toBe(true);
   assert.equal((await inspect()).session.interruptionRevision??0,originalEpoch);
   releaseSend();
   await expect(page.getByText('NEXT_TURN_FINAL',{exact:true})).toHaveCount(1);
   await expect(message).not.toContainText('Not sent');
   await expect.poll(async()=>(await inspect()).session.collaborationGeneration.id).not.toBe(parent);
   const completed=await inspect();
   assert.equal(completed.session.messages.filter(m=>m.inputId===submissions[1].id).length,1);
   assert.equal(completed.wire.filter(r=>r.kind==='wire'&&r.payload.type==='response.create').length,2);
   assert.equal(completed.wire.filter(r=>r.kind==='wire'&&r.payload.type==='response.steer').length,0);
  }else if(['native','native-stop'].includes(scenario)){
   await expect.poll(async()=>(await inspect()).wire.filter(r=>r.kind==='wire'&&r.payload.type==='response.steer').length,{timeout:15000}).toBe(1);
   const before=await inspect();assert.ok(!before.wire.some(r=>r.kind==='parent-terminal'));
   assert.equal(before.session.collaborationGeneration.id,parent);assert.equal(before.session.collaborationGeneration.terminal,false);
   await expect(message).toContainText('Sending live direction');
   await release('accept');await expect(message).toContainText('Accepted; applying live direction');
   await expect(message).not.toContainText('waiting for its next step');
   await expect(message).not.toContainText('Live direction applied');
   if(output)await page.screenshot({path:output+'/native-accepted.png'});
   if(scenario==='native-stop'){
    await release('pending');
    await expect(message).toContainText('queued at the provider');
    await expect.poll(async()=>(await inspect()).toolRunning).toBe(true);
    await page.getByRole('button',{name:'Stop response',exact:true}).click();
    await expect.poll(async()=>(await inspect()).session.collaborationGeneration.terminal).toBe(true);
    await expect.poll(async()=>(await inspect()).session.messages.find(m=>m.inputId===submissions[1].id)?.steering?.disposition).toBe('unknown');
    await expect(message).not.toContainText('Live direction applied');
   }else{
    await release('apply');await expect(message).toContainText('Live direction applied to the successor response');
    await release('finish');await expect(page.getByText('ONE_NATIVE_FINAL',{exact:true})).toHaveCount(1);
   }
  }else if(scenario==='native-unknown'){
   await expect.poll(async()=>(await inspect()).wire.filter(r=>r.kind==='wire'&&r.payload.type==='response.steer').length).toBe(1);
   await expect.poll(async()=>(await inspect()).session.messages.find(m=>m.inputId===submissions[1].id)?.steering?.disposition,{timeout:15000}).toBe('unknown');
   await expect(message).not.toContainText('Live direction applied');
   assert.equal((await inspect()).wire.filter(r=>r.kind==='wire'&&r.payload.type==='response.create').length,1);
   assert.equal((await inspect()).wire.filter(r=>r.kind==='ordinary-request').length,0,'Unknown native effect has no fallback request');
   const beforeCheck=(await inspect()).wire.length;
   await message.getByRole('button',{name:'Check delivery',exact:true}).click();
   await expect(message).toContainText('Nothing was resent');
   assert.equal((await inspect()).wire.length,beforeCheck,'Passive delivery check cannot resend');
  }else if(scenario==='ordinary'){
   await expect(message).toContainText('waiting for its next step');
   const before=await inspect();assert.equal(before.wire.filter(r=>r.kind==='ordinary-request').length,1);
   assert.ok(!before.wire.some(r=>r.kind==='wire'));
   await release('ordinary-release');await expect(page.getByText('ORDINARY_FINAL_2',{exact:true})).toHaveCount(1);
   await expect(message).toContainText('Added to the active run’s context');
  }else if(scenario==='tool'){
   await expect(message).toContainText('waiting for its next step');
   await expect(message).not.toContainText('Accepted;');
   assert.equal((await inspect()).wire.filter(r=>r.kind==='wire'&&r.payload.type==='response.steer').length,0);
   assert.equal((await inspect()).toolRunning,true);
   await release('tool-release');
   await expect(page.getByText('TOOL_BOUNDARY_FINAL',{exact:true})).toHaveCount(1);
   await expect(message).toContainText('Added to the active run’s context');
  }
  if(scenario!=='native-unknown')await expect.poll(async()=>(await inspect()).session.collaborationGeneration?.terminal,{timeout:15000}).toBe(true);
  const final=await inspect();
  if(!crossesCompletion)assert.equal(final.session.collaborationGeneration.id,parent,'No artificial successor turn');
  assert.equal(submissions.length,2,'Exactly two normal composer clicks');
  assert.equal(Object.hasOwn(submissions[1].args,'expectedGenerationId'),false);
  assert.equal(Object.hasOwn(submissions[1].args,'allowNextTurn'),false);
  assert.equal(submissions[1].args.expectedInterruptionRevision,originalEpoch);
  assert.equal(submissions[1].args.expectedConfigurationRevision,0);
  assert.equal(final.session.messages.filter(m=>m.text==='DIRECTION_FROM_NORMAL_COMPOSER').length,stale?0:1);
  if(scenario==='ordinary'){
   const requests=final.wire.filter(r=>r.kind==='ordinary-request');
   assert.equal(requests.length,2,'Boundary guidance continues without a tool or a third request');
   const payload=JSON.stringify(requests[1].payload);
   assert.equal(payload.split('DIRECTION_FROM_NORMAL_COMPOSER').length-1,1);
   assert.equal(payload.split('ORIGINAL_HELD_MODEL').length-1,1);
  }
  if(scenario==='tool'){
   const requests=final.wire.filter(r=>r.kind==='wire'&&r.payload.type==='response.create');
   assert.equal(requests.length,2);
   const payload=JSON.stringify(requests[1].payload);
   assert.equal(payload.split('DIRECTION_FROM_NORMAL_COMPOSER').length-1,1);
   assert.match(payload,/call-tool/,'The completed tool result is retained');
  }
  if(!stale){
   const duplicate=await page.request.post(ready.url+'/api/actions',{data:submissions[1]});
   const receipt=await duplicate.json();assert.equal(receipt.duplicate,true,'Same identity returns its recorded outcome');
   const afterDuplicate=await inspect();
   assert.equal(afterDuplicate.session.messages.filter(m=>m.inputId===submissions[1].id).length,1);
   assert.equal(afterDuplicate.wire.length,final.wire.length,'Duplicate Send cannot replay any provider transport');
  }
  await composer.fill('KEEP_NEW_PRIVATE_DRAFT');
  await page.reload();await showFixtureConversation(page);await expect(composer).toHaveDraft('KEEP_NEW_PRIVATE_DRAFT');
  assert.equal((await inspect()).wire.length,final.wire.length,'Reload must not replay unknown or held input');
  assert.deepEqual(errors,[]);
  results.push({scenario,passed:true,source:ready.source,host:ready.host,python:ready.python,submissions,receipts,observed:final});
  if(output)writeFileSync(output+'/'+scenario+'.json',JSON.stringify(results.at(-1),null,2)+'\n');
  console.log(scenario+' composer scenario passed');
 }catch(error){
  if(inspect){const observed=await inspect();console.error(scenario,JSON.stringify({status:observed.session.status,error:observed.session.error,failure:observed.session.failure,wireKinds:observed.wire.map(r=>r.kind)}));if(output)writeFileSync(output+'/'+scenario+'-failure.json',JSON.stringify(observed,null,2)+'\n')}
  if(output&&page)await page.screenshot({path:output+'/'+scenario+'-failure.png'});
  throw error;
 }finally{
  if(page&&ready)await page.request.post(ready.url+'/fixture/shutdown').catch(()=>{});
  if(browser)await browser.close();
  const exited=fixture.exitCode!==null?Promise.resolve(fixture.exitCode):new Promise(resolve=>fixture.once('exit',resolve));
  if(!ready)fixture.kill('SIGTERM');
  let timer;try{await Promise.race([exited,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Fixture cleanup timeout')),15000)})])}finally{clearTimeout(timer)}
  assert.match(stdout,/"cleanupConfirmed": true/);
  const result=results.at(-1);
  if(result?.scenario===scenario){result.cleanupConfirmed=true;if(output)writeFileSync(output+'/'+scenario+'.json',JSON.stringify(result,null,2)+'\n')}
 }
}
if(output)writeFileSync(output+'/result.json',JSON.stringify({passed:true,productionBuiltBrowser:true,externalTransportOnly:true,results},null,2)+'\n');
console.log('Immediate direction browser passed: '+results.map(row=>row.scenario).join(', ')+'. Production composer, real Worker/Core/loop, draft/reload and no replay.');
