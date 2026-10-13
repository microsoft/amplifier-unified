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
 browser=await chromium.launch({headless:true});
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
 await writeFile(html,`<!doctype html><h1>Saved native MCP</h1><output id="count"></output><button id="add">Add MCP count</button><button id="context">Send visible context</button><p id="error"></p><script type="module">${built.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`);
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
 await action(page,'canvas.select',{id:mcpId});
 await mcpFrame().getByRole('button',{name:'Add MCP count'}).click();
 await expect(mcpFrame().locator('#error')).toContainText('read-only');assert.equal(await mutations(),1);
 await mcpCard.getByRole('button',{name:'Go to Latest',exact:true}).click();
 await expect(mcpFrame().locator('body')).toHaveAttribute('data-booted','true');assert.equal(await mutations(),1);
 await action(page,'canvas.select',{id,version:1});
 await mcpFrame().getByRole('button',{name:'Add MCP count'}).click();await expect(mcpFrame().locator('#count')).toHaveText('8');assert.equal(await mutations(),2);
 await mcpFrame().getByRole('button',{name:'Send visible context'}).click();
 await expect.poll(()=>requests.filter(row=>row.action==='smartTools.context').length).toBe(1);
 const contextRequest=requests.find(row=>row.action==='smartTools.context');
 assert.equal(contextRequest.args.resourceId,mcpId);for(const field of fields)assert.notEqual(contextRequest.args[field],undefined);
 await operation('smartTools.disconnect',{id:'native-fixture'});
 await expect(mcpCard.locator('.a-mcp-status')).toHaveAttribute('data-phase','error');
 await mcpCard.getByRole('button',{name:'Reconnect tool view',exact:true}).click();
 await expect(mcpCard.locator('.a-mcp-status')).toHaveAttribute('data-phase','ready');assert.equal(await mutations(),2);
 // Reload is a qualified view update, never smartTools.open/call.
 let liveView=(await state(page)).canvasWorkspace.views.find(row=>row.resourceId===mcpId&&row.viewId!=='primary');
 await action(page,'canvas.views.command',{...target(liveView),action:'canvas.view',args:{id:mcpId,patch:{reload:Date.now()}}});
 await expect(mcpFrame().locator('body')).toHaveAttribute('data-booted','true');assert.equal(await mutations(),2);
 await mcpCard.getByRole('button',{name:'Return to published version',exact:true}).click();
 await expect(mcpFrame().locator('#count')).toHaveText('7');assert.equal(await mutations(),2);
 await mcpCard.getByRole('button',{name:'Go to Latest',exact:true}).click();
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