// Uses the real chat UI and isolated runtime. No model or existing user data.
// Vite serves source directly, so this regression does not rebuild static assets.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {once} from 'node:events';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u','-c',`
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
let vite,browser,page;
try{
 const port=await Promise.race([fixtureReady,new Promise((_,reject)=>setTimeout(()=>reject(new Error('Scroll fixture did not start')),15000).unref())]);
 const target=`http://127.0.0.1:${port}`;
 vite=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,configure(proxy){proxy.on('proxyReq',request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});const errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(vite.resolvedUrls.local[0]);await page.waitForSelector('#amp-one');
 const pane=page.locator('.a-messages'),input=page.getByRole('textbox',{name:'Message Amplifier'});
 const atBottom=()=>page.waitForFunction(()=>{const p=document.querySelector('.a-messages');return p.scrollHeight-p.scrollTop-p.clientHeight<3});
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const scrollBack=async()=>{const box=await pane.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.wheel(0,-1200);await page.waitForFunction(()=>{const p=document.querySelector('.a-messages');return p.scrollHeight-p.scrollTop-p.clientHeight>500});await page.waitForTimeout(150)};
 await atBottom();
 // The rail previews one turn, keeps bookmarks across reload, and jumps without
 // enabling scroll-follow. Metadata is quiet until hover/focus in Balanced.
 const marks=page.locator('.a-rail-mark');assert.equal(await marks.count(),15);
 await expect(page.getByRole('navigation',{name:'Chat navigator'})).not.toHaveAttribute('title');
 await expect(marks.last()).toHaveAttribute('data-visible','true');
 // Preview geometry follows the actual mark, including the short marks list
 // inside a taller rail; hovering never changes the reading-position highlight.
 for(const index of [0,7,14]){
  await marks.nth(index).hover();
  const preview=page.locator('.a-rail-preview');await preview.waitFor();
  await expect.poll(async()=>{
   const anchor=await marks.nth(index).boundingBox(),box=await preview.boundingBox(),container=await page.locator('.a-conversation').boundingBox();
   const top=Math.max(8,container.y+8),bottom=Math.min(900-8,container.y+container.height-8)-box.height;
   return Math.abs(box.y-Math.max(top,Math.min(bottom,anchor.y+anchor.height/2-box.height/2)));
  }).toBeLessThan(2);
  await expect(marks.last()).toHaveAttribute('data-visible','true');
 }
 await page.setViewportSize({width:1280,height:600});
 await marks.last().hover();
 await expect.poll(async()=>{const box=await page.locator('.a-rail-preview').boundingBox();return box.y>=8&&box.y+box.height<=592}).toBe(true);
 await page.setViewportSize({width:1280,height:900});
 await marks.nth(3).hover();await page.locator('.a-rail-preview').waitFor();
 assert.match(await page.locator('.a-rail-preview').innerText(),/History 6[\s\S]*History 7/);
 assert.equal(await page.locator('.a-rail-preview-jump').evaluate(node=>{const style=getComputedStyle(node);return style.whiteSpace==='nowrap'&&style.textOverflow==='ellipsis'&&node.scrollWidth>node.clientWidth}),true);
 await page.getByRole('button',{name:'Bookmark message',exact:true}).click();
 assert.equal(await marks.nth(3).getAttribute('data-bookmarked'),'true');
 await marks.nth(3).focus();await marks.nth(3).press('ArrowDown');
 assert.equal(await marks.nth(4).evaluate(n=>n===document.activeElement),true);
 await marks.nth(4).press('ArrowUp');
 await marks.nth(3).click();
 const targetMessage=pane.locator('[data-message-id="history-6"]');
 await expect(marks.nth(3)).toHaveAttribute('data-visible','true');
 await expect(marks.last()).toHaveAttribute('data-visible','false');
 // A partially visible response and the following user turn both light up.
 await pane.evaluate(element=>{const node=element.querySelector('[data-message-id="history-7"]');element.scrollTop+=node.getBoundingClientRect().top-element.getBoundingClientRect().top});
 await expect(marks.nth(3)).toHaveAttribute('data-visible','true');
 await expect(marks.nth(4)).toHaveAttribute('data-visible','true');
 await marks.nth(3).click();
 assert.ok(Math.abs((await targetMessage.boundingBox()).y-(await pane.boundingBox()).y-16)<3);
 await input.focus();await page.mouse.move(0,0);await page.waitForTimeout(200);
 assert.equal(await targetMessage.locator('.a-message-actions').evaluate(n=>getComputedStyle(n).opacity),'0');
 await targetMessage.hover();await page.waitForTimeout(200);
 assert.equal(await targetMessage.locator('.a-message-actions').evaluate(n=>getComputedStyle(n).opacity),'1');
 assert.equal(await targetMessage.locator('time').getAttribute('title'),null);
 assert.ok(await targetMessage.locator('time').getAttribute('aria-label'));
 assert.equal(await targetMessage.locator('.a-message-actions > :last-child').evaluate(node=>node.tagName),'TIME');
 await targetMessage.locator('time').hover();await expect(page.getByRole('tooltip')).toHaveCount(0);
 const copyButton=targetMessage.getByRole('button',{name:'Copy message as Markdown'});
 await copyButton.hover();await expect(page.getByRole('tooltip')).toHaveText('Copy as Markdown',{timeout:500});
 await expect(copyButton).not.toHaveAttribute('title');
 assert.ok(await copyButton.getAttribute('aria-describedby'));
 assert.equal(await page.getByRole('tooltip').evaluate(node=>{const probe=document.createElement('span');probe.style.background='var(--a-surface)';node.parentElement.append(probe);const expected=getComputedStyle(probe).backgroundColor;probe.remove();return getComputedStyle(node).backgroundColor===expected}),true);
 await page.keyboard.press('Escape');await expect(page.getByRole('tooltip')).toHaveCount(0);await expect(copyButton).not.toHaveAttribute('title');
 await copyButton.focus();await expect(page.getByRole('tooltip')).toHaveText('Copy as Markdown');
 await page.keyboard.press('Escape');await expect(page.getByRole('tooltip')).toHaveCount(0);await expect(copyButton).not.toHaveAttribute('title');
 await targetMessage.locator('.a-msg-meta').hover();await expect(page.getByRole('tooltip')).toHaveCount(0);
 assert.match(await targetMessage.locator('time').getAttribute('datetime'),/^1970-/);
 await page.evaluate(()=>document.querySelector('#amp-one').dataset.interfaceDetail='detailed');
 await page.mouse.move(0,0);await page.waitForTimeout(200);
 assert.equal(await targetMessage.locator('.a-message-actions').evaluate(n=>getComputedStyle(n).opacity),'1');
 await page.evaluate(()=>document.querySelector('#amp-one').dataset.interfaceDetail='standard');
 await page.getByRole('button',{name:'Jump to latest messages'}).click();await atBottom();
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

 // The response may grow, but the start stays visible once there is enough
 // content to position the submitted message near the top of the viewport.
 const grow=()=>page.evaluate(()=>fetch('/api/fixture/grow',{method:'POST'}));
 await grow();await grow();await grow();await page.waitForTimeout(300);
 await page.waitForFunction(()=>{const pane=document.querySelector('.a-messages'),user=[...pane.querySelectorAll('.a-user')].at(-1);return Math.abs(user.getBoundingClientRect().bottom-pane.getBoundingClientRect().top-Math.min(160,pane.clientHeight*.25))<3});
 const replyStart=await pane.evaluate(element=>element.scrollTop);
 await grow();await page.waitForTimeout(300);
 assert.ok(Math.abs(await pane.evaluate(element=>element.scrollTop)-replyStart)<3,'streaming should stop following at the submitted message: '+JSON.stringify(await pane.evaluate(element=>({top:element.scrollTop,height:element.scrollHeight,viewport:element.clientHeight})))+' previous '+replyStart);
 await page.getByRole('button',{name:'Jump to latest messages'}).click();await atBottom();
 await grow();await page.waitForTimeout(300);
 assert.ok(await pane.evaluate(element=>element.scrollHeight-element.scrollTop-element.clientHeight)>80,'jumping is a one-time action, not continuous follow');
 await scrollBack();
 const savedTop=await pane.evaluate(element=>element.scrollTop);
 await grow();await page.waitForTimeout(200);
 assert.ok(Math.abs(await pane.evaluate(element=>element.scrollTop)-savedTop)<3,'streaming must preserve scrollback');
 await input.fill('Keep this draft while I check another chat');
 await page.waitForTimeout(400);
 const anchorBefore=await pane.evaluate(element=>{const top=element.getBoundingClientRect().top;const rows=[...element.querySelectorAll('[data-message-id]')];const node=rows.find(n=>n.getBoundingClientRect().bottom>top)||rows.at(-1);return {id:node.dataset.messageId,offset:node.getBoundingClientRect().top-top}});
 await action('session.create',{title:'Another chat'});await action('session.select',{id:sessionId});
 await page.waitForTimeout(500);
 assert.equal(await input.inputValue(),'Keep this draft while I check another chat');
 const offset=await pane.evaluate((element,id)=>[...element.querySelectorAll('[data-message-id]')].find(n=>n.dataset.messageId===id).getBoundingClientRect().top-element.getBoundingClientRect().top,anchorBefore.id);
 assert.ok(Math.abs(offset-anchorBefore.offset)<3,`chat switch restores reading anchor: ${offset} vs ${anchorBefore.offset}`);
 await page.reload();await page.waitForSelector('#amp-one');await page.waitForTimeout(500);
 const afterReload=await pane.evaluate((element,id)=>[...element.querySelectorAll('[data-message-id]')].find(n=>n.dataset.messageId===id).getBoundingClientRect().top-element.getBoundingClientRect().top,anchorBefore.id);
 assert.ok(Math.abs(afterReload-anchorBefore.offset)<3,'tab reload restores reading position');
 assert.equal(await page.locator('.a-rail-mark').nth(3).getAttribute('data-bookmarked'),'true');
 await page.locator('.a-rail-mark').nth(3).hover();
 await page.screenshot({path:process.env.AMPLIFIER_READING_SCREENSHOT||'/tmp/unified-reading-preview.png'});
 assert.deepEqual(errors,[]);
 console.log('Chat navigator tooltip removal, visible turns, preview placement/resize and scroll browser checks passed: delayed send receipt, reply start anchoring, jump button, streaming scrollback, draft preservation and reading position on chat switch/reload.');
}finally{
 await page?.unrouteAll({behavior:'ignoreErrors'});await browser?.close();await vite?.close();if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}
}
