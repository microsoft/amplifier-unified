import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(root+'.venv/bin/python',['-u',root+'tests/fixtures/generated_images_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const boot=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Image fixture timeout')),20000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Image fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}});
 });
 if(process.env.AMPLIFIER_EXPECTED_PACKAGE)assert.equal(boot.package,process.env.AMPLIFIER_EXPECTED_PACKAGE+'/server.py');
 browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
 const page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-generated-images'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const control=async(op,id)=>{const response=await page.request.post(boot.url+'/api/fixture/images',{data:{op,id}});assert.equal(response.status(),200);return response.json()};
 await page.goto(boot.url+'/login');await page.getByLabel('Username').fill('image-fixture');await page.getByLabel('Password').fill('fixture-password');await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.waitForSelector('#amp-one');
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this unsent');
 await control('start','first');await control('start','second');
 const generating=page.locator('.a-image-generation[data-running]');
 await expect(generating).toHaveCount(2);
 await expect(generating.first()).toHaveText('Creating image…');
 assert.ok(await page.evaluate(()=>{
  const work=document.querySelector('.a-execution-turn'),image=document.querySelector('.a-image-generation');
  return work&&!!(work.compareDocumentPosition(image)&Node.DOCUMENT_POSITION_FOLLOWING);
 }),'Image progress follows the work that started it');
 const placeholder=await generating.first().boundingBox();
 assert.ok(Math.abs(placeholder.width-placeholder.height)<1,'Generation has an image-shaped placeholder');

 await page.screenshot({path:'/tmp/unified-generated-images-running.png'});
 assert.notEqual(await generating.first().locator('svg').evaluate(node=>getComputedStyle(node).animationName),'none');
 await page.emulateMedia({reducedMotion:'reduce'});
 assert.equal(await generating.first().locator('svg').evaluate(node=>getComputedStyle(node).animationName),'none');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveText('Keep this unsent');
 await control('later');
 const first=(await control('finish','first')).result;
 await expect(generating).toHaveCount(1);
 const gallery=page.locator('.a-inline-gallery');
 await gallery.scrollIntoViewIfNeeded();await expect(gallery.locator('.a-inline-image img')).toBeVisible();
 await control('finish','second');await control('stop');
 await expect(page.locator('.a-image-generation')).toHaveCount(0);
 await expect(gallery).toHaveAttribute('aria-label','Image gallery, 2 images');
 assert.ok(await page.evaluate(()=>{
  const work=document.querySelector('.a-execution-turn'),image=document.querySelector('.a-inline-gallery'),later=document.querySelector('[data-message-id="later"]');
  return work&&later&&!!(work.compareDocumentPosition(image)&Node.DOCUMENT_POSITION_FOLLOWING)&&!!(image.compareDocumentPosition(later)&Node.DOCUMENT_POSITION_FOLLOWING);
 }),'Saved image follows its work and stays before the next request');

 const publication=await page.evaluate(id=>window.amplifier.getState().canvasArtifacts.find(row=>row.id===id),first.canvasId);
 assert.deepEqual(publication.publications,[{messageId:'origin',version:1}]);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.open),false);
 await gallery.getByRole('button',{name:'Image 2: Second concept',exact:true}).click();
 await gallery.getByRole('button',{name:'Open Second concept in full view',exact:true}).first().click();
 await expect(page.locator('.a-canvas-workspace img').first()).toBeVisible();
 await page.evaluate(async()=>{await window.amplifier.dispatch('view.update',{patch:{canvasFocused:false}});await window.amplifier.dispatch('canvas.close')});
 await control('start','uncertain');await expect(generating).toHaveCount(1);
 await control('unknown','uncertain');await expect(generating).toHaveCount(0);
 await expect(page.locator('.a-image-generation')).toHaveText('Image generation stopped; check activity for its outcome');
 await control('stop');await page.reload();
 await expect(page.locator('.a-image-generation')).toHaveText('Image generation stopped; check activity for its outcome');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveText('Keep this unsent');
 await page.setViewportSize({width:390,height:844});
 await expect(gallery).toHaveAttribute('aria-label','Image gallery, 2 images');
 await gallery.scrollIntoViewIfNeeded();
 await expect(gallery.locator('.a-inline-image img')).toBeVisible();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.screenshot({path:'/tmp/unified-generated-images-mobile.png'});
 await control('delegated-start');
 await expect(generating).toHaveCount(1);
 await expect(generating).toHaveText('Creating image…');
 await page.reload();await expect(generating).toHaveCount(1);
 await control('delegated-finish');await expect(generating).toHaveCount(0);
 await expect(page.locator('.a-image-generation').filter({hasText:'Image generated'})).toHaveCount(1);
 await control('stop');await page.reload();await expect(generating).toHaveCount(0);
 assert.deepEqual(errors,[]);
 console.log('Generated images passed: observed multiple placeholders, reduced motion, exact original turn, shared gallery and full view, unsent draft retained, unknown outcome settled across reload, mobile containment. Synthetic receipts; no model/image API calls.');
}finally{await browser?.close();fixture.kill('SIGTERM');await new Promise(resolve=>{if(fixture.exitCode!==null)resolve();else{fixture.once('exit',resolve);setTimeout(resolve,5000)}})}
