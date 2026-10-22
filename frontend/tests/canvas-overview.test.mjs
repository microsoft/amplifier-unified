import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {acceptedCanvasReceipt,settleCanvasReceipts,assertCanvasVisibility,acceptedCanvasVisibility} from './canvas-test-receipts.mjs';
import {createPendingView} from '../src/pending-view.js';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const source=await readFile(new URL('../src/canvas-library.jsx',import.meta.url),'utf8');
const oldId=resolve('src/canvas-library-focus-negative.jsx');
const originGate='if(!fromDialog||dialogGeneration.current!==generation||!dialog.current?.open)return;';
const intentGate='focusIntent.current!==intent||latest.selectedSessionId';
assert.ok(source.includes(originGate)&&source.includes(intentGate),'Negative control must mutate the actual guards');
const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]},
 plugins:[{name:'canvas-focus-negative-control',resolveId(id){if(id===oldId)return id},
  load(id){if(id!==oldId)return;return source
   .replace(originGate,'')
   .replace(intentGate,'latest.selectedSessionId')}}]});
test.after(()=>vite.close());
const {overviewSectionOpen,overviewSectionPatch,CanvasOverviewSection}=await vite.ssrLoadModule('/src/canvas-overview-state.jsx');
const {ChatOverview}=await vite.ssrLoadModule('/src/work-overview.jsx');
const {CanvasTabs,closeCanvasDialog}=await vite.ssrLoadModule('/src/canvas-library.jsx');
const state={selectedSessionId:'chat-a',selectedWorkspaceId:'workspace-a',view:{canvasDraft:{filter:'retained'}},canvas:{open:true,placeholder:true},canvasArtifacts:[],sessions:[{id:'chat-a',status:'idle',messages:[{attachments:[{id:'source',name:'Source.txt'}]}],workers:[{id:'worker',status:'error',name:'Source check'}]}],workspaces:[{id:'workspace-a',name:'Ideas',path:'/example/ideas',available:true}],recentNavigation:{items:[],total:0}};
test('section choices preserve the draft shape, sibling choices and search',()=>{
 assert.equal(overviewSectionOpen(state.view.canvasDraft,'outputs'),true);
 assert.equal(overviewSectionOpen(state.view.canvasDraft,'publishing',false),false);
 const draft={...state.view.canvasDraft,sections:{sources:false}};
 const patch=overviewSectionPatch(draft,'outputs',false);
 assert.deepEqual(patch,{...draft,sections:{sources:false,outputs:false}});
 assert.equal(overviewSectionOpen(patch,'outputs'),false);
 assert.equal(overviewSectionOpen({},'outputs'),true,'another chat does not inherit choices');
});
test('native disclosure retains heading/status while content is collapsed',()=>{
 const html=renderToStaticMarkup(React.createElement(CanvasOverviewSection,{draft:{sections:{agents:false}},id:'agents',title:'Agents',summary:'1 needs attention',onChange:()=>{}},'Observation detail'));
 assert.match(html,/<details[^>]+data-overview-section="agents"/);
 assert.doesNotMatch(html,/<details[^>]+open=""/);
 assert.match(html,/<summary[^>]*>.*Agents.*1 needs attention/s);
 assert.match(html,/Observation detail/);
});
test('overview includes actual content in named top-level disclosures',()=>{
 const html=renderToStaticMarkup(React.createElement(ChatOverview,{state,act:()=>{},changeDraft:()=>{}}));
 for(const id of ['outputs','sources','agents','workspace','activity'])assert.match(html,new RegExp('data-overview-section="'+id+'"'));
 assert.match(html,/Source\.txt/);assert.match(html,/Source check/);assert.match(html,/needs attention/);assert.match(html,/Workspace work/);
 assert.doesNotMatch(html,/role="region"/,'outer shell owns the single overview scroll region');
});
test('one pinned overview and explicit document list affordance even without open tabs',()=>{
 const html=renderToStaticMarkup(React.createElement(CanvasTabs,{state,act:()=>{},overview:true,onOverview:()=>{}}));
 assert.equal((html.match(/aria-label="Chat overview"/g)||[]).length,1);
 assert.match(html,/aria-label="Open documents, 0 open"/);
 assert.match(html,/a-canvas-document-tabs/);
 assert.doesNotMatch(html,/Back to chat|Open views and view actions/);
});
test('accepted document selection closes native modality before scheduling tab focus',()=>{
 const previous=globalThis.requestAnimationFrame,frames=[],events=[];
 globalThis.requestAnimationFrame=callback=>{events.push('schedule');frames.push(callback);return frames.length};
 try{
  const dialog={open:true,close(){events.push('close');this.open=false}};
  closeCanvasDialog(dialog,()=>{assert.equal(dialog.open,false,'Focus must not target the modal-inert background');events.push('focus')});
  assert.deepEqual(events,['close','schedule']);
  frames.shift()();
  assert.deepEqual(events,['close','schedule','focus']);
  // Deliberate old sequence: focus runs before the passive native close.
  dialog.open=true;
  const oldPath=()=>{globalThis.requestAnimationFrame(()=>assert.equal(dialog.open,false,'modal-inert'));};
  oldPath();assert.throws(()=>frames.shift()(),/modal-inert/);
 }finally{if(previous===undefined)delete globalThis.requestAnimationFrame;else globalThis.requestAnimationFrame=previous}
});

// Real CanvasTabs handlers/hooks with mocked host nodes and deterministic receipt
// and frame delivery. This does not substitute for the real browser focus oracle.
async function controlledTabs(Component,run){
 const keys=['window','document','requestAnimationFrame','IS_REACT_ACT_ENVIRONMENT'];
 const previous=Object.fromEntries(keys.map(key=>[key,globalThis[key]]));
 const listeners=new Map(),frames=[],calls=[],events=[];
 let focused=null,root,pending,model={...state,canvas:{open:true,id:'a',selectedVersion:2},canvasArtifacts:['a','b'].map(id=>({id,title:'Document '+id,kind:'markdown',tabOpen:true,sessionId:'chat-a',workspaceId:'workspace-a'}))};
 const nodes=new Map();
 const node=id=>{if(!nodes.has(id))nodes.set(id,{dataset:id==='overview'?{}:{artifactId:id},focus(){focused=this;events.push('focus:'+id)},closest(){return this},getBoundingClientRect(){return {left:0,right:100}}});return nodes.get(id)};
 const tabNodes=()=>['overview','a','b'].map(node);
 const strip={scrollLeft:0,querySelectorAll:()=>tabNodes().slice(1),getBoundingClientRect:()=>({left:0,right:300}),addEventListener(){},removeEventListener(){}};
 const tabs={querySelectorAll:tabNodes,querySelector:selector=>selector.includes(':focus')&&tabNodes().includes(focused)?focused:null};
 const dialog={open:false,showModal(){this.open=true;events.push('open')},close(){this.open=false;events.push('close')}};
 const trigger={focus(){focused=this;events.push('focus:trigger')}};
 globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 globalThis.window={addEventListener(){},removeEventListener(){}};
 globalThis.document={get activeElement(){return focused},addEventListener(type,fn){listeners.set(type,fn)},removeEventListener(type){listeners.delete(type)}};
 globalThis.requestAnimationFrame=fn=>{frames.push(fn);return frames.length};
 const dispatch=(action,args)=>{calls.push({action,args});return new Promise((resolve,reject)=>{pending={resolve,reject,args}})};
 const render=()=>React.createElement(Component,{state:model,dispatch});
 const findTab=id=>root.root.findAllByType('button').find(n=>id==='overview'?n.props['aria-label']==='Chat overview':n.props['data-artifact-id']===id);
 const key=async(value)=>{listeners.get('keydown')?.();await act(async()=>root.root.findByProps({role:'tablist'}).props.onKeyDown({key:value,target:focused,preventDefault(){}}))};
 const choose=async(id,modal=false)=>{
  if(modal){await act(async()=>root.root.findByProps({'aria-haspopup':'dialog'}).props.onClick());listeners.get('pointerdown')?.();
   await act(async()=>{root.root.findAllByType('li').find(n=>n.findAllByType('strong')[0].children.join('')==='Document '+id).findAllByType('button')[0].props.onClick()});
  }else{node(id).focus();listeners.get('keydown')?.();await act(async()=>{findTab(id).props.onClick()})}
 };
 const settle=async(receipt={accepted:true})=>{await act(async()=>{if(receipt.accepted){model={...model,canvas:{...model.canvas,id:pending.args.id}};root.update(render())}pending.resolve(receipt)})};
 const flush=async()=>{await act(async()=>{while(frames.length)frames.shift()()})};
 try{
  await act(async()=>{root=create(render(),{createNodeMock:element=>element.type==='dialog'?dialog:element.props.className==='a-canvas-document-tabs'?strip:element.props.role==='tablist'?tabs:element.props['aria-haspopup']==='dialog'?trigger:null})});
  await run({choose,key,settle,flush,calls,events,dialog,node,findTab,get focused(){return focused},newKey(){listeners.get('keydown')?.()},supersede(){listeners.get('pointerdown')?.();trigger.focus()},
   async cancel(){await act(async()=>root.root.findByType('dialog').props.onCancel({preventDefault(){}}))}});
 }finally{if(root)await act(async()=>root.unmount());for(const key of keys)if(previous[key]===undefined)delete globalThis[key];else globalThis[key]=previous[key]}
}
const retainsNewerHome=async Component=>controlledTabs(Component,async h=>{
 await h.choose('b');await h.key('Home');await h.settle();await h.flush();
 assert.equal(h.focused,h.node('overview'),'late acceptance must not steal newer Home focus');
 assert.equal(h.findTab('overview').props.tabIndex,0,'the focused tab remains the roving Tab stop');
 assert.equal(h.findTab('b').props['aria-selected'],true,'selection still commits without moving focus');
 assert.deepEqual(h.calls,[{action:'canvas.select',args:{id:'b'}}]);
});
test('actual direct CanvasTabs choose preserves newer Home across delayed receipt and frame',()=>retainsNewerHome(CanvasTabs));
test('actual choose negative control rejects the previous unconditional deferred-focus path',async()=>{
 const {CanvasTabs:OldTabs}=await vite.ssrLoadModule(oldId);
 await assert.rejects(()=>retainsNewerHome(OldTabs),/late acceptance must not steal newer Home focus/);
});
test('actual modal choose closes before focus and keeps an exact selected version',()=>controlledTabs(CanvasTabs,async h=>{
 await h.choose('a',true);await h.settle();assert.equal(h.dialog.open,false);await h.flush();
 assert.equal(h.focused,h.node('a'));assert.ok(h.events.indexOf('close')<h.events.indexOf('focus:a'));
 assert.deepEqual(h.calls,[{action:'canvas.select',args:{id:'a',version:2}}]);
}));
test('actual modal choose never overrides a newer input or Escape',async()=>{
 await controlledTabs(CanvasTabs,async h=>{await h.choose('b',true);await h.settle();h.supersede();await h.flush();assert.equal(h.events.at(-1),'focus:trigger');assert.ok(!h.events.includes('focus:b'))});
 await controlledTabs(CanvasTabs,async h=>{await h.choose('b',true);h.newKey();await h.settle();await h.flush();assert.equal(h.dialog.open,false);assert.ok(!h.events.includes('focus:b'))});
 await controlledTabs(CanvasTabs,async h=>{await h.choose('b',true);await h.cancel();await h.settle();await h.flush();assert.equal(h.events.at(-1),'focus:trigger');assert.equal(h.dialog.open,false)});
 await controlledTabs(CanvasTabs,async h=>{await h.choose('b',true);await h.settle({accepted:false,error:'Unsaved input'});await h.flush();assert.equal(h.dialog.open,true);assert.ok(!h.events.includes('focus:b'))});
});
test('receipt barrier accepts only the exact fully read accepted command',async()=>{
 const row={request:{id:'close-details'},status:200,body:{accepted:true}},entry={row,settled:Promise.resolve()};
 assert.equal(await acceptedCanvasReceipt(entry,'close-details'),row);
 await assert.rejects(()=>acceptedCanvasReceipt(entry,undefined),/must have an id/);
 await assert.rejects(()=>acceptedCanvasReceipt(entry,'different-command'),/clicked command/);
 for(const bad of [{body:{accepted:false}},{readError:'lost body'},{transportError:'disconnected'},{body:undefined}]){
  await assert.rejects(()=>acceptedCanvasReceipt({row:{...row,...bad},settled:Promise.resolve()},'close-details'));
 }
});
test('receipt barrier fails an unsettled body at the existing eight-second bound',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 const result=acceptedCanvasReceipt({row:{request:{id:'close-details'},status:200},settled:new Promise(()=>{})},'close-details');
 const failed=assert.rejects(result,/remained unsettled/);
 t.mock.timers.tick(8000);await failed;
});
test('all-receipt drain records and fails any unsettled response without clearing it',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 const row={request:{id:'late-action',action:'view.update'},status:200};
 const failed=assert.rejects(settleCanvasReceipts([{row,settled:new Promise(()=>{})}]),/Every observed receipt must settle/);
 t.mock.timers.tick(8000);await failed;assert.match(row.receiptTimeout,/view.update late-action/);assert.equal(row.body,undefined);
});
test('visibility acceptance rejects the actual optimistic projection and waits for the exact receipt',async()=>{
 const expected={commandId:'reopen',clientId:'client',hostInstanceId:'host',sessionId:'chat',canvasId:'document',open:true};
 const server={revision:5,client:{id:'client',hostInstanceId:'host'},selectedSessionId:'chat',canvas:{id:'document',open:false},view:{}};
 const pending=createPendingView();pending.addCanvas(server,true);
 const painted=pending.apply(server);
 assert.equal(painted.canvas.open,true,'The old DOM-only inference would already pass');
 assert.throws(()=>assertCanvasVisibility(painted,expected),/still awaits acceptance/);
 assert.throws(()=>assertCanvasVisibility(server,expected),/Server visibility/);
 let finish,reads=0;
 const row={request:{id:'reopen',action:'canvas.visibility',args:{sessionId:'chat',canvasId:'document',open:true}},status:200};
 const entry={row,settled:new Promise(resolve=>{finish=resolve})};
 const accepted={...server,revision:6,canvas:{...server.canvas,open:true}};
 const result=acceptedCanvasVisibility(entry,expected,async()=>{reads++;return accepted});
 await Promise.resolve();assert.equal(reads,0,'No authoritative-read success before the receipt settles');
 row.body={accepted:true,revision:6,hostInstanceId:'host'};finish();assert.equal((await result).snapshot,accepted);
 for(const changed of [{canvas:{...accepted.canvas,open:false}},{canvas:{...accepted.canvas,visibilityPending:true}},{revision:5},{client:{id:'other',hostInstanceId:'host'}},{selectedSessionId:'other'}]){
  await assert.rejects(()=>acceptedCanvasVisibility(entry,expected,async()=>({...accepted,...changed})));
 }
 let invalidReads=0;
 await assert.rejects(()=>acceptedCanvasVisibility({...entry,row:{...row,body:{...row.body,accepted:false}}},expected,async()=>{invalidReads++;return accepted}),/explicitly accept/);
 assert.equal(invalidReads,0,'A rejected receipt cannot qualify by server readback');
 await assert.rejects(()=>acceptedCanvasVisibility(entry,{...expected,commandId:'other'},async()=>accepted),/clicked command/);
});
