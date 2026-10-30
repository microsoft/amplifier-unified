// Uses the real chat UI and isolated runtime. No model or existing user data.
// Vite serves source directly, so this regression does not rebuild static assets.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {once} from 'node:events';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const nonReduction=process.env.TEST_NATIVE_NO_REDUCTION==='1';
const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp';
const root=fileURLToPath(new URL('../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u','-c',`
import asyncio, json, os, sys, tempfile
from pathlib import Path
from aiohttp import web
if os.environ.get('AMPLIFIER_TEST_PACKAGE_ROOT'):
 import amplifier_web
 assert Path(amplifier_web.__file__).resolve().parent == Path(os.environ['AMPLIFIER_TEST_PACKAGE_ROOT']).resolve()
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
  if ${nonReduction ? 'True' : 'False'}:
   session['failure']=generation_failure({'error_category':'context_compaction',
    'error_type':'CompactionError','error_code':'native_no_reduction','error_stage':'context_preparation'})
  session['error']=session['failure']['summary']
  session['status']='error'
  service._publish()
  runner=web.AppRunner(app);await runner.setup()
  site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
  port=site._server.sockets[0].getsockname()[1]
  app['allowed_origins']=app['allowed_origins']|{f'http://127.0.0.1:{port}'}
  print(json.dumps({'port':port,'sessionId':session['id']}),flush=True)
  try:await asyncio.Event().wait()
  finally:await runner.cleanup()
asyncio.run(main())
`],{stdio:['ignore','pipe','pipe']});
let fixtureLog='';fixture.stderr.on('data',chunk=>{fixtureLog+=chunk});
const fixtureReady=new Promise((resolve,reject)=>{
 let text='';fixture.stdout.on('data',chunk=>{text+=chunk;const line=text.split('\n').find(row=>row.startsWith('{"port":'));if(line)resolve(JSON.parse(line))});
 fixture.once('exit',code=>reject(new Error(`Counting fixture exited ${code}: ${fixtureLog}`)));
});
let vite,browser,page;
try{
 const {port,sessionId}=await Promise.race([fixtureReady,new Promise((_,reject)=>setTimeout(()=>reject(new Error('Counting fixture did not start')),15000).unref())]);
 const target=`http://127.0.0.1:${port}`;
 vite=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});const errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(vite.resolvedUrls.local[0]);await page.waitForSelector('#amp-one');
 const openFixtureChat=async (targetPage,{touch=false}={})=>{
  const card=targetPage.locator(`article[data-session-id="${sessionId}"]`);
  await expect(card).toContainText('Settings test');
  const open=card.getByRole('button',{name:/^Settings test/});
  if(touch)await open.tap();else await open.click();
  await expect(targetPage.getByRole('navigation',{name:'Breadcrumb'}).locator('[aria-current=page]')).toContainText('Settings test');
  await expect.poll(()=>targetPage.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(sessionId);
 };
 await openFixtureChat(page);

 const banner=page.locator('.a-alert').filter({hasText:nonReduction?'Native compaction could not confirm a smaller continuation request.':'The provider’s token-counting service was temporarily unavailable.'});
 await expect(banner).toBeVisible();
 await expect(banner).not.toContainText('Context compaction failed');
 const details= banner.getByRole('button',{name:'View error details'});
 await details.focus(); await page.keyboard.press('Enter');
 if(nonReduction){
  await expect(page.getByText('A recovery copy with the same history can hit the same limit',{exact:false})).toBeVisible();
  await expect(page.getByText('Copy the chat diagnostics',{exact:false})).toBeVisible();
 }else{
  await expect(page.getByText('The counting service is temporarily unavailable.',{exact:false})).toBeVisible();
  await page.getByText('Technical details',{exact:true}).click();
  await expect(page.getByRole('definition').filter({hasText:'req_count_fixture'})).toBeVisible();
  await expect(page.getByText('Counting attempts',{exact:true})).toBeVisible();
  const diagnostic=page.getByRole('definition');
  await expect(diagnostic.filter({hasText:/^503$/})).toBeVisible();
  await expect(diagnostic.filter({hasText:/^3$/})).toBeVisible();
 }
 await page.screenshot({path:`${artifacts}/${nonReduction?'nonreduction':'count'}-recovery-details.png`});
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 if(nonReduction){
  const narrow=await browser.newPage({viewport:{width:402,height:700},hasTouch:true,
    extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  await narrow.goto(vite.resolvedUrls.local[0]); await narrow.waitForSelector('#amp-one');
  await openFixtureChat(narrow,{touch:true});
  await narrow.locator('.a-alert').filter({hasText:'Native compaction'}).getByRole('button',{name:'View error details'}).tap();
  await expect(narrow.getByText('Copy the chat diagnostics',{exact:false})).toBeVisible();
  await narrow.screenshot({path:`${artifacts}/nonreduction-recovery-narrow.png`});
  await narrow.getByRole('button',{name:'Close panel',exact:true}).tap();
  await narrow.close();
 }
 const sends=[];
 page.on('request',request=>{if(request.url().includes('/api/actions')){try{const body=request.postDataJSON();if(body.action==='conversation.send')sends.push(body)}catch{}}});
 await banner.getByRole('button',{name:'Continue conversation',exact:true}).click();
 await expect.poll(()=>sends.length).toBe(1);
 assert.equal(sends[0].args.preserveDraft,true);
 assert.match(sends[0].args.text,/Check what has already completed before repeating any actions/);
 assert.deepEqual(errors,[]);
 console.log(nonReduction?'Non-reduction browser passed: specific diagnosis, same-history warning, desktop keyboard and narrow touch details, single explicit continuation.':'Counting failure browser passed: accurate banner, guidance, request ID/status/attempt details, single explicit continuation.');
}finally{
 await page?.unrouteAll({behavior:'ignoreErrors'});await browser?.close();await vite?.close();if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}
}
