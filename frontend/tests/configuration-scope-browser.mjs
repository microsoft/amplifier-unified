import {fixturePython} from './fixture-python.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from './browser-settings.mjs';
const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/configuration_scope_server.py',import.meta.url))]);
let browser,stderr='';fixture.stderr.on('data',data=>stderr+=data);
try{
 const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(stderr||'Fixture timeout')),60000);fixture.stdout.on('data',data=>{const match=String(data).match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0])}});fixture.on('exit',code=>reject(Error('Fixture exited '+code+stderr)))});
 browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
 const page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[],requests=[];page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.method()==='POST'&&request.url().includes('/api/actions'))requests.push(request.postDataJSON());});
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const files=async()=>await (await page.request.get(base+'/fixture/configuration-files')).json();
 await page.goto(base);await page.waitForSelector('#amp-one');
 const before=await files();
 await action('view.update',{patch:{aiConnectionEditor:{scope:'project'},providerEditor:{scope:'local'}}});
 await openSettingsPage(page,'ai-connections');
 const content=()=>page.locator('.a-settings-page-content:not([hidden])');
 await expect(content().getByText('selected-only-model',{exact:false})).toHaveCount(0);
 await expect(content().getByText('fixture-model',{exact:false}).first()).toBeVisible();
 await content().getByLabel('Use for images',{exact:true}).selectOption('off');
 await expect.poll(async()=>requests.filter(r=>r.action==='providers.configureImages').length).toBe(1);
 assert.equal(requests.find(r=>r.action==='providers.configureImages').args.scope,'global');
 assert.deepEqual(requests.find(r=>r.action==='providers.configureImages').args.configurationTarget,{kind:'global'});
 await expect.poll(async()=>JSON.stringify((await files()).global)!==JSON.stringify(before.global)).toBe(true);
 assert.deepEqual((await files()).selected,before.selected);
 for(const destination of ['providers','routing','registries']){
  await openSettingsPage(page,destination);
  assert.equal(await content().locator('option[value="project"],option[value="local"]').count(),0,destination);
 }
 await action('view.update',{patch:{panel:null,workSurface:'workspace',workWorkspaceId:'other-workspace',workWorkspaceTab:'details'}});
 await page.getByRole('button',{name:'AI connections & images',exact:true}).click();
 const workspace=page.getByRole('region',{name:'Workspace configuration'});
 await expect(workspace.getByText('other-only-model',{exact:false})).toBeVisible();
 await workspace.getByLabel('Use for images',{exact:true}).selectOption('automatic');
 await expect.poll(async()=>requests.filter(r=>r.action==='providers.configureImages').length).toBe(2);
 const changed=requests.filter(r=>r.action==='providers.configureImages').at(-1).args;
 assert.deepEqual(changed.configurationTarget,{kind:'workspace',workspaceId:'other-workspace'});
 assert.equal(changed.scope,'local');
 await expect.poll(async()=>JSON.stringify((await files()).other)!==JSON.stringify(before.other)).toBe(true);
 assert.deepEqual((await files()).selected,before.selected);
 await workspace.getByText('Advanced preferences',{exact:true}).click();
 await expect(workspace.getByLabel('Save in')).toBeVisible();
 assert.deepEqual(await workspace.getByLabel('Save in').locator('option').evaluateAll(rows=>rows.map(row=>row.value)),['local','project']);
 await page.screenshot({path:'/tmp/settings-scope-workspace.png'});
 await openSettingsPage(page,'ai-connections');
 await expect(content().getByLabel('Use for images',{exact:true})).toHaveValue('off');
 assert.equal(await content().locator('option[value="project"],option[value="local"]').count(),0);
 await page.screenshot({path:'/tmp/settings-scope-global.png'});
 // The same global settings must survive rendering beside a help chat. A
 // canvas used to lose configurationTarget in the browser demand projection.
 await action('view.update',{patch:{panel:null,workSurface:'chat'}});
 await action('canvas.show',{kind:'settings'});
 const canvas=page.locator('.a-settings-canvas');
 await expect(canvas.getByText('fixture-model',{exact:false}).first()).toBeVisible();
 await expect(canvas.getByText('Start with one connection',{exact:true})).toHaveCount(0);
 await openSettingsPage(page,'appearance');
 // Navigation paints optimistically; reload only after the host saves it.
 const savedNavigation=page.waitForResponse(response=>{
  const request=response.request();
  if(request.method()!=='POST'||!request.url().includes('/api/actions'))return false;
  const body=request.postDataJSON();
  return body?.action==='view.update'&&body?.args?.patch?.settingsExpanded?.[0]==='ai-connections';
 });
 await openSettingsPage(page,'ai-connections');
 assert.equal((await savedNavigation).ok(),true);
 await expect(canvas.getByText('fixture-model',{exact:false}).first()).toBeVisible();
 await page.reload();
 await expect(canvas.getByText('fixture-model',{exact:false}).first()).toBeVisible();
 await action('canvas.close');
 await openSettingsPage(page,'ai-connections');

 const help=page.getByRole('button',{name:'Help me with settings',exact:true});
 await expect(help).toHaveCount(1);
 await expect(help).toBeVisible();
 const top=await help.boundingBox();
 await page.locator('.a-settings-content').evaluate(node=>node.scrollTop=node.scrollHeight);
 assert.equal((await help.boundingBox()).y,top.y,'Help stays in the fixed header');
 assert.equal(await page.locator('.a-settings-content').getByRole('button',{name:'Help me with settings',exact:true}).count(),0);
 for(const width of [390,320]){
  await page.setViewportSize({width,height:800});
  await expect(page.locator('.a-settings-experience')).toHaveAttribute('data-compact','true');
  await expect.poll(async()=>{const rect=await help.boundingBox();return Boolean(rect&&rect.x>=0&&rect.x+rect.width<=width)}).toBe(true);
  await expect(page.getByRole('button',{name:'Close settings',exact:true})).toBeVisible();
 }
 await page.screenshot({path:'/tmp/settings-scope-header-mobile.png'});
 assert.deepEqual(errors,[]);
 console.log('PASS: global scopes removed; image changes target correct files; workspace controls and drafts isolated.');
}finally{await browser?.close();fixture.kill('SIGTERM')}
