import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ScheduleControls}=await server.ssrLoadModule('/src/schedules.jsx');
const {NotificationSettings}=await server.ssrLoadModule('/src/notification-settings.jsx');
test.after(()=>server.close());
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}};

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

test('notification secrets wait for confirmed completion and preserve edits per field',async()=>{
 let renderer,request,state={view:{}},sequence=0;
 const send=()=>{request=deferred();return request.promise};
 const render=()=>React.createElement(NotificationSettings,{state,act:send});
 await act(async()=>{renderer=create(render())});
 const inputs=()=>renderer.root.findAllByType('input').filter(row=>row.props.type==='password');
 const fill=(index,value)=>act(()=>inputs()[index].props.onChange({target:{value}}));
 const save=()=>renderer.root.findAllByType('button').find(row=>row.children.includes('Save notifications')).props.onClick();
 const complete=async(id,phase)=>{state={...state,actionStatus:{'notifications.save':{commandId:id,phase,error:phase==='error'?'Try again':undefined}}};await act(()=>renderer.update(render()))};
 try{
  await fill(0,'fixture-topic');await fill(1,'fixture-token');let submitted;
  for(const result of [undefined,{accepted:false}]){
   await act(()=>{submitted=save()});await act(async()=>{request.resolve(result);await submitted});
   assert.deepEqual(inputs().map(row=>row.props.value),['fixture-topic','fixture-token']);
  }
  await act(()=>{submitted=save()});await act(async()=>{request.resolve({accepted:true,operationId:'failed'});await submitted});
  await complete('failed','error');assert.deepEqual(inputs().map(row=>row.props.value),['fixture-topic','fixture-token']);
  await act(()=>{submitted=save()});await fill(0,'changed');await fill(0,'fixture-topic');
  await act(async()=>{request.resolve({accepted:true,operationId:'saved'});await submitted});
  assert.deepEqual(inputs().map(row=>row.props.value),['fixture-topic','fixture-token'],'Acknowledgment is not completion');
  await complete('saved','ready');
  assert.deepEqual(inputs().map(row=>row.props.value),['fixture-topic',''],'Only the unchanged field clears');
  await act(()=>{submitted=save()});await act(async()=>{request.resolve({accepted:true,operationId:'again'});await submitted});await complete('again','ready');
  assert.deepEqual(inputs().map(row=>row.props.value),['','']);
 }finally{await act(()=>renderer.unmount())}
});
