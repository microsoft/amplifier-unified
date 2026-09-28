import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {RecoveryNotice,recoveryPresentation}=await server.ssrLoadModule('/src/recovery-notice.jsx');
const {ConversationError}=await server.ssrLoadModule('/src/conversation-controls.jsx');
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
