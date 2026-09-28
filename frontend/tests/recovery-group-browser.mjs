import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const recovered=i=>({id:'recovered-'+i,role:'user',text:'Retained result for job '+i,createdAt:i+1,observation:{source:'local-job-recovery',id:'job-'+i,recovery:{job_id:'job-'+i,call_id:'call-'+i,status:i===0?'cancelled':'returned',outcome:i===0?'unconfirmed':'tool_report_unverified',reason:'restored_evidence'}}});
const messages=[{id:'user',role:'user',createdAt:1789990000,text:'Review the retained work'},...Array.from({length:35},(_,i)=>recovered(i)),{id:'response',role:'assistant',createdAt:1789990100,text:'The saved results are available.'},recovered(35),recovered(36),{id:'quote',role:'user',createdAt:1789990200,text:'Recovered work update'}];
let state={revision:1,settings:{workspace:'/fixture',bundle:'anchors'},runtime:{available:true},view:{navPinned:true},sessions:[{id:'chat',sessionKind:'root',title:'Retained work',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages,execution:{turns:[],nodes:[{id:'saved-tool',kind:'tool',toolCallId:'call-34',output:'Retained worker report',label:'Research'}]}}],workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'chat',selectedWorkspaceId:'project',canvas:{open:false}};
let browser,vite;const errors=[],calls=[];
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false}});await vite.listen();
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1280,height:900}});page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{window.EventSource=class extends EventTarget{constructor(){super();setTimeout(()=>this.onopen?.(),0)}close(){}}});
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path==='/api/state')return route.fulfill({json:state});
  if(path==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  if(path==='/api/actions'){
   const body=route.request().postDataJSON();calls.push(body);
   if(body.action==='view.update')state={...state,revision:state.revision+1,view:{...state.view,...body.args.patch}};
   return route.fulfill({json:{accepted:true,state}});
  }
  return route.fulfill({json:{ok:true}});
 });
 await page.goto(vite.resolvedUrls.local[0]);
 const first=page.locator('details.a-recovery-group').first();
 await expect(first.locator(':scope > summary')).toHaveText('Saved work notices (35)');
 await expect(page.locator('details.a-recovery-group')).toHaveCount(2);
 await expect(first).not.toHaveAttribute('open','');
 await expect(page.locator('[data-message-id="response"]')).toBeVisible();
 await expect(page.locator('[data-message-id="quote"]')).toBeVisible();
 await first.locator(':scope > summary').click();
 await expect(first.locator('[data-message-id]')).toHaveCount(35);
 await expect(first.locator('details')).toHaveCount(36);
 await expect(first).toContainText('The final outcome is unconfirmed.');
 const result=first.locator('[data-message-id="recovered-34"]');
 await expect(result.getByText('Retained result for job 34',{exact:true})).not.toBeVisible();
 await result.getByText('View saved result',{exact:true}).click();
 await expect(result).toContainText('Retained worker report');
 await first.locator('[data-message-id="recovered-34"]').getByText('Technical details',{exact:true}).click();
 await first.locator('[data-message-id="recovered-34"]').getByRole('button',{name:'Copy observation'}).click();
 assert.ok(calls.some(call=>call.action==='message.copy'&&call.args.messageId==='recovered-34'));
 await first.locator(':scope > summary').click();
 await expect(first).not.toHaveAttribute('open','');
 await page.setViewportSize({width:390,height:844});
 await first.locator(':scope > summary').scrollIntoViewIfNeeded();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:'/tmp/unified-recovery-group-mobile.png'});
 assert.equal(calls.some(call=>['runtime.control','conversation.send','session.recover'].includes(call.action)),false);
 assert.deepEqual(errors,[]);
 console.log('Recovery grouping passed: 35 original records, one disclosure, unchanged chronology, individual copy, collapse and mobile width.');
}finally{await browser?.close();await vite?.close()}
