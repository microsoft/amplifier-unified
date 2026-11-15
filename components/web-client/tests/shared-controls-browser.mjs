// Rendered production components with synthetic props. Run in the owning DTU;
// no provider, reset, inspection or production-history operations are allowed.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createServer,transformWithEsbuild} from 'vite';
import {chromium,expect} from '@playwright/test';

const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS||'output/shared-controls';
const fixture=`
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {StarterSettings} from '/src/workspace-starters.jsx';
import {MaintenanceSettings} from '/src/maintenance.jsx';
import {SettingsPageContext} from '/src/settings-ui.jsx';
import {SettingsLayoutContext} from '/src/settings-layout.jsx';
import {ArtifactRuntime} from '/src/artifact-runtime.jsx';
import {SessionHistoryControls} from '/src/shell-panels.jsx';
import {WorkProjectionStatus} from '/src/work-projection-status.jsx';
import {Search} from 'lucide-react';
import '/src/reference.css';
import '/src/workspace-setup.css';
import '/src/settings-everyday.css';
import '/src/settings-mobile.css';
import '/src/appearance-settings.css';
import '/src/composer-formatting.css';
import '/src/canvas-workspace.css';
import '/src/context-inspector.css';
window.calls=[];
const catalog={items:[{id:'one',revision:1,name:'Synthetic starter',description:'',instructions:'',bundle:'',repositories:[],scratch:false,trackResources:false,rootGit:false}],bundles:[]};
// Enough real rows to exercise the production desktop scroll owner and sticky
// first-child search, rather than a computed outline in an unclipped mock row.
catalog.items.push(...Array.from({length:12},(_,index)=>({...catalog.items[0],id:'extra-'+index,name:'Extra starter '+index})));
const act=async(action,args)=>{
 window.calls.push({action,args});
 if(action==='workspace.starters.list')return {accepted:true,result:catalog};
 if(action==='view.update'||action==='session.select')return {accepted:true};
 throw Error('Spacing fixture refuses effects: '+action);
};
const state={view:{workspaceStarterEditor:{detailOpen:true},settingsExpanded:['diagnostics','reset']},workspaceStarters:catalog,
 diagnostics:{config:{enabled:false,streams:['app'],destinations:[],retentionDays:7,maxRecords:1000},streams:[{id:'app',label:'App actions'}]}};
function History(){
 const [progress,setProgress]=useState({status:'loading'}),[patch,setPatch]=useState({});
 window.updateHistory=(progress,patch)=>{setProgress(progress);setPatch(patch)};
 return <section aria-label="History"><WorkProjectionStatus session={{id:'history',workProjection:progress,...patch}}/>
  <SessionHistoryControls session={{id:'history',historyLoaded:true,historyError:'Could not read this saved message window. Original files are unchanged.',...patch}} act={act}/>
  <p id="saved-reply">Saved reply stays readable</p></section>;
}
const options=<><option value="one">Long selected native value</option><option value="two">Second native value</option></>;
function Forms(){
 const compact=innerWidth<960,[view,setView]=useState({...state.view,workspaceStarterEditor:{detailOpen:!compact}});
 const route=async(action,args)=>{if(action==='view.update')setView(current=>({...current,...args.patch}));return act(action,args)};
 return <SettingsLayoutContext.Provider value={{compact,active:true,footer:null}}>
 <section className="a-dialog a-settings-experience" data-compact={compact} aria-label="Forms">
  <StarterSettings state={{...state,view}} act={route}/>
  <SettingsPageContext.Provider value="diagnostics"><MaintenanceSettings state={state} act={act}/></SettingsPageContext.Provider>
  <SettingsPageContext.Provider value="reset"><MaintenanceSettings state={state} act={act}/></SettingsPageContext.Provider>
  <label id="wrapped-choice"><input type="checkbox"/>A multiline choice keeps its first line beside the control and all remaining lines in the same text column even on a narrow screen.</label>
  <label className="a-sr-only" htmlFor="ordinary">Ordinary field</label><input id="ordinary"/>
  <div className="a-workspace-picker"><label className="a-picker-search"><Search aria-hidden="true"/><input aria-label="Compound picker"/></label></div>
  <div className="a-appearance-settings"><label className="a-appearance-toggle"><input type="checkbox"/>Appearance toggle</label>
   <div className="a-detail-choices"><label><input type="checkbox"/>Metric choice</label></div>
   <label className="a-appearance-card"><input className="a-appearance-radio-input" type="radio" name="appearance"/><span>Appearance card</span></label>
  </div>
  <ArtifactRuntime sessionId="history" act={act}/>
  <section aria-label="Select variants">
   <label>Native value<select id="native">{options}</select></label>
   <div className="a-workspace-setup"><select aria-label="Workspace value">{options}</select></div>
   <div className="a-context-inspector"><select aria-label="Inspector value">{options}</select></div>
   <div className="a-composer-format" style={{position:'static',width:'100%'}}><div className="a-composer-format-row"><select aria-label="Format value">{options}</select></div></div>
   <div className="a-resource-view-controls"><select aria-label="Compact position" style={{width:68}}><option>1</option><option>2</option></select></div>
   <div className="a-canvas-toolbar"><select aria-label="Canvas value">{options}</select></div>
   <div className="a-order-item"><span className="a-order-copy">Ordered entry</span><select aria-label="Ordered position"><option>100</option><option>999</option></select></div>
   <select aria-label="Explicit size one" size={1}>{options}</select>
   <select aria-label="Multiple choices" multiple defaultValue={['one']}>{options}</select>
   <select aria-label="Visible list" size={3}>{options}</select>
   <select aria-label="Custom native indicator" data-native-indicator>{options}</select>
  </section>
 </section>
 </SettingsLayoutContext.Provider>;
}
createRoot(document.getElementById('fixture')).render(<>
 <Forms/>
 <History/>
</>);
`;
const metrics=[],errors=[];
let vite,browser,page,failure;
await mkdir(artifacts,{recursive:true});
function gap(a,b){return Math.max(b.left-a.right,a.left-b.right,b.top-a.bottom,a.top-b.bottom)}
async function rect(locator){return locator.evaluate(node=>{const r=node.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}})}
async function searchFocusPaint(wrapper,checkpoint,{desktop=false}={}){
 const geometry=await wrapper.evaluate(node=>{
  const css=getComputedStyle(node),rect=node.getBoundingClientRect();
  const width=parseFloat(css.outlineWidth),offset=parseFloat(css.outlineOffset),outset=width+offset;
  // The outer paint envelope includes both the outline thickness and offset.
  // A solid computed outline alone says nothing about clipping by ancestors.
  const paint={left:rect.left-outset,right:rect.right+outset,top:rect.top-outset,bottom:rect.bottom+outset};
  const ancestors=[];
  for(let parent=node.parentElement;parent;parent=parent.parentElement){
   const style=getComputedStyle(parent),box=parent.getBoundingClientRect();
   const clips=value=>['auto','scroll','hidden','clip'].includes(value);
   if(!clips(style.overflowX)&&!clips(style.overflowY))continue;
   ancestors.push({className:parent.className,overflowX:style.overflowX,overflowY:style.overflowY,
    clipsX:clips(style.overflowX),clipsY:clips(style.overflowY),scrollTop:parent.scrollTop,
    left:box.left+parent.clientLeft,top:box.top+parent.clientTop,
    right:box.left+parent.clientLeft+parent.clientWidth,bottom:box.top+parent.clientTop+parent.clientHeight});
  }
  return {width,offset,style:css.outlineStyle,paint,ancestors,collectionMounted:!!node.closest('.a-collection')};
 });
 assert.equal(geometry.style,'solid');assert.ok(geometry.width>=2,'Focus remains visibly substantial');
 assert.ok(geometry.collectionMounted,'Real collection is mounted, not an isolated search');
 if(desktop){
  assert.ok(geometry.ancestors.some(row=>row.className.split(' ').includes('a-collection')),'Real desktop collection clips its contents');
  assert.ok(geometry.ancestors.some(row=>row.className==='a-collection-list'&&row.overflowY==='auto'),'Production desktop list owns scrolling');
 }
 for(const ancestor of geometry.ancestors){
  const {paint}=geometry,tolerance=.5;
  if(ancestor.clipsX)assert.ok(paint.left>=ancestor.left-tolerance&&paint.right<=ancestor.right+tolerance,'Complete horizontal outline paint is inside '+JSON.stringify({checkpoint,paint,ancestor}));
  if(ancestor.clipsY)assert.ok(paint.top>=ancestor.top-tolerance&&paint.bottom<=ancestor.bottom+tolerance,'Complete vertical outline paint is inside '+JSON.stringify({checkpoint,paint,ancestor}));
 }
 metrics.push({checkpoint,searchFocusPaint:geometry});
}
async function choiceGeometry(locator,dir,firstLine=true){
 const geometry=await locator.evaluate(node=>{
  const input=node.querySelector('input'),walker=document.createTreeWalker(node,NodeFilter.SHOW_TEXT);
  let text;while(walker.nextNode()){if(walker.currentNode.textContent.trim()){text=walker.currentNode;break}}
  const range=document.createRange();range.selectNodeContents(text);
  const boxes=[...range.getClientRects()].map(r=>({left:r.left,right:r.right,top:r.top,bottom:r.bottom}));
  const r=input.getBoundingClientRect();return {input:{left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width},boxes};
 });
 const {input,boxes}=geometry;
 assert.ok(input.width>=16&&input.width<=18,'Choice stays intrinsic-sized');
 for(const box of boxes)assert.ok((dir==='rtl'?input.left-box.right:box.left-input.right)>=6,'Every wrapped line stays in the text column');
 if(firstLine)assert.ok(Math.abs(input.top-boxes[0].top)<=6,'Choice aligns with first text line, not whole wrapped block');
 metrics.push({choice:await locator.textContent(),dir,...geometry});
}
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),
  server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-runtime']},
  plugins:[{name:'shared-controls-fixture',
   resolveId(id){if(id==='/shared-controls.jsx')return id},
   async load(id){if(id==='/shared-controls.jsx')return (await transformWithEsbuild(fixture,'shared-controls.jsx',{loader:'jsx',jsx:'automatic'})).code},
   configureServer(server){server.middlewares.use((req,res,next)=>{
    if(req.url!=='/shared-controls')return next();
    res.setHeader('Content-Type','text/html');
    res.end('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{height:auto!important;overflow:auto!important;margin:0}#amp-one{padding:16px;min-height:100vh}#fixture{display:grid;gap:24px;max-width:900px;margin:auto}#fixture>.a-dialog{width:100%;max-height:none;overflow:visible}#fixture>section{min-width:0}</style><main id="amp-one"><div id="fixture"></div></main><script type="module" src="/shared-controls.jsx"></script>');
   })}
  }]});
 await vite.listen();browser=await chromium.launch({headless:true});
 for(const width of [1280,390,320]){
  page=await browser.newPage({viewport:{width,height:900},hasTouch:width<400,isMobile:width<400});
  page.on('pageerror',error=>errors.push(error.message));
  for(const dir of ['ltr','rtl'])for(const scheme of ['light','dark'])for(const forced of [false,true]){
   await page.emulateMedia({colorScheme:scheme,forcedColors:forced?'active':'none',reducedMotion:'reduce'});
   await page.goto(vite.resolvedUrls.local[0]+'shared-controls');
   const search=page.getByRole('searchbox',{name:'Find a starter'}),wrapper=search.locator('..');
   await expect(search).toBeVisible();
   await page.locator('#amp-one').evaluate((node,{dir,scheme})=>{node.dir=dir;node.dataset.themeScheme=scheme},{dir,scheme});
   const icon=await rect(wrapper.locator('svg')),field=await rect(search);
   assert.ok(Math.min(icon.bottom,field.bottom)-Math.max(icon.top,field.top)>10,'Search icon beside input');
   assert.ok(gap(icon,field)>=6,'Search icon separated from input');
   await search.focus();await expect(search).toHaveCSS('outline-style','none');
   await expect(wrapper).toHaveCSS('outline-style','solid');
   const focusLabel=[width,dir,scheme,forced?'forced':'normal'].join('-');
   await searchFocusPaint(wrapper,focusLabel+'-top',{desktop:width>=960});
   if(width>=960){
    const list=page.locator('[data-part=workspace-starters] .a-collection-list');
    const scrolled=await list.evaluate(node=>{node.scrollTop=100;return node.scrollTop});
    assert.ok(scrolled>0,'Populated production collection actually scrolls');
    await expect(wrapper).toHaveCSS('position','sticky');
    await searchFocusPaint(wrapper,focusLabel+'-scrolled',{desktop:true});
    await list.evaluate(node=>{node.scrollTop=0});
   }
   if(width<960)await page.getByRole('button',{name:/Synthetic starter/}).click();
   await expect(page.getByRole('textbox',{name:'Starter name'})).toBeVisible();
   for(const detail of await page.locator('.a-starter-options').all())await detail.evaluate(node=>{node.open=true});
   const label=[width,dir,scheme,forced?'forced':'normal'].join('-');
   for(const name of ['Create SCRATCH.md working memory','Track external resources','Initialize local workspace Git',
    'Collect app diagnostics and enable selected forwarding','Record provider requests and responses for troubleshooting',
    'Reset app configuration and credentials']){
    await choiceGeometry(page.getByRole('checkbox',{name,exact:true}).locator('..'),dir);
   }
   await choiceGeometry(page.locator('#wrapped-choice'),dir);
   for(const name of ['Appearance toggle','Metric choice']){
    const choice=page.getByRole('checkbox',{name,exact:true});
    await choiceGeometry(choice.locator('..'),dir,false);
    assert.equal((await rect(choice)).width,16,'Existing Appearance choice geometry retained');
    await expect(choice.locator('..'),'Appearance keeps its intentional gap').toHaveCSS('column-gap','8px');
   }
   for(const input of [page.getByRole('textbox',{name:'Compound picker'})]){
    await input.focus();await expect(input).toBeFocused();
    await expect(input).toHaveCSS('outline-style','none');
    await expect(input.locator('..')).toHaveCSS('outline-style','solid');
   }
   const ordinary=page.getByRole('textbox',{name:'Ordinary field'});
   await ordinary.focus();await expect(ordinary).toHaveCSS('outline-style','solid');
   assert.equal((await rect(page.locator('label[for=ordinary]'))).width,1,'Generic label does not unhide sr-only');
   const radio=page.getByRole('radio',{name:'Appearance card'});
   // Programmatic focus is not a keyboard-modality guarantee for a radio:
   // text inputs can match :focus-visible without a preceding keyboard action.
   // Keep the old path as diagnostic evidence, then exercise real native Tab
   // traversal and require BOTH the pseudo-class and its visible card outline.
   const cardFocusState=()=>radio.evaluate(node=>({
    focused:document.activeElement===node,focusVisible:node.matches(':focus-visible'),
    outline:getComputedStyle(node.parentElement).outlineStyle,
   }));
   await radio.focus();
   const programmatic=await cardFocusState(),beforeCardFocus=await page.evaluate(()=>window.calls.length);
   const metricChoice=page.getByRole('checkbox',{name:'Metric choice',exact:true});
   await metricChoice.focus();await expect(metricChoice).toBeFocused();
   await page.keyboard.press('Tab');
   const keyboard=await cardFocusState();
   metrics.push({label,appearanceCardFocus:{programmatic,keyboard}});
   await expect(radio,'Native Tab reaches the card radio').toBeFocused();
   assert.equal(keyboard.focusVisible,true,'Native keyboard focus must match :focus-visible');
   await expect(radio.locator('..')).toHaveCSS('outline-style','solid');
   await expect(radio.locator('..')).toHaveCSS('outline-width','2px');
   await expect(radio).not.toBeChecked();
   assert.equal(await page.evaluate(()=>window.calls.length),beforeCardFocus,'Keyboard focus alone cannot select an appearance or dispatch an action');
   const actions=page.locator('.a-control-actions button'),a=await rect(actions.nth(0)),b=await rect(actions.nth(1));
   assert.ok(gap(a,b)>=8,'Document actions keep horizontal/vertical clearance');
   if(Math.abs(a.top-b.top)<2)assert.equal(a.top,b.top,'Same-line buttons are aligned');
   await actions.nth(0).focus();await page.keyboard.press('Tab');await expect(actions.nth(1)).toBeFocused();
   await expect(actions.nth(1)).toHaveCSS('outline-style','solid');
   for(const select of await page.locator('select:not([multiple]):not([size="3"]):not([data-native-indicator])').all()){
    const css=await select.evaluate(node=>{const s=getComputedStyle(node);return {appearance:s.appearance,end:parseFloat(s.paddingInlineEnd),image:s.backgroundImage,position:s.backgroundPosition,width:node.clientWidth}});
    assert.equal(css.appearance,forced?'auto':'none');assert.ok(css.end>=36);
    assert.equal(css.image==='none',forced,'Exactly one decorative chevron, or native forced-colors fallback');
    if(!forced)assert.match(css.position,dir==='rtl'?/12px/:/100%/);
    assert.ok(css.width>css.end+12,'Even compact numeric selects retain text space');
    metrics.push({label,select:await select.getAttribute('aria-label'),css});
   }
   for(const name of ['Multiple choices','Visible list','Custom native indicator']){
    await expect(page.getByRole('listbox',{name}).or(page.getByRole('combobox',{name}))).toHaveCSS('appearance','auto');
   }
   const native=page.locator('#native');
   await native.focus();await native.press('ArrowDown');await native.press('Tab');
   await expect(native,'Native keyboard selection survives decoration').toHaveValue('two');
   const numericClearance=async checkpoint=>{
    const numeric=await page.getByRole('combobox',{name:'Ordered position'}).evaluate(node=>{
     const s=getComputedStyle(node),canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');ctx.font=s.font;
     const parent=node.parentElement,box=node.getBoundingClientRect(),bounds=parent.getBoundingClientRect();
     return {available:node.clientWidth-parseFloat(s.paddingInlineStart)-parseFloat(s.paddingInlineEnd),needed:ctx.measureText(node.selectedOptions[0].textContent).width,
      font:s.font,direction:s.direction,box:{left:box.left,right:box.right},parent:{left:bounds.left,right:bounds.right}};
    });
    assert.ok(numeric.available>=numeric.needed,'Multi-digit position remains readable before the chevron: '+JSON.stringify(numeric));
    assert.ok(numeric.available>=Math.ceil(numeric.needed)+2,'Multi-digit position retains rounding/paint reserve: '+JSON.stringify(numeric));
    assert.ok(numeric.box.left>=numeric.parent.left&&numeric.box.right<=numeric.parent.right,'Intrinsic select stays inside its narrow row');
    metrics.push({label,checkpoint,numericClearance:numeric});
   };
   await numericClearance('inherited-font');
   // Fractional glyph advance is not an assertion tolerance. Exercise actual
   // font-relative sizing in the same desktop/compact, RTL and contrast scenes.
   const numericSelect=page.getByRole('combobox',{name:'Ordered position'});
   for(const font of ['14.25px serif','16.5px monospace']){
    await numericSelect.evaluate((node,font)=>{node.style.font=font},font);
    await numericClearance(font);
   }
   await numericSelect.evaluate(node=>node.style.removeProperty('font'));
   const history=page.getByRole('region',{name:'History'}),retry=history.getByRole('button',{name:'Try again'});
   const statusGeometry=async()=>{
    for(const row of await history.locator('.a-history-status:has(>svg)').all()){
     const icon=await rect(row.locator(':scope>svg')),text=await rect(row.locator(':scope>span'));
     assert.ok((dir==='rtl'?icon.left-text.right:text.left-icon.right)>=8,'Visible status text has icon clearance');
    }
    const message=await rect(history.locator('.a-session-history-error>.a-history-status')),button=await rect(retry);
    assert.ok(gap(message,button)>=8,'Error/retry do not touch, including wrapped layout');
   };
   await expect(history.getByRole('status')).toContainText('Loading work details');
   await expect(history.getByRole('alert')).toContainText('Original files are unchanged');
   await statusGeometry();
   const before=await page.evaluate(()=>window.calls.filter(c=>c.action==='session.select').length);
   await retry.focus();await expect(retry).toHaveCSS('outline-style','solid');await retry.press('Enter');
   assert.equal(await page.evaluate(()=>window.calls.filter(c=>c.action==='session.select').length),before+1,'Only explicit retry dispatches');
   for(const code of ['recovered_backup','scan_limit']){
    await page.evaluate(code=>window.updateHistory({status:'retrying'},{historyActivity:{diagnostics:[{code}]}}),code);
    await expect(history.getByRole('status').first()).toContainText('Retrying shortly');
    await statusGeometry();
   }
   await page.evaluate(()=>window.updateHistory({status:'unavailable'},{workspaceAvailable:false,historyActivity:null}));
   await expect(history.getByRole('status').first()).toContainText('loading limit');
   await statusGeometry();
   await page.evaluate(()=>window.updateHistory({status:'ready'},{historyError:null,messages:[{id:'saved'}],historyActivity:null}));
   await expect(history.locator('.a-history-status')).toHaveCount(0);
   const stable=await rect(history.locator('#saved-reply'));
   await page.evaluate(()=>window.updateHistory({status:'loading',receipt:{sessionId:'history'}},{historyError:null,messages:[{id:'saved'}],historyLoading:true,historyActivity:null}));
   await expect(history.locator('.a-session-history,.a-history-status')).toHaveCount(0);
   assert.deepEqual(await rect(history.locator('#saved-reply')),stable,'Quiet refresh inserts no empty row or reading shift');
   if(width===320){
    await page.locator('#amp-one').evaluate(node=>node.style.setProperty('--a-text-base','24px'));
    await page.getByRole('combobox',{name:'Ordered position'}).evaluate(node=>{node.style.fontSize='24px'});
    await numericClearance('enlarged-24px');
    await page.evaluate(()=>window.updateHistory({status:'retrying'},{historyError:'Could not read this saved message window. Original files are unchanged. '.repeat(4)}));
    await statusGeometry();
   }
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal page overflow');
   assert.ok((await page.evaluate(()=>window.calls)).every(call=>['workspace.starters.list','view.update','session.select'].includes(call.action)),'No runtime/reset/repair/provider action');
   if(!forced)await page.screenshot({path:artifacts+'/'+label+'.png',fullPage:true});
  }
  await page.close();
 }
 assert.deepEqual(errors,[]);console.log('PASS: shared control geometry, native selection, focus and history feedback.');
}catch(error){failure=String(error.stack||error);throw error}
finally{
 await writeFile(artifacts+'/result.json',JSON.stringify({passed:!failure,scope:'Production components, synthetic props; not actual host/mobile OS acceptance',metrics,errors,failure},null,2)+'\n');
 if(browser)await browser.close();if(vite)await vite.close();
}