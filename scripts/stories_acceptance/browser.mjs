import {pathToFileURL} from 'node:url';
const {chromium}=await import(pathToFileURL(process.env.STORIES_PLAYWRIGHT));
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const base=process.env.STORIES_STATE,url=process.env.STORIES_URL,server=process.env.STORIES_SERVER;
const identity=JSON.parse(await fs.readFile(base+'/identity.json','utf8'));
const report=JSON.parse(await fs.readFile(base+'/joined-report.json','utf8'));
const token=(await fs.readFile(process.env.STORIES_TOKEN_FILE,'utf8')).trim();
const browser=await chromium.launch({headless:true,args:JSON.parse(process.env.AMPLIFIER_CHROMIUM_ARGS||'[]')});
const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true,extraHTTPHeaders:{Authorization:'Bearer '+token}});
const page=await context.newPage(),errors=[],calls=[];page.on('pageerror',e=>errors.push(e.message));
page.on('request',r=>{if(r.method()==='POST'){try{const p=r.postDataJSON();if(p?.name)calls.push(p.name);else if(p?.args?.name)calls.push(p.args.name)}catch{}}});
try{
 await page.goto(url);await page.waitForFunction(()=>window.amplifier?.getState());
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 await action('session.select',{id:identity.sessionId});
 await action('view.update',{patch:{panel:null,canvasControlsPinned:true}});
 const opened=await action('smartTools.open',{id:server,tool:'stories_create_storyboard',operationId:report.operationId,sessionId:identity.sessionId});
 await fs.writeFile(base+'/opened.json',JSON.stringify(opened,null,2));
 await page.locator('.a-mcp-app-viewer iframe').waitFor({timeout:20000});
 const frame=page.frameLocator('.a-mcp-app-viewer iframe');
 await frame.frameLocator('#material').getByRole('heading',{name:'A fictional request',exact:true}).waitFor({timeout:30000});
 if (!await frame.getByLabel('Export format',{exact:true}).isVisible()) {
  if (!await frame.getByRole('button',{name:'Details',exact:true}).isVisible()) await frame.getByRole('button',{name:'View & comments',exact:true}).click();
  await frame.getByRole('button',{name:'Details',exact:true}).click();
 }
 await frame.getByLabel('Export format',{exact:true}).selectOption('zip');
 const downloadPromise=page.waitForEvent('download',{timeout:15000});
 await frame.getByRole('button',{name:'Export this version',exact:true}).click();
 const download=await downloadPromise;await download.saveAs(base+'/native-export.zip');
 assert.equal(await download.failure(),null);
 await page.screenshot({path:base+'/native-review.png',fullPage:true});
 await fs.writeFile(base+'/native-review-dom.txt',await frame.locator('body').innerText());
 await fs.writeFile(base+'/native-review-controls.json',JSON.stringify(await frame.locator('button,input,select,textarea').evaluateAll(els=>els.map(e=>({tag:e.tagName,id:e.id,text:e.innerText,label:e.getAttribute('aria-label'),type:e.type}))),null,2));
 const canvas=await page.evaluate(()=>window.amplifier.getState().canvas);
 const operation=async(name,args)=>{const r=await action(name,args);if(!r.operationId)return r;for(let i=0;i<100;i++){const row=await (await page.request.get(url+'/api/smart-tools/operations/'+r.operationId)).json();if(row.status==='completed')return row;if(row.status==='failed')throw Error(row.error);await page.waitForTimeout(100)}throw Error('Timed out '+name)};
 await operation('smartTools.disconnect',{id:server});
 await page.getByRole('button',{name:'Reconnect tool view',exact:true}).click();
 await page.locator('.a-mcp-status[data-phase="ready"]').waitFor({state:'attached'});
 await frame.frameLocator('#material').getByRole('heading',{name:'A fictional request',exact:true}).waitFor();
 await page.reload();await page.waitForFunction(()=>window.amplifier?.getState());
 await action('session.select',{id:identity.sessionId});await action('canvas.select',{id:canvas.id});
 await frame.frameLocator('#material').getByRole('heading',{name:'A fictional request',exact:true}).waitFor();
 assert.deepEqual(errors,[]);
 assert.equal(calls.filter(name=>name==='stories_create_storyboard').length,0);
 await fs.writeFile(base+'/browser-first.json',JSON.stringify({passed:true,errors,canvasId:canvas.id,reconnect:true,reload:true,calls,importCallsOnView:0},null,2));
 console.log(JSON.stringify({passed:true,errors,frames:page.frames().length}));
}catch(e){await page.screenshot({path:base+'/browser-failure.png',fullPage:true});await fs.writeFile(base+'/browser-failure.json',JSON.stringify({error:String(e),errors,body:await page.locator('body').innerText()},null,2));throw e}finally{await context.close();await browser.close()}
