import './composer-test-helpers.mjs';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {writeFile} from 'node:fs/promises';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url)),measureOnly=process.argv.includes('--measure-only');
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/chat_render_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,vite;
const evidence={measureOnly};
try{
 const url=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),30000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}});
 });
 const headers={Authorization:'Bearer fixture-browser-control-token','Content-Type':'application/json'};
 const change=async data=>{const response=await fetch(url+'/fixture/render',{method:'POST',headers,body:JSON.stringify(data)});assert.equal(response.status,200);return response.json()};
 vite=await createServer({root:root+'frontend',configFile:root+'frontend/vite.config.js',server:{host:'127.0.0.1',port:0,hmr:false,
  proxy:{'/api':{target:url,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,req=>req.setHeader('Origin',url))}},'/branding':url}},
  plugins:[{name:'render-test-counts',enforce:'pre',transform(code,id){
   if(id.endsWith('/src/main.jsx')){
    const needle='function App(){';assert.equal(code.split(needle).length,2);
    return code.replace(needle,needle+' globalThis.renderProbe.apps++;');
   }
   if(id.endsWith('/src/markdown.js')){
    const needle='remarkPlugins:[remarkGfm,';assert.equal(code.split(needle).length,2);
    return code.replace(needle,'remarkPlugins:[()=>()=>{globalThis.renderProbe.parses.push(original)},remarkGfm,');
   }
  }}]});
 await vite.listen();
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({viewport:{width:1400,height:950},extraHTTPHeaders:{Authorization:headers.Authorization}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(()=>{
  window.renderProbe={apps:0,parses:[]};
  const intervals=new Map(),nativeSet=setInterval,nativeClear=clearInterval;let id=-1,offset=0;
  const nativeNow=Date.now;Date.now=()=>nativeNow()+offset;
  window.setInterval=(fn,ms,...args)=>{if(ms!==1000)return nativeSet(fn,ms,...args);const key=id--;intervals.set(key,()=>fn(...args));return key};
  window.clearInterval=key=>intervals.has(key)?intervals.delete(key):nativeClear(key);
  window.tickActivity=()=>{offset+=1000;for(const fn of intervals.values())fn()};
 });
 await page.goto(vite.resolvedUrls.local[0]);
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.waitFor();await page.locator('[data-message-id="message-79"] strong').waitFor();
 await expect(page.getByRole('timer')).toBeVisible();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const selected=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 const stableText=await page.evaluate(()=>window.amplifier.getState().sessions.find(row=>row.id===window.amplifier.getState().selectedSessionId).messages.find(row=>row.id==='message-79').text);
 const counts=()=>page.evaluate(text=>({apps:renderProbe.apps,stableParses:renderProbe.parses.filter(value=>value===text).length}),stableText);
 const settleRender=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 // A state receipt precedes React's draft effect and its scheduled layout work.
 // Observe the visible draft and drain frames before measuring timer-only work.
 await action('view.update',{patch:{draft:'Ready'}});
 await expect(composer).toHaveDraft('Ready');
 await expect(composer).toHaveText('Ready');
 await settleRender();
 let before=await counts(),elapsed=await page.getByRole('timer').innerText();
 for(let i=0;i<3;i++){
  await page.evaluate(()=>window.tickActivity());
  await expect(page.getByRole('timer')).not.toHaveText(elapsed);elapsed=await page.getByRole('timer').innerText();
 }
 // Count delayed work as well; do not let the final tick escape the assertion.
 await settleRender();
 let after=await counts();evidence.clock={apps:after.apps-before.apps,stableParses:after.stableParses-before.stableParses,ticks:3};
 before=await counts();
 await composer.fill('Private draft');
 await action('view.update',{patch:{draft:'Private draft'}});
 await expect(composer).toHaveDraft('Private draft');
 await expect(composer).toHaveText('Private draft');
 await settleRender();
 after=await counts();evidence.draft={apps:after.apps-before.apps,stableParses:after.stableParses-before.stableParses};
 before=await counts();
 for(let i=0;i<3;i++){
  await change({workers:[{id:'active-child',kind:'session',callId:'active-call',status:'working',detail:'Progress '+i}]});
  await page.waitForFunction(i=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId).workers[0]?.detail==='Progress '+i,i);
 }
 await settleRender();
 after=await counts();evidence.workerUpdates={stableParses:after.stableParses-before.stableParses,updates:3};
 await change({workers:[]});
 // Changed body and streaming remain live; these are positive controls.
 const replacement='Changed **response** with `code`';
 await change({text:replacement,streaming:'New **streamed** text'});
 await expect(page.locator('[data-message-id="message-79"]')).toContainText('Changed response');
 await expect(page.locator('.a-messages')).toContainText('New streamed text');
 assert.ok(await page.evaluate(text=>renderProbe.parses.includes(text),replacement));
 await change({status:'idle',streaming:''});
 await expect(page.getByRole('timer')).toHaveCount(0);
 const user=page.locator('[data-message-id="message-78"]');
 await user.getByRole('button',{name:'Edit message',exact:true}).click();
 await expect(user.getByRole('textbox',{name:'Edit your message'})).toBeVisible();
 await user.getByRole('button',{name:'Cancel',exact:true}).click();
 await expect(user.locator('.a-markdown')).toContainText('Saved message 78');
 // Keep earlier-history loading and chat-specific drafts working.
 await page.getByRole('button',{name:'Load earlier conversation',exact:true}).click();
 await expect(page.locator('[data-message-id="message-0"]')).toBeAttached();
 await action('session.create');
 await expect(composer).toHaveDraft('');
 await action('session.select',{id:selected});
 await expect(composer).toHaveDraft('Private draft');
 assert.deepEqual(errors,[]);
 evidence.controls={changedText:true,streaming:true,editing:true,olderPage:true,privateDraft:true,pageErrors:errors};
 console.log(JSON.stringify(evidence,null,2));
 if(!measureOnly){assert.equal(evidence.clock.stableParses,0);assert.equal(evidence.clock.apps,0);assert.equal(evidence.draft.stableParses,0);assert.equal(evidence.workerUpdates.stableParses,0)}
}finally{
 if(process.env.AMPLIFIER_PERF_EVIDENCE)await writeFile(process.env.AMPLIFIER_PERF_EVIDENCE,JSON.stringify(evidence,null,2)+'\n');
 await browser?.close();await vite?.close();
 if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit')}
}