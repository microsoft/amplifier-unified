import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ConversationLibrary}=await server.ssrLoadModule('/src/conversation-library.jsx');
const {ScheduleControls}=await server.ssrLoadModule('/src/schedules.jsx');
test.after(()=>server.close());
const event={preventDefault(){}};
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}};

test('collection creation clears only acknowledged, unchanged input, including same-text later edits',async()=>{
 let renderer,request;
 const send=()=>{request=deferred();return request.promise};
 await act(async()=>{renderer=create(React.createElement(ConversationLibrary,{state:{},session:{id:'chat'},act:send}))});
 const input=()=>renderer.root.findByProps({id:'new-chat-collection'});
 const fill=value=>act(()=>input().props.onChange({target:{value}}));
 try{
  await fill('First');let submitted;
  await act(()=>{submitted=renderer.root.findByType('form').props.onSubmit(event)});
  await fill('Different');await fill('First');
  await act(async()=>{request.resolve({accepted:true});await submitted});
  assert.equal(input().props.value,'First','A late success cannot erase newly typed identical text');
  await act(()=>{submitted=renderer.root.findByType('form').props.onSubmit(event)});
  await act(async()=>{request.resolve({accepted:false});await submitted});
  assert.equal(input().props.value,'First','Rejected changes preserve the draft');
  await act(()=>{submitted=renderer.root.findByType('form').props.onSubmit(event)});
  await act(async()=>{request.resolve({accepted:true});await submitted});
  assert.equal(input().props.value,'');
 }finally{await act(()=>renderer.unmount())}
});

test('schedule completion preserves newer drafts and stale previews never become approval',async()=>{
 let renderer,request,holdPreview=false;
 const send=(action,args)=>{
  if(action==='schedule.preview'&&!holdPreview)return Promise.resolve({accepted:true,result:{previewHash:'fixture',binding:{...args,spec:{timezone:'UTC'}},occurrences:[{utc:'one',dueAt:1}]}});
  request=deferred();return request.promise;
 };
 await act(async()=>{renderer=create(React.createElement(ScheduleControls,{session:{id:'chat'},act:send}))});
 const input=()=>renderer.root.findByProps({id:'schedule-prompt'});
 const fill=value=>act(()=>input().props.onChange({target:{value}}));
 const click=action=>renderer.root.findByProps({'data-action':action}).props.onClick();
 try{
  await fill('First schedule');await act(async()=>{await click('schedule.preview')});let submitted;
  await act(()=>{submitted=click('schedule.create')});await fill('A different schedule');
  await act(async()=>{request.resolve({accepted:true});await submitted});
  assert.equal(input().props.value,'A different schedule');
  assert.equal(renderer.root.findAllByProps({'data-part':'schedule-preview'}).length,0);
  await act(async()=>{await click('schedule.preview')});await act(()=>{submitted=click('schedule.create')});
  await act(async()=>{request.resolve(undefined);await submitted});assert.equal(input().props.value,'A different schedule');
  await act(()=>{submitted=click('schedule.create')});await act(async()=>{request.resolve({accepted:true});await submitted});assert.equal(input().props.value,'');
  holdPreview=true;await fill('Preview this');await act(()=>{submitted=click('schedule.preview')});await fill('Changed before preview');
  await act(async()=>{request.resolve({result:{previewHash:'old',binding:{prompt:'Preview this',spec:{timezone:'UTC'}},occurrences:[{utc:'one',dueAt:1}]}});await submitted});
  assert.equal(renderer.root.findAllByProps({'data-part':'schedule-preview'}).length,0);
 }finally{await act(()=>renderer.unmount())}
});
