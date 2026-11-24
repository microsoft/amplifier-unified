import {showFixtureConversation} from './browser-chat-pages.mjs';
import {fixturePython} from './fixture-python.mjs';
import './composer-test-helpers.mjs';
// Real HTTP/SSE and saved JSONL, with a synthetic runtime and temporary home.
import {chromium,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'.venv/bin/python'),['-u',root+'tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','pipe']});
let browser,page,releasePage,releaseLate,primary,stage='startup',log='';
const waits=[],cleanupErrors=[];
// Response waiters start before gestures. Attach rejection handlers immediately:
// a primary assertion must survive closing the browser before its response.
const observed=promise=>{promise.catch(()=>{});waits.push(promise);return promise};
fixture.stderr.on('data',chunk=>log=(log+chunk).slice(-16384));
try{
 const port=await new Promise((resolve,reject)=>{
  let output='';const timeout=setTimeout(()=>reject(Error('Fixture startup timed out: '+log)),30000);
  fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line){clearTimeout(timeout);resolve(JSON.parse(line).port)}});
  fixture.once('exit',code=>{clearTimeout(timeout);reject(Error('Fixture exited '+code+': '+log))});
 });
 const base=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const response=await fetch(base+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(response.status,200);return response.json()};
 const prepared=await control({op:'native-history',longMessage:true});
 const native=prepared.state.sessions.find(row=>row.nativeIdentity==='paging-fixture');assert.ok(native);
 const inspect=()=>control({op:'inspect-native-history',id:native.id});
 const before=await inspect();
 assert.equal(before.session.messages.length,100);assert.equal(before.session.sharedHistoryOffset,205);
 const longText='Saved message 150\n\n'+'Long retained paragraph. '.repeat(1500)+'LONG-HISTORY-END';
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1400,height:1000},extraHTTPHeaders:headers});
 const errors=[],pages=[],textReads=[],textResponses=[];
 const earlier=url=>url.pathname==='/api/conversation/detail'&&url.searchParams.get('sessionId')===native.id&&url.searchParams.get('part')==='messages'&&url.searchParams.has('before')&&!url.searchParams.has('field');
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{
  const url=new URL(request.url());
  if(earlier(url))pages.push(Object.fromEntries(url.searchParams));
  if(url.pathname==='/api/conversation/detail'&&url.searchParams.get('field')==='text')textReads.push({args:Object.fromEntries(url.searchParams),client:request.headers()['x-amplifier-client']});
 });
 page.on('response',response=>{
  const url=new URL(response.url());
  if(url.pathname==='/api/conversation/detail'&&url.searchParams.get('field')==='text')textResponses.push(observed(response.json().then(value=>({status:response.status(),value}))));
 });
 await page.addInitScript(()=>{
  const original=window.fetch;window.fixtureHistoryReads=[];window.fixtureHistoryMotion=[];
  const record=(kind,pane,extra={})=>{
   window.fixtureHistoryMotion.push({kind,at:performance.now(),top:pane.scrollTop,
    busy:pane.getAttribute('aria-busy'),height:pane.scrollHeight,clientHeight:pane.clientHeight,...extra});
   if(window.fixtureHistoryMotion.length>48)window.fixtureHistoryMotion.shift();
  };
  for(const type of ['wheel','scroll','focusin','click'])document.addEventListener(type,event=>{
   const pane=event.target instanceof Element?event.target.closest('.a-messages'):null;
   if(!pane)return;
   record(type,pane,{trusted:event.isTrusted,...type==='wheel'?{deltaY:event.deltaY,deltaX:event.deltaX}:{}});
   if(type==='wheel')requestAnimationFrame(()=>record('wheel-frame',pane));
  },{capture:true,passive:true});
  window.fetch=(url,options)=>{if(String(url).startsWith('/api/conversation/detail?')&&String(url).includes('before=')){
   window.fixtureHistoryReads.push({url:String(url),signal:options?.signal});
   const pane=document.querySelector('.a-messages');if(pane)record('older-request',pane);
  }return original(url,options)};
 });
 await page.goto(base);await showFixtureConversation(page);
 const view=()=>page.evaluate(id=>{const s=window.amplifier.getState();return {client:s.client.id,selected:s.selectedSessionId,row:s.sessions.find(row=>row.id===id)}},native.id);
 const numbers=rows=>rows.map(row=>Number(/^Saved message (\d+)\n/.exec(row.text)?.[1]));
 const range=(start,end)=>Array.from({length:end-start},(_,i)=>start+i);
 const renderedIds=()=>page.locator('[data-message-id]').evaluateAll(rows=>rows.map(row=>row.dataset.messageId));
 const messages=page.locator('[data-message-id]'),pane=page.locator('.a-messages');
 const earlierButton=()=>page.getByRole('button',{name:'Load earlier conversation',exact:true});
 const latest=()=>page.getByRole('button',{name:'Jump to latest messages',exact:true});
 await expect(messages).toHaveCount(20);
 await expect(pane).toHaveAttribute('aria-busy','false');
 const initial=await view();
 assert.equal(initial.selected,native.id);assert.deepEqual(numbers(initial.row.messages),range(285,305));
 assert.ok(initial.row.messages.every(row=>row.displayOnly===true));
 assert.equal(initial.row.messageWindow.mode,'cursor');assert.equal(initial.row.messageWindow.total,null);assert.equal(initial.row.messageWindow.offset,null);
 assert.ok(initial.row.messageWindow.before);assert.equal(initial.row.messageWindow.hasEarlier,true);
 let ids=initial.row.messages.map(row=>row.id),cursor=initial.row.messageWindow.before;
 assert.deepEqual(await renderedIds(),ids);
 const acceptPage=async (response,start,end)=>{
  assert.equal(response.status(),200);
  const url=new URL(response.url()),value=await response.json();
  assert.equal(url.searchParams.get('sessionId'),native.id);assert.equal(url.searchParams.get('before'),cursor);
  assert.equal(value.mode,'cursor');assert.equal(value.offset,null);assert.equal(value.total,null);
  assert.deepEqual(value.revision,initial.row.messageWindow.revision);assert.deepEqual(value.unavailableRanges,[]);
  assert.deepEqual(numbers(value.items),range(start,end));assert.ok(value.items.every(row=>row.displayOnly===true));
  assert.notEqual(value.before,cursor);cursor=value.before;
  ids=[...value.items.map(row=>row.id),...ids];assert.equal(new Set(ids).size,ids.length);
  await expect(messages).toHaveCount(ids.length);assert.deepEqual(await renderedIds(),ids);
  return value;
 };
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this draft');
 await page.waitForFunction(()=>{const p=document.querySelector('.a-messages');return p.scrollHeight-p.scrollTop-p.clientHeight<3});
 // This explicit click is not a scroll gesture. Startup is20; each requested
 // older page independently keeps its existing up-to100-message contract.
 stage='manual-first-page';
 const firstResponse=observed(page.waitForResponse(response=>earlier(new URL(response.url()))));
 await earlierButton().evaluate(button=>button.click());
 await acceptPage(await firstResponse,185,285);assert.equal(pages.length,1);
 await expect(pane).toHaveAttribute('aria-busy','false');
 await pane.evaluate(element=>element.scrollTop=60);
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 assert.equal(pages.length,1,'Programmatic restoration must not authorize another read');
 const pageGate=new Promise(resolve=>releasePage=resolve);
 const heldCursor=cursor;
 const holdPage=async route=>{const url=new URL(route.request().url());if(earlier(url)&&url.searchParams.get('before')===heldCursor)await pageGate;await route.continue()};
 await page.route('**/api/conversation/detail?*',holdPage);
 stage='single-wheel-held-page';
 const secondResponse=observed(page.waitForResponse(response=>earlier(new URL(response.url()))));
 await pane.hover();await page.mouse.wheel(0,-60);
 await expect.poll(()=>pages.length).toBe(2);
 await expect(page.getByRole('button',{name:'Loading earlier conversation…',exact:true})).toBeDisabled();
 await expect(pane).toHaveAttribute('aria-busy','true');assert.deepEqual(await renderedIds(),ids);
 const anchor=await pane.evaluate(element=>{const first=element.querySelector('[data-message-id]');return {id:first.dataset.messageId,y:first.getBoundingClientRect().y}});
 releasePage();
 const second=await acceptPage(await secondResponse,85,185);
 await expect(pane).toHaveAttribute('aria-busy','false');
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 const after=await page.locator(`[data-message-id="${anchor.id}"]`).boundingBox();
 assert.ok(Math.abs(after.y-anchor.y)<3,`Scroll anchor moved ${after.y-anchor.y}px`);
 await page.unroute('**/api/conversation/detail?*',holdPage);assert.equal(pages.length,2);
 stage='final-native-page';
 const finalResponse=observed(page.waitForResponse(response=>earlier(new URL(response.url()))));
 await earlierButton().evaluate(button=>button.click());
 const finalPage=await acceptPage(await finalResponse,0,85);
 assert.equal(finalPage.hasEarlier,false);assert.equal(finalPage.before,null);assert.equal(pages.length,3);
 await expect(earlierButton()).toHaveCount(0);await expect(pane).toHaveAttribute('aria-busy','false');
 assert.deepEqual((await inspect()).session.messages,before.session.messages);
 assert.deepEqual((await inspect()).files,before.files);
 // An older user message's complete body requires its exact issued descriptor,
 // not a speculative full read or a larger canonical resident history.
 const longRow=second.items.find(row=>row.text.startsWith('Saved message 150\n'));
 assert.equal(longRow.text.length,4096);assert.equal(longRow.textDetail.length,longText.length);
 assert.equal(longRow.textDetail.sessionId,native.id);assert.equal(textReads.length,0);
 const longElement=page.locator(`[data-message-id="${longRow.id}"]`);
 stage='explicit-long-text';
 await longElement.getByRole('button',{name:'Show full text',exact:true}).click();
 await expect(longElement).toContainText('LONG-HISTORY-END');
 const chunks=await Promise.all(textResponses);assert.equal(chunks.length,textReads.length);
 assert.ok(textReads.length>1);let offset=0;
 for(const [index,{args,client}] of textReads.entries()){
  for(const key of ['sessionId','part','id','field','digest','windowKey'])assert.equal(args[key],String(longRow.textDetail[key]));
  assert.equal(client,initial.client);assert.equal(Number(args.offset),offset);
  const chunk=chunks[index];assert.equal(chunk.status,200);assert.equal(chunk.value.total,longText.length);
  const length=Array.from(chunk.value.value).length;assert.ok(length>0&&length<=16000);
  offset+=length;assert.equal(chunk.value.nextOffset,index===chunks.length-1?null:offset);
 }
 assert.equal(offset,longText.length);assert.equal(pages.length,3);
 assert.equal(chunks.map(row=>row.value.value).join(''),longText);assert.equal(chunks.at(-1).value.nextOffset,null);
 const paged=await inspect();assert.deepEqual(paged,before,'All paging and long-text reads are observational');
 // A frozen reader keeps all305 messages while accepted live status changes.
 const frozenIds=[...ids];
 stage='frozen-working-idle';
 await control({op:'live-response',id:native.id});
 await expect(page.getByText('Still writing...',{exact:true})).toBeVisible();
 await expect.poll(async()=>(await view()).row.status).toBe('working');
 await expect(page.getByText('A new live response.',{exact:true})).toHaveCount(0);
 assert.deepEqual(await renderedIds(),frozenIds);
 const working=await inspect();assert.equal(working.session.sharedHistoryOffset,205);
 assert.deepEqual(working.session.messages.slice(0,100),before.session.messages);
 assert.equal(working.session.messages.at(-1).text,'A new live response.');assert.equal(working.session.streaming,'Still writing...');
 await control({op:'live-history-idle',id:native.id});
 await expect.poll(async()=>(await view()).row.status).toBe('idle');
 await expect.poll(async()=>(await view()).row.streaming||'').toBe('');
 assert.deepEqual(await renderedIds(),frozenIds);
 await pane.evaluate(el=>el.scrollTop=el.scrollHeight);
 await expect(latest()).toBeInViewport();await latest().click();
 await expect(page.getByText('A new live response.',{exact:true})).toBeVisible();
 await expect(messages).toHaveCount(20);
 assert.deepEqual(await renderedIds(),(await view()).row.messages.map(row=>row.id));
 // Hold a real older-page response after the server produced it, then select
 // another chat. A superseded reader must not attach that late response.
 const lateGate=new Promise(resolve=>releaseLate=resolve);let capturedLate,lateSettled=false;
 stage='superseded-real-page';
 const holdLate=async route=>{
  if(!earlier(new URL(route.request().url())))return route.continue();
  const response=await route.fetch();assert.equal(response.status(),200);capturedLate=await response.json();assert.ok(capturedLate.items.length);
  await lateGate;
  try{await route.fulfill({response})}catch(error){if(!await page.evaluate(()=>window.fixtureHistoryReads.every(row=>row.signal?.aborted)))throw error}
  finally{lateSettled=true}
 };
 await page.route('**/api/conversation/detail?*',holdLate);
 await page.evaluate(()=>window.fixtureHistoryReads=[]);
 await earlierButton().evaluate(button=>button.click());await expect.poll(()=>!!capturedLate).toBe(true);
 await expect(pane).toHaveAttribute('aria-busy','true');
 await page.evaluate(async id=>{const r=await window.amplifier.dispatch('session.select',{id});if(!r.accepted)throw Error('Fixture selection refused')},prepared.beta);
 await expect.poll(()=>page.evaluate(()=>window.fixtureHistoryReads.length>0&&window.fixtureHistoryReads.every(row=>row.signal?.aborted))).toBe(true);
 releaseLate();await expect.poll(()=>lateSettled).toBe(true);await page.unroute('**/api/conversation/detail?*',holdLate);
 assert.equal((await view()).selected,prepared.beta);await expect(messages).toHaveCount(1);
 await page.evaluate(async id=>{const r=await window.amplifier.dispatch('session.select',{id});if(!r.accepted)throw Error('Fixture selection refused')},native.id);
 await expect(messages).toHaveCount(20);await expect(pane).toHaveAttribute('aria-busy','false');
 assert.deepEqual(await renderedIds(),(await view()).row.messages.map(row=>row.id));
 assert.equal(pages.length,4,'Only the three accepted older pages and one superseded read were requested');
 const current=await inspect();
 stage='final-canonical-preservation';
 assert.equal(current.session.sharedHistoryOffset,205);assert.equal(current.session.status,'idle');
 // Idle settles an unfinished synthetic stream into a retained partial and
 // removes the streaming key; the read-only fixture projects absence as null.
 assert.equal(current.session.streaming,null);
 assert.equal(current.session.messages.length,102);
 assert.equal(current.session.messages.at(-2).text,'A new live response.');
 assert.equal(current.session.messages.at(-1).text,'[Interrupted response]\n\nStill writing...');
 assert.equal(current.session.messages.at(-1).source,'interrupted-stream');
 assert.equal(current.session.messages.at(-1).partial,true);
 assert.deepEqual(current.session.messages.slice(0,100),before.session.messages);assert.deepEqual(current.files,before.files);
 assert.equal(current.runtimeSends,prepared.runtimeSends);assert.equal(current.runtimeStarts,prepared.runtimeStarts);
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep this draft');assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,initialMessages:20,completeNativeMessages:305,earlierPages:3,explicitLongText:true,staleResponseRejected:true,stableReaderWhileWorkingAndIdle:true,explicitLatest:true,scrollAnchorDelta:after.y-anchor.y,
  historyMotion:await page.evaluate(()=>window.fixtureHistoryMotion)}));
}catch(error){
 primary=error;
 console.error(JSON.stringify({liveHistoryFailure:{stage,name:error.name,message:error.message,stack:error.stack}}));
 if(page&&!page.isClosed()){
  let timer;
  try{
   const observation=await Promise.race([page.evaluate(()=>{
    const pane=document.querySelector('.a-messages'),state=window.amplifier?.getState();
    return {rendered:document.querySelectorAll('[data-message-id]').length,busy:pane?.getAttribute('aria-busy'),
     top:pane?.scrollTop,height:pane?.scrollHeight,clientHeight:pane?.clientHeight,
     status:state?.sessions?.find(row=>row.id===state.selectedSessionId)?.status,
     reads:window.fixtureHistoryReads?.map(row=>({aborted:row.signal?.aborted})),
     historyMotion:window.fixtureHistoryMotion};
   }),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Failure observation timed out')),2000)})]);
   console.error(JSON.stringify({liveHistoryObservation:observation}));
  }catch(diagnostic){console.error(JSON.stringify({diagnosticFailure:String(diagnostic)}))}finally{clearTimeout(timer)}
 }
}finally{
 releasePage?.();releaseLate?.();
 const drain=async(label,operation)=>{
  let timer;
  try{return await Promise.race([operation,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(label+' cleanup timed out')),5000)})])}
  catch(error){cleanupErrors.push(String(error))}finally{clearTimeout(timer)}
 };
 // Bound cleanup waits independently of journey assertions so a stalled route
 // or browser close cannot prevent signalling this test's fixture child.
 if(page&&!page.isClosed())await drain('Routes',page.unrouteAll({behavior:'wait'}));
 await drain('Browser',browser?.close());
 if(fixture.exitCode===null&&fixture.signalCode===null){
  let timer;
  try{
   const exited=once(fixture,'exit');fixture.kill('SIGTERM');
   await Promise.race([exited,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Fixture teardown timeout')),10000)})]);
  }catch(error){cleanupErrors.push(String(error))}finally{clearTimeout(timer)}
 }
 const settlements=await drain('Response waiters',Promise.allSettled(waits))||[];
 const rejected=settlements.filter(row=>row.status==='rejected').map(row=>String(row.reason));
 if(rejected.length||cleanupErrors.length)console.error(JSON.stringify({responseWaitFailures:rejected,cleanupErrors}));
 if(!primary&&(rejected.length||cleanupErrors.length))primary=Error('Response waiter or fixture cleanup failed');
 if(primary){console.error(JSON.stringify({fixtureStderr:log}));throw primary}
}
