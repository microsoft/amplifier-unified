import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp';
await mkdir(artifacts,{recursive:true});
const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/chat_ui_server.py',import.meta.url))],{stdio:'ignore'});
for(let i=0;i<100;i++){try{if((await fetch('http://127.0.0.1:8958/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1600,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const action=(name,args)=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
const patch=values=>action('view.update',{patch:values});
const box=selector=>page.locator(selector).boundingBox();
const settled=()=>page.waitForTimeout(120);
const focusedBounds=()=>page.evaluate(()=>{
 const top=document.querySelector('.a-work-header').getBoundingClientRect().bottom;
 return {x:0,y:top,width:innerWidth,height:innerHeight-top};
});
const toolbarsMatch=visible=>page.locator('#canvas-options .a-canvas-toolbar').evaluateAll((nodes,visible)=>nodes.length>0&&nodes.every(node=>!!node.getClientRects().length===visible),visible);
const capture=name=>page.screenshot({path:join(artifacts,`amplifier-canvas-${name}.png`)});
const canvasDiagnostics=()=>page.evaluate(()=>{
 const describe=node=>{
  const style=getComputedStyle(node),r=node.getBoundingClientRect();
  return {tag:node.tagName,id:node.id,class:node.className,label:node.getAttribute('aria-label'),haspopup:node.getAttribute('aria-haspopup'),hidden:node.hidden,inert:node.inert,
   scrollWidth:node.scrollWidth,clientWidth:node.clientWidth,
   rects:node.getClientRects().length,box:{x:r.x,y:r.y,width:r.width,height:r.height},
   display:style.display,visibility:style.visibility,opacity:style.opacity,pointerEvents:style.pointerEvents,
   transition:style.transition,hover:node.matches(':hover')};
 };
 const panel=document.querySelector('.a-canvas-panel');
 return {controls:panel?.dataset.controls,pinned:panel?.dataset.pinned,
  nodes:[...document.querySelectorAll('.a-work-header,.a-layout,.a-nav-slot,.a-nav-resize,.a-conversation,.a-composer,.a-compose-configuration,.a-model-trigger,.a-model-popover,.a-canvas-panel,.a-canvas-resize,.a-canvas-head,.a-canvas-artifact-toolbar,.a-canvas-body,.a-canvas-html,#canvas-options,.a-canvas-toolbar,[aria-label="Copy canvas source"]')].map(node=>{
   const ancestors=[];for(let parent=node.parentElement;parent;parent=parent.parentElement){ancestors.push(describe(parent));if(parent===panel)break}
   return {...describe(node),ancestors};
  })};
});
try{
 await page.goto('http://127.0.0.1:8958/');await showFixtureConversation(page);
 // The empty pane must be resizable before any artifact supplies its layout.
 await action('canvas.reopen',{});await settled();
 const separator=page.getByRole('separator',{name:'Resize canvas'});
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvasWorkspace?.views?.length||0),0,'Canvas starts without an artifact view');
 const emptyPanel=await box('.a-canvas-panel'),emptyHandle=await separator.boundingBox();
 assert.ok(emptyPanel?.width>0&&emptyPanel.height>0,'Empty Canvas has nonzero bounds');
 assert.ok(emptyHandle?.width>0&&emptyHandle.height>0,'Empty Canvas separator has a nonzero hit area');
 const hit={x:emptyHandle.x+emptyHandle.width/2,y:emptyHandle.y+emptyHandle.height/2};
 assert.equal(await separator.evaluate((el,{x,y})=>el.contains(document.elementFromPoint(x,y)),hit),true,'The pointer hits the empty Canvas separator, not adjacent content');
 const dragX=hit.x+(hit.x<emptyPanel.x+emptyPanel.width/2?-80:80);
 await page.mouse.move(hit.x,hit.y);await page.mouse.down();await page.mouse.move(dragX,hit.y,{steps:6});await page.mouse.up();
 await page.waitForFunction(width=>Math.abs(document.querySelector('.a-canvas-panel').getBoundingClientRect().width-width)>20,emptyPanel.width);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvasWorkspace?.views?.length||0),0,'Pointer resize does not create an artifact');
 await action('canvas.show',{kind:'html',title:'A canvas that keeps its place',content:'<!doctype html><html><head><style>body{margin:0;background:linear-gradient(135deg,#eef2ff,#e6f9f5);font:18px system-ui;min-height:100vh}main{padding:40px}button{font:inherit;padding:12px 20px;border:0;border-radius:12px;background:#6261db;color:white}</style></head><body><main><h1>Room to explore</h1><p>This interactive view stays alive as you resize and focus it.</p><button onclick="this.textContent=String(Number(this.textContent)+1)">0</button></main></body></html>'});
 // Details is explicit, not hover/pin chrome. A narrow split uses an overlay
 // that must not move the iframe; wide Details may take reader width.
 const draft='Retain the panel-layout draft';
 await patch({canvasWidth:600,canvasControlsExpanded:false,draft});
 const details=page.getByRole('region',{name:'Canvas details',exact:true}),detailsButton=page.getByRole('button',{name:'Canvas details',exact:true});
 const closeDetails=details.getByRole('button',{name:'Close canvas details',exact:true});
 await page.locator('#canvas-options').waitFor({state:'hidden'});
 const frame=page.frameLocator('.a-canvas-html');await frame.getByRole('button',{name:'0',exact:true}).click();await settled();
 const closedFrame=await box('.a-canvas-html');await capture('details-closed');
 assert.equal(await page.locator('.a-canvas-content').getAttribute('data-details-overlay'),'true');
 assert.equal(await toolbarsMatch(false),true);
 const copy=page.getByRole('button',{name:'Copy canvas source',exact:true});await copy.focus();
 assert.equal(await copy.evaluate(el=>{
  const r=el.getBoundingClientRect();
  return document.activeElement===el&&!el.closest('[hidden],[inert]')&&r.width>0&&r.height>0
   &&r.x>=0&&r.y>=0&&r.right<=innerWidth&&r.bottom<=innerHeight
   &&el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));
 }),true,'Primary Copy stays focusable and hit-testable with Details closed');
 await detailsButton.click();await details.waitFor({state:'visible'});await settled();
 assert.equal(await toolbarsMatch(true),true);
 for(const name of ['Artifact version','Open with'])assert.equal(await details.getByRole('combobox',{name,exact:true}).isVisible(),true);
 assert.equal(await details.getByRole('button',{name:'Source',exact:true}).isVisible(),true);
 assert.deepEqual(await box('.a-canvas-html'),closedFrame,'Overlay Details does not move Canvas content');await capture('details-open');
 // Dismissing Details retains unfinished forms, the unrelated composer draft
 // and the running HTML document, without changing the collapsed layout.
 for(const [button,input,value] of [['Open a file in canvas','#canvas-file-path','notes.md'],['Open a website in canvas','#canvas-browser-url','https://example.com/']]){
  await details.getByRole('button',{name:button,exact:true}).click();
  const field=page.locator(input);await field.fill(value);
  await closeDetails.click();await page.locator('#canvas-options').waitFor({state:'hidden'});await settled();
  assert.equal(await toolbarsMatch(false),true);
  assert.deepEqual(await box('.a-canvas-html'),closedFrame,'Closing Details restores the same iframe box');
  assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),draft);
  assert.equal(await frame.getByRole('button',{name:'1',exact:true}).count(),1,'Closing Details keeps the live frame state');
  await detailsButton.click();await details.waitFor({state:'visible'});
  assert.equal(await field.inputValue(),value,'Reopening Details keeps the unfinished form');
 }
 await closeDetails.click();await page.locator('#canvas-options').waitFor({state:'hidden'});await settled();
 await patch({canvasDraft:{},canvasControlsExpanded:false});
 const header=await box('.a-canvas-head'),body=await box('.a-canvas-body'),iframe=await box('.a-canvas-html');
 assert.equal(header.height,52);assert.ok(Math.abs(body.x-iframe.x)<1&&Math.abs(body.width-iframe.width)<1);assert.ok(Math.abs(body.y-iframe.y)<1);assert.ok(Math.abs(iframe.y+iframe.height-950)<1);
 await separator.focus();await page.keyboard.press('End');await settled();
 const expanded=await box('.a-canvas-panel'),chat=await box('.a-conversation');assert.ok(expanded.width>1000);assert.ok(chat.width>=360&&chat.width<=361);
 const composer=await box('.a-composer');assert.ok(composer.x>=chat.x&&composer.x+composer.width<=chat.x+chat.width,'.a-composer stays inside the 360px conversation');
 // The compact composer owns a separate configuration row, not the removed
 // bottom toolbar. Its options branch is not selected by the window width.
 const configuration=page.locator('.a-composer .a-compose-configuration');
 assert.equal(await configuration.count(),1,'The composer owns one .a-compose-configuration row');
 assert.ok(await configuration.evaluate(el=>el.scrollWidth<=el.clientWidth),'.a-compose-configuration does not overflow at the chat minimum width');
 const modelSettings=configuration.getByRole('button',{name:'Model and reasoning settings',exact:true});
 assert.equal(await modelSettings.count(),1,'.a-compose-configuration contains the Model and reasoning settings trigger');
 const compact=await modelSettings.getAttribute('aria-haspopup')==='dialog';
 assert.equal(compact,true,'ComposerOptions selects compact chat options even in a 1600px window with a 360px chat');
 await modelSettings.click();
 const modelDialog=page.getByRole('dialog',{name:'Model for this chat',exact:true});await modelDialog.waitFor({state:'visible'});
 const models=await modelDialog.boundingBox();assert.ok(models&&models.x>=0&&models.x+models.width<=1600,'Model for this chat stays within the viewport, not the conversation containing block');
 const closeModelSettings=modelDialog.getByRole('button',{name:compact?'Close chat options':'Close model settings',exact:true});
 assert.equal(await closeModelSettings.count(),1,`Model for this chat exposes the ${compact?'compact chat-options':'model-settings'} close action`);
 await closeModelSettings.click();await modelDialog.waitFor({state:'hidden'});
 await capture('wide');
 // Mouse drag reaches the same bounds and persists only its completed preference.
 const handle=await separator.boundingBox();await page.mouse.move(handle.x+handle.width/2,handle.y+80);await page.mouse.down();await page.mouse.move(handle.x+200,handle.y+80,{steps:6});await page.mouse.up();await settled();
 assert.ok((await box('.a-canvas-panel')).width<expanded.width-150,'Dragging Resize canvas right reduces the right-hand .a-canvas-panel by over 150px');
 await patch({navPinned:true,navExpanded:true});await settled();
 const nav=page.getByRole('separator',{name:'Resize navigation'});await nav.focus();await page.keyboard.press('End');await settled();
 assert.ok((await box('.a-nav-slot')).width>700,'Resize navigation End gives .a-nav-slot the remaining width');assert.ok((await box('.a-canvas-panel')).width>=300,'Navigation resize preserves the 300px Canvas minimum');assert.ok((await box('.a-conversation')).width>=360,'Navigation resize preserves the 360px chat minimum');
 await nav.focus();await page.keyboard.press('Home');await separator.focus();await page.keyboard.press('End');await settled();assert.equal(Math.round((await box('.a-nav-slot')).width),216,'Resize navigation Home restores the 216px minimum');assert.equal(Math.round((await box('.a-conversation')).width),360,'Resize canvas End retains the 360px chat minimum beside pinned navigation');
 const wideBody=await box('.a-canvas-body'),wideFrame=await box('.a-canvas-html');
 await detailsButton.click();await details.waitFor({state:'visible'});await settled();assert.equal(await toolbarsMatch(true),true);
 assert.equal(await page.locator('.a-canvas-content').getAttribute('data-details-overlay'),'false','Wide .a-canvas-content uses inline Details, not the narrow overlay');
 const detailsBody=await box('.a-canvas-body');
 assert.equal(detailsBody.y,wideBody.y,'Wide Details preserves .a-canvas-body top');assert.equal(detailsBody.height,wideBody.height,'Wide Details preserves .a-canvas-body height');assert.ok(detailsBody.width<wideBody.width,'Wide Details takes width, not reader height');
 await closeDetails.click();await page.locator('#canvas-options').waitFor({state:'hidden'});await settled();assert.deepEqual(await box('.a-canvas-html'),wideFrame,'Closing wide Details restores the same iframe box');
 await page.getByRole('button',{name:'Focus canvas',exact:true}).click();await page.mouse.move(500,400);await patch({canvasControlsExpanded:false});await settled();
 assert.deepEqual(await box('.a-canvas-panel'),await focusedBounds(),'Focused Canvas fills the viewport below .a-work-header');assert.equal(await page.locator('.a-conversation').evaluate(el=>el.inert),true,'Focused Canvas makes the background conversation inert');
 assert.equal(await frame.getByRole('button',{name:'1',exact:true}).count(),1,'Canvas focus retains the running iframe counter');await frame.getByRole('button',{name:'1',exact:true}).click();
 await capture('focus');
 await page.getByRole('button',{name:'Exit canvas focus',exact:true}).focus();await page.keyboard.press('Escape');await settled();assert.equal(await page.locator('.a-canvas-panel').getAttribute('data-focused'),'false','Host-UI Escape exits Canvas focus');assert.equal(await frame.getByRole('button',{name:'2',exact:true}).count(),1,'Exiting focus retains iframe interaction state');assert.equal(await page.locator('.a-conversation').evaluate(el=>el.inert),false,'Exiting Canvas focus releases conversation inertness');
 // Agent uses the same header-preserving focus action; close resets focus for subsequent reopen.
 await patch({canvasFocused:true});await action('canvas.close',{});await action('canvas.reopen',{});assert.equal(await page.locator('.a-canvas-panel').getAttribute('data-focused'),'false','canvas.close clears focus before canvas.reopen restores the split');
 for(const width of [1050,800,740,700,390]){
  await page.setViewportSize({width,height:844});await settled();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight),`Document does not overflow the ${width}px viewport`);
  const canvas=await box('.a-canvas-panel');assert.ok(canvas.x>=0&&canvas.x+canvas.width<=width,`.a-canvas-panel stays within the ${width}px viewport`);
  if(width>=800)assert.ok((await box('.a-conversation')).width>=360,`.a-conversation retains its 360px minimum at ${width}px`);
 }
 await capture('narrow');
 await patch({canvasFocused:true});await settled();assert.deepEqual(await box('.a-canvas-panel'),await focusedBounds(),'Agent-requested narrow Canvas focus preserves .a-work-header');await page.getByRole('button',{name:'Exit canvas focus',exact:true}).click();
 assert.deepEqual(errors,[]);console.log('Flexible canvas passed: empty-pane separator, minimum widths, drag/keyboard resize, primary actions and Details, edge-to-edge, live focus/escape/restore, agent actions and narrow layouts.');
}catch(error){
 // Keep the original assertion and failure. Hidden ancestors and separate
 // primary/Details hosts must be visible in evidence, not guessed from a boolean.
 try{const diagnostics=await canvasDiagnostics();console.error('Canvas failure diagnostics:',JSON.stringify(diagnostics));await writeFile(join(artifacts,'panel-layout-failure.json'),JSON.stringify({error:error.stack,diagnostics},null,2))}catch(e){console.error('Canvas diagnostics failed:',e.message)}
 try{await capture('failure')}catch(e){console.error('Canvas failure capture failed:',e.message)}
 throw error;
}finally{await browser.close();fixture.kill()}
