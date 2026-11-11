// Rendered source UI with synthetic HTTP/SSE; no model, external push or live permission.
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';
import './composer-test-helpers.mjs';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const artifact=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/input-status';await mkdir(artifact,{recursive:true});
const now=Math.floor(Date.now()/1000);
const input={id:'input',inputId:'one',role:'user',via:'text',text:'A short request.',createdAt:now-100,delivery:{status:'accepted'}};
const state={revision:1,settings:{workspace:'/fixture'},runtime:{available:true},view:{navPinned:true},sessions:[{id:'chat',title:'Status fixture',sessionKind:'root',workspace:'/fixture',workspaceId:'project',status:'working',historyManaged:true,historyLoaded:true,messages:[input],workers:[],execution:{turns:[{id:'unrelated',inputId:'other',phase:'running',startedAt:now-999}],nodes:[]}}],workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'chat',selectedWorkspaceId:'project',setup:{providers:[{id:'fixture',module:'provider-fixture',config:{default_model:'fixture-model'}}],providersLoadedAt:1,providersWorkspace:'/fixture'},canvas:{open:false},notificationMessages:[]};
const shell=createShellHttpFixture(()=>state),calls=[],errors=[];let vite,browser,page;
const emit=async()=>{const revision=++state.revision;await page.evaluate(s=>window.emitState(s),shell.state());await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().revision)).toBeGreaterThanOrEqual(revision)};
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1280,height:900},hasTouch:true,isMobile:true});page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(installFixtureEvents);
 await page.addInitScript(()=>{
  window.fixtureNotifications=[];
  window.Notification=class{
   static permission='granted';
   constructor(title,options){this.title=title;this.options=options;window.fixtureNotifications.push(this)}
  };
  Object.defineProperty(navigator,'serviceWorker',{value:{register:async()=>{throw Error('fixture does not register a worker')},getRegistration:async()=>undefined}});
 });
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname,response=shell.response(route.request());
  if(response!==undefined)return route.fulfill({json:response});
  if(path==='/api/state')return route.fulfill({json:shell.state()});
  if(path==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  if(path!=='/api/actions')return route.fulfill({json:{ok:true}});
  const body=route.request().postDataJSON();calls.push(body);
  if(body.action==='view.update')state.view={...state.view,...body.args.patch};
  if(body.action==='session.select')state.selectedSessionId=body.args.id;
  state.revision++;return route.fulfill({json:{accepted:true,state:shell.state()}});
 });
 await page.goto(vite.resolvedUrls.local[0]);
 const row=page.locator('[data-message-id="input"]'),status=row.locator('.a-input-status');
 await expect(status).toHaveAttribute('data-input-phase','received');
 assert.doesNotMatch(await status.innerText(),/Working|Read /);
 await status.locator('summary').click();await expect(status).toContainText('Model inclusion and execution are not yet confirmed');
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.fill('Another thought while working');
 input.inputStatus={version:1,inputId:'one',phase:'working',basis:'execution',startedAt:now-42,updatedAt:now};
 await emit();await expect(status).toHaveAttribute('data-input-phase','working');
 await expect(status.locator('.a-input-duration')).toContainText(/0:4|0:5|1:/);
 await expect(status.locator('summary')).toHaveAccessibleName(/^Working.*Observed elapsed time \d+:\d{2}.*Status observed /);
 await expect(composer).toHaveDraft('Another thought while working');
 await page.screenshot({path:artifact+'/status-desktop.png'});
 for(const width of [390,320]){
  await page.setViewportSize({width,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  const button=row.getByRole('button',{name:'Copy message as Markdown'});
  assert.ok((await button.boundingBox()).height>=44);
  await page.screenshot({path:artifact+`/status-mobile-${width}.png`});
 }
 for(const phase of ['waiting','done','failed','unknown']){
  input.inputStatus={...input.inputStatus,phase,endedAt:now,reason:phase==='waiting'?'Waiting for background work.':undefined};
  await emit();await expect(status).toHaveAttribute('data-input-phase',phase);
 }
 // Correction survives reload; no synthetic session-idle/ambient busy inference.
 input.inputStatus={...input.inputStatus,phase:'done'};await emit();await page.reload();
 await expect(page.locator('[data-message-id="input"] .a-input-status')).toHaveAttribute('data-input-phase','done');
 assert.equal(await page.evaluate(()=>window.fixtureNotifications.length),0);
 // The host's bounded native transcript rows are display-only, but an exact
 // native binding can carry authoritative saved observations (never a live clock).
 const saved={id:'native-window:working',role:'user',displayOnly:true,nativeInputId:'saved',text:'Saved exact request.',inputStatus:{version:1,inputId:'saved',phase:'working',basis:'execution_observed',startedAt:now-100,updatedAt:now-58}};
 const controls=[
  ['no-evidence',{inputStatus:undefined,delivery:{status:'accepted'}}],
  ['no-receipt',{inputStatus:undefined}],
  ['unbound',{nativeInputId:undefined,inputId:'saved'}],
  ['ambiguous',{nativeInputAmbiguous:true}],
  ['conflict',{inputId:'other'}],
  ['mismatched',{inputStatus:{...saved.inputStatus,inputId:'other'}}],
  ['unversioned',{inputStatus:{...saved.inputStatus,version:undefined}}],
  ['no-basis',{inputStatus:{...saved.inputStatus,basis:'no_evidence'}}],
  ['observation',{observation:{source:'amplifier-delegate'}}],
  ['peer',{via:'peer',inputOrigin:'peer'}],
 ].map(([name,patch])=>({...saved,...patch,id:'native-window:'+name}));
 const finished={...saved,id:'native-window:done',nativeInputId:'finished',inputStatus:{...saved.inputStatus,inputId:'finished',phase:'done',endedAt:now-50}};
 state.sessions[0].messages=[input,saved,finished,...controls];await emit();
 const savedRow=page.locator('[data-message-id="native-window:working"]'),savedStatus=savedRow.locator('.a-input-status');
 const checkSaved=async()=>{
  await expect(savedStatus).toHaveAttribute('data-input-phase','working');
  await expect(savedStatus.locator('summary > [aria-live="polite"][aria-atomic="true"]')).toHaveText('Working last observed · saved history');
  await expect(savedStatus.locator('.a-input-duration')).toHaveText(' · 0:42');
  await expect(savedStatus.locator('summary')).toHaveAccessibleName(/^Working last observed · saved history.*Observed elapsed time 0:42.*Status observed /);
  await expect(page.locator('[data-message-id="native-window:done"] .a-input-status')).toHaveAttribute('data-input-phase','done');
  await expect(page.locator('[data-message-id="native-window:done"] .a-input-duration')).toHaveText(' · 0:50');
  for(const control of controls)await expect(page.locator(`[data-message-id="${control.id}"] .a-input-status`)).toHaveCount(0);
  await expect(savedRow.getByRole('button',{name:'Copy message as Markdown'})).toHaveCount(1);
  await expect(savedRow.getByRole('button',{name:/Edit message|Fork conversation|Reply to|React to/})).toHaveCount(0);
 };
 await checkSaved();
 const summary=savedStatus.locator('summary'),explanation='Execution observed for this exact input, not proof that the model read or understood it.';
 const detail=savedStatus.getByText(explanation,{exact:true});
 await summary.focus();
 for(const key of ['Enter','Space']){
  await expect(detail).toBeHidden();assert.ok(!(await savedStatus.ariaSnapshot()).includes(explanation));
  await summary.press(key);await expect(savedStatus).toHaveJSProperty('open',true);
  await expect(detail).toBeVisible();assert.ok((await savedStatus.ariaSnapshot()).includes(explanation));
  await expect(summary).toBeFocused();
  await summary.press(key);await expect(savedStatus).toHaveJSProperty('open',false);
  await expect(detail).toBeHidden();assert.ok(!(await savedStatus.ariaSnapshot()).includes(explanation));
  await expect(summary).toBeFocused();
 }
 await page.waitForTimeout(1200);await checkSaved();
 await page.reload();await checkSaved();
 state.notificationSettings={desktop:true,needsInput:false,preview:true};
 state.attention={items:[{id:'question:disabled',sessionId:'other',fingerprint:'disabled-v1',detail:'Private disabled question'}]};
 await emit();assert.equal(await page.evaluate(()=>window.fixtureNotifications.length),0);
 state.notificationSettings.needsInput=true;
 await emit();assert.equal(await page.evaluate(()=>window.fixtureNotifications.length),0); // No replay on opt-in.
 state.attention.items.push({id:'question:q1',sessionId:'other',fingerprint:'q1v1',detail:'Private question'});
 await emit();await expect.poll(()=>page.evaluate(()=>window.fixtureNotifications.length)).toBe(1);
 assert.equal(await page.evaluate(()=>window.fixtureNotifications[0].options.body),'Amplifier needs your input.');
 await emit();assert.equal(await page.evaluate(()=>window.fixtureNotifications.length),1);
 // A visible selected-chat event is suppressed, not deferred until hidden.
 state.attention.items.push({id:'question:visible',sessionId:'chat',fingerprint:'visible-v1'});
 await emit();assert.equal(await page.evaluate(()=>window.fixtureNotifications.length),1);
 const messages=page.getByRole('log',{name:'Conversation messages'});
 await expect(messages).toHaveAttribute('data-session-id','chat');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),'chat');
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'))});
 await emit();assert.equal(await page.evaluate(()=>window.fixtureNotifications.length),1); // No replay on hiding.
 state.attention.items.push({id:'question:q2',sessionId:state.selectedSessionId,fingerprint:'q2v1',detail:'Private selected-chat question'});
 input.inputStatus={...input.inputStatus,phase:'failed'}; // Detect a render improperly advancing while hidden.
 await emit();await expect.poll(()=>page.evaluate(()=>window.fixtureNotifications.length)).toBe(2);
 assert.deepEqual(await page.evaluate(()=>({hidden:document.hidden,selected:window.amplifier.getState().selectedSessionId,target:window.fixtureNotifications[1].options.data.sessionId,body:window.fixtureNotifications[1].options.body})),{hidden:true,selected:'chat',target:'chat',body:'Amplifier needs your input.'});
 await expect(messages).toHaveAttribute('data-session-id','chat');
 await expect(status).toHaveAttribute('data-input-phase','done'); // Notification arrived before foreground/render.
 await emit();assert.equal(await page.evaluate(()=>window.fixtureNotifications.length),2);
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'))});
 await expect(status).toHaveAttribute('data-input-phase','failed');
 assert.equal(await page.evaluate(()=>window.fixtureNotifications.length),2);
 const clickStart=calls.length;
 await page.evaluate(()=>window.fixtureNotifications[0].onclick());
 await expect.poll(()=>calls.filter(c=>c.action==='session.select').map(c=>c.args.id)).toEqual(['other']);
 await expect.poll(()=>calls.slice(clickStart).some(c=>c.action==='view.update'&&c.args.patch.mode==='text')).toBe(true);
 assert.ok(calls.slice(clickStart).every(c=>['session.select','view.update'].includes(c.action)));
 assert.deepEqual(calls.filter(c=>['conversation.send','conversation.retry','approval.respond','question.answer','question.cancel','question.supersede','message.react'].includes(c.action)),[]);
 await page.reload();await expect(page.locator('[data-part="app"]')).toBeVisible();
 assert.equal(await page.evaluate(()=>window.fixtureNotifications.length),0); // Existing attention is not replayed on opening.
 assert.deepEqual(errors,[]);
 await writeFile(artifact+'/input-status-browser.json',JSON.stringify({passed:true,widths:[1280,390,320],cases:['receipt-not-read','unrelated-busy','observed-timer','summary-accessible-phase-and-elapsed','keyboard-enter-space-disclosure','accessible-explanation-expanded-only','disclosure-focus-retained','draft-while-working','status-corrections','reload','touch-targets','native-saved-exact-evidence','native-saved-frozen-timer','native-saved-terminal','native-saved-no-fallback','native-saved-unbound-ambiguous-conflict','native-saved-malformed-evidence','native-saved-observation-peer-excluded','native-saved-copy-only','native-saved-reload','opt-in-decision-notice','no-replay-on-opt-in','visible-selected-chat-suppressed','no-replay-on-hiding','hidden-selected-chat-notice-before-render','generic-decision-body-with-preview','dedup','no-replay-on-foreground-or-reload','click-is-navigation-only','no-execution-or-approval'],calls,errors},null,2));
 console.log('Input status browser passed (synthetic peer, no provider or push).');
}finally{await browser?.close();await vite?.close()}