// Real main.jsx with synthetic HTTP/SSE; no provider, agent or backend execution.
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';
import {shellScope,sameScope} from '../src/shell/resources.js';
import './composer-test-helpers.mjs';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

const artifact=process.env.AMPLIFIER_TEST_ARTIFACTS||'/artifacts/shared-room';
const workspace='/fixture',outboxKey='amplifier.messageOutbox.v1';
const agents=[{id:'alpha',label:'Alpha'},{id:'beta',label:'Beta'},{id:'gamma',label:'Gamma'}];
const reports=[];let vite,browser;
function initialState(){
 return {revision:1,settings:{workspace},runtime:{available:true},
  view:{navPinned:true,workSurface:'chat',setupJourney:'done',newSessionDraft:{workspace,externalAgentIds:['alpha','beta']}},
  sessions:[{id:'previous',title:'Previous chat',sessionKind:'root',workspace,workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[]}],
  workspaces:[{id:'project',name:'Fixture',path:workspace,available:true}],selectedSessionId:null,selectedWorkspaceId:'project',
  setup:{providers:[{id:'fixture',module:'provider-fixture',config:{default_model:'fixture'}}],providersLoadedAt:1,providersWorkspace:workspace},canvas:{open:false}};
}
async function runCase(kind){
 const report={case:kind,passed:false,actions:[],errors:[],externalRequests:[]};reports.push(report);
 const context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage();
 let state=initialState(),deliveryMode='unknown';const shell=createShellHttpFixture(()=>state);
 const count=action=>report.actions.filter(row=>row.action===action).length;
 const outbox=()=>page.evaluate(key=>JSON.parse(sessionStorage.getItem(key)||'[]'),outboxKey);
 const reply=(route,extra={})=>{state.revision++;return route.fulfill({json:{accepted:true,state:shell.state(),...extra}})};
 const room=()=>state.sessions.find(row=>row.id==='shared');
 page.on('pageerror',error=>report.errors.push(error.message));
 await page.addInitScript(installFixtureEvents);
 try{
  const url=vite.resolvedUrls.local[0],origin=new URL(url).origin;
  await page.route('**/*',async route=>{
   const request=route.request(),address=new URL(request.url());
   if(address.origin!==origin){report.externalRequests.push(request.url());return route.abort('blockedbyclient')}
   if(!address.pathname.startsWith('/api/'))return route.continue();
   if(['/api/shell','/api/shell/resources'].includes(address.pathname)){
    const scope=address.pathname==='/api/shell/resources'?request.postDataJSON().scope:JSON.parse(address.searchParams.get('scope'));
    if(!sameScope(scope,shellScope(state)))return route.fulfill({status:409,json:{error:'Shell scope changed; request the current scope.'}});
   }
   const response=shell.response(request);
   if(response!==undefined)return route.fulfill({json:response});
   if(address.pathname==='/api/state')return route.fulfill({json:shell.state()});
   if(address.pathname==='/api/actions'&&request.method()==='GET')return route.fulfill({json:[]});
   if(address.pathname!=='/api/actions')return route.fulfill({json:{ok:true}});
   const raw=request.postDataJSON(),body=raw.action==='shell.command'?{...raw,action:raw.args.action,args:raw.args.args}:raw.action==='shell.view.update'?{...raw,action:'view.update'}:raw;
   report.actions.push(body);
   if(body.action==='externalAgents.list')return reply(route,{result:{agents}});
   if(body.action==='workspace.list')return reply(route,{result:{items:state.workspaces,nextOffset:null}});
   if(body.action==='session.create'){
    assert.deepEqual(body.args.externalAgentIds,['alpha','beta']);assert.equal(body.args.workspace,workspace);
    assert.equal(Object.hasOwn(body.args,'externalAgentId'),false);
    assert.equal(Object.hasOwn(body.args,'participation'),false,'The host chooses the mode for a new group');
    if(kind==='failed-creation'&&count('session.create')===1)return route.fulfill({status:409,json:{accepted:false,error:'Creation was refused before admission',code:'invalid_input'}});
    assert.equal(room(),undefined,'Only one room is admitted');
    state.sessions.push({id:'shared',title:'Shared room',creationCommandId:body.id,sessionKind:'root',workspace,workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[],externalAgent:{label:'Alpha and Beta',participation:'shared',participants:agents.slice(0,2).map(agent=>({...agent,status:'idle',sessionId:'remote-'+agent.id}))}});
    state.selectedSessionId='shared';return reply(route,{sessionId:'shared'});
   }
   if(body.action==='conversation.send'){
    assert.equal(body.args.sessionId,'shared');
    assert.equal(Object.hasOwn(body.args,'recipientAgentId'),false,'A room post must not capture the first agent');
    assert.equal(Object.hasOwn(body.args,'recipientAgentLabel'),false);
    if(count('conversation.send')===2)return route.fulfill({status:503,json:{error:'Synthetic receipt unavailable; admission unknown'}});
    assert.equal(count('conversation.send'),1,'No send may be retried implicitly');
    room().messages.push({id:'saved-first',inputId:body.id,role:'user',text:body.args.text,delivery:{status:'accepted'}});
    room().status='working';room().externalAgent.participants.forEach(agent=>{agent.status='working'});
    room().externalStreams={alpha:{id:'a-draft',text:'Alpha is checking',inputId:body.id},beta:{id:'b-draft',text:'Beta is checking',inputId:body.id}};
    return reply(route,{delivery:'accepted'});
   }
   if(body.action==='conversation.delivery')return reply(route,{result:{sessionId:body.args.sessionId,inputId:body.args.inputId,delivery:deliveryMode==='unknown'?'unknown':'accepted',source:{workspace,sessionId:deliveryMode==='wrong-room'?'another-room':'shared'},message:'Nothing was resent.'}});
   assert.ok(!['conversation.retry','worker.spawn','call.start','probe.spawn','runtime.start'].includes(body.action),'No execution or replay operation belongs in this fixture');
   if(body.action==='view.update')state.view={...state.view,...body.args.patch};
   if(body.action==='session.select')state.selectedSessionId=body.args.id;
   return reply(route);
  });
  await page.goto(url);
  const composer=page.getByRole('textbox',{name:'Message Amplifier'});
  await expect(composer).toBeVisible();await expect(page.getByRole('banner')).toContainText('New chat');
  await composer.fill('A room question for everyone');await page.getByRole('button',{name:'Send message',exact:true}).click();
  if(kind==='failed-creation'){
   await expect.poll(async()=>(await outbox())[0]?.status).toBe('failed');
   assert.equal(count('conversation.send'),0);
   const before=(await outbox())[0];assert.deepEqual(before.creation.setup.externalAgentIds,['alpha','beta']);
   await page.getByRole('combobox',{name:'Agent for new chat',exact:true}).selectOption('gamma');
   await expect.poll(()=>state.view.newSessionDraft.externalAgentIds).toEqual(['gamma','beta']);
   await page.getByRole('button',{name:'Retry',exact:true}).click();
   await expect.poll(()=>count('conversation.send')).toBe(1);
   const creates=report.actions.filter(row=>row.action==='session.create');
   assert.equal(creates.length,2);assert.notEqual(creates[0].id,creates[1].id);
   assert.deepEqual(creates[1].args.externalAgentIds,['alpha','beta']);
   report.capturedRosterRetained=true;
  }
  await expect(page.locator('[data-message-id="saved-first"]')).toContainText('A room question for everyone');
  await expect(page.getByRole('combobox',{name:'Message recipient'})).toHaveCount(0);
  await expect(page.getByRole('article',{name:'Alpha activity'})).toContainText('Alpha is checking');
  await expect(page.getByRole('article',{name:'Beta activity'})).toContainText('Beta is checking');
  await expect.poll(async()=>(await outbox()).length).toBe(0);
  if(kind==='busy-and-recovery'){
   assert.ok(room().externalAgent.participants.every(agent=>agent.status==='working'));
   await composer.fill('Beta, another thought while both of you work');
   await expect(page.getByRole('button',{name:'Send message',exact:true})).toBeEnabled();
   await page.getByRole('button',{name:'Send message',exact:true}).click();
   await expect.poll(async()=>(await outbox())[0]?.status).toBe('unknown');
   const held=(await outbox())[0],send=report.actions.filter(row=>row.action==='conversation.send')[1];
   assert.equal(held.commandId,send.id);assert.equal(held.sessionId,'shared');assert.equal(Object.hasOwn(held,'recipientAgentId'),false);
   await page.reload();await expect(composer).toBeVisible();
   await expect(page.getByRole('button',{name:'Check delivery',exact:true})).toBeVisible();
   await page.getByRole('button',{name:'Check delivery',exact:true}).click();
   await expect.poll(()=>count('conversation.delivery')).toBeGreaterThan(0);
   await expect.poll(async()=>(await outbox())[0]?.status).toBe('unknown');
   const retained=(await outbox())[0];assert.equal(retained.commandId,held.commandId);assert.equal(retained.text,held.text);assert.equal(Object.hasOwn(retained,'recipientAgentId'),false);
   deliveryMode='wrong-room';
   await page.getByRole('button',{name:'Check delivery',exact:true}).click();
   await expect(page.getByText('The delivery source could not be verified. Nothing was sent again.',{exact:true})).toBeVisible();
   assert.equal((await outbox())[0].commandId,held.commandId,'An accepted receipt from another room cannot retire this input');
   report.wrongRoomReceiptPreservedInput=true;
   deliveryMode='accepted';
   await page.getByRole('button',{name:'Check delivery',exact:true}).click();
   await expect.poll(async()=>(await outbox()).length).toBe(0);
   report.exactRoomReceiptSettledWithoutReplay=true;
   assert.equal(count('session.create'),1);assert.equal(count('conversation.send'),2);assert.equal(count('conversation.retry'),0);
   assert.ok(report.actions.filter(row=>row.action==='conversation.delivery').every(row=>row.args.inputId===held.commandId&&row.args.sessionId==='shared'));
   report.busyPostAndPassiveRecovery=true;
  }
  assert.deepEqual(report.errors,[]);assert.deepEqual(report.externalRequests,[]);
  await page.screenshot({path:`${artifact}/${kind}.png`});report.passed=true;
 }catch(error){report.failure=String(error.stack||error);try{await page.screenshot({path:`${artifact}/${kind}-failure.png`})}catch{}throw error}
 finally{await context.close()}
}
await mkdir(artifact,{recursive:true});
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 for(const kind of ['busy-and-recovery','failed-creation']){
  // Single-process Chromium can exit when its isolated context closes.
  // Give each independent case its own browser process as well as its context.
  browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS?{args:['--no-zygote','--single-process','--disable-gpu']}:{}),...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
  try{await runCase(kind)}finally{await browser.close();browser=null}
 }
 console.log('Shared room browser passed: busy room posts, passive unknown recovery and captured-roster creation retry. Synthetic HTTP/SSE only.');
}finally{
 await writeFile(`${artifact}/browser-result.json`,JSON.stringify({passed:reports.length===2&&reports.every(row=>row.passed),evidence:'Real main.jsx, controlled synthetic HTTP/SSE. No agent/model execution.',reports},null,2));
 if(browser)await browser.close();if(vite)await vite.close();
}
