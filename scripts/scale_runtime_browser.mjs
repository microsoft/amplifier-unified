// Private scale + real local workers; canonical copied owners remain read-only.
import {chromium,expect} from '/opt/unified-dev/source/frontend/node_modules/@playwright/test/index.mjs';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const base='/var/lib/unified-scale-runtime-20261015',url='http://127.0.0.1:8943';
const ready=JSON.parse(await fs.readFile(base+'/run/ready.json','utf8'));
const token=(await fs.readFile(base+'/writable/app/config/auth/control-token','utf8')).trim();
const out=base+'/browser';await fs.mkdir(out,{recursive:true,mode:0o700});
const q=values=>{const s=values.toSorted((a,b)=>a-b);return {count:s.length,p50:s[Math.floor(s.length*.5)]??null,p95:s[Math.min(s.length-1,Math.floor(s.length*.95))]??null,max:s.at(-1)??null}};
const result={source:ready.source,context:'private copied catalog and primary history + real synthetic local workers',eventsVerifiedShards:10,eventsTotalShards:32,fullEventFidelity:false,runtimeQualificationBypassed:true,provider:'deterministic local 250ms',stages:[],errors:[],startedAt:new Date().toISOString()};
let browser,page;
try{
 browser=await chromium.launch({headless:true,args:['--no-sandbox','--no-zygote','--single-process','--disable-gpu']});
 page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer '+token}});page.setDefaultTimeout(20000);
 page.on('pageerror',e=>result.errors.push(e.message));
 await page.addInitScript(()=>{window.__bench={after:Date.now(),seen:new Set(),samples:[]};let queued=false;const scan=()=>{const el=document.querySelector('.a-messages');if(!el)return;const now=Date.now();for(const m of(el.textContent||'').matchAll(/\[BENCH:(\d+):(\d+):(\d+)\]/g)){const at=Number(m[3]);if(at<window.__bench.after||window.__bench.seen.has(m[0]))continue;window.__bench.seen.add(m[0]);window.__bench.samples.push({lagMs:now-at,pid:Number(m[1])})}};new MutationObserver(()=>{if(!queued){queued=true;requestAnimationFrame(()=>{queued=false;scan()})}}).observe(document,{subtree:true,childList:true,characterData:true})});
 const inspect=async(reset=false)=>{const r=await page.request.get(url+'/fixture/runtime'+(reset?'?reset=1':''));assert.equal(r.status(),200);return r.json()};
 const action=async(action,args)=>{const r=await page.request.post(url+'/api/actions',{headers:{'X-Amplifier-State-Transport':'delta-v1'},data:{action,args}});assert.equal(r.status(),200,await r.text());const b=await r.json();assert.equal(b.accepted,true);return b};
 const nav=async()=>{const b=page.getByRole('button',{name:'Open navigation',exact:true});if(await b.isVisible())await b.click()};
 const row=id=>page.locator(`.a-nav-chat[data-session-id="${id}"]`).first();
 const choose=async id=>{await nav();const t=performance.now();await row(id).locator('[data-navigation-select]').click();await expect(page.locator('.a-messages')).toHaveAttribute('data-session-id',id);await expect(page.locator('.a-messages [data-message-id]').first()).toBeVisible();return performance.now()-t};
 const health=async()=>{const v=[];for(let i=0;i<8;i++){const t=performance.now();const r=await page.request.get(url+'/api/health');assert.equal(r.status(),200);v.push(performance.now()-t)}return q(v)};
 await page.goto(url,{waitUntil:'domcontentloaded'});
 await page.addLocatorHandler(page.getByRole('button',{name:'Close settings',exact:true}),async b=>b.click());
 await nav();await row(ready.ids[13]).waitFor();
 result.build=await(await page.request.get(url+'/build.json')).json();
 const copied=page.locator('[data-sidebar-section="pinned"] [data-session-id]').first();await copied.waitFor();const copiedId=await copied.getAttribute('data-session-id');assert.ok(!ready.ids.includes(copiedId));
 for(const count of [0,6,12]){
  const stage={activeTarget:count,baselineActive:Math.max(0,count-1),measuredActive:Math.max(1,count),selectionMs:[],copiedNavigationMs:[],healthBefore:await health()};result.stages.push(stage);
  const fg=count?ready.ids[count-1]:ready.ids[13],bg=count?ready.ids.slice(0,count-1):[];
  await Promise.all(bg.map(sessionId=>action('conversation.send',{sessionId,text:'Synthetic background stream'})));
  if(bg.length)await expect.poll(async()=>{const s=await inspect();return bg.filter(id=>s.sessions.some(r=>r.id===id&&r.status==='working'&&r.streamingBytes>0)&&s.workers.some(w=>w.id===id&&w.returncode===null)).length},{timeout:60000}).toBe(bg.length);
  stage.before=await inspect(true);
  await choose(fg);await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Synthetic foreground stream until stopped');
  await page.evaluate(()=>{window.__bench.after=Date.now();window.__bench.samples=[]});
  const response=page.waitForResponse(r=>{try{const d=r.request().postDataJSON();return new URL(r.url()).pathname==='/api/actions'&&(d.action==='conversation.send'||d.action==='shell.command'&&(d.args?.command||d.args)?.action==='conversation.send')}catch{return false}});
  const t=performance.now();await page.getByRole('button',{name:'Send message',exact:true}).click();const ack=await response;assert.equal(ack.status(),200,await ack.text());assert.equal((await ack.json()).accepted,true);stage.sendAckMs=performance.now()-t;
  await page.waitForFunction(()=>window.__bench.samples.length>0,{},{timeout:30000});stage.firstVisibleMs=performance.now()-t;
  await expect.poll(async()=>(await inspect()).workers.filter(w=>w.returncode===null).length,{timeout:30000}).toBe(Math.max(1,count));
  for(let i=0;i<3;i++){stage.copiedNavigationMs.push(await choose(copiedId));stage.selectionMs.push(await choose(fg))}
  await page.evaluate(()=>{window.__bench.after=Date.now();window.__bench.samples=[]});await page.waitForTimeout(6000);
  stage.streamLag=q((await page.evaluate(()=>window.__bench.samples)).map(r=>r.lagMs));assert.ok(stage.streamLag.count>0);
  stage.healthLoaded=await health();stage.loaded=await inspect();assert.ok(stage.loaded.workers.every(w=>ready.ids.includes(w.id)));
  const stop=performance.now();await page.getByRole('button',{name:'Stop response',exact:true}).click();await expect(page.getByRole('button',{name:'Stop response',exact:true})).toHaveCount(0);stage.stopUiMs=performance.now()-stop;
  for(const sessionId of bg)await action('conversation.stop',{sessionId});
  await expect.poll(async()=>(await inspect()).workers.length,{timeout:30000}).toBe(0);
  stage.afterStop=await inspect();stage.selection=q(stage.selectionMs);stage.copiedNavigation=q(stage.copiedNavigationMs);
  console.log(JSON.stringify({stage:count,stream:stage.streamLag,health:stage.healthLoaded,selection:stage.selection,copied:stage.copiedNavigation}));
 }
 assert.deepEqual(result.errors,[]);result.passed=true;
}catch(e){result.failure={name:e.name,message:e.message};process.exitCode=1;await page?.screenshot({path:out+'/failure.png'}).catch(()=>{});}
finally{if(page){try{const r=await page.request.get(url+'/fixture/runtime');const state=await r.json();for(const w of state.workers){assert.ok(ready.ids.includes(w.id));await page.request.post(url+'/api/actions',{headers:{'X-Amplifier-State-Transport':'delta-v1'},data:{action:'conversation.stop',args:{sessionId:w.id}}})}await expect.poll(async()=>{const r=await page.request.get(url+'/fixture/runtime');return (await r.json()).workers.length},{timeout:30000}).toBe(0);result.workersStopped=true}catch(e){result.cleanupFailure=e.message;process.exitCode=1}}result.finishedAt=new Date().toISOString();await fs.writeFile(out+'/results.json',JSON.stringify(result,null,2),{mode:0o600});await browser?.close();}
console.log(JSON.stringify({passed:result.passed,failure:result.failure?.message?.slice(0,400)}));
