// Production components and CSS with synthetic retained state only. No live work.
import {createServer,transformWithEsbuild} from 'vite';
import {chromium,expect} from '@playwright/test';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {assertWorkHierarchy,assertDisclosureBody} from './work-hierarchy-geometry.mjs';
import {assertConversationErrorFocus as assertFocus} from './conversation-error-geometry.mjs';
import {workHierarchyData} from './fixtures/work-hierarchy.js';
const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS||'/artifacts/work-hierarchy';
const fixture=`
import React,{useEffect,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {TurnTimeline,WorkTimeline} from '/src/timeline.jsx';
import {ShellContext} from '/src/shell/runtime.jsx';
import {Questions} from '/src/questions.jsx';
import {WorkspaceReview} from '/src/workspace-review.jsx';
import {ArtifactFileDetails} from '/src/artifact-file-details.jsx';
import {workHierarchyData} from '/tests/fixtures/work-hierarchy.js';
import '/src/reference.css';import '/src/chat-presentation.css';import '/src/main-refresh.css';import '/src/canvas-workspace.css';import '/src/work-shell.css';import '/src/coordination.css';
window.calls=[];
const data=workHierarchyData(),target={id:'file-fixture',version:1,sessionId:'main-fixture'};
const review={sessionId:'main-fixture',workspace:'/fixture',capturedAt:2,items:[],claims:{status:'unassigned',items:[],total:0},sourceObservations:[],installedApp:{serverVersion:'fixture',frontend:{id:'source-identity-'.repeat(8),version:'fixture'}}};
const dispatch=async(name,args)=>{window.calls.push({name,args});
 if(name==='coordination.review')return {accepted:true,result:review};
 if(name==='canvas.versions.inspect')return {accepted:true,result:{...target,filePaths:{absolute:'/synthetic/'+('long-path-segment/'.repeat(12))+'file.txt',base:'/synthetic',relative:'file.txt'}}};
 throw Error('Hierarchy fixture refuses effects: '+name);
};
function Scene(){
 const [expanded,setExpanded]=useState([]),[detail,setDetail]=useState('standard');
 useEffect(()=>{window.fixtureMounted=true;return()=>{window.fixtureMounted=false}},[]);
 window.setDetail=setDetail;
 const act=(name,args)=>{if(name==='view.update'&&args.patch.executionExpanded){setExpanded(args.patch.executionExpanded);return Promise.resolve({accepted:true})}return dispatch(name,args)};
 const state={selectedSessionId:'main-fixture',view:{executionExpanded:expanded}};
 const pending={id:'question',revision:1,contentRevision:1,status:'pending',prompt:'Choose a **synthetic** path',dependency:'',required:true,allowFreeText:true,options:[{id:'keep',label:'Keep retained data'}]};
 const history={...pending,id:'answered',status:'answered',delivery:{status:'accepted'},answer:{text:'Retained answer',questionRevision:1}};
 return <ShellContext.Provider value={{composition:{presentation:{interfaceDetail:detail,executionMetrics:['elapsedSeconds','calls']}}}}>
 <section aria-label="Single failed turn"><TurnTimeline data={{...data,nodes:[],detailsDeferred:true}} turnId="failed-turn" state={state} act={act}/></section>
 <section aria-label="Grouped turns"><WorkTimeline data={data} items={[{turnId:'failed-turn'},{message:{id:'interim',text:'Retained interim update'}},{turnId:'worked-turn'}]} row={{summaryId:'group'}} state={state} act={act} renderMessage={message=><p>{message.text}</p>}/></section>
 <section aria-label="Questions"><Questions session={{id:'main-fixture',questions:[pending]}} dispatch={dispatch}/><Questions history session={{id:'main-fixture',questions:[history]}} dispatch={dispatch}/></section>
 <section aria-label="Canvas file"><ArtifactFileDetails target={target} dispatch={dispatch} placement="fixture"/></section>
 <section aria-label="Workspace review"><WorkspaceReview sessionId="main-fixture" workspace="/fixture" dispatch={dispatch}/></section>
 </ShellContext.Provider>;
}
createRoot(document.getElementById('fixture'),{onUncaughtError(error,info){
 window.fixtureFailure={message:error.message,stack:error.stack,componentStack:info.componentStack};
}}).render(<Scene/>);
`;
let vite,browser,page;const metrics=[],failures=[];
try{
 await mkdir(artifacts,{recursive:true});
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-runtime']},plugins:[{
  name:'work-hierarchy',resolveId(id){if(id==='/work-hierarchy-fixture.jsx')return id},async load(id){if(id==='/work-hierarchy-fixture.jsx')return (await transformWithEsbuild(fixture,'work-hierarchy-fixture.jsx',{loader:'jsx',jsx:'automatic'})).code},
  configureServer(server){server.middlewares.use((req,res,next)=>{if(req.url!=='/work-hierarchy')return next();res.setHeader('Content-Type','text/html');res.end('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{margin:0;height:auto!important;overflow:auto!important}#amp-one{height:auto!important;min-height:100vh;overflow:visible!important;display:block!important;padding:16px!important}#fixture{display:grid;gap:32px;max-width:760px;margin:auto}#fixture>section{min-width:0}#fixture>section+section{border-top:1px solid var(--a-line);padding-top:16px}</style><main id="amp-one" class="a-chat-shell a-approachable-shell"><div id="fixture"></div></main><script type="module" src="/work-hierarchy-fixture.jsx"></script>')})}
 }]});
 await vite.listen();browser=await chromium.launch({headless:true});
 for(const width of [1280,768,420,390,320]){
  page=await browser.newPage({viewport:{width,height:900}});
  page.on('pageerror',error=>failures.push(error.message));
  await page.route('**/api/**',route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/api/conversation/detail'&&url.searchParams.get('sessionId')==='main-fixture'&&url.searchParams.get('group')==='failed-turn'&&url.searchParams.get('part')==='nodes'){
    return route.fulfill({contentType:'application/json',body:JSON.stringify({items:workHierarchyData().nodes.filter(node=>node.turnId==='failed-turn'),before:url.searchParams.has('before')?null:'prior-fixture-page'})});
   }
   failures.push('Unexpected fixture API read: '+url.pathname);return route.abort('blockedbyclient');
  });
  for(const scheme of ['light','dark'])for(const pane of [null,...(width===1280?[360]:[])])for(const detail of ['minimal','standard','detailed']){
   await page.emulateMedia({colorScheme:scheme});await page.goto(vite.resolvedUrls.local[0]+'work-hierarchy');
   await page.waitForFunction(()=>window.fixtureMounted||window.fixtureFailure);
   const renderFailure=await page.evaluate(()=>window.fixtureFailure||null);
   assert.equal(renderFailure,null,'production-component fixture failed to render: '+JSON.stringify(renderFailure));
   await page.evaluate(({pane,detail})=>{document.getElementById('fixture').style.maxWidth=pane?pane+'px':'760px';document.getElementById('amp-one').dataset.executionDetail=detail;window.setDetail(detail)},{pane,detail});
   const label=[width,scheme,pane||'full',detail].join('-'),single=page.getByRole('region',{name:'Single failed turn',exact:true}),turn=single.locator('[data-part=execution]');
   const summary=turn.locator(':scope>.a-execution-turn-line');await expect(summary).toBeVisible();
   if(await summary.getAttribute('aria-expanded')!=='true')await summary.click();
   const tool=turn.locator('[data-node-id=root-tool]>button');await tool.click();
   const model=turn.locator('[data-node-id=root-model]>button');await model.click();
   const related=turn.locator('[data-node-id=unlinked-record]>button');await related.click();
   metrics.push({label,work:await assertWorkHierarchy(turn,label)});
   const parentId=await summary.getAttribute('aria-controls');await expect(turn.locator('[id="'+parentId+'"]')).toBeVisible();
   await expect(turn.getByRole('button',{name:'Load earlier steps',exact:true})).toBeVisible();
   const earlier=await turn.getByRole('button',{name:'Load earlier steps',exact:true}).boundingBox(),parentLabel=await summary.locator('.a-execution-label').boundingBox();
   assert.ok(earlier.x>=parentLabel.x-1,label+' pagination starts under the parent text column');
   await summary.focus();await page.keyboard.press('Enter');await expect(turn.locator('.a-execution-turn-body')).toHaveCount(0);
   await page.keyboard.press('Enter');await expect(tool).toBeVisible();
   await assertFocus(tool,label+' tool');
   const grouped=page.getByRole('region',{name:'Grouped turns',exact:true}).locator('[data-part=execution-group]'),groupSummary=grouped.locator(':scope>button');
   if(await groupSummary.getAttribute('aria-expanded')!=='true')await groupSummary.click();
   const branches=grouped.locator('.a-work-group-body>[data-part=execution]');await expect(branches).toHaveCount(2);
   await expect(branches.locator(':scope>.a-execution-turn-line')).toHaveCount(0);
   const questions=page.getByRole('region',{name:'Questions',exact:true});
   for(const selector of ['.a-question-alternative','.a-question-context']){
    const disclosure=questions.locator(selector);await disclosure.locator('summary').click();metrics.push({label,selector,geometry:await assertDisclosureBody(disclosure,'.a-question-disclosure-body',label+selector)});
   }
   const history=questions.locator('.a-question-history');if(await history.count())await history.locator(':scope>summary').click();
   await history.locator('.a-question-item>summary').click();
   const original=questions.locator('.a-question-original');await original.locator('summary').click();metrics.push({label,original:await assertDisclosureBody(original,'.a-question-disclosure-body',label+' original')});
   const file=page.getByRole('region',{name:'Canvas file',exact:true}).locator('details');await file.locator('summary').click();await expect(file).toContainText('Original source location');metrics.push({label,file:await assertDisclosureBody(file,'div',label+' file')});
   const review=page.getByRole('region',{name:'Workspace review',exact:true}),outer=review.locator('.a-workspace-review-disclosure').first();await outer.locator(':scope>summary').click();
   const inner=outer.locator('.a-workspace-review-disclosure');await inner.locator('summary').click();metrics.push({label,review:await assertDisclosureBody(outer,'.a-workspace-review-body',label+' review'),nested:await assertDisclosureBody(inner,'.a-workspace-review-body',label+' frontend')});
   assert.ok((await page.evaluate(()=>window.calls)).every(call=>['coordination.review','canvas.versions.inspect'].includes(call.name)),label+' only explicit passive fixture reads; no retry/answer/approval/download/copy/provider/runtime effect');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,label+' horizontal overflow');
  }
  await page.screenshot({path:artifacts+'/'+width+'.png',fullPage:true});await page.close();page=null;
 }
 assert.deepEqual(failures,[]);console.log('PASS: expanded failed/worked body hierarchy, operation siblings/grandchildren, narrow panes, details, questions, file provenance and workspace review; no live effects.');
}catch(error){failures.push(String(error.stack||error));if(page)await page.screenshot({path:artifacts+'/failure.png',fullPage:true}).catch(()=>{});throw error}
finally{await writeFile(artifacts+'/result.json',JSON.stringify({passed:failures.length===0,scope:'Synthetic retained-state production components/CSS, not installed-host acceptance.',metrics,failures},null,2)+'\n');if(browser)await browser.close();if(vite)await vite.close()}
