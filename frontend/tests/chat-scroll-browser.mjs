// Uses the real chat UI and isolated runtime. No model or existing user data.
// Vite serves source directly, so this regression does not rebuild static assets.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {once} from 'node:events';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../',import.meta.url));
const fixture=spawn(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u','-c',`
import asyncio, json, sys, tempfile
from pathlib import Path
from aiohttp import web
sys.path.insert(0, ${JSON.stringify(fileURLToPath(new URL('../../tests/fixtures',import.meta.url)))})
import chat_ui_server as fixture
class ScrollRuntime(fixture.ChatRuntime):
 async def send(self, session, text, input_id, emit):
  await emit('runtime.status', {'sessionId':session['id'], 'status':'working'})
  await asyncio.Event().wait()
fixture.fixture.Runtime=ScrollRuntime
async def main():
 with tempfile.TemporaryDirectory(prefix='amplifier-scroll-ui-') as home:
  app=await fixture.fixture.main(Path(home))
  service=app['service']
  session=service.state['sessions'][0]
  session['messages']=[{'id':f'history-{i}', 'role':'user' if i%2==0 else 'assistant', 'text':f'History {i}\\n\\n'+('A useful paragraph. '*35), 'createdAt':i+1} for i in range(30)]
  async def grow(request):
   await service.on_runtime_event('assistant.delta', {'sessionId':session['id'], 'text':'\\n\\nMore streaming content. '*25})
   return web.json_response({'ok':True})
  app.router.add_post('/api/fixture/grow', grow)
  runner=web.AppRunner(app);await runner.setup()
  site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
  port=site._server.sockets[0].getsockname()[1]
  app['allowed_origins']=app['allowed_origins']|{f'http://127.0.0.1:{port}'}
  print(json.dumps({'port':port}),flush=True)
  try:await asyncio.Event().wait()
  finally:await runner.cleanup()
asyncio.run(main())
`],{stdio:['ignore','pipe','pipe']});
let fixtureLog='';fixture.stderr.on('data',chunk=>{fixtureLog+=chunk});
const fixtureReady=new Promise((resolve,reject)=>{
 let text='';fixture.stdout.on('data',chunk=>{text+=chunk;const line=text.split('\n').find(row=>row.startsWith('{"port":'));if(line)resolve(JSON.parse(line).port)});
 fixture.once('exit',code=>reject(new Error(`Scroll fixture exited ${code}: ${fixtureLog}`)));
});
let vite,browser;
try{
 const port=await Promise.race([fixtureReady,new Promise((_,reject)=>setTimeout(()=>reject(new Error('Scroll fixture did not start')),15000).unref())]);
 const target=`http://127.0.0.1:${port}`;
 vite=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,configure(proxy){proxy.on('proxyReq',request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(vite.resolvedUrls.local[0]);await page.waitForSelector('#amp-one');
 const pane=page.locator('.a-messages'),input=page.getByRole('textbox',{name:'Message Amplifier'});
 const atBottom=()=>page.waitForFunction(()=>{const p=document.querySelector('.a-messages');return p.scrollHeight-p.scrollTop-p.clientHeight<3});
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const scrollBack=async()=>{const box=await pane.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.wheel(0,-1200);await page.waitForFunction(()=>{const p=document.querySelector('.a-messages');return p.scrollHeight-p.scrollTop-p.clientHeight>500});await page.waitForTimeout(150)};
 await atBottom();
 const sessionId=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await input.fill(Array.from({length:7},(_,i)=>`New multiline message ${i+1}`).join('\n'));
 assert.ok((await input.boundingBox()).height>=175);
 await scrollBack();
 // SSE delivers the visible message before the send request resolves. The old
 // implementation enabled follow-bottom only after that response, too late.
 await page.route('**/api/actions',async route=>{
  if(route.request().postDataJSON()?.action!=='conversation.send')return route.continue();
  const response=await route.fetch();await new Promise(resolve=>setTimeout(resolve,450));await route.fulfill({response});
 });
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('.a-user:last-of-type')?.textContent.includes('New multiline message')||[...document.querySelectorAll('.a-user')].at(-1)?.textContent.includes('New multiline message'));
 await page.waitForFunction(()=>document.querySelector('[aria-label="Message Amplifier"]').value==='');
 await atBottom();
 const message=await page.locator('.a-user').last().boundingBox(),composer=await page.locator('.a-composer').boundingBox();
 assert.ok(message.y+message.height<=composer.y,`submitted message ends at ${message.y+message.height}, composer starts at ${composer.y}`);
 assert.ok(message.y>=(await pane.boundingBox()).y,'submitted message is visible without scrolling');

 // Layout-only changes keep following: composer resize, late image decoding,
 // expanded details, viewport and canvas widths do not need a new chat event.
 await input.fill('Draft line\n'.repeat(12));await atBottom();await input.fill('');await atBottom();
 await pane.evaluate(element=>{const image=new Image();image.id='late-image';[...element.querySelectorAll('.a-user')].at(-1).append(image);image.style.cssText='display:block;width:180px';setTimeout(()=>{image.src='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="180" height="160"><rect width="180" height="160" fill="lavender"/></svg>')},100)});
 await page.waitForFunction(()=>document.querySelector('#late-image')?.naturalHeight===160);await atBottom();
 await pane.evaluate(element=>{const detail=document.createElement('details');detail.innerHTML='<summary>Fixture tool details</summary><div style="height:240px">Expanded tool result</div>';element.querySelectorAll('.a-user').item(element.querySelectorAll('.a-user').length-1).append(detail)});
 await page.getByText('Fixture tool details',{exact:true}).click();await atBottom();
 await action('canvas.show',{kind:'markdown',title:'Resize fixture',content:'# Canvas'});await atBottom();
 await page.setViewportSize({width:390,height:844});await atBottom();await action('canvas.close');await atBottom();

 // A reader in scrollback stays there as streaming arrives. Returning to the
 // bottom resumes follow, and selecting the session through shared actions
 // also reveals its latest content (including agent-origin selection).
 const grow=()=>page.evaluate(()=>fetch('/api/fixture/grow',{method:'POST'}));
 await grow();await atBottom();await scrollBack();
 const savedTop=await pane.evaluate(element=>element.scrollTop);
 await grow();await page.waitForTimeout(200);
 assert.ok(Math.abs(await pane.evaluate(element=>element.scrollTop)-savedTop)<3,'streaming must preserve scrollback');
 await input.fill('Draft line\n'.repeat(10));await page.waitForTimeout(100);
 assert.ok(await pane.evaluate(element=>element.scrollHeight-element.scrollTop-element.clientHeight)>500,'composer growth must preserve scrollback');
 await input.fill('');
 await pane.evaluate(element=>{element.scrollTop=element.scrollHeight});await page.waitForTimeout(100);await grow();await atBottom();
 await scrollBack();await action('session.create',{title:'Another chat'});await action('session.select',{id:sessionId});await atBottom();
 assert.deepEqual(errors,[]);
 console.log('Chat scroll browser checks passed: delayed send receipt, composer/image/details resize, canvas/mobile layout, streaming scrollback and session selection.');
}finally{
 await browser?.close();await vite?.close();if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}
}
