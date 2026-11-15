// Rendered client with synthetic HTTP/SSE. This is not native mobile/IME or
// actual-host admission acceptance; the integration owner runs the paired check.
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';
import './composer-test-helpers.mjs';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

const artifact=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/composer-return';
const cases=[
 {name:'desktop',viewport:{width:1280,height:900},safe:false},
 {name:'narrow-desktop',viewport:{width:390,height:844},safe:false},
 {name:'phone',viewport:{width:390,height:844},hasTouch:true,isMobile:true,safe:true},
 {name:'android',viewport:{width:390,height:844},hasTouch:true,isMobile:true,userAgent:'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36',safe:true,deferred:true},
 {name:'tablet',viewport:{width:1024,height:900},hasTouch:true,safe:true},
 {name:'hybrid',viewport:{width:1280,height:900},hybrid:true,safe:true},
 {name:'ipad-desktop',viewport:{width:1280,height:900},hybrid:true,userAgent:'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15',safe:true,deferred:true,safari:true},
 {name:'unknown-touch-capability',viewport:{width:1280,height:900},unknown:true,safe:true},
];
const results=[];let browser,vite;
await mkdir(artifact,{recursive:true});
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true});
 for(const scenario of cases){
  const context=await browser.newContext({viewport:scenario.viewport,hasTouch:!!scenario.hasTouch,isMobile:!!scenario.isMobile,...(scenario.userAgent?{userAgent:scenario.userAgent}:{})});
  try{
   const page=await context.newPage(),calls=[],errors=[];
   const state={revision:1,settings:{workspace:'/fixture'},runtime:{available:true},view:{navPinned:true},sessions:[{id:'chat',title:'Return fixture',sessionKind:'root',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[]}],workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'chat',selectedWorkspaceId:'project',setup:{providers:[{id:'fixture',module:'provider-fixture',config:{default_model:'fixture-model'}}],providersLoadedAt:1,providersWorkspace:'/fixture'},canvas:{open:false}};
   const shell=createShellHttpFixture(()=>state);
   page.on('pageerror',error=>errors.push(error.message));
   await page.addInitScript(installFixtureEvents);
   if(scenario.hybrid)await page.addInitScript(()=>Object.defineProperty(navigator,'maxTouchPoints',{get:()=>5}));
   if(scenario.safari)await page.addInitScript(()=>Object.defineProperty(navigator,'vendor',{get:()=>'Apple Computer, Inc.'}));
   if(scenario.unknown)await page.addInitScript(()=>Object.defineProperty(navigator,'maxTouchPoints',{get:()=>undefined}));
   await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname,response=shell.response(route.request());
    if(response!==undefined)return route.fulfill({json:response});
    if(path==='/api/state')return route.fulfill({json:shell.state()});
    if(path==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
    if(path!=='/api/actions')return route.fulfill({json:{ok:true}});
    const body=route.request().postDataJSON();calls.push(body);
    if(body.action==='view.update')state.view={...state.view,...body.args.patch};
    if(body.action==='conversation.send')state.sessions[0].messages.push({id:'sent-'+body.id,inputId:body.id,role:'user',text:body.args.text,delivery:{status:'accepted'}});
    state.revision++;
    return route.fulfill({json:{accepted:true,delivery:'accepted',state:shell.state()}});
   });
   await page.goto(vite.resolvedUrls.local[0]);
   const composer=page.getByRole('textbox',{name:'Message Amplifier'}),sends=()=>calls.filter(call=>call.action==='conversation.send');
   await expect(composer).toBeVisible();
   await expect(composer).toHaveAttribute('enterkeyhint','enter');
   await expect(composer).toHaveAccessibleDescription(scenario.safe?'Return adds a new line. Use Send to send.':'Enter to send. Shift+Enter or Ctrl+Enter adds a new line.');
   const retained=async text=>{
    await expect(composer).toHaveDraft(text);
    await expect.poll(()=>state.view.draft).toBe(text);
    assert.equal(sends().length,0);
    assert.equal(await page.locator('.a-user').count(),0,'No provisional outgoing bubble');
    const entries=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]'));
    assert.equal(entries.length,0,'Editing does not create an outbox command');
   };
   for(const inputType of ['insertParagraph','insertLineBreak']){
    await composer.fill('First');await composer.press('End');
    const prevented=await composer.evaluate((node,inputType)=>!node.dispatchEvent(new InputEvent('beforeinput',{bubbles:true,cancelable:true,inputType})),inputType);
    assert.equal(prevented,true);await page.keyboard.type('Second');
    await retained('First\n\nSecond');
   }
   // A noncancelable event has no native default when dispatched synthetically.
   // Verify no custom mutation, then let a browser edit reach the DOM observer.
   await composer.fill('First');await composer.press('End');
   const noncancelable=await composer.evaluate(node=>{
    const before=node.innerHTML,input=new InputEvent('beforeinput',{bubbles:true,cancelable:false,inputType:'insertParagraph'});
    node.dispatchEvent(input);return {before,after:node.innerHTML,prevented:input.defaultPrevented};
   });
   assert.equal(noncancelable.before,noncancelable.after);assert.equal(noncancelable.prevented,false);
   await composer.evaluate(()=>document.execCommand('insertParagraph'));
   await page.keyboard.type('Second');await retained('First\n\nSecond');
   // Native keydown/default sequencing: canceled Return must not double-insert.
   await composer.fill('First');await composer.press('End');
   await composer.evaluate(node=>{
    window.returnEvents=[];
    for(const type of ['keydown','beforeinput'])node.addEventListener(type,event=>window.returnEvents.push({type:event.type,key:event.key,inputType:event.inputType,prevented:event.defaultPrevented}));
   });
   await composer.press(scenario.safe?'Enter':'Shift+Enter');await page.keyboard.type('Second');
   await retained('First\n\nSecond');
   const events=await page.evaluate(()=>window.returnEvents);
   if(scenario.deferred)await page.waitForTimeout(300); // Beyond PM's iOS fallback, not application dedup.
   await retained('First\n\nSecond');
   if(!scenario.deferred){
    assert.ok(events.some(event=>event.type==='keydown'&&event.key==='Enter'&&event.prevented));
    assert.equal(events.filter(event=>event.type==='beforeinput'&&['insertParagraph','insertLineBreak'].includes(event.inputType)).length,0,'Canceled keydown suppresses the native paragraph default');
   }
   await composer.press('Control+Enter');await page.keyboard.type('Third');await retained('First\n\nSecond\n\nThird');
   // Candidate confirmation is left native; these flags are synthetic guards,
   // not proof of compositionend ordering on physical iOS/Android keyboards.
   await composer.evaluate(node=>{
    node.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,cancelable:true,key:'Enter',isComposing:true}));
    node.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,cancelable:true,key:'Enter',keyCode:229}));
    node.dispatchEvent(new InputEvent('beforeinput',{bubbles:true,cancelable:true,inputType:'insertParagraph',isComposing:true}));
   });
   await retained('First\n\nSecond\n\nThird');
   if(scenario.safari){
    await composer.evaluate(node=>{
     node.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));
     node.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true}));
     // Safari's candidate-confirmation ordering has already cleared the
     // composing flag. The event itself has no synthetic browser default.
     node.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,cancelable:true,key:'Enter',keyCode:13}));
    });
    await retained('First\n\nSecond\n\nThird');
    await composer.press('Enter');await page.keyboard.type('Fourth');
    await page.waitForTimeout(300);
    await retained('First\n\nSecond\n\nThird\n\nFourth');
    await composer.fill('First\n\nSecond\n\nThird');
   }
   if(scenario.name==='hybrid'){
    await composer.fill('Native deletion pending');await composer.press('End');
    await composer.evaluate(async node=>{
     const text=node.querySelector('p').firstChild;
     text.nodeValue='Kept';
     const range=document.createRange();range.setStart(text,4);range.collapse(true);
     const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
     // Let the MutationObserver observe the edit before the next input. A
     // same-task script mutation+keydown is not a native browser input stream.
     await new Promise(resolve=>queueMicrotask(resolve));
     node.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,cancelable:true,key:'Enter',keyCode:13}));
    });
    await page.keyboard.type('Second');await retained('Kept\n\nSecond');
    await composer.fill('First\n\nSecond\n\nThird');
   }
   await page.setViewportSize({width:scenario.viewport.width===390?1280:390,height:844});
   await expect(composer).toHaveAccessibleDescription(scenario.safe?/Return adds/:/Enter to send/);
   if(!scenario.safe){
    await composer.press('Enter');
    await expect.poll(()=>sends().length).toBe(1);
    assert.equal(sends()[0].args.text,'First\n\nSecond\n\nThird');
    await expect(composer).toHaveDraft('');
    await composer.fill('New draft');
    await composer.evaluate(node=>node.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,cancelable:true,key:'Enter',repeat:true})));
    await expect(composer).toHaveDraft('New draft');assert.equal(sends().length,1);
   }
   const beforePreference=sends().length;
   await composer.fill('Preference retains this draft');
   await expect.poll(()=>state.view.draft).toBe('Preference retains this draft');
   await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'settings',settingsExpanded:['appearance']}}));
   const choice=page.getByLabel('Return key',{exact:true});
   await expect(choice).toHaveValue('automatic');await choice.selectOption('newline');
   await expect(choice).toHaveValue('newline');
   assert.equal(await page.evaluate(()=>localStorage.getItem('amplifier.composer.returnKey.v1')),'newline');
   assert.equal(sends().length,beforePreference);
   await expect(composer).toHaveDraft('Preference retains this draft');
   await page.reload();
   await expect(choice).toHaveValue('newline');await expect(composer).toHaveDraft('Preference retains this draft');
   await page.getByRole('button',{name:/^Close (settings|panel)$/}).last().click();
   await expect(composer).toHaveAccessibleDescription('Return adds a new line. Use Send to send.');
   // End stops at a visual line after the narrow-width reflow. This journey
   // appends to the entire draft, not to whichever wrapped line kept the caret.
   await composer.press('Control+End');
   assert.equal(await composer.evaluate(node=>{
    const selection=getSelection();
    if(!selection?.isCollapsed||!node.contains(selection.focusNode))return false;
    const tail=document.createRange();tail.selectNodeContents(node);
    tail.setStart(selection.focusNode,selection.focusOffset);
    return tail.toString()==='';
   }),true,'Document-end navigation leaves no draft text after the caret');
   await composer.press('Enter');await page.keyboard.type('Still writing');
   await expect(composer).toHaveDraft('Preference retains this draft\n\nStill writing');
   assert.equal(sends().length,beforePreference);
   // Explicit keyboard activation of Send still uses the existing guarded
   // send path and retains the complete semantic multiline payload.
   const send=page.getByRole('button',{name:'Send message',exact:true});
   await expect(send).toBeEnabled();await send.focus();await send.press('Enter');
   await expect.poll(()=>sends().length).toBe(beforePreference+1);
   assert.equal(sends().at(-1).args.text,'Preference retains this draft\n\nStill writing');
   await expect(composer).toHaveDraft('');
   assert.deepEqual(errors,[]);
   results.push({scenario:scenario.name,passed:true,nativeEvents:events,submissions:sends().length});
  }finally{await context.close();}
 }
 await writeFile(artifact+'/result.json',JSON.stringify({scope:'synthetic client Chromium',results},null,2)+'\n');
 console.log(JSON.stringify({scope:'synthetic client Chromium',results}));
}finally{await browser?.close();await vite?.close();}