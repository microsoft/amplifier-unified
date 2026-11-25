import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir, writeFile} from 'node:fs/promises';
import {dirname, join} from 'node:path';
import {createInterface} from 'node:readline';
import {fileURLToPath} from 'node:url';
import {chromium, expect} from '@playwright/test';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {ownFixture} from './fixture-lifecycle.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const installed=process.env.AMPLIFIER_TEST_PACKAGE_ROOT;
const python=process.env.AMPLIFIER_TEST_PYTHON||join(root,'.venv/bin/python');
const fixture=spawn(python,['-u',join(root,'tests/fixtures/browser_detail_server.py'),installed?dirname(installed):root],
 {stdio:['ignore','pipe','inherit']});
const stop=ownFixture(fixture);
const readiness=createInterface({input:fixture.stdout});
const reads=[],errors=[],checks=[];
let disclosing=false;
const markers=['RECORDED-REQUEST','FULL-RECORDING-END','RECORDED-RESPONSE','FULL-RESPONSE-END'];
const transfers=[];
const output=process.env.AMPLIFIER_TEST_OUTPUT_DIR||process.env.AMPLIFIER_TEST_ARTIFACTS;
let browser,page;
async function screenshot(name){
 if(!output)return;
 await mkdir(output,{recursive:true});
 await page.screenshot({path:join(output,'grouped-work-'+name+'.png'),animations:'disabled'});
}
try{
 const [line]=await once(readiness,'line',{signal:AbortSignal.timeout(30000)});
 const {port,cookie}=JSON.parse(line);
 const base='http://127.0.0.1:'+port;
 const headers={Authorization:'Bearer fixture-detail-token','Content-Type':'application/json'};
 async function control(body){
  const response=await fetch(base+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});
  // Fixture-only endpoint, synthetic input; retain a bounded failure fact before
  // Chromium starts rather than losing the cause behind an HTTP status.
  if(!response.ok){
   const fact={op:body.op,status:response.status,body:(await response.text()).slice(0,2000)};
   if(output){await mkdir(output,{recursive:true});await writeFile(join(output,'fixture-control-error.json'),JSON.stringify(fact,null,2)+'\n')}
   assert.fail('Fixture control failed: '+JSON.stringify(fact));
  }
  return response.json();
 }
 await control({op:'bounded-provider-records',retention:true,largeResponse:true});
 const {alpha,beta}=await control({op:'grouped-long-chat'});
 browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH});
 const context=await browser.newContext({viewport:{width:1360,height:950},extraHTTPHeaders:headers});
 await context.addCookies([{name:'amplifier_unified_session',value:cookie,url:base,httpOnly:true,sameSite:'Strict'}]);
 function record(request){
  const url=new URL(request.url()),field=url.searchParams.get('field');
  if(url.pathname==='/api/conversation/provider-recording')reads.push('recording:'+field);
  if(url.pathname==='/api/conversation/detail'&&['request','response'].includes(field)&&Number(url.searchParams.get('offset'))===0)reads.push('detail:'+field);
 }
 context.on('request',record);
 // Count explicit field reads above and inspect the JSON and live frames that
 // supplied the list. An inline recording could otherwise bypass both routes.
 let unexpectedBody=false;
 const inspect=text=>{if(markers.some(marker=>text.includes(marker)))unexpectedBody=true};
 context.on('response',response=>{
  if(!disclosing&&response.url().startsWith(base+'/api/')&&response.headers()['content-type']?.includes('application/json'))
   transfers.push(response.text().then(inspect,()=>{}));
 });
 async function open(){
  const tab=await context.newPage();tab.on('pageerror',error=>errors.push(error.message));
  tab.on('websocket',socket=>socket.on('framereceived',frame=>{
   if(!disclosing)inspect(String(frame.payload));
  }));
  await tab.goto(base);await showFixtureConversation(tab);return tab;
 }
 page=await open();
 const group=()=>page.locator('[data-activity-group]');
 const local=()=>group().locator('[data-local-activity=true] > button');
 const peer=()=>group().locator('.a-peer-run-summary');
 const model=()=>group().locator('[data-kind=llm]').filter({hasText:'bounded-recording'}).first();
 const inspector=()=>page.locator('#work-step-inspector');
 async function expand(button){await expect(button).toBeVisible();if(await button.getAttribute('aria-expanded')!=='true')await button.click()}
 async function checkList(){
  await expand(local());
  const step=model();await expect(step).toBeVisible();
  for(const fact of ['Returned text','test','bounded-recording','In 2,500','Out 103','Cached 2,000 (80%)','$0.0048 estimated'])
   await expect(step).toContainText(fact);
  assert.match(await step.innerText(),/\d+(?:\.\d+)?\s*(ms|s)\b/);
  const body=await page.evaluate(()=>JSON.stringify(window.amplifier.getState()));
  for(const marker of markers)
   assert.ok(!body.includes(marker),'Captured body leaked into client state');
  assert.equal(reads.length,0,'Listing model metrics must be metadata-only');
 }
 await expect(group()).toHaveCount(1);
 await expect(group()).toHaveAttribute('data-running','true');
 await expect(peer()).toContainText('3 updates');
 await expect(page.getByRole('button',{name:'Load earlier conversation',exact:true})).toBeVisible();
 await checkList();await model().scrollIntoViewIfNeeded();await screenshot('working');checks.push('Live partial transcript shows local steps, peer count and model metrics before payload reads');
 await model().locator('button.a-execution-action-line').click();
 await expect(inspector()).toBeVisible();assert.equal(reads.length,0);
 await screenshot('model-inspector');
 await control({op:'grouped-long-chat',complete:true});
 await expect(group()).toHaveAttribute('data-running','false');
 await expect(page.getByText('The grouped work is complete.',{exact:true})).toBeVisible();
 await expect(inspector()).toBeVisible();
 await expect(inspector().getByRole('button',{name:/^View request/})).toBeVisible();
 assert.equal(reads.length,0);checks.push('Selected model survives live completion without reading its recording');
 await inspector().getByRole('button',{name:'Close step details'}).click();
 await page.evaluate(async id=>{await window.amplifier.dispatch('session.select',{id})},beta);
 await page.close();
 page=await open();
 await page.evaluate(async id=>{await window.amplifier.dispatch('session.select',{id})},alpha);
 await expect(group().locator('.a-activity-summary')).toContainText(/Worked for|Recent work/);
 await expect(page.getByText('The grouped work is complete.',{exact:true})).toBeVisible();
 await expect(group().locator('.a-activity-summary')).toHaveAttribute('aria-expanded','false');
 await group().locator('.a-activity-summary').scrollIntoViewIfNeeded();await screenshot('reopened');
 await expand(group().locator('.a-activity-summary'));
 await checkList();checks.push('Reopening a completed long chat restores grouping and metadata with zero payload reads');
 await expand(peer());
 await group().locator('[data-inspect-peer]').first().click();
 await expect(inspector()).toContainText('Contributing work update 0');
 await inspector().getByRole('button',{name:'Close step details'}).click();
 await group().locator('[data-kind=tool] button.a-execution-action-line').first().click();
 await expect(inspector()).toBeVisible();
 await inspector().getByRole('button',{name:'Close step details'}).click();
 await model().locator('button.a-execution-action-line').click();
 assert.equal(reads.length,0);checks.push('Peer and tool inspectors open independently of the model recording');
 await Promise.all(transfers);assert.equal(unexpectedBody,false,'Model recording leaked before disclosure');
 disclosing=true;
 await inspector().getByRole('button',{name:/^View request/}).click();
 const full=inspector().getByRole('button',{name:'Show full recorded request',exact:true});
 await expect(full).toBeVisible();assert.deepEqual(reads,['detail:request']);
 const recorded=page.waitForResponse(res=>new URL(res.url()).pathname==='/api/conversation/provider-recording');
 await full.click();
 const body=await (await recorded).json();
 assert.equal(body.payload.end,'FULL-RECORDING-END');
 const documentView=inspector().getByRole('group',{name:'Recorded request payload'});
 await documentView.getByRole('button').first().click();
 await expect(documentView).toContainText('FULL-RECORDING-END');
 assert.equal(body.capture.truncated,false);assert.equal(body.payload.largeDiagnostic.length,1_100_000);
 assert.deepEqual(reads,['detail:request','recording:request']);
 await inspector().getByRole('button',{name:/^View response/}).click();
 await expect.poll(()=>reads).toContain('detail:response');
 assert.equal(reads.filter(read=>read.includes('response')).length,1);
 const responseRecording=page.waitForResponse(res=>new URL(res.url()).pathname==='/api/conversation/provider-recording');
 await inspector().getByRole('button',{name:'Show full recorded response',exact:true}).click();
 const responseBody=await (await responseRecording).json();
 assert.equal(responseBody.payload.largeDiagnostic.length,1_100_000);
 assert.equal(responseBody.capture.truncated,false);
 const responseView=inspector().getByRole('group',{name:'Recorded response payload'});
 await responseView.getByRole('button').first().click();
 await expect(responseView).toContainText('FULL-RESPONSE-END');
 assert.deepEqual(reads,['detail:request','recording:request','detail:response','recording:response']);
 checks.push('Both 1.1 MB recordings open independently on explicit action and render their final fields');
 await page.setViewportSize({width:390,height:844});
 await expect(page.getByRole('dialog',{name:'Step details'})).toBeVisible();
 assert.ok(await inspector().evaluate(el=>el.getBoundingClientRect().width<=innerWidth&&el.scrollWidth<=el.clientWidth+1));
 await page.keyboard.press('Escape');await expect(inspector()).toHaveCount(0);
 await model().scrollIntoViewIfNeeded();
 const narrow=await model().locator('.a-model-call-metrics > span').evaluateAll(facts=>facts.map(el=>{
  const box=el.getBoundingClientRect();
  return {label:el.textContent,inViewport:box.left>=0&&box.right<=innerWidth+1&&box.width>0&&box.height>0};
 }));
 assert.ok(narrow.length>=4&&narrow.every(fact=>fact.inViewport),JSON.stringify(narrow));
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 assert.deepEqual(errors,[]);
 const receipt={olderMessages:800,activeMessages:804,finishedMessages:805,installedPackage:!!installed,checks,
  narrowMetrics:narrow,payloadReadsBeforeDisclosure:0,inlineRecordingBeforeDisclosure:unexpectedBody,explicitReads:reads,pageErrors:errors};
 if(output){await mkdir(output,{recursive:true});await writeFile(join(output,'grouped-work-browser.json'),JSON.stringify(receipt,null,2)+'\n')}
 console.log(JSON.stringify(receipt));
}finally{
 await browser?.close();readiness.close();await stop();
}
