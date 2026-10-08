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
class CountingRuntime(fixture.ChatRuntime):
 async def send(self, session, text, input_id, emit):
  await emit('runtime.status', {'sessionId':session['id'], 'status':'working'})
  await asyncio.Event().wait()
fixture.fixture.Runtime=CountingRuntime
async def main():
 with tempfile.TemporaryDirectory(prefix='amplifier-count-ui-') as home:
  app=await fixture.fixture.main(Path(home))
  service=app['service']
  session=service.state['sessions'][0]
  session['messages']=[{'id':f'history-{i}', 'role':'user' if i%2==0 else 'assistant', 'text':f'History {i}\\n\\n'+('A useful paragraph. '*35), 'createdAt':i+1} for i in range(30)]
  from amplifier_web.session_health import generation_failure
  session['failure']=generation_failure({'error_category':'context_measurement', 'error_type':'LLMError',
   'count_failure':{'category':'service','retryable':True,'httpStatus':503,'attempts':3,'requestId':'req_count_fixture'}})
  session['error']=session['failure']['summary']
  session['status']='error'
  service._publish()
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
 fixture.once('exit',code=>reject(new Error(`Counting fixture exited ${code}: ${fixtureLog}`)));
});
let vite,browser,page;
try{
 const port=await Promise.race([fixtureReady,new Promise((_,reject)=>setTimeout(()=>reject(new Error('Counting fixture did not start')),15000).unref())]);
 const target=`http://127.0.0.1:${port}`;
 vite=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,configure(proxy){proxy.on('proxyReq',request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});const errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(vite.resolvedUrls.local[0]);await page.waitForSelector('#amp-one');

 const banner=page.locator('.a-alert').filter({hasText:'Could not check conversation size.'});
 await expect(banner).toBeVisible();
 await expect(banner).not.toContainText('Context compaction failed');
 await banner.getByRole('button',{name:'View error details'}).click();
 await expect(page.getByText('The counting service is temporarily unavailable.',{exact:false})).toBeVisible();
 await expect(page.getByRole('definition').filter({hasText:'req_count_fixture'})).toBeVisible();
 await expect(page.getByText('Counting attempts',{exact:true})).toBeVisible();
 await page.screenshot({path:'/tmp/count-recovery-details.png'});
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 const sends=[];
 page.on('request',request=>{if(request.url().includes('/api/actions')){try{const body=request.postDataJSON();if(body.action==='conversation.send')sends.push(body)}catch{}}});
 await banner.getByRole('button',{name:'Continue conversation',exact:true}).click();
 await expect.poll(()=>sends.length).toBe(1);
 assert.equal(sends[0].args.preserveDraft,true);
 assert.match(sends[0].args.text,/Check what has already completed before repeating any actions/);
 assert.deepEqual(errors,[]);
 console.log('Counting failure browser passed: accurate banner, guidance, request ID/status/attempt details, single explicit continuation.');
}finally{
 await page?.unrouteAll({behavior:'ignoreErrors'});await browser?.close();await vite?.close();if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}
}
