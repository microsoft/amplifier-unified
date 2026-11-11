import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/message_delivery_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
const fixtureExit=new Promise(resolve=>fixture.once('exit',resolve));
let browser;
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}});fixture.once('exit',code=>reject(Error('Fixture exit '+code)))});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 const isActionResponse=(response,action)=>{const request=response.request();return new URL(request.url()).pathname==='/api/actions'&&request.method()==='POST'&&request.postDataJSON()?.action===action};
 // Observe the real service's automatic passive check; do not suppress it or
 // replace its response merely to preserve the former manual-check sequence.
 const automaticCheck=page.waitForResponse(response=>isActionResponse(response,'conversation.delivery'));
 await page.goto(ready.url);await showFixtureConversation(page);
 const original=page.locator('.a-user').filter({hasText:'Recover this synthetic message with its attachment.'});
 await expect(original).toHaveCount(1);
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Preserve my next unsent thought');
 assert.equal(await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]').length),0,'Recovery works without a local outbox');
 const inspect=async()=>await (await page.request.get(ready.url+'/fixture')).json();
 const checked=await automaticCheck,checkRequest=checked.request().postDataJSON(),checkReceipt=await checked.json();
 const sessionId=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 assert.equal(checkRequest.args.sessionId,sessionId);assert.equal(checkRequest.args.inputId,'original-fixture-input');
 assert.equal(checkReceipt.accepted,true);assert.equal(checkReceipt.result.sessionId,sessionId);assert.equal(checkReceipt.result.inputId,'original-fixture-input');assert.equal(checkReceipt.result.delivery,'unknown');
 await expect(original.getByText(/Delivery is still uncertain/)).toBeVisible();
 const passive=await inspect();assert.ok(passive.checked.includes('original-fixture-input'));assert.deepEqual(passive.sent,[]);assert.deepEqual(passive.retried,[]);
 await original.getByRole('button',{name:'Send again',exact:true}).click();
 await expect(original.getByText(/Sending again could repeat that work/)).toBeVisible();
 await original.getByRole('button',{name:'Cancel',exact:true}).click();
 assert.deepEqual((await inspect()).sent,[]);assert.deepEqual((await inspect()).retried,[]);
 await page.setViewportSize({width:390,height:844});
 await original.getByRole('button',{name:'Send again',exact:true}).click();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 const confirmation=await original.locator('.a-delivery-confirm').boundingBox();
 const resendButton=await original.getByRole('button',{name:'Send this message again',exact:true}).boundingBox();
 assert.ok(confirmation.width>=200,'Phone confirmation remains readable');
 assert.ok(resendButton.width>=150&&resendButton.height<80,'Resend label must not collapse into a vertical column');
 await page.screenshot({path:'/tmp/unified26-delivery-confirmation-mobile.png'});
 assert.deepEqual((await inspect()).sent,[]);assert.deepEqual((await inspect()).retried,[],'Opening confirmation alone never retries');
 const retryResponse=page.waitForResponse(response=>isActionResponse(response,'conversation.retry'));
 await original.getByRole('button',{name:'Send this message again',exact:true}).click();
 const retryRequest=(await retryResponse).request().postDataJSON();
 assert.equal(retryRequest.args.sessionId,sessionId);assert.equal(retryRequest.args.inputId,'original-fixture-input');assert.equal(retryRequest.args.confirmUncertain,true);
 await expect(page.getByText('Recovery succeeded: one synthetic submission received.',{exact:true})).toBeVisible();
 await expect(composer).toHaveDraft('Preserve my next unsent thought');
 await expect(original).toHaveCount(1);
 const receipt=await inspect();assert.equal(receipt.sent.length,1);assert.equal(receipt.retried.length,1);assert.equal(receipt.sent[0].inputId,'original-fixture-input');
 const state=await page.evaluate(()=>window.amplifier.getState());
 const saved=state.sessions.find(s=>s.id===state.selectedSessionId).messages.find(m=>m.inputId==='original-fixture-input');
 assert.equal(saved.delivery.status,'accepted');assert.equal(saved.attachments[0].name,'reference.txt');
 await page.reload();await expect(composer).toHaveDraft('Preserve my next unsent thought');
 const reloaded=await inspect();assert.equal(reloaded.sent.length,1);assert.equal(reloaded.retried.length,1);assert.deepEqual(errors,[]);
 console.log('Delivery recovery browser passed: automatic passive check with exact-bound service receipt, no local outbox, cancel and confirmation without admission, explicit single confirmed retry, original identity/attachment, preserved draft, reload and mobile bounds.');
 await context.close();

 // These cases use the same AppService producer, not route.fulfill snapshots.
 // Barriers hold real completed action responses or the synthetic native ACK.
 const api=async(path,body)=>{
  const response=await fetch(ready.url+path,{headers:{Authorization:'Bearer fixture-browser-control-token','Content-Type':'application/json'},signal:AbortSignal.timeout(15000),...(body===undefined?{}:{method:'POST',body:JSON.stringify(body)})});
  assert.equal(response.status,200,path);return response.json();
 };
 const observations=()=>api('/fixture');
 const arm=(key,action,sessionId,id)=>api('/fixture/barrier',{key,action,sessionId,...(id?{id}:{})});
 const release=(key,patch={})=>api('/fixture/release',{key,...patch});
 const arrived=async key=>{
  await expect.poll(async()=>(await observations()).barriers[key]?.arrived,{message:'Real fixture barrier '+key,timeout:15000}).toBe(true);
  return (await observations()).barriers[key];
 };
 const outbox=tab=>tab.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]'));
 const localEntry=async(tab,id)=>(await outbox(tab)).find(row=>row.commandId===id);
 const select=async(tab,id)=>{
  const result=await tab.evaluate(id=>window.amplifier.dispatch('session.select',{id}),id);
  assert.equal(result.accepted,true,'Actual session.select acceptance');
  await tab.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id,id);
  return result;
 };
 const omittedOwner=async(tab,{a,b})=>{
  await tab.waitForFunction(({a,b})=>{
   const frame=window.amplifier.getState();
   return frame.selectedSessionId===b&&frame.sessions.some(row=>row.id===b)&&!frame.sessions.some(row=>row.id===a);
  },{a,b});
  const clientId=await tab.evaluate(()=>window.amplifier.getState().client.id);
  const response=await tab.request.get(ready.url+'/api/state',{headers:{'X-Amplifier-Client':clientId}});
  assert.equal(response.status(),200);
  const frame=await response.json();
  assert.equal(frame.selectedSessionId,b);
  assert.ok(frame.sessions.some(row=>row.id===b));
  assert.ok(!frame.sessions.some(row=>row.id===a),'Actual selected-only AppService frame omits A; do not repair tests by expanding sessions');
 };
 const openCase=async(label,outcome='accept')=>{
  const ids=await api('/fixture/case',{label,outcome});
  const owner=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  const tab=await owner.newPage();tab.on('pageerror',error=>errors.push(error.message));
  await tab.goto(ready.url);await showFixtureConversation(tab,{navPinned:false});
  await select(tab,ids.a);
  await tab.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{navPinned:false,navExpanded:false}}));
  return {...ids,owner,tab,label};
 };
 const submit=async(test,text)=>{
  await test.tab.getByRole('textbox',{name:'Message Amplifier'}).fill(text);
  await test.tab.locator('[data-part="composer-primary-action"][data-action="conversation.send"]').click();
  await test.tab.waitForFunction(text=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]').some(row=>row.text===text),text);
  return (await outbox(test.tab)).find(row=>row.text===text);
 };
 const sendFacts=async test=>{
  const data=await observations(),ids=new Set([test.a,test.b]);
  return {
   posts:data.posts.filter(row=>row.action==='conversation.send'&&ids.has(row.args.sessionId)),
   sends:data.sent.filter(row=>ids.has(row.sessionId)),
   retries:data.posts.filter(row=>row.action==='conversation.retry'&&ids.has(row.args.sessionId)),
  };
 };
 const oneSendToA=async(test,entry)=>{
  const facts=await sendFacts(test);
  assert.equal(facts.posts.length,1,'Exactly one send POST, no automatic re-POST');
  assert.equal(facts.posts[0].args.sessionId,test.a);assert.equal(facts.posts[0].id,entry.commandId);
  assert.equal(facts.posts[0].args.text,entry.text);
  assert.deepEqual(facts.sends.map(row=>[row.sessionId,row.inputId,row.text]),[[test.a,entry.commandId,entry.text]]);
  assert.equal(facts.sends[0].savedBeforeInput,true,'Canonical message and command were saved before native input');
  assert.deepEqual(facts.retries,[],'Passive checks and navigation never retry');
 };
 const acceptedWithoutWarning=async(test,entry)=>{
  await expect.poll(async()=>(await outbox(test.tab)).length).toBe(0);
  assert.equal(await test.tab.evaluate(()=>window.amplifier.getState().selectedSessionId),test.b,'Late send ACK does not undo B navigation');
  await select(test.tab,test.a);
  const row=test.tab.locator('.a-user').filter({hasText:entry.text});
  await expect(row).toHaveCount(1);
  await expect(row.locator('.a-message-delivery, .a-delivery-warning')).toHaveCount(0);
  await expect(test.tab.getByText(/The (delivery target|source) changed|original message belongs to a different source/)).toHaveCount(0);
  await oneSendToA(test,entry);
 };
 const occupySendQueue=async test=>{
  const key=test.label+'-edit-response';
  await arm(key,'message.edit',test.a,key);
  // A legitimate rejected edit has no continuation/native work, but its real
  // response occupies the same sendQueue as conversation.send. Catch it so the
  // expected edit refusal is not an unhandled rejection/page error.
  await test.tab.evaluate(({a,key})=>{
   window.fixtureEdit=window.amplifier.dispatch('message.edit',{
    sessionId:a,messageId:'missing-message-for-queue-barrier',text:'Rejected queue barrier',mode:'current',
   },{id:key,feedback:false}).then(result=>({accepted:result.accepted}),error=>({rejected:true,message:error.message}));
  },{a:test.a,key});
  const barrier=await arrived(key);
  assert.ok(barrier.receipt.status>=400&&barrier.receipt.status<500,'The real edit was refused before native work');
  return key;
 };
 const publishedInput=async(test,entry)=>{
  await test.tab.waitForFunction(({a,inputId})=>{
   const frame=window.amplifier.getState(),row=frame.sessions.find(row=>row.id===a);
   return frame.selectedSessionId===a&&row?.messages?.some(message=>message.inputId===inputId&&message.delivery?.status==='sending');
  },{a:test.a,inputId:entry.commandId});
 };

 {
  const test=await openCase('pre-post-navigation');
  try{
   const edit=await occupySendQueue(test),entry=await submit(test,'Pending A must survive the bounded B frame.');
   assert.deepEqual((await sendFacts(test)).posts,[],'Send is captured but still queued before POST');
   await select(test.tab,test.b);await omittedOwner(test.tab,test);
   await release(edit);
   assert.equal((await test.tab.evaluate(()=>window.fixtureEdit)).rejected,true);
   await acceptedWithoutWarning(test,entry);
  }finally{await test.owner.close()}
 }

 // Repeat only the changed boundary: native input was written/published before
 // B navigation, but its real accepted action receipt has not reached the tab.
 for(const changedSource of [false,true]){
  const test=await openCase(changedSource?'post-post-source-contradiction':'post-post-navigation');
  try{
   const native=test.label+'-native',ack=test.label+'-ack';
   await arm(native,'runtime.send',test.a);await arm(ack,'conversation.send',test.a);
   const entry=await submit(test,'Original A post: '+test.label);
   await arrived(native);await publishedInput(test,entry);await oneSendToA(test,entry);
   await select(test.tab,test.b);await omittedOwner(test.tab,test);
   if(changedSource){
    await api('/fixture/source',{kind:'native',sessionId:test.a});
    await select(test.tab,test.a);
    await test.tab.waitForFunction(a=>{
     const row=window.amplifier.getState().sessions.find(row=>row.id===a);
     return !!row?.nativeIdentity&&row.nativeIdentity!==a;
    },test.a);
   }
   await release(native);
   const receipt=await arrived(ack);
   assert.equal(receipt.receipt.body.accepted,true);assert.equal(receipt.receipt.body.delivery,'accepted');
   assert.equal(receipt.receipt.body.inputId,entry.commandId);assert.equal(receipt.receipt.body.sessionId,test.a);
   assert.equal((await localEntry(test.tab,entry.commandId)).status,'sending','No ACK and no in-scope positive A frame yet');
   await release(ack);
   if(changedSource){
    await expect.poll(async()=>(await localEntry(test.tab,entry.commandId))?.status).toBe('unknown');
    assert.equal((await localEntry(test.tab,entry.commandId)).scope,entry.scope,'A real native contradiction cannot rebind saved input');
    await oneSendToA(test,entry);
   }else await acceptedWithoutWarning(test,entry);
  }finally{await test.owner.close()}
 }

 // A host-generation rollover models the restart fence without restarting this
 // fixture process. Account/native/workspace changes are real producer/auth
 // changes, not injected browser frames. Every refusal is known zero-POST.
 for(const kind of ['host','account','native','workspace']){
  const test=await openCase('pre-post-'+kind);
  try{
   const edit=await occupySendQueue(test),entry=await submit(test,'Retain zero-POST input across '+kind);
   const before=await test.tab.evaluate(()=>window.amplifier.getState());
   await api('/fixture/source',{kind,sessionId:test.a});
   if(['host','account'].includes(kind))await test.tab.evaluate(()=>window.dispatchEvent(new Event('online')));
   await test.tab.waitForFunction(({kind,before,a})=>{
    const frame=window.amplifier.getState(),row=frame.sessions.find(row=>row.id===a),old=before.sessions.find(row=>row.id===a);
    return frame.selectedSessionId===a&&!!row&&(kind==='host'?frame.client.hostInstanceId!==before.client.hostInstanceId:
     kind==='account'?frame.client.historyScope!==before.client.historyScope:
     kind==='native'?row.nativeIdentity!==old.nativeIdentity:row.workspace!==old.workspace);
   },{kind,before,a:test.a});
   await release(edit);
   await expect.poll(async()=>(await localEntry(test.tab,entry.commandId))?.status).toBe('failed');
   const retained=await localEntry(test.tab,entry.commandId);
   assert.equal(retained.text,entry.text);assert.equal(retained.scope,entry.scope);
   assert.equal(retained.sessionId,test.a);assert.equal(retained.commandId,entry.commandId);
   assert.deepEqual(await sendFacts(test),{posts:[],sends:[],retries:[]},'Known preflight refusal is zero POST, not unknown delivery');
   await test.tab.reload();await test.tab.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
   assert.equal((await localEntry(test.tab,entry.commandId)).status,'failed','Reload preserves known non-delivery');
   assert.deepEqual(await sendFacts(test),{posts:[],sends:[],retries:[]},'Reload never replays a refused input');
   if(kind==='host'){
    const row=test.tab.locator('.a-user').filter({hasText:entry.text});
    await expect(row.locator('.a-message-delivery')).toContainText('Not sent');
    await expect(row.getByRole('button',{name:'Check delivery',exact:true})).toHaveCount(0);
    await test.tab.screenshot({path:'/artifacts/delivery-not-posted-desktop.png'});
    await test.tab.setViewportSize({width:390,height:844});
    await expect(row.getByRole('button',{name:'Retry',exact:true})).toBeVisible();
    await test.tab.screenshot({path:'/artifacts/delivery-not-posted-mobile.png'});
   }
  }finally{
   await test.owner.close();
   await api('/fixture/source',{kind:'restore',sessionId:test.a});
  }
 }

 // A's changed canonical binding is deliberately absent from the browser frame.
 // The captured witness must be checked at fresh admission, not inferred from B.
 for(const kind of ['native','workspace']){
  const test=await openCase('offscreen-'+kind);
  try{
   const edit=await occupySendQueue(test),entry=await submit(test,'Offscreen canonical '+kind+' must refuse this payload.');
   await select(test.tab,test.b);await omittedOwner(test.tab,test);
   await api('/fixture/source',{kind,sessionId:test.a});
   await omittedOwner(test.tab,test);
   await release(edit);
   await expect.poll(async()=>(await localEntry(test.tab,entry.commandId))?.status).toBe('failed');
   const facts=await sendFacts(test);
   assert.equal(facts.posts.length,1,'One attempted POST receives a definitive canonical refusal');
   assert.equal(facts.posts[0].args.sessionId,test.a);
   assert.ok(facts.posts[0].args.expectedSource.nativeIdentity,'The original canonical source travels with admission');
   assert.deepEqual(facts.sends,[],'No input to either A or B after an offscreen rebind');
   assert.deepEqual(facts.retries,[]);
   assert.equal((await localEntry(test.tab,entry.commandId)).scope,entry.scope);
  }finally{
   await test.owner.close();
   await api('/fixture/source',{kind:'restore',sessionId:test.a});
  }
 }

 {
  const test=await openCase('lost-ack','lost_ack');
  try{
   const native='lost-ack-native',ack='lost-ack-response';
   await arm(native,'runtime.send',test.a);await arm(ack,'conversation.send',test.a);
   const entry=await submit(test,'A written input with no native acknowledgement must remain unknown.');
   await arrived(native);await publishedInput(test,entry);await select(test.tab,test.b);await omittedOwner(test.tab,test);
   await release(native);
   const response=await arrived(ack);
   assert.ok(response.receipt.status>=400,'The real service reports missing native ACK, not synthetic success');
   await release(ack);
   await expect.poll(async()=>(await localEntry(test.tab,entry.commandId))?.status).toBe('unknown');
   await oneSendToA(test,entry);

   // A selected B receipt, even forged to claim A/input acceptance, is not
   // delivery evidence. Its actual producer transition still selects only B.
   await select(test.tab,test.a);
   await expect(test.tab.locator('.a-user').filter({hasText:entry.text}).getByRole('button',{name:'Check delivery',exact:true})).toBeEnabled();
   const selection='lost-ack-forged-selection';
   await arm(selection,'session.select',test.b);
   await test.tab.evaluate(b=>{
    window.fixtureSelection=window.amplifier.dispatch('session.select',{id:b}).then(result=>({accepted:result.accepted}));
   },test.b);
   await arrived(selection);await omittedOwner(test.tab,test);
   await release(selection,{forge:'selection',ownerSessionId:test.a,inputId:entry.commandId});
   assert.equal((await test.tab.evaluate(()=>window.fixtureSelection)).accepted,true);
   assert.equal((await localEntry(test.tab,entry.commandId)).status,'unknown','Selection acceptance is never send acceptance');

   for(const forge of ['wrong_input','wrong_session','wrong_source','wrong_host','generic_acceptance','refused']){
    await select(test.tab,test.a);
    const row=test.tab.locator('.a-user').filter({hasText:entry.text});
    await expect(row.getByRole('button',{name:'Check delivery',exact:true})).toBeEnabled();
    const key='lost-ack-'+forge;
    await arm(key,'conversation.delivery',test.a,key);
    await test.tab.evaluate(({a,inputId,key})=>{
     window.fixtureCheck=window.amplifier.dispatch('conversation.delivery',{sessionId:a,inputId},{id:key,feedback:false})
      .then(result=>({accepted:result.accepted}),error=>({rejected:true,message:error.message}));
    },{a:test.a,inputId:entry.commandId,key});
    const check=await arrived(key);
    assert.equal(check.receipt.body.accepted,true);assert.equal(check.receipt.body.result.delivery,'unknown');
    assert.equal(check.receipt.body.result.inputId,entry.commandId);
    if(forge==='wrong_input'){await select(test.tab,test.b);await omittedOwner(test.tab,test)}
    await release(key,{forge,wrongSessionId:test.b});
    assert.equal((await test.tab.evaluate(()=>window.fixtureCheck)).rejected,true,forge+' positive receipt must fail closed');
    assert.equal((await localEntry(test.tab,entry.commandId)).status,'unknown',forge+' cannot retire the exact saved input');
    await oneSendToA(test,entry);
   }
   await test.tab.reload();await test.tab.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
   assert.equal((await localEntry(test.tab,entry.commandId)).status,'unknown','Reload does not invent acceptance or non-delivery');
   await oneSendToA(test,entry);
   await select(test.tab,test.a);
   await expect(test.tab.locator('.a-user').filter({hasText:entry.text}).getByRole('button',{name:'Check delivery',exact:true})).toBeEnabled();
   await test.tab.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this unsent while checking exact evidence');
   await api('/fixture/native-evidence',{inputId:entry.commandId});
   const exact=await test.tab.evaluate(({a,inputId})=>window.amplifier.dispatch('conversation.delivery',{sessionId:a,inputId},{feedback:false}),{a:test.a,inputId:entry.commandId});
   assert.equal(exact.result.sessionId,test.a);assert.equal(exact.result.inputId,entry.commandId);
   assert.equal(exact.result.delivery,'accepted');assert.ok(exact.result.source.nativeIdentity);
   await expect.poll(async()=>(await outbox(test.tab)).length).toBe(0);
   await expect(test.tab.locator('.a-user').filter({hasText:entry.text}).locator('.a-message-delivery, .a-delivery-warning')).toHaveCount(0);
   await expect(test.tab.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep this unsent while checking exact evidence');
   await oneSendToA(test,entry);
  }finally{await test.owner.close()}
 }
 assert.deepEqual(errors,[]);
 console.log('Captured delivery browser passed: real selected-only A/B navigation before POST and before ACK; exactly one A input and zero B inputs; zero-POST host/account/native/workspace refusals remain failed across reload; post-POST native contradiction and missing ACK remain unknown; forged late/wrong/selection receipts fail closed; only exact native-backed passive evidence settles without resend.');
}finally{
 try{if(browser)await browser.close()}
 finally{
  fixture.kill('SIGTERM');
  const timeout=setTimeout(()=>fixture.kill('SIGKILL'),10000);
  try{await fixtureExit}finally{clearTimeout(timeout)}
 }
}
