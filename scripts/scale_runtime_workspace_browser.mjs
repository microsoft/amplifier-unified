// Private scale + real local workers; canonical copied owners remain read-only.
import {chromium,expect} from '/opt/unified-dev/source/frontend/node_modules/@playwright/test/index.mjs';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const base='/var/lib/unified-scale-runtime-20261015',url='http://127.0.0.1:8943';
const ready=JSON.parse(await fs.readFile(base+'/run/ready.json','utf8'));
const token=(await fs.readFile(base+'/writable/app/config/auth/control-token','utf8')).trim();
const target=JSON.parse(await fs.readFile(base+'/run/workspace-target.json','utf8'));
const activeTarget=Number(process.argv[2]);assert.ok([0,6,12].includes(activeTarget));
const tier=JSON.parse(await fs.readFile(base+'/run/capture-tier.json','utf8'));
const out=base+'/workspace-browser/'+activeTarget;await fs.mkdir(out,{recursive:true,mode:0o700});
const q=values=>{const s=values.toSorted((a,b)=>a-b);return {count:s.length,p50:s[Math.floor(s.length*.5)]??null,p95:s[Math.min(s.length-1,Math.floor(s.length*.95))]??null,max:s.at(-1)??null}};
const result={source:ready.source,context:'fresh browser first workspace demand and retained-worker send at copied scale; existing service and filesystem caches',workspaceCandidateNativeRoots:target.nativeRootRows,desiredWarmAckMs:300,eventsVerifiedShards:tier.verifiedEventShards,eventsTotalShards:32,fullEventFidelity:false,runtimeQualificationBypassed:true,provider:'deterministic local 250ms',stages:[],errors:[],startedAt:new Date().toISOString()};
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
 const ids=()=>page.locator('.a-work-browser .a-nav-chats [data-session-id]').evaluateAll(rows=>rows.map(r=>r.dataset.sessionId));
 const settled=async(filter,index)=>{await page.waitForFunction(({wid,filter,index})=>{const s=window.amplifier.getShellState()?.snapshots?.chats,p=s?.sidebarNavigation?.workspace;return p?.scope?.workspaceId===wid&&p.scope.filter===filter&&p.index===index&&!s.navigationResourcePending?.sidebarWorkspace},{wid:target.workspaceId,filter,index});return page.evaluate(()=>{const p=window.amplifier.getShellState().snapshots.chats.sidebarNavigation.workspace;return {total:p.total,index:p.index,items:p.items.map(r=>r.id),pages:p.pages}})};
 let activePhase=null;
 page.on('response',r=>{if(!activePhase||new URL(r.url()).pathname!=='/api/actions')return;try{const d=r.request().postDataJSON(),c=d.action==='shell.command'?(d.args?.command||d.args):d;activePhase.actionReceipts.push({action:c.action,status:r.status(),elapsedMs:performance.now()-activePhase.started})}catch{}});
 const measure=async(name,gesture,filter,index)=>{const prior=await page.evaluate(()=>window.amplifier.getShellState()?.snapshots?.chats?.sidebarNavigation?.workspace);assert.ok(!prior||prior.scope.workspaceId!==target.workspaceId||prior.scope.filter!==filter||prior.index!==index,'Measured gesture must change the current requested page');const m={name,started:performance.now(),actionReceipts:[]};activePhase=m;result.incompletePhase=m;const receipt=page.waitForResponse(r=>{try{if(new URL(r.url()).pathname!=='/api/actions')return false;const d=r.request().postDataJSON(),c=d.action==='shell.command'?(d.args?.command||d.args):d,patch=c.args?.patch||{};return ['view.update','shell.view.update'].includes(c.action)&&(name==='first workspace demand'?patch.workSurface==='workspace'&&patch.workWorkspaceId===target.workspaceId:name.includes('page')?patch.navChatPage?.index===index:patch.navFilter===filter)}catch{return false}}).then(async r=>{const ms=performance.now()-m.started;assert.equal(r.status(),200);assert.equal((await r.json()).accepted,true);return ms});await gesture();const p=await settled(filter,index);m.pageSettledMs=performance.now()-m.started;await expect.poll(ids).toEqual(p.items);await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));m.visibleMs=performance.now()-m.started;m.total=p.total;m.rows=p.items.length;m.targetActionAckMs=await receipt;delete m.started;activePhase=null;delete result.incompletePhase;return {m,p}};
 const sendUi=async text=>{await page.getByRole('textbox',{name:'Message Amplifier'}).fill(text);await page.evaluate(()=>{window.__bench.after=Date.now();window.__bench.samples=[]});await expect(page.getByRole('button',{name:'Send message',exact:true})).toBeEnabled();const response=page.waitForResponse(r=>{try{const d=r.request().postDataJSON(),c=d.action==='shell.command'?(d.args?.command||d.args):d;return new URL(r.url()).pathname==='/api/actions'&&c.action==='conversation.send'}catch{return false}});const t=performance.now(),wall=Date.now();await page.getByRole('button',{name:'Send message',exact:true}).click();const r=await response;assert.equal(r.status(),200,await r.text());assert.equal((await r.json()).accepted,true);const ackMs=performance.now()-t;await page.waitForFunction(()=>window.__bench.samples.length>0,{},{timeout:30000});const timing=r.request().timing();return {ackMs,firstVisibleMs:performance.now()-t,gestureToRequestMs:timing.startTime-wall,requestToResponseHeadersMs:timing.responseStart,requestToBodyMs:timing.responseEnd}};
 for(const count of [activeTarget]){
  const stage={activeTarget:count,backgroundWorkers:Math.max(0,count-1),actualWorkers:Math.max(1,count),workspace:[]};result.stages.push(stage);
  const fg=count?ready.ids[count-1]:ready.ids[13],bg=count?ready.ids.slice(0,count-1):[];
  await Promise.all(bg.map(sessionId=>action('conversation.send',{sessionId,text:'Synthetic background workspace measurement'})));
  if(bg.length)await expect.poll(async()=>{const s=await inspect();return bg.filter(id=>s.sessions.some(r=>r.id===id&&r.status==='working'&&r.streamingBytes>0)&&s.workers.some(w=>w.id===id&&w.returncode===null)).length},{timeout:60000}).toBe(bg.length);
  await choose(fg);stage.prepareRetainedWorker=await sendUi('BENCH_FINISH_FAST Prepare retained worker');
  await expect.poll(async()=>(await inspect()).sessions.find(r=>r.id===fg)?.status,{timeout:15000}).toBe('idle');
  const retained=(await inspect()).workers.find(r=>r.id===fg&&r.returncode===null);assert.ok(retained);
  stage.before=await inspect(true);stage.warmSendSamples=[];
  for(let i=0;i<2;i++){stage.warmSendSamples.push(await sendUi('BENCH_FINISH_FAST Retained-worker sample '+i));await expect.poll(async()=>(await inspect()).sessions.find(r=>r.id===fg)?.status,{timeout:15000}).toBe('idle');assert.equal((await inspect()).workers.find(r=>r.id===fg)?.pid,retained.pid)}
  stage.warmSend=await sendUi('Retained-worker workspace measurement');stage.warmSendSamples.push(stage.warmSend);stage.warmAck=q(stage.warmSendSamples.map(r=>r.ackMs));
  const running=await inspect();assert.equal(running.workers.find(r=>r.id===fg)?.pid,retained.pid);assert.equal(running.workers.filter(r=>r.returncode===null).length,Math.max(1,count));stage.sameRealWorker=true;
  await nav();const section=page.locator('[data-sidebar-section="workspaces"]'),heading=section.locator('.a-sidebar-section-heading button[aria-expanded]').first();if(await heading.getAttribute('aria-expanded')==='false')await heading.click();
  await page.getByRole('button',{name:'All workspaces',exact:true}).click();const filter=page.getByRole('searchbox',{name:'Filter workspaces',exact:true});await filter.fill(target.path);
  const open=page.getByRole('button',{name:'Open chats in '+target.path,exact:true}).last();await open.waitFor();await page.waitForFunction(()=>window.amplifier.getState()?.view?.workSurface==='workspaces'&&!window.amplifier.getShellState()?.snapshots?.chats?.sidebarNavigation?.workspace);
  const first=await measure('first workspace demand',()=>open.click(),'',0);stage.workspace.push(first.m);assert.ok(first.p.total>40&&first.p.pages>1);
  const chatFilter=page.getByRole('searchbox',{name:'Filter conversations',exact:true});
  const filtered=await measure('exact chat filter',()=>chatFilter.fill(first.p.items[0]),first.p.items[0],0);stage.workspace.push(filtered.m);assert.deepEqual(filtered.p.items,[first.p.items[0]]);
  const cleared=await measure('clear filter',()=>chatFilter.fill(''),'',0);stage.workspace.push(cleared.m);assert.deepEqual(cleared.p.items,first.p.items);
  const next=await measure('explicit next page',()=>page.getByRole('button',{name:'Show more conversations',exact:true}).click(),'',1);stage.workspace.push(next.m);assert.ok(next.p.items.every(id=>!first.p.items.includes(id)));
  const back=await measure('explicit previous page',()=>page.getByRole('button',{name:'Show previous conversations',exact:true}).click(),'',0);stage.workspace.push(back.m);assert.deepEqual(back.p.items,first.p.items);
  stage.loaded=await inspect();stage.healthLoaded=await health();
  await choose(fg);await page.getByRole('button',{name:'Stop response',exact:true}).click();for(const sessionId of bg)await action('conversation.stop',{sessionId});
  await expect.poll(async()=>(await inspect()).workers.length,{timeout:30000}).toBe(0);stage.afterStop=await inspect();
  console.log(JSON.stringify({stage:count,warm:stage.warmSend,workspace:stage.workspace}));
 }
 assert.deepEqual(result.errors,[]);result.passed=true;
}catch(e){result.failure={name:e.name,message:e.message};process.exitCode=1;await page?.screenshot({path:out+'/failure.png'}).catch(()=>{});}
finally{if(page){try{const r=await page.request.get(url+'/fixture/runtime');const state=await r.json();for(const w of state.workers){assert.ok(ready.ids.includes(w.id));await page.request.post(url+'/api/actions',{headers:{'X-Amplifier-State-Transport':'delta-v1'},data:{action:'conversation.stop',args:{sessionId:w.id}}})}await expect.poll(async()=>{const r=await page.request.get(url+'/fixture/runtime');return (await r.json()).workers.length},{timeout:30000}).toBe(0);result.workersStopped=true}catch(e){result.cleanupFailure=e.message;process.exitCode=1}}result.finishedAt=new Date().toISOString();await fs.writeFile(out+'/results.json',JSON.stringify(result,null,2),{mode:0o600});await browser?.close();}
console.log(JSON.stringify({passed:result.passed,failure:result.failure?.message?.slice(0,400)}));
