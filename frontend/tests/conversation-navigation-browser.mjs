import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {shellFor} from './shell-host.mjs';
const full=Array.from({length:791},(_,i)=>({id:'m'+i,role:i<731&&i%10===0?'user':'assistant',text:'Message '+i,createdAt:1000+i}));
let state={revision:1,settings:{workspace:'/fixture'},runtime:{available:true},view:{navPinned:true},sessions:[{id:'chat',sessionKind:'root',title:'Long chat',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:false,historyLoaded:true,messages:full.slice(-60),messageWindow:{offset:731,total:791,before:'m731'},execution:{nodes:[],turns:[]}}],workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'chat',selectedWorkspaceId:'project',canvas:{open:false}};
const previews=[],windows=[];let invalidIndex=true;
let browser,vite,page;const errors=[],calls=[];
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false}});await vite.listen();
 browser=await chromium.launch({headless:true,...(process.env.UNIFIED_BROWSER_SINGLE_PROCESS?{args:['--no-zygote','--single-process','--disable-gpu']}:{})});page=await browser.newPage({viewport:{width:1440,height:900}});page.on('pageerror',e=>errors.push(e.stack||e.message));
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
   const envelope=route.request().postDataJSON(),body=envelope.action==='shell.command'?envelope.args:envelope;calls.push(body);
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
 state={...state,revision:2,sessions:state.sessions.map(s=>({...s,sharedHistoryOffset:10,messages:[...s.messages,{id:'m791',role:'assistant',text:'New live reply'}],messageWindow:{...s.messageWindow,total:792}}))};
 await page.evaluate(value=>window.emitState(value),state);
 await expect(page.locator('[data-message-id="m791"]')).toHaveCount(0);
 await expect.poll(()=>pane.evaluate(el=>el.scrollTop)).toBe(readingTop);
 // A reload initially gets only the live tail. Fetch the saved reading window
 // through the same bounded API and retain the offset within its anchor row.
 const reading=await pane.evaluate(el=>{const top=el.getBoundingClientRect().top,node=[...el.querySelectorAll('[data-message-id]')].find(node=>node.getBoundingClientRect().bottom>top);return {id:node.dataset.messageId,offset:node.getBoundingClientRect().top-top}});
 await page.reload();
 await expect(page.locator(`[data-message-id="${reading.id}"]`)).toBeVisible();
 await expect.poll(()=>pane.evaluate((el,id)=>el.querySelector(`[data-message-id="${id}"]`).getBoundingClientRect().top-el.getBoundingClientRect().top,reading.id)).toBeCloseTo(reading.offset,0);
 assert.equal(windows.at(-1),reading.id);
 assert.ok(await page.locator('[data-message-id]').count()<=60);
 await page.evaluate(value=>window.emitState(value),state);
 await page.getByRole('button',{name:'Return to latest',exact:true}).click();
 await expect(page.locator('[data-message-id="m791"]')).toBeVisible();
 assert.ok(await page.locator('[data-message-id]').count()<=61);
 await marks.nth(4).hover();await expect(page.locator('.a-rail-preview-jump')).toHaveText('Message 40');
 // The current/last turn must use the same fisheye width as any other turn.
 const preview=page.locator('.a-rail-preview');
 const expandedWidth=async mark=>(await mark.locator('span').boundingBox()).width;
 await expect.poll(()=>expandedWidth(marks.nth(4))).toBeCloseTo(37.8,1);
 const ordinaryWidth=await expandedWidth(marks.nth(4));
 const assertPreviewGap=async mark=>{
  const line=await mark.locator('span').boundingBox(),card=await preview.boundingBox();
  assert.ok(card.x-line.x-line.width>=6&&card.x-line.x-line.width<=10,'Preview should stay close to the expanded mark without overlapping it.');
 };
 await assertPreviewGap(marks.nth(4));
 await page.screenshot({path:'/tmp/conversation-navigation.png'});
 await marks.last().hover();await expect(preview.locator('.a-rail-preview-jump')).toHaveText('Message 730');
 await expect.poll(()=>expandedWidth(marks.last())).toBeCloseTo(ordinaryWidth,1);
 await assertPreviewGap(marks.last());
 await page.screenshot({path:'/tmp/conversation-navigation-last.png'});
 await marks.nth(4).hover();await expect(preview.locator('.a-rail-preview-jump')).toHaveText('Message 40');
 await page.emulateMedia({reducedMotion:'reduce'});
 assert.equal(await marks.nth(4).locator('span').evaluate(el=>getComputedStyle(el).transitionDuration),'0s');
 assert.equal(await page.locator('.a-messages').getAttribute('data-overflow-above'),'true');
 assert.notEqual(await page.locator('.a-messages').evaluate(el=>getComputedStyle(el).maskImage),'none');
 await page.emulateMedia({forcedColors:'active'});
 assert.equal(await page.locator('.a-messages').evaluate(el=>getComputedStyle(el).maskImage),'none');
 // Navigation responds to the available chat width, not just the window.
 await page.emulateMedia({forcedColors:'none'});
 const conversation=page.locator('.a-conversation'),rail=page.locator('.a-conversation-rail');
 await expect(rail).toBeVisible();
 await conversation.evaluate(el=>el.style.maxWidth='700px');
 await expect(rail).toBeHidden();await expect(preview).toBeHidden();
 await conversation.evaluate(el=>el.style.removeProperty('max-width'));
 await expect(rail).toBeVisible();
 await page.setViewportSize({width:1100,height:900});
 await expect.poll(()=>conversation.evaluate(el=>el.clientWidth)).toBeLessThan(961);
 await expect(rail).toBeHidden();
 await pane.evaluate(el=>{el.scrollTop=(el.scrollHeight-el.clientHeight)/2;el.dispatchEvent(new Event('scroll'))});
 const latest=page.getByRole('button',{name:'Jump to latest messages'});await expect(latest).toBeVisible();
 for(const [trigger,label,close] of [['Model and reasoning settings','Conversation model','Close model settings'],['Conversation bundle','Choose conversation bundle','Close bundle settings']]){
  await page.getByRole('button',{name:trigger,exact:true}).click({timeout:8000});
  const popup=page.getByRole('region',{name:label,exact:true});await expect(popup).toBeVisible();
  await expect.poll(()=>popup.evaluate((el,jump)=>{
   const a=el.getBoundingClientRect(),b=document.querySelector(jump).getBoundingClientRect();
   const left=Math.max(a.left,b.left),right=Math.min(a.right,b.right),top=Math.max(a.top,b.top),bottom=Math.min(a.bottom,b.bottom);
   if(right<=left||bottom<=top)return 'no overlap';
   return el.contains(document.elementFromPoint((left+right)/2,(top+bottom)/2));
  },'.a-chat-jump button')).toBe(true);
  await page.getByRole('button',{name:close,exact:true}).click();
 }
 await page.screenshot({path:'/tmp/conversation-navigation-narrow.png'});
 await page.setViewportSize({width:390,height:844});await expect(rail).toBeHidden();
 await page.setViewportSize({width:1440,height:900});await expect(rail).toBeVisible();
 // New input leaves an older reading window; arrival alone did not.
 await marks.nth(6).click();await expect(page.getByRole('button',{name:'Return to latest',exact:true})).toBeVisible();
 await page.locator('.ProseMirror').fill('A new question');await page.locator('.ProseMirror').press('Enter');
 await expect(page.getByRole('button',{name:'Return to latest',exact:true})).toHaveCount(0);
 await expect(page.locator('[data-message-id="m791"]')).toHaveCount(1);
 assert.deepEqual(errors,[]);console.log('Full-history rail, lazy cached hover, bounded unloaded jump, and return to latest passed.');
}catch(error){console.error(JSON.stringify({errors,calls:calls.slice(-8)}));await page?.screenshot({path:'/tmp/conversation-navigation-failure.png'});throw error;}finally{await browser?.close();await vite?.close();}
