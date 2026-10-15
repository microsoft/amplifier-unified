import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/archive-feedback';
await mkdir(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'/.venv/bin/python',[root+'/tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,release;
try{
 const url=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}});
  fixture.once('error',reject);fixture.once('exit',code=>reject(Error('Fixture exited '+code)));
 });
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1200,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),peer=await context.newPage(),errors=[];
 for(const p of [page,peer]){p.on('pageerror',e=>errors.push(e.message));await p.goto(url);await p.waitForFunction(()=>window.amplifier?.dispatch)}
 const action=(name,args={},p=page)=>p.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const workspace=(await state()).workspaces.find(row=>row.path);
 await action('session.create',{title:'Archive example',workspace:workspace.path,location:{kind:'workspace'}});
 const sid=(await state()).selectedSessionId;
 await action('session.create',{title:'Other example',workspace:workspace.path,location:{kind:'workspace'}});
 const other=(await state()).selectedSessionId;
 await action('session.select',{id:sid});await action('session.select',{id:sid},peer);
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Draft is preserved');
 await action('attachment.add',{sessionId:sid,name:'draft.txt',base64:Buffer.from('Synthetic attachment').toString('base64')});
 await action('view.update',{patch:{navPinned:true}});
 const before=await page.request.get(url+'/fixture').then(r=>r.json());
 let mode='hold',archiveCalls=0,restoreCalls=0;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='session.restore')restoreCalls++;
  if(body?.action!=='session.archive'){await route.continue();return}
  archiveCalls++;
  if(mode==='reject'){await route.fulfill({status:403,contentType:'application/json',body:JSON.stringify({accepted:false,error:'Synthetic archive rejection'})});return}
  const response=await route.fetch();
  if(mode==='hold')await new Promise(resolve=>{release=resolve});
  await route.fulfill({response});
 });
 async function headerArchive(){
  await page.getByRole('button',{name:'Chat',exact:true}).click();
  await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Archive',exact:true}).click();
 }
 await headerArchive();
 await expect(page.locator('[data-archive-notice]')).toContainText('Archiving…');
 assert.equal((await state()).view.workSurface||'chat','chat');
 await page.screenshot({path:output+'/pending.png'});
 await expect.poll(()=>!!release).toBe(true);release();release=null;mode='normal';
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat archived');
 await expect(page.locator('[data-part="work-browser"] h1')).toHaveText(workspace.name);
 await page.waitForFunction(()=>document.activeElement?.matches('[data-part="work-browser"] h1'));
 assert.equal((await state()).view.workSurface,'workspace');
 assert.equal((await state()).selectedSessionId,sid); // No replacement chat opens.
 const peerState=await peer.evaluate(()=>window.amplifier.getState());
 assert.equal(peerState.selectedSessionId,sid);assert.equal(peerState.view.workSurface||'chat','chat');
 await page.screenshot({path:output+'/archived-desktop.png'});
 await page.locator('[data-archive-notice]').getByRole('button',{name:'Undo',exact:true}).click();
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat restored');
 assert.equal(restoreCalls,1);assert.equal((await state()).view.workSurface,'workspace');
 await action('session.select',{id:sid});
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveText('Draft is preserved');
 await expect(page.locator('[data-part="composer"]')).toContainText('draft.txt');
 mode='reject';await headerArchive();
 await expect(page.locator('[data-archive-notice]')).toContainText('Synthetic archive rejection');
 assert.equal((await state()).view.workSurface||'chat','chat');
 // Same A -> B -> A identity is not authority for an old archive to redirect.
 mode='hold';await headerArchive();await expect.poll(()=>!!release).toBe(true);
 await action('session.select',{id:other});await action('session.select',{id:sid});
 release();release=null;mode='normal';
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat archived');
 assert.equal((await state()).view.workSurface||'chat','chat');
 await action('session.restore',{id:sid});
 // A row action for another chat doesn't leave the current conversation.
 await action('session.select',{id:sid});await action('session.pin',{id:other,pinned:true});
 await page.getByRole('button',{name:'Details and actions for Other example',exact:true}).first().click();
 await page.getByRole('button',{name:'Archive Other example',exact:true}).click();
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat archived');
 assert.equal((await state()).selectedSessionId,sid);assert.equal((await state()).view.workSurface||'chat','chat');
 // Public integration dispatch never invokes interactive navigation.
 await action('session.archive',{id:sid});
 assert.equal((await state()).view.workSurface||'chat','chat');await action('session.restore',{id:sid});
 await page.setViewportSize({width:390,height:844});
 await headerArchive();
 await page.waitForFunction(()=>window.amplifier.getState().view.workSurface==='workspace');
 await expect(page.locator('[data-part="work-browser"] h1')).toBeVisible();
 assert.ok(await page.locator('[data-archive-notice]').evaluate(el=>el.scrollWidth<=el.clientWidth));
 await page.screenshot({path:output+'/archived-mobile.png'});
 // Managed chats land on the existing full chat list rather than their private folder.
 await action('session.create',{title:'Managed example',location:{kind:'managed'}});
 await headerArchive();await page.waitForFunction(()=>window.amplifier.getState().view.workSurface==='chats');
 await expect(page.getByRole('heading',{name:'Search chats',exact:true})).toBeVisible();
 const after=await page.request.get(url+'/fixture').then(r=>r.json());
 assert.deepEqual(after.started,before.started);assert.deepEqual(after.stopped,before.stopped);assert.deepEqual(after.sent,before.sent);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,archiveCalls,restoreCalls,pending:true,workspaceAndManagedDestination:true,undo:true,explicitRejection:true,roundtripRace:true,noncurrentRow:true,rawApiUnchanged:true,peerUnchanged:true,draftAndAttachmentPreserved:true,narrow:true,noRuntimeLifecycleChanges:true}));
}finally{release?.();await browser?.close();fixture.kill()}