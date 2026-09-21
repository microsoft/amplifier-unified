import {openSettingsPage} from './browser-settings.mjs';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
let state={revision:1,settings:{workspace:'/fixture',bundle:'anchors'},runtime:{available:true},view:{navPinned:true,panel:'settings',settingsSection:'setup',settingsExpanded:['providers'],providerEditor:{id:'one',module:'provider-openai',model:'model-a',config:'{}',scope:'global',credentialMode:'private',source:'',advanced:false,dirty:true}},sessions:[{id:'chat',sessionKind:'root',title:'Saved chat',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[]}],workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'chat',selectedWorkspaceId:'project',setup:{providers:[{id:'one',module:'provider-openai',config:{default_model:'model-a'},credentialsConfigured:true}],providersLoadedAt:1,providersWorkspace:'/fixture',metadata:{'provider-openai':{info:{config_fields:[]}}},providerCatalogs:{one:{phase:'ready',models:[{id:'model-a'},{id:'model-b'}]}}},canvas:{open:false}};
let browser,vite;const errors=[],waiting=[],calls=[];
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function pending(action){for(let i=0;i<100&&!waiting.some(row=>row.body.action===action);i++)await sleep(10);const index=waiting.findIndex(row=>row.body.action===action);assert.ok(index>=0,'Missing '+action);return waiting.splice(index,1)[0]}
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false}});await vite.listen();
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1280,height:900}});page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{const sources=[];window.EventSource=class extends EventTarget{constructor(){super();sources.push(this);setTimeout(()=>this.onopen?.(),0)}close(){}};window.emitState=state=>sources.forEach(source=>source.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(state)})));});
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path==='/api/state')return route.fulfill({json:state});
  if(path==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  if(path==='/api/actions'){
   const body=route.request().postDataJSON();calls.push(body);
   if(['providers.models','locations.list','session.draft','notifications.get','maintenance.backup'].includes(body.action)){waiting.push({route,body});return}
   if(body.action==='view.update')state={...state,revision:state.revision+1,view:{...state.view,...body.args.patch}};
   return route.fulfill({json:{accepted:true,state}});
  }
  return route.fulfill({json:{ok:true}});
 });
 await page.goto(vite.resolvedUrls.local[0]);await page.locator('#provider-model').waitFor();
 await page.getByRole('button',{name:'Refresh models',exact:true}).click();const refresh=await pending('providers.models');
 assert.equal(await page.locator('[data-action="providers.models"][data-action-pending]').count(),1);
 assert.equal(await page.locator('[data-activity-region="provider-models"]').getAttribute('aria-busy'),'true');
 assert.equal(await page.locator('#provider-model').inputValue(),'model-a');
 assert.equal(await page.locator('#provider-model-catalog option[value="model-b"]').count(),1);
 assert.equal(await page.locator('[data-activity-region="provider-options"]').getAttribute('aria-busy'),null);
 state.setup.operations={'providers.models:one':{phase:'working',commandId:refresh.body.id}};state.setup.providerCatalogs.one={...state.setup.providerCatalogs.one,phase:'working'};state.revision++;
 await refresh.route.fulfill({json:{accepted:true,state}});
 await page.waitForFunction(()=>!document.querySelector('[data-action="providers.models"][data-action-pending]'));
 assert.equal(await page.locator('[data-activity-region="provider-models"]').getAttribute('aria-busy'),'true');
 assert.equal(await page.locator('[data-action="providers.models"]').getAttribute('aria-busy'),'true');
 assert.equal(await page.locator('#provider-model-catalog option[value="model-b"]').count(),1);
 await page.locator('#provider-source').count();
 // User edits survive independent completions and dismissal.
 state.setup.operations['providers.models:one']={phase:'error',commandId:refresh.body.id,error:'Fixture unavailable'};state.setup.providerCatalogs.one={...state.setup.providerCatalogs.one,phase:'error'};state.revision++;
 await page.evaluate(value=>window.emitState(value),state);
 await page.waitForFunction(()=>!document.querySelector('[data-activity-region="provider-models"][data-region-pending]'));
 assert.equal(await page.locator('#provider-model').evaluate(el=>el.tagName),'INPUT');
 assert.equal(await page.locator('#provider-model-options option[value="model-b"]').count(),1);
 await page.locator('#provider-model').fill('custom-model-after-failure');await page.locator('#provider-id').click();
 await page.waitForFunction(()=>window.amplifier.getState().view.providerEditor.model==='custom-model-after-failure');
 await page.locator('#provider-id').fill('unsaved-name');
 assert.equal(await page.locator('#provider-id').inputValue(),'unsaved-name');
 await page.locator('#provider-id').click();assert.equal(await page.locator('[role="dialog"]').count(),1);
 const input=await page.locator('#provider-id').boundingBox();await page.mouse.move(input.x+10,input.y+10);await page.mouse.down();await page.mouse.move(2,2);await page.mouse.up();assert.equal(await page.locator('[role="dialog"]').count(),1);
 await page.mouse.click(2,2);await page.waitForFunction(()=>!document.querySelector('[role="dialog"]'));
 await page.getByRole('button',{name:'Settings',exact:true}).click();await page.locator('#provider-id').waitFor();assert.equal(await page.locator('#provider-id').inputValue(),'unsaved-name');
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{providerEditor:{...window.amplifier.getState().view.providerEditor,advanced:true}}}));
 await page.locator('.a-path-field').getByRole('button',{name:'Browse',exact:true}).click();const locations=await pending('locations.list');
 state.locationListing={controlId:'provider-source',path:'/fixture',parent:'/',entries:[{name:'Old folder',path:'/fixture/old',directory:true}]};state.actionStatus={'locations.list':{phase:'ready',target:{controlId:'provider-source'}}};state.revision++;
 await locations.route.fulfill({json:{accepted:true,state}});await page.getByRole('button',{name:'Old folder',exact:true}).waitFor();
 await page.getByRole('button',{name:'Go',exact:true}).click();const nextLocations=await pending('locations.list');state.actionStatus['locations.list'].phase='working';state.revision++;await page.evaluate(value=>window.emitState(value),state);
 assert.equal(await page.getByRole('button',{name:'Old folder',exact:true}).count(),1);assert.equal(await page.locator('.a-location-picker').getAttribute('aria-busy'),'true');
 await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await page.locator('.a-location-picker').evaluate(el=>getComputedStyle(el).animationName),'none');
 await page.screenshot({path:'/tmp/amplifier-region-feedback.png',animations:'disabled'});
 await nextLocations.route.fulfill({status:500,json:{error:'Fixture failed'}});
 await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('.a-location-picker'));assert.equal(await page.locator('[role="dialog"]').count(),1);
 await page.mouse.click(2,2);await page.waitForFunction(()=>!document.querySelector('[role="dialog"]'));
 await page.getByRole('button',{name:'New chat',exact:true}).click();const create=await pending('session.draft');assert.equal(await page.getByRole('button',{name:'New chat',exact:true}).getAttribute('aria-busy'),'true');
 await page.getByRole('button',{name:'New chat',exact:true}).click();assert.equal(calls.filter(row=>row.action==='session.draft').length,1);
 await create.route.fulfill({status:500,json:{error:'Fixture rejected creation'}});await page.waitForFunction(()=>!document.querySelector('[data-action="session.draft"][data-action-pending]'));
 // Maintenance actions keep lifecycle feedback after their HTTP receipt.
 state.notificationSettings={server:'https://old.example',desktop:true};state.revision++;
 await page.evaluate(value=>window.emitState(value),state);
 await openSettingsPage(page,'notifications');
 await page.getByLabel(/^Topic/).fill('unsaved-private-topic');
 await page.getByRole('button',{name:'Load notification settings',exact:true}).click();const notification=await pending('notifications.get');
 state.actionStatus['notifications.get']={phase:'working',commandId:notification.body.id};state.revision++;
 await notification.route.fulfill({json:{accepted:true,state}});
 await page.waitForFunction(()=>!document.querySelector('[data-action="notifications.get"][data-action-pending]'));
 assert.equal(await page.locator('[data-action="notifications.get"]').getAttribute('aria-busy'),'true');
 assert.equal(await page.locator('[data-activity-region="notifications-preferences"]').getAttribute('aria-busy'),'true');
 assert.equal(await page.getByLabel('Notification server',{exact:true}).inputValue(),'https://old.example');
 await openSettingsPage(page,'voice');await openSettingsPage(page,'notifications');
 assert.equal(await page.getByLabel(/^Topic/).inputValue(),'unsaved-private-topic');
 state.actionStatus['notifications.get'].phase='ready';state.notificationSettings.server='https://new.example';state.revision++;
 await page.evaluate(value=>window.emitState(value),state);
 await page.waitForFunction(()=>!document.querySelector('[data-activity-region="notifications-preferences"][data-region-pending]'));
 assert.equal(await page.getByLabel('Notification server',{exact:true}).inputValue(),'https://new.example');
 await openSettingsPage(page,'repair');await page.getByRole('button',{name:'Create full backup',exact:true}).click();const backup=await pending('maintenance.backup');
 state.actionStatus['maintenance.backup']={phase:'working',commandId:backup.body.id};state.management={phase:'working',operation:'maintenance.backup'};state.revision++;
 await backup.route.fulfill({json:{accepted:true,state}});
 await page.waitForFunction(()=>!document.querySelector('[data-action="maintenance.backup"][data-action-pending]'));
 assert.equal(await page.getByRole('button',{name:'Create full backup',exact:true}).getAttribute('aria-busy'),'true');
 state.actionStatus['maintenance.backup'].phase='ready';state.management.phase='ready';state.maintenance={backup:'/fixture/private-backup.zip'};state.revision++;
 await page.evaluate(value=>window.emitState(value),state);await page.getByText('Saved backup: /fixture/private-backup.zip',{exact:true}).waitFor();
 assert.equal(await page.getByRole('button',{name:'Create full backup',exact:true}).getAttribute('aria-busy'),null);
 assert.deepEqual(errors,[]);console.log('Activity feedback browser passed: immediate button state, lifecycle refresh, retained options and drafts, outside dismissal, nested Escape, reduced motion, failure cleanup, duplicate suppression.');
}finally{await browser?.close();await vite?.close()}
