import {showFixtureConversation} from './browser-chat-pages.mjs';
import {settledAppearance} from './settings-fixture.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {spawn} from 'node:child_process';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'Chat plan fixture'});
let browser,failure;const errors=[];
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.port){clearTimeout(timer);resolve(value)}}catch{}})});
 const target=`http://127.0.0.1:${ready.port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{
  const response=await fetch(target+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});
  const text=await response.text();assert.equal(response.status,200,text.slice(0,4000));
  return JSON.parse(text);
 };
 const {alpha,beta}=await control({op:'heavy',messages:100,otherMessages:1,chars:50,nodes:0});
 await control({op:'chat-plan'});
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:headers});page.on('pageerror',e=>errors.push(e.message));
 // Use the already built exact client served by the real host, not a second
 // development-server rendering path or a new production asset build.
 await page.goto(target);await showFixtureConversation(page);
 const placement=async value=>{
  await openSettingsPage(page,'appearance',{activate:(_label,control,options)=>control.click(options)});
  const advanced=page.locator('details.a-appearance-more');
  if(!await advanced.evaluate(el=>el.open))await advanced.locator(':scope > summary').click();
  const before=await settledAppearance(page),field=page.getByRole('combobox',{name:'Plan location',exact:true});
  await expect(field).toBeVisible();await field.selectOption(value);
  await page.waitForFunction(value=>{
   const a=window.amplifier.getState().appearance;
   return a.effective.planPlacement===value&&a.renderedPreferences.planPlacement===value;
  },value);
  const after=await settledAppearance(page);
  assert.equal(after.namespace,before.namespace);assert.equal(after.generation,before.generation);
  assert.equal(after.activeScope,before.activeScope);assert.equal(after.effective.planPlacement,value);
  await expect(field).toHaveValue(value);await expect(field).toBeEnabled();
  await page.getByRole('button',{name:'Close panel',exact:true}).click();
 };
 const plan=page.getByRole('region',{name:'Plan',exact:true});
 await expect(plan).toHaveCount(1);await expect(plan).toHaveClass(/a-chat-plan-composer/);
 await expect(plan).toContainText('1 of 3 done');await expect(plan).toContainText('Reported in progress · Draft the team update');
 await expect(plan).not.toContainText('Drafting the team update');
 assert.equal(await plan.evaluate(el=>!!el.closest('.a-messages')),false);
 // Expansion renders optimistically. Reload only after its durable save responds.
 const expandedSaved=page.waitForResponse(response=>{if(response.request().method()!=='POST'||!response.url().includes('/api/actions'))return false;const raw=response.request().postDataJSON(),body=raw.action==='shell.command'?raw.args:raw;return body.action==='view.update'&&body.args?.patch?.planExpanded?.[alpha]===true&&response.ok()});
 await plan.locator('summary').click();await expect(plan.getByText('Prepare a document to share',{exact:true})).toBeVisible();
 assert.equal((await(await expandedSaved).json()).accepted,true);
 await page.reload();await expect(plan).toContainText('1 of 3 done');await expect(plan.getByText('Prepare a document to share',{exact:true})).toBeVisible();
 await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),beta);
 await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),alpha);
 await expect(plan.getByText('Prepare a document to share',{exact:true})).toBeVisible();
 assert.equal((await settledAppearance(page)).effective.planPlacement,'composer');
 await placement('inline');
 await expect(plan).toHaveClass(/a-chat-plan-inline/);
 assert.equal(await plan.evaluate(el=>!!el.closest('.a-messages')),true);
 await page.reload();await expect(plan).toHaveClass(/a-chat-plan-inline/);await expect(plan).toContainText('1 of 3 done');
 const reported=await page.evaluate(id=>window.amplifier.getState().sessions.find(row=>row.id===id).execution.plan,alpha);
 const patchStatus=async status=>{
  const receipt=await control({op:'patch',sessions:{[alpha]:{status}}});
  await page.waitForFunction(([id,status,revision])=>{
   const state=window.amplifier.getState();
   return state.revision>=revision&&state.sessions.find(row=>row.id===id)?.status===status;
  },[alpha,status,receipt.revision]);
  assert.deepEqual(await page.evaluate(id=>window.amplifier.getState().sessions.find(row=>row.id===id).execution.plan,alpha),reported);
 };
 await patchStatus('interrupted');
 await expect(plan).toContainText('Reported in progress · Draft the team update');
 await expect(plan).not.toContainText('Outcome unknown');
 await expect(plan).not.toContainText('Drafting the team update');
 await placement('composer');
 await patchStatus('paused');
 await expect(plan).toContainText('Reported in progress · Draft the team update');
 await expect(plan).not.toContainText('Paused ·');
 await placement('active');
 await expect(plan).toHaveCount(0);
 await control({op:'chat-plan',status:'working'});await expect(plan).toHaveClass(/a-chat-plan-composer/);
 await patchStatus('idle');await expect(plan).toHaveCount(0);
 await placement('overview');
 await page.evaluate(()=>window.amplifier.dispatch('canvas.visibility',{open:true}));
 await expect(page.locator('#workspace-canvas')).toHaveAttribute('data-navigation','compact');
 await page.locator('#workspace-canvas').getByRole('button',{name:'Chat overview',exact:true}).click();
 await expect(plan).toHaveClass(/a-chat-plan-overview/);
 await expect(plan.getByText('Prepare a document to share',{exact:true})).toBeVisible();
 await page.evaluate(()=>window.amplifier.dispatch('canvas.visibility',{open:false}));
 await placement('composer');
 await page.setViewportSize({width:390,height:844});
 await expect(page.locator('.a-chat-plan-composer')).toBeVisible();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 const output=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/chat-plan';await mkdir(output,{recursive:true});
 await page.screenshot({path:join(output,'chat-plan-app-mobile.png')});
 await control({op:'chat-plan',items:[],status:'idle'});await expect(plan).toHaveCount(0);
 assert.deepEqual(errors,[]);
 console.log('Plan passed: ordinary successful root report, bounded history, reload, persisted placement, status-independent checklist, mobile, and cleared list.');
}catch(error){failure=error;throw error}
finally{await finishFixture({label:'Chat plan fixture',close:[['browser',()=>browser?.close()]],stop:stopFixture,originalError:failure})}
