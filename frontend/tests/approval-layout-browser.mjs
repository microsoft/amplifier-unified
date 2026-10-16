import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

// Run after building this exact source, in an owned isolated test environment.
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!output)throw Error('AMPLIFIER_TEST_ARTIFACTS must name an owned evidence directory');
await mkdir(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',
 [root+'tests/fixtures/approval_layout_ui_server.py'],{stdio:['ignore','pipe','pipe']});
let browser,page,logs='',failure;
const results=[];
fixture.stderr.on('data',chunk=>logs+=chunk);
const unwrap=raw=>raw?.action==='shell.command'?(raw.args.command||raw.args):raw;
const columnMatches=({approvals,composer})=>Math.abs(approvals.x-composer.x)<=1&&Math.abs(approvals.width-composer.width)<=1;
try{
 const ready=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(Error('Approval fixture startup timed out')),20000);
  fixture.once('error',error=>{clearTimeout(timer);reject(error)});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Approval fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{
   const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}
  }catch{}});
 });
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1600,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.setDefaultTimeout(8000);
 const errors=[],responses=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{
  if(request.method()==='POST'&&request.url().endsWith('/api/actions')){
   const body=unwrap(request.postDataJSON());if(body?.action==='approval.respond')responses.push(body);
  }
 });
 const act=(action,args)=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const patch=values=>act('view.update',{patch:values});
 const check=()=>page.request.get(ready.url+'/fixture/check').then(response=>{assert.equal(response.ok(),true);return response.json()});
 const card=page.locator('[data-part=approvals]');
 const geometry=()=>page.evaluate(()=>{
  const rect=selector=>{const r=document.querySelector(selector).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}};
  return {approvals:rect('[data-part=approvals]'),composer:rect('[data-part=composer]'),chat:rect('[data-part=conversation]')};
 });
 const visibleControl=async button=>{
  await expect(button).toBeFocused();
  await expect.poll(()=>button.evaluate(element=>{
   const r=element.getBoundingClientRect(),card=element.closest('[data-part=approvals]'),c=card.getBoundingClientRect();
   const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
   return r.width>0&&r.height>0&&r.left>=c.left+card.clientLeft&&r.right<=c.left+card.clientLeft+card.clientWidth+1
    &&r.top>=c.top+card.clientTop&&r.bottom<=c.top+card.clientTop+card.clientHeight+1
    &&r.top>=0&&r.bottom<=innerHeight&&element.contains(hit);
  })).toBe(true);
 };
 const probe=async label=>{
  await expect.poll(async()=>columnMatches(await geometry()),`${label}: approval/composer column`).toBe(true);
  const bounds=await geometry();
  assert.ok(bounds.approvals.x>=bounds.chat.x&&bounds.approvals.x+bounds.approvals.width<=bounds.chat.x+bounds.chat.width+1,label+': within conversation');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),label+': no page overflow');
  assert.ok(await card.evaluate(el=>el.scrollWidth<=el.clientWidth+1),label+': no approval overflow');
  for(const identity of ['layout-object','layout-string']){
   const details=page.locator(`[data-approval-id="${identity}"] pre`);
   await expect(details).toContainText('DETAILS END');
   assert.ok(await details.evaluate(el=>el.scrollWidth<=el.clientWidth+1),label+': long detail tokens reflow');
   assert.ok(await details.evaluate(el=>{el.scrollTop=el.scrollHeight;return el.scrollHeight>el.clientHeight&&el.scrollTop+el.clientHeight>=el.scrollHeight-1}),label+': all details remain scrollable');
  }
  // Focus and Tab must reveal both actions, not just create off-screen DOM nodes.
  // Never activate a permission response, including the synthetic one.
  for(const [index,identity] of ['layout-object','layout-string',ready.grant].entries()){
   const approval=page.locator(`[data-approval-id="${identity}"]`);
   const allow=approval.getByRole('button',{name:'Allow',exact:true}),deny=approval.getByRole('button',{name:'Deny',exact:true});
   if(index===0)await allow.focus();else await page.keyboard.press('Tab');
   await visibleControl(allow);
   await page.keyboard.press('Tab');await visibleControl(deny);
   await page.keyboard.press('Shift+Tab');await visibleControl(allow);
   await page.keyboard.press('Tab');await visibleControl(deny);
  }
  assert.ok(await card.evaluate(el=>el.scrollTop>0),label+': lower requests reachable by keyboard');
  await page.screenshot({path:join(output,label+'-actions.png')});
  await card.evaluate(el=>{document.activeElement?.blur();el.scrollTop=0});
  await page.screenshot({path:join(output,label+'-content.png')});
  results.push({label,status:'passed',...bounds});
 };
 await page.goto(ready.url);await expect(card).toBeVisible();
 await expect(card.locator('[data-approval-id]')).toHaveCount(3);
 const before=await check();
 await probe('desktop-light');
 // Negative control restores the exact old width/margin behavior. A rendered
 // check that also accepts this state cannot protect the reported regression.
 const oldWidth=await page.addStyleTag({content:'#amp-one.a-chat-shell.a-approachable-shell .a-approvals{width:auto;max-width:none;margin-inline:18px;overflow-wrap:normal}'});
 try{
  await expect(card).toHaveCSS('max-width','none');
  await expect(card).toHaveCSS('margin-left','18px');
  const bounds=await geometry();assert.equal(columnMatches(bounds),false,'Old full-width behavior must be rejected');
  assert.ok(bounds.approvals.width>bounds.composer.width+100,'Negative control must actually stretch on desktop');
  results.push({label:'old-width-negative-control',status:'rejected',...bounds});
 }finally{await oldWidth.evaluate(el=>el.remove())}
 for(const scheme of ['light','dark']){
  await patch({scheme});
  for(const viewport of [{width:1600,height:1000},{width:390,height:844},{width:320,height:568},{width:900,height:480}]){
   await page.setViewportSize(viewport);await probe(`${scheme}-${viewport.width}x${viewport.height}`);
  }
  // A narrow conversation inside a wide desktop must use container gutters.
  await page.setViewportSize({width:1600,height:1000});
  await act('canvas.show',{kind:'text',title:'Layout fixture',content:'Synthetic split-pane content'});
  const separator=page.getByRole('separator',{name:'Resize canvas'});
  await separator.focus();await page.keyboard.press('End');
  await expect.poll(async()=>Math.round((await geometry()).chat.width)).toBe(360);
  await probe(scheme+'-split-360');
  await act('canvas.close',{});
 }
 await page.reload();await expect(card.locator('[data-approval-id]')).toHaveCount(3);
 const after=await check();
 assert.deepEqual(after,before,'Resize, keyboard inspection and reload must preserve approvals/grants');
 assert.deepEqual(responses,[],'Layout inspection must never dispatch approval.respond');
 assert.deepEqual(errors,[],'No browser exceptions');
 console.log('PASS: approval reading column, long details, keyboard-visible actions, desktop/narrow/short/split layouts, light/dark, old-width negative control, pending state retained.');
}catch(error){failure=error;console.error(error.stack);console.error(logs)}
finally{
 await browser?.close();
 if(fixture.exitCode===null&&fixture.pid){
  const exited=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);
  try{const [code,signal]=await exited;if(code!==0||signal)failure||=Error(`Fixture cleanup failed: ${code} ${signal}`)}finally{clearTimeout(timer)}
 }
 await writeFile(join(output,'approval-layout-result.json'),JSON.stringify({status:failure?'failed':'passed',results,error:failure?.stack??null,fixtureLog:logs},null,2)+'\n');
}
if(failure)process.exitCode=1;
