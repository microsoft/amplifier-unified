// AUTHORED, UNEXECUTED source-unit regression. Run only after ROOT integrates
// the view-qualified backend and builds the matching frontend in its owned DTU.
// Uses real AppService + existing synthetic runtime/MCP server, no paid provider.
// One deliberately broken body response isolates the frontend Retry path.
// Account/grant propagation is mocked in mcp-app-lifecycle.test.mjs; it is NOT
// real account/security acceptance. No fixture capability is fabricated here.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {build} from 'esbuild';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url));
const python=process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python';
const fields=['viewId','resourceId','resourceRevision','generation'];
const target=view=>Object.fromEntries(fields.map(field=>[field,view[field]]));
const surface=revision=>({
 title:'Shared native counter',
 manifest:{version:1,theme:'inherit',stateSchema:{type:'object',properties:{count:{type:'integer'}},required:['count'],additionalProperties:false},events:{set:{schema:{type:'object',properties:{count:{type:'integer'}},required:['count']},updates:{count:'count'}}}},
 initialState:{count:0},
 content:`<!doctype html><h1>Native revision ${revision}</h1><output id="count"></output>
 <input aria-label="Private native input"><button id="add">Add shared count</button>
 <button id="clean">Keep input, mark clean</button><p id="error"></p><script>
 let state;const draw=snapshot=>{state=snapshot.app.state;document.querySelector('#count').textContent=String(state.count)};
 canvasApp.subscribe(draw);canvasApp.ready.then(draw);
 document.querySelector('#add').onclick=()=>canvasApp.emit('set',{count:state.count+1},{commit:canvasApp.getEditVersion()}).catch(error=>document.querySelector('#error').textContent=error.message);
 document.querySelector('#clean').onclick=()=>canvasApp.setDirty(false).catch(error=>document.querySelector('#error').textContent=error.message);
 // Adversarial saved document: a stale token is not a clean acknowledgement.
 addEventListener('message',event=>{
  const data=event.data;if(window.staleTransition&&data?.prepareTransition){
   event.stopImmediatePropagation();document.documentElement.inert=true;
   parent.postMessage({type:'canvas-app',id:canvasApp.getSnapshot().id,channel:data.channel,op:'transition',
    token:'obsolete-token',editVersion:canvasApp.getEditVersion(),blocked:true,quiescent:true,
    localPending:false,queued:0,pending:0,gestures:0,draftPending:false,uncertain:false},'*');
  }
  if(window.staleTransition&&data?.releaseTransition)document.documentElement.inert=false;
 },true);
 </script>`
});
const a2ui={surfaceId:'native-controls',root:'column',components:[
 {id:'column',component:{Column:{children:{explicitList:['button']}}}},
 {id:'button',component:{Button:{child:'label',action:{name:'review'}}}},
 {id:'label',component:{Text:{text:{literalString:'Review native controls'}}}}
]};
let fixture,browser,directory;
try{
 fixture=spawn(python,[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit'],env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
 const url=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{
   const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data.url)}
  }catch{}});
 });
 browser=await chromium.launch({headless:true,chromiumSandbox:true});
 const context=await browser.newContext({viewport:{width:1500,height:1100},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[],requests=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{
  const url=new URL(request.url());
  if(url.pathname==='/api/actions'&&request.method()==='POST')requests.push(request.postDataJSON());
 });
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(client,name,args={},meta)=>client.evaluate(([name,args,meta])=>window.amplifier.dispatch(name,args,meta),[name,args,meta]);
 const state=client=>client.evaluate(()=>window.amplifier.getState());
 const names=await page.evaluate(()=>window.amplifier.getActions().map(row=>row.name||row.action));
 assert.ok(names.includes('canvas.views.follow'),'Backend prerequisite missing: integrate the frozen exact-view follow contract. Do not mock admission or silently skip.');
 await action(page,'session.create');
 await action(page,'conversation.send',{text:'Retain native interactive artifacts for this turn'});
 await expect.poll(async()=>{const s=await state(page);return s.sessions.find(row=>row.id===s.selectedSessionId)?.status}).not.toBe('working');
 const s=await state(page),sid=s.selectedSessionId,messageId=s.sessions.find(row=>row.id===sid).messages.filter(row=>row.role==='assistant').at(-1).id;
 const draft='Do not use native surface events as conversation input';
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill(draft);
 const id=(await action(page,'canvas.apps.create',surface(1))).result.id;
 const inspect=()=>action(page,'canvas.apps.inspect',{id,sessionId:sid}).then(row=>row.result);
 const cas=async()=>{const row=await inspect();return {id,sessionId:sid,expectedRevision:row.app.revision,expectedStateRevision:row.app.stateRevision}};
 await action(page,'canvas.apps.revise',{...await cas(),content:surface(2).content});
 for(const version of [1,2])await action(page,'canvas.present',{id,sessionId:sid,version,messageId,presentation:{chat:'inline',canvas:'unchanged'}});
 await action(page,'canvas.select',{id,version:1});
 const card=(client,aid,version)=>client.locator(`.a-general-artifact[data-artifact-id="${aid}"][data-artifact-version="${version}"]`);
 const one=card(page,id,1),two=card(page,id,2);
 const appFrame=card=>card.frameLocator('iframe').frameLocator('iframe');
 for(const [node,version] of [[one,1],[two,2]]){
  await node.scrollIntoViewIfNeeded();
  await expect(appFrame(node).getByRole('heading',{name:`Native revision ${version}`})).toBeVisible();
  await expect(node).toContainText(`Published version ${version} · Read only`);
 }
 // The explicit number equal to Latest is still read-only. Test host refusal
 // directly as well, with primary Latest, before following either inline view.
 await action(page,'canvas.select',{id});
 let views=(await state(page)).canvasWorkspace.views.filter(row=>row.resourceId===id&&row.viewId!=='primary');
 const numbered=views.find(row=>row.publishedVersion===2);
 await assert.rejects(action(page,'canvas.apps.state',{...await cas(),...target(numbered),patch:{count:99}}),/read.only|saved version|Latest/i);
 await action(page,'canvas.select',{id,version:1});
 for(const node of [one,two])await node.getByRole('button',{name:'Go to Latest',exact:true}).click();
 for(const node of [one,two]){await expect(node).toContainText('Latest · Version 2');await expect(appFrame(node).getByRole('heading',{name:'Native revision 2'})).toBeVisible()}
 views=(await state(page)).canvasWorkspace.views.filter(row=>row.resourceId===id&&row.viewId!=='primary');
 assert.equal(views.length,2);assert.ok(views.every(row=>row.followLatest&&row.selectedVersion===null));
 assert.equal((await state(page)).canvas.selectedVersion,1,'Live inline does not swizzle the primary historical selection');
 await appFrame(one).getByRole('button',{name:'Add shared count'}).click();
 for(const node of [one,two])await expect(appFrame(node).locator('#count')).toHaveText('1');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft(draft);
 await appFrame(one).getByRole('textbox',{name:'Private native input'}).fill('Private first view');
 await appFrame(two).getByRole('textbox',{name:'Private native input'}).fill('Private second view');
 await expect.poll(async()=>(await state(page)).canvasWorkspace.views.filter(row=>row.resourceId===id&&row.dirty).length).toBe(2);
 const beforeFollow=target((await state(page)).canvasWorkspace.views.find(row=>row.viewId===views[0].viewId));
 await one.getByRole('button',{name:'Return to published version',exact:true}).click();
 await expect(one.locator('.a-inline-artifact-notice')).toContainText('Finish or cancel');
 await assert.rejects(action(page,'canvas.apps.revise',{...await cas(),content:surface(3).content}),/Finish or cancel|dirty/i);
 await assert.rejects(action(page,'canvas.apps.restore',{...await cas(),version:1}),/Finish or cancel|dirty/i);
 assert.deepEqual(target((await state(page)).canvasWorkspace.views.find(row=>row.viewId===beforeFollow.viewId)),beforeFollow);
 await expect(appFrame(one).getByRole('textbox',{name:'Private native input'})).toHaveValue('Private first view');
 await expect(appFrame(two).getByRole('textbox',{name:'Private native input'})).toHaveValue('Private second view');
 for(const node of [one,two])await appFrame(node).getByRole('button',{name:'Keep input, mark clean'}).click();
 await expect.poll(async()=>(await state(page)).canvasWorkspace.views.some(row=>row.resourceId===id&&row.dirty)).toBe(false);
 await action(page,'canvas.apps.revise',{...await cas(),content:surface(3).content});
 for(const node of [one,two])await expect(appFrame(node).getByRole('heading',{name:'Native revision 3'})).toBeVisible();
 // A real command ahead of follow keeps the queued interval editable. Late
 // private input vetoes execution, not just the original button click.
 const blockQueue=async()=>page.evaluate(args=>{
  window.nativeQueueGate=new Promise(resolve=>{window.releaseNativeQueue=resolve});
  window.nativeQueueBlocker=window.amplifier.dispatch('canvas.apps.inspect',args,{before:window.nativeQueueGate});
 },{id,sessionId:sid});
 const releaseQueue=async()=>page.evaluate(()=>{window.releaseNativeQueue();return window.nativeQueueBlocker});
 const followPosts=()=>requests.filter(row=>row.action==='canvas.views.follow').length;
 const nativeInput=()=>appFrame(one).getByRole('textbox',{name:'Private native input'});
 await nativeInput().evaluate(node=>{window.originalNativeInput=node});
 const sameNativeInput=async()=>assert.equal(await nativeInput().evaluate(node=>node===window.originalNativeInput),true);
 let postedBefore=followPosts();
 await blockQueue();await one.getByRole('button',{name:'Return to published version',exact:true}).click();
 await expect(one.locator('iframe')).not.toHaveAttribute('inert','');
 await nativeInput().fill('Typed after queuing');
 await releaseQueue();await expect(one.locator('.a-inline-artifact-notice')).toContainText('Finish or cancel');
 assert.equal(followPosts(),postedBefore);await sameNativeInput();await expect(nativeInput()).toHaveValue('Typed after queuing');
 await appFrame(one).getByRole('button',{name:'Keep input, mark clean'}).click();
 await expect.poll(async()=>(await state(page)).canvasWorkspace.views.some(row=>row.resourceId===id&&row.dirty)).toBe(false);

 // A native event queues behind follow while the command queue is blocked.
 // Preparation must veto rather than flush/wait and deadlock that queue.
 await blockQueue();await one.getByRole('button',{name:'Return to published version',exact:true}).click();
 await appFrame(one).getByRole('button',{name:'Add shared count'}).click();
 await releaseQueue();await expect(one.locator('.a-inline-artifact-notice')).toContainText('Finish or cancel');
 assert.equal(followPosts(),postedBefore);
 await expect(appFrame(one).locator('#count')).toHaveText('2');
 await sameNativeInput();

 // Delay the real editing notification in the trusted host fixture; the
 // bridge's local pending facts still refuse preparation before any POST.
 const nativeHost=await (await one.locator('iframe').elementHandle()).contentFrame();
 await nativeHost.evaluate(()=>{
  window.delayedNativeEditing=event=>{
   if(event.data?.type==='canvas-app'&&event.data.op==='editing'){
    event.stopImmediatePropagation();setTimeout(()=>parent.postMessage(event.data,'*'),350);
   }
  };addEventListener('message',window.delayedNativeEditing,true);
 });
 await nativeInput().fill('Delayed editing is still private');
 await one.getByRole('button',{name:'Return to published version',exact:true}).click();
 await expect(one.locator('.a-inline-artifact-notice')).toContainText(/pending|unfinished|Finish or cancel/);
 assert.equal(followPosts(),postedBefore);await sameNativeInput();
 await nativeHost.evaluate(()=>removeEventListener('message',window.delayedNativeEditing,true));
 await expect.poll(async()=>(await state(page)).canvasWorkspace.views.some(row=>row.resourceId===id&&row.dirty)).toBe(true);
 await appFrame(one).getByRole('button',{name:'Keep input, mark clean'}).click();
 await expect.poll(async()=>(await state(page)).canvasWorkspace.views.some(row=>row.resourceId===id&&row.dirty)).toBe(false);
 await nativeInput().evaluate(()=>{window.staleTransition=true});
 await one.getByRole('button',{name:'Return to published version',exact:true}).click();
 await expect(one.locator('.a-inline-artifact-notice')).toContainText('did not acknowledge');
 assert.equal(followPosts(),postedBefore);await sameNativeInput();
 await nativeInput().evaluate(()=>{window.staleTransition=false});

 // A known host refusal releases the hold, keeping identical DOM and text.
 const knownRefusal=async route=>{
  if(route.request().postDataJSON()?.action==='canvas.views.follow')return route.fulfill({status:409,json:{error:'Exact fixture refusal'}});
  return route.continue();
 };
 await page.route('**/api/actions',knownRefusal);
 await one.getByRole('button',{name:'Return to published version',exact:true}).click();
 await expect(one.locator('.a-inline-artifact-notice')).toContainText('Exact fixture refusal');
 await expect(one.locator('iframe')).not.toHaveAttribute('inert','');await sameNativeInput();
 await expect(nativeInput()).toHaveValue('Delayed editing is still private');
 await page.unroute('**/api/actions',knownRefusal);

 // Execute on the real host, then withhold the HTTP receipt while SSE changes
 // the target. The old DOM must remain visibly disabled throughout.
 let discardReceipt,receivedByHost=false;
 const uncertainFollow=async route=>{
  const payload=route.request().postDataJSON();
  if(payload?.action!=='canvas.views.follow')return route.continue();
  const response=await route.fetch();assert.equal(response.status(),200);receivedByHost=true;
  await new Promise(resolve=>{discardReceipt=resolve});return route.abort('failed');
 };
 await page.route('**/api/actions',uncertainFollow);
 postedBefore=followPosts();
 await one.getByRole('button',{name:'Return to published version',exact:true}).click();
 await expect.poll(()=>receivedByHost).toBe(true);
 await expect.poll(async()=>(await state(page)).canvasWorkspace.views.find(row=>row.viewId===beforeFollow.viewId)?.followLatest).toBe(false);
 await expect(one.locator('iframe')).toHaveAttribute('inert','');await sameNativeInput();
 const bounds=await nativeInput().boundingBox();
 await page.mouse.click(bounds.x+bounds.width/2,bounds.y+bounds.height/2);
 await page.keyboard.type('NOT_ACCEPTED_WHILE_DISABLED');
 await expect(nativeInput()).toHaveValue('Delayed editing is still private');
 await expect(one.getByRole('status').filter({hasText:'New typing is not accepted'})).toBeVisible();
 discardReceipt();
 await expect(one.getByRole('button',{name:'Retry version change',exact:true})).toBeEnabled();
 await sameNativeInput();await expect(one.locator('iframe')).toHaveAttribute('inert','');
 const originalFollow=requests.filter(row=>row.action==='canvas.views.follow').at(-1);
 assert.equal(followPosts(),postedBefore+1,'No automatic follow-back or replay');
 await page.unroute('**/api/actions',uncertainFollow);
 await one.getByRole('button',{name:'Retry version change',exact:true}).click();
 await expect(one).toContainText('Published version 1 · Read only');
 await expect(appFrame(one).getByRole('heading',{name:'Native revision 1'})).toBeVisible();
 assert.deepEqual(requests.filter(row=>row.action==='canvas.views.follow').at(-1),originalFollow);
 await one.getByRole('button',{name:'Go to Latest',exact:true}).click();
 await expect(appFrame(one).getByRole('heading',{name:'Native revision 3'})).toBeVisible();
 // A successful follow's generation may already be obsolete when its HTTP
 // receipt arrives. SSE advances once more while the original DOM is held.
 await one.getByRole('button',{name:'Return to published version',exact:true}).click();
 await expect(appFrame(one).getByRole('heading',{name:'Native revision 1'})).toBeVisible();
 const advanceArgs=await cas(),oldHeldFrame=await one.locator('iframe').elementHandle();
 let returnReceipt,followReceipt;
 const advancingFollow=async route=>{
  if(route.request().postDataJSON()?.action!=='canvas.views.follow')return route.continue();
  const response=await route.fetch();assert.equal(response.status(),200);followReceipt=await response.json();
  await new Promise(resolve=>{returnReceipt=resolve});return route.fulfill({response});
 };
 await page.route('**/api/actions',advancingFollow);postedBefore=followPosts();
 await one.getByRole('button',{name:'Go to Latest',exact:true}).click();
 await expect.poll(()=>!!followReceipt).toBe(true);
 await expect(one.locator('iframe')).toHaveAttribute('inert','');
 const currentClient=(await state(page)).client.id??(await state(page)).client.clientId;
 const revised=await page.request.post(url+'/api/actions?clientId='+encodeURIComponent(currentClient),{
  data:{id:'advance-during-follow-receipt',action:'canvas.apps.revise',args:{...advanceArgs,content:surface(4).content}}
 });
 assert.equal(revised.status(),200);
 await expect.poll(async()=>(await state(page)).canvasWorkspace.views.find(row=>row.viewId===followReceipt.result.viewId)?.generation).toBeGreaterThan(followReceipt.result.generation);
 assert.equal(await one.locator('iframe').evaluate((node,old)=>node===old,oldHeldFrame),true);
 await expect(appFrame(one).getByRole('heading',{name:'Native revision 1'})).toBeVisible();
 returnReceipt();
 await expect(appFrame(one).getByRole('heading',{name:'Native revision 4'})).toBeVisible();
 assert.equal(followPosts(),postedBefore+1,'No compensating follow or automatic retry');
 await page.unroute('**/api/actions',advancingFollow);
 // Lost JSON after host success and ambiguous HTTP errors must retain the
 // exact command and disabled old DOM, just like a connection failure.
 for(const failure of ['non-json',408,503]){
  const heldFrame=await one.locator('iframe').elementHandle();
  const failAfterSuccess=async route=>{
   if(route.request().postDataJSON()?.action!=='canvas.views.follow')return route.continue();
   const response=await route.fetch();assert.equal(response.status(),200);
   return failure==='non-json'?route.fulfill({status:200,contentType:'text/html',body:'not-json'}):
    route.fulfill({status:failure,json:{error:'Outcome unknown after host success'}});
  };
  await page.route('**/api/actions',failAfterSuccess);postedBefore=followPosts();
  await one.getByRole('button',{name:'Return to published version',exact:true}).click();
  await expect(one.getByRole('button',{name:'Retry version change',exact:true})).toBeEnabled();
  await expect(one.locator('iframe')).toHaveAttribute('inert','');
  assert.equal(await one.locator('iframe').evaluate((node,old)=>node===old,heldFrame),true);
  const exact=requests.filter(row=>row.action==='canvas.views.follow').at(-1);
  assert.equal(followPosts(),postedBefore+1);
  await page.unroute('**/api/actions',failAfterSuccess);
  await one.getByRole('button',{name:'Retry version change',exact:true}).click();
  await expect(appFrame(one).getByRole('heading',{name:'Native revision 1'})).toBeVisible();
  assert.deepEqual(requests.filter(row=>row.action==='canvas.views.follow').at(-1),exact);
  await one.getByRole('button',{name:'Go to Latest',exact:true}).click();
  await expect(appFrame(one).getByRole('heading',{name:'Native revision 4'})).toBeVisible();
 }
 const peer=await context.newPage();await peer.goto(url);await peer.getByRole('textbox',{name:'Message Amplifier'}).waitFor();await action(peer,'session.select',{id:sid});
 await card(peer,id,1).scrollIntoViewIfNeeded();await expect(card(peer,id,1)).toContainText('Published version 1 · Read only');
 await one.getByRole('button',{name:'Collapse Shared native counter',exact:true}).click();
 await expect(one).toHaveAttribute('data-chat-mode','compact');
 await expect(two.locator('iframe')).toHaveCount(1);
 await expect(card(peer,id,1)).toHaveAttribute('data-chat-mode','inline');
 await two.getByRole('button',{name:'Return to published version',exact:true}).click();
 await expect(appFrame(two).getByRole('heading',{name:'Native revision 2'})).toBeVisible();
 // Return renders the saved state, not current canonical inputs.
 await expect(appFrame(two).locator('#count')).toHaveText('0');
 await expect(two).toContainText('Published version 2 · Read only');
 await expect(card(peer,id,1)).toContainText('Published version 1 · Read only');
 await two.getByRole('button',{name:'Open Shared native counter in Canvas',exact:true}).click();
 assert.equal((await state(page)).canvas.selectedVersion,2,'Open in Canvas retains the published number, not the local live choice');
 await two.getByRole('button',{name:'Collapse Shared native counter',exact:true}).click();await peer.close();
 await action(page,'canvas.close');

 // Native browser reference and supported A2UI events use exact view commands,
 // not a contribution, fake frozen page, primary substitution or chat send.
 const browserId=(await action(page,'canvas.show',{kind:'browser',title:'Native browser reference',url:url+'/fixture'})).result.id;
 await action(page,'canvas.present',{id:browserId,sessionId:sid,version:1,messageId,presentation:{chat:'inline',canvas:'unchanged'}});
 const uiId=(await action(page,'canvas.show',{kind:'a2ui',title:'Native A2UI',surface:a2ui})).result.id;
 await action(page,'canvas.present',{id:uiId,sessionId:sid,version:1,messageId,presentation:{chat:'inline',canvas:'unchanged'}});
 await action(page,'canvas.select',{id,version:1});
 const browserCard=card(page,browserId,1),uiCard=card(page,uiId,1);
 await browserCard.scrollIntoViewIfNeeded();await expect(browserCard.frameLocator('iframe').locator('body')).toContainText('"sent"');
 assert.equal(await browserCard.locator('iframe').getAttribute('src'),url+'/fixture');
 assert.equal(await browserCard.locator('iframe').getAttribute('sandbox'),'allow-scripts allow-forms');
 await uiCard.scrollIntoViewIfNeeded();const sentBefore=(await (await page.request.get(url+'/fixture')).json()).sent.length;
 await uiCard.getByRole('button',{name:'Review native controls',exact:true}).click();
 await expect.poll(()=>requests.filter(row=>row.action==='canvas.views.command'&&row.args.action==='canvas.event').length).toBe(1);
 const event=requests.find(row=>row.action==='canvas.views.command'&&row.args.action==='canvas.event');
 assert.equal(event.args.resourceId,uiId);
 for(const field of fields)assert.notEqual(event.args[field],undefined);
 assert.equal((await (await page.request.get(url+'/fixture')).json()).sent.length,sentBefore);
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft(draft);
 const old=target((await state(page)).canvasWorkspace.views.find(row=>row.resourceId===uiId&&row.viewId!=='primary'));
 await uiCard.getByRole('button',{name:'Collapse Native A2UI',exact:true}).click();
 await assert.rejects(action(page,'canvas.views.command',{...old,action:'canvas.event',args:event.args.args}),/view|changed|Unknown/i);
 await browserCard.getByRole('button',{name:'Collapse Native browser reference',exact:true}).click();

 // Real saved MCP source/result and underlying tool; mount/follow/reload do
 // not repeat its launch. Reconnect is explicit and retains grants/schemas.
 await mkdir(join(root,'output'),{recursive:true});directory=await mkdtemp(join(root,'output','interactive-fixture-'));
 const html=join(directory,'app.html'),calls=join(directory,'calls.jsonl'),mode=join(directory,'mode');
 const built=await build({stdin:{contents:`import {App} from '@modelcontextprotocol/ext-apps';
 const app=new App({name:'Native inline fixture',version:'1.0.0'},{});
 const draw=result=>document.querySelector('#count').textContent=String(result.structuredContent.count);
 app.ontoolresult=draw;
 document.querySelector('#add').onclick=async()=>{try{draw(await app.callServerTool({name:'counter_add',arguments:{amount:1}}))}catch(error){document.querySelector('#error').textContent=error.message}};
 document.querySelector('#context').onclick=()=>app.updateModelContext({structuredContent:{visible:'native-inline'}});
 await app.connect();document.body.dataset.booted='true';`,resolveDir:join(root,'frontend'),sourcefile:'interactive-fixture.js'},bundle:true,write:false,format:'esm'});
 await writeFile(html,`<!doctype html><h1>Saved native MCP</h1><input aria-label="Private MCP input"><output id="count"></output><button id="add">Add MCP count</button><button id="context">Send visible context</button><p id="error"></p><script type="module">${built.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`);
 const operation=async(name,args)=>{
  const receipt=await action(page,name,args);if(!receipt.operationId)return receipt.result;
  const read=async()=>{const response=await page.request.get(url+'/api/smart-tools/operations/'+receipt.operationId);assert.equal(response.status(),200);return response.json()};
  await expect.poll(async()=>{const row=await read();if(['failed','interrupted'].includes(row.status))throw Error(row.error);return row.status},{timeout:20000}).toBe('completed');
  return (await read()).result;
 };
 const mutations=async()=>(await readFile(calls,'utf8')).trim().split('\n').filter(Boolean).length;
 await operation('smartTools.configure',{id:'native-fixture',name:'Native fixture',command:python,args:[root+'tests/fixtures/canvas_recovery_mcp_server.py',html,calls,mode]});
 await operation('smartTools.connect',{id:'native-fixture'});
 const launch=await action(page,'smartTools.call',{id:'native-fixture',name:'counter_add',arguments:{amount:7}});
 await expect.poll(async()=>(await (await page.request.get(url+'/api/smart-tools/operations/'+launch.operationId)).json()).status,{timeout:20000}).toBe('completed');
 const mcpId=(await operation('smartTools.open',{id:'native-fixture',tool:'counter_add',operationId:launch.operationId})).canvasId;
 await action(page,'canvas.present',{id:mcpId,sessionId:sid,version:1,messageId,presentation:{chat:'inline',canvas:'unchanged'}});
 const mcpCard=card(page,mcpId,1),mcpFrame=()=>mcpCard.frameLocator('iframe');
 await mcpCard.scrollIntoViewIfNeeded();await expect(mcpFrame().locator('body')).toHaveAttribute('data-booted','true');await expect(mcpFrame().locator('#count')).toHaveText('7');
 assert.equal(await mutations(),1);
 await mcpFrame().getByRole('textbox',{name:'Private MCP input'}).fill('Opaque tool input');
 await mcpFrame().getByRole('textbox',{name:'Private MCP input'}).evaluate(node=>{window.originalMcpInput=node});
 const beforeMcpReset=followPosts();
 await mcpCard.getByRole('button',{name:'Go to Latest',exact:true}).click();
 await expect(mcpCard.getByRole('alertdialog')).toContainText('cannot confirm whether that input is saved');
 await expect(mcpCard.locator('iframe')).toHaveAttribute('inert','');
 assert.equal(await mcpCard.locator('iframe').evaluate(node=>document.activeElement===node),false);
 const mcpBounds=await mcpFrame().getByRole('textbox',{name:'Private MCP input'}).boundingBox();
 await page.mouse.click(mcpBounds.x+10,mcpBounds.y+10);await page.keyboard.type('NOT_ACCEPTED');
 await expect(mcpFrame().getByRole('textbox',{name:'Private MCP input'})).toHaveValue('Opaque tool input');
 await mcpCard.getByRole('button',{name:'Keep this tool view',exact:true}).click();
 await expect(mcpCard.getByRole('alertdialog')).toHaveCount(0);
 await expect(mcpCard.locator('iframe')).not.toHaveAttribute('inert','');
 assert.equal(await mcpFrame().getByRole('textbox',{name:'Private MCP input'}).evaluate(node=>node===window.originalMcpInput),true);
 assert.equal(followPosts(),beforeMcpReset);assert.equal(await mutations(),1);
 await action(page,'canvas.select',{id:mcpId});
 await mcpFrame().getByRole('button',{name:'Add MCP count'}).click();
 await expect(mcpFrame().locator('#error')).toContainText('read-only');assert.equal(await mutations(),1);
 await mcpCard.getByRole('button',{name:'Go to Latest',exact:true}).click();
 await mcpCard.getByRole('button',{name:'Reset local input and switch version',exact:true}).click();
 await expect(mcpFrame().locator('body')).toHaveAttribute('data-booted','true');assert.equal(await mutations(),1);
 await action(page,'canvas.select',{id,version:1});
 await mcpFrame().getByRole('button',{name:'Add MCP count'}).click();await expect(mcpFrame().locator('#count')).toHaveText('8');assert.equal(await mutations(),2);
 await mcpFrame().getByRole('button',{name:'Send visible context'}).click();
 await expect.poll(()=>requests.filter(row=>row.action==='smartTools.context').length).toBe(1);
 const contextRequest=requests.find(row=>row.action==='smartTools.context');
 assert.equal(contextRequest.args.resourceId,mcpId);for(const field of fields)assert.notEqual(contextRequest.args[field],undefined);
 // Opaque tool reset is intentional only after confirmation. Refusal retains
 // the same DOM/text; transport unknown holds it until an exact explicit retry.
 await mcpFrame().getByRole('textbox',{name:'Private MCP input'}).fill('Retain this opaque draft');
 await mcpFrame().getByRole('textbox',{name:'Private MCP input'}).evaluate(node=>{window.originalMcpInput=node});
 await page.route('**/api/actions',knownRefusal);
 await mcpCard.getByRole('button',{name:'Return to published version',exact:true}).click();
 await mcpCard.getByRole('button',{name:'Reset local input and switch version',exact:true}).click();
 await expect(mcpCard.locator('.a-inline-artifact-notice')).toContainText('Exact fixture refusal');
 await expect(mcpFrame().getByRole('textbox',{name:'Private MCP input'})).toHaveValue('Retain this opaque draft');
 assert.equal(await mcpFrame().getByRole('textbox',{name:'Private MCP input'}).evaluate(node=>node===window.originalMcpInput),true);
 await page.unroute('**/api/actions',knownRefusal);
 receivedByHost=false;
 await page.route('**/api/actions',uncertainFollow);
 await mcpCard.getByRole('button',{name:'Return to published version',exact:true}).click();
 await mcpCard.getByRole('button',{name:'Reset local input and switch version',exact:true}).click();
 await expect.poll(()=>receivedByHost).toBe(true);
 await expect(mcpCard.locator('iframe')).toHaveAttribute('inert','');
 assert.equal(await mcpFrame().getByRole('textbox',{name:'Private MCP input'}).evaluate(node=>node===window.originalMcpInput),true);
 await expect(mcpFrame().getByRole('textbox',{name:'Private MCP input'})).toHaveValue('Retain this opaque draft');
 discardReceipt();
 await expect(mcpCard.getByRole('button',{name:'Retry version change',exact:true})).toBeEnabled();
 const mcpUnknown=requests.filter(row=>row.action==='canvas.views.follow').at(-1);
 await page.unroute('**/api/actions',uncertainFollow);
 await mcpCard.getByRole('button',{name:'Retry version change',exact:true}).click();
 await expect(mcpCard).toContainText('Published version 1 · Read only');
 assert.deepEqual(requests.filter(row=>row.action==='canvas.views.follow').at(-1),mcpUnknown);
 await mcpCard.getByRole('button',{name:'Go to Latest',exact:true}).click();
 await mcpCard.getByRole('button',{name:'Reset local input and switch version',exact:true}).click();
 await expect(mcpFrame().locator('body')).toHaveAttribute('data-booted','true');assert.equal(await mutations(),2);
 await operation('smartTools.disconnect',{id:'native-fixture'});
 await expect(mcpCard.locator('.a-mcp-status')).toHaveAttribute('data-phase','error');
 await mcpCard.getByRole('button',{name:'Reconnect tool view',exact:true}).click();
 await expect(mcpCard.locator('.a-mcp-status')).toHaveAttribute('data-phase','ready');assert.equal(await mutations(),2);
 // Reload is a qualified view update, never smartTools.open/call.
 let liveView=(await state(page)).canvasWorkspace.views.find(row=>row.resourceId===mcpId&&row.viewId!=='primary');
 await action(page,'canvas.views.command',{...target(liveView),action:'canvas.view',args:{id:mcpId,patch:{reload:Date.now()}}});
 await expect(mcpFrame().locator('body')).toHaveAttribute('data-booted','true');assert.equal(await mutations(),2);
 await mcpCard.getByRole('button',{name:'Return to published version',exact:true}).click();
 await mcpCard.getByRole('button',{name:'Reset local input and switch version',exact:true}).click();
 await expect(mcpFrame().locator('#count')).toHaveText('7');assert.equal(await mutations(),2);
 await mcpCard.getByRole('button',{name:'Go to Latest',exact:true}).click();
 await mcpCard.getByRole('button',{name:'Reset local input and switch version',exact:true}).click();
 await operation('smartTools.disconnect',{id:'native-fixture'});await writeFile(mode,'changed');
 await mcpCard.getByRole('button',{name:'Reconnect tool view',exact:true}).click();
 await expect(mcpCard.locator('.a-mcp-status')).toHaveAttribute('data-phase','error');
 assert.equal(await mutations(),2,'Schema mismatch never changes saved grants or replays launch');
 await mcpCard.getByRole('button',{name:/Collapse/}).click();
 await expect(mcpCard.locator('iframe')).toHaveCount(0);

 // Inject one source-body omission, not an admission success/blank PASS.
 await one.getByRole('button',{name:'Expand Shared native counter in chat',exact:true}).click();
 await expect(appFrame(one).getByRole('heading',{name:'Native revision 1'})).toBeVisible();
 await one.getByRole('button',{name:'Collapse Shared native counter',exact:true}).click();
 let omit=true;
 await page.route('**/api/canvas/views/*/resource?*',async route=>{
  if(omit&&new URL(route.request().url()).searchParams.get('resourceId')===id){omit=false;return route.fulfill({json:{id,kind:'canvas-app'}})}
  return route.continue();
 });
 await one.getByRole('button',{name:'Expand Shared native counter in chat',exact:true}).click();
 await expect(one.getByRole('alert')).toContainText('source is unavailable');
 await expect(one.locator('iframe')).toHaveCount(0);
 const admission=(await state(page)).canvasWorkspace.views.find(row=>row.resourceId===id&&row.publishedVersion===1&&row.viewId!=='primary').viewId;
 await one.getByRole('button',{name:'Retry source',exact:true}).click();
 await expect(appFrame(one).getByRole('heading',{name:'Native revision 1'})).toBeVisible();
 assert.ok((await state(page)).canvasWorkspace.views.some(row=>row.viewId===admission),'Retry body keeps admission identity');
 await page.unrouteAll({behavior:'wait'});
 for(const width of [1500,390]){
  await page.setViewportSize({width,height:1100});await one.scrollIntoViewIfNeeded();
  assert.ok(await one.evaluate(node=>Math.abs(node.getBoundingClientRect().width-node.parentElement.getBoundingClientRect().width)<=2));
 }
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft(draft);
 assert.deepEqual(errors,[]);
 console.log('Real-host native interactive artifact regressions passed (synthetic runtime/MCP only; body Retry has one injected response; account security remains separate).');
}finally{
 await browser?.close();fixture?.kill('SIGTERM');
 if(directory)await rm(directory,{recursive:true,force:true});
}