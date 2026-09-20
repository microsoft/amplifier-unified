import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {FeedbackFollowup}=await server.ssrLoadModule('/src/feedback-followup.jsx');
test.after(()=>server.close());
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const base=()=>({view:{feedbackFollowupDraft:{feedbackId:'original-feedback',body:'Extra details'}},feedback:{requests:[{requestId:'original-feedback',title:'Original report',status:'submitted'}],followups:[]}});

test('comment retry and remount reuse frozen identity and reviewed text after lost response',async()=>{
 let state=base(),root;const calls=[];
 const action=async(name,args)=>{
  calls.push({name,args:structuredClone(args)});
  if(name==='view.update'){
   state={...state,view:{...state.view,...args.patch}};
   root.update(React.createElement(FeedbackFollowup,{state,act:action}));return {accepted:true,state};
  }
  throw Error('Lost acknowledgement');
 };
 await renderAct(async()=>{root=create(React.createElement(FeedbackFollowup,{state,act:action}))});
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 const initial=calls.find(call=>call.name==='feedback.comment').args;
 assert.equal(initial.body,'Extra details');assert.ok(initial.requestId);
 assert.equal(root.root.findByProps({id:'feedback-comment'}).props.disabled,true);
 await renderAct(async()=>root.unmount());
 await renderAct(async()=>{root=create(React.createElement(FeedbackFollowup,{state,act:action}))});
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 assert.deepEqual(calls.filter(call=>call.name==='feedback.comment').map(call=>call.args),[initial,initial]);
 await renderAct(async()=>root.unmount());
});

test('report refresh uses shared read action; unknown outcomes have no send-again button',async()=>{
 let root;const calls=[],state=base();
 await renderAct(async()=>{root=create(React.createElement(FeedbackFollowup,{state,act:async(name,args)=>{calls.push({name,args});return {accepted:true}}}))});
 await renderAct(async()=>root.root.findAllByType('button').find(node=>node.children.includes('Refresh report')).props.onClick());
 const read=calls.find(call=>call.name==='feedback.get');
 assert.equal(read.args.feedbackId,'original-feedback');assert.equal(read.args.page,1);
 const pending={requestId:'frozen-comment',feedbackId:'original-feedback',body:'Extra details'};
 const next={...state,view:{feedbackFollowupDraft:{...state.view.feedbackFollowupDraft,pending}},feedback:{...state.feedback,followups:[{...pending,action:'feedback.comment',status:'unknown',url:'https://github.com/bkrabach/amplifier-unified/issues/42',message:'Check the issue'}]}};
 await renderAct(async()=>root.update(React.createElement(FeedbackFollowup,{state:next,act:async()=>({accepted:true})})));
 assert.equal(root.root.findAllByType('button').filter(node=>node.props.type==='submit').length,0);
 assert.ok(root.root.findAllByType('a').some(node=>node.props.href.endsWith('/42')));
 await renderAct(async()=>root.unmount());
});
