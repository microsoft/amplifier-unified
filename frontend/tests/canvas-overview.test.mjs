import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
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
