import {finishFixture} from './fixture-lifecycle.mjs';
import {openSettingsPage,openPhoneNotificationSettings} from '../../.build/web-client/tests/browser-settings.mjs';
import {settingsToolObservations} from './settings-tool-observations.mjs';
import {settingsSections} from '../../.build/web-client/src/settings-navigation.js';
import {startSettingsFixture,readAppearance,settledAppearance} from './settings-fixture.mjs';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const {base,stop}=await startSettingsFixture();
let browser,page,failure,releaseDiagnostics;const errors=[],diagnosticNavigation=[];
const diagnosticSnapshot=()=>page.evaluate(()=>{
 const root=document.querySelector('.a-settings-experience'),content=root?.querySelector('.a-settings-page-content:not([hidden])');
 const box=content?.getBoundingClientRect(),style=content&&getComputedStyle(content);
 return {page:root?.dataset.settingsPage,route:root?.dataset.settingsRoute,compact:root?.dataset.compact,index:root?.dataset.settingsIndex,
  configPresent:!!window.amplifier.getState().diagnostics?.config,contentCount:root?.querySelectorAll('.a-settings-page-content:not([hidden])').length,
  box:box?{width:box.width,height:box.height}:null,display:style?.display,visibility:style?.visibility,
  hiddenAncestor:!!content?.closest('[hidden]')};
});
try{
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base+'/');await page.waitForSelector('#amp-one');
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const advanced=async()=>{const details=page.locator('.a-appearance-more');if(!await details.evaluate(el=>el.open))await details.locator(':scope > summary').click()};
 const editor=async()=>{
  await advanced();
  const details=page.locator('.a-appearance-advanced');
  if(!await details.evaluate(el=>el.open))await details.locator(':scope > summary').click();
  await expect(details.getByRole('button',{name:'Save as copy',exact:true})).toBeVisible();
 };
 const presentation=async(id,value)=>{
  if(id!=='scheme')await advanced();
  const control=id==='scheme'?page.getByRole('radiogroup',{name:'Color mode',exact:true}).getByRole('radio',{name:{dark:'Dark',light:'Light',system:'System'}[value],exact:true}):page.getByRole('combobox',{name:'Layout',exact:true});
  // A prior edit may already be painted but still awaiting persistence. Do not
  // accidentally accept its response as the acknowledgment for this edit.
  await expect(control).toBeEnabled();
  const before=await settledAppearance(page),scope=before.activeScope,profile=scope==='client'?'local':'shared';
  const selected=before.effective[id]===value;
  if(!selected){
   const applied=page.waitForResponse(response=>{
    const request=response.request(),body=request.method()==='POST'?request.postDataJSON():null;
    return response.url().endsWith('/api/actions')&&body?.action==='appearance.preferences.update'&&body.args.scope===scope&&body.args.generation===before.generation&&body.args.expectedRevision===before[profile].revision&&body.args.patch[id]===value;
   });
   if(id==='scheme')await control.check();else await control.selectOption(value);
   const application=await applied;assert.ok(application.ok());
   assert.equal((await application.json()).accepted,true);
   await expect.poll(async()=>(await readAppearance(page))[profile].revision).toBe(before[profile].revision+1);
  }
  await expect(control).toBeEnabled();
  assert.equal((await settledAppearance(page)).effective[id],value);
  if(id==='scheme')await expect(control).toBeChecked();else await expect(control).toHaveValue(value);
 };
 await openSettingsPage(page,'notifications');
 assert.equal((await page.locator('.a-dialog').boundingBox()).width,1120);
 assert.equal(await page.locator('.a-dialog').evaluate(el=>getComputedStyle(el).padding),'0px');
 assert.equal(await page.locator('.a-dialog-head').evaluate(el=>getComputedStyle(el).paddingLeft),'24px');
 await openPhoneNotificationSettings(page);
 await page.getByLabel('Notification server',{exact:true}).fill('https://notify.example');
 await page.getByLabel(/^Topic/).fill('fixture-private-topic');
 await page.getByLabel(/^Access token/).fill('fixture-private-token');
 await page.getByRole('checkbox',{name:/^On another device/}).check();
 await page.getByLabel('Include a response preview',{exact:true}).check();
 await openSettingsPage(page,'appearance');await openSettingsPage(page,'notifications');
 assert.equal(await page.getByLabel(/^Topic/).inputValue(),'fixture-private-topic');
 assert.equal(await page.getByLabel(/^Access token/).inputValue(),'fixture-private-token');
 assert.ok(!JSON.stringify(await state()).includes('fixture-private-token'));
 await page.getByRole('button',{name:'Save phone connection',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().notificationSettings?.tokenConfigured);
 await page.waitForFunction(()=>[...document.querySelectorAll('input[type=password]')].every(input=>input.value===''));
 assert.equal(await page.getByLabel(/^Access token/).inputValue(),'');
 await openPhoneNotificationSettings(page);
 await page.getByLabel('Include a response preview',{exact:true}).uncheck();
 await page.waitForFunction(()=>window.amplifier.getState().notificationSettings?.preview===false);
 await page.reload();await openPhoneNotificationSettings(page);
 await page.waitForFunction(()=>window.amplifier.getState().notificationSettings?.server==='https://notify.example');
 let notify=(await state()).notificationSettings;assert.ok(notify.tokenConfigured&&notify.topicConfigured&&notify.enabled&&!notify.preview);assert.equal(notify.topic,undefined);assert.equal(notify.token,undefined);
 // Server-side rejection is visible and does not replace the last good settings.
 await page.getByLabel('Notification server',{exact:true}).fill('http://invalid.example');
 await page.getByRole('button',{name:'Save phone connection',exact:true}).click();
 await page.getByText('Enter an HTTPS notification server URL',{exact:true}).first().waitFor();
 assert.equal((await state()).notificationSettings.server,'https://notify.example');
 await page.getByLabel('Notification server',{exact:true}).fill('https://notify.example');
 await expect(page.getByRole('button',{name:'Save phone connection',exact:true})).toHaveCount(0);
 // Returning to the saved value needs no write; a new valid edit exercises
 // recovery from the rejected URL through the actual save operation.
 await page.getByLabel('Notification server',{exact:true}).fill('https://notify-recovered.example');
 await page.getByRole('button',{name:'Save phone connection',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().notificationSettings?.server==='https://notify-recovered.example');
 await expect(page.getByText('Enter an HTTPS notification server URL',{exact:true})).toHaveCount(0);
 await openSettingsPage(page,'voice');
 await page.locator('#preferred-voice').selectOption('gpt-realtime-2.1');
 await page.waitForFunction(()=>window.amplifier.getState().settings.preferredVoice==='gpt-realtime-2.1');
 await page.getByText('If the preferred model is unavailable',{exact:true}).click();
 await page.getByRole('combobox',{name:'Fallback model',exact:true}).selectOption('gpt-live-1');
 await page.waitForFunction(()=>window.amplifier.getState().settings.fallbackVoice==='gpt-live-1');
 await page.reload();await page.locator('#preferred-voice').waitFor();await page.getByText('If the preferred model is unavailable',{exact:true}).click();
 assert.equal(await page.locator('#preferred-voice').inputValue(),'gpt-realtime-2.1');assert.equal(await page.getByRole('combobox',{name:'Fallback model',exact:true}).inputValue(),'gpt-live-1');
 await openSettingsPage(page,'providers');
 // Missing credentials open this disclosure initially; only expand a closed one.
 if(!await page.locator('.a-provider-access').evaluate(el=>el.open))await page.locator('.a-provider-access summary').click();
 await page.locator('#provider-key-source').selectOption('private');
 await page.locator('#provider-key').fill('fixture-unsaved-key');
 await openSettingsPage(page,'routing');await openSettingsPage(page,'providers');
 assert.equal(await page.locator('#provider-key').inputValue(),'fixture-unsaved-key');
 assert.ok(!JSON.stringify(await state()).includes('fixture-unsaved-key'));
 await openSettingsPage(page,'appearance');await editor();
 const beforePreview=await settledAppearance(page);
 await page.locator('#theme-name').fill('Acceptance skin');await page.locator('#theme-css').fill('#amp-one { --a-accent: #6b4dcc; }');
 await page.getByRole('button',{name:'Preview draft',exact:true}).click();
 await expect.poll(async()=>(await readAppearance(page)).preview?.theme?.name).toBe('Acceptance skin');
 assert.deepEqual((await settledAppearance(page)).effective,beforePreview.effective);
 await openSettingsPage(page,'updates');await openSettingsPage(page,'appearance');await editor();
 await expect.poll(async()=>(await readAppearance(page)).preview).toBe(null);
 await expect(page.locator('#theme-name')).toHaveValue('Acceptance skin');
 await expect(page.locator('#theme-css')).toHaveValue('#amp-one { --a-accent: #6b4dcc; }');
 await page.getByRole('button',{name:'Save as copy',exact:true}).click();
 const use=page.getByRole('button',{name:'Use Acceptance skin',exact:true});await expect(use).toBeVisible();
 assert.deepEqual((await readAppearance(page)).effective,beforePreview.effective,'Saving a library copy never selects it');
 await use.click();
 await expect.poll(async()=>(await readAppearance(page)).theme.name).toBe('Acceptance skin');
 await settledAppearance(page);
 // Persistence must survive a reload even when appearance paints before the
 // server accepts it. Keep these real writes in flight long enough to expose
 // a helper that mistakes optimistic controls for saved settings.
 await page.route('**/api/actions',async route=>{
  if(route.request().postDataJSON()?.action==='appearance.preferences.update')await new Promise(resolve=>setTimeout(resolve,500));
  await route.continue();
 });
 await openSettingsPage(page,'appearance');await presentation('scheme','dark');await presentation('layout','work');
 await page.reload();await page.locator('.a-settings-experience').waitFor();await openSettingsPage(page,'appearance');await editor();await page.locator('#theme-name').waitFor();
 assert.equal(await page.locator('#theme-name').inputValue(),'Acceptance skin');
 // Shell settings hydrate separately from the host's skin controls after reload.
 await expect(page.getByRole('combobox',{name:'Layout',exact:true})).toHaveValue('work');
 assert.equal((await settledAppearance(page)).effective.scheme,'dark');
 await page.unrouteAll({behavior:'wait'});
 await page.getByRole('button',{name:'Restore neutral appearance',exact:true}).click();
 await expect.poll(async()=>(await readAppearance(page)).effective.appearanceId).toBe('reference');
 await settledAppearance(page);
 await openSettingsPage(page,'tool-connections');
 await page.getByRole('button',{name:'Tool catalog',exact:true}).click();
 await page.getByRole('button',{name:/Fixture catalog tool/}).waitFor();
 await page.locator('#filter-smart-tool-catalog').fill('fixture*');
 await page.getByRole('button',{name:/Fixture catalog tool/}).click();await page.getByRole('button',{name:'Inspect source and setup',exact:true}).click();
 await page.getByText('Python 3.13',{exact:true}).waitFor();
 await page.locator('#smart-tool-extras').fill('mcp');
 await page.getByRole('button',{name:'Install package',exact:true}).click();
 await page.getByRole('button',{name:'Configure MCP connection',exact:true}).click();
 await page.locator('#smart-tool-command').fill('/fixture/bin/mcp');
 await page.locator('#smart-tool-arguments').fill('--read-only\n--verbose');
 await page.locator('#smart-tool-environment').fill('TOOL_KEY=FIXTURE_KEY');
 await page.getByRole('button',{name:'Save connection',exact:true}).click();
 await expect.poll(async()=>(await settingsToolObservations(page)).servers.length).toBe(1);
 await page.getByRole('button',{name:'View connections',exact:true}).click();
 await page.getByRole('button',{name:/Fixture catalog tool.*disconnected/i}).waitFor();
 const server=(await settingsToolObservations(page)).servers[0];assert.deepEqual(server.args,['--read-only','--verbose']);assert.deepEqual(server.env,{TOOL_KEY:'FIXTURE_KEY'});
 await page.reload();await page.getByRole('button',{name:/Fixture catalog tool.*disconnected/i}).waitFor();
 // Every declared destination is reachable by pointer and remains inside the
 // dialog on small phones, tablets, desktop, and both appearance schemes.
 let layouts=0;
 for(const scheme of ['light','dark']){
  await openSettingsPage(page,'appearance');await presentation('scheme',scheme);
  for(const width of [320,390,736,1280]){
   await page.setViewportSize({width,height:900});
   for(const section of settingsSections)for(const [id] of section.pages){
    if(id==='diagnostics'&&!diagnosticNavigation.length){
     // The route paints optimistically while diagnostics config is still
     // demand-loaded. Hold that one real navigation to distinguish route entry
     // from content readiness, without injecting config or replaying navigation.
     const observation={width,scheme,matchingRequests:0};diagnosticNavigation.push(observation);
     let navigationRequest;
     const gate=new Promise(resolve=>{releaseDiagnostics=resolve});
     const hold=async route=>{
      const request=route.request(),body=request.method()==='POST'?request.postDataJSON():null;
      if(body?.action==='view.update'&&Array.isArray(body.args?.patch?.settingsExpanded)&&body.args.patch.settingsExpanded.includes('diagnostics')){
       observation.matchingRequests++;
       if(!navigationRequest){
        navigationRequest=request;observation.requestId=body.id;await gate;
       }
      }
      await route.continue();
     };
     await page.route('**/api/actions',hold);
     const response=page.waitForResponse(reply=>{
      return reply.request()===navigationRequest;
     },{timeout:10000}).then(value=>({value}),error=>({error}));
     try{
      await openSettingsPage(page,id);
      await expect.poll(()=>!!navigationRequest,{timeout:5000}).toBe(true);
      const submitted=navigationRequest.postDataJSON();
      assert.equal(typeof submitted.id,'string');assert.ok(submitted.id.length>0&&submitted.id.length<=200);
      assert.equal(submitted.args.patch.panel,'settings');
      assert.equal(submitted.args.patch.settingsSection,'maintenance');
      assert.deepEqual(submitted.args.patch.settingsExpanded,['diagnostics']);
      assert.equal(observation.matchingRequests,1,'One gesture submits one Diagnostics navigation');
      observation.beforeReceipt=await diagnosticSnapshot();
      console.log(JSON.stringify({diagnosticNavigation:observation}));
      releaseDiagnostics();
      const settled=await response;if(settled.error)throw settled.error;
      const receipt=await settled.value.json();
      const replied=settled.value.request().postDataJSON();
      assert.equal(replied.id,submitted.id);
      observation.receipt={requestId:replied.id,status:settled.value.status(),accepted:receipt.accepted,stateRevision:receipt.revision??receipt.state?.revision??null};
      assert.equal(receipt.accepted,true,'Diagnostics navigation accepted');
      await expect.poll(()=>page.evaluate(()=>!!window.amplifier.getState().diagnostics?.config),{timeout:5000}).toBe(true);
      await expect(page.locator('.a-settings-page-content:not([hidden])'),id).toBeVisible({timeout:5000});
      observation.afterReceipt=await diagnosticSnapshot();
      assert.equal(observation.matchingRequests,1,'Diagnostics navigation was not repeated during settlement');
      console.log(JSON.stringify({diagnosticNavigation:observation}));
     }finally{releaseDiagnostics();await page.unroute('**/api/actions',hold)}
    }else{
     await openSettingsPage(page,id);
     await expect(page.locator('.a-settings-page-content:not([hidden])'),id).toBeVisible({timeout:5000});
    }
    assert.ok(await page.locator('.a-settings-content').evaluate(el=>el.scrollWidth<=el.clientWidth+1),`${id} overflow at ${width} ${scheme}`);
    assert.ok(await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1),`${id} dialog overflow at ${width}`);
    layouts++;
   }
  }
 }
 await openSettingsPage(page,'notifications');await page.screenshot({animations:'disabled',path:'/tmp/settings-notifications-final.png'});
 await openSettingsPage(page,'providers');await page.screenshot({animations:'disabled',path:'/tmp/settings-providers-final.png'});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({destinations:settingsSections.reduce((count,section)=>count+section.pages.length,0),layouts,notificationPersistence:true,privateDrafts:true,voicePersistence:true,skinPersistence:true,catalogToConnection:true,browserErrors:0}));
}catch(error){
 failure=error;
 console.error(error);
 if(page)try{console.error(JSON.stringify({diagnosticNavigation,currentDiagnosticSnapshot:await diagnosticSnapshot()}))}catch{}
 try{if(page)await page.screenshot({animations:'disabled',path:'/tmp/settings-completeness-failure.png'})}catch(captureError){console.error('Failure screenshot unavailable:',captureError.message)}
 throw error;
}
finally{releaseDiagnostics?.();await finishFixture({label:"Settings fixture",close:[["Settings browser",()=>browser?.close()]],stop,originalError:failure})}
