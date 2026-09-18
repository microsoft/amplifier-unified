// Real source UI against an isolated host. GitHub and providers are mocked;
// this test must never create a real issue or read existing app data.
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
import settings_ui_server as fixture
from amplifier_web import feedback
calls=[]
async def create_issue(title, body):
 calls.append({'title':title,'body':body})
 await asyncio.sleep(.15)
 return feedback.ISSUES_URL+'/42'
feedback.create_issue=create_issue
async def main():
 with tempfile.TemporaryDirectory(prefix='amplifier-feedback-ui-') as home:
  app=await fixture.main(Path(home));service=app['service']
  service.state['updates'].update(application={'id':'application','kind':'app','label':'Amplifier Unified','current':'0.6.3','latest':'v0.6.4','status':'update','detail':'A newer application release is available; installation restarts the host when idle.'},appAvailable=True)
  async def inspect(request):return web.json_response({'calls':calls})
  app.router.add_get('/api/fixture/feedback',inspect)
  runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
  port=site._server.sockets[0].getsockname()[1]
  app['allowed_origins']=app['allowed_origins']|{f'http://127.0.0.1:{port}'}
  print(json.dumps({'port':port}),flush=True)
  try:await asyncio.Event().wait()
  finally:await runner.cleanup()
asyncio.run(main())
`],{stdio:['ignore','pipe','pipe']});
let fixtureLog='';fixture.stderr.on('data',chunk=>fixtureLog+=chunk);
const ready=new Promise((resolve,reject)=>{let text='';fixture.stdout.on('data',chunk=>{text+=chunk;const line=text.split('\n').find(row=>row.startsWith('{"port":'));if(line)resolve(JSON.parse(line).port)});fixture.once('exit',code=>reject(new Error(`Feedback fixture exited ${code}: ${fixtureLog}`)))});
let vite,browser;
try{
 const port=await Promise.race([ready,new Promise((_,reject)=>setTimeout(()=>reject(new Error('Feedback fixture did not start')),15000).unref())]);
 const target=`http://127.0.0.1:${port}`;
 vite=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,configure(proxy){proxy.on('proxyReq',request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(vite.resolvedUrls.local[0]);await page.waitForSelector('#amp-one');
 await page.getByRole('button',{name:'Send feedback',exact:true}).click();
 await page.getByLabel('Title',{exact:true}).fill('Canvas feedback fixture');
 await page.getByLabel('Details',{exact:true}).fill('This is a mocked browser test.');
 assert.equal(await page.getByRole('checkbox',{name:'Include app version and operating system'}).isChecked(),false);
 await page.getByRole('dialog').getByRole('button',{name:'Send feedback',exact:true}).click();
 await page.getByText('Feedback sent. Thank you.',{exact:true}).waitFor();
 assert.equal(await page.locator('.a-feedback .a-check-result.success').count(),1);
 assert.equal(await page.getByRole('link',{name:'View issue'}).getAttribute('href'),'https://github.com/bkrabach/amplifier-unified/issues/42');
 await page.screenshot({path:'/tmp/amplifier-feedback-desktop.png'});
 const saved=await page.evaluate(()=>window.amplifier.getState().view.feedbackDraft.pending);
 const calls=await page.evaluate(()=>fetch('/api/fixture/feedback').then(response=>response.json()));
 assert.equal(calls.calls.length,1);assert.equal(calls.calls[0].title,'Canvas feedback fixture');assert.doesNotMatch(calls.calls[0].body,/App version:|fixture-private-key/);
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await page.getByRole('button',{name:'Send feedback',exact:true}).click();
 await page.getByText('Feedback sent. Thank you.',{exact:true}).waitFor();
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/tmp/amplifier-feedback-narrow.png'});
 assert.ok(await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
 await page.evaluate(payload=>window.amplifier.dispatch('feedback.submit',payload),saved);
 assert.equal((await page.evaluate(()=>fetch('/api/fixture/feedback').then(response=>response.json()))).calls.length,1);
 await page.reload();await page.getByText('Feedback sent. Thank you.',{exact:true}).waitFor();
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'settings',settingsSection:'maintenance',settingsExpanded:['updates']}}));
 await page.locator('[aria-label="Application release status"]').waitFor();
 assert.equal(await page.locator('.a-app-update-versions').textContent(),'Installed0.6.3Latest releasev0.6.4');
 await page.screenshot({path:'/tmp/amplifier-updates-narrow.png'});
 await page.setViewportSize({width:1280,height:900});await page.screenshot({path:'/tmp/amplifier-updates-desktop.png'});
 assert.deepEqual(errors,[]);
 console.log('Feedback browser passed: header entry, mocked submit, green receipt/link, reopen/reload, exact shared-action retry, narrow layout; update version cards captured. No live issue created.');
}finally{
 await browser?.close();await vite?.close();if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}
}
