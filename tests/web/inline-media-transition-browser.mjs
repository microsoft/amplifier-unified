import {showFixtureConversation} from './browser-chat-pages.mjs';
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url)).replace(/\/$/,''),out=process.env.AMPLIFIER_TEST_ARTIFACTS||process.env.EVIDENCE_DIR||root+'/output/media-transition';
await mkdir(out,{recursive:true});
let log='',browser,page;const requests=[],transitions=[],pending=new Set(),bodyReads=new Set(),responseErrors=[];
const responseStatus=row=>{
 if(row.network==='pending'||row.bodyPending)return 'pending';
 if(row.network==='failed')return row.failure?.errorText==='net::ERR_ABORTED'?'cancelled':'network-error';
 return row.httpStatus===200&&Object.hasOwn(row,'body')&&!row.bodyError?'completed':'response-error';
};
// A cancelled body is neither success nor a malformed completed response.
assert.equal(responseStatus({network:'failed',failure:{errorText:'net::ERR_ABORTED'},bodyPending:true}),'pending');
assert.equal(responseStatus({network:'failed',failure:{errorText:'net::ERR_ABORTED'},bodyError:'body cancelled'}),'cancelled');
assert.equal(responseStatus({network:'failed',failure:{errorText:'net::ERR_CONNECTION_RESET'}}),'network-error');
assert.equal(responseStatus({network:'finished',httpStatus:200,bodyError:'Invalid JSON'}),'response-error');
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'/.venv/bin/python',['-u',root+'/tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','pipe']});
fixture.stderr.on('data',data=>log+=data);
const exited=new Promise(resolve=>fixture.once('exit',resolve));
try{
 const started=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout '+log)),30000);fixture.stdout.on('data',data=>{output+=data;const line=output.split('\n').find(line=>line.startsWith('{"port":'));if(line){clearTimeout(timer);resolve(JSON.parse(line))}});fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code+log))})});
 const base=`http://127.0.0.1:${started.port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const response=await fetch(base+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(response.ok,true,await response.clone().text());return response.json()};
 await control({op:'interim-start'});await control({op:'interim-finish'});const seeded=await control({op:'interim-media'});
 const messages=seeded.state.sessions.find(row=>row.id===started.alpha).messages;
 const assistants=messages.filter(row=>row.role==='assistant'),attached=assistants.filter(row=>row.text==='Checking the first detail.');
 assert.equal(assistants.length,4);assert.equal(attached.length,1);const messageId=attached[0].id;
 const busy=async value=>control({op:'patch',sessions:{[started.alpha]:{configurationBusy:value}}});
 const initialBusy=await busy(true);
 browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}: {})});
 const context=await browser.newContext({viewport:{width:1440,height:1100},extraHTTPHeaders:headers});
 await context.addInitScript(()=>{
  const original=window.fetch;window.mediaDiscoveryWitness=[];
  window.fetch=function(input,options){
   const address=new URL(typeof input==='string'?input:input.url,location.href);
   if(address.origin===location.origin&&address.pathname==='/api/outputs/media'){
    const state=window.amplifier?.getState(),sid=address.searchParams.get('sessionId');
    window.mediaDiscoveryWitness.push({url:address.href,sessionId:sid,messageId:address.searchParams.get('messageId'),
     revision:state?.revision,busy:state?.sessions?.find(row=>row.id===sid)?.configurationBusy===true});
   }
   return original.apply(this,arguments);
  };
 });
 await context.addCookies([{name:'amplifier_unified_session',value:started.cookie,url:base,httpOnly:true,sameSite:'Strict'}]);
 page=await context.newPage();const errors=[],rows=new Map();page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{
  const address=new URL(request.url());if(address.pathname!=='/api/outputs/media')return;
  const row={url:request.url(),sessionId:address.searchParams.get('sessionId'),messageId:address.searchParams.get('messageId'),network:'pending',status:'pending'};
  rows.set(request,row);requests.push(row);pending.add(request);
 });
 page.on('response',response=>{
  const request=response.request(),row=rows.get(request);if(!row)return;
  row.httpStatus=response.status();row.bodyPending=true;bodyReads.add(request);
  response.json().then(body=>{row.body=body},error=>{row.bodyError=error.message})
   .finally(()=>{row.bodyPending=false;bodyReads.delete(request);row.status=responseStatus(row)});
 });
 page.on('requestfinished',request=>{const row=rows.get(request);if(row){row.network='finished';pending.delete(request);row.status=responseStatus(row)}});
 page.on('requestfailed',request=>{const row=rows.get(request);if(row){row.network='failed';row.failure=request.failure();pending.delete(request);row.status=responseStatus(row)}});
 const applied=async(receipt,value)=>{
  await page.waitForFunction(({sid,revision,value})=>{
   const state=window.amplifier?.getState();return state?.selectedSessionId===sid&&state.revision>=revision&&
    state.sessions.find(row=>row.id===sid)?.configurationBusy===value;
  },{sid:started.alpha,revision:receipt.revision,value});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 };
 const witness=()=>page.evaluate(()=>window.mediaDiscoveryWitness);
 const settled=async()=>{
  // Network termination does not settle a still-running response.json reader.
  await expect.poll(()=>pending.size+bodyReads.size).toBe(0);
  responseErrors.splice(0,responseErrors.length,...requests.filter(row=>
   !['completed','cancelled'].includes(responseStatus(row))).map(row=>({url:row.url,status:responseStatus(row),failure:row.failure,bodyError:row.bodyError,httpStatus:row.httpStatus})));
  assert.deepEqual(responseErrors,[]);
  assert.ok((await witness()).every(row=>row.busy===false),'No discovery may dispatch while the browser observes busy');
 };
 let qualified;
 const discover=async label=>{
  const before=requests.length,receipt=await busy(false);await applied(receipt,false);
  await expect.poll(()=>requests.slice(before).some(row=>row.sessionId===started.alpha&&
   row.messageId===messageId&&row.status==='completed'&&row.httpStatus===200)).toBe(true);
  const row=requests.slice(before).find(row=>row.sessionId===started.alpha&&row.messageId===messageId&&row.status==='completed'&&row.httpStatus===200);
  assert.equal(row.body.items.length,1);const item=row.body.items[0];
  assert.equal(item.sessionId,started.alpha);assert.equal(item.title,'Retained interim animation');
  const identity={id:item.id,version:item.version,sha256:item.sha256,sessionId:item.sessionId};
  if(qualified)assert.deepEqual(identity,qualified);else qualified=identity;
  await expect(page.locator(`figure[data-media-id="${item.id}"]`)).toHaveAttribute('data-media-state','ready');
  await settled();transitions.push({label,revision:receipt.revision,start:before,end:requests.length,identity});
 };
 const retainBusy=async label=>{
  const before=requests.length,figure=await page.locator(`figure[data-media-id="${qualified.id}"]`).elementHandle();
  const receipt=await busy(true);await applied(receipt,true);
  const afterApplied=requests.length,transitionWitness=await witness();
  transitions.push({label,revision:receipt.revision,before,afterApplied,
   transitionRequests:requests.slice(before,afterApplied),dispatchWitness:transitionWitness.slice(before)});
  await expect(page.getByText('Retained interim animation',{exact:true})).toBeVisible();
  assert.equal(await figure.evaluate(node=>node.isConnected),true,'Qualified figure remains mounted during busy');
  await page.waitForTimeout(250);
  assert.equal(requests.length,afterApplied,'qualified media remains without new discovery after busy is applied');
  assert.ok((await witness()).every(row=>row.busy===false),'Transition requests must not dispatch under observed busy');
  await figure.dispose();
 };
 await page.goto(base);await showFixtureConversation(page);await expect(page.getByText('The final next step.',{exact:true})).toBeVisible();
 await applied(initialBusy,true);
 await page.waitForTimeout(250);assert.equal(requests.length,0,'ordinary messages defer while busy');
 await discover('ordinary-ready');const ordinaryCount=requests.length;
 await retainBusy('ordinary-busy');
 await discover('ordinary-revalidated');
 await page.waitForFunction(()=>{const a=window.amplifier?.getState()?.appearance;return a?.local?.reported===true&&!a.local.error});
 await openSettingsPage(page,'appearance');
 const advanced=page.locator('.a-appearance-more');
 if(!await advanced.evaluate(node=>node.open))await advanced.locator(':scope > summary').click();
 const collapse=page.getByRole('checkbox',{name:'Collapse earlier updates',exact:true});
 await expect(collapse).toBeVisible();await collapse.check();
 await page.waitForFunction(()=>window.amplifier.getState().appearance?.effective?.collapseInterimReplies===true);
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await expect(page.locator('[data-interim-group]')).toHaveCount(1);await expect(page.getByText('Retained interim animation',{exact:true})).toBeVisible();
 const foldedBusy=await busy(true);await applied(foldedBusy,true);await settled();
 const beforeReload=requests.length;transitions.push({label:'before-folded-reload',dispatchWitness:await witness()});
 await page.reload();await expect(page.locator('[data-interim-group]')).toHaveCount(1);await applied(foldedBusy,true);
 await page.waitForTimeout(250);assert.equal(requests.length,beforeReload,'folded interim output defers while busy');
 await page.screenshot({path:out+'/busy.png'});
 await discover('folded-ready');
 const foldedCount=requests.length;await page.waitForTimeout(300);assert.equal(requests.length,foldedCount,'settled view does not poll');
 await expect(page.getByRole('button',{name:'Retry media metadata'})).toHaveCount(0);
 assert.deepEqual(errors,[]);await page.screenshot({path:out+'/settled.png'});
 await writeFile(out+'/result.json',JSON.stringify({passed:true,ordinaryMessagePath:true,foldedInterimPath:true,initialBusyMetadataRequests:0,foldedBusyMetadataRequests:0,ordinarySettledRequests:ordinaryCount,foldedSettledRequests:foldedCount,qualifiedMediaRetainedDuringBusy:true,messageId,qualified,requests,transitions,dispatchWitness:await witness(),errors,scope:'Built bundled client + real ephemeral AppService/GIF registry; synthetic configurationBusy publication, not real provider bundle switching.'},null,2)+'\n');
 console.log('Browser media transition paths passed');
}catch(error){await writeFile(out+'/failure.txt',String(error.stack)+'\n'+log);await writeFile(out+'/failure-requests.json',JSON.stringify({requests,transitions,responseErrors,dispatchWitness:page?await page.evaluate(()=>window.mediaDiscoveryWitness||[]).catch(()=>[]):[]},null,2));if(page)await page.screenshot({path:out+'/failure.png'}).catch(()=>{});throw error}
finally{if(browser)await browser.close();fixture.kill('SIGTERM');await exited;await writeFile(out+'/fixture.log',log)}
