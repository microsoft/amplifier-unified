// Actual frontend, controlled offline receipts; never sends user work to a model.
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';
import './composer-test-helpers.mjs';

let state={revision:1,settings:{workspace:'/existing'},runtime:{available:true},view:{navPinned:true,setupJourney:'done',newSessionDraft:{workspace:'/missing'}},
 sessions:[{id:'old',title:'Previous conversation',sessionKind:'root',workspace:'/existing',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[]}],
 workspaces:[{id:'project',name:'Existing',path:'/existing',available:true}],selectedSessionId:null,selectedWorkspaceId:'project',
 setup:{providers:[{id:'fixture',module:'provider-fixture',config:{model:'fixture-model'}}],providersLoadedAt:1,providersWorkspace:'/existing'},
 registeredBundles:[{value:'work',label:'Work'}],draftDefaults:Object.fromEntries(['/missing','/existing'].map(workspace=>[JSON.stringify([workspace,'']),{phase:'ready',bundle:'work',effective:{instance:'fixture',model:'fixture-model'},providers:[{id:'fixture'}]}])),canvas:{open:false}};
const calls=[],workers=[],folders=[],errors=[],handoffReads=[],handoffNavigations=[],externalRequests=[],heldStateReads=[];let browser,vite,page,holdHandoffNavigation=false,holdStateReads=false;
const shell=createShellHttpFixture(()=>state);
const draftScope=JSON.stringify([shell.state().client.historyScope||shell.state().client.hostInstanceId,null,null]);
const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
const reply=(route,extra={})=>{state.revision++;return route.fulfill({json:{accepted:true,state:shell.state(),...extra}})};
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS?{args:['--no-zygote','--single-process','--disable-gpu']}:{ }),...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});page=await browser.newPage({viewport:{width:1280,height:900}});page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(installFixtureEvents);
 await page.addInitScript(scope=>{
  // This is a failed submission from this known source, not a legacy unbound
  // row (which must remain retained and must not be assigned to the current host).
  if(sessionStorage.getItem('amplifier.messageOutbox.v1')===null)sessionStorage.setItem('amplifier.messageOutbox.v1',JSON.stringify([
   {id:'old-failed',commandId:'old-command',sessionId:null,scope,text:'Hi!',attachmentIds:[],status:'failed',error:'Old workspace unavailable',createdAt:1}]));
 },draftScope);
 await page.route('**/*',async route=>{
  const address=new URL(route.request().url()),path=address.pathname;
  if(address.origin!==new URL(vite.resolvedUrls.local[0]).origin){externalRequests.push(address.href);return route.abort('blockedbyclient')}
  if(!path.startsWith('/api/'))return route.continue();
  const shellResponse=shell.response(route.request());
  if(shellResponse!==undefined)return route.fulfill({json:shellResponse});
  if(path==='/api/state'){
   if(holdStateReads){heldStateReads.push(route);return}
   return route.fulfill({json:shell.state()});
  }
  if(path==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  if(path!=='/api/actions')return route.fulfill({json:{ok:true}});
  const original=route.request().postDataJSON();calls.push(original);
  const body=original.action==='shell.command'?{...original,action:original.args.action,args:original.args.args}:original.action==='shell.view.update'?{...original,action:'view.update'}:original;
  if(body.action==='session.handoff.read'){handoffReads.push({route,body});return}
  if(body.action==='session.select'){
   if(holdHandoffNavigation){handoffNavigations.push({route,body});return}
   state.selectedSessionId=body.args.id;
  }
  if(body.action==='view.update')state.view={...state.view,...body.args.patch};
  if(body.action==='session.draft'){state.selectedSessionId=null;state.view.draft='';}
  if(body.action==='workspace.list')return reply(route,{result:{items:state.workspaces,nextOffset:null}});
  if(body.action==='session.create'){
   if(body.args.workspace!=='/existing')return route.fulfill({status:409,json:{accepted:false,error:'Workspace folder does not exist: /missing'}});
   const chat={...state.sessions[0],id:'created',runtimeSessionId:'created-native',title:'Created conversation',messages:[],creationCommandId:body.id};
   state.sessions.unshift(chat);state.selectedSessionId=chat.id;delete state.view.newSessionDraft;
   // Creation returns a selected canonical record with workspace/native identity,
   // not only an ID that cannot bind the first submitted payload.
   return reply(route,{sessionId:chat.id});
  }
  if(body.action==='conversation.send'){
   state.sessions.find(row=>row.id===body.args.sessionId).messages.push({id:'received',inputId:body.id,role:'user',text:body.args.text,delivery:{status:'accepted'}});
  }
  if(body.action==='worker.spawn'){workers.push({route,body});return;}
  if(body.action==='locations.list'){
   state.locationListing={controlId:body.args.controlId,path:body.args.path||'/existing',parent:'/',entries:[]};
   state.actionStatus={'locations.list':{phase:'ready',commandId:body.id,target:{controlId:body.args.controlId}}};
  }
  if(body.action==='locations.create'){folders.push({route,body});return;}
  return reply(route);
 });
 await page.goto(vite.resolvedUrls.local[0]);const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.waitFor();
 await expect(page.getByRole('banner')).toContainText('New chat');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),null);
 assert.equal(await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1'))[0].scope),draftScope);
 await expect(page.getByText('Old workspace unavailable',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Discard unsent message',exact:true}).click();
 await expect(page.getByText('Hi!',{exact:true})).toHaveCount(0);await page.reload();await composer.waitFor();
 await expect(page.getByText('Hi!',{exact:true})).toHaveCount(0);
 assert.equal(calls.filter(row=>['conversation.send','session.create'].includes(row.action)).length,0,'Discard is local only');
 await composer.fill('Start fresh');await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(page.getByText('Workspace folder does not exist: /missing',{exact:true})).toBeVisible();
 await expect(page.getByRole('banner')).toContainText('New chat');
 await page.getByRole('button',{name:'Choose workspace',exact:true}).click();
 await page.getByRole('button',{name:'Existing · /existing',exact:true}).click();
 await expect(page.getByRole('dialog',{name:'Choose a workspace',exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'Retry',exact:true}).click();
 await expect(page.getByText('Start fresh',{exact:true})).toHaveCount(1);
 await expect(page.getByRole('banner')).toContainText('Created conversation');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),'created');
 await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')).length===0);
 assert.equal(calls.filter(row=>row.action==='conversation.send').length,1);
 assert.deepEqual(calls.filter(row=>row.action==='session.create').map(row=>row.args.workspace),['/missing','/existing']);
 const creations=calls.filter(row=>row.action==='session.create');
 assert.notEqual(creations[0].id,creations[1].id,'A definite rejection retries the corrected setup under a new command ID');
 assert.equal(calls.find(row=>row.action==='conversation.send').args.sessionId,'created');
 await action('view.update',{patch:{panel:'worker'}});
 const worker=page.getByLabel('Work to delegate');await worker.fill('First worker request');await page.getByRole('button',{name:'Start worker',exact:true}).click();
 await expect.poll(()=>workers.length).toBe(1);await worker.fill('A newer worker draft');await reply(workers.shift().route);
 await expect(worker).toHaveValue('A newer worker draft');await expect(page.getByRole('dialog')).toBeVisible();
 await page.getByRole('button',{name:'Start worker',exact:true}).click();await expect.poll(()=>workers.length).toBe(1);
 await workers.shift().route.fulfill({status:409,json:{accepted:false,error:'Worker rejected for fixture'}});
 await expect(worker).toHaveValue('A newer worker draft');
 await page.getByRole('dialog').getByRole('button',{name:'Dismiss error',exact:true}).click();
 await expect(page.getByText('Worker rejected for fixture',{exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'Start worker',exact:true}).click();await expect.poll(()=>workers.length).toBe(1);await reply(workers.shift().route);
 await expect(page.getByRole('dialog')).toHaveCount(0);assert.equal(state.view.workerDraft,'');
 await page.reload();await composer.waitFor();await action('view.update',{patch:{panel:'worker'}});await expect(worker).toHaveValue('');
 await worker.fill('Submitted worker instruction');await page.getByRole('button',{name:'Start worker',exact:true}).click();
 await expect.poll(()=>workers.length).toBe(1);const sharedWorker=workers.shift();
 assert.equal(sharedWorker.body.args.instruction,'Submitted worker instruction');
 // An agent's shared-view update arrives with the delayed submission receipt.
 state.view={...state.view,workerDraft:'A newer shared worker draft'};await reply(sharedWorker.route);
 await expect(worker).toHaveValue('A newer shared worker draft');await expect(page.getByRole('dialog')).toBeVisible();
 assert.equal(state.view.workerDraft,'A newer shared worker draft');
 await action('view.update',{patch:{panel:null}});await page.getByRole('button',{name:'New chat',exact:true}).click();
 await page.getByRole('button',{name:'Choose workspace',exact:true}).click();
 await page.getByRole('button',{name:'Use existing folder',exact:true}).click();
 await page.getByRole('form',{name:'Use existing folder',exact:true}).getByRole('button',{name:'Browse',exact:true}).click();
 await page.getByRole('button',{name:'New folder',exact:true}).click();
 const folder=page.getByRole('textbox',{name:'New folder name',exact:true});
 await folder.fill('first-folder');await page.getByRole('button',{name:'Create folder',exact:true}).click();
 await expect.poll(()=>folders.length).toBe(1);await expect(folder).toBeDisabled();
 await folders.shift().route.fulfill({status:409,json:{accepted:false,error:'Folder denied for fixture'}});
 await expect(folder).toBeEnabled();await expect(folder).toHaveValue('first-folder');
 await expect(page.getByText('Folder denied for fixture',{exact:true})).toBeVisible();
 await folder.fill('created-folder');await page.getByRole('button',{name:'Create folder',exact:true}).click();
 await expect.poll(()=>folders.length).toBe(1);const creation=folders.shift();
 assert.equal(creation.body.args.name,'created-folder');
 const createdPath=creation.body.args.path+'/created-folder';
 state.locationListing={controlId:creation.body.args.controlId,path:createdPath,parent:creation.body.args.path,entries:[],createdBy:creation.body.id};
 state.actionStatus['locations.create']={phase:'ready',commandId:creation.body.id};
 await reply(creation.route,{operationId:creation.body.id});
 await expect(folder).toHaveCount(0);
 await expect(page.getByRole('textbox',{name:'Folder path',exact:true})).toHaveValue(createdPath);
 await page.getByRole('button',{name:'Use this folder',exact:true}).click();
 await expect(page.getByRole('textbox',{name:/Folder on/})).toHaveValue(createdPath);
 assert.equal(calls.filter(row=>row.action==='conversation.send').length,1,'Folder selection does not send a new message');
 // Saved handoff receipts use the same real client and synthetic peer. No login,
 // runtime, live-control, or model actions are needed to inspect their delivery.
 const baseline={requestId:'past-0',sourceSessionId:'old',workspace:'/existing',bundle:'work',
  title:'Accepted chat 0',text:'Exact saved opening brief. <script>plain text only</script>',selection:{},
  status:'accepted',creation:'created',delivery:'accepted',revision:3,targetSessionId:'created',
  message:'Initial brief accepted. This does not mean its requested work has completed.'};
 const acceptedRows=Array.from({length:6},(_,index)=>({...baseline,requestId:`past-${index}`,title:index===5?'LongTitle'.repeat(24):`Accepted chat ${index}`}));
 state.handoffs=[...acceptedRows,
  {...baseline,requestId:'cancelled',title:'Cancelled proposal',status:'cancelled',creation:'not_started',delivery:'not_started',targetSessionId:null,message:'No chat was created.'},
  ...['pending','unknown','failed','creating','created','delivering','future-status'].map(status=>({...baseline,
   requestId:status,title:`Needs attention: ${status}`,status,delivery:status==='pending'?'not_started':'unknown',
   revision:2,message:`Visible ${status} delivery`,...(status==='pending'?{creation:'not_started',targetSessionId:null,reviewHash:'exact-review'}:{})})),
  {...baseline,requestId:'foreign',sourceSessionId:'another-source',title:'Private other-source receipt'},
 ];
 state.selectedSessionId='old';state.view={...state.view,panel:null,navPinned:false,draft:'Keep this source draft'};
 delete state.view.newSessionDraft;
 state.sessions.find(row=>row.id==='old').messages=[{id:'saved-answer',role:'assistant',text:'Saved conversation answer.',displayOnly:true}];
 const startHandoffCalls=calls.length;
 await page.reload();await expect(composer).toHaveDraft('Keep this source draft');
 const handoffs=page.getByRole('region',{name:'New chat requests',exact:true});
 const past=handoffs.locator('details.a-handoffs-past'),summary=past.locator(':scope > summary');
 const receipt=id=>handoffs.locator(`[data-handoff-id="${id}"]`);
 const emit=async()=>{
  const revision=++state.revision;
  await page.evaluate(frame=>window.emitState(frame),shell.state());
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().revision)).toBeGreaterThanOrEqual(revision);
 };
 const screenshot=async name=>{
  if(!process.env.AMPLIFIER_TEST_ARTIFACTS)return;
  await mkdir(process.env.AMPLIFIER_TEST_ARTIFACTS,{recursive:true});
  await page.screenshot({path:`${process.env.AMPLIFIER_TEST_ARTIFACTS}/handoffs-${name}.png`});
 };
 await expect(page.getByText('Saved conversation answer.',{exact:true})).toBeVisible();
 await expect(past).toHaveCount(1);await expect(summary).toHaveText('Past handoffs (7)');
 await expect(past).not.toHaveAttribute('open','');
 await expect(receipt('past-0')).not.toBeVisible();await expect(receipt('foreign')).toHaveCount(0);
 for(const status of ['pending','unknown','failed','creating','created','delivering','future-status']){
  await expect(handoffs.locator(`:scope > [data-handoff-id="${status}"]`)).toBeVisible();
 }
 await expect(receipt('pending').getByRole('button',{name:'Create and start',exact:true})).toBeVisible();
 await expect(receipt('unknown').getByRole('button',{name:'Check delivery',exact:true})).toBeVisible();
 await expect(handoffs.getByRole('button',{name:'Check status',exact:true})).toHaveCount(0);
 for(const width of [1280,320]){
  await page.setViewportSize({width,height:900});
  await summary.scrollIntoViewIfNeeded();
  assert.ok((await past.boundingBox()).height<=64,'Settled receipts occupy one compact collapsed line, not a stack of cards');
  await screenshot(`${width}-collapsed`);
  await summary.focus();await page.keyboard.press('Enter');
  await expect(past).toHaveAttribute('open','');
  await expect(receipt('past-0').getByText('Opening brief accepted',{exact:true})).toBeVisible();
  await expect(receipt('cancelled').getByText('Cancelled',{exact:true})).toBeVisible();
  await expect(past.getByText('Opening brief acceptance is not work completion.',{exact:true})).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(receipt('past-0').getByRole('button',{name:'Open chat',exact:true})).toBeFocused();
  const detail=receipt('past-5').locator(':scope > details');
  if(!await detail.evaluate(node=>node.open)){await detail.locator('summary').focus();await page.keyboard.press('Space')}
  await expect(detail).toHaveAttribute('open','');
  await expect(detail.locator('pre')).toBeVisible();
  await expect(detail.locator('pre')).toHaveText(baseline.text);
  await expect(handoffs.locator('script')).toHaveCount(0);
  const layout=await handoffs.evaluate(element=>({
   width:element.clientWidth,scroll:element.scrollWidth,
   page:document.documentElement.scrollWidth,viewport:window.innerWidth,
  }));
  assert.ok(layout.scroll<=layout.width+1,`Handoffs overflow at ${width}px: ${JSON.stringify(layout)}`);
  assert.ok(layout.page<=layout.viewport+1,`Page overflows at ${width}px: ${JSON.stringify(layout)}`);
  await screenshot(`${width}-expanded`);
  await summary.focus();await page.keyboard.press('Space');await expect(past).not.toHaveAttribute('open','');
  await expect(summary).toBeFocused();
 }
 assert.equal(handoffReads.length,0,'Rendering, disclosure and resize do not poll delivery');
 await expect(composer).toHaveDraft('Keep this source draft');
 // A compact exact read with a future stateRevision must settle without the
 // stream or awaitState's 1500ms recovery GET. Hold any GET so that recovery
 // cannot accidentally make this pass while the authoritative row stays older.
 holdStateReads=true;
 await receipt('unknown').getByRole('button',{name:'Check delivery',exact:true}).click();
 await expect.poll(()=>handoffReads.length).toBe(1);
 const localRead=handoffReads.shift();assert.equal(localRead.body.args.requestId,'unknown');
 const readStateRevision=state.revision+1;
 await localRead.route.fulfill({json:{accepted:true,hostInstanceId:shell.state().client.hostInstanceId,
  stateRevision:readStateRevision,result:{...baseline,requestId:'unknown'}}});
 await expect(summary).toHaveText('Past handoffs (8)');
 await expect(receipt('unknown')).not.toBeVisible();await expect(summary).toBeFocused();
 const afterRead=await page.evaluate(()=>window.amplifier.getState());
 assert.ok(afterRead.revision<readStateRevision,'Exact read does not advance the authoritative stream baseline');
 assert.equal(afterRead.handoffs.find(row=>row.requestId==='unknown').status,'unknown');
 assert.equal(state.handoffs.find(row=>row.requestId==='unknown').status,'unknown','Only the returned local receipt changed');
 assert.equal(heldStateReads.length,0,'Handoff read must not wait for or recover an unrelated state frame');
 holdStateReads=false;
 // A newer stream revision cannot be overwritten by an older delayed read.
 const unknownIndex=state.handoffs.findIndex(row=>row.requestId==='unknown');
 state.handoffs[unknownIndex]={...state.handoffs[unknownIndex],revision:4};await emit();
 await expect(receipt('unknown')).toBeVisible();
 await receipt('unknown').getByRole('button',{name:'Check delivery',exact:true}).click();
 await expect.poll(()=>handoffReads.length).toBe(1);
 state.handoffs[unknownIndex]={...state.handoffs[unknownIndex],revision:6,message:'Newer stream still cannot confirm delivery.'};await emit();
 await handoffReads.shift().route.fulfill({json:{accepted:true,result:{...baseline,requestId:'unknown',revision:5}}});
 await expect(receipt('unknown')).toContainText('Newer stream still cannot confirm delivery.');
 await expect(summary).toHaveText('Past handoffs (7)');
 await expect(receipt('unknown').getByRole('button',{name:'Check delivery',exact:true})).toBeEnabled();
 // An error after a streamed acceptance remains visible outside the disclosure.
 await receipt('unknown').getByRole('button',{name:'Check delivery',exact:true}).click();
 await expect.poll(()=>handoffReads.length).toBe(1);
 state.handoffs[unknownIndex]={...baseline,requestId:'unknown',revision:7};await emit();
 await handoffReads.shift().route.fulfill({status:503,json:{accepted:false,error:'Delivery read unavailable'}});
 await expect(handoffs.locator(':scope > [data-handoff-id="unknown"]').getByRole('alert')).toContainText('Delivery read unavailable');
 await expect(summary).toHaveText('Past handoffs (7)');await screenshot('read-error-visible');
 await expect(composer).toHaveDraft('Keep this source draft');
 // Moving focus away during a read supersedes the component's focus restoration.
 await receipt('unknown').getByRole('button',{name:'Check delivery',exact:true}).click();
 await expect.poll(()=>handoffReads.length).toBe(1);
 await composer.focus();await expect(composer).toBeFocused();
 await handoffReads.shift().route.fulfill({json:{accepted:true,result:{...baseline,requestId:'unknown',revision:7}}});
 await expect(summary).toHaveText('Past handoffs (8)');
 await expect(receipt('unknown')).not.toBeVisible();
 await expect(composer).toBeFocused();await expect(composer).toHaveDraft('Keep this source draft');
 const handoffActions=()=>calls.slice(startHandoffCalls).map(raw=>raw.action==='shell.command'?{...raw,action:raw.args.action,args:raw.args.args}:raw);
 assert.deepEqual(handoffActions().filter(row=>row.action.startsWith('session.handoff.')).map(row=>row.action),
  ['session.handoff.read','session.handoff.read','session.handoff.read','session.handoff.read']);
 assert.equal(handoffActions().filter(row=>/^(conversation\.(send|retry)|session\.(create|select)|worker\.spawn)$/.test(row.action)).length,0);
 // The target is already in the real client's session list: optimistic selection
 // would unmount the source card before these delayed failures could show.
 await page.setViewportSize({width:1280,height:900});
 await summary.focus();await page.keyboard.press('Enter');
 assert.ok((await page.evaluate(()=>window.amplifier.getState())).sessions.some(row=>row.id==='created'));
 const navigationCount=()=>handoffActions().filter(row=>row.action==='session.select').length;
 holdHandoffNavigation=true;
 for(const [id,outcome] of [['past-0','rejected'],['past-1','unknown']]){
  const openChat=receipt(id).getByRole('button',{name:'Open chat',exact:true}),before=navigationCount();
  await openChat.click();await expect.poll(()=>handoffNavigations.length).toBe(1);
  await expect(page.getByRole('banner')).toContainText('Previous conversation');
  await expect(handoffs).toBeVisible();await expect(openChat).toBeDisabled();
  await expect(composer).toHaveDraft('Keep this source draft');
  assert.equal((await page.evaluate(()=>window.amplifier.getState())).selectedSessionId,'old');
  await openChat.evaluate(node=>node.click());await emit();
  assert.equal(navigationCount(),before+1,'Reentry while navigation is pending must not post again');
  const pending=handoffNavigations.shift();assert.deepEqual(pending.body.args,{id:'created'});
  if(outcome==='rejected')await pending.route.fulfill({status:409,json:{accepted:false,error:'Navigation blocked for fixture'}});
  else await pending.route.abort('failed');
  const prominent=handoffs.locator(`:scope > [data-handoff-id="${id}"]`);
  await expect(prominent.getByRole('alert')).toBeVisible();
  if(outcome==='rejected')await expect(prominent.getByRole('alert')).toContainText('Navigation blocked for fixture');
  await expect(prominent.getByRole('button',{name:'Open chat',exact:true})).toBeEnabled();
  await expect(prominent.locator(':scope > details > summary')).toBeFocused();
  await emit();
  await expect(prominent.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('banner')).toContainText('Previous conversation');
  await expect(composer).toHaveDraft('Keep this source draft');
  assert.equal(navigationCount(),before+1,'Failure/unknown never automatically reopens a chat');
  await screenshot(`navigation-${outcome}-visible`);
 }
 holdHandoffNavigation=false;
 // Use the real dirty-settings registry, prompt and local accepted:false path,
 // with a private synthetic key draft beside the source chat. Never save it.
 state.canvas={id:'handoff-settings',kind:'settings',title:'Settings',open:true};
 state.voiceConfiguration={source:'private',available:false,environmentAvailable:false};
 state.view={...state.view,settingsExpanded:['voice'],canvasFocused:false,canvasControlsExpanded:false};
 await emit();
 const settings=page.locator('.a-settings-canvas'),voiceKey=settings.locator('#voice-api-key');
 await expect(voiceKey).toBeVisible();await voiceKey.fill('synthetic-unsaved-key');
 await expect(settings.getByText('You have unsaved settings changes. They are kept while Settings stays open.',{exact:true})).toBeVisible();
 const beforeGuard=navigationCount(),guardOpen=receipt('past-2').getByRole('button',{name:'Open chat',exact:true});
 await guardOpen.click();
 const guard=page.getByRole('alertdialog',{name:'Unsaved settings changes',exact:true});
 await expect(guard).toBeVisible();await expect(guardOpen).toBeDisabled();
 await guardOpen.evaluate(node=>node.click());
 await guard.getByRole('button',{name:'Keep settings open',exact:true}).click();
 await expect(guard).toHaveCount(0);
 await expect(handoffs.locator(':scope > [data-handoff-id="past-2"]').getByRole('alert')).toHaveText('Settings changes were kept.');
 await expect(voiceKey).toHaveValue('synthetic-unsaved-key');
 await expect(composer).toHaveDraft('Keep this source draft');
 assert.equal(navigationCount(),beforeGuard,'Declining the actual dirty guard sends no selection');
 assert.equal(handoffActions().filter(row=>row.action==='voice.configure').length,0,'Private fixture draft is never submitted');
 await screenshot('navigation-guard-declined');
 await voiceKey.fill('');
 await expect(settings.getByText('You have unsaved settings changes. They are kept while Settings stays open.',{exact:true})).toHaveCount(0);
 state.canvas={open:false};await emit();
 // A fresh explicit click may navigate after failure; no retry was automatic.
 await receipt('past-0').getByRole('button',{name:'Open chat',exact:true}).click();
 await expect(page.getByRole('banner')).toContainText('Created conversation');
 assert.deepEqual(handoffActions().filter(row=>row.action==='session.select').map(row=>row.args),[{id:'created'},{id:'created'},{id:'created'}]);
 assert.equal(handoffActions().filter(row=>/^(conversation\.(send|retry)|session\.create)$/.test(row.action)).length,0);
 assert.deepEqual(externalRequests,[]);
 assert.deepEqual(errors,[]);
 console.log('New chat recovery passed: truthful new-chat header; scoped persisted failed-message discard; inline creation error; corrected-path retry with canonical created-chat identity sends once; worker success persists reset; failure/local and shared newer drafts retained; modal error dismissal; compact past handoffs and visible details at 320/1280px; native disclosure and moved-away focus; local delivery regroup; stale read ordering; known-target delayed rejection/unknown and actual dirty-guard decline keep errors and drafts; explicit navigation only. Zero model calls.');
}catch(error){console.error('Recovery fixture evidence:',JSON.stringify({errors,body:await page?.locator('body').innerText().catch(()=>''),actions:calls.map(row=>({action:row.action,id:row.id}))}));throw error}
finally{await browser?.close();await vite?.close()}
