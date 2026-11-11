// Preview completion/cancellation is serialized across Settings navigation and
// component replacement. Draft text is tab-private and never projected to view.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',
 ['-u',root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stop=ownFixture(fixture,{label:'Appearance preview navigation fixture'});
let browser,release,failure;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{
   const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}
  }catch{}});
 });
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:900},
  extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const hasPreview=async()=>Boolean((await state()).appearance.preview);
 await page.goto(url);await openSettingsPage(page,'custom-appearance');
 await page.getByText('Advanced customization',{exact:true}).click();
 const name=page.locator('#theme-name'),css=page.locator('#theme-css');
 const firstCss='#amp-one { --a-accent: #6b4dcc; }';
 await name.fill('Navigation preview');await css.fill(firstCss);
 const original=(await state()).appearance.theme;
 let mode='hold',held=0;
 await page.route('**/api/actions',async route=>{
  const request=route.request(),body=request.method()==='POST'?request.postDataJSON():null;
  if(body?.action!=='appearance.preview'||mode==='pass')return route.continue();
  const response=mode==='after'?await route.fetch():null;
  held++;await new Promise(resolve=>release=resolve);
  if(response)return route.fulfill({response});
  return route.continue();
 });
 for(const phase of ['before','after','aba']){
  mode=phase==='after'?'after':'hold';
  const target=held+1;
  await page.getByRole('button',{name:'Preview draft',exact:true}).click();
  await expect.poll(()=>held).toBe(target);
  await expect(page.getByRole('button',{name:'Preparing…',exact:true})).toBeDisabled();
  await openSettingsPage(page,'updates');
  if(phase==='aba')await openSettingsPage(page,'custom-appearance');
  const completed=page.waitForResponse(response=>response.url().endsWith('/api/actions')
   &&response.request().method()==='POST'&&response.request().postDataJSON()?.action==='appearance.preview');
  release();release=null;await completed;
  await expect.poll(hasPreview,{message:phase+' navigation retires the transient preview'}).toBe(false);
  await openSettingsPage(page,'custom-appearance');
  await expect(page.locator('.a-appearance-preview')).toHaveCount(0);
  await expect(name).toHaveValue('Navigation preview');await expect(css).toHaveValue(firstCss);
  assert.deepEqual((await state()).appearance.theme,original,'Preview navigation cannot change the selected library appearance.');
  assert.equal(JSON.stringify((await state()).view).includes(firstCss),false,'Editor content is private to the browser tab.');
 }

 // Closing destroys one Settings component; a new draft preview after reopen
 // must not be cancelled by the older request's delayed completion.
 mode='hold';const oldCount=held;
 await page.getByRole('button',{name:'Preview draft',exact:true}).click();
 await expect.poll(()=>held).toBe(oldCount+1);
 const closed=page.waitForResponse(response=>{
  if(response.request().method()!=='POST'||!response.url().endsWith('/api/actions'))return false;
  const body=response.request().postDataJSON();return body?.action==='view.update'&&body.args?.patch?.panel===null;
 });
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await expect.poll(async()=>(await state()).view.panel).toBe(null);
 await expect(page.locator('.a-settings-experience')).toHaveCount(0);
 await openSettingsPage(page,'custom-appearance');
 await page.getByText('Advanced customization',{exact:true}).click();
 await name.fill('Successor preview');
 const successor=page.waitForResponse(response=>response.url().endsWith('/api/actions')
  &&response.request().method()==='POST'&&response.request().postDataJSON()?.action==='appearance.preview'
  &&response.request().postDataJSON().args.name==='Successor preview');
 await page.getByRole('button',{name:'Preview draft',exact:true}).click();
 mode='pass';release();release=null;
 const [,closeReceipt]=await Promise.all([successor,closed]);
 assert.ok(closeReceipt.ok());assert.equal((await closeReceipt.json()).accepted,true);
 await expect.poll(hasPreview).toBe(true);
 await expect(page.locator('.a-appearance-preview')).toContainText('Successor preview');
 await page.locator('.a-appearance-preview').getByRole('button',{name:'Cancel preview',exact:true}).click();
 await expect.poll(hasPreview).toBe(false);
 await expect(name).toHaveValue('Successor preview');
 await page.unrouteAll({behavior:'wait'});
 await expect(page.locator('#theme-css')).toHaveValue(firstCss);
 assert.deepEqual((await state()).appearance.theme,original);
 assert.deepEqual((await(await page.request.get(url+'/fixture')).json()).sent,[]);
 assert.deepEqual(errors,[]);
 console.log('Appearance preview navigation passed: pending requests retire on navigation, tab-private editor text survives, and a successor preview is not cancelled by a late completion.');
}catch(error){failure=error;throw error}
finally{
 release?.();
 await finishFixture({label:'Appearance preview navigation fixture',close:[['Appearance preview navigation browser',()=>browser?.close()]],stop,originalError:failure});
}