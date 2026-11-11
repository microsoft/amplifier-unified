import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';

const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {Questions,QuestionNotice,activeQuestions}=await server.ssrLoadModule('/src/questions.jsx');
test.after(()=>server.close());
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const question=(status,patch={})=>({id:'question-one',revision:3,contentRevision:1,status:'answered',prompt:'Choose a path',dependency:'',required:true,options:[],allowFreeText:true,answer:{text:'Keep the saved answer',questionRevision:1},delivery:{status,inputId:'answer-input',message:'Startup detail'},...patch});
const session=q=>({id:'chat-one',questions:Array.isArray(q)?q:[q]});
const render=q=>renderToStaticMarkup(React.createElement(Questions,{session:session(q),dispatch:()=>{}}));
const notice=q=>renderToStaticMarkup(React.createElement(QuestionNotice,{session:session(q),paneRef:{current:null}}));
const button=(root,text)=>root.root.findAllByType('button').find(row=>row.children.join('')===text);

test('known rejected answer is saved but not delivered; unknown and absent receipts stay uncertain',()=>{
 const rejected=question('rejected'),before=structuredClone(rejected),html=render(rejected);
 assert.match(html,/Answer saved; not delivered\./);
 assert.match(html,/Keep the saved answer/);assert.match(html,/Startup detail/);
 assert.doesNotMatch(html,/Check delivery|Retry|Send again|Delivery is unconfirmed/);
 assert.match(notice(rejected),/An answer was saved but not delivered/);
 assert.deepEqual(rejected,before);
 for(const status of ['unknown',undefined,'unrecognized']){
  const q=question(status);
  assert.match(render(q),/Answer saved\. Delivery is unconfirmed; it has not been sent again\./);
  assert.doesNotMatch(render(q),/Answer saved; not delivered/);
  assert.match(notice(q),/An answer needs delivery confirmation/);
  assert.equal(render(q).includes('Check delivery'),status==='unknown');
 }
 assert.match(notice([rejected,question('unknown',{id:'other'})]),/Saved answers need delivery review/);
 assert.match(notice([rejected,question('rejected',{id:'other'})]),/2 answers were saved but not delivered/);
 assert.match(notice([rejected,question(undefined,{id:'pending',status:'pending'})]),/A question needs your answer/);
});

test('accepted moves to history, sending remains confirming, and rendering never dispatches',async()=>{
 for(const status of ['rejected','unknown','accepted','sending',undefined]){
  const q=question(status),before=structuredClone(q),calls=[];let root;
  await act(async()=>{root=create(React.createElement(Questions,{session:session(q),dispatch:(...args)=>calls.push(args)}))});
  assert.deepEqual(calls,[]);assert.deepEqual(q,before);
  assert.equal(activeQuestions(session(q)).length,status==='accepted'?0:1);
  if(status==='accepted'){
   assert.equal(root.toJSON(),null);assert.equal(notice(q),'');
   await act(async()=>root.update(React.createElement(Questions,{session:session(q),history:true,dispatch:(...args)=>calls.push(args)})));
   assert.match(JSON.stringify(root.toJSON()),/Answer saved and delivered to Amplifier/);
   assert.doesNotMatch(JSON.stringify(root.toJSON()),/Startup detail/);
  }
  if(status==='sending')assert.match(JSON.stringify(root.toJSON()),/Confirming delivery/);
  await act(async()=>root.unmount());assert.deepEqual(calls,[]);
 }
});

test('unknown delivery check stays explicit, busy-disabled, exact-bound and never resends',async()=>{
 let root,finish;const calls=[],q=question('unknown'),before=structuredClone(q);
 const dispatch=(...args)=>{calls.push(args);return new Promise(resolve=>finish=resolve)};
 await act(async()=>{root=create(React.createElement(Questions,{session:session(q),dispatch}))});
 assert.equal(calls.length,0);
 await act(async()=>{button(root,'Check delivery').props.onClick()});
 assert.deepEqual(calls,[['conversation.delivery',{sessionId:'chat-one',inputId:'answer-input'}]]);
 assert.equal(button(root,'Checking delivery…').props.disabled,true);
 await act(async()=>button(root,'Checking delivery…').props.onClick());
 assert.equal(calls.length,1);
 await act(async()=>finish({accepted:true,result:{delivery:'unknown'}}));
 assert.match(JSON.stringify(root.toJSON()),/Delivery is still unconfirmed/);
 assert.equal(button(root,'Check delivery').props.disabled,false);
 assert.deepEqual(q,before);
 await act(async()=>root.unmount());
});

test('pending answer keeps form submission, empty and busy fences, original identity, and optional skip',async()=>{
 let root,finish;const calls=[];
 const q=question(undefined,{status:'pending',answer:undefined,options:[{id:'keep',label:'Keep'}],required:false});
 const dispatch=(...args)=>{calls.push(args);return new Promise(resolve=>finish=resolve)};
 await act(async()=>{root=create(React.createElement(Questions,{session:session(q),dispatch}))});
 assert.equal(button(root,'Submit answer').props.disabled,true);
 assert.equal(button(root,'Skip question').props.disabled,false);
 await act(async()=>root.root.findByType('textarea').props.onChange({target:{value:'Keyboard answer'}}));
 // The form owns native keyboard submission; no new key handler or action.
 await act(async()=>{root.root.findByType('form').props.onSubmit({preventDefault(){}})});
 assert.equal(root.root.findByType('fieldset').props.disabled,true);
 assert.equal(button(root,'Skip question').props.disabled,true);
 assert.equal(calls.length,1);
 assert.equal(calls[0][0],'question.answer');
 assert.deepEqual(calls[0][1],{sessionId:'chat-one',id:'question-one',expectedRevision:3,text:'Keyboard answer'});
 assert.equal(typeof calls[0][2].id,'string');
 await act(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 assert.equal(calls.length,1);
 await act(async()=>finish({accepted:true}));
 assert.equal(root.root.findByType('fieldset').props.disabled,false);
 await act(async()=>root.unmount());
});

for(const status of ['unknown','rejected'])test(`${status} reminder clearance is explicit exact-bound and durable, never a local mute or delivery change`,async()=>{
 let root,finish;const calls=[],q=question(status),before=structuredClone(q);
 const dispatch=(...args)=>{calls.push(args);return new Promise(resolve=>finish=resolve)};
 await act(async()=>{root=create(React.createElement(Questions,{session:session(q),dispatch}))});
 assert.match(render(q),/Clear reminder/);
 assert.match(render(q),/Delivery status stays unchanged; nothing is sent again/);
 assert.deepEqual(calls,[]);
 const click=button(root,'Clear reminder').props.onClick;
 await act(async()=>{click();click()});
 assert.equal(calls.length,1);
 assert.equal(calls[0][0],'question.dismiss');
 assert.deepEqual(calls[0][1],{sessionId:'chat-one',id:'question-one',expectedRevision:3,inputId:'answer-input'});
 assert.equal(calls[0][2].propagateError,true);
 assert.equal(typeof calls[0][2].id,'string');
 assert.equal(button(root,'Please wait…').props.disabled,true);
 if(status==='unknown')assert.equal(button(root,'Check delivery').props.disabled,true);
 await act(async()=>finish({accepted:true}));
 assert.ok(root.toJSON(),'an admission without projection must not locally hide a reminder');
 assert.deepEqual(q,before);
 const saved={...q,revision:4,reminderDismissal:{inputId:'answer-input',dismissedAt:123,origin:'ui',clientId:'fixture'}};
 await act(async()=>root.update(React.createElement(Questions,{session:session(saved),dispatch})));
 assert.equal(root.toJSON(),null);
 assert.equal(notice(saved),'');
 assert.deepEqual(activeQuestions(session(saved)),[]);
 await act(async()=>root.unmount());
 // Fresh rendering from the saved host projection, not browser session storage.
 const history=renderToStaticMarkup(React.createElement(Questions,{session:session(saved),dispatch,history:true}));
 assert.match(history,/Reminder cleared/);
 assert.match(history,/Keep the saved answer/);
 assert.match(history,/Original question &amp; provenance/);
 assert.match(history,status==='unknown'?/Delivery is unconfirmed/:/Answer saved; not delivered/);
 assert.doesNotMatch(history,/Clear reminder|data-action="question.answer"|data-action="question.cancel"/);
 assert.equal(calls.length,1);
});

test('only answered unknown/rejected with an exact input can clear; pending required and sending remain active',()=>{
 for(const q of [question('sending'),question('accepted'),question(undefined),question('unknown',{status:'pending'}),question('unknown',{delivery:{status:'unknown'}})]){
  assert.doesNotMatch(render(q),/Clear reminder/);
 }
 for(const patch of [{inputId:'wrong'},{}]){
  const q=question('unknown',{reminderDismissal:patch});
  assert.equal(activeQuestions(session(q)).length,1);
  assert.match(notice(q),/delivery confirmation/);
 }
 const pending=question('unknown',{status:'pending',reminderDismissal:{inputId:'answer-input'}});
 assert.equal(activeQuestions(session(pending)).length,1);
 assert.match(notice(pending),/needs your answer/);
 const laterAccepted=question('accepted',{reminderDismissal:{inputId:'answer-input'}});
 assert.equal(notice(laterAccepted),'');
 const html=renderToStaticMarkup(React.createElement(Questions,{session:session(laterAccepted),dispatch:()=>{},history:true}));
 assert.match(html,/saved and delivered/);assert.match(html,/Reminder cleared/);
});

test('clear refusal/unknown keeps reminder truthful; only uncertainty reuses stable command identity',async()=>{
 for(const failure of ['refusal','transport']){
  let root;const calls=[],q=question('unknown');
  const dispatch=async(...args)=>{calls.push(args);throw Object.assign(Error('Fixture '+failure),failure==='refusal'?{accepted:false}:{unconfirmed:true})};
  await act(async()=>{root=create(React.createElement(Questions,{session:session(q),dispatch}))});
  await act(async()=>button(root,'Clear reminder').props.onClick());
  assert.match(JSON.stringify(root.toJSON()),new RegExp('Fixture '+failure));
  assert.equal(activeQuestions(session(q)).length,1);
  assert.equal(button(root,'Clear reminder').props.disabled,false);
  assert.equal(calls.length,1);
  await act(async()=>button(root,'Clear reminder').props.onClick());
  assert.deepEqual(calls[0].slice(0,2),calls[1].slice(0,2));
  if(failure==='transport')assert.equal(calls[0][2].id,calls[1][2].id);
  else assert.notEqual(calls[0][2].id,calls[1][2].id);
  await act(async()=>root.unmount());
 }
});

for(const change of ['revision','input','question','session','accepted','unmount'])test(`clear gesture cannot consume stale ${change} identity`,async()=>{
 let root;const calls=[],q=question('unknown'),dispatch=async(...args)=>{calls.push(args);return{accepted:true}};
 await act(async()=>{root=create(React.createElement(Questions,{session:session(q),dispatch}))});
 const stale=button(root,'Clear reminder').props.onClick;
 const next=change==='revision'?{...q,revision:4}:change==='input'?{...q,delivery:{...q.delivery,inputId:'replacement'}}:change==='question'?{...q,id:'other'}:change==='accepted'?{...q,delivery:{...q.delivery,status:'accepted'}}:q;
 if(change==='unmount')await act(async()=>root.unmount());
 else await act(async()=>root.update(React.createElement(Questions,{session:{...session(next),id:change==='session'?'chat-two':'chat-one'},dispatch})));
 await act(async()=>stale());
 assert.deepEqual(calls,[]);
 if(change!=='unmount')await act(async()=>root.unmount());
});