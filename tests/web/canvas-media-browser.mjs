import {showFixtureConversation} from './browser-chat-pages.mjs';
import {prepareCanvasReaderInput} from './canvas-reader-input.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
// Production HTML sandbox and genuine decoder/playback, without a model call.
// Tiny VP8 fixture: ffmpeg -f lavfi -i testsrc=size=64x48:rate=10 -t 1
// -c:v libvpx -pix_fmt yuv420p -an tiny.webm. Audio is one second of silent PCM.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const outputDir=process.env.AMPLIFIER_TEST_ARTIFACTS||root+'output/canvas-media-proof';
await mkdir(outputDir,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'Canvas media fixture'});
let browser,page,failure;
try{
 const url=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),15000);let output='';
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data.url)}}catch{}}});
 });
 const media=JSON.parse(await readFile(new URL('./fixtures/embedded-media.json',import.meta.url),'utf8'));
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 let remoteRequests=0;
 await context.route('https://media.invalid/**',route=>{remoteRequests++;return route.abort()});
 page=await context.newPage();
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 await action('session.create',{});
 await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true,canvasWidth:640}});
 const content=`<style>body{font:16px system-ui;padding:24px;color:#21334a}section{display:flex;gap:16px;flex-wrap:wrap}video{width:240px}audio{width:240px}button{padding:12px}</style>
 <h1>Embedded media review</h1><section>
 <div><h2>Embedded video</h2><video id="data-video" controls muted playsinline src="data:video/webm;base64,${media.video}"></video></div>
 <div><h2>Blob video</h2><video id="blob-video" controls muted playsinline></video></div>
 <div><h2>Embedded audio</h2><audio id="data-audio" controls muted src="data:audio/wav;base64,${media.audio}"></audio></div>
 <div><h2>Blob audio</h2><audio id="blob-audio" controls muted></audio></div></section>
 <button id="play">Play all</button><button id="pause">Pause all</button><p id="status">Ready to review</p>
 <script>
 const media=[...document.querySelectorAll('video,audio')];
 function blob(id,body,type){const bytes=Uint8Array.from(atob(body),c=>c.charCodeAt(0));document.getElementById(id).src=URL.createObjectURL(new Blob([bytes],{type}));}
 blob('blob-video',${JSON.stringify(media.video)},'video/webm');blob('blob-audio',${JSON.stringify(media.audio)},'audio/wav');
 document.getElementById('play').onclick=()=>Promise.all(media.map(m=>m.play())).then(()=>document.getElementById('status').textContent='Playing all four').catch(e=>document.getElementById('status').textContent=e.message);
 document.getElementById('pause').onclick=()=>media.forEach(m=>m.pause());
 </script>`;
 const readyReport=(previousClient)=>page.waitForResponse(response=>{
  if(!response.url().endsWith('/api/actions')||response.request().method()!=='POST')return false;
  const body=response.request().postDataJSON();
  return body?.action==='canvas.views.command'&&body.args?.action==='canvas.report'&&
   body.args.args?.part==='preview'&&body.args.args.status==='ready'&&
   response.request().headers()['x-amplifier-client']!==previousClient;
 });
 const documentResponse=()=>{
  const pending=page.waitForResponse(response=>
   new URL(response.url()).origin===new URL(url).origin&&
   /^\/api\/canvas\/[^/]+\/document$/.test(new URL(response.url()).pathname)&&response.request().resourceType()==='document');
  pending.catch(()=>{});return pending; // Awaited below; an earlier failure retains its primary error.
 };
 let ready=readyReport(),documentReady=documentResponse();
 const isolation=[];
 await action('canvas.show',{kind:'html',title:'Embedded media review',content});
 async function checkPlayback(ready){
  // Decoder readiness can precede the host's render acknowledgement. With
  // controls pinned, moving the status into the toolbar shifts the iframe;
  // a reload also inherits the old ready label. Require this page's real
  // accepted ready report and its public state before clicking inside it.
  const response=await ready,receipt=await response.json();
  assert.equal(response.ok(),true);assert.equal(receipt.accepted,true);
  await page.waitForFunction(({revision,clientId})=>{
   const state=window.amplifier?.getState();
   return state?.client?.id===clientId&&state.revision>=revision&&state.canvas?.renderReports?.preview?.status==='ready';
  },{revision:receipt.revision,clientId:response.request().headers()['x-amplifier-client']});
  const details=page.getByRole('region',{name:'Canvas details',exact:true});
  if(!await details.isVisible())await page.getByRole('button',{name:'Canvas details',exact:true}).click();
  // Isolation is an enforced frame/response boundary, not a retired caption.
  const iframe=page.locator('.a-canvas-workspace iframe.a-canvas-html');
  await expect(iframe).toHaveCount(1);
  await expect(iframe).toHaveAttribute('sandbox','allow-scripts');
  await expect(iframe).toHaveAttribute('referrerpolicy','no-referrer');
  const document=await documentReady,headers=document.headers();
  assert.equal(document.status(),200);
  assert.match(headers['content-type'],/^text\/html\b/);
  const directives=Object.fromEntries(headers['content-security-policy'].split(';').map(value=>value.trim().split(/\s+/)).filter(row=>row[0]).map(([name,...values])=>[name,values]));
  for(const [name,values] of Object.entries({sandbox:['allow-scripts'],'default-src':["'none'"],
   'media-src':['data:','blob:'],'connect-src':["'none'"],'frame-src':["'none'"],
   'object-src':["'none'"],'form-action':["'none'"],'base-uri':["'none'"],'frame-ancestors':["'self'"]}))
   assert.deepEqual(directives[name],values,'HTML isolation policy: '+name);
  const canvasId=await page.evaluate(()=>window.amplifier.getState().canvas.id);
  assert.equal(new URL(document.url()).pathname,`/api/canvas/${canvasId}/document`);
  assert.equal(new URL(await iframe.getAttribute('src'),url).href,document.url());
  isolation.push({canvasId,url:document.url(),sandbox:await iframe.getAttribute('sandbox'),csp:headers['content-security-policy']});
  const frame=page.frames().find(frame=>frame.url().includes('/document'));
  assert.ok(frame,'HTML document must be mounted');
  await expect.poll(()=>frame.evaluate(()=>[...document.querySelectorAll('video,audio')].filter(m=>m.readyState>=2&&!m.error&&m.duration>0).length)).toBe(4);
  await prepareCanvasReaderInput(page,{target:frame.getByRole('button',{name:'Play all',exact:true}),
   label:previousPlayback++?'media-reloaded':'media-initial',outputDir,expectCovered:true});
  await frame.getByRole('button',{name:'Play all',exact:true}).click();
  await expect.poll(()=>frame.evaluate(()=>[...document.querySelectorAll('video,audio')].every(m=>m.currentTime>.05))).toBe(true);
  await frame.getByRole('button',{name:'Pause all',exact:true}).click();
  assert.equal(await frame.evaluate(()=>[...document.querySelectorAll('video,audio')].every(m=>m.paused)),true);
  await frame.evaluate(()=>[...document.querySelectorAll('video,audio')].forEach(m=>{m.currentTime=.5}));
  await expect.poll(()=>frame.evaluate(()=>[...document.querySelectorAll('video,audio')].every(m=>!m.seeking&&Math.abs(m.currentTime-.5)<.1))).toBe(true);
  assert.equal(await frame.evaluate(()=>{try{void parent.document.body;return false}catch{return true}}),true,'Sandbox remains opaque to parent application');
  return frame;
 }
 let previousPlayback=0;
 await expect.poll(()=>page.frames().some(frame=>frame.url().includes('/document'))).toBe(true);
 const frame=await checkPlayback(ready);
 await frame.evaluate(()=>{
  window.blocked=[];document.addEventListener('securitypolicyviolation',e=>window.blocked.push(e.effectiveDirective));
  const external=document.createElement('video');external.src='https://media.invalid/blocked.webm';external.preload='auto';external.style.display='none';document.body.append(external);external.load();
  fetch('https://media.invalid/blocked.json').catch(()=>{});
 });
 await expect.poll(()=>frame.evaluate(()=>window.blocked.includes('media-src')&&window.blocked.includes('connect-src'))).toBe(true);
 assert.equal(remoteRequests,0,'No remote media or fetch request may leave the sandbox');
 const previousClient=await page.evaluate(()=>window.amplifier.getState().client.id);
 ready=readyReport(previousClient);documentReady=documentResponse();
 await page.reload();await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await expect.poll(()=>page.frames().some(frame=>frame.url().includes('/document'))).toBe(true);
 await checkPlayback(ready);
 await page.screenshot({path:outputDir+'/embedded-media.png'});
 const inspect=await page.request.get(url+'/fixture');
 assert.deepEqual((await inspect.json()).sent,[]);
 await writeFile(outputDir+'/isolation.json',JSON.stringify({isolation,remoteRequests,parentAccessDenied:true,playbackCheckedTwice:true},null,2)+'\n');
 console.log('Canvas media passed: data/blob video and audio decode, play, pause, seek and reopen; remote media/network and parent access remain blocked; no model calls.');
}catch(error){
 failure=error;
 if(page){
  const frames=await Promise.all(page.frames().filter(frame=>frame.url().includes('/document')).map(frame=>frame.evaluate(()=>({
   status:document.querySelector('#status')?.textContent,
   media:[...document.querySelectorAll('video,audio')].map(media=>({id:media.id,readyState:media.readyState,currentTime:media.currentTime,paused:media.paused,seeking:media.seeking,error:media.error?.code??null}))
  })).catch(()=>({unavailable:true}))));
  await writeFile(outputDir+'/failure.json',JSON.stringify({frames},null,2)).catch(()=>{});
  await page.screenshot({path:outputDir+'/failure.png'}).catch(()=>{});
 }
 throw error;
}finally{
 await finishFixture({label:'Canvas media fixture',close:[['Canvas media browser',()=>browser?.close()]],
  stop:stopFixture,originalError:failure});
}
