import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer,transformWithEsbuild} from 'vite';
import {readFile} from 'node:fs/promises';
import {workHierarchyData} from './fixtures/work-hierarchy.js';
import {assertDisclosureBody} from './work-hierarchy-geometry.mjs';

const browserSource=await readFile(new URL('./work-hierarchy-browser.mjs',import.meta.url),'utf8');
const fixtureStart=browserSource.indexOf('const fixture=`')+'const fixture=`'.length;
const fixtureEnd=browserSource.indexOf('\n`;\nlet vite',fixtureStart);
assert.ok(fixtureStart>14&&fixtureEnd>fixtureStart,'the exact browser scene is available to render preflight');
const fixture=browserSource.slice(fixtureStart,fixtureEnd);
const scene=fixture.slice(0,fixture.lastIndexOf('\ncreateRoot('))+'\nexport {Scene};';
const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]},plugins:[{
 name:'work-hierarchy-scene-preflight',
 resolveId(id){if(id==='virtual:work-hierarchy-scene.jsx')return id},
 async load(id){if(id==='virtual:work-hierarchy-scene.jsx')return (await transformWithEsbuild(scene,'work-hierarchy-scene.jsx',{loader:'jsx',jsx:'automatic'})).code},
}]});
const {TurnTimeline,WorkTimeline}=await server.ssrLoadModule('/src/timeline.jsx');
const {ShellContext}=await server.ssrLoadModule('/src/shell/runtime.jsx');
const {ArtifactFileDetails}=await server.ssrLoadModule('/src/artifact-file-details.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const wrap=child=>React.createElement(ShellContext.Provider,{value:{composition:{presentation:{interfaceDetail:'standard',executionMetrics:['elapsedSeconds','calls']}}}},child);
const hostParent=node=>{let parent=node.parent;while(parent&&typeof parent.type!=='string')parent=parent.parent;return parent};
const allText=node=>typeof node==='string'?node:(node.children||[]).map(allText).join('');

test('the exact browser scene commits every production surface before geometry or detail-level changes',async()=>{
 const previousWindow=globalThis.window;
 globalThis.window={};
 let root;
 try{
  const {Scene}=await server.ssrLoadModule('virtual:work-hierarchy-scene.jsx');
  await act(async()=>{root=create(React.createElement(Scene))});
  assert.equal(window.fixtureMounted,true,'readiness comes from a committed effect, not a setter assigned during a failed render');
  for(const label of ['Single failed turn','Grouped turns','Questions','Canvas file','Workspace review']){
   assert.equal(root.root.findByProps({'aria-label':label}).type,'section');
  }
  for(const detail of ['minimal','standard','detailed']){
   await act(async()=>window.setDetail(detail));
   assert.equal(root.root.findByProps({'aria-label':'Single failed turn'}).findAllByProps({'data-part':'execution'}).length,1);
   assert.equal(root.root.findByProps({'aria-label':'Questions'}).findAllByType('textarea').length,1);
  }
  assert.deepEqual(window.calls.map(call=>call.name),['coordination.review'],'only the production review component makes its expected passive initial read');
 }finally{await act(async()=>root?.unmount());assert.equal(window.fixtureMounted,false);globalThis.window=previousWindow}
});

for(const phase of ['error','completed'])test(`${phase} turn details are one controlled child body; operation siblings and exact identities are retained`,async()=>{
 const data=workHierarchyData();data.turns[0].phase=phase;
 const before=JSON.stringify(data),calls=[];let root;
 try{
  await act(async()=>{root=create(wrap(React.createElement(TurnTimeline,{data,turnId:'failed-turn',state:{view:{executionExpanded:['turn:failed-turn','root-tool','root-model','unlinked-record']}},act:(...args)=>calls.push(args)})))});
  const turn=root.root.findByProps({'data-part':'execution'}),summary=turn.findByProps({className:'a-execution-line a-execution-turn-line'});
  const body=turn.findByProps({id:summary.props['aria-controls']});
  assert.equal(body.props.className,'a-execution-turn-body');
  assert.equal(hostParent(body),turn);
  assert.equal(turn.props['data-embedded'],false);
  const stream=body.findByProps({'data-part':'work-stream'});
  const operations=stream.findAll(node=>node.type==='div'&&node.props.className==='a-execution-node'&&hostParent(node)===stream);
  assert.deepEqual(operations.map(node=>node.props['data-node-id']),['root-tool','root-model','child-tool','root-context']);
  for(const operation of operations){
   const header=operation.findAllByProps({className:'a-execution-line a-execution-action-line'}).find(node=>hostParent(node)===operation);
   if(header.props['aria-expanded'])assert.equal(operation.findByProps({id:header.props['aria-controls']}).props.className,'a-execution-body');
  }
  const child=operations.find(node=>node.props['data-node-id']==='child-tool');
  assert.equal(child.props['data-owner-id'],'reviewer');
  assert.match(allText(child),/Delegate.*Review child/,'chronological flat stream keeps explicit actor, not invented tree ancestry');
  const tool=operations[0];
  assert.equal(tool.findByProps({'aria-label':'Related saved records'}).findByProps({'data-node-id':'unlinked-record'}).props['data-kind'],'tool');
  assert.match(allText(turn),phase==='error'?/Failed/:/Worked/);
  assert.deepEqual(calls,[],'presentation never retries tools, stops workers or changes live work');
  assert.equal(JSON.stringify(data),before);
 }finally{await act(async()=>root?.unmount())}
});

test('grouped updates indent once and embedded turns do not gain another summary or branch',async()=>{
 const data=workHierarchyData(),calls=[];let root;
 const items=[{turnId:'failed-turn'},{message:{id:'interim',text:'Retained interim update'}},{turnId:'worked-turn'}];
 try{
  await act(async()=>{root=create(wrap(React.createElement(WorkTimeline,{data,items,row:{summaryId:'group'},state:{view:{executionExpanded:['group']}},act:(...args)=>calls.push(args),renderMessage:message=>React.createElement('p',{'data-fixture-message':message.id},message.text)})))});
  const group=root.root.findByProps({'data-part':'execution-group'}),summary=group.findAllByType('button').find(node=>node.props.className==='a-execution-line a-execution-turn-line');
  const body=group.findByProps({id:summary.props['aria-controls']});
  assert.equal(body.props.className,'a-work-group-items a-work-group-body');
  const turns=body.findAllByProps({'data-part':'execution'});
  assert.equal(turns.length,2);
  for(const turn of turns){
   assert.equal(turn.props['data-embedded'],true);
   assert.equal(turn.findAll(node=>node.type==='button'&&node.props.className==='a-execution-line a-execution-turn-line').length,0);
  }
  assert.equal(body.findByProps({'data-fixture-message':'interim'}).children[0],'Retained interim update');
  assert.deepEqual(calls,[]);
 }finally{await act(async()=>root?.unmount())}
});

test('load earlier steps remains inside the turn body and only reads the exact saved page',async()=>{
 const original=globalThis.fetch,calls=[],data=workHierarchyData();let root;
 data.detailsDeferred=true;data.nodes=[];
 const items=[{id:'paged-tool',kind:'tool',label:'read_file',phase:'completed'}];
 globalThis.fetch=async(url,options)=>{calls.push({url:String(url),method:options?.method||'GET'});return new Response(JSON.stringify({items,before:calls.length===1?'prior-page':null}),{headers:{'content-type':'application/json'}})};
 try{
  await act(async()=>{root=create(wrap(React.createElement(TurnTimeline,{data,turnId:'failed-turn',state:{view:{executionExpanded:['turn:failed-turn']}},act:()=>assert.fail('no host mutation')})))});
  await act(async()=>new Promise(resolve=>setTimeout(resolve,150)));
  const earlier=root.root.findByProps({className:'a-link a-execution-earlier'});
  assert.equal(hostParent(earlier).props.className,'a-execution-turn-body');
  await act(async()=>earlier.props.onClick());
  assert.equal(calls.length,2);
  const query=new URL(calls[1].url,'http://fixture').searchParams;
  assert.equal(query.get('sessionId'),'main-fixture');assert.equal(query.get('group'),'failed-turn');assert.equal(query.get('before'),'prior-page');
  assert.ok(calls.every(call=>call.method==='GET'&&call.url.startsWith('/api/conversation/detail?')));
 }finally{await act(async()=>root?.unmount());globalThis.fetch=original}
});

test('hierarchy layout is bounded and uses a component container, not viewport width alone',async()=>{
 const css=await readFile(new URL('../src/execution-content.css',import.meta.url),'utf8');
 assert.match(css,/\.a-execution-turn\{container-type:inline-size;container-name:work-detail\}/);
 assert.match(css,/@container work-detail \(max-width:600px\)/);
 assert.match(css,/data-embedded=false\]>.a-execution-turn-body/);
 assert.match(css,/\.a-execution-content>\.a-work-group-body/);
 assert.match(css,/\.a-execution-content \.a-execution-model-facts\{grid-template-columns:minmax\(0,1fr\) minmax\(0,2fr\)/);
});

test('native disclosure bodies budget for marker advance without replacing native summaries',async()=>{
 for(const [path,selector,padding] of [
  ['questions.css','.a-question-disclosure-body','max(24px,2em) 0'],
  ['coordination.css','.a-workspace-review-body','max(24px,2em) 0'],
  ['canvas-workspace.css','.a-canvas-file-path>div','max(28px,2em) 8px'],
 ]){
  const css=await readFile(new URL('../src/'+path,import.meta.url),'utf8');
  const rule=css.split('\n').find(line=>line.includes(selector)&&line.includes('padding-inline:'));
  assert.ok(rule?.includes('padding-inline:'+padding),path+': child inset clears native marker and scales with text');
  assert.doesNotMatch(css,/summary(?:::-webkit-details-marker|::marker)\s*\{[^}]*display:\s*none/,'native disclosure semantics and indicator stay intact');
 }
});

test('disclosure geometry still rejects a child before its summary text and retains failing coordinates',async()=>{
 // Contract test of the assertion itself, not browser-layout qualification.
 const geometry={outer:{left:10,right:310},body:{left:10,right:310},summaryText:{left:28,right:180},inset:12,overflow:false};
 await assert.rejects(assertDisclosureBody({evaluate:async()=>geometry},'.body','native marker regression'),error=>{
  assert.match(error.message,/child content does not start before summary text/);
  assert.match(error.message,/"summaryText":\{"left":28/);
  assert.match(error.message,/"inset":12/);
  return true;
 });
});

test('Canvas file detail body remains a single demand-loaded child bound to the exact saved version',async()=>{
 let root;const calls=[],target={id:'file',version:7,sessionId:'synthetic'};
 const dispatch=async(name,args)=>{calls.push([name,args]);assert.equal(name,'canvas.versions.inspect');return {accepted:true,result:{...target,filePaths:{absolute:'/synthetic/retained/file.txt'}}}};
 try{
  await act(async()=>{root=create(React.createElement(ArtifactFileDetails,{target,dispatch,placement:'fixture'}))});
  assert.deepEqual(calls,[]);
  const disclosure=root.root.findByType('details');
  await act(async()=>disclosure.props.onToggle({currentTarget:{open:true}}));
  assert.deepEqual(calls,[['canvas.versions.inspect',target]]);
  const code=disclosure.findByType('code');
  assert.equal(hostParent(code).type,'div');
  assert.equal(hostParent(hostParent(code)),disclosure);
  assert.equal(code.children[0],'/synthetic/retained/file.txt');
  assert.equal(calls.some(([name])=>/download|reveal|recover/.test(name)),false);
 }finally{await act(async()=>root?.unmount())}
});
