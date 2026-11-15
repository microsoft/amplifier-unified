// Production components and CSS, synthetic state/action receipts. The owning
// Unified DTU exports actual theme.read CSS for the finite paint/case plan;
// no live host effects, guessed theme bytes, or Cartesian appearance campaign.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer,transformWithEsbuild} from 'vite';
import {chromium,expect} from '@playwright/test';
import {assertUpdateSupportGeometry} from './update-support-geometry.mjs';
import {assertConversationErrorActionAlignment} from './conversation-error-geometry.mjs';

const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS||'output/settings-action-spacing';
const appearancePath=process.env.AMPLIFIER_TEST_APPEARANCES;
assert.ok(appearancePath,'Export actual paired theme.read rows to AMPLIFIER_TEST_APPEARANCES; no guessed appearance or live-host fallback');
const appearanceBytes=await readFile(appearancePath),manifest=JSON.parse(appearanceBytes);
const rows=Array.isArray(manifest)?manifest:manifest.appearances;
assert.ok(Array.isArray(rows),'Appearance export is an array or {appearances:[{id,css}]}');
const ids=['reference','builtin:default','builtin:aurora','builtin:atelier','builtin:graphite','builtin:digital-rain','builtin:lightline','builtin:ready'];
const appearances=ids.map(id=>{
 const matches=rows.filter(row=>row.id===id);
 assert.equal(matches.length,1,'Exactly one actual exported appearance: '+id);
 const {css}=matches[0];assert.equal(typeof css,'string');
 assert.ok(id==='reference'||css.trim(),'Actual gallery CSS is nonempty: '+id);
 return {id,css,sha256:createHash('sha256').update(css).digest('hex')};
});
// Eight common desktop paint cases plus eight chosen edge combinations.
// These are representative changed-risk cases, not every possible cross-product.
const common={width:1280,scheme:'light',forced:false,dir:'ltr',density:'comfortable',enlarged:false};
const cases=[
 ...ids.map((appearance,index)=>({...common,id:'paint-'+appearance,appearance,scheme:index%2?'dark':'light',pending:appearance==='reference'})),
 {...common,id:'reference-phone',appearance:'reference',width:390,density:'compact'},
 {...common,id:'default-phone-dark',appearance:'builtin:default',width:390,scheme:'dark',density:'compact',pending:true},
 {...common,id:'reference-small-large',appearance:'reference',width:320,enlarged:true},
 {...common,id:'default-small-large',appearance:'builtin:default',width:320,scheme:'dark',density:'compact',enlarged:true},
 {...common,id:'reference-rtl-forced',appearance:'reference',width:390,dir:'rtl',forced:true,density:'compact'},
 {...common,id:'default-rtl-large',appearance:'builtin:default',width:320,dir:'rtl',enlarged:true,density:'compact'},
 {...common,id:'reference-desktop-dark-compact',appearance:'reference',scheme:'dark',density:'compact'},
 {...common,id:'default-desktop-forced',appearance:'builtin:default',forced:true,dir:'rtl'},
];
assert.equal(cases.length,16);assert.equal(new Set(cases.map(row=>row.id)).size,16);
const fixture=`
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {UpdateSupport} from '/src/update-support.jsx';
import {ConversationError} from '/src/conversation-controls.jsx';
import {ModelCapacitySettings} from '/src/model-capacity-settings.jsx';
import {StorageCleanupSettings} from '/src/storage-cleanup-settings.jsx';
import '/src/reference.css';
import '/src/updates.css';
import '/src/settings-everyday.css';
import '/src/settings-mobile.css';
import '/src/compact-composer.css';
window.calls=[];
const dispatch=async(name,args)=>{
 window.calls.push({name,args});
 if(name==='modelCapacity.read')return {accepted:true,result:{revision:1,maxWaitingCalls:256,waitTimeoutSeconds:0}};
 if(name==='storage.cleanup.read')return {accepted:true,result:{policy:{revision:1,enabled:false,keepDays:14,maxUnusedBytes:0,workspaceArchiveKeepDays:null,operationOutputKeepDays:null}}};
 if(name!=='updates.diagnostics')throw Error('Spacing fixture refuses effects: '+name);
 return new Promise(resolve=>{window.resolveDiagnostics=()=>resolve({accepted:true,result:{synthetic:true}})});
};
function Fixture(){
 const [category,setCategory]=useState('context_limit');
 window.setCategory=setCategory;
 return <>
  <section className="a-dialog a-settings-experience a-updates" data-compact={innerWidth<960} aria-label="Update actions">
   <UpdateSupport state={{updates:{}}} act={dispatch}/>
  </section>
  <section aria-label="Error actions">
   <ConversationError state={{}} session={{id:'synthetic',status:'error',failure:{category,summary:'Recorded synthetic error',guidance:'Your saved messages are retained.'}}} act={dispatch}/>
  </section>
  <section className="a-dialog a-settings-experience" data-compact={innerWidth<960} aria-label="Saved preference actions">
   <ModelCapacitySettings act={dispatch}/><StorageCleanupSettings act={dispatch}/>
  </section>
 </>;
}
createRoot(document.getElementById('fixture')).render(<Fixture/>);
`;
const metrics=[],errors=[],failures=[],completedCases=[];
let vite,browser,page;
await mkdir(artifacts,{recursive:true});
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),
  server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-runtime']},
  plugins:[{name:'settings-action-spacing',
   resolveId(id){if(id==='/settings-action-spacing.jsx')return id},
   async load(id){if(id==='/settings-action-spacing.jsx')return (await transformWithEsbuild(fixture,'settings-action-spacing.jsx',{loader:'jsx',jsx:'automatic'})).code},
   configureServer(server){server.middlewares.use((req,res,next)=>{
    if(req.url!=='/settings-action-spacing')return next();
    res.setHeader('Content-Type','text/html');
    res.end('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><main id="amp-one" class="a-chat-shell a-approachable-shell"><div id="fixture"></div></main><script type="module" src="/settings-action-spacing.jsx"></script>');
   })}
  }]});
 await vite.listen();browser=await chromium.launch({headless:true,
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),
  args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 for(const scene of cases){
  const {width,scheme,forced,dir,density,enlarged}=scene;
  const appearance=appearances.find(row=>row.id===scene.appearance),label=scene.id;
  page=await browser.newPage({viewport:{width,height:900},hasTouch:width<400,isMobile:width<400});
  page.on('pageerror',error=>errors.push(error.message));
   await page.emulateMedia({colorScheme:scheme,forcedColors:forced?'active':'none',reducedMotion:'reduce'});
   await page.goto(vite.resolvedUrls.local[0]+'settings-action-spacing');
   await expect(page.getByRole('button',{name:'Copy diagnostics',exact:true})).toBeVisible();
   // Apply actual paired appearance CSS after the production component styles,
   // as the app does. Only document/fixture stacking is synthetic geometry.
   await page.addStyleTag({content:appearance.css});
   await page.addStyleTag({content:'html,body{height:auto!important;overflow:auto!important;margin:0}#amp-one.a-chat-shell{height:auto;overflow:visible;display:block;padding:16px}#fixture{display:grid;gap:24px;max-width:800px;margin:auto}#fixture>section{min-width:0;width:100%;box-sizing:border-box}#fixture>.a-dialog{max-height:none;overflow:visible}'});
   await page.evaluate(()=>document.fonts.ready);
   await page.locator('#amp-one').evaluate((node,scheme)=>node.dataset.themeScheme=scheme,scheme);
   await page.evaluate(()=>{
    window.fixtureClipboard=[];
    Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>window.fixtureClipboard.push(text)}});
   });
   const support=page.locator('[data-part=update-support]'),banner=page.locator('[data-part=conversation-trouble]');
   const copy=support.getByRole('button',{name:'Copy diagnostics',exact:true}),save=support.getByRole('button',{name:'Save diagnostics',exact:true});
   const preferences=page.getByRole('region',{name:'Saved preference actions',exact:true});
   await expect(preferences.locator('#model-capacity-max-waiting')).toHaveValue('256');
   await preferences.locator('#model-capacity-max-waiting').fill('300');
   await preferences.getByRole('checkbox',{name:'Automatically clean up unused copies',exact:true}).check();
   await expect(preferences.getByRole('button',{name:'Discard edits and use saved settings',exact:true})).toHaveCount(2);
   const initialReads=await page.evaluate(()=>window.calls.map(call=>call.name));
   assert.deepEqual([...initialReads].sort(),['modelCapacity.read','storage.cleanup.read']);
    await page.locator('#amp-one').evaluate((node,{dir,density,enlarged})=>{
     node.dir=dir;node.dataset.density=density;node.style.setProperty('--a-text-base',enlarged?'24px':'14px');
     // Compact Settings normally requests 14px button text explicitly. This
     // enlarged-text case must actually enlarge those glyphs, not only a token
     // which that component overrides. It is a text reflow probe, not OS zoom.
     for(const button of node.querySelectorAll('[data-part=update-support] button'))button.style.fontSize=enlarged?'24px':'';
    },{dir,density,enlarged});
    const supportGeometry=await assertUpdateSupportGeometry(support,label);
    metrics.push(supportGeometry);
    if(width===320&&enlarged)assert.ok(supportGeometry.updateSupport.buttons.some(button=>button.text.length>1),'Narrow enlarged scene actually wraps a diagnostic label');
    await copy.focus();await page.keyboard.press('Tab');await expect(save).toBeFocused();
    await expect(save).toHaveCSS('outline-style','solid');
    for(const group of await preferences.locator('.a-control-actions').all()){
     const bounds=await group.boundingBox(),buttons=await group.getByRole('button').all();
     for(const button of buttons){
      const box=await button.boundingBox();
      assert.ok(box.x>=bounds.x-1&&box.x+box.width<=bounds.x+bounds.width+1,label+' saved preference action contained');
      assert.ok(await button.evaluate(node=>node.scrollWidth<=node.clientWidth+1),label+' saved preference label not clipped');
     }
     for(let index=1;index<buttons.length;index++){
      const a=await buttons[index-1].boundingBox(),b=await buttons[index].boundingBox();
      assert.ok(Math.max(b.x-a.x-a.width,a.x-b.x-b.width,b.y-a.y-a.height,a.y-b.y-b.height)>=8,label+' saved preference controls have a real gap');
     }
    }
    // Both categories retain their own meaning; native disclosure remains
    // keyboard-operable and never invokes recovery, settings or reporting.
    for(const category of ['context_limit','authentication']){
     await page.evaluate(category=>window.setCategory(category),category);
     await expect(banner.getByRole('button',{name:category==='context_limit'?'Create recovery copy':'AI connections',exact:true})).toBeVisible();
     const summary=banner.locator('summary');
     await banner.locator('details').evaluate(node=>{node.open=false});
     await page.keyboard.press('Tab');
     await summary.focus();await expect(summary).toHaveCSS('outline-style','solid');
     metrics.push(await assertConversationErrorActionAlignment(banner,label+'-'+category+'-closed',{requireAdjacentSingleLine:width===1280&&!enlarged}));
     await summary.press('Enter');await expect(banner.locator('details')).toHaveJSProperty('open',true);
     metrics.push(await assertConversationErrorActionAlignment(banner,label+'-'+category+'-open'));
     await summary.press('Space');await expect(banner.locator('details')).toHaveJSProperty('open',false);
    }
    assert.deepEqual(await page.evaluate(()=>window.calls.map(call=>call.name)),initialReads,'Geometry/disclosure/focus is passive after initial synthetic reads');
   // A synthetic delayed diagnostic receipt verifies disabled geometry without
   // touching a real host or triggering Save's download. Unit tests own payload.
   if(scene.pending){
   await copy.press('Enter');
   await expect(copy).toBeDisabled();await expect(save).toBeDisabled();
   metrics.push(await assertUpdateSupportGeometry(support,label+'-pending'));
   await page.evaluate(()=>window.resolveDiagnostics());
   await expect(copy).toBeEnabled();await expect(support.getByRole('status')).toContainText('Diagnostics copied.');
   assert.deepEqual(await page.evaluate(()=>window.calls.map(call=>call.name)),[...initialReads,'updates.diagnostics']);
   assert.equal(await page.evaluate(()=>window.fixtureClipboard.length),1);
   }
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal page overflow');
   completedCases.push(scene.id);
  await page.close();page=null;
 }
 assert.deepEqual(errors,[]);
 console.log('PASS: actual paired appearance paints retain diagnostic button clearance and native error disclosure alignment.');
}catch(error){
 failures.push(String(error.stack||error));throw error;
}finally{
 await writeFile(join(artifacts,'result.json'),JSON.stringify({passed:failures.length===0,
  scope:'Production components with actual paired appearance CSS; synthetic receipts, not installed-host or incident acceptance',
  appearanceExportSha256:createHash('sha256').update(appearanceBytes).digest('hex'),
  appearances:appearances.map(({id,sha256})=>({id,sha256})),caseCount:cases.length,cases,completedCases,metrics,errors,failures},null,2)+'\n');
 if(browser)await browser.close();if(vite)await vite.close();
}