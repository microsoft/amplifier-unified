// Actual bundled application; synthetic host/runtime. No personal data or provider.
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {clearComposer} from './composer-test-helpers.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/compact-composer';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'Compact composer fixture',timeoutMs:5000});
let browser,page,primaryError;
const evidence={geometry:[],coarseGeometry:[],negativeControl:null,negativeControls:[],limits:['Synthetic runtime; no live provider or physical keyboard/audio','Multi-quote implementation is independently owned; Markdown quotes exercise the existing editor, not immutable-node provenance','Pointer media is actual Chromium context emulation, not physical touch-device acceptance','Plain editor bounds follow resizeComposer inline 44/180; compact CSS 32/120 alone is not the effective mounted bound']};
try{
 const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 await mkdir(output,{recursive:true});browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1000,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[],sends=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/actions'&&r.postDataJSON()?.action==='conversation.send')sends.push(r.postDataJSON());});
 await page.goto(url);await showFixtureConversation(page,{navPinned:false});
 const input=page.getByRole('textbox',{name:'Message Amplifier'}),surface=page.locator('[data-part="composer-surface"]'),primary=page.locator('[data-part="composer-primary-action"]'),options=page.getByRole('button',{name:'Model and reasoning settings',exact:true});
 await input.waitFor();
 await expect(page.getByRole('button',{name:'Return to reading position',exact:true})).toHaveCount(0);
 if(process.env.AMPLIFIER_TEST_PACKAGE_ROOT){
  const identity=await(await page.request.get(url+'/fixture')).json();
  assert.equal(identity.installedPackageVerified,true,'Actual fixture must import the installed wheel');
  const assets=await page.locator('script[src],link[rel=stylesheet][href]').evaluateAll(nodes=>nodes.map(n=>n.src||n.href));
  assert.ok(assets.length);
  const served={};
  for(const asset of assets){
   const path=new URL(asset).pathname.slice(1);
   const response=await page.request.get(asset);assert.equal(response.status(),200);
   const hash=createHash('sha256').update(await response.body()).digest('hex');
   assert.equal(hash,identity.staticSha256[path],`Served asset differs from installed wheel: ${path}`);served[path]=hash;
  }
  evidence.installedServedAssets=served;
 }
 const presentation=async patch=>{
  const receipt=await page.evaluate(async patch=>{
   const shell=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;
   const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:shell.revision,composition:{...shell.effectiveComposition,presentation:{...shell.effectiveComposition.presentation,...patch}}});
   assertAccepted(prepared);
   const applied=await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:shell.revision,changeId:prepared.result.id});
   assertAccepted(applied);
   if(applied.result?.status!=='awaiting-storage'||!applied.result.commandId)throw Error('Expected browser preference storage receipt');
   return {commandId:applied.result.commandId,baseRevision:prepared.result.baseRevision};
   function assertAccepted(r){if(!r.accepted)throw Error(r.error||'Presentation refused');}
  },patch);
  // An accepted mutation precedes the SSE/rendered shell. The next change
  // must use the acknowledged revision, even when the scheme stayed equal.
  await expect.poll(()=>page.evaluate(({commandId,baseRevision})=>{
   const shell=window.amplifier.getShellState();
   return shell?.preferenceCommand?.id===commandId&&shell.preferenceCommand.status==='stored'&&shell.revision===baseRevision+1;
  },receipt)).toBe(true);
  await expect.poll(()=>page.evaluate(patch=>{
   const actual=window.amplifier.getShellState()?.effectiveComposition?.presentation;
   return Object.fromEntries(Object.keys(patch).map(key=>[key,actual?.[key]]));
  },patch)).toEqual(patch);
 };
 const responsiveReady=target=>target.waitForFunction(()=>{
  const layout=document.querySelector('[data-part="workspace"]');
  return layout?.dataset.narrow===String(matchMedia('(max-width:900px)').matches)
   &&layout.dataset.navigationNarrow===String(matchMedia('(max-width:640px)').matches);
 });
 const measure=async(target=surface)=>target.evaluate(el=>{
  const rect=node=>{const {x,y,width,height}=node.getBoundingClientRect();return {x,y,width,height}};
  const input=el.querySelector('[role=textbox]'),style=getComputedStyle(el),form=el.closest('form'),help=document.getElementById(input.getAttribute('aria-describedby'));
  const conversation=el.closest('[data-part=conversation]'),conversationStyle=getComputedStyle(conversation);
  const button=node=>{const box=rect(node);return {...box,name:node.getAttribute('aria-label')||node.textContent.trim(),
   hit:node.contains(document.elementFromPoint(box.x+box.width/2,box.y+box.height/2)),
   kind:node.matches('.a-model-trigger')?'settings':'action'}};
  return {surface:rect(el),input:rect(input),writing:rect(el.querySelector('.a-compose-writing')),
   add:rect(el.querySelector('.a-compose-add')),end:rect(el.querySelector('.a-compose-end')),
   options:rect(el.querySelector('.a-model-trigger')),configuration:rect(el.querySelector('.a-compose-configuration')),
   configurationInside:el.contains(form.querySelector('.a-compose-configuration')),form:rect(form),
   conversationWidth:conversation.getBoundingClientRect().width-parseFloat(conversationStyle.borderLeftWidth)-parseFloat(conversationStyle.borderRightWidth),
   fine:matchMedia('(pointer:fine)').matches,coarse:matchMedia('(pointer:coarse)').matches,
   viewport:{width:innerWidth,height:innerHeight},inputMin:getComputedStyle(input).minHeight,inputMax:getComputedStyle(input).maxHeight,
   help:help?{...rect(help),text:help.textContent,inside:el.contains(help),visible:help.checkVisibility(),
    display:getComputedStyle(help).display,visibility:getComputedStyle(help).visibility,
    position:getComputedStyle(help).position,clip:getComputedStyle(help).clip,clipPath:getComputedStyle(help).clipPath,hidden:!!help.closest('[hidden],[aria-hidden=true]')}:null,
   border:style.borderTopWidth,borderStyle:style.borderTopStyle,background:style.backgroundColor,
   overflow:el.scrollWidth>el.clientWidth+1,pageOverflow:document.documentElement.scrollWidth>innerWidth+1,
   buttons:[...el.querySelectorAll('button')].filter(b=>b.getClientRects().length).map(button)};
 });
 const near=(actual,expected,label)=>assert.ok(Math.abs(actual-expected)<=.5,`${label}: ${actual} vs ${expected}`);
 const checkRows=(m,{idle=false}={})=>{
  assert.notEqual(m.fine,m.coarse,'The fixture has one primary pointer class');
  const hit=m.fine?32:44,narrow=m.conversationWidth<=360,padX=narrow?5:6,padY=narrow?5:3,gapY=narrow?2:0;
  const left=m.surface.x+1+padX,right=m.surface.x+m.surface.width-1-padX;
  near(m.writing.x,left,'Writing row starts at the content edge');
  near(m.writing.width,right-left,'Writing row spans full width');
  near(m.input.x,m.writing.x,'Editor uses the writing row');
  near(m.input.width,m.writing.width,'Editor spans the full writing width');
  near(m.input.y,m.writing.y,'Editor starts in the writing row');
  assert.ok(m.input.y+m.input.height<=m.writing.y+m.writing.height+.5,'Editor stays inside its writing row');
  near(m.writing.y,m.surface.y+1+padY,'No blank area above writing');
  if(idle){near(m.input.height,44,'Mounted idle editor has its effective 44px minimum');near(m.writing.height,m.input.height,'No blank writing-row spacer')}
  assert.equal(m.configurationInside,true,'Settings belong inside the composer surface');
  const actionTop=m.writing.y+m.writing.height+gapY;
  for(const [name,box] of [['Add',m.add],['Settings',m.configuration],['End',m.end]]){
   near(box.y,actionTop,name+' belongs below writing');
   near(box.height,hit,name+' row has native pointer height');
  }
  near(m.add.x,left,'Add starts the action row');
  near(m.configuration.x,m.add.x+m.add.width+2,'Settings follows Add');
  near(m.end.x,m.configuration.x+m.configuration.width+2,'End follows Settings');
  near(m.end.x+m.end.width,right,'Actions end at the content edge');
  near(m.surface.height,m.writing.height+gapY+hit+2*(1+padY),'Surface contains exactly two rows, padding and border');
  near(m.form.y+m.form.height,m.surface.y+m.surface.height,'Accessible hint adds no blank row below the surface');
  assert.equal(m.border,'1px');assert.equal(m.borderStyle,'solid');
  assert.equal(m.overflow,false,'No composer overflow');assert.equal(m.pageOverflow,false,'No page overflow');
  assert.equal(m.buttons.length,4,'Idle fixture has Add, model, dictation and primary controls');
  assert.deepEqual(m.buttons.slice(0,3).map(box=>box.name),['Add attachments','Model and reasoning settings','Dictate message']);
  assert.ok(['Start voice call','Set up voice','Send message'].includes(m.buttons[3].name),'Primary action has its current accessible meaning');
  for(const box of m.buttons){
   assert.ok(box.width>=hit&&box.height>=hit,'Native pointer hit area');
   if(box.kind==='action'){near(box.width,hit,'Icon action width');near(box.height,hit,'Icon action height')}
   near(box.y+box.height/2,actionTop+hit/2,'Actions share the second-row center');
   assert.ok(box.x>=left&&box.x+box.width<=right+.5,'Action stays in the surface');
   assert.equal(box.hit,true,'Action center is an actual hit target');
  }
  for(let i=1;i<m.buttons.length;i++)assert.ok(m.buttons[i].x>=m.buttons[i-1].x+m.buttons[i-1].width,'Actions do not overlap');
 };
 const checkIdle=m=>{
  checkRows(m,{idle:true});
  assert.ok(m.help?.text.trim(),'Effective Return description remains associated');assert.equal(m.help.hidden,false);assert.equal(m.help.inside,false);
  assert.notEqual(m.help.display,'none');assert.notEqual(m.help.visibility,'hidden');
  assert.equal(m.help.position,'absolute');assert.equal(m.help.clip,'rect(0px, 0px, 0px, 0px)');assert.equal(m.help.clipPath,'inset(50%)');
  assert.ok(m.help.width<=1&&m.help.height<=1,'User-requested visible hint is absent');
 };
 for(const width of [900,520,390,320]){
  await page.setViewportSize({width,height:900});await responsiveReady(page);await clearComposer(input);
  for(const scheme of ['light','dark'])for(const interfaceDetail of ['minimal','standard','detailed']){
   await presentation({scheme,interfaceDetail,executionDetail:interfaceDetail});await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);await expect(surface).toBeVisible();
   const m=await measure();assert.equal(m.fine,true,'Narrow viewport does not imply coarse input');
   evidence.geometry.push({width,scheme,interfaceDetail,...m});checkIdle(m);
  }
  await input.fill('Ready');await expect(primary).toHaveAccessibleName('Send message');await expect(input).toHaveDraft('Ready');await clearComposer(input);
  await page.screenshot({path:`${output}/compact-empty-${width}.png`});
 }
 // A deliberate return to the old tall framed input must fail the SAME user-boundary assertion.
 const previous=await surface.getAttribute('style');
 try{
  await surface.evaluate(el=>el.style.minHeight='160px');
  const broken=await measure();assert.ok(broken.surface.height>=160);assert.throws(()=>checkIdle(broken));
  evidence.negativeControl={rejected:true,observed:broken};
 }finally{await surface.evaluate((el,value)=>value===null?el.removeAttribute('style'):el.setAttribute('style',value),previous)}
 checkIdle(await measure());
 const negative=async(name,target,style,pattern)=>{
  const saved=await target.getAttribute('style');
  try{
   await target.evaluate((node,style)=>{for(const [key,value] of Object.entries(style))node.style.setProperty(key,value,'important')},style);
   const measured=await measure();assert.throws(()=>checkIdle(measured),pattern,name);
   evidence.negativeControls.push({name,rejected:true,observed:measured});
  }finally{await target.evaluate((node,value)=>value===null?node.removeAttribute('style'):node.setAttribute('style',value),saved)}
  checkIdle(await measure());
 };
 await negative('settings overlaps writing',surface.locator('.a-compose-configuration'),{'grid-area':'writing'},/belongs below writing|Writing row|spacer|row has native/);
 await negative('blank spacer between rows',surface,{'row-gap':'20px'},/belongs below writing/);
 await negative('writing loses half its width',input,{width:'50%'},/full writing width/);
 await negative('editor overlaps actions vertically',input,{transform:'translateY(8px)'},/Editor starts in the writing row/);
 await negative('action hit area is too small',surface.locator('.a-compose-add>button'),{width:'24px','min-width':'24px',height:'24px','min-height':'24px'},/Add belongs below writing|native pointer height|Native pointer hit area/);
 await negative('editor overflows its surface',input,{'min-width':'1000px'},/full writing width|overflow/);
 const cover=await primary.evaluateHandle(button=>{
  const r=button.getBoundingClientRect(),node=document.createElement('div');
  Object.assign(node.style,{position:'fixed',left:r.x+'px',top:r.y+'px',width:r.width+'px',height:r.height+'px',zIndex:'2147483647',pointerEvents:'auto'});
  document.body.append(node);return node;
 });
 try{const m=await measure();assert.throws(()=>checkIdle(m),/actual hit target/);evidence.negativeControls.push({name:'primary action occluded',rejected:true,observed:m})}
 finally{await cover.evaluate(node=>node.remove());await cover.dispose()}
 checkIdle(await measure());
 const coarseContext=await browser.newContext({viewport:{width:390,height:900},hasTouch:true,isMobile:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 try{
  const touch=await coarseContext.newPage();touch.on('pageerror',e=>errors.push(e.message));
  touch.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/actions'&&r.postDataJSON()?.action==='conversation.send')sends.push(r.postDataJSON())});
  await touch.goto(url);await showFixtureConversation(touch,{navPinned:false});
  const touchSurface=touch.locator('[data-part="composer-surface"]');
  await expect(touchSurface).toBeVisible();
  for(const width of [900,520,390,320]){
   await touch.setViewportSize({width,height:900});await responsiveReady(touch);
   const m=await measure(touchSurface);assert.equal(m.coarse,true,'Touch context really selects coarse pointer CSS');
   evidence.coarseGeometry.push({width,...m});checkIdle(m);
  }
 }finally{await coarseContext.close()}
 await input.fill('Long draft. '.repeat(150));await expect(surface).toHaveAttribute('data-expanded','true');await expect(primary).toBeInViewport();
 const long=await measure();checkRows(long);
 // resizeComposer owns inline 44/180 for ordinary Markdown. Its mounted value
 // overrides compact CSS's 32/min(20dvh,120px); do not assert the unloaded rule.
 assert.equal(long.inputMin,'44px');assert.equal(long.inputMax,'180px');
 assert.ok(long.input.height<=180.5,'Long ordinary draft respects its effective mounted bound');
 evidence.longDraft=long;
 await input.evaluate(el=>{el.scrollTop=el.scrollHeight});await page.screenshot({path:output+'/compact-long-320.png'});
 const retained=await input.innerText();await options.click();const menu=page.getByRole('dialog',{name:'Model for this chat',exact:true});await expect(menu).toBeVisible();await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);await expect(options).toBeFocused();assert.equal(await input.innerText(),retained);
 await clearComposer(input);await input.fill('Do not send during composition');
 await input.evaluate(el=>{el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,keyCode:229,bubbles:true,cancelable:true}));el.dispatchEvent(new InputEvent('beforeinput',{inputType:'insertParagraph',isComposing:true,bubbles:true,cancelable:true}));});assert.equal(sends.length,0);await expect(input).toHaveDraft('Do not send during composition');
 await input.press('Shift+Enter');await page.keyboard.type('Second line');assert.equal(sends.length,0);
 await expect(input).toHaveDraft('Do not send during composition\n\nSecond line');
 await page.getByLabel('Attach files',{exact:true}).setInputFiles([{name:'notes.txt',mimeType:'text/plain',buffer:Buffer.from('Synthetic notes')},{name:'budget.csv',mimeType:'text/csv',buffer:Buffer.from('item,cost\nroom,20')}]);await page.getByRole('button',{name:'Remove notes.txt'}).waitFor();await expect(surface).toHaveAttribute('data-expanded','true');assert.equal((await measure()).overflow,false);
 const observed=await page.evaluate(()=>window.amplifier.getState().renderedView);
 assert.ok(!JSON.stringify(observed).includes('Do not send during composition'));
 assert.ok(!JSON.stringify(observed).includes('notes.txt'),'Ordinary rendered readback excludes attachment draft names');
 assert.ok(!JSON.stringify(observed).includes('data-expanded'),'Layout-only attributes are not projected');
 await page.getByRole('button',{name:'Remove budget.csv'}).click();await expect(input).toHaveDraft('Do not send during composition\n\nSecond line');
 await presentation({scheme:'light',interfaceDetail:'minimal'});await expect(page.getByRole('button',{name:'Remove notes.txt'})).toBeVisible();await page.getByRole('button',{name:'Remove notes.txt'}).click();
 await page.getByLabel('Attach files',{exact:true}).setInputFiles({name:'sample.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/ldcAAAAASUVORK5CYII=','base64')});
 await page.getByRole('button',{name:'Remove sample.png'}).waitFor();await input.fill('Caption for the synthetic image');await expect(primary).toBeEnabled();
 await page.screenshot({path:output+'/compact-image-caption.png'});await page.getByRole('button',{name:'Remove sample.png'}).click();
 await clearComposer(input);
 const quoteDraft='Before\n\n> First reference\n\nBetween\n\n> Second reference\n\nAfter';
 await input.evaluate((el,text)=>{const clipboardData=new DataTransfer();clipboardData.setData('text/plain',text);el.dispatchEvent(new ClipboardEvent('paste',{clipboardData,bubbles:true,cancelable:true}));},quoteDraft);
 await expect(input).toHaveDraft(quoteDraft);
 await page.reload();await input.waitFor();await expect(input).toHaveDraft('Before\n\n> First reference\n\nBetween\n\n> Second reference\n\nAfter');await expect(primary).toBeInViewport();
 // Emulate the CSS viewport and pixel density of an 800x1000 display at 200%.
 // CSS zoom alone leaves vh unchanged and doubles the app height; it is not browser zoom.
 const cdp=await page.context().newCDPSession(page);
 await cdp.send('Emulation.setDeviceMetricsOverride',{width:400,height:500,deviceScaleFactor:2,mobile:false});
 await expect(primary).toBeInViewport();assert.equal((await measure()).pageOverflow,false);
 evidence.zoomReflow={kind:'CDP 2x pixel-density / half CSS viewport, not physical browser zoom',cssViewport:await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})),geometry:await measure()};
 await page.screenshot({path:output+'/compact-zoom-reflow2.png'});
 await cdp.send('Emulation.clearDeviceMetricsOverride');await cdp.detach();await page.setViewportSize({width:800,height:1000});
 await clearComposer(input);await input.fill('A retained first message');await primary.click();await page.getByText('Synthetic first response',{exact:true}).waitFor();assert.equal(sends.length,1);await expect(input).toHaveDraft('');
 assert.deepEqual(errors,[]);await writeFile(output+'/compact-composer.json',JSON.stringify(evidence,null,2));console.log('PASS compact composer: rendered geometry, modes, long text, attachments, quote-shaped Markdown, focus, composition, reload, synthetic send; limits retained.');
}catch(error){
 primaryError=error;
 evidence.failure=String(error?.stack||error);
 await writeFile(output+'/compact-composer.json',JSON.stringify(evidence,null,2));
 if(page&&!page.isClosed())await page.screenshot({path:output+'/failure.png'}).catch(()=>{});
 throw error;
}finally{
 await finishFixture({label:'Compact composer fixture',close:[['browser',()=>browser?.close()]],
  stop:async()=>{const result=await stopFixture();await writeFile(output+'/cleanup.json',JSON.stringify(result,null,2));return result},originalError:primaryError});
}
