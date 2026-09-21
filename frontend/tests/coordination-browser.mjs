import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const python=process.env.AMPLIFIER_TEST_PYTHON||root+'/.venv/bin/python';
const fixture=spawn(python,[root+'/tests/fixtures/coordination_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,page;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timeout=setTimeout(()=>reject(Error('startup')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timeout);resolve(row.url)}}catch{}});fixture.once('error',reject)});
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1100,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.setDefaultTimeout(10000);
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Preserve this unsent draft');
 await page.getByRole('button',{name:'Chat details',exact:true}).click();
 await page.getByRole('button',{name:'Tasks and workers',exact:true}).click();
 const first=page.getByRole('region',{name:'Worker: First worker'}),second=page.getByRole('region',{name:'Worker: Second worker'});
 await first.getByRole('checkbox').check();
 await second.getByRole('checkbox').check();
 const current=await page.request.get(url+'/fixture').then(response=>response.json());
 const emit=data=>page.request.post(url+'/fixture/emit',{data});
 await emit({id:'worker-b',status:'idle',report:'Second worker finished its first report.',reportId:'b-report-1'});
 await expect(second.locator('[data-report-id="b-report-1"]')).toHaveCount(1);
 await first.getByText('Follow up',{exact:true}).click();
 await first.getByLabel('Follow-up for First worker').fill('Investigate the remaining check');
 await first.getByRole('button',{name:'Send follow-up',exact:true}).click();
 await expect(page.getByText('Follow-up accepted.',{exact:true})).toBeVisible();
 let observed=await page.request.get(url+'/fixture').then(response=>response.json());
 assert.deepEqual(observed.messages.map(({sessionId,workerId,text})=>({sessionId,workerId,text})),[{sessionId:current.other,workerId:'worker-a',text:'Investigate the remaining check'}]);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),current.selected);
 await expect(composer).toHaveValue('Preserve this unsent draft');
 // Reconnect with retained cursors: one stable DOM report, no repeated command.
 await page.reload();
 await expect(second.locator('[data-report-id="b-report-1"]')).toHaveCount(1);
 await expect(second.getByRole('checkbox')).toBeChecked();
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 let interruptedRead=false;
 await page.route('**/api/actions',async route=>{
  if(!interruptedRead&&route.request().method()==='POST'&&route.request().postDataJSON()?.action==='coordination.wait'){interruptedRead=true;await route.abort('connectionreset')}else await route.continue();
 });
 await page.getByRole('button',{name:'Chat details',exact:true}).click();
 await page.getByRole('button',{name:'Tasks and workers',exact:true}).click();
 await expect(page.getByText(/Waiting to reconnect:/)).toBeVisible();
 await emit({id:'worker-b',status:'idle',report:'Second worker finished its first report.',reportId:'b-report-1'});
 await emit({id:'worker-a',status:'idle',report:'First worker follow-up report.',reportId:'a-report-2'});
 await expect(first.locator('[data-report-id="a-report-2"]')).toHaveCount(1);
 await expect(second.locator('[data-report-id="b-report-1"]')).toHaveCount(1);
 // An active long wait must not occupy the shared command queue.
 await first.getByRole('button',{name:'Interrupt',exact:true}).click();
 await expect(first.getByText('Outcome unknown · Needs attention',{exact:true})).toBeVisible();
 observed=await page.request.get(url+'/fixture').then(response=>response.json());
 assert.equal(observed.messages.length,1);assert.equal(observed.stops.length,1);
 await page.locator('.a-dialog').evaluate(element=>element.scrollTop=0);
 await page.screenshot({path:'/tmp/amplifier-coordination-desktop.png',animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.locator('.a-dialog').evaluate(element=>element.scrollWidth<=element.clientWidth),true);
 await page.screenshot({path:'/tmp/amplifier-coordination-mobile.png',animations:'disabled'});
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await expect(composer).toHaveValue('Preserve this unsent draft');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),current.selected);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,actualService:true,twoTargets:true,followupExactTarget:true,cursorReconnect:true,failedReadReconnect:true,noDuplicateReports:true,noRepeatedSubmission:true,interruptWhileWaiting:true,selectionAndDraftPreserved:true,mobileNoOverflow:true,providerCalls:false}));
}catch(error){await page?.screenshot({path:"/tmp/amplifier-coordination-failure.png"});console.error((await page?.locator("body").innerText())?.slice(-5000));throw error}finally{await browser?.close();fixture.kill()}
