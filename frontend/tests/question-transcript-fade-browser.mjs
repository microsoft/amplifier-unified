// Focused production-host regression for question cards inside the shared
// transcript scroller. Requires the already-prepared exact-source browser-test
// executionContract; it builds/installs nothing and uses no live provider.
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdtempSync,readFileSync,realpathSync,writeFileSync} from 'node:fs';
import {isAbsolute,join,relative,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root=realpathSync(fileURLToPath(new URL('../../',import.meta.url)));
const within=(parent,path)=>{
 const rel=relative(parent,path);
 return rel===''||(!isAbsolute(rel)&&rel!=='..'&&!rel.startsWith('..'+sep));
};
const contractPath=process.env.AMPLIFIER_TEST_CONTRACT;
const pythonEnv=process.env.AMPLIFIER_TEST_PYTHON;
const artifactEnv=process.env.AMPLIFIER_TEST_ARTIFACTS;
assert.ok(contractPath,'An owned browser-test executionContract is required.');
assert.ok(pythonEnv,'Use the Python executable supplied by the executionContract.');
assert.ok(artifactEnv,'Use the artifact directory supplied by the executionContract.');
const contractBytes=readFileSync(realpathSync(contractPath));
const contract=JSON.parse(contractBytes);
assert.equal(contract.profileId,'browser-test');
assert.equal(realpathSync(contract.workspace),root,'Run the admitted source, not another checkout.');
assert.ok(isAbsolute(contract.python),'Use the contract’s explicit Python executable.');
assert.equal(realpathSync(pythonEnv),realpathSync(contract.python));
const artifactRoot=realpathSync(artifactEnv);
assert.equal(artifactRoot,realpathSync(contract.artifacts));
assert.ok(!within(root,artifactRoot),'Retained evidence must stay outside the repository.');

const artifacts=mkdtempSync(join(artifactRoot,'question-transcript-fade-'));
const hash=value=>createHash('sha256').update(value).digest('hex');
const sourceFiles=[
 'frontend/src/conversation-rail.css',
 'frontend/src/questions.jsx',
 'frontend/src/main.jsx',
 'frontend/src/chat-scroll.js',
 'frontend/tests/transcript-overflow.test.mjs',
 'frontend/tests/question-transcript-fade-browser.mjs',
 'frontend/tests/fixture-lifecycle.mjs',
 'tests/fixtures/questions_ui_server.py',
];
const sourceHashes=()=>Object.fromEntries(sourceFiles.map(file=>
 [file,hash(readFileSync(join(root,file)))]));
const buildBytes=readFileSync(join(root,'amplifier_web/static/build.json'));
const report={
 status:'running',
 runtime:'synthetic-question-fixture',
 liveProvider:false,
 executionContract:{version:contract.version,profileId:contract.profileId,source:contract.source},
 contractSha256:hash(contractBytes),
 sourceSha256:sourceHashes(),
 build:{
  path:'amplifier_web/static/build.json',
  diskSha256:hash(buildBytes),
  diskMetadata:JSON.parse(buildBytes),
  servedSha256:null,
  servedMetadata:null,
  stylesheetAssets:[],
  stylesheetAssetsDropped:0,
 },
 fixture:{started:false,ready:false,close:null,shutdown:null,stderrTail:'',stderrCharactersObserved:0,stderrTruncated:false},
 browser:{started:false,closed:false},
 capabilities:null,
 question:{id:null,required:false,answerSubmitted:false,answerRequests:[]},
 actions:[],
 focus:[],
 geometry:[],
 screenshots:[],
 browserErrors:[],
 httpErrors:[],
 externalRequests:[],
 instrumentationErrors:[],
 evidenceDropped:0,
 limits:{
  successScreenshots:4,
  failureScreenshots:1,
  stderrTailCharacters:24000,
  retainedEntriesPerArray:32,
  stylesheetAssets:12,
  actionRecords:80,
 },
 limitations:[
  'One synthetic chat message uses the fixture runtime; no provider is configured or contacted.',
  'The optional question is cancelled after inspection; no question answer is submitted.',
  'Saved screenshots require separate pixel review; this script records browser state and geometry.',
  'Chromium forced-colors is emulated and is not a native operating-system check.',
  'The report keeps source and served-build identities separate; matching source-to-build provenance remains the runner owner’s responsibility.',
 ],
};
const writeReport=()=>writeFileSync(join(artifacts,'report.json'),JSON.stringify(report,null,2)+'\n');
const boundedPush=(array,value,limit=report.limits.retainedEntriesPerArray)=>{
 if(array.length<limit)array.push(value);
 else report.evidenceDropped++;
};
let fixture,stopFixture,browser,page,failure,stderr='',stderrCharacters=0;
let failureScreenshotAttempted=false;

const inspect=async(pane,questionId)=>pane.evaluate((el,id)=>{
 const box=node=>{
  if(!node)return null;
  const r=node.getBoundingClientRect();
  return {x:r.x,y:r.y,width:r.width,height:r.height,top:r.top,bottom:r.bottom};
 };
 const section=el.querySelector('[data-part="questions"]');
 const question=id?el.querySelector(`[data-question-id="${CSS.escape(id)}"]`):null;
 const active=document.activeElement;
 const style=getComputedStyle(el);
 const paneBox=box(el),activeName=active?.getAttribute('aria-label')||active?.textContent?.trim()||'';
 return {
  geometry:{
   pane:paneBox,
   scrollTop:el.scrollTop,
   scrollHeight:el.scrollHeight,
   clientHeight:el.clientHeight,
   maxScrollTop:Math.max(0,el.scrollHeight-el.clientHeight),
   remaining:Math.max(0,el.scrollHeight-el.clientHeight-el.scrollTop),
   question:box(question),
  },
  overflow:{above:el.dataset.overflowAbove??null,below:el.dataset.overflowBelow??null},
  mask:{
   standard:style.maskImage,
   webkit:style.webkitMaskImage,
   composite:style.maskComposite,
   edge:style.getPropertyValue('--_a-transcript-edge-mask').slice(0,600),
  },
  focus:{
   activeTag:active?.tagName?.toLowerCase()||null,
   activeRole:active?.getAttribute('role')||null,
   activeName:activeName.slice(0,180),
   activeClass:String(active?.className||'').slice(0,180),
   activeFocusVisible:!!active?.matches?.(':focus-visible'),
   activeInsidePane:!!active&&el.contains(active),
   activeTextarea:active instanceof HTMLTextAreaElement,
   activeEditable:!!active?.matches?.('input:focus, textarea:focus, select:focus, [contenteditable]:focus, iframe:focus'),
   paneFocused:document.activeElement===el,
   paneFocusVisible:el.matches(':focus-visible'),
   paneFocusWithin:el.matches(':focus-within'),
   questionSectionFocused:!!section&&document.activeElement===section,
   questionSectionFocusVisible:!!section&&section.matches(':focus-visible'),
   forcedColors:matchMedia('(forced-colors: active)').matches,
  },
 };
},questionId);

const maskActive=state=>[state.mask.standard,state.mask.webkit].some(value=>Boolean(value)&&value!=='none');
const record=async(label,pane,questionId=null)=>{
 const state=await inspect(pane,questionId);
 boundedPush(report.focus,{label,...state.focus});
 boundedPush(report.geometry,{label,...state.geometry,overflow:state.overflow,mask:state.mask});
 return state;
};
const settled=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
const successShot=async name=>{
 const count=report.screenshots.filter(row=>row.kind==='success').length;
 assert.ok(count<report.limits.successScreenshots,'Success screenshot cap');
 const file=`${name}.png`,path=join(artifacts,file);
 const bytes=await page.screenshot({path,timeout:8000,animations:'disabled'});
 report.screenshots.push({kind:'success',file,sha256:hash(bytes),bytes:bytes.length});
};
const failureShot=async()=>{
 if(failureScreenshotAttempted||!page||page.isClosed())return;
 failureScreenshotAttempted=true;
 try{
  const path=join(artifacts,'failure.png');
  const bytes=await page.screenshot({path,timeout:2000,animations:'disabled'});
  report.screenshots.push({kind:'failure',file:'failure.png',sha256:hash(bytes),bytes:bytes.length});
 }catch(error){report.failureScreenshotError=String(error?.stack||error).slice(0,2000)}
};

try{
 const fixturePath=join(root,'tests/fixtures/questions_ui_server.py');
 fixture=spawn(contract.python,[fixturePath],{cwd:root,stdio:['ignore','pipe','pipe']});
 stopFixture=ownFixture(fixture,{label:'Question transcript fade fixture'});
 report.fixture.started=true;
 fixture.on('close',(code,signal)=>{report.fixture.close={observed:true,code,signal}});
 fixture.stderr.on('data',chunk=>{
  const text=chunk.toString('utf8');
  stderrCharacters+=text.length;
  stderr=(stderr+text).slice(-report.limits.stderrTailCharacters);
 });
 const ready=await new Promise((resolve,reject)=>{
  let pending='',settledStartup=false;
  const cleanup=()=>{
   clearTimeout(timer);fixture.stdout.off('data',onData);fixture.off('error',onError);fixture.off('exit',onExit);
  };
  const finish=(error,value)=>{
   if(settledStartup)return;
   settledStartup=true;cleanup();error?reject(error):resolve(value);
  };
  const onError=error=>finish(error);
  const onExit=(code,signal)=>finish(Error(`Question fixture exited before readiness (${code??signal})`));
  const onData=chunk=>{
   pending=(pending+chunk.toString('utf8')).slice(-24000);
   const lines=pending.split('\n');pending=lines.pop()||'';
   for(const line of lines){
    let row;try{row=JSON.parse(line)}catch{continue}
    if(row.url){finish(null,row);return}
   }
  };
  const timer=setTimeout(()=>finish(Error('Question fixture startup timeout')),20000);
  fixture.stdout.on('data',onData);fixture.once('error',onError);fixture.once('exit',onExit);
 });
 report.fixture.ready=true;
 const origin=new URL(ready.url).origin;
 browser=await chromium.launch({
  headless:true,
  ignoreDefaultArgs:['--hide-scrollbars'],
  args:[
   '--disable-features=OverlayScrollbar,FluentOverlayScrollbar',
   ...(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]),
   ...JSON.parse(process.env.AMPLIFIER_CHROMIUM_ARGS||'[]'),
  ],
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),
 });
 report.browser.started=true;
 const context=await browser.newContext({
  viewport:{width:1280,height:900},deviceScaleFactor:1,colorScheme:'light',reducedMotion:'no-preference',
  extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'},
 });
 page=await context.newPage();
 page.setDefaultTimeout(10000);page.setDefaultNavigationTimeout(20000);
 await page.route('**/*',route=>{
  const target=new URL(route.request().url());
  if(['http:','https:'].includes(target.protocol)&&target.origin!==origin){
   boundedPush(report.externalRequests,target.origin+target.pathname);
   return route.abort('blockedbyclient');
  }
  return route.continue();
 });
 page.on('pageerror',error=>boundedPush(report.browserErrors,String(error?.stack||error).slice(0,1500)));
 page.on('response',response=>{
  if(response.status()>=400)boundedPush(report.httpErrors,{path:new URL(response.url()).pathname,status:response.status()});
 });
 page.on('request',request=>{
  if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
  try{
   const raw=request.postDataJSON(),body=raw?.action==='shell.command'?raw.args:raw;
   const action=body?.action;
   if(action==='question.answer')boundedPush(report.question.answerRequests,{action,requestId:raw.id??null});
   boundedPush(report.actions,{action:action||'unknown',requestId:raw.id??body?.id??null},report.limits.actionRecords);
  }catch(error){boundedPush(report.instrumentationErrors,`Action observer: ${String(error).slice(0,1000)}`)}
 });
 await page.goto(ready.url);
 await showFixtureConversation(page);
 await expect(page.locator('#amp-one')).toBeVisible();
 const pane=page.locator('[data-part="messages"]');
 await expect(pane).toBeVisible();
 const support=await page.evaluate(()=>({
  hasFocusVisible:CSS.supports('selector(:focus-visible)'),
  hasHasFocusVisible:CSS.supports('selector(:has(:focus-visible))'),
 }));
 report.capabilities=support;
 assert.equal(support.hasFocusVisible,true,'Browser supports keyboard-visible focus selectors');
 assert.equal(support.hasHasFocusVisible,true,'Browser supports the shared :has() focus guard');

 const buildResponse=await page.request.get(ready.url+'/build.json');
 assert.ok(buildResponse.ok(),'Production fixture serves build identity');
 const servedBuildBytes=await buildResponse.body();
 report.build.servedSha256=hash(servedBuildBytes);
 report.build.servedMetadata=JSON.parse(servedBuildBytes);
 assert.equal(report.build.servedSha256,report.build.diskSha256,'Fixture serves the exact admitted static build marker');
 const stylesheetUrls=await page.evaluate(()=>performance.getEntriesByType('resource')
  .filter(entry=>/\.css(?:[?#]|$)/i.test(entry.name))
  .map(entry=>entry.name).slice(0,13));
 report.build.stylesheetAssetsDropped=Math.max(0,stylesheetUrls.length-12);
 for(const rawUrl of stylesheetUrls.slice(0,12)){
  const asset=new URL(rawUrl,ready.url);
  if(asset.origin!==origin)continue;
  const response=await page.request.get(asset.href);
  assert.ok(response.ok(),`Production stylesheet responds: ${asset.pathname}`);
  const bytes=await response.body();
  report.build.stylesheetAssets.push({path:asset.pathname,sha256:hash(bytes),bytes:bytes.length});
 }

 const fixtureCheck=async()=>await (await page.request.get(ready.url+'/fixture/check')).json();
 const readState=(questionId=null)=>inspect(pane,questionId);
 const wheelAtPane=async delta=>{
  const box=await pane.boundingBox();assert.ok(box,'Transcript pane has a rendered box');
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
  await page.mouse.wheel(0,delta);await settled();
 };
 const wheelToEdge=async edge=>{
  for(let index=0;index<24;index++){
   const current=await readState();
   if(edge==='top'&&current.geometry.scrollTop<=0.5)return current;
   if(edge==='bottom'&&current.geometry.remaining<=1)return current;
   await wheelAtPane(edge==='top'?-1100:1100);
  }
  return readState();
 };
 const assertPaneFrameUnchanged=(before,after,label)=>{
  for(const key of ['x','y','width','height'])
   assert.ok(Math.abs(before.geometry.pane[key]-after.geometry.pane[key])<=1,`${label}: pane ${key} changed`);
 };
 const assertMasked=(state,label)=>assert.ok(maskActive(state),`${label}: overflow mask is active`);

 await page.setViewportSize({width:1280,height:2800});await settled();
 const short=await record('short-content',pane);
 assert.ok(short.geometry.scrollHeight<=short.geometry.clientHeight+1,'Short transcript content fits the pane');
 assert.equal(short.overflow.above,'false');assert.equal(short.overflow.below,'false');
 assert.equal(maskActive(short),false,'Short content has no unnecessary mask');

 await page.setViewportSize({width:1280,height:900});await settled();
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 const ordinaryText=Array.from({length:28},(_,index)=>
  `Synthetic ordinary chat paragraph ${index+1}. ${'This fixture message stays in the ordinary conversation. '.repeat(3)}`
 ).join('\n\n');
 await composer.fill(ordinaryText);
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(pane.locator('.a-message.a-user')).toContainText('Synthetic ordinary chat paragraph 1');
 await expect.poll(async()=>(await fixtureCheck()).sent.length).toBe(1);
 const ordinaryTop=await wheelToEdge('top');
 assert.ok(ordinaryTop.geometry.remaining>1,'Ordinary chat has content below the viewport');
 assert.equal(ordinaryTop.overflow.below,'true');
 assertMasked(ordinaryTop,'Ordinary chat at the top edge');
 const ordinary=await record('ordinary-chat-top',pane);
 await successShot('ordinary-chat-top');

 const anchor= pane.locator('.a-message.a-user').first();
 const anchorBefore=await anchor.evaluate(element=>{
  const pane=element.closest('[data-part="messages"]'),rect=element.getBoundingClientRect(),box=pane.getBoundingClientRect();
  return {id:element.dataset.messageId,offset:rect.top-box.top,paneTop:box.top,scrollTop:pane.scrollTop};
 });
 const questionMarker='Synthetic transcript fade decision';
 const questionPrompt=Array.from({length:32},(_,index)=>
  `${questionMarker}, paragraph ${index+1}. ${'Keep this pending question readable in the shared conversation. '.repeat(3)}`
 ).join('\n\n');
 const created=await page.evaluate(async({sessionId,prompt})=>{
  const receipt=await window.amplifier.dispatch('question.create',{
   sessionId,prompt,required:false,dependency:'Inspect this synthetic question layout',
  });
  if(!receipt?.accepted)throw Error(`Synthetic optional question was refused: ${JSON.stringify(receipt?.error||receipt)}`);
  return receipt.result;
 },{sessionId:ready.sessionId,prompt:questionPrompt});
 assert.ok(created?.id,'The fixture returns the new question identity');
 report.question.id=created.id;
 const question=pane.locator(`[data-question-id="${created.id}"]`);
 const questionSection=pane.locator('[data-part="questions"]');
 await expect(question).toBeVisible();
 await expect(question.locator('.a-question-prompt')).toContainText(`${questionMarker}, paragraph 32`);
 await settled();
 const anchorAfter=await anchor.evaluate(element=>{
  const pane=element.closest('[data-part="messages"]'),rect=element.getBoundingClientRect(),box=pane.getBoundingClientRect();
  return {id:element.dataset.messageId,offset:rect.top-box.top,paneTop:box.top,scrollTop:pane.scrollTop};
 });
 const afterQuestion=await record('ordinary-chat-plus-question-same-pane',pane,created.id);
 assertPaneFrameUnchanged(ordinary,afterQuestion,'Question insertion');
 assert.equal(anchorAfter.id,anchorBefore.id,'Question insertion retains the existing message anchor');
 assert.ok(Math.abs(anchorAfter.offset-anchorBefore.offset)<=1,'Question insertion does not move the existing reading anchor');
 assert.ok(Math.abs(anchorAfter.scrollTop-anchorBefore.scrollTop)<=1,'Question insertion does not change the reader scroll position');
 assert.ok(afterQuestion.geometry.remaining>1,'Question card contributes real overflow to the same pane');

 const review=page.getByRole('button',{name:'Review questions',exact:true});
 await review.scrollIntoViewIfNeeded();await settled();
 const beforePointer=await record('before-pointer-review',pane,created.id);
 assert.equal(beforePointer.focus.questionSectionFocused,false,'Pointer journey begins without manually focusing the question section');
 report.question.pointerActivation='Playwright locator.click on the production Review questions button';
 await review.click();
 await expect(questionSection).toBeFocused();
 await settled();
 const afterPointer=await record('after-pointer-review',pane,created.id);
 assert.equal(afterPointer.focus.questionSectionFocused,true,'Review questions focuses the real question section');
 assert.equal(afterPointer.focus.paneFocusWithin,true,'Question section remains inside the transcript scroller');
 assert.ok(afterPointer.geometry.remaining>1,'Overflow remains after pointer Review questions');
 assert.equal(afterPointer.overflow.below,'true','Question content continues below the viewport after pointer review');
 assert.equal(afterPointer.focus.questionSectionFocusVisible,false,
  'This regression must exercise passive pointer-triggered focus; visible-focus-only coverage is insufficient');
 assertMasked(afterPointer,'Non-visible pointer-triggered section focus');

 const scrollQuestionAcrossBottom=async label=>{
  let current=await readState(created.id);
  for(let index=0;index<12;index++){
   const paneBottom=current.geometry.pane.bottom,questionBounds=current.geometry.question;
   if(questionBounds&&questionBounds.top<paneBottom-1&&questionBounds.bottom>paneBottom+1&&current.geometry.remaining>1)break;
   await wheelAtPane(260);current=await readState(created.id);
  }
  current=await record(label,pane,created.id);
  assert.ok(current.geometry.remaining>1,`${label}: more than one pixel remains below the viewport`);
  assert.equal(current.overflow.below,'true',`${label}: bottom overflow is reported`);
  assert.ok(current.geometry.question.top<current.geometry.pane.bottom-1,`${label}: question enters the viewport`);
  assert.ok(current.geometry.question.bottom>current.geometry.pane.bottom+1,`${label}: question crosses the pane bottom`);
  return current;
 };
 const pointerCrossing=await scrollQuestionAcrossBottom('pointer-review-question-crosses-bottom');
 assert.equal(pointerCrossing.focus.questionSectionFocusVisible,false,
  'Passive question-section focus remains distinct from the keyboard journey');
 assertMasked(pointerCrossing,'Pointer review at the question bottom edge');
 await successShot('question-review-pointer');

 await review.focus();
 await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
 await expect(review).toBeFocused();
 assert.equal(await review.evaluate(element=>element.matches(':focus-visible')),true,
  'Keyboard traversal gives the real Review questions button visible focus');
 await page.keyboard.press('Enter');
 await expect(questionSection).toBeFocused();await settled();
 const keyboardStart=await record('after-keyboard-review',pane,created.id);
 assert.equal(keyboardStart.focus.questionSectionFocused,true);
 assert.equal(keyboardStart.focus.questionSectionFocusVisible,true,
  'Keyboard activation transfers visible focus to the question section');
 assert.ok(keyboardStart.geometry.remaining>1);
 assert.equal(maskActive(keyboardStart),false,'Keyboard-visible question focus suppresses the mask');
 const keyboardCrossing=await scrollQuestionAcrossBottom('keyboard-review-question-crosses-bottom');
 assert.equal(keyboardCrossing.focus.questionSectionFocusVisible,true);
 assert.equal(maskActive(keyboardCrossing),false,'Keyboard-visible focus stays fully unmasked during scrolling');
 await successShot('question-review-keyboard');

 const editor=question.locator('textarea');
 await editor.click();
 await editor.fill('Synthetic local draft. Do not submit this question.');
 await expect(editor).toHaveValue('Synthetic local draft. Do not submit this question.');
 const editing=await record('question-textarea-editing',pane,created.id);
 assert.equal(editing.focus.activeTextarea,true);
 assert.equal(editing.focus.activeInsidePane,true);
 assert.equal(maskActive(editing),false,'Active question editing remains fully readable');
 await successShot('question-editor-focused');
 const scrollBeforeBlur=editing.geometry.scrollTop;
 await composer.click();
 await expect(composer).toBeFocused();await settled();
 const blurred=await record('question-editor-blurred-to-composer',pane,created.id);
 assert.ok(Math.abs(blurred.geometry.scrollTop-scrollBeforeBlur)<=1,'Blurring to the composer does not move the transcript');
 assert.equal(blurred.focus.activeInsidePane,false);
 assertMasked(blurred,'Focus outside the transcript restores the overflow fade');

 await page.emulateMedia({forcedColors:'active'});
 const forcedColors=await record('forced-colors',pane,created.id);
 assert.equal(forcedColors.focus.forcedColors,true);
 assert.equal(maskActive(forcedColors),false,'Forced colors removes the transcript mask');
 await page.emulateMedia({forcedColors:'none'});
 await expect.poll(async()=>maskActive(await readState())).toBe(true);

 const bottom=await wheelToEdge('bottom');
 assert.ok(bottom.geometry.remaining<=1,'Real wheel scrolling reaches the transcript end');
 assert.equal(bottom.overflow.below,'false','The bottom fade is absent at the end');
 assert.ok(bottom.overflow.above==='true'||bottom.overflow.above==='false');
 if(bottom.overflow.above==='true')assertMasked(bottom,'Top overflow remains visible at the transcript end');
 else assert.equal(maskActive(bottom),false,'A transcript with no remaining overflow has no mask');
 await record('transcript-bottom',pane,created.id);

 await question.getByRole('button',{name:'Skip question',exact:true}).click();
 await expect(question).toHaveCount(0,'Optional question leaves the active transcript after cancellation');
 await page.getByRole('button',{name:'Chat',exact:true}).click();
 await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Chat info',exact:true}).click();
 await page.getByText('Questions & answers',{exact:true}).click();
 const history=page.locator('.a-question-history');
 await expect(history).toBeVisible();
 await history.locator(':scope > summary').click();
 const historyItem=page.locator('.a-question-history .a-question-item').filter({hasText:questionMarker});
 await expect(historyItem).toHaveCount(1);
 const historySummary=historyItem.locator(':scope > summary');
 await expect(historySummary).toBeVisible();
 const historyMaskBefore=await inspect(pane,created.id);
 await historySummary.focus();
 await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
 await expect(historySummary).toBeFocused();
 const historyFocus=await record('question-history-summary-keyboard',pane,created.id);
 const outline=await historySummary.evaluate(element=>{
  const style=getComputedStyle(element);
  return {outlineStyle:style.outlineStyle,outlineWidth:parseFloat(style.outlineWidth),focusVisible:element.matches(':focus-visible')};
 });
 report.focus.at(-1).outline=outline;
 assert.equal(outline.focusVisible,true,'Closed-question summary retains keyboard-visible focus');
 assert.notEqual(outline.outlineStyle,'none','Closed-question summary has a visible focus outline');
 assert.ok(outline.outlineWidth>=2,'Closed-question summary focus outline remains at least 2px');
 assert.equal(historyFocus.focus.activeInsidePane,false,'History disclosure focus is outside the transcript scroller');
 assert.equal(historyFocus.mask.standard,historyMaskBefore.mask.standard,'History-summary focus does not alter transcript masking');

 const final=await fixtureCheck();
 report.question.answerSubmitted=report.question.answerRequests.length>0;
 assert.deepEqual(report.question.answerRequests,[],'The regression never submits a question answer');
 assert.equal(final.sent.length,1,'Only the synthetic ordinary chat message reaches the fixture runtime');
 assert.equal(final.sent[0].text,ordinaryText);
 assert.equal(final.retries.length,0,'The synthetic fixture performs no retries');
 assert.equal(final.questions.find(row=>row.id===created.id)?.status,'cancelled',
  'The optional synthetic question is cancelled without an answer');
 assert.deepEqual(report.browserErrors,[],'Production page has no uncaught errors');
 assert.deepEqual(report.httpErrors,[],'Production page has no failed local responses');
 assert.deepEqual(report.externalRequests,[],'Fixture browser contacts no external origin');
 assert.deepEqual(report.instrumentationErrors,[],'Bounded evidence observers remain healthy');
 assert.equal(report.evidenceDropped,0,'No bounded evidence was dropped');
 assert.deepEqual(sourceHashes(),report.sourceSha256,'Authored source remains unchanged during the browser journey');
 const servedBuild=await page.request.get(ready.url+'/build.json');
 assert.equal(hash(await servedBuild.body()),report.build.servedSha256,'Served build identity remains stable');
}catch(error){
 failure=error;
 report.failure={name:error?.name||'Error',message:String(error?.message||error),stack:String(error?.stack||error).slice(0,8000)};
 await failureShot();
}finally{
 report.question.answerSubmitted=report.question.answerRequests.length>0;
 report.fixture.stderrTail=stderr;
 report.fixture.stderrCharactersObserved=stderrCharacters;
 report.fixture.stderrTruncated=stderrCharacters>report.limits.stderrTailCharacters;
 if(stopFixture){
  try{
   await finishFixture({
    label:'Question transcript fade fixture',
    originalError:failure,
    close:[['Question transcript fade browser',async()=>{
     if(browser){await browser.close();report.browser.closed=true}
    }]],
    stop:async()=>{
     const result=await stopFixture();
     report.fixture.shutdown={code:result.code,signal:result.signal,closeObserved:!!report.fixture.close?.observed};
    },
   });
  }catch(error){
   report.cleanupError=String(error?.stack||error).slice(0,4000);
   failure??=error;
  }
 }
 if(report.fixture.started&&!report.fixture.close?.observed){
  report.cleanupError??='Fixture close event was not observed.';
  failure??=Error(report.cleanupError);
 }
 if(report.browser.started&&!report.browser.closed){
  report.cleanupError??='Browser close did not resolve.';
  failure??=Error(report.cleanupError);
 }
 report.status=failure?'failed':'passed';
 if(failure&&!report.failure)report.failure={name:failure.name||'Error',message:String(failure.message||failure),stack:String(failure.stack||failure).slice(0,8000)};
 writeReport();
 console.log(JSON.stringify({
  status:report.status,artifacts,report:join(artifacts,'report.json'),
  checks:{questionPointerFocus:report.focus.some(row=>row.label==='after-pointer-review'),
   keyboardFocus:report.focus.some(row=>row.label==='after-keyboard-review'),
   editorFocus:report.focus.some(row=>row.label==='question-textarea-editing'),
   historySummary:report.focus.some(row=>row.label==='question-history-summary-keyboard')},
  screenshots:report.screenshots.map(row=>row.file),
  fixtureClose:report.fixture.close,fixtureShutdown:report.fixture.shutdown,
 }));
}
if(failure)throw failure;