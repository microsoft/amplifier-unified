// Isolated real UI and service, with all GitHub operations intercepted.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {once} from 'node:events';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u','-c',`
import asyncio, json, sys, tempfile
from pathlib import Path
from aiohttp import web
sys.path.insert(0, ${JSON.stringify(fileURLToPath(new URL('../../tests/fixtures',import.meta.url)))})
import settings_ui_server as fixture
from amplifier_web import feedback
posts=[]
url=feedback.ISSUES_URL+'/42'
async def api(endpoint,payload):
 if endpoint=='user':return {'id':7,'login':'fixture-owner'}
 if payload is not None:
  posts.append(payload)
  return {'id':123,'html_url':url+'#issuecomment-123','user':{'id':7}}
 if '/comments?' in endpoint:return [{'id':99,'body':'Maintainer response','user':{'id':8,'login':'maintainer'}}]
 return {'number':42,'html_url':url,'user':{'id':7},'title':'Original report','body':'Original details\\n<!-- amplifier-feedback:original-feedback -->','state':'open'}
feedback.github_api=api
async def main():
 with tempfile.TemporaryDirectory(prefix='amplifier-followup-ui-') as home:
  app=await fixture.main(Path(home));service=app['service']
  service.feedback.accept({'requestId':'original-feedback','title':'Original report','body':'Original details','category':'bug','includeDiagnostics':False})
  await service.feedback.update('original-feedback',status='submitted',url=url)
  async def inspect(request):return web.json_response({'posts':posts})
  app.router.add_get('/api/fixture/followup',inspect)
  runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
  port=site._server.sockets[0].getsockname()[1]
  app['allowed_origins']=app['allowed_origins']|{f'http://127.0.0.1:{port}'}
  print(json.dumps({'port':port}),flush=True)
  try:await asyncio.Event().wait()
  finally:await runner.cleanup()
asyncio.run(main())
`],{stdio:['ignore','pipe','pipe']});
let fixtureLog='';fixture.stderr.on('data',chunk=>fixtureLog+=chunk);
const ready=new Promise((resolve,reject)=>{let text='';fixture.stdout.on('data',chunk=>{text+=chunk;const line=text.split('\n').find(row=>row.startsWith('{"port":'));if(line)resolve(JSON.parse(line).port)});fixture.once('exit',code=>reject(new Error(`Fixture exited ${code}: ${fixtureLog}`)))});
let vite,browser;
try{
 const port=await Promise.race([ready,new Promise((_,reject)=>setTimeout(()=>reject(Error('Fixture timeout')),15000).unref())]);
 const target=`http://127.0.0.1:${port}`;
 vite=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,configure(proxy){proxy.on('proxyReq',request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(vite.resolvedUrls.local[0]);await page.waitForSelector('#amp-one');
 await page.getByRole('button',{name:'More app options',exact:true}).click();await page.getByRole('button',{name:'Send feedback',exact:true}).click();
 await page.getByLabel('Submitted report',{exact:true}).selectOption('original-feedback');
 await page.getByRole('button',{name:'Refresh report',exact:true}).click();
 await page.getByText('Feedback report loaded.',{exact:true}).waitFor();
 await page.getByText('Maintainer response',{exact:true}).waitFor();
 // Each browser owns its selected report page and unsent follow-up draft.
 const other=await browser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 other.on('pageerror',error=>errors.push(error.message));
 await other.goto(vite.resolvedUrls.local[0]);await other.waitForSelector('#amp-one');
 await other.getByRole('button',{name:'More app options',exact:true}).click();await other.getByRole('button',{name:'Send feedback',exact:true}).click();
 await other.getByLabel('Submitted report',{exact:true}).selectOption('original-feedback');
 await other.getByLabel('Add a comment',{exact:true}).fill('Unsent draft on the other browser.');
 await other.evaluate(()=>window.amplifier.dispatch('feedback.get',{requestId:'second-browser-read',feedbackId:'original-feedback',page:2}));
 await other.waitForFunction(()=>window.amplifier.getState().feedback.report?.page===2);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().feedback.report?.page),1);
 await page.getByLabel('Add a comment',{exact:true}).fill('Additional details from the browser.');
 await page.getByRole('button',{name:'Send comment',exact:true}).click();
 await page.getByText('Comment added to your feedback report.',{exact:true}).waitFor();
 assert.equal(await page.getByRole('link',{name:'View comment',exact:true}).getAttribute('href'),'https://github.com/bkrabach/amplifier-unified/issues/42#issuecomment-123');
 const pending=await page.evaluate(()=>window.amplifier.getState().view.feedbackFollowupDraft.pending);
 await page.evaluate(args=>window.amplifier.dispatch('feedback.comment',args),pending);
 const sent=await page.evaluate(()=>fetch('/api/fixture/followup').then(response=>response.json()));
 assert.equal(sent.posts.length,1);assert.match(sent.posts[0].body,/Additional details from the browser/);
 assert.equal(await other.getByLabel('Add a comment',{exact:true}).inputValue(),'Unsent draft on the other browser.');
 assert.equal(await other.evaluate(()=>window.amplifier.getState().feedback.report?.page),2);
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.locator('.a-dialog').evaluate(element=>element.scrollWidth<=element.clientWidth+1));
 await page.reload();await page.getByText('Comment added to your feedback report.',{exact:true}).waitFor();
 assert.equal(await page.getByLabel('Add a comment',{exact:true}).inputValue(),'Additional details from the browser.');
 assert.deepEqual(errors,[]);
 console.log('Feedback follow-up browser passed: report/comments read, user comment, agent retry deduplicated, independent browser pages/drafts, reload retains frozen draft, narrow layout; no live GitHub writes.');
}finally{
 await browser?.close();await vite?.close();if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}
}
