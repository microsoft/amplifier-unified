// Real UI with explicitly controlled admission, lost replies, and server updates.
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
let state={revision:1,settings:{workspace:'/fixture'},runtime:{available:true},view:{navPinned:true},sessions:[{id:'chat',title:'Saved conversation',sessionKind:'root',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[]}],workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'chat',selectedWorkspaceId:'project',setup:{providers:[],providersLoadedAt:1,providersWorkspace:'/fixture'},canvas:{open:false}};
const waiting=[],calls=[],errors=[];let browser,vite,page;
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check,label){for(let i=0;i<150&&!check();i++)await sleep(10);assert.ok(check(),label)}
async function next(){await until(()=>waiting.length,'Expected pending operation');return waiting.shift()}
const chat=()=>state.sessions.find(s=>s.id===state.selectedSessionId);
async function emit(){state.revision++;await page.evaluate(state=>window.emitState(state),state)}
async function received(item,text=item.body.args.text){const message={id:'server-'+item.body.id,inputId:item.body.id,role:'user',text,createdAt:Date.now()/1000,delivery:{status:'accepted'}};chat().messages.push(message);state.revision++;await item.route.fulfill({json:{accepted:true,delivery:'accepted',state}})}
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1280,height:900}});page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{const sources=[];window.EventSource=class extends EventTarget{constructor(){super();sources.push(this)}close(){}};window.emitState=state=>sources.forEach(source=>source.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(state)})))});
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path==='/api/state')return route.fulfill({json:state});
  if(path==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  if(path!=='/api/actions')return route.fulfill({json:{ok:true}});
  const body=route.request().postDataJSON();calls.push(body);
  if(['conversation.send','conversation.delivery','conversation.retry','message.edit'].includes(body.action)){waiting.push({route,body,client:route.request().headers()['x-amplifier-client']});return}
  if(body.action==='view.update')state.view={...state.view,...body.args.patch};
  state.revision++;return route.fulfill({json:{accepted:true,state}});
 });
 const composer=()=>page.getByRole('textbox',{name:'Message Amplifier'});
 const send=async text=>{await composer().fill(text);await page.getByRole('button',{name:'Send message',exact:true}).click();assert.equal(await composer().inputValue(),'');await page.locator('.a-user').filter({hasText:text}).waitFor();return next()};
 await page.goto(vite.resolvedUrls.local[0]);await composer().waitFor();
 const first=await send('Same text twice intentionally');
 await composer().fill('Same text twice intentionally');await until(()=>state.view.draft==='Same text twice intentionally','New typing saves while admission waits');
 chat().messages.push({id:'first',inputId:first.body.id,role:'user',text:first.body.args.text,delivery:{status:'sending'}});await emit();
 assert.equal(await page.locator('.a-user').count(),1,'Tentative shared bubble replaces optimistic copy');assert.equal(await composer().inputValue(),'Same text twice intentionally');
 chat().messages[0].delivery.status='accepted';state.revision++;await first.route.fulfill({json:{accepted:true,delivery:'accepted',state}});
 await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')).length===0);assert.equal(await composer().inputValue(),'Same text twice intentionally','Late acknowledgement cannot clear the next identical draft');
 const failed=await send('Rejected input');await failed.route.fulfill({status:409,json:{accepted:false,error:'Fixture rejection',code:'invalid_input'}});
 const rejected=page.locator('.a-user').filter({hasText:'Rejected input'});await rejected.getByRole('button',{name:'Retry',exact:true}).waitFor();
 await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:()=>new Promise(resolve=>window.finishCopy=resolve)}}));
 await rejected.getByRole('button',{name:'Copy message as Markdown',exact:true}).click();assert.equal(await rejected.getByRole('button',{name:'Copy message as Markdown',exact:true}).getAttribute('aria-busy'),'true');await page.evaluate(()=>window.finishCopy());
 await rejected.getByRole('button',{name:'Edit message',exact:true}).click();
 await page.getByRole('textbox',{name:'Edit your message'}).fill('Corrected input');assert.equal(await page.getByLabel('Start a new conversation instead').count(),0);
 await page.getByRole('button',{name:'Save & regenerate',exact:true}).click();const corrected=await next();assert.notEqual(corrected.body.id,failed.body.id);assert.equal(corrected.body.args.text,'Corrected input');await received(corrected);
 await page.getByText('Corrected input',{exact:true}).waitFor();assert.equal(state.sessions.length,1);assert.equal(calls.filter(c=>c.action==='message.edit').length,0,'Unsent edit retries delivery without forking or rewinding');
 const lostRejection=await send('Rejection reply lost');await lostRejection.route.abort('failed');await page.getByRole('button',{name:'Check delivery',exact:true}).click();
 const rejectionCheck=await next();assert.equal(rejectionCheck.body.action,'conversation.delivery');assert.equal(rejectionCheck.body.args.inputId,lostRejection.body.id);await rejectionCheck.route.fulfill({json:{accepted:true,result:{delivery:'not_saved',message:'No saved copy; choose Send again.'},state}});
 assert.equal(calls.filter(c=>c.action==='conversation.send'&&c.id===lostRejection.body.id).length,1,'Check never resends');
 await page.getByRole('button',{name:'Send again',exact:true}).click();await page.getByRole('button',{name:'Send this message again',exact:true}).click();
 const rejectionReceipt=await next();assert.equal(rejectionReceipt.body.id,lostRejection.body.id);await rejectionReceipt.route.fulfill({json:{accepted:false,duplicate:true,status:409,error:'Saved rejection',state}});
 await page.getByRole('button',{name:'Retry',exact:true}).click();const rejectedRetry=await next();assert.notEqual(rejectedRetry.body.id,lostRejection.body.id);await received(rejectedRetry);
 const delayed=await send('Acknowledgement delayed');chat().status='working';chat().messages.push({id:'delayed',inputId:delayed.body.id,role:'user',text:delayed.body.args.text,delivery:{status:'sending'}});await emit();
 await composer().fill('Keep this next draft');await until(()=>state.view.draft==='Keep this next draft','Draft saves while acknowledgement waits');
 await delayed.route.fulfill({status:504,json:{error:'The runtime operation has not returned yet. It may still be running; do not automatically repeat it.',code:'runtime_pending',delivery:'unknown'}});
 await page.getByRole('button',{name:'Check delivery',exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Retry',exact:true}).count(),0,'Unknown delivery does not offer a fresh retry');
 assert.equal(await composer().inputValue(),'Keep this next draft');assert.equal(calls.filter(c=>c.id===delayed.body.id).length,1,'Timeout does not automatically resend');
 chat().messages.at(-1).delivery.status='accepted';chat().status='idle';await emit();
 await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')).length===0);assert.equal(await page.getByText('Acknowledgement delayed',{exact:true}).count(),1);assert.equal(await composer().inputValue(),'Keep this next draft');assert.equal(calls.filter(c=>c.id===delayed.body.id).length,1,'Late acknowledgement does not resend');
 const lost=await send('Delivery uncertain');await lost.route.abort('failed');await page.getByRole('button',{name:'Check delivery',exact:true}).waitFor();
 await page.reload();await composer().waitFor();await page.getByRole('button',{name:'Check delivery',exact:true}).click();const checked=await next();assert.equal(checked.body.action,'conversation.delivery');assert.equal(checked.body.args.inputId,lost.body.id);assert.notEqual(checked.client,lost.client,'Reload has a new client identity');
 await checked.route.fulfill({json:{accepted:true,result:{delivery:'sending',message:'The original send is still in progress.'},state}});await page.getByRole('button',{name:'Check delivery',exact:true}).waitFor();
 chat().messages.push({id:'late',inputId:lost.body.id,role:'user',text:lost.body.args.text,delivery:{status:'accepted'}});await emit();
 await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')).length===0);assert.equal(await page.getByText('Delivery uncertain',{exact:true}).count(),1);assert.equal(calls.filter(c=>c.action==='conversation.send'&&c.id===lost.body.id).length,1,'Check did not resubmit');
 await page.locator('.a-user').last().getByRole('button',{name:'Edit message',exact:true}).click();await page.getByRole('textbox',{name:'Edit your message'}).fill('Edit the last input');assert.equal(await page.getByLabel('Start a new conversation instead').isChecked(),false);
 await page.getByRole('button',{name:'Save & regenerate',exact:true}).click();const edit=await next();assert.equal(edit.body.action,'message.edit');assert.equal(edit.body.args.mode,'current');assert.equal(edit.body.args.sessionId,'chat');
 await edit.route.fulfill({status:409,json:{accepted:false,error:'Fixture safe-boundary failure'}});await page.getByText('Fixture safe-boundary failure',{exact:true}).waitFor();assert.equal(await page.getByRole('textbox',{name:'Edit your message'}).inputValue(),'Edit the last input');
 await page.getByLabel('Start a new conversation instead').check();await page.getByRole('button',{name:'Save & regenerate',exact:true}).click();const fork=await next();assert.equal(fork.body.args.mode,'fork');state.view.messageEdit=null;state.revision++;await fork.route.fulfill({json:{accepted:true,state}});
 await page.screenshot({path:'/tmp/amplifier-optimistic-messages.png'});await page.setViewportSize({width:390,height:844});await page.evaluate(()=>{const save=Storage.prototype.setItem;Storage.prototype.setItem=function(key,value){if(key==='amplifier.messageOutbox.v1')throw new DOMException('Fixture quota','QuotaExceededError');return save.call(this,key,value)}});const mobile=await send('Mobile failed message');await mobile.route.fulfill({status:409,json:{accepted:false,error:'Fixture rejection'}});await page.getByRole('button',{name:'Retry',exact:true}).waitFor();await page.getByText('This browser could not save the pending message. Keep this tab open until delivery is confirmed.',{exact:true}).waitFor();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:'/tmp/amplifier-message-retry-mobile.png'});
 await page.addInitScript(()=>Object.defineProperty(window,'sessionStorage',{get(){throw new DOMException('Fixture storage blocked','SecurityError')}}));await page.reload();await composer().waitFor();
 const blockedStorage=await send('Storage unavailable');await blockedStorage.route.fulfill({status:409,json:{accepted:false,error:'Fixture rejection'}});
 await page.getByRole('button',{name:'Retry',exact:true}).waitFor();await page.getByText('This browser could not save the pending message. Keep this tab open until delivery is confirmed.',{exact:true}).waitFor();
 assert.deepEqual(errors,[]);console.log('Outbox browser passed: immediate clear/bubble, late identical draft, server dedup, rejected-message edit, delayed504/late acknowledgement without replay, uncertain reload/check, explicit current/fork modes, failure keeps edit, mobile, blocked storage.');
}finally{await browser?.close();await vite?.close()}
