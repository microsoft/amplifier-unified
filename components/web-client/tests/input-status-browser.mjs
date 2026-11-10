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
const emit=async()=>{state.revision++;await page.evaluate(s=>window.emitState(s),shell.state())};
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
 state.notificationSettings={desktop:true,needsInput:true};
 state.attention={items:[{id:'question:q1',sessionId:'other',fingerprint:'q1v1',detail:'Private question'}]};
 await emit();await expect.poll(()=>page.evaluate(()=>window.fixtureNotifications.length)).toBe(1);
 assert.equal(await page.evaluate(()=>window.fixtureNotifications[0].options.body),'Amplifier needs your input.');
 await emit();assert.equal(await page.evaluate(()=>window.fixtureNotifications.length),1);
 await page.evaluate(()=>window.fixtureNotifications[0].onclick());
 await expect.poll(()=>calls.filter(c=>c.action==='session.select').map(c=>c.args.id)).toEqual(['other']);
 await page.evaluate(()=>Object.defineProperty(document,'hidden',{configurable:true,get:()=>true}));
 state.attention.items.push({id:'question:q2',sessionId:'chat',fingerprint:'q2v1'});
 await emit();await expect.poll(()=>page.evaluate(()=>window.fixtureNotifications.length)).toBe(2);
 await page.evaluate(()=>Object.defineProperty(document,'hidden',{configurable:true,get:()=>false}));
 assert.deepEqual(calls.filter(c=>['conversation.send','conversation.retry','approval.respond','message.react'].includes(c.action)),[]);
 assert.deepEqual(errors,[]);
 await writeFile(artifact+'/input-status-browser.json',JSON.stringify({passed:true,widths:[1280,390,320],cases:['receipt-not-read','unrelated-busy','observed-timer','draft-while-working','status-corrections','reload','touch-targets','opt-in-decision-notice','dedup','click-is-navigation-only','no-execution-or-approval'],calls,errors},null,2));
 console.log('Input status browser passed (synthetic peer, no provider or push).');
}finally{await browser?.close();await vite?.close()}