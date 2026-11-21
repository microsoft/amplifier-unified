// Actual Unified/browser + official ACP SDK peer; synthetic requests only.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {fixturePython} from './fixture-python.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'/.venv/bin/python'),[root+'/tests/fixtures/external_multiparty_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'External approval fixture'});
const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS,report={passed:false,decisions:[]};let browser,page;
try{
 const {url,workspace}=await new Promise((resolve,reject)=>{
  let output='';const timeout=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timeout);resolve(row)}}catch{}});
  fixture.once('error',reject);fixture.once('exit',code=>reject(Error('Fixture exited '+code)));
 });
 browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{
  if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
  const raw=request.postDataJSON(),body=raw.action==='shell.command'?{...raw,action:raw.args.action,args:raw.args.args}:raw;
  if(body.action==='approval.respond')report.decisions.push(body);
 });
 await page.goto(url);await page.waitForFunction(()=>window.amplifier?.dispatch);
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const created=await action('session.create',{workspace,externalAgentId:'legacy-alpha',title:'External approval checks'});
 assert.equal(created.accepted,true);const sid=created.result.sessionId;
 await action('session.select',{id:sid});
 const observe=()=>page.request.get(url+'/fixture/participants').then(response=>response.json());
 const idle=()=>expect.poll(async()=>(await observe()).sessions.find(row=>row.id===sid)?.status).toBe('idle');
 const send=async text=>{await page.getByRole('textbox',{name:'Message Amplifier',exact:true}).fill(text);await page.getByRole('button',{name:'Send message',exact:true}).click()};
 for(const [label,id] of [['Allow once','allow-this'],['Always allow matching request','remember-this'],['Deny','deny-this']]){
  await send('permission-options');
  const card=page.locator('[data-approval-id^="external:"]');
  await expect(card).toHaveCount(1);await expect(card.getByText('Requested by Legacy-Alpha.',{exact:true})).toBeVisible();
  await expect(card).toContainText('reviewed.txt');await expect(card).toContainText('Write the same reviewed file.');
  assert.equal(await card.getByRole('button').count(),3);
  const identity=await card.getAttribute('data-approval-id');
  await page.reload();await expect(page.locator(`[data-approval-id="${identity}"]`)).toHaveCount(1);
  assert.equal((await observe()).peers['legacy-alpha'].filter(row=>row.kind==='permission').length,report.decisions.length);
  await page.locator(`[data-approval-id="${identity}"]`).getByRole('button',{name:label,exact:true}).click();
  await idle();
  assert.deepEqual((await observe()).peers['legacy-alpha'].filter(row=>row.kind==='permission').at(-1).outcome,{outcome:'selected',optionId:id});
 }
 await send('permission-cancel-request');
 const card=page.locator('[data-approval-id^="external:"]');await expect(card).toHaveCount(1);
 await page.setViewportSize({width:390,height:844});
 await expect(card.getByRole('button',{name:'Allow once',exact:true})).toBeVisible();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
 if(artifacts){await mkdir(artifacts,{recursive:true});await page.screenshot({path:join(artifacts,'pending-mobile.png'),fullPage:true});}
 assert.equal((await page.request.post(url+'/fixture/withdraw-legacy-alpha')).ok(),true);
 await expect(card).toHaveCount(0);
 assert.equal((await observe()).sessions.find(row=>row.id===sid).status,'working');
 assert.equal((await page.request.post(url+'/fixture/release-legacy-alpha')).ok(),true);
 await idle();
 const final=await observe(),session=final.sessions.find(row=>row.id===sid);
 assert.equal(session.approvals.at(-1).status,'expired');
 assert.equal(final.peers['legacy-alpha'].filter(row=>row.kind==='permission-cancelled').length,1);
 assert.equal(final.peers['legacy-alpha'].filter(row=>row.kind==='permission').length,3);
 await send('permission-options');await expect(card).toHaveCount(1);
 await page.getByRole('button',{name:'Stop response',exact:true}).click();
 await expect.poll(async()=>(await observe()).sessions.find(row=>row.id===sid)?.status).toBe('stopped');
 await expect(card).toHaveCount(0);
 const stopped=await observe();
 assert.equal(stopped.sessions.find(row=>row.id===sid).approvals.at(-1).status,'expired');
 assert.equal(stopped.peers['legacy-alpha'].filter(row=>row.kind==='cancel').length,1);
 assert.equal(stopped.peers['legacy-alpha'].filter(row=>row.kind==='permission').length,3);
 assert.equal(report.decisions.length,3);assert.deepEqual(stopped.nativeStarts,[]);assert.deepEqual(stopped.nativeSends,[]);
 assert.deepEqual(errors,[]);report.passed=true;report.final=stopped;
 console.log(JSON.stringify({passed:true,decisions:3,reloadWithoutReplay:true,withdrawalWithoutDecision:true,stopWithoutDecision:true,mobileNoOverflow:true}));
}catch(error){report.error=error.stack||String(error);throw error}
finally{
 try{if(artifacts){await mkdir(artifacts,{recursive:true});await writeFile(join(artifacts,'result.json'),JSON.stringify(report,null,2));if(page&&!report.passed)await page.screenshot({path:join(artifacts,'failure.png'),fullPage:true}).catch(()=>{});}}
 finally{await finishFixture({label:'External approval fixture',close:[['Browser',()=>browser?.close()]],stop:stopFixture})}
}
