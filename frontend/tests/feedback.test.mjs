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
  return {accepted:true,state};
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
 async function action(name,args){calls.push({name,args});if(name==='view.update')await wait;return {accepted:true};}
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
 await renderAct(async()=>new Promise(resolve=>setTimeout(resolve,300)));
 const saved=calls.at(-1);assert.equal(saved.name,'view.update');assert.equal(saved.args.patch.feedbackDraft.title,'Edited title');assert.equal(saved.args.patch.feedbackDraft.category,'idea');assert.equal(saved.args.patch.feedbackDraft.includeDiagnostics,true);
 assert.match(JSON.stringify(root.toJSON()),/0.6.4/);assert.match(JSON.stringify(root.toJSON()),/Darwin/);
 await renderAct(async()=>root.unmount());
});

test('selected attachment IDs are frozen through lost acknowledgement and preview is agent-visible',async()=>{
 const file={id:'a'.repeat(32),name:'report.png',mime:'image/png',size:68,url:'/ignored'};
 let state=base(),root;state.view.feedbackDraft.attachments=[file];
 const calls=[];
 async function action(name,args){
  calls.push({name,args:structuredClone(args)});
  if(name==='view.update'){state={...state,view:{...state.view,...args.patch}};root.update(React.createElement(FeedbackPanel,{state,act:action}))}
  else throw new Error('Response lost');
  return {accepted:true,state};
 }
 await renderAct(async()=>{root=create(React.createElement(FeedbackPanel,{state,act:action}))});
 await renderAct(async()=>root.root.findByProps({'aria-label':'Preview report.png'}).props.onClick());
 await renderAct(async()=>new Promise(resolve=>setTimeout(resolve,300)));
 assert.equal(calls.at(-1).args.patch.feedbackDraft.previewId,file.id);
 assert.equal(root.root.findByProps({alt:'Preview of report.png'}).props.src,'/api/attachments/'+file.id);
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 const first=calls.find(call=>call.name==='feedback.submit').args;
 assert.deepEqual(first.attachmentIds,[file.id]);
 assert.equal(root.root.findAllByProps({'aria-label':'Remove report.png'}).length,0);
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 const sends=calls.filter(call=>call.name==='feedback.submit');assert.equal(sends.length,2);assert.deepEqual(sends[1].args,first);
 await renderAct(async()=>root.unmount());
});

test('an untrusted shared preview URL is never used as an image source',()=>{
 const state=base();state.view.feedbackDraft.attachments=[{id:'a'.repeat(32),name:'screenshot.png',size:1,mime:'image/png',url:'https://outside.example/collect'}];
 const html=renderToStaticMarkup(React.createElement(FeedbackPanel,{state,act:()=>{}}));
 assert.match(html,/\/api\/attachments\/aaaaaaaa/);assert.doesNotMatch(html,/outside\.example/);
 assert.match(html,/private repository/);assert.match(html,/24 MB total/);
});

 test('new feedback includes allowlisted diagnostics by default',()=>{
  const html=renderToStaticMarkup(React.createElement(FeedbackPanel,{state:{view:{},feedback:{requests:[]}},act:()=>{}}));
  assert.match(html,/checked=""/);assert.match(html,/Include reproduction diagnostics/);
 });
 test('slow acknowledgements do not overwrite newer typing or queue every keystroke',async()=>{
  let root,release;const calls=[];
  const state=base();
  async function action(name,args){calls.push(args);await new Promise(resolve=>release=resolve);return {accepted:true}}
  await renderAct(async()=>{root=create(React.createElement(FeedbackPanel,{state,act:action}))});
  const change=value=>root.root.findByProps({id:'feedback-title'}).props.onChange({target:{value}});
  await renderAct(async()=>{change('First edit');await new Promise(resolve=>setTimeout(resolve,300))});
  await renderAct(async()=>{change('Second edit');change('Third edit');change('Latest edit')});
  await renderAct(async()=>{root.update(React.createElement(FeedbackPanel,{state:{...state,view:{feedbackDraft:{...draft,title:'First edit'}}},act:action}))});
  assert.equal(root.root.findByProps({id:'feedback-title'}).props.value,'Latest edit');assert.equal(calls.length,1);
  await renderAct(async()=>{release();await new Promise(resolve=>setTimeout(resolve,10))});
  assert.equal(calls.length,2);assert.equal(calls[1].patch.feedbackDraft.title,'Latest edit');
  await renderAct(async()=>release());await renderAct(async()=>root.unmount());
 });
