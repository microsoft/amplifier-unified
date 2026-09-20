import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(root+'/.venv/bin/python',[root+'/tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,release;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timeout=setTimeout(()=>reject(Error('startup')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timeout);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const act=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 await act('session.create',{title:'First chat'});const first=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await act('conversation.send',{sessionId:first,text:'First history'});
 await act('view.update',{sessionId:first,patch:{draft:'First private draft'}});
 await act('session.create',{title:'Second chat'});const second=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await act('conversation.send',{sessionId:second,text:'Second history'});
 await act('view.update',{sessionId:second,patch:{draft:'Second private draft'}});
 await act('session.select',{id:first});
 const blocked=new Promise(resolve=>{release=resolve});
 await page.route('**/api/actions',async route=>{
  const request=route.request();
  if(request.method()==='POST'&&request.postDataJSON()?.action==='session.select')await blocked;
  await route.continue();
 });
 await page.evaluate(id=>{window.switchStarted=performance.now();window.switchSettled=false;window.amplifier.dispatch('session.select',{id}).then(()=>window.switchSettled=true)},second);
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveValue('Second private draft',{timeout:1000});
 await expect(page.getByText('Second history',{exact:true})).toBeVisible({timeout:1000});
 const painted=await page.evaluate(()=>({milliseconds:performance.now()-window.switchStarted,settled:window.switchSettled,session:window.amplifier.getState().selectedSessionId}));
 assert.equal(painted.settled,false,'cached chat must paint before the blocked server request');
 assert.equal(painted.session,second);
 // A user can type while navigation is pending, with a stable target.
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Typed while selection was pending');
 release();await page.waitForFunction(()=>window.switchSettled);
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveValue('Typed while selection was pending');
 await act('session.select',{id:first});
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveValue('First private draft');
 await act('session.select',{id:second});
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveValue('Typed while selection was pending');
 console.log(JSON.stringify({passed:true,cachedSwitchPaintMs:painted.milliseconds,paintedBeforeServer:true,draftsPreserved:true}));
}finally{release?.();await browser?.close();fixture.kill()}
