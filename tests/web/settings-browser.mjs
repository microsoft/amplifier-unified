import {finishFixture} from './fixture-lifecycle.mjs';
import {showComposerOption,composerOptionsPanel} from './composer-options-helpers.mjs';
import {openChatAdvancedPage} from './browser-chat-pages.mjs';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {startSettingsFixture,readAppearance,settledAppearance,updateAppearance} from './settings-fixture.mjs';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const {base,stop}=await startSettingsFixture({SETTINGS_RELEASE_FIXTURE:'1'});
let browser,page,failure,releaseDraft;const errors=[];
const state=()=>page.request.get(base+'/api/state').then(r=>r.json());
try{
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1100,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base+'/');
 await page.waitForSelector('#amp-one');
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'settings',settingsSection:'setup',settingsExpanded:[]}}));
 await openSettingsPage(page,'providers');
 await page.locator('[data-part=provider-settings] .a-collection-row').first().waitFor();
 assert.equal(await page.getByRole('button',{name:'Conversation defaults',exact:false}).count(),0);
 await page.locator('#filter-providers').fill('t*');
 assert.equal(await page.locator('[data-part=provider-settings] .a-collection-row').count(),2);
 assert.equal(await page.locator('.a-order-grip').count(),0);
 await page.getByRole('button',{name:'Clear filter providers',exact:true}).click();
 await page.locator('select#provider-model-catalog').waitFor();
 await page.locator('#provider-model-catalog').selectOption('fixture-model');
 assert.equal(await page.locator('input#provider-model').count(),1);
 await page.getByRole('button',{name:'Refresh models',exact:true}).click();
 await page.getByRole('button',{name:'Test connection',exact:true}).click();
 await page.getByText('Provider check passed',{exact:true}).waitFor();
 assert.equal(await page.locator('.a-check-result.success').filter({hasText:'Provider check passed'}).count(),1);
 await page.getByRole('button',{name:'Preference order',exact:true}).click();await page.getByLabel('Position of one',{exact:true}).selectOption({value:'2'});await page.getByRole('button',{name:'Save order',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().setup.providers[2]?.id==='one');
 assert.deepEqual((await state()).setup.providers.map(row=>row.id),['two','three','one']);
 await openSettingsPage(page,'overview');
 await openSettingsPage(page,'routing');
 await page.locator('[data-part=routing-settings] [data-collection-id=general]>button').click();
 await page.locator('#routing-name').waitFor();
 assert.equal(await page.locator('#routing-name').inputValue(),'balanced');
 await page.getByLabel('general model 1',{exact:true}).fill('fixture-alternative');
 await page.getByRole('button',{name:'Save active profile',exact:true}).click();
 await page.getByText('Routing profile saved and selected.').waitFor();
 assert.equal((await state()).setup.matrix.roles.general.candidates[0].model,'fixture-alternative');
 // Keep the original adversarial delta: shared Settings geometry must still win.
 // Saving a format-2 package and selecting its ID are deliberately separate.
 const skin=await page.evaluate(()=>window.amplifier.dispatch('theme.save',{name:'Existing skin',css:'#amp-one .a-overlay{padding:24px;border-radius:22px} @media(max-width:760px){#amp-one .a-settings-overlay .a-dialog.wide{width:100%;height:100dvh;border-radius:0}} #amp-one .a-dialog label{display:block;flex-direction:column} #amp-one .a-dialog{max-height:90dvh;border-radius:24px;padding:26px} #amp-one .a-dialog.wide{width:min(860px,100%)}'}));
 assert.equal(skin.accepted,true);assert.equal(skin.result.packageVersion,2);
 await updateAppearance(page,{appearanceId:skin.result.id});
 await openSettingsPage(page,'updates');
 assert.equal(await page.getByRole('button',{name:'Same-chat CLI and web',exact:true}).count(),0);
 await openSettingsPage(page,'history');
 await page.getByText(/Your CLI projects and conversations appear automatically/).waitFor();
 assert.equal(await page.getByRole('button',{name:'Browse saved conversations',exact:true}).count(),0);
 await openSettingsPage(page,'updates');
 // News remains unread until viewed, but is not an actionable Settings badge.
 const attention=(await state()).attention,releaseNotices=attention.items.filter(item=>item.id.startsWith('release-notice:'));
 assert.deepEqual(releaseNotices.map(item=>item.id),['release-notice:0.21.0:settings','release-notice:0.21.0:permissions']);
 for(const item of releaseNotices){assert.equal(item.informational,true);assert.equal(item.read,false);assert.equal(!!item.actionRequired,false)}
 assert.equal(attention.pages.updates,0,'Informational release notices do not count as actionable updates');
 await expect(page.locator('[data-settings-section=updates] .a-attention-badge')).toHaveCount(0);
 await openSettingsPage(page,'updates');
 const advanced=page.locator('[data-part=ecosystem-update-details]');if(!await advanced.evaluate(el=>el.open))await advanced.locator(':scope > summary').click();await page.locator('[data-part=important-release-changes]>summary').click();
 await page.locator('#available-updates-list > li').first().waitFor();
 assert.equal(await page.locator('#available-updates-list > li').count(),1);
 assert.match(await page.locator('#available-updates-list').innerText(),/×2/);
 assert.equal(await page.locator('#available-updates-list .a-check-result').count(),0);
 await page.getByRole('region',{name:'High-impact changes',exact:true}).waitFor();
 const noticeCount=await page.locator('.a-release-notices article').count();assert.equal(noticeCount,2);
 for(const title of ['Settings scope updated','File access updated'])await expect(page.locator('.a-release-notices').getByText(title,{exact:true})).toBeVisible();
 assert.equal(await page.locator('.a-release-entry').count(),(await state()).updates.application.releaseNotes.length);
 await page.screenshot({animations:'disabled',path:'/tmp/amplifier-release-notices-desktop.png'});
 // Viewing notices acknowledges them; their contents remain readable afterward.
 for(const notice of await page.locator('.a-release-notices article').all())await notice.scrollIntoViewIfNeeded();
 await page.waitForFunction(()=>window.amplifier.getState().attention.unread===0);
 assert.deepEqual((await state()).attention.items.filter(item=>item.id.startsWith('release-notice:')),releaseNotices.map(item=>({...item,read:true})));
 assert.equal(await page.locator('[data-settings-section=updates] .a-attention-badge').count(),0);
 assert.equal(await page.locator('#available-updates-list > li').count(),1);
 assert.equal(await page.locator('.a-release-notices article').count(),noticeCount);
 assert.equal(await page.locator('.a-release-entry').count(),(await state()).updates.application.releaseNotes.length);
 assert.equal(await page.locator('.a-release-reviewed').count(),noticeCount);
 await page.reload();
 await openSettingsPage(page,'updates');if(!await advanced.evaluate(el=>el.open))await advanced.locator(':scope > summary').click();
 await page.getByRole('region',{name:'Changelog',exact:true}).waitFor();
 await page.getByRole('region',{name:'Changelog',exact:true}).locator('.a-update-disclosure > summary').click();
 assert.equal((await state()).attention.unread,0,'Viewed notices remain acknowledged after reload');
 assert.deepEqual((await state()).attention.items.filter(item=>item.id.startsWith('release-notice:')),releaseNotices.map(item=>({...item,read:true})));
 await page.locator('.a-release-entry summary').filter({hasText:'0.11.0'}).click();
 await page.locator('.a-release-entry').filter({hasText:'0.11.0'}).getByText('Settings now apply across Amplifier apps',{exact:true}).waitFor();
 await page.setViewportSize({width:680,height:844});
 await expect(page.locator('.a-settings-experience')).toHaveAttribute('data-compact','true');
 const floating=await page.locator('.a-dialog').boundingBox();assert.equal(floating.x,0);assert.equal(floating.y,0);assert.equal(floating.width,680);

 await page.getByText('Update preferences',{exact:true}).click();
 const choiceGeometry=()=>page.locator('.a-update-options label:has(>input[type=checkbox])').evaluate(label=>{
  const input=label.querySelector('input'),a=input.getBoundingClientRect(),b=label.getBoundingClientRect();
  const text=[...label.childNodes].find(node=>node.nodeType===Node.TEXT_NODE&&node.textContent.trim());
  if(!text)throw Error('Update checkbox must retain its visible label text');
  const range=document.createRange();range.selectNodeContents(text);
  const lines=[...range.getClientRects()].filter(rect=>rect.width&&rect.height),first=lines[0];
  if(!first)throw Error('Update checkbox label has no rendered first line');
  return {display:getComputedStyle(label).display,direction:getComputedStyle(label).flexDirection,
   width:a.width,height:a.height,hitHeight:b.height,before:a.right<=first.left,
   delta:Math.abs(a.top+a.height/2-first.top-first.height/2),
   lineCount:lines.length,
   textFits:lines.every(rect=>rect.left>=b.left&&rect.right<=b.right+1&&rect.top>=b.top&&rect.bottom<=b.bottom+1),
   labelFits:label.scrollWidth<=label.clientWidth};
 });
 const assertChoice=row=>{
  assert.equal(row.display,'flex');assert.equal(row.direction,'row');
  assert.equal(row.width,18);assert.equal(row.height,18);assert.ok(row.hitHeight>=48);
  assert.equal(row.before,true);assert.ok(row.delta<3);assert.equal(row.textFits,true);assert.equal(row.labelFits,true);
 };
 // Reference aligns the input with the first line, not the middle of the
 // enlarged compact label's 48px hit area. Retain the original 3px bound.
 const aligned=await choiceGeometry();assertChoice(aligned);
 for(const patch of [{direction:'column'},{width:48},{before:false},{delta:3},{textFits:false},{hitHeight:18}])
  assert.throws(()=>assertChoice({...aligned,...patch}));
 const field=await page.locator('#update-frequency').evaluate(select=>{
  const label=document.querySelector('label[for="update-frequency"]'),a=label.getBoundingClientRect(),b=select.getBoundingClientRect();
  return {labelDisplay:getComputedStyle(label).display,choiceChildren:label.querySelectorAll('input[type=checkbox],input[type=radio]').length,
   separate:select.parentElement===label.parentElement&&label.nextElementSibling===select,
   below:b.top>=a.bottom,width:b.width,height:b.height};
 });
 assert.equal(field.labelDisplay,'block');assert.equal(field.choiceChildren,0);assert.equal(field.separate,true);
 assert.equal(field.below,true);assert.ok(field.width>18&&field.height>18,'A separate label/select is not a native choice row');
 await page.setViewportSize({width:390,height:844});
 const rect=await page.locator('.a-dialog').boundingBox();assert.equal(rect.x,0);assert.equal(rect.y,0);assert.equal(rect.width,390);assert.equal(rect.height,844);
 assert.equal(await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth),true);
 assertChoice(await choiceGeometry());
 // Exercise actual wrapping rather than inferring it from a narrow viewport.
 // Only bound available width; the native control, text and layout rules remain.
 const choice=page.locator('.a-update-options label:has(>input[type=checkbox])');
 const priorMaxWidth=await choice.evaluate(label=>{const prior=label.style.maxWidth;label.style.maxWidth='180px';return prior});
 try{
  const wrapped=await choiceGeometry();assert.ok(wrapped.lineCount>1,'The wrapping control must render multiple text lines');
  assertChoice(wrapped);console.log(JSON.stringify({updateChoiceGeometry:{at680:aligned,wrappedAt390:wrapped},assertionNegativeControls:6}));
 }finally{await choice.evaluate((label,value)=>{label.style.maxWidth=value},priorMaxWidth)}
 await page.getByRole('region',{name:'Changelog',exact:true}).scrollIntoViewIfNeeded();
 await page.screenshot({animations:'disabled',path:'/tmp/amplifier-changelog-mobile.png'});
 await page.screenshot({animations:'disabled',path:'/tmp/amplifier-settings-mobile.png'});

 if(!await advanced.evaluate(el=>el.open))await advanced.locator(':scope > summary').click();await page.getByRole('button',{name:'Show all 87 sources',exact:true}).click();
 await page.locator('#filter-update-sources').fill('*42*');
 assert.equal(await page.locator('#update-sources-list > li').count(),1);
 assert.match(await page.locator('#update-sources-list > li').innerText(),/fixture-source-42/);

 await openSettingsPage(page,'updates');
 await openSettingsPage(page,'permissions');
 await expect(page.locator('.a-settings-mobile-actions').getByRole('button',{name:'Save permissions',exact:true})).toBeVisible();
 assert.equal(await page.getByText(/Provider keys live in the app/).count(),0);
 await openSettingsPage(page,'app-bundles');
 await openChatAdvancedPage(page,'Model & tools');
 await page.getByRole('dialog',{name:'Model & tools',exact:true}).locator('summary').filter({hasText:/^Conversation modules$/}).click();
 // Details are passive; explicitly inspect the conversation before editing loaded modules.
 await page.getByRole('dialog',{name:'Model & tools',exact:true}).getByRole('button',{name:'Load configuration',exact:true}).click();
 await page.locator('#filter-loaded-modules').fill('tool-*');
 assert.equal(await page.locator('.a-module-settings .a-collection-row').count(),1);
 // Collection rows own a stable module key. The module label and section
 // remain separate text nodes; their accessible whitespace depends on layout.
 const filesystemRow=page.getByRole('dialog',{name:'Model & tools',exact:true})
  .locator('.a-module-settings [data-collection-id="tools:0"]');
 const filesystemButton=filesystemRow.locator(':scope > button');
 await expect(filesystemButton.locator('strong')).toHaveText('tool-filesystem');
 await expect(filesystemButton.locator('small')).toHaveText('tools');
 await expect(filesystemButton).toHaveAccessibleName(/^tool-filesystem\s*tools$/);
 await filesystemButton.click();
 await expect(page.locator('.a-module-settings .a-collection-detail > h4')).toHaveText('tool-filesystem');
 await page.locator('#module-field-max_bytes').fill('4096');
 assert.ok((await page.locator('#module-field-max_bytes').boundingBox()).width>18,'Numeric fields never inherit choice-input width');
 await page.getByRole('button',{name:'Apply to loaded session',exact:true}).click();
 await page.getByText('Changes are applied to the loaded session.',{exact:true}).waitFor();
 assert.equal((await state()).sessions[0].configuration.plan.tools[0].config.max_bytes,4096);
 await expect(page.getByRole('checkbox',{name:'Enable tool-filesystem',exact:true})).toBeVisible();
 await page.getByRole('checkbox',{name:'Enable tool-filesystem',exact:true}).uncheck();
 await page.getByRole('button',{name:'Apply to loaded session',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().sessions[0].configuration.plan.tools[0].enabled===false);
 await page.screenshot({animations:'disabled',path:'/tmp/amplifier-settings-modules.png'});

 const modulesDialog=page.getByRole('dialog',{name:'Model & tools',exact:true});await modulesDialog.getByRole('button',{name:'Close panel',exact:true}).click();await expect(modulesDialog).toBeHidden();
 await openSettingsPage(page,'app-bundles');
 await openSettingsPage(page,'registries');
 const iconInline=await page.getByRole('button',{name:'Add module',exact:true}).evaluate(button=>{const icon=button.querySelector('svg').getBoundingClientRect(),range=document.createRange();range.selectNodeContents(button.lastChild);const label=range.getBoundingClientRect();return {direction:getComputedStyle(button).flexDirection,delta:Math.abs(icon.y+icon.height/2-label.y-label.height/2)}});
 assert.equal(iconInline.direction,'row');assert.ok(iconInline.delta<4);
 await openSettingsPage(page,'app-bundles');
 await openSettingsPage(page,'add-bundles');
 await page.locator('#bundle-source').fill('https://github.com/example/bundles');
 assert.ok((await page.locator('#bundle-source').boundingBox()).width>18,'Text fields never inherit choice-input width');
 await page.getByRole('button',{name:'Browse',exact:true}).click();
 await page.locator('.a-candidate').first().waitFor();
 const radioInline=await page.locator('.a-candidate').first().evaluate(label=>{const radio=label.querySelector('input').getBoundingClientRect(),text=label.querySelector('strong').getBoundingClientRect();return {direction:getComputedStyle(label).flexDirection,width:radio.width,before:radio.right<=text.left,delta:Math.abs(radio.y-text.y)}});
 assert.equal(radioInline.direction,'row');assert.equal(radioInline.width,18);assert.ok(radioInline.before);assert.ok(radioInline.delta<8);
 await page.locator('#filter-bundle-discovery').fill('*dev*');
 assert.equal(await page.locator('.a-candidate').count(),1);
 await page.locator('.a-candidate input').check();
 await openSettingsPage(page,'app-bundles');
 await openSettingsPage(page,'add-bundles');
 assert.equal(await page.locator('#filter-bundle-discovery').inputValue(),'*dev*');
 assert.ok(await page.locator('.a-candidate input').isChecked());
 await page.screenshot({animations:'disabled',path:'/tmp/amplifier-settings-focused.png'});
 assert.equal(await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth),true);
 await page.getByRole('button',{name:'Close settings',exact:true}).click();
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 const previousText=await composer.innerText();
 const previousChat=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 let draftRequested;const requested=new Promise(resolve=>{draftRequested=resolve}),held=new Promise(resolve=>{releaseDraft=resolve});
 const mutations=[];
 const holdDraft=async route=>{const request=route.request().postDataJSON();if(request.action==='session.draft'){draftRequested();await held}else if(['conversation.configure','view.update'].includes(request.action))mutations.push(request);await route.continue()};
 await page.route('**/api/actions',holdDraft);
 await page.getByRole('button',{name:'New chat',exact:true}).click();
 await requested;
 const options=page.locator('.a-composer').getByRole('button',{name:'Model and reasoning settings',exact:true});
 await expect(options).toBeDisabled();await expect(composer).toHaveAttribute('aria-readonly','true');
 // Real input events while the destination is pending cannot mutate the old
 // chat or create an optimistic edit that the draft receipt would discard.
 await options.click({force:true});await composer.press('x');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),previousChat);
 assert.equal(await composer.innerText(),previousText);
 await expect(composerOptionsPanel(page)).toHaveCount(0);
 assert.equal(mutations.some(row=>row.action==='conversation.configure'||Object.hasOwn(row.args?.patch||{},'draft')),false);
 releaseDraft();await page.waitForFunction(()=>window.amplifier.getState().selectedSessionId===null&&!window.amplifier.getState().draftNavigationPending);
 await page.unroute('**/api/actions',holdDraft);
 await expect(composer).toHaveAttribute('aria-readonly','false');
 const draftSaved=page.waitForResponse(response=>{if(response.request().method()!=='POST'||new URL(response.url()).pathname!=='/api/actions')return false;const request=response.request().postDataJSON();return request?.action==='view.update'&&request.args?.patch?.draft==='Keep the new chat draft'&&response.ok()});
 await composer.fill('Keep the new chat draft');await draftSaved;
 // The same fence also protects an already editable, unsent draft. Its text
 // remains intact while reopening it; no keystroke is accepted and discarded.
 let returning;const returnRequested=new Promise(resolve=>{returning=resolve}),returnHeld=new Promise(resolve=>{releaseDraft=resolve});
 const holdReturn=async route=>{if(route.request().postDataJSON().action==='session.draft'){returning();await returnHeld}await route.continue()};
 await page.route('**/api/actions',holdReturn);
 const openNavigation=page.getByRole('button',{name:'Open navigation',exact:true});if(await openNavigation.isVisible())await openNavigation.click();
 await page.getByRole('button',{name:'New chat',exact:true}).click();await returnRequested;
 await expect(options).toBeDisabled();await expect(composer).toHaveAttribute('aria-readonly','true');await composer.press('x');
 assert.equal(await composer.innerText(),'Keep the new chat draft');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),'Keep the new chat draft');
 releaseDraft();await page.waitForFunction(()=>!window.amplifier.getState().draftNavigationPending);await page.unroute('**/api/actions',holdReturn);
 await expect(composer).toHaveAttribute('aria-readonly','false');assert.equal(await composer.innerText(),'Keep the new chat draft');
 await page.locator('.a-composer').getByRole('button',{name:'Model and reasoning settings',exact:true}).click();
 await showComposerOption(page,'bundle');
 await page.getByLabel('Registered root bundles',{exact:true}).selectOption('fixture-root');
 await page.getByRole('button',{name:'Close chat options',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().view.newSessionDraft?.bundle==='fixture-root');
 await page.getByRole('button',{name:'Choose workspace',exact:true}).click();
 await page.getByRole('button',{name:'Use existing folder',exact:true}).click();
 const attach=page.getByRole('form',{name:'Use existing folder',exact:true});
 await attach.getByRole('button',{name:'Browse',exact:true}).click();
 const workspace=(await state()).settings.workspace;
 await attach.getByRole('textbox',{name:'Folder path',exact:true}).fill(workspace+'/missing');
 await page.getByRole('button',{name:'Go',exact:true}).click();
 await page.getByText('This folder does not exist. Enter a different location.',{exact:true}).first().waitFor();
 await attach.getByRole('textbox',{name:'Folder path',exact:true}).fill(workspace+'/project');
 await attach.getByRole('textbox',{name:'Folder path',exact:true}).press('Enter');
 await page.waitForFunction(path=>window.amplifier.getState().locationListing.path===path,workspace+'/project');

 await page.getByRole('button',{name:'Use this folder',exact:true}).click();
 assert.match(await attach.getByRole('textbox',{name:/Folder on/}).inputValue(),/\/workspace\/project$/);
 await attach.getByRole('button',{name:'Use folder',exact:true}).click();
 await expect(attach).toHaveCount(0);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.newSessionDraft.workspace)).toBe(workspace+'/project');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.newSessionDraft.bundle),'fixture-root','attaching a browsed folder preserves the selected bundle');
 assert.equal(await composer.innerText(),'Keep the new chat draft');
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'appearance'}}));
 await openSettingsPage(page,'appearance');
 const appearanceAdvanced=page.locator('.a-appearance-more');
 if(!await appearanceAdvanced.evaluate(el=>el.open))await appearanceAdvanced.locator(':scope > summary').click();
 const packageBase64=execFileSync(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-c',String.raw`
import base64,io,json,zipfile
out=io.BytesIO()
with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as archive:
 archive.writestr('appearance.json',json.dumps({'type':'appearance','formatVersion':2,'base':'reference-v1','name':'dropped','stylesheet':'delta.css'}))
 archive.writestr('delta.css','#amp-one { --a-accent: purple; }')
print(base64.b64encode(out.getvalue()).decode())
`],{encoding:'utf8'}).trim();
 const beforeDrop=await settledAppearance(page);
 const data=await page.evaluateHandle(base64=>{const transfer=new DataTransfer();transfer.items.add(new File([Uint8Array.from(atob(base64),c=>c.charCodeAt(0))],'dropped.zip',{type:'application/zip'}));return transfer},packageBase64);
 await page.locator('.a-file-drop').dispatchEvent('drop',{dataTransfer:data});
 await expect(page.getByRole('button',{name:'Use dropped',exact:true})).toBeVisible();
 assert.deepEqual((await readAppearance(page)).effective,beforeDrop.effective,'Package drop does not select it');
 await page.getByRole('button',{name:'Edit dropped',exact:true}).click();
 await page.getByRole('button',{name:'Preview draft',exact:true}).click();
 await expect(page.locator('.a-appearance-preview')).toContainText('Previewing dropped');
 assert.deepEqual((await settledAppearance(page)).effective,beforeDrop.effective);
 await page.locator('.a-appearance-preview').getByRole('button',{name:'Cancel preview',exact:true}).click();
 await expect.poll(async()=>(await readAppearance(page)).preview).toBe(null);
 assert.deepEqual((await settledAppearance(page)).theme,beforeDrop.theme);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({attentionTrail:true,informationalNoticesDoNotBadge:true,noticeReviewPersists:true,compactGroupedUpdates:true,narrowerModalBreakpoint:true,typedFolderPath:true,registeredBundlePicker:true,actionSpacing:true,focusedPages:true,wildcardFilters:true,filterRetainsSelection:true,inlineBundleRadio:true,inlineAddModule:true,singleModelSelector:true,providerResult:true,providerDragOrder:true,activeRoutingEdit:true,inlineCheckbox:true,mobileFullscreen:true,noHorizontalOverflow:true,liveModuleConfig:true,liveModuleToggle:true,folderBrowser:true,fileDrop:true,browserErrors:errors.length}));
}catch(error){
 failure=error;
 console.error(error);
 try{await page.screenshot({animations:'disabled',path:'/tmp/amplifier-settings-failure.png'})}catch(captureError){console.error('Failure screenshot unavailable:',captureError.message)}
 try{const view=await page.evaluate(()=>window.amplifier?.getState?.());console.error(JSON.stringify({pageErrors:errors,locationListing:view?.locationListing,locationPicker:view?.view?.locationPicker}))}catch(diagnosticError){console.error('Settings diagnostics unavailable:',diagnosticError.message)}
 throw error;
}finally{releaseDraft?.();await finishFixture({label:"Settings fixture",close:[["Settings browser",()=>browser?.close()]],stop,originalError:failure})}
