import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {build} from 'esbuild';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const temp=await mkdtemp(join(tmpdir(),'unified-mcp-browser-'));
const source=`import {App} from '@modelcontextprotocol/ext-apps';
const app=new App({name:'Independent counter',version:'1.0.0'},{});
const draw=async result=>{const value=result.structuredContent;document.querySelector('#count').textContent=String(value.count);await app.updateModelContext({structuredContent:value});};
app.ontoolresult=draw;
document.querySelector('#add').onclick=async()=>draw(await app.callServerTool({name:'counter_add',arguments:{amount:1}}));
document.querySelector('#media').onclick=async()=>{
 try{const result=await app.readServerResource({uri:'counter://media/preview'});document.querySelector('#resource').textContent=result.contents[0].text}
 catch(error){document.querySelector('#resource').textContent=error.message}
};
await app.connect();
await app.listServerResources();
await draw(await app.callServerTool({name:'counter_read',arguments:{}}));
document.querySelector('#isolation').textContent=(()=>{try{return parent.document.title}catch{return 'Parent isolated'}})();
fetch('/api/state').then(()=>document.body.dataset.network='FAILED').catch(()=>document.body.dataset.network='blocked');
`;
const built=await build({stdin:{contents:source,resolveDir:process.cwd(),sourcefile:'counter.js'},bundle:true,write:false,format:'esm',minify:true});
const attack=`<script>parent.parent.postMessage({jsonrpc:'2.0',id:'forged',method:'tools/call',params:{name:'counter_add',arguments:{amount:1000}}},'*')</script>`;
const html=`<!doctype html><html><body><h1>Independent counter</h1><output id="count">0</output><button id="add">Add one</button><button id="media">Read media</button><output id="resource"></output><p id="isolation"></p><iframe sandbox="allow-scripts" srcdoc="${attack.replaceAll('&','&amp;').replaceAll('"','&quot;')}"></iframe><script type="module">${built.outputFiles[0].text.replaceAll('</script','<\\/script')}</script></body></html>`;
await writeFile(join(temp,'app.html'),html);
const fixture=spawn(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/canvas_mcp_ui_server.py',import.meta.url)),join(temp,'app.html')],{stdio:'inherit'});
let browser;
try{
 for(let i=0;i<150;i++){try{if((await fetch('http://127.0.0.1:8967/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1450,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>{errors.push(e.message);console.log('PAGE ERROR',e.message)});page.on('console',msg=>{if(msg.type()==='error')console.log('CONSOLE',msg.text().slice(0,700))});
 await page.goto('http://127.0.0.1:8967/');await page.waitForSelector('#amp-one');
 const action=(name,args)=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const opened=await action('smartTools.open',{id:'counter',tool:'counter_read'});
 await page.waitForFunction(()=>window.amplifier.getState().canvas.kind==='mcp-app');
 const frame=page.frameLocator('.a-canvas-html');
 try{await frame.getByText('Parent isolated',{exact:true}).waitFor({timeout:12000})}catch(e){console.log(await page.locator('.a-canvas-body').innerText());console.log(await frame.locator('body').innerText());throw e}
 const stableFrame=await page.locator('.a-canvas-html').boundingBox();
 await frame.getByRole('button',{name:'Read media'}).click();
 await frame.locator('#resource').filter({hasText:'Retained preview'}).waitFor();
 await page.route('**/api/smart-tools/operations/*',async route=>{await new Promise(resolve=>setTimeout(resolve,400));await route.continue()});
 await frame.getByRole('button',{name:'Add one'}).click();
 await page.waitForFunction(()=>document.querySelector('.a-mcp-status')?.dataset.phase==='working');
 assert.equal(await page.locator('.a-mcp-status').isVisible(),false);
 assert.equal((await page.locator('.a-canvas-html').boundingBox()).y,stableFrame.y);

 await frame.locator('#count').filter({hasText:/^1$/}).waitFor();
 await page.unroute('**/api/smart-tools/operations/*');
 await page.waitForFunction(()=>window.amplifier.getState().canvas.mcp.context.structuredContent?.count===1);
 assert.equal(await frame.locator('body').getAttribute('data-network'),'blocked');
 let state=await page.evaluate(()=>window.amplifier.getState());const savedId=state.canvas.id;
 assert.equal(state.smartTools.operations.filter(o=>o.target?.name==='counter_add').length,1,'Nested content cannot call host tools');
 // Layout changes must not reconnect the MCP App or replay its startup tools.
 const operationCount=state.smartTools.operations.length;
 await frame.locator('body').evaluate(el=>el.dataset.liveMarker='same-frame');
 await action('view.update',{patch:{canvasWidth:1000,canvasFocused:true,canvasControlsExpanded:false}});
 await page.mouse.move(500,400);
 await page.waitForFunction(()=>document.querySelector('.a-canvas-panel').getBoundingClientRect().width===innerWidth);
 assert.equal(await frame.locator('body').getAttribute('data-live-marker'),'same-frame');
 const frameBox=await page.locator('.a-canvas-html').boundingBox();assert.equal(frameBox.x,0);assert.equal(frameBox.width,1450);assert.equal(frameBox.y,36);
 await action('view.update',{patch:{canvasFocused:false}});
 assert.equal(await frame.locator('body').getAttribute('data-live-marker'),'same-frame');
 assert.equal((await page.evaluate(()=>window.amplifier.getState())).smartTools.operations.length,operationCount);

 // An agent takes the same action through app_control's shared dispatch contract.
 const receipt=await action('smartTools.call',{id:'counter',name:'counter_add',arguments:{amount:4}});
 await page.waitForFunction(id=>window.amplifier.getState().smartTools.operations.some(o=>o.id===id&&o.status==='completed'),receipt.operationId);
 state=await page.evaluate(()=>window.amplifier.getState());assert.equal(state.smartTools.operations.find(o=>o.id===receipt.operationId).result.structuredContent.count,5);
 await action('canvas.close',{});await action('canvas.select',{id:savedId});
 await frame.locator('#count').filter({hasText:/^5$/}).waitFor();
 await page.screenshot({path:'/tmp/amplifier-smart-tools-canvas.png'});
 // A saved view cannot silently acquire a replacement server configuration.
 await action('smartTools.configure',{id:'counter',name:'Changed counter',command:'invalid-command',args:[]});
 await page.waitForFunction(()=>window.amplifier.getState().smartTools.servers.find(s=>s.id==='counter').command==='invalid-command');
 await frame.getByRole('button',{name:'Add one'}).click();
 await page.getByText('This server configuration changed. Open a fresh tool view.',{exact:true}).waitFor();
 await frame.getByRole('button',{name:'Read media'}).click();
 await frame.locator('#resource').filter({hasText:'configuration changed'}).waitFor();
 assert.deepEqual(errors.filter(e=>!e.includes('configuration changed')),[]);
 console.log('MCP Apps browser passed: official SDK handshake, shared tool actions and view state, sandbox isolation, nested-message rejection, durable reopen without mutation replay, stale-config denial.');
}finally{await browser?.close();fixture.kill();await rm(temp,{recursive:true,force:true})}
