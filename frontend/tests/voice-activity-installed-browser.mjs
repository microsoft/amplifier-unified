// One retained production-SPA page, using actual installed native projection.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync,writeFileSync} from 'node:fs';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
const [python,fixture,root] = process.argv.slice(2);
assert.ok(python&&fixture&&root,'python fixture root arguments required');
const require=createRequire(import.meta.url),{chromium,expect}=require('@playwright/test');
const result={status:'failed',launch:{channel:'chromium',headless:true,args:['--no-zygote','--single-process','--disable-gpu']},screenshots:[],modelInputs:0,forbiddenActions:[]};
let server,browser,page,token='',logs='';
const clean=value=>String(value).replaceAll(token||'no-fixture-token','[redacted-fixture-token]');
try{
 server=spawn(python,['-I','-B','-u',fixture,root],{stdio:['ignore','pipe','pipe']});
 server.stderr.on('data',data=>logs+=data);
 const ready=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+logs)),45000);
  const lines=createInterface({input:server.stdout});
  lines.on('line',line=>{try{const value=JSON.parse(line);if(value.controlToken){clearTimeout(timer);lines.close();resolve(value)}}catch{logs+=line+'\n'}});
  server.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+logs))});
 });
 token=ready.controlToken;result.serverPid=ready.pid;
 browser=await chromium.launch(result.launch);
 const context=await browser.newContext({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer '+token}});
 page=await context.newPage();
 page.setDefaultTimeout(15000);
 const errors=[];page.on('pageerror',e=>errors.push(clean(e)));
 await page.route('**/*',route=>{
  const request=route.request(),url=request.url();
  if(!url.startsWith(ready.origin+'/'))return route.abort('blockedbyclient');
  if(request.method()==='POST'&&url.endsWith('/api/actions')){
   const action=request.postDataJSON()?.action;
   if(['conversation.send','conversation.retry','call.start','runtime.restart','session.resume','worker.spawn'].includes(action)){result.forbiddenActions.push(action);return route.abort('blockedbyclient')}
  }
  return route.continue();
 });
 await page.goto(ready.origin,{waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>!!window.amplifier?.getState()?.selectedSessionId,null,{timeout:30000});
 for(const width of [1280,390]){
  await page.setViewportSize({width,height:width===390?844:950});
  await expect(page.getByText('Public saved answer',{exact:true})).toBeVisible();
  const text=await page.locator('body').innerText();
  assert.ok(!text.includes('PRIVATE-HANDOFF-SENTINEL')&&!text.includes('PRIVATE-TOOL-SENTINEL'));
  const state=await page.evaluate(()=>window.amplifier.getState());
  const session=state.sessions.find(row=>row.id===state.selectedSessionId);
  assert.equal(session.execution.nodes.filter(row=>row.kind==='tool').length,2);
  assert.equal(session.execution.nodes.filter(row=>row.kind==='llm').length,0);
  assert.equal(session.execution.turns.length,2);
  assert.ok(session.execution.turns.some(turn=>turn.anchorMessageId===null));
  const controls=page.locator('section[aria-label="Execution details"] > button.a-execution-turn-line');
  assert.ok(await controls.count()>=2,'Both execution groups have rendered controls');
  const control=controls.first();
  await control.focus();
  if(await control.getAttribute('aria-expanded')==='true'){
   await page.keyboard.press('Enter');await expect(control).toHaveAttribute('aria-expanded','false');
  }
  await page.keyboard.press('Enter');await expect(control).toHaveAttribute('aria-expanded','true');
  await expect(page.getByText('inspect',{exact:true}).first()).toBeVisible();
  assert.ok(!(await page.locator('body').innerText()).includes('PRIVATE-TOOL-SENTINEL'));
  const path=root+'-'+width+'.png';await page.screenshot({path,fullPage:true});result.screenshots.push(path);
  await page.reload({waitUntil:'domcontentloaded'});
  await expect(page.getByText('Public saved answer',{exact:true})).toBeVisible();
 }
 assert.deepEqual(errors,[]);
 assert.deepEqual(result.forbiddenActions,[]);
 assert.equal(context.pages().length,1);assert.equal(browser.contexts().length,1);
 result.status='passed';result.pageCount=1;result.contextCount=1;
 result.scope='Installed activity rendering only; not spoken-only/fallback/artifact presentation or hearing acceptance';
}catch(error){result.error=clean(error.stack);process.exitCode=1;}
finally{
 if(browser)await browser.close();
 if(server){
  if(server.exitCode===null)server.kill('SIGTERM');
  await new Promise(resolve=>{if(server.exitCode!==null)return resolve();const timer=setTimeout(()=>server.kill('SIGKILL'),10000);server.once('exit',()=>{clearTimeout(timer);resolve()})});
  result.serverExit=server.exitCode;if(server.exitCode!==0)process.exitCode=1;
 }
 result.serverLog=clean(logs);
 try{result.cleanup=JSON.parse(readFileSync(root+'/final.json','utf8'));assert.equal(result.cleanup.canonical_unchanged,true);assert.equal(result.cleanup.runtime_workers,0)}catch(error){result.cleanupError=clean(error);process.exitCode=1}
 if(process.exitCode)result.status='failed';
 writeFileSync(root+'-browser-result.json',JSON.stringify(result,null,2));
 console.log(JSON.stringify(result));
}