import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {shellFor} from './shell-host.mjs';
const full=Array.from({length:791},(_,i)=>({id:'m'+i,role:i<731&&i%10===0?'user':'assistant',text:'Message '+i,createdAt:1000+i}));
let state={revision:1,settings:{workspace:'/fixture'},runtime:{available:true},view:{navPinned:true},sessions:[{id:'chat',sessionKind:'root',title:'Long chat',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:false,historyLoaded:true,messages:full.slice(-60),messageWindow:{offset:731,total:791,before:'m731'},execution:{nodes:[],turns:[]}}],workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'chat',selectedWorkspaceId:'project',canvas:{open:false}};
const previews=[],windows=[];let invalidIndex=true;
let browser,vite;const errors=[],calls=[];
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false}});await vite.listen();
 browser=await chromium.launch({headless:true,...(process.env.UNIFIED_BROWSER_SINGLE_PROCESS?{args:['--no-zygote','--single-process','--disable-gpu']}:{})});const page=await browser.newPage({viewport:{width:1280,height:900}});page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(initial=>{const sources=[];window.EventSource=class extends EventTarget{constructor(){super();sources.push(this);setTimeout(()=>{this.onopen?.();this.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(initial)}))},0)}close(){}};window.emitState=state=>sources.forEach(source=>source.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(state)})));},state);
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path==='/api/conversation/navigation'){
   const query=new URL(route.request().url()).searchParams,id=query.get('messageId');
   if(!id&&invalidIndex){invalidIndex=false;return route.fulfill({json:{}})}
   if(!id)return route.fulfill({json:{revision:'fixed',turns:full.filter(row=>row.role==='user').map(row=>({id:row.id,position:Number(row.id.slice(1))}))}});
   const index=full.findIndex(row=>row.id===id);
   if(query.get('window')==='true'){windows.push(id);const start=Math.max(0,index-5);return route.fulfill({json:{messages:full.slice(start,start+60),offset:start,total:full.length,before:start?'m'+Math.max(0,start-55):null,after:'m'+(start+60),execution:{nodes:[],turns:[]}}})}
   previews.push(id);return route.fulfill({json:{id,text:full[index].text,reply:full[index+1].text}});
  }
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
 await page.getByRole('button',{name:'Retry chat navigator',exact:true}).click();
 const marks=page.locator('.a-rail-mark');await expect(marks).toHaveCount(74);
 assert.equal(previews.length,0);
 await expect(marks.last()).toHaveAttribute('data-visible','true');
 await marks.nth(3).hover();await expect(page.locator('.a-rail-preview-jump')).toHaveText('Message 30');
 assert.deepEqual(previews,['m30']);
 const firstMark=await marks.nth(3).boundingBox(),firstLine=await marks.nth(3).locator('span').boundingBox();
 assert.ok(firstLine.x-firstMark.x<5,'Marks must start at the rail edge, leaving room for magnification.');
 const bookmark=page.getByRole('button',{name:'Bookmark message',exact:true});
 const bookmarkBox=await bookmark.boundingBox();
 await page.mouse.move(bookmarkBox.x+8,bookmarkBox.y+8,{steps:20});
 await bookmark.click();await expect(bookmark).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Remove message bookmark',exact:true})).toBeVisible();
 await page.mouse.move(600,80);await marks.nth(3).hover();await expect(page.locator('.a-rail-preview-jump')).toHaveText('Message 30');
 assert.deepEqual(previews,['m30']);
 await marks.nth(3).click();await expect(page.locator('[data-message-id="m30"]')).toBeVisible();
 assert.deepEqual(windows,['m30']);
 assert.ok(await page.locator('[data-message-id]').count()<=60);
 assert.equal(await page.locator('[data-message-id="m790"]').count(),0);
 // A publication while reading older messages must not repeat the jump.
 const pane=page.locator('.a-messages');
 await pane.evaluate(el=>{el.scrollTop=250;el.dispatchEvent(new Event('scroll'))});
 const readingTop=await pane.evaluate(el=>el.scrollTop);
 state={...state,revision:2,sessions:state.sessions.map(s=>({...s,messages:[...s.messages,{id:'m791',role:'assistant',text:'New live reply'}],messageWindow:{...s.messageWindow,total:792}}))};
 await page.evaluate(value=>window.emitState(value),state);
 await expect(page.locator('[data-message-id="m791"]')).toHaveCount(0);
 await expect.poll(()=>pane.evaluate(el=>el.scrollTop)).toBe(readingTop);
 await page.getByRole('button',{name:'Return to latest',exact:true}).click();
 await expect(page.locator('[data-message-id="m791"]')).toBeVisible();
 assert.ok(await page.locator('[data-message-id]').count()<=61);
 await marks.nth(4).hover();await expect(page.locator('.a-rail-preview-jump')).toHaveText('Message 40');
 await page.screenshot({path:'/tmp/conversation-navigation.png'});
 await page.emulateMedia({reducedMotion:'reduce'});
 assert.equal(await marks.nth(4).locator('span').evaluate(el=>getComputedStyle(el).transitionDuration),'0s');
 assert.equal(await page.locator('.a-messages').getAttribute('data-overflow-above'),'true');
 assert.notEqual(await page.locator('.a-messages').evaluate(el=>getComputedStyle(el).maskImage),'none');
 await page.emulateMedia({forcedColors:'active'});
 assert.equal(await page.locator('.a-messages').evaluate(el=>getComputedStyle(el).maskImage),'none');
 assert.deepEqual(errors,[]);console.log('Full-history rail, lazy cached hover, bounded unloaded jump, and return to latest passed.');
}finally{await browser?.close();await vite?.close();}
