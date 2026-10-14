import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {RecoveryNotice,recoveryPresentation,recoveryWorkerTarget}=await server.ssrLoadModule('/src/recovery-notice.jsx');
const {ConversationError}=await server.ssrLoadModule('/src/conversation-controls.jsx');
const {ChatOverview}=await server.ssrLoadModule('/src/work-overview.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const message={id:'notice',text:'Original recovery JSON',observation:{source:'local-job-recovery',recovery:{job_id:'job-1',call_id:'call-1',status:'returned',outcome:'tool_report_unverified'}}};
test('recovery presents recorded status without claiming verified success',()=>{
 const html=renderToStaticMarkup(React.createElement(RecoveryNotice,{message,session:{id:'chat'},state:{},act:()=>{}}));
 assert.match(html,/Result returned/);assert.match(html,/does not mean the work ran again/);
 assert.match(html,/<details><summary>Technical details/);assert.match(html,/Original recovery JSON/);
 assert.match(html,/does not independently verify/);assert.doesNotMatch(html,/Open worker chat|View saved result/);
 const uncertain=recoveryPresentation({...message,observation:{recovery:{status:'cancelled',outcome:'unconfirmed'}}},{});
 assert.equal(uncertain.status,'Work cancelled');assert.match(uncertain.explanation,/final outcome is unconfirmed/);
});
test('saved result and worker link require matching canonical call identity',()=>{
 const session={id:'chat',execution:{nodes:[{id:'tool',kind:'tool',toolCallId:'call-1',output:'Saved result',label:'Research'}]},workers:[{id:'worker',callId:'call-1',name:'Researcher'}]};
 const props={message,session,state:{sessions:[{id:'worker'}]},act:()=>{}};
 const html=renderToStaticMarkup(React.createElement(RecoveryNotice,props));
 assert.match(html,/View saved result/);assert.match(html,/Open worker chat/);assert.match(html,/Researcher/);
 assert.doesNotMatch(renderToStaticMarkup(React.createElement(RecoveryNotice,{...props,message:{...message,observation:{recovery:{call_id:'different'}}}})),/View saved result|Open worker chat/);
});
test('failure banner identifies the recorded incident and respects acknowledgement',()=>{
 const props={state:{},act:()=>{},session:{id:'chat',error:'Runtime failed',failure:{summary:'The manager turn failed.',recordedAt:1790517218.5,generationId:'failed-turn'}}};
 const html=renderToStaticMarkup(React.createElement(ConversationError,props));
 assert.match(html,/A turn stopped/);assert.match(html,/2026-09-27T13:53:38.500Z/);assert.match(html,/View error details/);
 assert.doesNotMatch(html,/Conversation stopped/);
 assert.equal(renderToStaticMarkup(React.createElement(ConversationError,{...props,state:{attention:{items:[{id:'session:chat',read:true}]}}})), '');
 assert.equal(renderToStaticMarkup(React.createElement(ConversationError,{...props,session:{id:'chat',status:'idle',failure:props.session.failure}})), '');
});
test('paged recovery keeps its worker identity when historical rows are deferred',()=>{
 const worker={id:'child',sessionId:'native-child',nativeProject:'project-a',name:'Earlier worker'};
 const saved={...message,recoveryWorker:worker},session={id:'chat',workers:[],workerSummary:{bounded:true,total:900}};
 assert.deepEqual(recoveryPresentation(saved,session).worker,worker);
 const html=renderToStaticMarkup(React.createElement(RecoveryNotice,{message:saved,session,state:{sessions:[{id:'child'}]},act:()=>{}}));
 assert.match(html,/Earlier worker/);assert.match(html,/Open worker chat/);
 assert.match(renderToStaticMarkup(React.createElement(RecoveryNotice,{message:saved,session,state:{sessions:[]},act:()=>{}})),/Open worker chat/);
});
test('finished-only worker summary remains visible without eager worker rows',()=>{
 const session={id:'chat',workers:[],workerSummary:{bounded:true,total:1000,finished:1000,
  recentFinished:[{id:'last',name:'Last saved worker',status:'completed'}]}};
 const html=renderToStaticMarkup(React.createElement(ChatOverview,{state:{selectedSessionId:'chat',sessions:[session],view:{}},act:()=>{},changeDraft:()=>{}}));
 assert.match(html,/1000.*finished observations/);assert.match(html,/Last saved worker/);
});


test('recovery navigation uses scoped canonical identity without a child catalog',async()=>{
 const session={id:'parent',nativeProject:'parent-project'},calls=[];
 const worker={id:'same-native-child',kind:'session',nativeProject:'exact-project',name:'Saved worker'};
 const state={browserResources:['selectedContext']};
 Object.defineProperty(state,'sessions',{get(){throw Error('A worker reference must not scan the catalog')}});
 let root;
 await renderAct(async()=>{root=create(React.createElement(RecoveryNotice,{message:{...message,recoveryWorker:worker},session,state,act:async(name,args)=>{calls.push({name,args})}}))});
 await renderAct(async()=>root.root.findByProps({'data-action':'session.select'}).props.onClick());
 assert.deepEqual(calls,[{name:'session.select',args:{id:'same-native-child',nativeProject:'exact-project'}}]);
 await renderAct(async()=>root.unmount());
 assert.deepEqual(recoveryWorkerTarget({id:'display-id',sessionId:'native-child'},session,state),{id:'native-child',nativeProject:'parent-project'});
 assert.equal(recoveryWorkerTarget({id:'same-native-child',kind:'job'},session,state),null);
 assert.equal(recoveryWorkerTarget({id:'untyped'},session,{sessions:[]}),null);
 assert.equal(recoveryWorkerTarget({id:'raw-native',kind:'session'}, {},{sessions:[]}),null,'No project proof means no unscoped native guess');
 assert.deepEqual(recoveryWorkerTarget({id:'saved-alias'},session,{sessions:[{id:'saved-alias',nativeProject:'alias-project'}]}),{id:'saved-alias',nativeProject:'alias-project'});
});
