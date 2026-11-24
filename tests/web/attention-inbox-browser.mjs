// Real shared actions in a disposable application; no accounts or provider calls.
import {spawn} from 'node:child_process';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'Attention inbox fixture'});
let browser,vite,failure;const errors=[];
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/attention-inbox';
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.port){clearTimeout(timer);resolve(value)}}catch{}})});
 const target=`http://127.0.0.1:${ready.port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const response=await fetch(target+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(response.status,200);return response.json()};
 vite=await createServer({configFile:false,root:root+'.build/web-client',server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}}});await vite.listen();
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 const context=await browser.newContext({viewport:{width:1280,height:920},extraHTTPHeaders:headers});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 const initial=await control({op:'many',count:13}),sid=initial.alpha,other=initial.beta;
 await page.goto(vite.resolvedUrls.local[0]);await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 const options=page.getByRole('button',{name:'App options',exact:true}),toggle=page.locator('#navigation-toggle');
 // Activity lives in the footer's menu. A hidden menu item does not mean the
 // navigation rail is closed (desktop can already have a Close toggle).
 if(await toggle.getAttribute('aria-expanded')==='false')await toggle.click();
 await expect(options).toBeVisible();await options.click();
 await page.getByRole('group',{name:'App options',exact:true})
  .getByRole('button',{name:/^Activity(?: \d+ (?:unread items|items needing attention))?$/}).click();
 const inbox=page.getByRole('region',{name:'Activity to review',exact:true});await expect(inbox).toBeVisible();
 const selection=()=>page.evaluate(()=>{
  const state=window.amplifier.getState();
  return {sessionId:state.selectedSessionId,workspaceId:state.selectedWorkspaceId};
 });
 const selectedBefore=await selection(),navigationRequests=[];
 page.on('request',request=>{
  if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
  const body=request.postDataJSON(),name=body?.action==='shell.command'?body.args?.action:body?.action;
  if(['session.select','session.draft','workspace.select','conversation.send'].includes(name))navigationRequests.push(name);
 });
 // Release news is quiet history; it needs no mark-read maintenance.
 const initialReview=inbox.getByRole('button',{name:'Mark all reviewed',exact:true});
 if(await initialReview.isEnabled())await initialReview.click();
 await expect(inbox.getByText('All caught up.',{exact:true})).toBeVisible();
 await expect(initialReview).toBeDisabled();
 assert.deepEqual(await selection(),selectedBefore);
 const sessions=Object.fromEntries(initial.state.sessions.filter(s=>s.id.startsWith('library-')).map((s,i)=>[s.id,{completion:{id:'g1',at:100+i}}]));
 await control({op:'patch',sessions});
 await page.waitForFunction(()=>window.amplifier?.getState()?.attention?.badgeUnread===0);
 await expect(inbox.getByRole('region',{name:'Finished responses',exact:true}).locator('.a-attention-item')).toHaveCount(10);
 await inbox.getByRole('button',{name:/Show more responses/}).click();
 await expect(inbox.getByRole('region',{name:'Finished responses',exact:true}).locator('.a-attention-item')).toHaveCount(13);
 await page.evaluate(sid=>window.amplifier.dispatch('question.create',{sessionId:sid,prompt:'Should I continue?',required:true,dependency:'Next step'}),sid);
 await control({op:'patch',sessions:{[other]:{error:'Synthetic failed request',errorAt:2,approvals:[{id:'pending-approval',status:'pending',tool:'bash',title:'Run a command'}]}}});
 await expect(inbox.getByRole('region',{name:'To review',exact:true}).locator('.a-attention-item')).toHaveCount(3);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().attention.badgeUnread)).toBe(3);
 const preference=inbox.getByRole('checkbox',{name:'Count finished responses in the app badge',exact:true});
 await preference.check();
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().notificationSettings.completionBadge)).toBe(true);
 await expect(preference).toBeEnabled();
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().attention.badgeUnread)).toBe(16);
 await page.reload();await expect(preference).toBeChecked();
 assert.deepEqual(await selection(),selectedBefore,'Reload retains this browser’s selected conversation and workspace');
 await preference.uncheck();await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().attention.badgeUnread)).toBe(3);
 await mkdir(output,{recursive:true});await page.screenshot({path:join(output,'attention-inbox-desktop.png')});
 // A failed receipt leaves all notices available to retry.
 let fail=true;await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(fail&&body.action==='attention.read'){fail=false;await route.fulfill({status:503,json:{error:'Synthetic offline failure'}})}else await route.continue()});
 await inbox.getByRole('button',{name:'Mark responses reviewed',exact:true}).click();
 await expect(inbox.getByRole('alert')).toContainText('Synthetic offline failure');
 await expect(inbox.getByRole('region',{name:'Finished responses',exact:true}).locator('.a-attention-item')).toHaveCount(10);
 await inbox.getByRole('button',{name:'Mark responses reviewed',exact:true}).click();
 await expect(inbox.getByRole('region',{name:'Finished responses',exact:true})).toHaveCount(0);
 await expect(inbox.getByRole('region',{name:'To review',exact:true}).locator('.a-attention-item')).toHaveCount(3);
 assert.deepEqual(await selection(),selectedBefore,'Group review does not navigate this browser');
 const peer=await context.newPage();await peer.goto(vite.resolvedUrls.local[0]);
 await peer.waitForFunction(()=>window.amplifier?.getState()?.attention?.badgeUnread===3);
 await inbox.getByRole('button',{name:'Mark all reviewed',exact:true}).click();
 await expect(inbox.getByText('All caught up.',{exact:true})).toBeVisible();
 await peer.waitForFunction(()=>window.amplifier.getState().attention.badgeUnread===0);
 await page.reload();await expect(inbox.getByText('All caught up.',{exact:true})).toBeVisible();
 assert.deepEqual(await selection(),selectedBefore,'Bulk review and reload retain the client-local selection');
 await inbox.getByRole('button',{name:'Show reviewed items',exact:true}).click();
 await expect(inbox.getByRole('region',{name:'Reviewed items',exact:true})).toBeVisible();
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:join(output,'attention-inbox-mobile.png')});
 assert.ok(await inbox.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
 const after=await control({op:'patch'});
 assert.equal(after.state.sessions.find(s=>s.id===sid).questions[0].status,'pending');
 assert.equal(after.state.sessions.find(s=>s.id===other).approvals[0].status,'pending');
 assert.equal(after.state.sessions.find(s=>s.id===other).error,'Synthetic failed request');
 assert.equal(after.state.selectedSessionId,initial.state.selectedSessionId,'Reviewing notices must not navigate to their conversations');
 assert.deepEqual(await selection(),selectedBefore);assert.deepEqual(navigationRequests,[],'Reviewing notices sends no selection or conversation input');
 assert.equal(after.runtimeStarts,initial.runtimeStarts);assert.equal(after.runtimeSends,initial.runtimeSends);
 assert.deepEqual(errors,[]);
 console.log('PASS: bounded completion list; quiet/persistent badge setting; retry; group and bulk review without navigation; cross-client read state; pending questions/approvals and history retained; mobile layout.');
}catch(error){failure=error;throw error}
finally{await finishFixture({label:'Attention inbox fixture',close:[['browser',()=>browser?.close()],['Vite',()=>vite?.close()]],stop:stopFixture,originalError:failure})}
