// Run against the already-built production app. Only navigator's native geometry
// is simulated; never inject/rewrite the shipped CSS or claim OS drag acceptance.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||root+'output/window-controls-overlay';
let browser;
try{
  const url=await new Promise((resolve,reject)=>{
    let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);
    fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
    fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}});
  });
  browser=await chromium.launch({headless:true});await mkdir(out,{recursive:true});
  const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  await context.addInitScript(()=>{
    const overlay=new EventTarget();let rect={x:80,y:0,width:1200,height:32};
    overlay.visible=true;overlay.getTitlebarAreaRect=()=>rect;
    Object.defineProperty(navigator,'windowControlsOverlay',{configurable:true,value:overlay});
    window.__simulatedWco=(next,visible=true)=>{rect=next;overlay.visible=visible;overlay.dispatchEvent(new Event('geometrychange'))};
  });
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  const app=page.locator('#amp-one');
  const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
  const presentation=patch=>page.evaluate(async patch=>{
    const state=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;
    const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:state.revision,composition:{...state.effectiveComposition,presentation:{...state.effectiveComposition.presentation,...patch}}});
    await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:state.revision,changeId:prepared.result.id});
  },patch);
  const geometry=async(rect,visible=true)=>{
    await page.evaluate(([rect,visible])=>window.__simulatedWco(rect,visible),[rect,visible]);
    if(!visible){await expect(app).not.toHaveAttribute('data-window-controls-overlay','true');return}
    await expect(app).toHaveAttribute('data-window-controls-overlay','true');
    await expect.poll(()=>app.evaluate(el=>el.style.getPropertyValue('--wco-width'))).toBe(rect.width+'px');
    await expect.poll(()=>app.evaluate(el=>el.style.getPropertyValue('--wco-y'))).toBe(rect.y+'px');
    await expect.poll(()=>app.evaluate(el=>el.style.getPropertyValue('--wco-height'))).toBe(rect.height+'px');
  };
  const clearHeader=async(selector,rect)=>{
    await expect.poll(()=>page.locator(selector).evaluate((header,rect)=>{
      const safe=Math.max(48,rect.y+rect.height),stacked=document.getElementById('amp-one').dataset.windowControlsOverlayStacked==='true';
      const b=header.getBoundingClientRect();
      return stacked?b.top>=safe-1:Math.abs(b.x-rect.x)<1&&Math.abs(b.width-rect.width)<1&&Math.abs(b.y-rect.y)<1;
    },rect)).toBe(true);
    const controls=await page.locator(selector).evaluate((header,rect)=>[...header.querySelectorAll('button,a,input,select,textarea,summary,[role=button]')].filter(el=>el.getClientRects().length).map(el=>{
      const b=el.getBoundingClientRect();return {name:el.getAttribute('aria-label')||el.textContent,box:{x:b.x,y:b.y,width:b.width,right:b.right},viewport:innerWidth,drag:getComputedStyle(el).getPropertyValue('-webkit-app-region'),fits:b.left>=-1&&b.right<=innerWidth+1,safe:b.top>=Math.max(48,rect.y+rect.height)-1||(b.left>=rect.x-1&&b.right<=rect.x+rect.width+1)};
    }),rect);
    for(const control of controls){assert.equal(control.drag,'no-drag',control.name);assert.ok(control.safe,'Native controls overlap '+control.name);assert.ok(control.fits,'Control clipped outside viewport: '+JSON.stringify({selector,rect,control}))}
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Horizontal overflow');
  };
  await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
  await expect(app).toHaveAttribute('data-window-controls-overlay','true');
  const manifest=await page.evaluate(()=>fetch('/manifest.webmanifest').then(r=>r.json()));
  assert.deepEqual(manifest.display_override,['window-controls-overlay','standalone']);
  // WorkDialog is a real portal into the app, not a hand-authored fixture.
  await page.getByRole('button',{name:'Choose workspace',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'Choose a workspace'})).toBeVisible();
  assert.ok((await page.locator('.a-work-dialog-backdrop').boundingBox()).y>=48);
  await page.getByRole('button',{name:'Close Choose a workspace',exact:true}).click();
  await action('session.create');
  await action('canvas.show',{kind:'html',title:'WCO retained viewer',content:'<h1>Retained viewer</h1><input aria-label="Retained note" value="Initial">'});
  const frame=page.frameLocator('.a-canvas-html');await frame.getByRole('textbox',{name:'Retained note'}).fill('Do not remount');
  await page.evaluate(()=>{window.__retainedFrame=document.querySelector('.a-canvas-html')});
  // Sandboxed HTML has an opaque origin: inspect its identity inside the frame
  // rather than treating the parent's null contentDocument as proof.
  const documentIdentity=await frame.locator('html').evaluate(()=>{
    window.__wcoDocumentIdentity='retained-'+Date.now()+'-'+Math.random();return window.__wcoDocumentIdentity;
  });
  await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Unsent WCO draft');

  for(const width of [1280,800,390,320]){
    await page.setViewportSize({width,height:900});
    for(const rect of [{x:80,y:0,width:width-80,height:32},{x:0,y:0,width:width-140,height:32},{x:70,y:8,width:width-210,height:52}]){
      await geometry(rect);await clearHeader('.a-work-header',rect);
      await action('view.update',{patch:{canvasFocused:true}});await expect(page.locator('.a-canvas-panel')).toHaveAttribute('data-focused','true');
      await clearHeader('.a-canvas-panel[data-focused=true] .a-canvas-head',rect);
      await expect(page.locator('.a-work-header')).toBeHidden();
      const exit=page.getByRole('button',{name:'Exit canvas focus',exact:true});
      assert.ok(await exit.evaluate(el=>{const b=el.getBoundingClientRect();return el.contains(document.elementFromPoint(b.x+b.width/2,b.y+b.height/2))}),'Focused header control is occluded');
      await expect(frame.getByRole('textbox',{name:'Retained note'})).toHaveValue('Do not remount');
      assert.ok(await page.evaluate(()=>window.__retainedFrame===document.querySelector('.a-canvas-html')),'Geometry/focus remounted iframe');
      assert.equal(await frame.locator('html').evaluate(()=>window.__wcoDocumentIdentity),documentIdentity,'Geometry/focus reloaded viewer document');
      await exit.click();await expect(page.locator('.a-canvas-panel')).toHaveAttribute('data-focused','false');
    }
    await action('view.update',{patch:{panel:'settings'}});await expect(page.locator('.a-settings-experience')).toBeVisible();
    const overlay=page.locator('.a-settings-redesign-overlay');
    await expect(page.locator('.a-work-header')).toHaveAttribute('inert','');
    await expect.poll(()=>overlay.evaluate(el=>el.getBoundingClientRect().top)).toBeGreaterThanOrEqual(60);
    if(width<960){
      // Simulate keyboard viewport movement using the existing hook's variables,
      // without rewriting a single shipped stylesheet rule.
      await overlay.evaluate(el=>{el.style.setProperty('--a-settings-viewport-top','20px');el.style.setProperty('--a-settings-viewport-height','500px')});
      await expect.poll(()=>overlay.evaluate(el=>el.getBoundingClientRect().height)).toBe(460);
      await overlay.evaluate(el=>{el.style.setProperty('--a-settings-viewport-top','80px');el.style.setProperty('--a-settings-viewport-height','500px')});
      await expect.poll(()=>overlay.evaluate(el=>el.getBoundingClientRect().top)).toBe(80);
      await expect.poll(()=>overlay.evaluate(el=>el.getBoundingClientRect().height)).toBe(500);
    }
    await page.screenshot({path:out+'/settings-'+width+'.png'});
    await action('view.update',{patch:{panel:null}});
  }
  await page.setViewportSize({width:1280,height:900});
  const rect={x:80,y:0,width:1200,height:32};await geometry(rect);
  await page.getByRole('button',{name:'Chat actions',exact:true}).click();
  const menu=page.getByRole('group',{name:'Chat actions',exact:true});await expect(menu).toBeVisible();
  assert.equal(await menu.evaluate(el=>getComputedStyle(el).getPropertyValue('-webkit-app-region')),'no-drag');
  await menu.getByRole('button',{name:'Chat details',exact:true}).click();
  await expect(page.locator('.a-overlay')).toBeVisible();assert.ok((await page.locator('.a-overlay').boundingBox()).y>=48);
  await expect(page.locator('.a-work-header')).toHaveAttribute('inert','');
  await action('view.update',{patch:{panel:null}});
  const palette=await action('theme.list');
  for(const id of ['builtin:default','builtin:atelier','builtin:aurora','builtin:graphite']){
    assert.ok(palette.result.items.some(row=>row.id===id));
    const theme=(await action('theme.read',{id})).result;
    for(const scheme of ['light','dark']){
      await presentation({scheme});await action('theme.preview',{name:theme.name,css:theme.css});
      await expect(app).toHaveAttribute('data-theme-scheme',scheme);await clearHeader('.a-work-header',rect);
      await page.screenshot({path:out+'/'+id.split(':')[1]+'-'+scheme+'.png'});
      await action('theme.revert');
    }
  }
  // Persist a CSS skin that changes geometry as well as paint. Host safety rules
  // must win the former without flattening the latter, including after reload.
  const skin=(await action('theme.read',{id:'builtin:graphite'})).result.css+'\n#amp-one .a-work-header{position:relative;margin:0;width:100%;min-height:80px;background:rgb(21,37,51)!important}';
  const saved=(await action('theme.save',{name:'Saved WCO geometry skin',css:skin})).result;
  const savedTheme=(await action('theme.read',{id:saved.id})).result;
  await action('theme.apply',{name:savedTheme.name,css:savedTheme.css});await clearHeader('.a-work-header',rect);
  assert.equal(await page.locator('.a-work-header').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(21, 37, 51)');
  // Contributed interactive roles and text selections are not drag surfaces.
  const dragFacts=await page.evaluate(()=>{
    const host=document.createElement('div');host.innerHTML='<span role="switch" tabindex="0">Switch</span><span role="menuitemradio">Choice</span><span contenteditable="true">Edit</span><span draggable="true">Move</span><summary>Disclosure</summary>';
    document.querySelector('.a-work-header').append(host);
    const facts=[...host.children].map(el=>getComputedStyle(el).getPropertyValue('-webkit-app-region'));host.remove();
    return {facts,header:getComputedStyle(document.querySelector('.a-work-header')).getPropertyValue('-webkit-app-region'),title:getComputedStyle(document.querySelector('.a-work-heading strong')).userSelect};
  });
  assert.ok(dragFacts.facts.every(value=>value==='no-drag'));assert.equal(dragFacts.header,'drag');assert.equal(dragFacts.title,'text');
  await geometry(rect,false);await expect(app).not.toHaveAttribute('data-window-controls-overlay','true');
  assert.equal(await app.evaluate(el=>el.style.getPropertyValue('--wco-safe-top')),'');
  assert.equal(await page.locator('.a-work-header').evaluate(el=>getComputedStyle(el).position),'relative');
  await geometry(rect);
  await page.evaluate(()=>window.__simulatedWco({x:0,y:0,width:Infinity,height:32}));
  await expect(app).not.toHaveAttribute('data-window-controls-overlay','true');await geometry(rect);
  await page.screenshot({path:out+'/saved-skin.png'});
  await expect(frame.getByRole('textbox',{name:'Retained note'})).toHaveValue('Do not remount');
  await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveValue('Unsent WCO draft');
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.draft)).toBe('Unsent WCO draft');
  await page.reload();await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();await clearHeader('.a-work-header',rect);
  await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveValue('Unsent WCO draft');
  assert.equal(await page.locator('.a-work-header').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(21, 37, 51)');
  const ordinary=await browser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  await ordinary.goto(url);await ordinary.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
  await expect(ordinary.locator('#amp-one')).not.toHaveAttribute('data-window-controls-overlay','true');await ordinary.close();
  assert.deepEqual(errors,[]);
  console.log('SIMULATED WCO GEOMETRY passed against shipped build: left/right/nonzero-y, 320–1280px, off/malformed recovery, menus/dialogs/compact Settings, retained viewer and saved appearance reload. Native installed launch, OS controls and dragging NOT verified.');
}finally{await browser?.close();fixture.kill()}
