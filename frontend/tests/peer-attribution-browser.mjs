// ROOT-only execution: real receiving UI, NoModel fixture, no paid provider.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
// ROOT may set this only with the installed candidate interpreter. The fixture
// refuses a checkout import and this path never serves Vite source as a wheel.
const installedStatic=process.env.AMPLIFIER_TEST_INSTALLED_STATIC==='1';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[
 fileURLToPath(new URL('../../tests/fixtures/peer_attribution_ui_server.py',import.meta.url)),fileURLToPath(new URL('../../',import.meta.url))
],{stdio:['ignore','pipe','inherit']});
let browser,vite;
try{
 const target=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Peer fixture timed out')),15000);let output='';
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Peer fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}}});
 });
 if(!installedStatic){
  const {createServer}=await import('vite');
  vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),
   server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,configure(proxy){proxy.on('proxyReq',request=>request.setHeader('Origin',target))}},'/branding':target}},
   optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
  await vite.listen();
 }
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 const context=await browser.newContext({viewport:{width:390,height:844},extraHTTPHeaders:{Authorization:'Bearer fixture-peer-attribution'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(installedStatic?target:vite.resolvedUrls.local[0]);await page.waitForSelector('#amp-one');
 const facts=await (await page.request.get(new URL('/api/fixture/peerFacts',page.url()).href)).json();
 assert.equal(facts.installedStatic,installedStatic);
 const article=id=>page.locator(`article[data-message-id="${id}"]`);
 const caption='Sent by Amplifier from another chat';
 await expect(article(facts.agent)).toHaveAttribute('aria-label',caption);
 await expect(article(facts.agent).locator('.a-message-attribution')).toHaveText(caption);
 await expect(article(facts.human)).toHaveAttribute('aria-label','From another chat');
 await expect(article(facts.quote)).toHaveAttribute('aria-label','Your message');
 await expect(article(facts.quote).locator('.a-message-attribution')).toHaveCount(0);
 await expect(article(facts.forged)).toHaveAttribute('aria-label','Message');
 await expect(article(facts.forged).locator('.a-message-attribution')).toHaveCount(0);
 await expect(article(facts.forged).getByText('via peer',{exact:true})).toHaveCount(1);
 const preserved=await page.evaluate(()=>{
  const state=window.amplifier.getState();
  return {selectedSessionId:state.selectedSessionId,draft:state.view.draft};
 });
 assert.deepEqual(preserved,{selectedSessionId:'receiving',draft:'Untouched draft'});
 const presentation=scheme=>page.evaluate(async scheme=>{
  const shell=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;
  const prepared=await window.amplifier.dispatch('shell.changes.prepare',{
   clientId,expectedRevision:shell.revision,
   composition:{...shell.effectiveComposition,presentation:{...shell.effectiveComposition.presentation,scheme}}
  });
  if(!prepared.accepted||!prepared.result?.id)throw Error('Theme preparation failed');
  const applied=await window.amplifier.dispatch('shell.changes.apply',{
   clientId,expectedRevision:shell.revision,changeId:prepared.result.id
  });
  if(!applied.accepted)throw Error('Theme commit failed');
 },scheme);
 for(const width of [320,390])for(const scheme of ['light','dark'])for(const zoom of [1,2]){
  await page.setViewportSize({width,height:844});
  await presentation(scheme);
  await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);
  assert.equal(await page.evaluate(()=>window.amplifier.getShellState().effectiveComposition.presentation.scheme),scheme);
  await page.evaluate(zoom=>{document.documentElement.style.zoom=String(zoom)},zoom);
  await article(facts.agent).scrollIntoViewIfNeeded();await page.mouse.move(0,0);
  const node=article(facts.agent).locator('.a-message-attribution');await expect(node).toBeVisible();
  const geometry=await node.evaluate(element=>{
   const style=getComputedStyle(element),article=element.closest('article'),box=element.getBoundingClientRect();
   const rgb=value=>value.match(/[\d.]+/g).slice(0,3).map(Number);
   const luminance=values=>values.map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
   let ancestor=element,bg='rgba(0, 0, 0, 0)';
   while(ancestor){bg=getComputedStyle(ancestor).backgroundColor;if(bg!=='rgba(0, 0, 0, 0)'&&bg!=='transparent')break;ancestor=ancestor.parentElement}
   const fgL=luminance(rgb(style.color)),bgL=luminance(rgb(bg));
   return {overflow:element.scrollWidth>element.clientWidth,visible:style.visibility,opacity:style.opacity,
    contrast:(Math.max(fgL,bgL)+.05)/(Math.min(fgL,bgL)+.05),width:box.width,
    actionsContain:!!element.closest('.a-message-actions'),tabIndex:element.tabIndex,
    articleOverflow:article.scrollWidth>article.clientWidth};
  });
  assert.equal(geometry.overflow,false);assert.equal(geometry.articleOverflow,false);
  assert.equal(geometry.actionsContain,false);assert.equal(geometry.visible,'visible');assert.equal(geometry.opacity,'1');
  assert.ok(geometry.contrast>=4.5,JSON.stringify({width,scheme,zoom,...geometry}));assert.equal(geometry.tabIndex,-1);
  assert.deepEqual(await page.evaluate(()=>{const state=window.amplifier.getState();return {
   selectedSessionId:state.selectedSessionId,draft:state.view.draft
  }}),preserved);
 }
 await page.evaluate(()=>{document.documentElement.style.zoom='1'});
 await page.reload();await expect(article(facts.agent).locator('.a-message-attribution')).toHaveText(caption);
 const state=await page.evaluate(()=>window.amplifier.getState());
 const receiving=state.sessions.find(row=>row.id===state.selectedSessionId);
 const older=await (await page.request.get(new URL(`/api/conversation/detail?sessionId=receiving&part=messages&before=${encodeURIComponent(receiving.messageWindow.before)}`,page.url()).href)).json();
 assert.equal(older.items.find(row=>row.id===facts.older).attribution.caption,caption);
 assert.ok(!JSON.stringify(older.items.find(row=>row.id===facts.older).attribution).includes('private-source'));
 // The focused rail is another receiving boundary, not a compact-text lookup.
 const focused=await (await page.request.get(new URL(`/api/conversation/navigation?sessionId=receiving&messageId=${encodeURIComponent(facts.agent)}&window=true`,page.url()).href)).json();
 assert.equal(focused.messages.find(row=>row.id===facts.agent).attribution?.caption,caption);
 assert.deepEqual(await page.evaluate(()=>{const state=window.amplifier.getState();return {
  selectedSessionId:state.selectedSessionId,draft:state.view.draft
 }}),preserved);
 const finalFacts=await (await page.request.get(new URL('/api/fixture/peerFacts',page.url()).href)).json();
 assert.equal(finalFacts.starts,0);assert.equal(finalFacts.sends,0);assert.deepEqual(finalFacts.probeAttempts,[]);assert.deepEqual(errors,[]);
 console.log(`${installedStatic?'Installed-static':'Vite-source'} peer attribution receiving fixture: visible caption, actor distinction, forgeries, reload, older/focused pages, committed 320/390px light/dark 200% zoom, draft/selection preserved; zero model/worker starts.`);
}finally{await browser?.close();await vite?.close();fixture.kill()}