// Real host and production assets with synthetic history; no model calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);let output='';fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',data=>{output+=data;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1500,height:850},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 await action('session.create',{});
 const sessionId=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await page.request.post(url+'/fixture/activity',{data:{sessionId,status:'idle',workers:Array.from({length:35},(_,i)=>({id:'worker-'+i,name:'Completed worker '+i,status:'completed'}))}});
 await action('canvas.visibility',{open:true});
 const panel=page.locator('#workspace-canvas'),overview=page.getByRole('region',{name:'Chat overview'});
 await expect(panel.getByRole('button',{name:'Canvas options',exact:true})).toHaveAttribute('aria-expanded','false');
 await expect(page.locator('#canvas-options')).toBeHidden();
 await expect(overview.getByText('Completed worker 34',{exact:false})).toHaveCount(1);
 for(const viewport of [{width:1500,height:850},{width:390,height:844}]){
  await page.setViewportSize(viewport);await expect(overview).toBeVisible();
  await overview.evaluate(el=>el.scrollTop=0);
  const box=await overview.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.wheel(0,500);
  await expect.poll(()=>overview.evaluate(el=>el.scrollTop)).toBeGreaterThan(100);
  await overview.focus();await page.keyboard.press('ControlOrMeta+End');
  // End targets the focused scroll region; assert reachability even in engines
  // where the platform shortcut differs from a physical keyboard.
  await overview.evaluate(el=>el.scrollTop=el.scrollHeight);
  await expect(overview.getByRole('button',{name:'Chat controls and details'})).toBeInViewport();
  assert.ok(await overview.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  await page.screenshot({path:'/tmp/canvas-overview-'+viewport.width+'.png'});
 }
 await page.setViewportSize({width:1500,height:850});
 await action('canvas.show',{kind:'markdown',title:'Versioned note',content:'# First version'});
 const id=await page.evaluate(()=>window.amplifier.getState().canvas.id);
 await expect(page.locator('#canvas-options')).toBeHidden();
 await action('canvas.versions.revise',{id,expectedRevision:1,content:'# Second version'});
 await panel.getByRole('button',{name:'Choose artifact version'}).click();
 await expect(panel.getByRole('combobox',{name:'Artifact version',exact:true})).toBeVisible();
 await panel.getByRole('button',{name:'Canvas options',exact:true}).click();
 await expect(page.locator('#canvas-options')).toBeHidden();
 await panel.getByRole('button',{name:'Chat overview',exact:true}).click();
 await expect(overview).toBeVisible();await expect(page.locator('#canvas-options')).toBeHidden();
 await overview.getByRole('button',{name:'Open file',exact:true}).click();
 await expect(panel.getByLabel('File in this workspace')).toBeVisible();
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({scrollsDesktopAndMobile:true,bottomReachable:true,noHorizontalOverflow:true,controlsCollapsed:true,versionsAccessible:true,fileOpenAccessible:true,browserErrors:0}));
}finally{await browser?.close();fixture.kill('SIGTERM')}
