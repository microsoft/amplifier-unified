import {showFixtureConversation} from './browser-chat-pages.mjs';
// Production UI + real transcript persistence, synthetic signaling; no real audio.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/voice_visual_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'Voice follow fixture'});
let browser,primaryFailure;
const readingChecks=[];
// Compare the original reading goal with a controller-free native scroller.
// A rolling prefix changes coordinates; it does not change the reader's goal.
function assertReading(goal,actual,label){
 const detail=label+': '+JSON.stringify({goal,actual});
 assert.equal(actual.sessionId,goal.sessionId,detail+' (session)');
 assert.equal(actual.id,goal.id,detail+' (message)');
 assert.deepEqual(actual.saved,{messageId:goal.id,offset:goal.offset},detail+' (saved desired goal)');
 assert.equal(actual.scrollTop,actual.attainable,detail+' (native attainable goal)');
 assert.equal(actual.offset,actual.contentTop-actual.attainable,detail+' (anchor offset)');
 if(actual.wanted===actual.attainable)assert.equal(actual.offset,goal.offset,detail+' (exact attainable offset)');
 if(actual.contentTop===goal.contentTop)assert.equal(actual.scrollTop,goal.scrollTop,detail+' (unchanged coordinates)');
}
try {
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'},reducedMotion:'reduce',colorScheme:'dark'});const page=await context.newPage();
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(()=>{
  window.Audio=class {setAttribute(){} pause(){} play(){return Promise.resolve()}};
  window.RTCPeerConnection=class extends EventTarget {
   constructor(){super();this.connectionState='new';this.iceGatheringState='complete'}
   createDataChannel(){const c=new EventTarget();c.readyState='open';c.close=()=>{};return c}
   addTrack(){}async createOffer(){return {type:'offer',sdp:'synthetic-offer'}}async setLocalDescription(value){this.localDescription=value}
   async setRemoteDescription(){this.connectionState='connected';this.dispatchEvent(new Event('connectionstatechange'))}close(){this.connectionState='closed'}
  };
  navigator.mediaDevices.getUserMedia=async()=>{const track=new EventTarget();Object.assign(track,{enabled:true,muted:false,readyState:'live',stop(){this.readyState='ended'}});return {getTracks:()=>[track],getAudioTracks:()=>[track]}};
 });
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const selected=()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 const workReady=async(target=page,sessionId=null)=>{
  await target.waitForFunction(expected=>{const pane=document.querySelector('.a-messages'),selected=window.amplifier?.getState()?.selectedSessionId;try{const receipt=JSON.parse(pane?.dataset.workProjection);return (!expected||selected===expected)&&pane.dataset.sessionId===selected&&receipt.sessionId===selected&&receipt.status==='ready'&&receipt.current===true&&!pane.querySelector('[data-part="work-projection-status"]')}catch{return false}},sessionId);
  await target.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 };
 const savedGoal=()=>pane.evaluate(el=>{
  const record=JSON.parse(sessionStorage.getItem('amplifier.chat-reading.v1')||'{}')[el.dataset.sessionId];
  return record?{messageId:record.messageId,offset:record.offset}:null;
 });
 const readingPosition=()=>pane.evaluate(el=>{
  const bounds=el.getBoundingClientRect(),node=[...el.querySelectorAll('[data-message-id]')].find(row=>!row.closest('[hidden]')&&row.getBoundingClientRect().bottom>bounds.top);
  if(!node||node.getBoundingClientRect().top>=bounds.bottom)throw Error('No visible reading anchor');
  if(window.amplifier.getState().selectedSessionId!==el.dataset.sessionId)throw Error('Reading pane does not belong to the selected chat');
  if(el.querySelectorAll(`[data-message-id="${CSS.escape(node.dataset.messageId)}"]`).length!==1)throw Error('Ambiguous reading anchor');
  const offset=node.getBoundingClientRect().top-bounds.top;
  return {sessionId:el.dataset.sessionId,id:node.dataset.messageId,offset,contentTop:el.scrollTop+offset,scrollTop:el.scrollTop};
 });
 const captureReading=async()=>{
  const goal=await readingPosition();
  await expect.poll(savedGoal).toEqual({messageId:goal.id,offset:goal.offset});
  return goal;
 };
 const checkReading=async(goal,label)=>{
  const actual=await pane.evaluate(async(el,goal)=>{
   const paint=()=>new Promise((resolve,reject)=>{
    let first,second;
    const timer=setTimeout(()=>{cancelAnimationFrame(first);cancelAnimationFrame(second);reject(Error('Native oracle animation frame timed out'))},2000);
    first=requestAnimationFrame(()=>{second=requestAnimationFrame(()=>{clearTimeout(timer);resolve()})});
   });
   let prototype=el;
   while(prototype&&!Object.getOwnPropertyDescriptor(prototype,'scrollTop'))prototype=Object.getPrototypeOf(prototype);
   const native=Object.getOwnPropertyDescriptor(prototype,'scrollTop');
   if(!native?.get||!native?.set)throw Error('Native scroll accessor unavailable');
   const measure=()=>{
    const bounds=el.getBoundingClientRect(),node=[...el.querySelectorAll('[data-message-id]')].find(row=>!row.closest('[hidden]')&&row.getBoundingClientRect().bottom>bounds.top);
    if(!node||node.getBoundingClientRect().top>=bounds.bottom)throw Error('No visible reading anchor');
    if(window.amplifier.getState().selectedSessionId!==el.dataset.sessionId)throw Error('Reading pane does not belong to the selected chat');
    if(el.querySelectorAll(`[data-message-id="${CSS.escape(node.dataset.messageId)}"]`).length!==1)throw Error('Ambiguous reading anchor');
    const offset=node.getBoundingClientRect().top-bounds.top;
    const record=JSON.parse(sessionStorage.getItem('amplifier.chat-reading.v1')||'{}')[el.dataset.sessionId];
    return {sessionId:el.dataset.sessionId,id:node.dataset.messageId,offset,contentTop:native.get.call(el)+offset,scrollTop:native.get.call(el),
     saved:record?{messageId:record.messageId,offset:record.offset}:null};
   };
   const dimensions=node=>{
    const box=node.getBoundingClientRect();
    return {clientWidth:node.clientWidth,clientHeight:node.clientHeight,scrollWidth:node.scrollWidth,scrollHeight:node.scrollHeight,
     offsetWidth:node.offsetWidth,offsetHeight:node.offsetHeight,rectWidth:box.width,rectHeight:box.height};
   };
   const before=measure(),shape=dimensions(el),style=getComputedStyle(el),bounds=el.getBoundingClientRect();
   const supportedCoordinates=()=>{
    for(let node=el;node;node=node.parentElement){
     const css=getComputedStyle(node);
     if(css.transform!=='none'||css.translate!=='none'||css.scale!=='none'||css.rotate!=='none'
       ||!['1','normal'].includes(css.zoom)||css.writingMode!=='horizontal-tb')throw Error('Unsupported native oracle coordinate scale');
    }
   };
   supportedCoordinates();
   if(style.direction!=='ltr'||style.scrollSnapType!=='none'||style.scrollBehavior!=='auto')throw Error('Unsupported native oracle scrolling mode');
   const doc=()=>({width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight,x:scrollX,y:scrollY});
   const documentBefore=doc(),focusBefore=document.activeElement;
   const host=document.createElement('div'),grid=document.createElement('div'),extent=document.createElement('div');
   host.setAttribute('aria-hidden','true');host.inert=true;
   host.style.cssText=`all:initial;position:fixed;left:${bounds.left}px;top:${bounds.top}px;width:${bounds.width}px;height:${bounds.height}px;visibility:hidden;pointer-events:none;overflow:hidden;contain:strict;`;
   const shadow=host.attachShadow({mode:'closed'}),rules=document.createElement('style');
   const bar=getComputedStyle(el,'::-webkit-scrollbar');
   rules.textContent=`#grid::-webkit-scrollbar{width:${bar.width};height:${bar.height};display:${bar.display}}`;
   grid.id='grid';
   // Separate controller-free native scroller: same measured viewport, origin,
   // borders/padding, gutter and content extent, with no application DOM or data.
   grid.style.cssText='all:initial;display:block;visibility:hidden;pointer-events:none;min-width:0;min-height:0;max-width:none;max-height:none;margin:0;scroll-behavior:auto;scroll-snap-type:none;overflow-anchor:none;';
   for(const key of ['width','height','box-sizing','padding-top','padding-bottom','padding-left','padding-right',
    'border-top-width','border-bottom-width','border-left-width','border-right-width','border-top-style','border-bottom-style','border-left-style','border-right-style',
    'overflow-x','overflow-y','scrollbar-gutter','scrollbar-width','direction','writing-mode','zoom','transform','transform-origin']){
    grid.style.setProperty(key,style.getPropertyValue(key));
   }
   const number=key=>parseFloat(style.getPropertyValue(key))||0;
   extent.style.cssText='all:initial;display:block;visibility:hidden;pointer-events:none;box-sizing:border-box;min-width:0;min-height:0;margin:0;padding:0;border:0;';
   extent.style.width=(shape.scrollWidth-number('padding-left')-number('padding-right'))+'px';
   extent.style.height=(shape.scrollHeight-number('padding-top')-number('padding-bottom'))+'px';
   grid.append(extent);shadow.append(rules,grid);document.body.append(host);
   try{
    for(const node of [host,grid,extent]){
     const css=getComputedStyle(node);
     if(css.visibility!=='hidden'||css.pointerEvents!=='none')throw Error('Native oracle reference is not hidden and noninteractive');
    }
    if(JSON.stringify(dimensions(grid))!==JSON.stringify(shape))throw Error('Native oracle scroller geometry does not match: '+JSON.stringify({pane:shape,grid:dimensions(grid)}));
    if(grid.getBoundingClientRect().top!==bounds.top)throw Error('Native oracle fractional origin does not match');
    const wanted=before.contentTop-goal.offset,max=shape.scrollHeight-shape.clientHeight;
    if(wanted<0||wanted>max)throw Error('Original reading goal lies outside this matched scroll range');
    native.set.call(grid,wanted);const attainable=native.get.call(grid);
    await paint();
    if(native.get.call(grid)!==attainable)throw Error('Native oracle did not settle');
    // Countercheck sample, independent of the tested pane and controller.
    const neighbor=wanted+1<=max?wanted+1:wanted-1;
    if(neighbor<0)throw Error('Native oracle has no in-range neighbor');
    native.set.call(grid,neighbor);const adjacent=native.get.call(grid);
    await paint();
    if(native.get.call(grid)!==adjacent||adjacent===attainable)throw Error('Native oracle countercheck is not discriminating');
    const after=measure();
    supportedCoordinates();
    if(JSON.stringify(after)!==JSON.stringify(before)||JSON.stringify(dimensions(el))!==JSON.stringify(shape)
      ||el.getBoundingClientRect().top!==bounds.top||el.getBoundingClientRect().left!==bounds.left
      ||JSON.stringify(doc())!==JSON.stringify(documentBefore)||document.activeElement!==focusBefore)throw Error('Reading geometry changed during native oracle sampling');
    return {...after,wanted,attainable,neighbor,adjacent,shape};
   }finally{host.remove()}
  },goal);
  assertReading(goal,actual,label);
  // Pure oracle controls: do not move the tested transcript or rewrite its goal.
  assert.throws(()=>assertReading(goal,{...actual,scrollTop:actual.adjacent,offset:actual.contentTop-actual.adjacent},'shifted reading'),/native attainable goal/);
  assert.throws(()=>assertReading(goal,{...actual,id:'different-anchor'},'wrong anchor'),/message/);
  assert.throws(()=>assertReading(goal,{...actual,sessionId:'different-chat'},'wrong chat'),/session/);
  assert.throws(()=>assertReading(goal,{...actual,saved:{messageId:goal.id,offset:goal.offset+1}},'changed desire'),/saved desired goal/);
  if(actual.offset!==goal.offset)assert.throws(()=>assertReading(goal,{...actual,saved:{messageId:goal.id,offset:actual.offset}},'rebased desire'),/saved desired goal/);
  readingChecks.push({label,goal,actual});
  console.log('VOICE-NATIVE-READING',JSON.stringify({label,goal,actual}));
 };
 await action('session.create',{title:'Call conversation'});const call=await selected();
 await action('session.create',{title:'Other conversation'});const other=await selected();
 let sequence=0;
 const append=async(sessionId,count=1)=>{const response=await page.request.post(url+'/fixture/transcript',{data:{sessionId,messages:Array.from({length:count},()=>({id:'voice-'+(++sequence),role:sequence%2?'user':'assistant',text:'Spoken item '+sequence+'\n\n'+('A useful explanation for this conversation. '.repeat(90))}))}});assert.equal(response.status(),200);if(await selected()===sessionId){await expect(page.locator('.a-messages')).toContainText('Spoken item '+sequence+'\n');await workReady()}};
 const pane=page.locator('.a-messages'),top=()=>pane.evaluate(el=>el.scrollTop),remaining=()=>pane.evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight);
 const bottom=()=>expect.poll(remaining).toBeLessThan(3);
 const scrollBack=async()=>{await workReady();const box=await pane.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.wheel(0,-800);await expect.poll(remaining).toBeGreaterThan(500);await page.waitForTimeout(150)};
 await append(other,12);await action('session.select',{id:call});await append(call,12);
 const observer=await page.context().newPage();await observer.goto(url);await showFixtureConversation(observer);await observer.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await observer.evaluate(id=>window.amplifier.dispatch('session.select',{id}),call);
 const observerPane=observer.locator('.a-messages');await expect(observerPane).toContainText('Spoken item '+sequence+'\n');
 await observerPane.evaluate(el=>{el.dispatchEvent(new WheelEvent('wheel',{deltaY:-800}));el.scrollTop=1000;el.dispatchEvent(new Event('scroll'))});
 await workReady(observer);const observerTop=await observerPane.evaluate(el=>el.scrollTop);
 // Starting this client's call intentionally follows speech even when it was
 // reading earlier history; another observing client's position is independent.
 await scrollBack();assert.ok(await remaining()>500);
 await action('call.start');await page.getByRole('button',{name:'End call',exact:true}).waitFor();await bottom();
 // A wheel gesture toward newer messages at the bottom cannot move the pane.
 // It must not silently pause follow-latest for the next spoken fragment.
 const bottomBox=await pane.boundingBox();await page.mouse.move(bottomBox.x+bottomBox.width/2,bottomBox.y+bottomBox.height/2);
 await page.mouse.wheel(0,500);await page.waitForTimeout(100);await bottom();
 await append(call,2);await bottom();
 // A retained user bubble can still receive a late fragment after an assistant
 // reply. Growth above that reply must not strand it below the viewport. New
 // alternating speech also follows without any backend delegation/tool call.
 const speak=async messages=>{const response=await page.request.post(url+'/fixture/transcript',{data:{sessionId:call,messages}});assert.equal(response.status(),200);await workReady()};
 await speak([{id:'growth-user',role:'user',text:'Sure.'},{id:'growth-answer',role:'assistant',text:'Here are the findings you asked for.'}]);
 await expect(pane).toContainText('Here are the findings you asked for.');await bottom();
 for(const size of [20,60,120]){
  await speak([{id:'growth-user',role:'user',text:'Sure. '+('Later transcript detail. '.repeat(size))}]);
  await expect(pane).toContainText('Later transcript detail. '.repeat(size).trim());await bottom();
 }
 await speak([{id:'later-user',role:'user',text:'Are you working on that?'},{id:'later-answer',role:'assistant',text:'The requested answer is ready to explain.'}]);
 await expect(pane).toContainText('The requested answer is ready to explain.');await bottom();
 assert.equal(await pane.getByText('The requested answer is ready to explain.',{exact:true}).evaluate(el=>{const p=el.closest('.a-messages').getBoundingClientRect(),r=el.getBoundingClientRect();return r.top>=p.top&&r.bottom<=p.bottom}),true,'Newest spoken reply stays inside the conversation viewport');
 // Reading/clicking a transcript is not a request to stop following speech.
 const readingBox=await pane.boundingBox();await page.mouse.click(readingBox.x+60,readingBox.y+readingBox.height-100);
 await append(call,2);await bottom();
 await expect(observerPane).toContainText('Spoken item '+sequence+'\n');await workReady(observer);await expect.poll(()=>observerPane.evaluate(el=>el.scrollTop)).toBe(observerTop);await observer.close();
 // Prefix eviction changes content coordinates. Retain the original identity
 // and desired offset; compare actual geometry with its native attainable goal.
 await scrollBack();const heldReading=await captureReading();await append(call,2);await page.waitForTimeout(250);
 await checkReading(heldReading,'Call scrollback retains the original reading goal');
 assert.ok(await remaining()>500,'Deliberate scrollback pauses call following');
 await action('session.select',{id:other});await scrollBack();const otherReading=await captureReading();await append(call,2);await page.waitForTimeout(250);
 // This unrelated chat has no prefix change: retain its exact original oracle.
 const exactReading=({sessionId,id,offset})=>({sessionId,id,offset});
 assert.deepEqual(exactReading(await readingPosition()),exactReading(otherReading),'Call updates preserve the other conversation reading anchor');
 await workReady(page,other);
 assert.deepEqual(exactReading(await readingPosition()),exactReading(otherReading),'The other conversation reading anchor remains after Work settles');
 assert.equal(await top(),otherReading.scrollTop,'Call updates must not scroll a different conversation');
 await action('session.select',{id:call});await workReady();await checkReading(heldReading,'Returning to the call preserves its original paused reading goal');
 await page.getByRole('button',{name:'Jump to latest messages'}).click();await bottom();await append(call,2);await bottom();
 await page.setViewportSize({width:390,height:844});await append(call,2);await bottom();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:'/tmp/unified-voice-follow-latest-mobile.png'});
 await action('call.end');await page.getByRole('button',{name:'End call',exact:true}).waitFor({state:'hidden'});
 await workReady();
 const endedReading=await captureReading();await append(call,2);await page.waitForTimeout(250);
 await checkReading(endedReading,'Ending the call restores normal reading behavior');
 const result=await(await page.request.get(url+'/fixture')).json();assert.deepEqual(result.sent,[]);assert.deepEqual(errors,[]);
 console.log('Voice view passed: live transcript growth, late earlier-bubble growth with newer replies visible, alternating speech without delegation, deliberate scrollback, other chat isolation, paused position on return, explicit latest resumes, mobile/reduced motion and hangup restores reading behavior. Synthetic signaling only; no physical audio acceptance.');
}catch(error){primaryFailure=error;console.error('VOICE-NATIVE-READING-FAILURE',JSON.stringify({error:String(error),readingChecks}));throw error}
finally{await finishFixture({label:'Voice follow fixture',close:[['browser',()=>browser?.close()]],stop:stopFixture,originalError:primaryFailure})}
