import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url));
const python=process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python';
const directory=await mkdtemp(join(tmpdir(),'mcp-recovery-'));
const htmlPath=join(directory,'app.html'),callsPath=join(directory,'calls.jsonl'),modePath=join(directory,'mode');
const source=`import {App} from '@modelcontextprotocol/ext-apps';
const app=new App({name:'Saved recovery fixture',version:'1.0.0'},{});
const draw=result=>document.querySelector('#count').textContent=String(result.structuredContent.count);
app.ontoolresult=draw;
document.querySelector('#add').onclick=async()=>{try{draw(await app.callServerTool({name:'counter_add',arguments:{amount:1}}))}catch(error){document.querySelector('#error').textContent=error.message}};
await app.connect();document.body.dataset.booted='true';`;
const built=await build({stdin:{contents:source,resolveDir:process.cwd(),sourcefile:'recovery.js'},bundle:true,write:false,format:'esm'});
await writeFile(htmlPath,`<!doctype html><h1>Original saved document</h1><output id="count"></output><button id="add">Add one</button><input aria-label="Unfinished tool input"><p id="error"></p><script type="module">${built.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`);
let fixture,browser;
async function start(port=0){
 fixture=spawn(python,[root+'tests/fixtures/canvas_restart_ui_server.py',directory,String(port)],{stdio:['ignore','pipe','inherit'],env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
 return new Promise((resolve,reject)=>{
  let output='';const timeout=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);
  fixture.once('exit',code=>{clearTimeout(timeout);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timeout);resolve(value.url)}}catch{}}});
 });
}
async function stop(){if(fixture&&fixture.exitCode===null){const exited=once(fixture,'exit');fixture.kill('SIGTERM');await exited}fixture=null}
const mutations=async()=>((await readFile(callsPath,'utf8')).trim().split('\n').filter(Boolean)).length;
try{
 const url=await start();
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1450,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const operation=async(name,args)=>{
  const receipt=await action(name,args);
  if(!receipt.operationId)return receipt.result;
  for(let attempt=0;attempt<200;attempt++){
   const row=await (await page.request.get(url+'/api/smart-tools/operations/'+receipt.operationId)).json();
   if(row.status==='completed')return row.result;
   if(row.status==='failed')throw Error(row.error);
   await page.waitForTimeout(50);
  }
  throw Error('Operation did not finish: '+name);
 };
 await action('session.create');
 await action('view.update',{patch:{canvasControlsPinned:true}});
 await operation('smartTools.configure',{id:'recovery',name:'Recovery fixture',command:python,args:[root+'tests/fixtures/canvas_recovery_mcp_server.py',htmlPath,callsPath,modePath]});
 await operation('smartTools.connect',{id:'recovery'});
 const launch=await action('smartTools.call',{id:'recovery',name:'counter_add',arguments:{amount:7}});
 await page.waitForFunction(id=>window.amplifier.getState().smartTools.operations.some(row=>row.id===id&&row.status==='completed'),launch.operationId);
 await operation('smartTools.open',{id:'recovery',tool:'counter_add',operationId:launch.operationId});
 const frame=page.frameLocator('.a-mcp-app-viewer iframe');
 await expect(frame.locator('body')).toHaveAttribute('data-booted','true');
 await expect(frame.locator('#count')).toHaveText('7');
 const snapshot=()=>page.evaluate(()=>{const state=window.amplifier.getState(),row=state.canvasArtifacts.find(row=>row.id===state.canvas.id);return {id:row.id,body:row.body,messageId:row.messageId,artifacts:state.canvasArtifacts.length,session:state.selectedSessionId,launch:state.canvas.mcp.operationId,arguments:state.canvas.mcp.toolArguments,grants:state.canvas.mcp.allowedTools}});
 const before=await snapshot();
 assert.equal(await mutations(),1);
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep the unsent message');
 await action('view.update',{patch:{draft:'Keep the unsent message'}});
 await operation('smartTools.disconnect',{id:'recovery'});
 await page.reload();
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveValue('Keep the unsent message');
 await expect(frame.getByRole('heading',{name:'Original saved document'})).toBeVisible();
 await expect(page.getByRole('status').filter({hasText:'Saved document available.'})).toBeVisible();
 await frame.getByRole('textbox',{name:'Unfinished tool input'}).fill('Keep me through reconnect');
 await frame.locator('body').evaluate(body=>body.dataset.retained='yes');
 await page.getByRole('button',{name:'Reconnect tool view',exact:true}).click();
 await expect(page.locator('.a-mcp-status')).toHaveAttribute('data-phase','ready');
 await expect(frame.getByRole('textbox',{name:'Unfinished tool input'})).toHaveValue('Keep me through reconnect');
 await expect(frame.locator('body')).toHaveAttribute('data-retained','yes');
 assert.equal(await mutations(),1);
 assert.deepEqual(await snapshot(),before);
 await frame.getByRole('button',{name:'Add one'}).click();
 await expect(frame.locator('#count')).toHaveText('8');
 assert.equal(await mutations(),2);
 // Replace only the server's current resource, then restart the real host.
 // Recovery must use the immutable saved source, not read today's resource.
 await writeFile(htmlPath,'<!doctype html><h1>Changed live resource</h1>');
 await stop();await start(Number(new URL(url).port));await page.reload();
 await expect(frame.getByRole('heading',{name:'Original saved document'})).toBeVisible();
 await expect(frame.locator('#count')).toHaveText('7');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveValue('Keep the unsent message');
 await page.getByRole('button',{name:'Reconnect tool view',exact:true}).click();
 await expect(page.locator('.a-mcp-status')).toHaveAttribute('data-phase','ready');
 assert.deepEqual(await snapshot(),before);
 assert.equal(await mutations(),2);
 // An actual changed MCP schema must stay rejected after discovery.
 await operation('smartTools.disconnect',{id:'recovery'});
 await writeFile(modePath,'changed');await page.reload();
 await expect(frame.getByRole('heading',{name:'Original saved document'})).toBeVisible();
 await page.getByRole('button',{name:'Reconnect tool view',exact:true}).click();
 await expect(page.locator('.a-mcp-status')).toContainText('action schemas changed');
 assert.equal(await mutations(),2);
 assert.deepEqual(await snapshot(),before);
 await frame.getByRole('button',{name:'Add one'}).click();
 await expect(frame.locator('#error')).toContainText('action schemas changed');
 assert.equal(await mutations(),2);
 await page.screenshot({path:process.env.MCP_RECOVERY_SCREENSHOT||join(directory,'mcp-recovery.png')});
 assert.deepEqual(errors,[]);
 console.log('MCP recovery browser passed: actual MCP SDK, disconnect and host restart, same artifact/source hash, historical launch receipt, explicit unchanged-contract rebind without frame/input replacement, no replayed mutations, changed schema denied, composer preserved.');
}finally{await browser?.close();await stop();await rm(directory,{recursive:true,force:true})}
