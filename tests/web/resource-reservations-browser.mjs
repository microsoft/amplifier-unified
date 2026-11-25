import './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {spawn} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-resource-reservations';
await mkdir(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'/.venv/bin/python',[root+'/tests/fixtures/empty_host_ui_server.py','--chat-controls','--reservation-retirement'],{stdio:['ignore','pipe','inherit']});
let browser,page;
try{
 const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1100,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[],calls=[];page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(new URL(request.url()).pathname==='/api/actions'&&request.method()==='POST'){const raw=request.postDataJSON();calls.push(raw.action==='shell.command'?raw.args:raw)}});
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const observe=()=>page.request.get(url+'/fixture').then(response=>response.json());
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const workspace=(await state()).workspaces[0];
 const created=await action('session.create',{workspace:workspace.path,title:'Resource retirement check'});
 assert.ok(created.accepted);const sid=created.sessionId||created.result?.sessionId;
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Private unrelated draft');
 const baseline=await observe();assert.equal(baseline.reservationRetirement.before.claims,2);assert.equal(baseline.reservationRetirement.before.allocations,1);
 await action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:workspace.id,workWorkspaceTab:'details'}});
 const inventory=page.getByRole('region',{name:'Workspace resources',exact:true});
 await expect(inventory).toBeVisible();
 await expect(page.getByText('Resource reservations',{exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:/Reserve resource|Reconcile reservation|Release reservation/})).toHaveCount(0);
 // The real inventory editor still works, with no save, stop or deletion effect.
 await inventory.getByRole('button',{name:'Record resource',exact:true}).click();
 await inventory.getByLabel('Resource identifier',{exact:true}).fill('unsaved-inventory-draft');
 await inventory.getByRole('button',{name:/Cancel/}).click();
 await page.screenshot({path:output+'/workspace.png'});
 const managed=await action('workspace.resources.managed',{workspaceId:workspace.id,sessionId:sid});
 assert.ok(managed.accepted);assert.ok(managed.result.storage);assert.ok(managed.result.coverage);
 const context=await action('coordination.context',{sessionId:sid});
 const recap=await action('coordination.recap',{sessionId:sid});
 assert.ok(context.accepted&&recap.accepted);assert.equal(context.result.resources,undefined);
 assert.ok(!JSON.stringify(recap.result).includes('Resource reservation'));
 await openSettingsPage(page,'testing');
 const execution=page.getByRole('region',{name:'Test execution limits',exact:true});
 await execution.getByText('Advanced execution limits',{exact:true}).click();
 for(const label of ['Cooperative reservations across this installation','Cooperative reservations per chat'])await expect(execution.getByLabel(label,{exact:true})).toHaveCount(0);
 await expect(execution.getByLabel('Owned resources across this installation',{exact:true})).toHaveValue('21');
 await expect(execution.getByLabel('Owned test environments per chat',{exact:true})).toHaveValue('4');
 await expect(execution.getByLabel('Maximum pending checks',{exact:true})).toHaveValue('19');
 await expect(execution.getByLabel('Browser and frontend checks: CPU cores',{exact:true})).toHaveValue('3');
 const policy=(await action('testing.execution.read')).result;
 assert.equal(policy.effectiveConcurrency.maxResourceReservations,undefined);
 assert.equal(policy.effectiveConcurrency.maxResourceReservationsPerChat,undefined);
 for(const width of [1100,390]){
  await page.setViewportSize({width,height:900});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await expect(execution.getByRole('button',{name:'Save execution limits',exact:true})).toBeDisabled();
  await execution.getByLabel('Owned resources across this installation',{exact:true}).fill('22');
  await expect(execution.getByRole('button',{name:'Save execution limits',exact:true})).toBeEnabled();
  await execution.getByRole('button',{name:'Discard execution edits and refresh',exact:true}).click();
  await expect(execution.getByLabel('Owned resources across this installation',{exact:true})).toHaveValue('21');
  await page.screenshot({path:output+'/testing-'+width+'.png'});
 }
 await page.getByRole('button',{name:'Close settings',exact:true}).click();
 await action('view.update',{patch:{workSurface:'chat'}});
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Private unrelated draft');
 const final=await observe();assert.deepEqual(final.reservationRetirement.current,baseline.reservationRetirement.before);
 assert.equal(final.sent.length,0);assert.equal(final.stopped.length,0);
 const retired=['reservations','reserve','release','uncertain'].map(name=>'workspace.resources.'+name);
 assert.deepEqual(calls.filter(row=>retired.includes(row.action)),[]);
 assert.deepEqual(calls.filter(row=>/workspace\.resources\.(add|update|retire)|\.stop$|\.remove$|\.save$/.test(row.action)),[]);
 assert.deepEqual(errors,[]);
 const report={status:'passed',scenarios:8,build:(await state()).client?.build,retained:final.reservationRetirement,
  checks:['no reservation controls or requests','actual inventory usable without mutation','managed owners readable','context and recap omit claims','legacy positive limits preserved','retired quota fields absent','desktop and mobile','private draft and historical rows unchanged'],
  limits:['Synthetic runtime; no live model or infrastructure teardown. Raw retired dispatch/discovery and physical-owner guards are paired Python checks.'],calls:calls.map(row=>row.action)};
 await writeFile(output+'/report.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(error){await page?.screenshot({path:output+'/failure.png'}).catch(()=>{});console.error((await page?.locator('body').innerText())?.slice(-5000));throw error}
finally{await browser?.close();fixture.kill('SIGTERM')}
