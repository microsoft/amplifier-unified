import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {FeedbackPanel}=await server.ssrLoadModule('/src/feedback.jsx');
test.after(()=>server.close());
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const draft={title:'Canvas bug',body:'A preview stopped responding.',category:'bug',includeDiagnostics:false};
const base=()=>({view:{feedbackDraft:{...draft}},feedback:{requests:[],diagnostics:{appVersion:'0.6.4',osFamily:'Darwin'}}});

test('feedback defaults to reviewed text without diagnostics or private-state attachments',()=>{
 const html=renderToStaticMarkup(React.createElement(FeedbackPanel,{state:base(),act:()=>{}}));
 assert.match(html,/Canvas bug/);assert.match(html,/A preview stopped responding/);
 assert.match(html,/type="checkbox"/);assert.doesNotMatch(html,/checked=""/);
 assert.match(html,/credentials are not attached/);assert.doesNotMatch(html,/Included: app/);
 assert.match(html,/bkrabach\/amplifier-unified/);
});

test('a lost acknowledgement keeps the exact request and frozen text for explicit retry',async()=>{
 let state=base(),root;const calls=[];
 async function action(name,args){
  calls.push({name,args:structuredClone(args)});
  if(name==='view.update'){
   state={...state,view:{...state.view,...args.patch}};
   root.update(React.createElement(FeedbackPanel,{state,act:action}));
  }else throw new Error('Network response lost');
 }
 await renderAct(async()=>{root=create(React.createElement(FeedbackPanel,{state,act:action}))});
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 const first=calls.find(call=>call.name==='feedback.submit').args;
 assert.ok(first.requestId);assert.equal(first.body,draft.body);
 assert.equal(root.root.findByProps({id:'feedback-body'}).props.disabled,true);
 assert.match(JSON.stringify(root.toJSON()),/Check submission/);
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 const submissions=calls.filter(call=>call.name==='feedback.submit');
 assert.equal(submissions.length,2);assert.deepEqual(submissions[1].args,first);
 await renderAct(async()=>root.unmount());
});

test('request payload is frozen before waiting for draft persistence and double clicks do not send twice',async()=>{
 let release,root;const calls=[];const wait=new Promise(resolve=>release=resolve);
 async function action(name,args){calls.push({name,args});if(name==='view.update')await wait;}
 await renderAct(async()=>{root=create(React.createElement(FeedbackPanel,{state:base(),act:action}))});
 let submit;
 await renderAct(async()=>{submit=root.root.findByType('form').props.onSubmit({preventDefault(){}})});
 assert.equal(root.root.findByProps({id:'feedback-body'}).props.disabled,true);
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 await renderAct(async()=>{release();await submit});
 const sends=calls.filter(call=>call.name==='feedback.submit');assert.equal(sends.length,1);assert.equal(sends[0].args.body,draft.body);
 await renderAct(async()=>root.unmount());
});

test('durable success and uncertainty render meaningful results without allowing a repost',()=>{
 for(const status of ['submitted','unknown']){
  const requestId='fixture-123';
  const state=base();state.view.feedbackDraft.pending={...draft,requestId};
  state.feedback.requests=[{requestId,status,message:status==='submitted'?'Feedback sent. Thank you.':'GitHub may have received this feedback.',...(status==='submitted'?{url:'https://github.com/bkrabach/amplifier-unified/issues/42'}:{})}];
  const html=renderToStaticMarkup(React.createElement(FeedbackPanel,{state,act:()=>{}}));
  assert.match(html,/New feedback/);assert.doesNotMatch(html,/>Check submission</);
  if(status==='submitted'){assert.match(html,/a-check-result success/);assert.match(html,/View issue/)}
  else {assert.match(html,/a-check-result error/);assert.match(html,/Check repository issues/)}
 }
});

test('every editable form control publishes shared view state, including optional diagnostics',async()=>{
 const calls=[];let root;
 await renderAct(async()=>{root=create(React.createElement(FeedbackPanel,{state:base(),act:async(name,args)=>calls.push({name,args})}))});
 await renderAct(async()=>root.root.findByProps({id:'feedback-title'}).props.onChange({target:{value:'Edited title'}}));
 await renderAct(async()=>root.root.findByProps({id:'feedback-category'}).props.onChange({target:{value:'idea'}}));
 const checkbox=root.root.findAll(node=>node.type==='input'&&node.props.type==='checkbox')[0];
 await renderAct(async()=>checkbox.props.onChange({target:{checked:true}}));
 const saved=calls.at(-1);assert.equal(saved.name,'view.update');assert.equal(saved.args.patch.feedbackDraft.title,'Edited title');assert.equal(saved.args.patch.feedbackDraft.category,'idea');assert.equal(saved.args.patch.feedbackDraft.includeDiagnostics,true);
 assert.match(JSON.stringify(root.toJSON()),/0.6.4/);assert.match(JSON.stringify(root.toJSON()),/Darwin/);
 await renderAct(async()=>root.unmount());
});
