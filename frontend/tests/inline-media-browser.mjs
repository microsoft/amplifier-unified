// Supplied GIF/flower/large fixtures are synthetic/public, not the reporter original.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(root+'.venv/bin/python',['-u',root+'tests/fixtures/inline_media_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const boot=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Media fixture timeout')),20000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{
   const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}
  }catch{}});
 });
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:950}});
 await page.goto(boot.url+'/login');
 await page.getByLabel('Username').fill('media-fixture');await page.getByLabel('Password').fill('fixture-password');
 await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.waitForSelector('#amp-one');
 const article=page.locator(`[data-message-id="${boot.messageId}"]`);
 await expect(article).toBeVisible();
 await expect(article.locator('[data-media-id]')).toHaveCount(0);
 const response=await page.request.post(boot.url+'/api/fixture/media',{data:{name:'gif'}});
 assert.equal(response.status(),200);const saved=(await response.json()).result;
 const figure=article.locator(`[data-media-id="${saved.id}"]`);
 await expect(figure).toHaveAttribute('data-media-state','ready');
 const pixels=()=>figure.locator('canvas').evaluate(node=>Array.from(node.getContext('2d').getImageData(0,0,node.width,node.height).data));
 const initial=await pixels();
 await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
 await expect.poll(pixels).not.toEqual(initial);
 await figure.getByRole('button',{name:'Pause GIF',exact:true}).click();
 const paused=await pixels();
 // This bounded wait tests stability, not application synchronization.
 await new Promise(resolve=>setTimeout(resolve,300));
 assert.deepEqual(await pixels(),paused);
 await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
 await expect.poll(pixels).not.toEqual(paused);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.open),false);
 const link=await figure.getByRole('link',{name:'Download original'}).getAttribute('href');
 const download=await page.request.get(boot.url+link);
 assert.equal(download.status(),200);
 const digest=await page.evaluate(async bytes=>{
  const hashed=await crypto.subtle.digest('SHA-256',new Uint8Array(bytes));
  return Array.from(new Uint8Array(hashed),value=>value.toString(16).padStart(2,'0')).join('');
 },Array.from(await download.body()));
 assert.equal(digest,saved.sha256);
 await page.reload();await expect(article.locator(`[data-media-id="${saved.id}"]`)).toHaveAttribute('data-media-state','ready');
 if(boot.samples.includes('flower')){
  assert.equal((await page.request.post(boot.url+'/api/fixture/media',{data:{name:'flower'}})).status(),200);
  const video=article.locator('video');await expect(video).toBeVisible();
  assert.equal(await video.evaluate(node=>node.autoplay),false);
  await video.evaluate(node=>node.play());await expect.poll(()=>video.evaluate(node=>node.currentTime)).toBeGreaterThan(0);
  await video.evaluate(node=>node.pause());const at=await video.evaluate(node=>node.currentTime);
  await new Promise(resolve=>setTimeout(resolve,200));assert.equal(await video.evaluate(node=>node.currentTime),at);
  await video.evaluate(node=>{node.currentTime=Math.min(.5,node.duration/2)});
  await expect.poll(()=>video.evaluate(node=>node.currentTime)).toBeGreaterThan(0);
 }
 if(boot.samples.includes('large')){
  assert.equal((await page.request.post(boot.url+'/api/fixture/media',{data:{name:'large'}})).status(),200);
  await expect(article.locator('[data-media-id]').last()).toHaveAttribute('data-media-state','ready');
 }
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
}finally{
 await browser?.close();
 fixture.kill('SIGTERM');
 if(fixture.exitCode===null)await new Promise(resolve=>{
  const timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve()},5000);
  fixture.once('exit',()=>{clearTimeout(timer);resolve()});
 });
}