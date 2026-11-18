// Actual Unified host/actions/SSE + exact candidate client; synthetic runtime only.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {isAbsolute,join} from 'node:path';
import {mkdir,readFile,realpath,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import {startFixture} from './fixture-readiness.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';

const installed=process.env.AMPLIFIER_TEST_INSTALLED==='1';
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/retained-activity-host';
const source=process.env.AMPLIFIER_TEST_CLIENT_SOURCE||fileURLToPath(new URL('../../.build/web-client/',import.meta.url));
const fixturePath=fileURLToPath(new URL('../fixtures/retained_activity_ui_server.py',import.meta.url));
let logs='',stdout='',owned,vite,browser,page,primary;
const proof={mode:installed?'installed-production-assets':'source-vite',
 scope:installed?'installed candidate wheel host/bundled production entry; synthetic questions and runtime, not released/live acceptance':
  'candidate source host/browser; synthetic questions and runtime, not installed/released/live acceptance',
 limits:['Synthetic question transitions and runtime; no provider/native-runtime or live-app acceptance.',
  'Desktop Chromium numeric geometry and saved screenshots, not physical-device or pixel-review acceptance.',
  'Original v7 Vite evidence remains source-only; this mode does not relabel that historical proof.'],
 checkpoints:[],cleanup:{complete:false}};
const errors=[];
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
const entryAssets=bytes=>[...bytes.toString().matchAll(/(?:src|href)=["']([^"']+\.(?:js|css)(?:\?[^"']*)?)["']/g)].map(match=>match[1]);
const browserAssetReads=[];
try{
 await mkdir(out,{recursive:true});
 const env={...process.env};
 let identity;
 if(installed){
  for(const key of ['AMPLIFIER_TEST_PYTHON','AMPLIFIER_TEST_PACKAGE_ROOT','AMPLIFIER_TEST_ARTIFACTS'])
   assert.ok(env[key]&&isAbsolute(env[key]),key+' must select an absolute owned path');
  const identityPath=env.AMPLIFIER_TEST_INSTALLED_IDENTITY||env.AMPLIFIER_TEST_WHEEL_IDENTITY;
  assert.ok(identityPath&&isAbsolute(identityPath),'Installed mode requires an absolute wheel files identity');
  identity=JSON.parse(await readFile(identityPath,'utf8'));
  assert.match(identity.wheelSha256,/^[0-9a-f]{64}$/);
  delete env.PYTHONPATH;delete env.PYTHONHOME;
  env.PYTHONDONTWRITEBYTECODE='1';
 }
 owned=await startFixture({command:env.AMPLIFIER_TEST_PYTHON||'python',
  args:[...(installed?['-B','-I']:[]),'-u',fixturePath],env,
  tag:'retained-activity',name:'Retained activity',timeoutMs:60000});
 owned.fixture.stderr.on('data',value=>logs+=value);
 owned.fixture.stdout.on('data',value=>stdout+=value);
 const target=owned.base;
 let entryUrl=target;
 if(!installed){
  const {createServer}=await import('vite');
  vite=await createServer({configFile:false,root:source,cacheDir:'/tmp/retained-activity-vite-cache',server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,req=>req.setHeader('Origin',target))}},'/branding':target,'/fixture':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
  await vite.listen();entryUrl=vite.resolvedUrls.local[0];
 }
 proof.origin={fixture:target,entry:entryUrl,directHost:installed,vite:!installed};
 browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),...(process.env.CHROMIUM_SINGLE_PROCESS==='1'?{args:['--single-process','--no-zygote','--no-sandbox']}: {})});
 page=await browser.newPage({viewport:{width:1440,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.stack||error.message));
 const fixtureResponse=await page.request.get(target+'/api/fixture/activity');assert.equal(fixtureResponse.status(),200);
 const initial=await fixtureResponse.json(),{owner,peer}=initial;
 assert.equal(initial.mode,proof.mode);
 proof.fixtureBefore=initial;
 if(installed){
  assert.equal(initial.installedIdentity.verified,true);
  assert.equal(initial.installedIdentity.packageRoot,await realpath(env.AMPLIFIER_TEST_PACKAGE_ROOT));
  assert.equal(initial.installedIdentity.wheelSha256,identity.wheelSha256);
  assert.equal(initial.installedIdentity.fileCount,Object.keys(identity.files).length);
  assert.equal(initial.installedIdentity.isolated,true);assert.equal(initial.installedIdentity.noPythonPath,true);
  const served={};
  const fetchBytes=async(path,name,asset)=>{
   const response=await page.request.get(new URL(path,target).href);
   assert.equal(response.status(),200,path);assert.equal(response.url(),new URL(path,target).href,'No asset redirect');
   const bytes=await response.body(),sha256=digest(bytes);
   assert.equal(sha256,identity.files['amplifier_web/static/'+name],'Wheel identity binds '+name);
   if(asset){assert.equal(sha256,asset.sha256,name);assert.equal(bytes.length,asset.bytes,name)}
   served[path]={bytes:bytes.length,sha256};return bytes;
  };
  const component=JSON.parse((await fetchBytes('/client-component.json','client-component.json')).toString());
  assert.equal(component.kind,'unified-web-static-artifact');assert.equal(component.schema,1);
  assert.deepEqual(component.source,initial.installedIdentity.source);
  const build=JSON.parse((await fetchBytes('/build.json','build.json',component.assets['build.json'])).toString());
  assert.deepEqual(build,component.build);assert.deepEqual(build,initial.installedIdentity.build);
  const packagedIndex=await readFile(join(env.AMPLIFIER_TEST_PACKAGE_ROOT,'static/index.html'));
  assert.equal(digest(packagedIndex),identity.files['amplifier_web/static/index.html']);
  assert.equal(digest(packagedIndex),component.assets['index.html'].sha256);
  assert.equal(packagedIndex.length,component.assets['index.html'].bytes);
  const response=await page.request.get(target+'/');assert.equal(response.status(),200);assert.equal(response.url(),target+'/');
  const html=await response.body(),paths=entryAssets(html);
  // The normal host brands its HTML. Bind references, not a fictitious byte-equal index.
  assert.deepEqual(paths,entryAssets(packagedIndex));
  assert.ok(paths.some(path=>new URL(path,target).pathname.endsWith('.js')));
  assert.ok(paths.some(path=>new URL(path,target).pathname.endsWith('.css')));
  served['/']={bytes:html.length,sha256:digest(html),transform:'host instance branding',
   packagedSha256:digest(packagedIndex),references:paths};
  for(const path of new Set(paths)){
   const url=new URL(path,target);assert.equal(url.origin,target);
   assert.equal(url.username,'');assert.equal(url.password,'');assert.equal(url.hash,'');
   const name=url.pathname.slice(1);assert.ok(component.assets[name],name+' belongs to the manifest');
   await fetchBytes(path,name,component.assets[name]);
  }
  proof.pairedIdentity={wheelSha256:identity.wheelSha256,source:component.source,build,served,
   installedOrigin:initial.installedIdentity,browserAssets:[],assetReadErrors:[]};
  const expectedUrls=new Set(paths.map(path=>new URL(path,target).href));
  page.on('response',response=>{
   if(!expectedUrls.has(response.url()))return;
   const read=(async()=>{
    assert.equal(response.status(),200,response.url());
    const bytes=await response.body(),name=new URL(response.url()).pathname.slice(1),sha256=digest(bytes);
    assert.equal(sha256,identity.files['amplifier_web/static/'+name],name);
    assert.equal(sha256,component.assets[name].sha256,name);
    assert.equal(bytes.length,component.assets[name].bytes,name);
    proof.pairedIdentity.browserAssets.push({url:response.url(),bytes:bytes.length,sha256});
   })().catch(error=>proof.pairedIdentity.assetReadErrors.push(String(error.stack||error)));
   browserAssetReads.push(read);
  });
 }
 const shell=async()=>{await page.waitForFunction(()=>window.amplifier?.getShellState()?.snapshots?.chats);return page.evaluate(()=>window.amplifier.getShellState().snapshots.chats)};
 const mode=mode=>page.evaluate(async mode=>{const r=await fetch('/api/fixture/activity',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})});if(!r.ok)throw Error('Fixture transition '+r.status);return r.json()},mode);
 await page.goto(entryUrl);
 assert.equal(new URL(page.url()).origin,new URL(entryUrl).origin,'Browser uses the selected mode origin');
 await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 const opened=await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{workSurface:'chat',navPinned:true}}));
 assert.equal(opened.accepted,true);
 await page.waitForFunction(()=>window.amplifier?.getState()?.view?.workSurface==='chat');
 const pin=page.locator(`.a-pinned-chats [data-session-id="${owner}"]`);
 const recent=page.locator(`.a-recent-shortcuts [data-session-id="${owner}"]`);
 const status=locator=>locator.locator('.a-navigation-status');
 await expect(pin).toHaveCount(1);await expect(recent).toHaveCount(1);
 await expect(status(pin)).toHaveAttribute('aria-label','Answer requested');
 await expect(status(recent)).toHaveAttribute('aria-label','Answer requested');
 for(const resource of ['pinnedNavigation','recentNavigation']){
  await expect.poll(async()=>(await shell()).navigationResources.includes(resource)).toBe(true);
  await expect.poll(async()=>(await shell()).navigationResourcePending[resource]).toBeUndefined();
 }
 await expect.poll(async()=>(await shell()).navigationActivityById?.[owner]?.label).toBe('Answer requested');
 assert.equal((await shell()).selectedSessionId,owner);
 proof.checkpoints.push({transition:'initial',label:'Answer requested',selected:owner,pending:false});
 await pin.scrollIntoViewIfNeeded();
 const geometry=()=>pin.evaluate(node=>{const scroll=node.closest('.a-nav-content'),r=node.getBoundingClientRect();return {top:r.top,height:r.height,scrollTop:scroll.scrollTop,rows:scroll.querySelectorAll('[data-session-id]').length}});
 const readingRows=()=>page.locator('.a-pinned-chats [data-session-id],.a-recent-shortcuts [data-session-id]')
  .evaluateAll(nodes=>nodes.map(node=>node.dataset.sessionId));
 const before=await geometry();
 const beforeRows=await readingRows();
 assert.ok(before.height>0&&before.rows>1);
 await mode('hold');
 await page.evaluate(async id=>{const r=await window.amplifier.dispatch('session.select',{id});if(!r.accepted)throw Error('Selection rejected')},peer);
 await expect.poll(async()=>(await shell()).selectedSessionId).toBe(peer);
 await expect.poll(async()=>Object.keys((await shell()).navigationResourceRetained||{})).toContain('pinnedNavigation');
 const held=await shell();
 assert.equal(held.navigationResources.includes('pinnedNavigation'),false);
 assert.equal(held.navigationResources.includes('recentNavigation'),false);
 assert.equal(held.navigationResourcePending.pinnedNavigation.status,'pending');
 assert.equal(held.navigationResourcePending.recentNavigation.status,'pending');
 assert.ok(held.navigationResourceRetained.recentNavigation);
 assert.equal(held.navigationActivityById[owner].label,'Answer requested');
 await expect(status(pin)).toHaveAttribute('aria-label','Answer requested');
 await expect(status(recent)).toHaveAttribute('aria-label','Answer requested');
 const stableGeometry=(sample,prior)=>{
  assert.equal(sample.rows,prior.rows);assert.ok(Math.abs(sample.scrollTop-prior.scrollTop)<1,'Retain navigation scroll');
  assert.ok(Math.abs(sample.top-prior.top)<1,'Retain row top');assert.ok(Math.abs(sample.height-prior.height)<1,'Retain row height');
 };
 const after=await geometry();stableGeometry(after,before);
 assert.deepEqual(await readingRows(),beforeRows,'Retain exact Pinned/Recent row identities and order through selection');
 await pin.locator('.a-nav-chat-select').focus();
 await pin.getByRole('button',{name:'Details and actions for Question owner',exact:true}).click();
 const details=page.getByRole('dialog',{name:'Details for Question owner',exact:true});
 const detailStatus=details.locator('.a-navigation-detail-status strong');
 await expect(detailStatus).toHaveText('Answer requested');
 const detailsGeometry=await geometry(),detailsRows=await readingRows();
 for(const [transition,label] of [['answered','Working'],['pending','Answer requested'],['unknown','Check answer delivery'],['rejected','Check answer delivery'],['multiple','Answer requested']]){
  await mode(transition);
  await expect.poll(async()=>(await shell()).navigationActivityById?.[owner]?.label).toBe(label);
  await expect(status(pin)).toHaveAttribute('aria-label',label);
  await expect(status(recent)).toHaveAttribute('aria-label',label);
  await expect(detailStatus).toHaveText(label);
  const current=await shell(),sample=await geometry();
  assert.equal(current.selectedSessionId,peer);
  for(const resource of ['pinnedNavigation','recentNavigation']){
   assert.equal(current.navigationResources.includes(resource),false);
   assert.equal(current.navigationResourcePending[resource].status,'pending');
   assert.ok(current.navigationResourceRetained[resource]);
  }
  stableGeometry(sample,detailsGeometry);assert.deepEqual(await readingRows(),detailsRows);
  proof.checkpoints.push({transition,label,selected:current.selectedSessionId,pending:true,
   currentActivity:current.navigationActivityById[owner],openDetails:true,geometry:sample});
 }
 await page.screenshot({path:out+'/retained-current.png'});
 await mode('fresh');
 await expect.poll(async()=>(await shell()).navigationResourcePending.pinnedNavigation).toBeUndefined();
 await expect.poll(async()=>(await shell()).navigationResourcePending.recentNavigation).toBeUndefined();
 for(const resource of ['pinnedNavigation','recentNavigation']){
  await expect.poll(async()=>(await shell()).navigationResources.includes(resource)).toBe(true);
  assert.equal((await shell()).navigationResourceRetained[resource],undefined);
 }
 await expect.poll(async()=>(await shell()).navigationActivityById?.[owner]?.label).toBe('Working');
 await expect(status(pin)).toHaveAttribute('aria-label','Working');
 await expect(status(recent)).toHaveAttribute('aria-label','Working');await expect(detailStatus).toHaveText('Working');
 assert.equal((await shell()).selectedSessionId,peer);
 proof.checkpoints.push({transition:'fresh',label:'Working',selected:peer,pending:false,openDetails:true});
 const info=await page.evaluate(async()=>{const r=await fetch('/api/fixture/activity');return r.json()});
 assert.deepEqual(info.runtimeStarts,[]);assert.deepEqual(info.runtimeSends,[]);assert.equal(info.historyUnchanged,true);assert.deepEqual(errors,[]);
 await Promise.all(browserAssetReads);
 if(installed){
  assert.deepEqual(proof.pairedIdentity.assetReadErrors,[]);
  const seen=new Set(proof.pairedIdentity.browserAssets.map(row=>row.url));
  for(const path of proof.pairedIdentity.served['/'].references)
   assert.ok(seen.has(new URL(path,target).href),'Browser actually loaded packaged entry asset '+path);
 }
 proof.status='PASS';proof.owner=owner;proof.peer=peer;proof.geometry={before,after};proof.info=info;proof.errors=errors;
}catch(error){
 primary=error;proof.status='FAIL';proof.failure=String(error.stack||error);
 if(error.fixtureDiagnostics)proof.fixtureDiagnostics=error.fixtureDiagnostics;
 if(page)await page.screenshot({path:out+'/failure.png'}).catch(()=>{});
}
finally{
 proof.errors=errors;
 // A closed page or failed evidence write must never bypass fixture release.
 try{if(page)await writeFile(out+'/final.html',await page.content())}
 catch(error){proof.htmlEvidenceError=String(error.stack||error)}
 try{
  await finishFixture({label:'Retained activity',close:[['Browser',()=>browser?.close()],['Vite',()=>vite?.close()]],
   stop:()=>owned?.stop()});
  if(owned){
   const cleanup=stdout.split('\n').flatMap(line=>{try{return [JSON.parse(line)]}catch{return []}})
    .find(row=>row.retainedActivityCleanup)?.retainedActivityCleanup;
   assert.equal(cleanup?.complete,true,'Canonical fixture cleanup must complete');
   if(installed){
    assert.equal(cleanup.installedIdentity.verified,true);
    assert.equal(cleanup.installedIdentity.identitySha256,proof.fixtureBefore.installedIdentity.identitySha256);
    assert.equal(cleanup.installedIdentity.staticSha256,proof.fixtureBefore.installedIdentity.staticSha256);
   }
   proof.cleanup=cleanup;
  }
 }catch(error){proof.cleanup.error=String(error.stack||error);primary??=error}
 try{
  await Promise.all(browserAssetReads);
  assert.deepEqual(errors,[]);
  if(proof.pairedIdentity)assert.deepEqual(proof.pairedIdentity.assetReadErrors,[]);
 }catch(error){primary??=error}
 if(primary){proof.status='FAIL';proof.failure??=String(primary.stack||primary)}
 await writeFile(out+'/fixture.stdout.log',stdout);
 await writeFile(out+'/fixture.log',logs);
 await writeFile(out+'/result.json',JSON.stringify(proof,null,2)+'\n');
 console.log(JSON.stringify(proof,null,2));
}
if(primary)throw primary;
