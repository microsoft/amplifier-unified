// Actual production components/CSS with synthetic props only; never a host mutation.
import {createServer,transformWithEsbuild} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/shared-notices-motion';
await mkdir(output,{recursive:true});
const fixture=`
import React,{useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ArchiveNotice} from '/src/archive-feedback.jsx';
import {createArchiveAction} from '/src/archive-action.js';
import {NavigationStatus} from '/src/navigation-details.jsx';
import {LiveActivity} from '/src/live-activity.jsx';
import {TurnTimeline} from '/src/timeline.jsx';
import {AppearanceSettings} from '/src/appearance-settings.jsx';
import {SettingsLayoutContext} from '/src/settings-layout.jsx';
import '/src/reference.css';
import '/src/main-refresh.css';
window.calls=[];
const id='captured-chat',title='A long archived conversation title '.repeat(8);
function App(){
 const [feedback,setFeedback]=useState({notice:null,pending:[]}),[placement,setPlacement]=useState('top');
 const [scope,setScope]=useState('shared'),[preview,setPreview]=useState(false);
 const action=useRef(null);
 if(!action.current)action.current=createArchiveAction({
  getState:()=>({selectedSessionId:'other',view:{},workspaces:[]}),getRevision:()=>0,navigate:async()=>({accepted:true}),
  dispatch:async(name,args)=>{window.calls.push({action:name,args});return {accepted:true,result:{id:args.id,archived:name==='session.archive'}}},
  onChange:setFeedback,
 });
 window.archiveFixture=()=>action.current.run({id,title});
 window.replaceFixture=phase=>setFeedback({notice:{operation:999,id,title,text:phase,phase},pending:phase==='pending'?[id]:[]});
 window.setPlacement=setPlacement;window.setPreview=setPreview;
 const value={...feedback,...action.current};
 const values={appearanceId:'reference',scheme:'system',density:'comfortable',layout:'balanced'};
 const appearance={namespace:'amplifier.appearance.v2:'+'a'.repeat(32),generation:'fixture',activeScope:scope,
  effective:values,shared:{revision:1,values},local:{revision:1,values,storageVersion:'fixture'},
  theme:{id:'reference',name:'Reference',css:''},preview:preview?{theme:{name:'Synthetic preview'}}:null};
 const act=async(name,args)=>{window.calls.push({action:name,args});if(name==='theme.list')return {accepted:true,result:{items:[],errors:[]}};
  if(name==='appearance.preview.cancel'){setPreview(false);return {accepted:true}};throw Error('Unexpected fixture action: '+name)};
 return <>
  <div id="top-notice">{placement==='top'&&<ArchiveNotice value={value}/>}</div>
  <div id="dialog-notice" className="a-dialog">{placement==='dialog'&&<ArchiveNotice value={value}/>}</div>
  <section id="motion"><NavigationStatus activity={{kind:'working',label:'Working'}}/>
   <LiveActivity session={{id:'working',status:'working'}}/>
   <TurnTimeline data={{nodes:[],turns:[{id:'turn',phase:'running',startedAt:Date.now()/1000-10}]}} turnId="turn" state={{selectedSessionId:'working',view:{}}} act={act}/>
  </section>
  <button id="outside" type="button">Outside notice</button>
  <section id="settings"><SettingsLayoutContext.Provider value={{active:true}}>
   <AppearanceSettings state={{client:{hostInstanceId:'notice-motion-fixture'}}} appearance={appearance} act={act} editor={{}}
    onPreferenceSelect={async next=>{setScope(next);return {status:'stored',revision:1}}}/>
  </SettingsLayoutContext.Provider></section>
 </>;
}
createRoot(document.getElementById('fixture')).render(<App/>);
`;
let vite,browser,page;
const errors=[],evidence={scope:'production components, synthetic props; not paired server',motion:[],geometry:[],checks:[]};
async function samples(selector,property){
 return page.locator(selector).first().evaluate(async(node,property)=>{
  const rows=[];
  for(const delay of [0,137,223,311]){
   if(delay)await new Promise(resolve=>setTimeout(resolve,delay));
   rows.push({at:performance.now(),value:getComputedStyle(node)[property]});
  }
  return rows;
 },property);
}
async function geometry(label){
 const notice=page.locator('[data-archive-notice]');await expect(notice).toBeVisible();
 const value=await notice.evaluate(node=>{
  const rect=n=>{const r=n.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}};
  return {notice:rect(node),text:rect(node.querySelector(':scope>span')),buttons:[...node.querySelectorAll('button')].map(rect),
   scrollWidth:node.scrollWidth,clientWidth:node.clientWidth,paddingTop:parseFloat(getComputedStyle(node).paddingTop)};
 });
 assert.ok(value.text.left-value.notice.left>=16,label+' horizontal inset');
 assert.ok(value.text.top-value.notice.top>=12,label+' vertical inset');
 assert.ok(value.scrollWidth<=value.clientWidth,label+' no horizontal overflow');
 for(const button of value.buttons){
  assert.ok(button.height>=34&&button.width>=24,label+' usable targets');
  assert.ok(button.left>=value.notice.left&&button.right<=value.notice.right,label+' bounded actions');
  assert.ok(button.top>=value.notice.top+10&&button.bottom<=value.notice.bottom-10,label+' vertical padding');
 }
 evidence.geometry.push({label,...value});
}
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),
  server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-runtime']},
  plugins:[{name:'shared-notices-motion',
   resolveId:id=>id==='/shared-notices-motion.jsx'?id:undefined,
   async load(id){if(id==='/shared-notices-motion.jsx')return (await transformWithEsbuild(fixture,'shared-notices-motion.jsx',{loader:'jsx',jsx:'automatic'})).code},
   configureServer(server){server.middlewares.use((req,res,next)=>{
    if(req.url!=='/shared-notices-motion')return next();
    res.setHeader('Content-Type','text/html');res.end('<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><main id="amp-one" class="a-approachable-shell"><div id="fixture"></div></main><script type="module" src="/shared-notices-motion.jsx"></script>');
   })},
  }]});
 await vite.listen();browser=await chromium.launch({headless:true,
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),
  ...(process.env.CHROMIUM_SINGLE_PROCESS==='1'?{args:['--single-process','--no-zygote','--no-sandbox']}:{}),
 });
 page=await browser.newPage({viewport:{width:1280,height:900}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(vite.resolvedUrls.local[0]+'shared-notices-motion');
 // Only remove the document viewport lock: this fixture stacks isolated surfaces.
 await page.addStyleTag({content:'html,body{height:auto;overflow:auto}body{margin:0}#fixture{display:grid;gap:16px}#settings,#motion{padding:16px;min-width:0}#dialog-notice:empty{display:none}.a-dialog{width:min(620px,100%)}'});
 await expect(page.locator('.a-progress-spinner').first()).toBeVisible();
 // Fresh browser default; no motion override or OS preference change.
 assert.equal(await page.evaluate(()=>matchMedia('(prefers-reduced-motion: reduce)').matches),false);
 for(const [selector,property] of [['.a-navigation-status .a-progress-spinner','transform'],['.a-activity-pulse b','opacity'],['.a-execution-dot','opacity']]){
  const rows=await samples(selector,property);evidence.motion.push({selector,property,mode:'device-default',rows});
  assert.ok(new Set(rows.map(row=>row.value)).size>1,selector+' changes over non-cycle temporal samples');
 }
 const advanced=page.locator('.a-appearance-more'),summary=advanced.locator(':scope>summary');
 await expect(advanced).toHaveJSProperty('open',false);
 await expect(page.getByLabel('Density',{exact:true})).toBeVisible();
 await expect(page.getByLabel('Layout',{exact:true})).toBeHidden();
 await expect(page.locator('[data-appearance-motion]')).toContainText('Standard motion is on');
 const before=await page.evaluate(()=>window.calls.length);
 await summary.focus();await summary.press('Enter');await expect(advanced).toHaveJSProperty('open',true);
 await expect(page.getByLabel('Layout',{exact:true})).toBeVisible();
 await summary.press('Space');await expect(advanced).toHaveJSProperty('open',false);
 assert.equal(await page.evaluate(()=>window.calls.length),before,'Native disclosure dispatches nothing');
 await page.getByRole('button',{name:'This browser',exact:true}).click();
 await expect(page.getByRole('button',{name:'This browser',exact:true})).toHaveAttribute('aria-pressed','true');
 await expect(page.getByLabel('Layout',{exact:true})).toBeHidden();
 await page.evaluate(()=>window.setPreview(true));
 await expect(page.locator('.a-appearance-preview')).toBeVisible();
 await expect(page.locator('.a-appearance-preview button')).toBeVisible();
 await page.emulateMedia({reducedMotion:'reduce'});
 await expect(page.locator('[data-appearance-motion]')).toContainText('Reduced motion is on');
 for(const [selector,property] of [['.a-progress-spinner','transform'],['.a-activity-pulse b','opacity'],['.a-execution-dot','opacity']]){
  const rows=await samples(selector,property);evidence.motion.push({selector,property,mode:'reduced',rows});
  assert.equal(new Set(rows.map(row=>row.value)).size,1,selector+' static when reduced');
  await expect(page.locator(selector).first()).toBeVisible();
  assert.equal(await page.locator(selector).first().evaluate(node=>getComputedStyle(node).animationName),'none');
 }
 await page.emulateMedia({reducedMotion:'no-preference'});
 await page.clock.install();
 await page.evaluate(()=>window.archiveFixture());
 for(const width of [1280,390,320]){
  await page.setViewportSize({width,height:900});
  for(const placement of ['top','dialog']){
   await page.evaluate(value=>window.setPlacement(value),placement);await geometry(width+'-'+placement);
   await page.screenshot({path:output+'/'+width+'-'+placement+'.png',fullPage:true});
  }
 }
 await page.evaluate(()=>window.setPlacement('top'));
 const notice=page.locator('[data-archive-notice]'),undo=notice.getByRole('button',{name:'Undo',exact:true});
 await undo.hover();await page.clock.runFor(20_000);await expect(notice).toBeVisible();
 await undo.focus();await page.mouse.move(0,0);await page.clock.runFor(20_000);
 await expect(undo).toBeFocused();await expect(notice).toBeVisible();
 await page.locator('#outside').focus();
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'))});
 await page.clock.runFor(20_000);await expect(notice).toBeVisible();
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'))});
 await page.clock.runFor(15_001);await expect(notice).toHaveCount(0);
 assert.equal((await page.evaluate(()=>window.calls.filter(row=>row.action==='session.restore'))).length,0,'Expiry never undoes');
 await page.evaluate(()=>window.archiveFixture());
 await notice.getByRole('button',{name:'Keep open',exact:true}).click();
 await page.evaluate(()=>window.setPlacement('dialog'));
 await page.mouse.move(0,0);await page.locator('#outside').focus();await page.clock.runFor(30_000);
 await expect(notice).toBeVisible();await expect(notice.getByRole('button',{name:'Keeping open'})).toHaveAttribute('aria-pressed','true');
 await notice.getByRole('button',{name:'Undo',exact:true}).click();
 assert.deepEqual(await page.evaluate(()=>window.calls.filter(row=>row.action==='session.restore')),[{action:'session.restore',args:{id:'captured-chat'}}]);
 await expect(notice).toContainText('Chat restored');
 for(const phase of ['error','unknown','pending']){
  await page.evaluate(value=>window.replaceFixture(value),phase);await page.clock.runFor(60_000);
  await expect(notice).toContainText(phase);
  await expect(notice.getByRole('button',{name:'Undo',exact:true})).toHaveCount(0);
 }
 evidence.checks.push('temporal default/reduced motion; responsive top/dialog notice; expiry paused hover/focus/hidden; no replay; Keep open survives remount; captured Undo; persistent failure/unknown/pending; native Advanced keyboard/no writes; same profile layout; preview outside fold; live device Motion status');
 assert.deepEqual(errors,[]);evidence.passed=true;
 console.log('Shared notices/motion browser passed (synthetic components; no actual host effects).');
}finally{
 evidence.errors=errors;await writeFile(output+'/result.json',JSON.stringify(evidence,null,2));
 await browser?.close();await vite?.close();
}