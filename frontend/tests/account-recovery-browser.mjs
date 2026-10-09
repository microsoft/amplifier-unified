import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {shellFor} from './shell-host.mjs';
import {openSettingsPage} from './browser-settings.mjs';
const provider={id:'chatgpt',module:'provider-openai-chatgpt',enabled:true,authenticationRequired:true,config:{default_model:'fixture-model'},account:{connected:false,authMode:'chatgpt_codex',needsAttention:true},credentialsConfigured:false};
let state={revision:1,settings:{workspace:'/fixture',bundle:'anchors'},runtime:{available:true},view:{navPinned:true},sessions:[{id:'chat',sessionKind:'root',title:'Saved chat',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[]}],workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'chat',selectedWorkspaceId:'project',setup:{providers:[provider],providersLoadedAt:1,providersWorkspace:'/fixture',providerCatalogs:{chatgpt:{phase:'error',models:[]}}},attention:{unread:1,settingsUnread:1,sections:{setup:1},pages:{'ai-connections':1},items:[]},canvas:{open:false}};
let browser,vite;const errors=[],calls=[];
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false}});await vite.listen();
 browser=await chromium.launch({headless:true,...(process.env.UNIFIED_BROWSER_SINGLE_PROCESS?{args:['--no-zygote','--single-process','--disable-gpu']}:{})});const page=await browser.newPage({viewport:{width:1280,height:900}});page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(initial=>{const sources=[];window.EventSource=class extends EventTarget{constructor(){super();sources.push(this);setTimeout(()=>{this.onopen?.();this.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(initial)}))},0)}close(){}};window.emitState=state=>sources.forEach(source=>source.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(state)})));},state);
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path==='/api/state')return route.fulfill({json:state});
  if(path==='/api/shell')return route.fulfill({json:shellFor(state,()=>{}).data});
  if(path==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  if(path==='/api/actions'){
   const body=route.request().postDataJSON();calls.push(body);
   if(body.action==='view.update')state={...state,revision:state.revision+1,view:{...state.view,...body.args.patch}};
   return route.fulfill({json:{accepted:true,operationId:body.id,state}});
  }
  return route.fulfill({json:{ok:true}});
 });
 await page.goto(vite.resolvedUrls.local[0]);
 await page.getByRole('button',{name:/^App options/}).click();
 await expect(page.getByRole('button',{name:/^Settings\b/}).locator('.a-attention-badge')).toHaveText('1');
 await openSettingsPage(page,'ai-connections');
 await expect(page.locator('[data-settings-section="ai"] .a-attention-badge')).toHaveText('1');
 const account=page.getByRole('button',{name:/ChatGPT Subscription.*Sign-in needed/});
 await expect(account.locator('.a-attention-badge')).toHaveText('1');await account.click();
 await expect(page.getByRole('alert').filter({hasText:'renewal'})).toBeVisible();
 await page.getByRole('button',{name:'Refresh credentials',exact:true}).click();
 assert.ok(calls.some(call=>call.action==='providers.list'&&call.args.refresh===true));
 state={...state,revision:state.revision+1,attention:{settingsUnread:0,sections:{},pages:{},items:[]},setup:{...state.setup,operations:{'providers.list:':{phase:'ready',commandId:calls.findLast(call=>call.action==='providers.list'&&call.args.refresh)?.id}},providers:[{...provider,authenticationRequired:false,accountConnected:true,account:{connected:true,authMode:'chatgpt_codex'}}]}};
 await page.evaluate(value=>window.emitState(value),state);
 await expect(page.getByText('Your ChatGPT account is connected.',{exact:true})).toBeVisible();
 assert.equal(await page.locator('.a-attention-badge').count(),0);
 await page.screenshot({path:'/tmp/account-recovery-browser.png'});
 assert.deepEqual(errors,[]);console.log('Account recovery: Settings → AI connections → account badges, renewal action, and recovered state passed.');
}finally{await browser?.close();await vite?.close();}
