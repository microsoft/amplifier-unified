// Real host, native messages and ordinary event ingress; no model or saved Plan seed.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdir,mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {fixturePython} from './fixture-python.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const home=await mkdtemp(join(tmpdir(),'last-reported-plan-'));
const headers={Authorization:'Bearer last-plan-fixture-token','Content-Type':'application/json'};
let browser,page,stop,base,sid,primary,log='';
const evidence={checkpoints:[]},errors=[],actions=[];
async function start(){
 const child=spawn(fixturePython(root+'.venv/bin/python'),['-u',root+'tests/fixtures/last_reported_plan_server.py',root,home],{stdio:['ignore','pipe','pipe']});
 stop=ownFixture(child,{label:'Last reported Plan host'});
 child.stderr.on('data',chunk=>{log=(log+chunk).slice(-16384)});
 const meta=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(Error('Fixture startup: '+log)),30000);
  child.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+log))});
  child.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{
   const row=JSON.parse(line);if(row.port){clearTimeout(timer);resolve(row)}
  }catch{}});
 });
 base=`http://127.0.0.1:${meta.port}`;sid=meta.id;
}
async function control(op,extra={}){
 const response=await fetch(base+'/api/fixture/plan',{method:'POST',headers,body:JSON.stringify({op,...extra})});
 assert.equal(response.status,200,await response.clone().text());return response.json();
}
async function open(){
 page=await browser.newPage({viewport:{width:1400,height:1000},extraHTTPHeaders:headers});
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(new URL(request.url()).pathname==='/api/actions')actions.push(request.postDataJSON()?.action)});
 await page.goto(base);await showFixtureConversation(page);
 await expect(page.locator('.a-messages')).toHaveAttribute('aria-busy','false');
 await expect(page.locator('[data-message-id]')).toHaveCount(20);
}
const plan=()=>page.locator('.a-chat-plan').filter({visible:true}).first();
const current=()=>page.evaluate(id=>window.amplifier.getState().sessions.find(row=>row.id===id)?.execution?.plan,sid);
async function check(text){
 await expect(plan()).toBeVisible();
 if(await plan().locator('details').getAttribute('open')===null)await plan().locator('summary').click();
 await expect(plan()).toContainText(text);
 assert.ok(!/\b(Working|Failed|Stopping|Outcome unknown)\b/.test(await plan().innerText()));
 evidence.checkpoints.push(text);
}
try{
 await start();browser=await chromium.launch({headless:true});
 await open();
 await expect(page.locator('.a-chat-plan:visible')).toHaveCount(0);
 evidence.checkpoints.push('legacy-empty-no-backfill');
 await control('success');await check('Prepare the team update');
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Private draft remains');
 const first=await current();
 for(const op of ['failure','malformed','child']){
  await control(op,{text:'Must not replace root',status:'completed'});
  await expect.poll(current).toEqual(first);await check('Prepare the team update');
 }
 await control('edit-history');await check('Prepare the team update');
 assert.deepEqual(await current(),first);
 await control('success',{text:'Publish the team update',status:'completed'});
 await check('Publish the team update');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveValue('Private draft remains');
 const saved=await current();
 evidence.beforeRestart=await control('inspect');
 assert.equal(evidence.beforeRestart.historicalPlanReads,0);
 assert.equal(evidence.beforeRestart.runtimeCalls,0);
 await page.close();await stop();stop=null;
 await start();await open();await check('Publish the team update');
 assert.deepEqual(await current(),saved);evidence.checkpoints.push('host-restart-restored-report');
 await control('clear');
 await expect(page.locator('.a-chat-plan:visible')).toHaveCount(0);
 assert.deepEqual((await current()).items,[]);
 await page.reload();await showFixtureConversation(page);
 await expect(page.locator('.a-messages')).toHaveAttribute('aria-busy','false');
 await expect(page.locator('.a-chat-plan:visible')).toHaveCount(0);
 evidence.checkpoints.push('clear-reload-hidden');
 evidence.final=await control('inspect');
 assert.equal(evidence.final.historicalPlanReads,0);
 assert.equal(evidence.final.runtimeCalls,0);
 assert.ok(evidence.final.logBytes>16*1024*1024);
 assert.ok(!actions.includes('session.plans'),'UI requested historical reconstruction');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify(evidence));
}catch(error){primary=error;console.error(log);throw error}
finally{
 try{if(process.env.AMPLIFIER_TEST_ARTIFACTS){
  await mkdir(process.env.AMPLIFIER_TEST_ARTIFACTS,{recursive:true});
  await writeFile(process.env.AMPLIFIER_TEST_ARTIFACTS+'/last-reported-plan.json',JSON.stringify({...evidence,errors},null,2));
 }}finally{
  await finishFixture({label:'Last reported Plan',close:[['browser',()=>browser?.close()]],stop:()=>stop?.(),originalError:primary});
  await rm(home,{recursive:true,force:true});
 }
}