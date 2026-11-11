import {readComposerDraft} from './composer-test-helpers.mjs';
// Real React UI, delayed/rejected shared actions, and synthetic state only.
import {createServer} from 'vite';
import {openSettingsDialog} from './browser-settings.mjs';
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';
import {chromium} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

// This is an already configured chat; first-run connection setup has its own journey.
let state={revision:1,settings:{workspace:'/fixture',bundle:'anchors'},runtime:{available:true},view:{navPinned:true},sessions:[{id:'chat',sessionKind:'root',title:'Saved chat',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[{id:'answer',role:'assistant',text:'Saved answer',createdAt:1}]}],workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'chat',selectedWorkspaceId:'project',setup:{providers:[{id:'fixture',module:'provider-fixture',enabled:true,config:{default_model:'fixture-model'}}],providersLoadedAt:1,providersWorkspace:'/fixture'},canvas:{open:false}};
const waiting=[],calls=[],errors=[],copyResults=[];let browser,vite,page,stage='startup',baseline,failure;
const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS||fileURLToPath(new URL('../output/pending-view',import.meta.url));
// Only bounded fixture metadata is retained, never the entire app snapshot.
const actionSummary=({action,args={}})=>({action,patchKeys:Object.keys(args.patch||{}),panel:args.patch?.panel,scope:args.scope});
const browserSummary=()=>{
 const state=window.amplifier?.getState(),root=document.querySelector('#amp-one');
 return {revision:state?.revision,selectedSessionId:state?.selectedSessionId,panel:state?.view?.panel,
  draftLength:state?.view?.draft?.length,connection:root?.dataset.appConnection,hidden:document.hidden,
  events:window.fixtureEventState?.(),permission:window.Notification?.permission,
  notificationSettings:state?.notificationSettings,
  noticeIds:state?.notificationMessages?.slice(-12).map(row=>row.id),
  notificationCount:window.fixtureNotifications?.length,copyCount:window.fixtureCopies?.length,
  serviceWorker:window.fixtureServiceWorker,
  settingsPage:document.querySelector('.a-settings-experience')?.dataset.settingsPage};
};
state.notificationMessages=[{id:'old-notice',sessionId:'off-page-chat',role:'assistant',via:'text',text:'Previously finished'}];
const shell=createShellHttpFixture(()=>state);
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function nextAction(){for(let i=0;i<100&&!waiting.length;i++)await sleep(10);assert.ok(waiting.length,'Expected a queued shared action');return waiting.shift()}
async function finish({route,action,args},error,effects=[]){
 if(error)return route.fulfill({status:409,json:{accepted:false,error}});
 assert.ok(['view.update','conversation.send','providers.save'].includes(action));state={...state,revision:state.revision+1,view:{...state.view,...(args.patch||{})}};
 await route.fulfill({json:{accepted:true,state:shell.state(),effects}});
}
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});page=await browser.newPage({viewport:{width:1280,height:900}});page.on('pageerror',error=>errors.push(error.message.slice(0,500)));
 await page.addInitScript(installFixtureEvents);
 await page.addInitScript(()=>{
  window.fixtureOpens=[];window.open=url=>window.fixtureOpens.push(url);
  window.fixtureCopies=[];Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>window.fixtureCopies.push(text)},configurable:true});
  window.fixtureNotifications=[];window.Notification=class{static permission='granted';constructor(title,options){this.title=title;this.options=options;window.fixtureNotifications.push(this)}};
  // This journey observes constructor delivery, not installed-worker delivery.
  // Match input-status-browser's platform isolation; PWA registration must not
  // divert a notification away from the synthetic constructor counter.
  window.fixtureServiceWorker={registerCalls:0,registrationReads:0,active:false};
  Object.defineProperty(navigator,'serviceWorker',{value:{
   register:async()=>{window.fixtureServiceWorker.registerCalls++;throw Error('fixture does not register a worker')},
   getRegistration:async()=>{window.fixtureServiceWorker.registrationReads++;return undefined},
  }});
 });
 await page.route('**/api/**',route=>{
  const path=new URL(route.request().url()).pathname;
  const shellResponse=shell.response(route.request());
  if(shellResponse!==undefined)return route.fulfill({json:shellResponse});
  if(path==='/api/state'){const target=new URL(route.request().url()).searchParams.get('sessionId'),current=shell.state();return route.fulfill({json:target==='off-page-copy'?{...current,sessions:[...current.sessions,{id:target,messages:[{id:'off-page-answer',role:'assistant',text:'**Saved answer to copy**'}]}]}:current})}
  if(path==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  // Shell reports and automatic settings reads are outside this controlled queue fixture.
  if(path==='/api/actions'){
   const body=route.request().postDataJSON();
   if(body.action==='theme.list')return route.fulfill({json:{accepted:true,result:{items:[],errors:[]}}});
   if(['modules.diagnostics','providers.decisionModels'].includes(body.action)){
    // These Settings mount reads belong to global configuration, not the delayed editor queue.
    assert.deepEqual(body.args,{scope:'global',configurationTarget:{kind:'global'}});
    const diagnostic=body.action==='modules.diagnostics',owner=diagnostic?'registry':'setup';
    const result=diagnostic?{diagnostics:{scope:'global',items:[],takesEffect:'new_sessions'}}
     :{decisionModels:{catalog:[],services:[],backends:{},selection:{}},decisionModelsError:null};
    state={...state,revision:state.revision+1,[owner]:{...state[owner],...result,configurationTarget:body.args.configurationTarget}};
    return route.fulfill({json:{accepted:true,result,state:shell.state()}});
   }
   if(['shell.report','shell.view.update','providers.list','routing.list','configuration.defaults'].includes(body.action))return route.fulfill({json:{accepted:true,result:{}}});
   if(body.action==='message.copyResult'){copyResults.push(body.args);return route.fulfill({json:{accepted:true}})}
   calls.push(body);waiting.push({route,...body});return;
  }
  return route.fulfill({json:{ok:true}});
 });
 stage='connected historical baseline';
 await page.goto(vite.resolvedUrls.local[0]);
 await page.locator('#amp-one[data-app-connection=connected]').waitFor();
 await page.waitForFunction(()=>{
  const state=window.amplifier?.getState(),events=window.fixtureEventState();
  return events.opened>0&&events.frames>0&&events.ready.includes(1)&&
   state?.notificationMessages?.some(row=>row.id==='old-notice');
 });
 baseline=await page.evaluate(browserSummary);
 assert.equal(await page.evaluate(()=>window.fixtureNotifications.length),0,'Initial historical notices are not replayed');
 stage='fresh off-page notification';
 state={...state,revision:state.revision+1,notificationMessages:[...state.notificationMessages,{id:'new-notice',sessionId:'off-page-chat',role:'assistant',via:'text',text:'Just finished'}]};
 await page.evaluate(state=>window.emitFixtureState(state),shell.state());await page.waitForFunction(()=>window.fixtureNotifications.length===1);
 await page.evaluate(state=>window.emitFixtureState(state),shell.state());
 assert.equal(await page.evaluate(()=>window.fixtureNotifications.length),1);
 assert.equal(await page.evaluate(()=>window.fixtureNotifications[0].options.body),'Your Amplifier response is ready.');
 stage='older history does not notify';
 state={...state,revision:state.revision+1,sessions:[...state.sessions,{id:'older-chat',status:'working',messages:[{id:'older-loaded-answer',role:'assistant',via:'text',text:'An older reply now materialized'}]}]};
 await page.evaluate(state=>window.emitFixtureState(state),shell.state());await sleep(50);
 assert.equal(await page.evaluate(()=>window.fixtureNotifications.length),1,'Materializing older off-page history must not replay notices');
 stage='scoped off-page copy';
 state={...state,revision:state.revision+1,deviceCommands:[{id:'copy-effect',type:'message.copy',sessionId:'off-page-copy',messageId:'off-page-answer',requestId:'copy-request'}]};
 await page.evaluate(state=>window.emitFixtureState(state),shell.state());
 await page.waitForFunction(()=>window.fixtureCopies.length===1);
 assert.equal(await page.evaluate(()=>window.fixtureCopies[0]),'**Saved answer to copy**');
 for(let i=0;i<100&&!copyResults.length;i++)await sleep(10);
 assert.equal(copyResults[0]?.status,'ready');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),'chat','Scoped copy reads do not change UI selection');

 stage='draft overlay and save ordering';
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('First typed draft');
 state={...state,revision:state.revision+1,view:{...state.view,draft:'Older shared draft'}};
 await page.evaluate(state=>window.emitFixtureState(state),shell.state());
 assert.equal(await readComposerDraft(composer),'First typed draft','SSE cannot erase the pre-debounce local draft');
 const firstDraft=await nextAction();assert.equal(firstDraft.action,'view.update',JSON.stringify({action:firstDraft.action,args:firstDraft.args}));assert.equal(firstDraft.args.patch.draft,'First typed draft');
 await composer.fill('Newer typed draft');
 state={...state,revision:state.revision+1};await page.evaluate(state=>window.emitFixtureState(state),shell.state());
 assert.equal(await readComposerDraft(composer),'Newer typed draft');
 await finish(firstDraft);assert.equal(await readComposerDraft(composer),'Newer typed draft','An older save cannot erase newer typing');
 const secondDraft=await nextAction();assert.equal(secondDraft.args.patch.draft,'Newer typed draft');await finish(secondDraft);
 stage='send before debounce';
 await composer.fill('Submitted before debounce');await page.getByRole('button',{name:'Send message',exact:true}).click();
 const capture=await nextAction();assert.equal(await readComposerDraft(composer),'');assert.equal(capture.args.patch.draft,'');await finish(capture);
 const send=await nextAction();assert.equal(send.action,'conversation.send');assert.equal(send.args.text,'Submitted before debounce');await finish(send);
 await page.waitForFunction(()=>window.amplifier.getState().view.draft==='');
 stage='optimistic settings navigation and close ordering';
 await openSettingsDialog(page);
 await page.getByRole('heading',{name:'Settings',exact:true}).waitFor();
 const opening=await nextAction();assert.equal(state.view.panel,undefined,'The settings UI paints before the server accepts navigation');
 await page.locator('[data-settings-section=updates]').click();
 await page.getByRole('heading',{name:'Updates',exact:true}).waitFor();
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await page.waitForFunction(()=>!document.querySelector('[role="dialog"]'));
 assert.equal(waiting.length,0,'Later actions retain their original serialized order');
 await finish(opening);const maintenance=await nextAction();assert.equal(maintenance.args.patch?.settingsSection,'maintenance',JSON.stringify(maintenance.args)+' '+maintenance.action);
 await page.waitForFunction(()=>!document.querySelector('[role="dialog"]'));
 await finish(maintenance);const closing=await nextAction();assert.equal(closing.args.patch.panel,null);await finish(closing);
 await page.waitForFunction(()=>window.amplifier.getState().view.panel===null);

 // A rejected older request cannot discard the newer agent-issued navigation.
 stage='rejected older navigation';
 await openSettingsDialog(page);const failing=await nextAction();
 await page.evaluate(()=>{window.laterNavigation=window.amplifier.dispatch('view.update',{patch:{panel:'appearance'}}).catch(error=>error.message)});
 await page.locator('.a-settings-page-heading').getByRole('heading',{name:'Appearance',exact:true}).waitFor();
 await finish(failing,'Fixture rejected older navigation');const appearance=await nextAction();
 await page.locator('.a-settings-page-heading').getByRole('heading',{name:'Appearance',exact:true}).waitFor();await finish(appearance);
 await page.waitForFunction(()=>window.amplifier.getState().view.panel==='appearance');

 // Shared updates continue under the optimistic overlay; rollback uses the latest
 // shared view and keeps genuine message/tool changes, rather than an old snapshot.
 stage='rollback onto latest shared state';
 await page.evaluate(()=>{window.failingNavigation=window.amplifier.dispatch('view.update',{patch:{panel:'settings'}}).catch(error=>error.message)});const pending=await nextAction();
 state={...state,revision:state.revision+1,view:{...state.view,panel:'activity'},sessions:[{...state.sessions[0],streaming:'A new streamed answer',status:'working'}]};
 await page.evaluate(state=>window.emitFixtureState(state),shell.state());
 await page.getByRole('heading',{name:'Settings',exact:true}).waitFor();
 await page.getByText('A new streamed answer',{exact:true}).waitFor();
 await finish(pending,'Fixture rejected latest navigation');await page.getByRole('heading',{name:'Ready for you',exact:true}).waitFor();
 await page.getByText('A new streamed answer',{exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.panel),'activity');
 // No preview was started: leaving Appearance must not add a cleanup mutation.
 assert.equal(waiting.length,0,'Inactive appearance preview creates no queued cleanup');

 // A response overtaken by a newer shared revision must settle its local patch
 // without reverting navigation that the server has changed since accepting it.
 stage='overtaken response navigation';
 await page.evaluate(()=>{window.outdatedNavigation=window.amplifier.dispatch('view.update',{patch:{panel:'settings'}}).catch(error=>error.message)});const outdated=await nextAction();
 assert.equal(outdated.action,'view.update');assert.deepEqual(outdated.args,{patch:{panel:'settings'}});
 state={...state,revision:state.revision+1,view:{...state.view,panel:'settings'}};
 const accepted=shell.state();
 state={...state,revision:accepted.revision+1,view:{...state.view,panel:'appearance'}};
 await page.evaluate(state=>window.emitFixtureState(state),shell.state());
 await page.getByRole('heading',{name:'Settings',exact:true}).waitFor();
 await outdated.route.fulfill({json:{accepted:true,state:accepted}});
 await page.locator('.a-settings-page-heading').getByRole('heading',{name:'Appearance',exact:true}).waitFor();
 // Agent and UI dispatch share the queue: a slow request must not accumulate
 // every intermediate editor keystroke, erase a newer draft, or cross a save.
 stage='editor coalescing and save barrier';
 await page.evaluate(()=>{window.initialEditor=window.amplifier.dispatch('view.update',{patch:{providerEditor:{id:'provider',model:'initial'}}})});
 const initialEditor=await nextAction();
 await page.evaluate(()=>{
  window.editorChanges=Array.from({length:12},(_,i)=>window.amplifier.dispatch('view.update',{patch:{providerEditor:{id:'provider',model:'model-'+i}}}));
  window.saveProvider=window.amplifier.dispatch('providers.save',{id:'provider',config:{default_model:'model-11'}});
  window.afterSave=window.amplifier.dispatch('view.update',{patch:{providerEditor:{id:'provider',model:'after-save'}}});
 });
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.providerEditor.model),'after-save');
 await finish(initialEditor);const latestEditor=await nextAction();
 assert.equal(latestEditor.args.patch.providerEditor.model,'model-11');
 await finish(latestEditor,null,[{type:'browser.open',url:'https://example.invalid/review'}]);const saveProvider=await nextAction();assert.equal(saveProvider.action,'providers.save');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.providerEditor.model),'after-save');
 await finish(saveProvider);const afterSave=await nextAction();assert.equal(afterSave.args.patch.providerEditor.model,'after-save');await finish(afterSave);
 await page.evaluate(()=>Promise.all([...window.editorChanges,window.initialEditor,window.saveProvider,window.afterSave]));
 assert.deepEqual(await page.evaluate(()=>window.fixtureOpens),['https://example.invalid/review'],'A shared response runs its effects once');
 state={...state,revision:state.revision+1,view:{...state.view,providerEditor:{id:'provider',model:'agent-choice'}}};
 await page.evaluate(state=>window.emitFixtureState(state),shell.state());
 await page.waitForFunction(()=>window.amplifier.getState().view.providerEditor.model==='agent-choice');
 assert.equal(calls.filter(call=>call.args?.patch?.providerEditor).length,3,'Only the in-flight, latest queued, and post-save snapshots are sent');
 // A remote catalog request waits for preceding edits, but never blocks a
 // subsequent save. A response from before that save cannot regress the view.
 stage='catalog reads independent of saves';
 await page.evaluate(()=>{
  window.beforeLookup=window.amplifier.dispatch('view.update',{patch:{providerEditor:{id:'provider',model:'before-lookup'}}});
  window.catalogLookup=window.amplifier.dispatch('providers.models',{id:'provider'});
 });
 const beforeLookup=await nextAction();assert.equal(beforeLookup.action,'view.update');
 assert.equal(waiting.length,0,'Lookup waits for preceding settings edits');
 await finish(beforeLookup);const catalogLookup=await nextAction();assert.equal(catalogLookup.action,'providers.models');
 const oldCatalogState=shell.state();
 await page.evaluate(()=>{window.duringLookup=window.amplifier.dispatch('providers.save',{id:'provider',config:{default_model:'new-model'}})});
 const duringLookup=await nextAction();assert.equal(duringLookup.action,'providers.save');await finish(duringLookup);
 await page.evaluate(()=>window.duringLookup);
 const savedRevision=state.revision;
 await catalogLookup.route.fulfill({json:{accepted:true,state:oldCatalogState}});
 await page.evaluate(()=>Promise.all([window.beforeLookup,window.catalogLookup]));
 assert.equal(await page.evaluate(()=>window.amplifier.getState().revision),savedRevision);
 assert.deepEqual(calls.filter(({action})=>!['view.update','conversation.send','providers.save','providers.models'].includes(action)),[]);assert.deepEqual(errors,[]);
 console.log('Pending view browser checks passed: draft/send ordering, navigation rollback, agent parity, coalesced settings drafts, and catalog reads independent of saves.');
}catch(error){
 failure=error;
 let snapshot;
 try{
  snapshot=await Promise.race([page?.evaluate(browserSummary),new Promise(resolve=>setTimeout(()=>resolve({unavailable:'diagnostic read exceeded 2s'}),2000))]);
 }catch(readError){snapshot={unavailable:String(readError).slice(0,500)}}
 const diagnostic={stage,error:String(error).slice(0,1500),fixtureRevision:state.revision,baseline,snapshot,
  callCount:calls.length,calls:calls.slice(-20).map(actionSummary),waiting:waiting.slice(-20).map(actionSummary),
  copyResultCount:copyResults.length,pageErrors:errors.slice(-10)};
 try{await mkdir(artifacts,{recursive:true});await writeFile(artifacts+'/failure.json',JSON.stringify(diagnostic,null,2)+'\n')}
 catch(writeError){console.error('Could not retain pending-view diagnostics:',String(writeError).slice(0,500))}
 console.error('Pending view failure:',JSON.stringify(diagnostic));
 throw error;
}finally{
 const cleanup=await Promise.allSettled([browser?.close(),vite?.close()]);
 for(const result of cleanup)if(result.status==='rejected'){
  if(!failure)throw result.reason;
  console.error('Cleanup after pending-view failure:',String(result.reason).slice(0,500));
 }
}
