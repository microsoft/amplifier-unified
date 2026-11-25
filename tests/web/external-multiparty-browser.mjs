// Built client, real host and independent ACP transports; no model/account calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {fixturePython} from './fixture-python.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'/.venv/bin/python'),[root+'/tests/fixtures/external_multiparty_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'External multiparty fixture'});
const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS;
const report={passed:false,posts:[]};let browser;
try{
 const {url,workspace,legacyId}=await new Promise((resolve,reject)=>{
  let output='';const timeout=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timeout);resolve(row)}}catch{}});
  fixture.once('error',reject);fixture.once('exit',code=>reject(Error('Fixture exited '+code)));
 });
 browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 const page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{
  if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
  const raw=request.postDataJSON(),body=raw.action==='shell.command'?{...raw,action:raw.args.action,args:raw.args.args}:raw;
  if(['conversation.send','conversation.stop','session.create','conversation.retry'].includes(body.action))report.posts.push(body);
 });
 await page.goto(url);
 await page.waitForFunction(()=>window.amplifier?.dispatch);
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 await action('session.draft',{workspace});
 await page.getByRole('combobox',{name:'Agent for new chat',exact:true}).selectOption('alpha');
 await page.getByRole('button',{name:'Add agent',exact:true}).click();
 await page.getByRole('combobox',{name:'Additional agent 1',exact:true}).selectOption('beta');
 await expect(page.getByText('Start a shared chat. Everyone can reply; address an agent by name when useful.',{exact:true})).toBeVisible();
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 const recipient=page.getByRole('combobox',{name:'Message recipient',exact:true});
 const observe=()=>page.request.get(url+'/fixture/participants').then(response=>response.json());
 const idle=async id=>expect.poll(async()=>{const row=(await observe()).sessions.find(row=>row.id===id);return row?.status==='idle'&&!row.externalAgent.pendingCount}).toBe(true);
 const work=(snapshot,identity)=>snapshot.peers[identity].filter(row=>row.kind==='work');
 const send=async text=>{await composer.fill(text);await page.getByRole('button',{name:'Send message',exact:true}).click()};
 // The maintained shared peer keeps Beta busy while Alpha posts a question.
 // Releasing that test-only hold permits automatic consideration of the peer
 // source, without a second human message or a forced work prompt.
 await send('busy exchange');
 await expect(page.getByText('Question for Beta: check 42',{exact:true})).toBeVisible();
 await expect(page.getByRole('article',{name:'Beta activity'})).toContainText('Beta working');
 const sharedId=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await expect(recipient).toHaveCount(0);
 await expect.poll(async()=>(await observe()).sessions.find(row=>row.id===sharedId).sourceDeliveries.some(row=>row.agentId==='beta'&&row.sequence===2&&row.state==='pending')).toBe(true);
 assert.equal(work(await observe(),'beta').length,1);
 assert.equal(report.posts.filter(row=>row.action==='conversation.send').length,1);
 assert.equal((await page.request.post(url+'/fixture/release-beta')).ok(),true);
 await expect(page.getByText('Beta answer: 42',{exact:true})).toBeVisible();await idle(sharedId);
 const sharedFirst=await observe(),shared=sharedFirst.sessions.find(row=>row.id===sharedId);
 assert.equal(shared.externalAgent.participation,'shared');
 assert.deepEqual(work(sharedFirst,'alpha').map(row=>row.source),['busy exchange']);
 assert.deepEqual(work(sharedFirst,'beta').map(row=>row.source),['busy exchange','Question for Beta: check 42']);
 const question=shared.messages.find(row=>row.agentId==='alpha'),answer=shared.messages.find(row=>row.text==='Beta answer: 42');
 assert.equal(answer.agentId,'beta');assert.equal(answer.sourceMessageId,question.id);
 assert.equal(work(sharedFirst,'beta')[1].sourceId,question.id);assert.equal(work(sharedFirst,'beta')[1].sender,'alpha');
 assert.equal(shared.messages.filter(row=>row.role==='user').length,1);
 assert.equal(shared.messages.some(row=>row.text.includes('Private reasoning')||row.text.includes('"decision"')),false);
 const sharedBindings=shared.externalAgent.participants.map(row=>row.sessionId);assert.equal(new Set(sharedBindings).size,2);
 await page.reload();await expect(page.getByText('Beta answer: 42',{exact:true})).toBeVisible();
 assert.deepEqual((await observe()).peers,sharedFirst.peers);
 // Change each independent peer through its own standard ACP session.
 for(const [width,identity] of [[1280,'alpha'],[390,'beta']]){
  const phone=width===390?await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined}):null;
  try{
  const editor=phone?await phone.newPage({viewport:{width,height:844},hasTouch:true,isMobile:true,
   extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}):page;
  if(phone){
   await editor.goto(url);await editor.waitForFunction(()=>window.amplifier?.dispatch);
   await editor.evaluate(id=>window.amplifier.dispatch('session.select',{id}),sharedId);
  }
  const settings=editor.getByRole('button',{name:'Agent settings',exact:true});
  const panel=editor.getByRole('dialog',{name:'Agent settings',exact:true});
  const input=editor.getByRole('textbox',{name:'Message Amplifier',exact:true});
  await expect(settings).toBeVisible();
  const centerY=async locator=>{const {y,height}=await locator.boundingBox();return y+height/2};
  assert.ok(Math.abs(await centerY(settings.locator('span'))-await centerY(settings))<=1,'Trigger text is vertically centered');
  assert.ok(Math.abs(await centerY(settings)-await centerY(editor.locator('[data-part="composer-primary-action"]')))<=2,'Settings and the send control share a center line');
  const before=await input.boundingBox();
  await settings.click();
  await panel.getByLabel('Agent',{exact:true}).selectOption(identity);
  await expect(panel.getByLabel('Connection')).toHaveValue('one');
  const after=await input.boundingBox();
  assert.deepEqual(after,before);
  for(const [label,value] of [['Connection','two'],['Model','review'],['Effort','high']]){
   const selector=panel.getByLabel(label,{exact:true});
   await selector.selectOption(value);
   await expect(selector).toHaveValue(value);
   await expect(selector).toBeEnabled();
  }
  await expect(panel.getByRole('button',{name:/^Keep /})).toHaveCount(0);
  const snapshot=await observe();
  const changes=snapshot.peers[identity].filter(row=>row.kind==='configure');
  assert.equal(changes.length,3);
  assert.ok(changes.every(row=>row.sessionId===sharedBindings[identity==='alpha'?0:1]));
  assert.deepEqual(changes.at(-1).settings,{provider:'two',model:'review',effort:'high'});
  if(identity==='alpha')assert.equal(snapshot.peers.beta.filter(row=>row.kind==='configure').length,0);
  if(artifacts)await editor.screenshot({path:join(artifacts,'agent-settings-'+width+'.png')});
  await expect(panel.getByText('Agent settings',{exact:true})).toHaveCount(0);
  await expect(panel.getByRole('button',{name:/close|dismiss/i})).toHaveCount(0);
  if(phone){
   const popup=await panel.boundingBox();
   await editor.touchscreen.tap(popup.x+popup.width/2,popup.y-8);
  }else await editor.keyboard.press('Escape');
  await expect(panel).toBeHidden();
  if(!phone)await expect(settings).toBeFocused();
  }finally{await phone?.close()}
 }
 await page.setViewportSize({width:1280,height:950});
 // Separate browser clients keep private drafts and navigation, even while
 // they inspect the same public room. Neither draft is submitted to a peer.
 const afterSettings=(await observe()).peers;
 const secondBrowser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined});
 const other=await secondBrowser.newPage({viewport:{width:1280,height:950},
  extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 try{
  await other.goto(url);await other.waitForFunction(()=>window.amplifier?.dispatch);
  await other.evaluate(id=>window.amplifier.dispatch('session.select',{id}),sharedId);
  const otherComposer=other.getByRole('textbox',{name:'Message Amplifier',exact:true});
  await composer.fill('First private draft — never public');
  await otherComposer.fill('Second private draft — never public');
  await expect(composer).toHaveText('First private draft — never public');
  await other.evaluate(id=>window.amplifier.dispatch('session.select',{id}),legacyId);
  assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),sharedId);
  await expect(composer).toHaveText('First private draft — never public');
  await other.evaluate(id=>window.amplifier.dispatch('session.select',{id}),sharedId);
  await expect(otherComposer).toHaveText('Second private draft — never public');
  assert.deepEqual((await observe()).peers,afterSettings);
  report.privateClientViews={separateDrafts:true,separateNavigation:true,noPeerPrompt:true};
 }finally{await secondBrowser.close()}
 // Both streamed drafts remain attributed; a public post while busy is not
 // forced into either current prompt. Room Stop cancels current and queued work.
 await send('wait-for-stop');
 await expect(page.getByRole('article',{name:'Alpha activity'})).toContainText('alpha unfinished draft');
 await expect(page.getByRole('article',{name:'Beta activity'})).toContainText('beta unfinished draft');
 await send('pending while busy');
 await expect.poll(async()=>(await observe()).sessions.find(row=>row.id===sharedId).externalAgent.pendingCount).toBe(2);
 await page.getByRole('button',{name:'Stop response',exact:true}).click();await idle(sharedId);
 const sharedStopped=await observe(),stoppedRoom=sharedStopped.sessions.find(row=>row.id===sharedId);
 assert.equal(stoppedRoom.messages.filter(row=>row.partial).length,2);
 assert.ok(stoppedRoom.messages.filter(row=>row.partial).every(row=>!row.roomSequence));
 for(const identity of ['alpha','beta']){
  assert.equal(sharedStopped.peers[identity].filter(row=>row.kind==='cancel').length,1);
  assert.equal(work(sharedStopped,identity).some(row=>row.source==='pending while busy'),false);
 }
 await send('after room stopped');
 await expect(page.getByText('alpha public answer: after room stopped',{exact:true})).toBeVisible();
 await expect(page.getByText('beta public answer: after room stopped',{exact:true})).toBeVisible();await idle(sharedId);
 const sharedFinal=await observe();
 assert.deepEqual(work(sharedFinal,'alpha').map(row=>row.source),['busy exchange','wait-for-stop','after room stopped']);
 assert.deepEqual(work(sharedFinal,'beta').map(row=>row.source),['busy exchange','Question for Beta: check 42','wait-for-stop','after room stopped']);
 assert.deepEqual(sharedFinal.sessions.find(row=>row.id===sharedId).externalAgent.participants.map(row=>row.sessionId),sharedBindings);
 assert.ok(report.posts.filter(row=>row.action==='conversation.send').every(row=>!Object.hasOwn(row.args,'recipientAgentId')));
 if(artifacts){await mkdir(artifacts,{recursive:true});await page.screenshot({path:join(artifacts,'shared-room-desktop.png'),animations:'disabled'});}
 await page.setViewportSize({width:390,height:844});await expect(composer).toBeVisible();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
 if(artifacts)await page.screenshot({path:join(artifacts,'shared-room-mobile.png'),animations:'disabled'});
 await page.setViewportSize({width:1280,height:950});
 report.shared={id:sharedId,bindings:sharedBindings,publicSourceToPeer:true,separateStreams:true,roomStopAndLaterPost:true,reloadWithoutReplay:true};
 // Explicitly seeded unmarked history retains the earlier directed controls.
 await action('session.select',{id:legacyId});
 await expect(recipient).toHaveValue('legacy-alpha');
 await composer.fill('shared result 42');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(page.getByText('Reply: shared result 42',{exact:true})).toBeVisible();
 const sid=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await expect(recipient).toHaveValue('legacy-alpha');
 await recipient.selectOption('legacy-beta');
 await composer.fill('consider Alpha’s result');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(page.getByText('Reply: consider Alpha’s result',{exact:true})).toBeVisible();
 const first=await observe(),room=first.sessions.find(row=>row.id===sid);
 assert.equal(Object.hasOwn(room.externalAgent,'participation'),false);
 assert.equal(room.externalAgent.participants.length,2);
 const identities=room.externalAgent.participants.map(row=>row.sessionId);
 assert.equal(new Set(identities).size,2);
 assert.deepEqual(room.messages.map(row=>row.agentId||row.recipientAgentId),['legacy-alpha','legacy-alpha','legacy-beta','legacy-beta']);
 const betaPrompt=first.peers['legacy-beta'].find(row=>row.kind==='prompt');
 const context=JSON.parse(betaPrompt.context[0].split('\n',2)[1]);
 assert.equal(context.messages.at(-1).agentId,'legacy-alpha');
 assert.equal(context.messages.at(-1).text,'Reply: shared result 42');
 // Reload reuses the same host and remote identities; it never resubmits work.
 await page.reload();await expect(recipient).toHaveValue('legacy-beta');
 const reloaded=await observe();assert.deepEqual(reloaded.peers,first.peers);
 assert.deepEqual(reloaded.sessions.find(row=>row.id===sid).externalAgent.participants.map(row=>row.sessionId),identities);
 await recipient.selectOption('legacy-alpha');
 await composer.fill('wait-for-stop');await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(recipient).toBeDisabled();
 await page.waitForFunction(()=>{const state=window.amplifier.getState();return state.sessions.find(row=>row.id===state.selectedSessionId)?.streaming==='Reply: '});
 await page.getByRole('button',{name:'Stop response',exact:true}).click();
 await expect(recipient).toBeEnabled();
 const stopped=await observe();
 assert.equal(stopped.peers['legacy-alpha'].filter(row=>row.kind==='cancel').length,1);
 assert.equal(stopped.peers['legacy-beta'].filter(row=>row.kind==='cancel').length,0);
 assert.equal(stopped.sessions.find(row=>row.id===sid).messages.at(-1).agentId,'legacy-alpha');
 await recipient.selectOption('legacy-beta');await composer.fill('after Alpha stopped');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(page.getByText('Reply: after Alpha stopped',{exact:true})).toBeVisible();
 const final=await observe();
 assert.equal(final.peers['legacy-alpha'].filter(row=>row.kind==='prompt').length,2);
 assert.equal(final.peers['legacy-beta'].filter(row=>row.kind==='prompt').length,2);
 assert.deepEqual(final.nativeStarts,[]);assert.deepEqual(final.nativeSends,[]);assert.deepEqual(errors,[]);
 assert.equal(report.posts.filter(row=>row.action==='conversation.send').length,8);
 assert.equal(report.posts.filter(row=>row.action==='conversation.retry').length,0);
 report.legacyDirectedPreserved=true;report.final=final;
 if(artifacts){await mkdir(artifacts,{recursive:true});await page.screenshot({path:join(artifacts,'multiparty-desktop.png'),animations:'disabled'});}
 await page.setViewportSize({width:390,height:844});await expect(recipient).toBeVisible();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
 if(artifacts)await page.screenshot({path:join(artifacts,'multiparty-mobile.png'),animations:'disabled'});
 report.passed=true;
 console.log(JSON.stringify({passed:true,sharedRoom:true,autonomousPeerResponse:true,independentStreams:true,roomStopAndLaterPost:true,legacyDirectedPreserved:true,noNativeWorker:true,mobileNoOverflow:true}));
}finally{
 try{if(artifacts){await mkdir(artifacts,{recursive:true});await writeFile(join(artifacts,'result.json'),JSON.stringify(report,null,2));}}
 finally{await finishFixture({label:'External multiparty fixture',close:[['Browser',()=>browser?.close()]],stop:stopFixture})}
}
