import {showFixtureConversation} from './browser-chat-pages.mjs';
import {readComposerDraft} from './composer-test-helpers.mjs';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {readAppearance,settledAppearance,updateAppearance,previewAppearance,cancelAppearancePreview} from './settings-fixture.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stop=ownFixture(fixture,{label:'Reference delta fixture'});
let browser,releaseShell,failure;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({colorScheme:'dark',viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 // Hold real preference writes long enough to distinguish storage from paint.
 await page.route('**/api/actions',async route=>{if(route.request().postDataJSON()?.action==='appearance.preferences.update')await new Promise(resolve=>setTimeout(resolve,500));await route.continue()});
 const action=async(name,args={})=>{const result=await page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);assert.equal(result.accepted,true);return result};
 const presentation=patch=>updateAppearance(page,patch);
 const tokens=['bg','surface','soft','ink','muted','line','accent','tint','green','danger'];
 const paletteCss=`#amp-one {${tokens.map(key=>`--a-${key}:light-dark(${key==='bg'?'#eef8f6':'#245b53'},${key==='bg'?'#102c28':'#b4e4d8'});`).join('')}}`;
 const waterCss=paletteCss+'\n#amp-one[data-theme-scheme="light"]{--a-background-art:linear-gradient(135deg, #b4e4d8, #eef8f6)}\n#amp-one[data-theme-scheme="dark"]{--a-background-art:radial-gradient(ellipse at top, #245b53, #102c28)}';
 const css=()=>page.locator('#amp-one').evaluate(el=>{const s=getComputedStyle(el);return {background:s.backgroundImage,color:s.backgroundColor,scheme:s.colorScheme}});
 await page.goto(url);await showFixtureConversation(page,{navPinned:false});await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
 await action('session.create');
 const draft='Preserve this unsent draft\nwith eight\nsaved\nlines\nthat need\nroom\nto stay\nreadable';
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill(draft);
 // Filling a contenteditable creates paragraphs; compare the canonical Markdown
 // captured before any theme operation, while still checking every input line.
 const savedDraft=await readComposerDraft(page.getByRole('textbox',{name:'Message Amplifier'}));
 assert.deepEqual(savedDraft.split(/\n+/),draft.split('\n'));
 assert.equal(savedDraft,draft.replaceAll('\n','\n\n'));
 console.log(JSON.stringify({draftBeforeTheme:savedDraft}));
 await presentation({scheme:'light'});await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme','light');
 const before=await settledAppearance(page),beforePaint=await css(),selected=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await previewAppearance(page,{name:'Quiet water',css:waterCss});
 await expect.poll(async()=>(await css()).color).toBe('rgb(238, 248, 246)');
 const preview=await css();assert.match(preview.background,/linear-gradient/);assert.doesNotMatch(preview.background,/207, 196, 255/);
 assert.deepEqual((await readAppearance(page)).effective,before.effective);
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft(savedDraft);
 await cancelAppearancePreview(page);assert.deepEqual((await readAppearance(page)).theme,before.theme);
 await expect.poll(css).toEqual(beforePaint);
 const saved=(await action('theme.save',{name:'Quiet water',css:waterCss})).result;
 assert.equal(saved.packageVersion,2);assert.equal(saved.base,'reference-v1');
 assert.deepEqual((await readAppearance(page)).effective,before.effective,'Library save does not select');
 const applied=await updateAppearance(page,{appearanceId:saved.id});
 assert.equal(applied.theme.fingerprint,saved.fingerprint);assert.equal(applied.theme.css,waterCss);
 assert.deepEqual(await css(),preview);
 await openSettingsPage(page,'appearance');
 await page.locator('.a-appearance-more > summary').click();
 const decoration=page.getByRole('checkbox',{name:'Show background decorations',exact:true});
 const setDecoration=async enabled=>{
  await expect(decoration).toBeEnabled();
  const before=await settledAppearance(page),profile=before.activeScope==='client'?'local':'shared';
  const applied=page.waitForResponse(response=>{
   const request=response.request(),body=request.method()==='POST'?request.postDataJSON():null;
   return response.url().endsWith('/api/actions')&&body?.action==='appearance.preferences.update'&&body.args.scope===before.activeScope&&body.args.generation===before.generation&&body.args.expectedRevision===before[profile].revision&&body.args.patch.decorations===enabled;
  });
  await decoration.setChecked(enabled);
  await expect.poll(async()=>(await css()).background).toBe(enabled?preview.background:'none');
  const receipt=await applied;assert.ok(receipt.ok());assert.equal((await receipt.json()).accepted,true);
  await expect.poll(async()=>(await readAppearance(page))[profile].revision).toBe(before[profile].revision+1);
  assert.equal((await settledAppearance(page)).effective.decorations,enabled);
  await expect(decoration).toBeEnabled();
 };
 await setDecoration(false);
 await setDecoration(true);
 const confirmed=await settledAppearance(page);
 await expect.poll(()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.appearance-composition.v4'))?.compositionId)).toBe(confirmed.composition.id);
 // Keep Settings open: its focus trap must attach after shell restoration.
 // Delay shell preferences on reload: the app must not paint system-dark UI
 // while waiting for the authoritative client composition to arrive.
 let continued;const gate=new Promise(resolve=>{releaseShell=resolve});
 const continuation=new Promise(resolve=>{continued=resolve});
 await page.route('**/api/shell?*',async route=>{await gate;await route.continue();continued()});
 await page.reload({waitUntil:'domcontentloaded'});
 await expect(page.locator('.boot')).toBeVisible();await expect(page.locator('#amp-one')).toHaveCount(0);
 assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).colorScheme),'light');
 assert.equal(await page.evaluate(()=>getComputedStyle(document.body).backgroundColor),'rgb(238, 248, 246)');
 releaseShell();await continuation;await page.unrouteAll({behavior:'wait'});
 await expect(page.getByRole('dialog')).toBeVisible();
 await expect.poll(()=>page.getByRole('dialog').evaluate(el=>el.contains(document.activeElement))).toBe(true);
 await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).toHaveCount(0);
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft(savedDraft);
 await expect.poll(()=>page.getByRole('textbox',{name:'Message Amplifier'}).evaluate(el=>el.getBoundingClientRect().height)).toBeGreaterThanOrEqual(170);
 assert.deepEqual(await css(),preview);
 await presentation({scheme:'system'});await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme','dark');
 await expect.poll(async()=>(await css()).color).toBe('rgb(16, 44, 40)');
 await page.emulateMedia({colorScheme:'light'});await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme','light');
 await expect.poll(async()=>(await css()).background).toBe(preview.background);
 const flat=(await action('theme.save',{name:'Flat water',css:paletteCss})).result;
 await updateAppearance(page,{appearanceId:flat.id});
 await expect.poll(async()=>(await css()).background).toBe('none');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),selected);
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft(savedDraft);
 assert.deepEqual(errors,[]);
 console.log('Format-2 Reference delta preview/cancel/save/select, decoration toggle, system appearance, reload first-paint preference and draft preservation passed.');
}catch(error){failure=error;throw error}finally{
 releaseShell?.();
 await finishFixture({label:'Reference delta fixture',close:[['Reference delta browser',()=>browser?.close()]],stop,originalError:failure});
}
