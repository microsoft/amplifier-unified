import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
// Authored, unexecuted source-lane check. --retained-thumbnails uses a real host
// with ten canonical saved receipts; not a stub that ignores the view limit.
const legacyMode=process.argv.includes('--legacy-thumbnails');
const retainedMode=process.argv.includes('--retained-thumbnails')||legacyMode;
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+(retainedMode?'tests/test_retained_thumbnails.py':'tests/fixtures/generated_images_server.py'),...(retainedMode?['--browser-fixture',...(legacyMode?['--legacy-thumbnails']:[])]:[])],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const boot=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Image fixture timeout')),20000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Image fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}});
 });
 if(process.env.AMPLIFIER_EXPECTED_PACKAGE)assert.equal(boot.package,process.env.AMPLIFIER_EXPECTED_PACKAGE+'/server.py');
 if(process.env.AMPLIFIER_BROWSER_TEST_MODE==='installed')assert.equal(boot.mode,'installed');
 browser=await chromium.launch({headless:true,chromiumSandbox:true});
 const page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer '+(retainedMode?'fixture-retained-thumbnails':'fixture-generated-images')}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const control=async(op,id)=>{const response=await page.request.post(boot.url+'/api/fixture/images',{data:{op,id}});assert.equal(response.status(),200);return response.json()};
 if(retainedMode){
  await retainedJourney(page,boot);
  assert.deepEqual(errors,[]);
 }else{
 await page.goto(boot.url+'/login');await page.getByLabel('Username').fill('image-fixture');await page.getByLabel('Password').fill('fixture-password');await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.waitForSelector('#amp-one');
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this unsent');
 // A persisted older appearance is loaded after application styles.
 const legacyCss=await readFile(root+'tests/fixtures/legacy_theme.css','utf8');
 await page.evaluate(css=>window.amplifier.dispatch('theme.apply',{name:'Older image layout',css:css+' #amp-one .a-inline-gallery{width:100%;grid-template-columns:minmax(0,1fr) 60px} #amp-one .a-image-generation[data-running]{width:100%;flex:1;aspect-ratio:1}'}),legacyCss);
 await control('start','first');
 const gallery=page.locator('.a-inline-gallery');
 const frame=gallery.locator('.a-gallery-image-frame');
 const geometry=async()=>({box:await gallery.boundingBox(),frame:await frame.boundingBox(),scroll:await page.locator('.a-messages').evaluate(node=>node.scrollTop)});
 await expect(gallery).toBeVisible();
 const single=await frame.boundingBox();assert.ok(single.width<=320);
 await control('start','second');await control('start','third');
 await expect(gallery).toHaveAttribute('aria-label','Image gallery, 3 images');
 await gallery.scrollIntoViewIfNeeded();
 const rail=gallery.locator('.a-inline-gallery-choices');
 await expect(rail.locator('[data-running]')).toHaveCount(3);
 assert.equal((await frame.boundingBox()).width,single.width,'Adding slots does not resize the main image');
 const generating=page.locator('.a-image-generation[data-running]');
 await expect(generating).toHaveCount(1);
 await expect(generating.first()).toHaveText('Creating image…');
 assert.ok(await page.evaluate(()=>{
  const work=document.querySelector('.a-execution-turn'),image=document.querySelector('.a-image-generation');
  return work&&!!(work.compareDocumentPosition(image)&Node.DOCUMENT_POSITION_FOLLOWING);
 }),'Image progress follows the work that started it');
 const placeholder=await generating.first().boundingBox();
 assert.ok(Math.abs(placeholder.width-placeholder.height)<1,'Generation has an image-shaped placeholder');
 assert.ok(placeholder.width<=321,'Generation stays compact instead of flex-growing to the chat width');

 await page.screenshot({path:'/tmp/unified-generated-images-running.png'});
 assert.notEqual(await generating.first().locator('svg').evaluate(node=>getComputedStyle(node).animationName),'none');
 await page.emulateMedia({reducedMotion:'reduce'});
 assert.equal(await generating.first().locator('svg').evaluate(node=>getComputedStyle(node).animationName),'none');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveText('Keep this unsent');
 await control('later');
 // Complete a later slot first; selection and all three positions stay put.
 const runningBox=await frame.boundingBox();
 await control('finish','second');
 await expect(rail.locator('[data-running]')).toHaveCount(2);
 await expect(rail.locator('button').nth(1).locator('img')).toBeVisible();
 await expect(generating).toHaveCount(1);
 assert.equal((await frame.boundingBox()).height,runningBox.height);
 const first=(await control('finish','first')).result;
 await expect(generating).toHaveCount(0);
 await expect(rail.locator('[data-running]')).toHaveCount(1);
 assert.ok((await gallery.boundingBox()).width<=388,'Gallery stays compact with its side rail');
 await gallery.scrollIntoViewIfNeeded();await expect(gallery.locator('.a-inline-image img')).toBeVisible();
 await control('finish','third');await control('stop');
 await expect(rail.locator('[data-running]')).toHaveCount(0);
 await expect(rail.locator('img')).toHaveCount(3);
 await expect.poll(()=>rail.locator('img').evaluateAll(images=>images.every(image=>image.complete&&image.naturalWidth>0))).toBe(true);
 await expect(gallery).toHaveAttribute('aria-label','Image gallery, 3 images');
 assert.equal((await frame.boundingBox()).height,runningBox.height);

 assert.ok(await page.evaluate(()=>{
  const work=document.querySelector('.a-execution-turn'),image=document.querySelector('.a-inline-gallery'),later=document.querySelector('[data-message-id="later"]');
  return work&&later&&!!(work.compareDocumentPosition(image)&Node.DOCUMENT_POSITION_FOLLOWING)&&!!(image.compareDocumentPosition(later)&Node.DOCUMENT_POSITION_FOLLOWING);
 }),'Saved image follows its work and stays before the next request');

 const publication=await page.evaluate(id=>window.amplifier.getState().canvasArtifacts.find(row=>row.id===id),first.canvasId);
 assert.equal(publication.publications.length,1);
 assert.deepEqual({...publication.publications[0],imageGeneration:undefined},
  {messageId:'origin',version:1,chat:'inline',outputId:first.id,imageGeneration:undefined});
 assert.equal(publication.publications[0].imageGeneration.verified,true);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.open),false);
 // Hold the resource request to measure the loading frame and scroll position.
 let release,entered;
 const waiting=new Promise(resolve=>{entered=resolve}),gate=new Promise(resolve=>{release=resolve});
 await page.route('**/api/canvas/views/*/resource?*',async route=>{entered();await gate;await route.continue()});
 await gallery.scrollIntoViewIfNeeded();
 const before=await geometry();
 assert.ok(before.scroll>100,'Exercise image swapping from a scrolled reading position');
 await gallery.getByRole('button',{name:'Image 2: Second concept',exact:true}).click();
 await waiting;
 await expect(gallery.getByText('Loading preview…')).toBeVisible();
 const during=await geometry();
 assert.equal(during.box.height,before.box.height,'No collapse during delayed loading');
 assert.ok(Math.abs(during.scroll-before.scroll)<1,'No scroll jump during delayed loading');
 release();await page.unrouteAll({behavior:'wait'});
 await expect(gallery.locator('.a-inline-image img')).toBeVisible();
 const after=await geometry();
 assert.equal(after.box.height,before.box.height,'Landscape uses the same frame');
 assert.ok(Math.abs(after.scroll-before.scroll)<1,'No scroll jump when loading completes');
 await page.route('**/api/canvas/views/*/resource?*',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Preview temporarily unavailable'})}));
 await gallery.getByRole('button',{name:'Image 3: Third concept',exact:true}).click();
 await expect(gallery.getByRole('button',{name:'Try preview again',exact:true})).toBeVisible();
 assert.equal((await geometry()).box.height,before.box.height,'Error keeps the same frame');
 await page.unrouteAll({behavior:'wait'});
 await gallery.getByRole('button',{name:'Try preview again',exact:true}).click();
 await expect(gallery.locator('.a-inline-image img')).toBeVisible();
 assert.equal((await geometry()).box.height,before.box.height,'Portrait uses the same frame');
 await gallery.getByRole('button',{name:'Image 2: Second concept',exact:true}).click();
 await gallery.getByRole('button',{name:'Open Second concept in full view',exact:true}).first().click();
 await expect(page.locator('.a-canvas-workspace img').first()).toBeVisible();
 await page.evaluate(async()=>{await window.amplifier.dispatch('view.update',{patch:{canvasFocused:false}});await window.amplifier.dispatch('canvas.close')});
 await page.screenshot({path:'/tmp/unified-generated-images-complete.png'});
 await control('start','uncertain');await gallery.getByRole('button',{name:'Image 4: Creating image…',exact:true}).click();await expect(generating).toHaveCount(1);
 await control('unknown','uncertain');await expect(generating).toHaveCount(0);
 await expect(page.locator('.a-image-generation')).toHaveText('Image generation stopped; check activity for its outcome');
 await control('stop');await page.reload();
 await gallery.scrollIntoViewIfNeeded();
 await expect(rail.locator('img')).toHaveCount(3);
 await gallery.getByRole('button',{name:'Image 4: Image generation stopped; check activity for its outcome',exact:true}).click();
 await expect(page.locator('.a-image-generation')).toHaveText('Image generation stopped; check activity for its outcome');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveText('Keep this unsent');
 await page.setViewportSize({width:390,height:844});
 await expect(gallery).toHaveAttribute('aria-label','Image gallery, 4 images');
 await gallery.getByRole('button',{name:/^Image 1:/}).click();
 await gallery.scrollIntoViewIfNeeded();
 await expect(gallery.locator('.a-inline-image img')).toBeVisible();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.screenshot({path:'/tmp/unified-generated-images-mobile.png'});
 await control('delegated-start');
 await gallery.getByRole('button',{name:'Image 5: Creating image…',exact:true}).click();
 await expect(generating).toHaveCount(1);
 await expect(generating).toHaveText('Creating image…');
 await page.reload();await gallery.getByRole('button',{name:'Image 5: Creating image…',exact:true}).click();await expect(generating).toHaveCount(1);
 await control('delegated-finish');await expect(generating).toHaveCount(0);
 await expect(page.locator('.a-image-generation').filter({hasText:'Image generated'})).toHaveCount(1);
 await control('stop');await page.reload();await expect(generating).toHaveCount(0);
 assert.deepEqual(errors,[]);
 console.log('Generated images passed: stable pending/result gallery slots, out-of-order completion, delayed landscape/portrait swaps without scroll movement, older saved appearance, automatic native receipt display without a model attach action, reduced motion, exact original turn, shared gallery and full view, unsent draft retained, unknown outcome settled across reload, mobile containment. Synthetic receipts; no model/image API calls.');
 }
}finally{await browser?.close();fixture.kill('SIGTERM');await new Promise(resolve=>{if(fixture.exitCode!==null)resolve();else{fixture.once('exit',resolve);setTimeout(resolve,5000)}})}

async function retainedJourney(page,boot){
 const admissions=[],releases=[],reads=[];
 page.on('request',request=>{
  const url=new URL(request.url());
  if(/^\/api\/outputs\/[^/]+\/image$/.test(url.pathname))reads.push(url);
  if(url.pathname==='/api/actions'&&request.method()==='POST'){
   const command=request.postDataJSON();
   if(command.action==='canvas.views.inline')admissions.push(command.args);
   if(command.action==='canvas.views.release')releases.push(command.args);
  }
 });
 await page.addInitScript(()=>{
  const decode=window.createImageBitmap.bind(window);
  window.thumbnailDecodes={started:0,pending:0,peak:0,closed:0};
  window.thumbnailDecodeWaits=[];
  window.createImageBitmap=async(...args)=>{
   const stats=window.thumbnailDecodes;
   stats.started++;stats.pending++;stats.peak=Math.max(stats.peak,stats.pending);
   if(window.holdThumbnailDecode)await new Promise(resolve=>window.thumbnailDecodeWaits.push(resolve));
   const bitmap=await decode(...args),close=bitmap.close.bind(bitmap);
   bitmap.close=()=>{stats.closed++;stats.pending--;close()};
   return bitmap;
  };
 });
 await page.goto(boot.url+'/login');
 await page.getByLabel('Username').fill('thumbnail-fixture');
 await page.getByLabel('Password').fill('fixture-password');
 await page.getByRole('button',{name:'Sign in',exact:true}).click();
 await page.waitForSelector('#amp-one');
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Unsent thumbnail review');
 const gallery=page.locator('.a-inline-gallery'),rail=gallery.locator('.a-inline-gallery-choices');
 const ordinary=page.locator(`.a-general-artifact[data-artifact-id="${boot.document.id}"]`);
 const action=(name,args)=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const allThumbnails=async(count=10)=>{
  await expect(rail.locator('button')).toHaveCount(count);
  for(let index=0;index<count;index++){
   const choice=rail.locator('button').nth(index);await choice.scrollIntoViewIfNeeded();
   await expect.poll(()=>choice.locator('img').evaluateAll(images=>images.length===1&&images[0].complete&&images[0].naturalWidth>0)).toBe(true);
  }
  await expect(rail.locator('img')).toHaveCount(count);
  assert.ok(await rail.locator('img').evaluateAll(images=>images.every(image=>
   image.src.startsWith('data:image/png;base64,')&&Math.max(image.naturalWidth,image.naturalHeight)<=80)));
 };
 await gallery.scrollIntoViewIfNeeded();await allThumbnails();
 await expect(gallery.locator('.a-inline-image img')).toBeVisible();
 await ordinary.scrollIntoViewIfNeeded();await expect(ordinary.getByText('Independent ordinary renderer',{exact:true})).toBeVisible();
 assert.equal(admissions.length,2,'Ten thumbnails leave slots for the selected preview and the ordinary renderer');
 assert.ok(admissions.every(args=>[boot.records[0].canvasId,boot.document.id].includes(args.resourceId)));
 assert.equal(releases.length,0,'No thumbnail admission/release occurs during retained loading');
 assert.equal(reads.length,10,'Each current tuple receives one bounded read');
 for(const url of reads){
  const record=boot.records.find(record=>url.pathname===`/api/outputs/${record.id}/image`);
  assert.ok(record);assert.equal(url.searchParams.get('sessionId'),boot.sessionId);
  assert.equal(url.searchParams.get('artifactId'),record.canvasId);
  assert.equal(url.searchParams.get('messageId'),boot.messageId);
  assert.equal(url.searchParams.get('version'),'1');assert.equal(url.searchParams.get('sha256'),record.sha256);
  assert.ok(url.searchParams.get('clientId'),'Bytes use the actual attached client URL');
 }
 await expect.poll(()=>page.evaluate(()=>window.thumbnailDecodes.pending)).toBe(0);
 assert.ok((await page.evaluate(()=>window.thumbnailDecodes.peak))<=2);
 const targetFields=['viewId','resourceId','resourceRevision','generation'];
 const preview=await page.evaluate(id=>window.amplifier.getState().canvasWorkspace.views.find(view=>view.resourceId===id),boot.records[0].canvasId);
 await action('canvas.views.release',Object.fromEntries(targetFields.map(key=>[key,preview[key]])));
 await expect(gallery.locator('.a-inline-image img')).toHaveCount(0);
 await expect(rail.locator('img')).toHaveCount(10);
 assert.equal(reads.length,10,'Releasing an individual selected preview does not reauthorize thumbnails');
 await expect(ordinary.getByText('Independent ordinary renderer',{exact:true})).toBeVisible();
 // A persistent failure on an UNSELECTED thumbnail cannot consume selection.
 // Retry is a sibling keyboard control, never a capture listener/nested button.
 let fail=true;
 await page.route('**/api/outputs/*/image?*',route=>{
  if(fail&&new URL(route.request().url()).pathname===`/api/outputs/${boot.records[0].id}/image`){
   return route.fulfill({status:503,contentType:'application/json',body:'{"error":"Persistent thumbnail-only failure"}'});
  }
  return route.continue();
 });
 await page.reload();await gallery.scrollIntoViewIfNeeded();
 const retry=gallery.getByRole('button',{name:'Retry thumbnail for Image 1: Concept 1',exact:true});
 await expect(retry).toBeVisible();
 await gallery.getByRole('button',{name:'Image 2: Concept 2',exact:true}).click();
 await expect(gallery.locator('.a-inline-image img')).toBeVisible();
 const selection=await gallery.locator('.a-inline-artifact-heading strong').textContent();
 await retry.focus();await retry.press('Enter');await expect(retry).toBeVisible();
 await retry.focus();await retry.press('Space');await expect(retry).toBeVisible();
 assert.equal(await gallery.locator('.a-inline-artifact-heading strong').textContent(),selection);
 await gallery.getByRole('button',{name:'Image 1: Concept 1',exact:true}).click();
 await expect(gallery.locator('.a-inline-artifact-heading strong')).toHaveText('Concept 1');
 await expect(gallery.locator('.a-inline-image img')).toBeVisible();
 await expect(retry).toBeVisible();
 fail=false;await retry.click();await allThumbnails();
 assert.equal(await rail.locator('button button').count(),0);
 await page.unroute('**/api/outputs/*/image?*');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveText('Unsent thumbnail review');
 // Hold real saved bytes, remove only that saved publication, then release
 // the late response. It may not reinsert the removed thumbnail.
 const removed=boot.records[9];let finish,entered;
 const waiting=new Promise(resolve=>{entered=resolve}),gate=new Promise(resolve=>{finish=resolve});
 await page.route(`**/api/outputs/${removed.id}/image?*`,async route=>{
  entered();await gate;await route.continue().catch(()=>{});
 });
 await page.reload();await rail.locator('button').last().scrollIntoViewIfNeeded();await waiting;
 await action('outputs.unlink',{sessionId:boot.sessionId,id:removed.id,expectedRevision:removed.revision});
 await expect(rail.locator('button')).toHaveCount(9);finish();
 await page.unrouteAll({behavior:'wait'});
 await allThumbnails(9);
 assert.equal(await gallery.getByRole('button',{name:/Concept 10/}).count(),0);
 // Decode is also asynchronous. Suppressing its exact publication while a
 // real bitmap is pending clears old pixels before completion; closed bitmaps
 // do not revive that publication.
 await page.addInitScript(()=>{window.holdThumbnailDecode=true});
 await page.reload();await gallery.scrollIntoViewIfNeeded();
 await expect.poll(()=>page.evaluate(()=>window.thumbnailDecodeWaits.length)).toBeGreaterThan(0);
 await action('canvas.present',{sessionId:boot.sessionId,id:boot.records[0].canvasId,version:1,messageId:boot.messageId,
  presentation:{chat:'none',canvas:'unchanged'}});
 await expect(rail.locator('button')).toHaveCount(8);
 await page.evaluate(()=>{window.holdThumbnailDecode=false;window.thumbnailDecodeWaits.splice(0).forEach(resolve=>resolve())});
 await allThumbnails(8);
 assert.equal(await gallery.getByRole('button',{name:/Concept 1$/}).count(),0);
 await expect.poll(()=>page.evaluate(()=>window.thumbnailDecodes.pending)).toBe(0);
 // Scope changes unmount the rail. Re-entry reads the remaining saved tuples,
 // rather than reviving pixels from the previous client/session scope.
 const before=reads.length;
 await action('session.create',{title:'Other thumbnail scope'});
 await expect(gallery).toHaveCount(0);
 await action('session.select',{id:boot.sessionId});
 await gallery.scrollIntoViewIfNeeded();await allThumbnails(8);
 assert.ok(reads.length>=before+8);
 console.log('Retained thumbnail journey passed: real host, ten decoded <=80px thumbnails, selected and unrelated inline renderers with only two initial admissions, two decoder slots, persistent failed unselected thumbnail selection and independent keyboard retry, reload reauthorization, independent selected-view release, late fetch/unlink and late decode/publication suppression, scope cleanup. '+(boot.legacy?'True absent publication fields, empty jobs, cold host restart and exact canonical metadata IDs. ':'')+'Synthetic saved receipts; no image/model API calls.');
}
