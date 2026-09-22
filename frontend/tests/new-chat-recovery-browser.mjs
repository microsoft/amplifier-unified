// Actual frontend, controlled offline receipts; never sends user work to a model.
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {shellFor} from './shell-host.mjs';

let state={revision:1,settings:{workspace:'/existing'},runtime:{available:true},view:{navPinned:true,newSessionDraft:{workspace:'/missing'}},
 sessions:[{id:'old',title:'Previous conversation',sessionKind:'root',workspace:'/existing',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[]}],
 workspaces:[{id:'project',name:'Existing',path:'/existing',available:true}],selectedSessionId:null,selectedWorkspaceId:'project',
 setup:{providers:[],providersLoadedAt:1,providersWorkspace:'/existing'},canvas:{open:false}};
const calls=[],workers=[],workspaces=[],errors=[];let browser,vite,page;
const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
const reply=(route,extra={})=>{state.revision++;return route.fulfill({json:{accepted:true,state,...extra}})};
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1280,height:900}});page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{
  window.EventSource=class extends EventTarget{close(){}};
  if(sessionStorage.getItem('amplifier.messageOutbox.v1')===null)sessionStorage.setItem('amplifier.messageOutbox.v1',JSON.stringify([
   {id:'old-failed',commandId:'old-command',sessionId:null,text:'Hi!',attachmentIds:[],status:'failed',error:'Old workspace unavailable',createdAt:1}]));
 });
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path==='/api/state')return route.fulfill({json:state});
  if(path==='/api/shell')return route.fulfill({json:shellFor(state,()=>{}).data});
  if(path==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  if(path!=='/api/actions')return route.fulfill({json:{ok:true}});
  const original=route.request().postDataJSON();calls.push(original);
  const body=original.action==='shell.command'?{...original,action:original.args.action,args:original.args.args}:original.action==='shell.view.update'?{...original,action:'view.update'}:original;
  if(body.action==='view.update')state.view={...state.view,...body.args.patch};
  if(body.action==='session.draft'){state.selectedSessionId=null;state.view.draft='';}
  if(body.action==='session.create'){
   if(body.args.workspace!=='/existing')return route.fulfill({status:409,json:{accepted:false,error:'Workspace folder does not exist: /missing'}});
   const chat={...state.sessions[0],id:'created',title:'Created conversation',messages:[],creationCommandId:body.id};
   state.sessions.unshift(chat);state.selectedSessionId=chat.id;delete state.view.newSessionDraft;
  }
  if(body.action==='conversation.send'){
   state.sessions.find(row=>row.id===body.args.sessionId).messages.push({id:'received',inputId:body.id,role:'user',text:body.args.text,delivery:{status:'accepted'}});
  }
  if(body.action==='worker.spawn'){workers.push({route,body});return;}
  if(body.action==='workspace.create'){workspaces.push({route,body});return;}
  return reply(route);
 });
 await page.goto(vite.resolvedUrls.local[0]);const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.waitFor();
 await expect(page.getByRole('combobox',{name:'Select conversation'}).locator('option:checked')).toHaveText('New chat');
 await expect(page.getByText('Old workspace unavailable',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Discard unsent message',exact:true}).click();
 await expect(page.getByText('Hi!',{exact:true})).toHaveCount(0);await page.reload();await composer.waitFor();
 await expect(page.getByText('Hi!',{exact:true})).toHaveCount(0);
 assert.equal(calls.filter(row=>['conversation.send','session.create'].includes(row.action)).length,0,'Discard is local only');
 await composer.fill('Start fresh');await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(page.getByText('Workspace folder does not exist: /missing',{exact:true})).toBeVisible();
 await expect(page.getByRole('heading',{name:'New chat',exact:true})).toBeVisible();
 await page.locator('#new-chat-workspace').fill('/existing');
 await page.getByRole('button',{name:'Retry',exact:true}).click();
 await expect(page.getByText('Start fresh',{exact:true})).toHaveCount(1);
 await expect(page.getByRole('combobox',{name:'Select conversation'})).toHaveValue('created');
 await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')).length===0);
 assert.equal(calls.filter(row=>row.action==='conversation.send').length,1);
 assert.deepEqual(calls.filter(row=>row.action==='session.create').map(row=>row.args.workspace),['/missing','/existing']);
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
 await action('view.update',{patch:{panel:null}});await page.getByRole('button',{name:'New workspace',exact:true}).click();
 const workspace=page.locator('#nav-workspace-path');await workspace.fill('/first-workspace');await page.getByRole('button',{name:'Create workspace',exact:true}).click();
 await expect.poll(()=>workspaces.length).toBe(1);await workspace.fill('/newer-workspace');await reply(workspaces.shift().route);
 await expect(workspace).toHaveValue('/newer-workspace');
 await page.getByRole('button',{name:'Create workspace',exact:true}).click();await expect.poll(()=>workspaces.length).toBe(1);await workspaces.shift().route.fulfill({status:409,json:{accepted:false,error:'Workspace denied for fixture'}});
 await expect(workspace).toHaveValue('/newer-workspace');await expect(page.getByText('Workspace denied for fixture',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Create workspace',exact:true}).click();await expect.poll(()=>workspaces.length).toBe(1);await reply(workspaces.shift().route);
 await expect(workspace).toHaveCount(0);assert.deepEqual(state.view.workspaceDraft,{});
 assert.deepEqual(errors,[]);
 console.log('New chat recovery passed: truthful new-chat header; persisted failed-message discard; inline creation error; corrected-path retry sends once; worker success persists reset; failure/new typing retained; modal error dismissal. Zero model calls.');
}finally{await browser?.close();await vite?.close()}
