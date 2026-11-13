import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {NewChatHandoffs}=await server.ssrLoadModule('/src/new-chat-handoffs.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
let root;
test.afterEach(async()=>{await act(async()=>root?.unmount());root=null});test.after(()=>server.close());
const row={requestId:'request',sourceSessionId:'source',workspace:'/project/B',title:'Read the findings',text:'Read FINDINGS.md, then wait. <script>untrusted</script>',bundle:'work',selection:{instance:'provider',model:'model'},reviewHash:'exact-review',status:'pending',revision:1,message:'Review before creating the chat.'};
const state=(entry=row,selectedSessionId='source')=>({selectedSessionId,handoffs:Array.isArray(entry)?entry:[entry]});
const view=(current,dispatch)=>React.createElement(NewChatHandoffs,{state:current,dispatch});
const button=action=>root.root.findAllByType('button').find(b=>b.props['data-action']===action);
const text=()=>JSON.stringify(root.toJSON());
const history=()=>root.root.findAllByProps({className:'a-handoffs-past'});
const pastIds=()=>history().flatMap(node=>node.findAllByType('article').map(card=>card.props['data-handoff-id']));
const activeIds=()=>root.root.findAllByProps({className:'a-handoff'}).map(card=>card.props['data-handoff-id']);
const accepted={...row,status:'accepted',creation:'created',delivery:'accepted',targetSessionId:'target',revision:3,message:'Initial brief accepted. This does not mean its requested work has completed.'};

test('review is demand-only, shows exact plain brief and hides unrelated source requests',async()=>{
 let calls=0;await act(async()=>{root=create(view(state(),()=>{calls++}))});
 assert.equal(calls,0);assert.match(text(),/FINDINGS.md/);assert.match(text(),/\/project\/B/);
 assert.equal(root.root.findAllByType('script').length,0);
 assert.equal(root.root.findByType('pre').children.join(''),row.text);
 assert.equal(button('session.select'),undefined);
 await act(async()=>root.update(view(state(row,'other'),()=>{calls++})));
 assert.equal(root.toJSON(),null);assert.equal(calls,0);
});
test('double click creates once and acceptance never navigates or sends from the browser',async()=>{
 const calls=[];let finish;
 const dispatch=(action,args)=>{calls.push({action,args});return new Promise(resolve=>{finish=resolve})};
 await act(async()=>{root=create(view(state(),dispatch))});
 await act(async()=>{button('session.handoff.confirm').props.onClick();button('session.handoff.confirm').props.onClick()});
 assert.deepEqual(calls,[{action:'session.handoff.confirm',args:{requestId:'request',reviewHash:'exact-review'}}]);
 await act(async()=>finish({accepted:true,result:{...row,revision:3,status:'accepted',creation:'created',targetSessionId:'target',message:'Opening message accepted.'}}));
 assert.equal(button('session.handoff.confirm'),undefined);assert.equal(calls.length,1);
 await act(async()=>{button('session.select').props.onClick()});
 assert.deepEqual(calls[1],{action:'session.select',args:{id:'target'}});
 await act(async()=>finish({accepted:true}));
});
test('lost acknowledgement offers a read, not another create, and newer server receipt wins',async()=>{
 const calls=[];
 const accepted={...row,revision:3,status:'accepted',creation:'created',targetSessionId:'target',message:'Opening message accepted.'};
 const dispatch=async(action,args)=>{calls.push({action,args});if(action.endsWith('.confirm'))throw Error('Connection lost');return {accepted:true,result:accepted}};
 await act(async()=>{root=create(view(state(),dispatch))});
 await act(async()=>button('session.handoff.confirm').props.onClick());
 assert.equal(button('session.handoff.confirm'),undefined);assert.ok(button('session.handoff.read'));
 await act(async()=>button('session.handoff.read').props.onClick());
 assert.equal(calls.length,2);assert.equal(calls[1].action,'session.handoff.read');assert.ok(button('session.select'));
 await act(async()=>root.update(view(state({...accepted,revision:4,status:'unknown',message:'Delivery remains unconfirmed.'}),dispatch)));
 assert.match(text(),/Delivery remains unconfirmed/);assert.equal(calls.length,2);
});
test('only a definitive pending read re-enables confirmation after lost response',async()=>{
 const calls=[];const dispatch=async(action)=>{calls.push(action);if(action.endsWith('.confirm'))throw Error('Connection lost');return {accepted:true,result:row}};
 await act(async()=>{root=create(view(state(),dispatch))});
 await act(async()=>button('session.handoff.confirm').props.onClick());assert.equal(button('session.handoff.confirm'),undefined);
 await act(async()=>button('session.handoff.read').props.onClick());assert.ok(button('session.handoff.confirm'));assert.equal(calls.length,2);
});
test('cancellation sends no creation or navigation and terminal rows remain inspectable',async()=>{
 const calls=[];const dispatch=async(action,args)=>{calls.push({action,args});return {accepted:true,result:{...row,revision:2,status:'cancelled',message:'Nothing was created.'}}};
 await act(async()=>{root=create(view(state(),dispatch))});
 await act(async()=>button('session.handoff.cancel').props.onClick());
 assert.deepEqual(calls,[{action:'session.handoff.cancel',args:{requestId:'request'}}]);assert.match(text(),/Nothing was created/);assert.equal(button('session.handoff.confirm'),undefined);assert.equal(button('session.select'),undefined);
 assert.deepEqual(pastIds(),['request']);assert.equal(history()[0].props.open,undefined);
});

test('one native collapsed history contains only known settled receipts; attention stays outside',async()=>{
 const settled=Array.from({length:6},(_,index)=>({...accepted,requestId:`accepted-${index}`,title:`Past chat ${index}`}));
 const cancelled={...row,requestId:'cancelled',status:'cancelled',creation:'not_started',delivery:'not_started'};
 const statuses=['pending','unknown','failed','creating','created','delivering','future-status',''];
 const attention=statuses.map(status=>({...row,requestId:`attention-${status}`,status}));
 const contradictions=[
  {...accepted,requestId:'unknown-delivery',delivery:'unknown'},
  {...accepted,requestId:'future-delivery',delivery:'future-state'},
  {...accepted,requestId:'unknown-creation',creation:'unknown'},
  {...cancelled,requestId:'cancelled-but-created',creation:'created'},
 ];
 let calls=0;
 const current=state([...settled,cancelled,...attention,...contradictions,{...accepted,requestId:'foreign',sourceSessionId:'other'}]);
 await act(async()=>{root=create(view(current,()=>{calls++}))});
 assert.equal(history().length,1);assert.equal(history()[0].props.open,undefined);
 assert.deepEqual(pastIds(),[...settled,cancelled].map(entry=>entry.requestId));
 assert.deepEqual(activeIds(),[...attention,...contradictions].map(entry=>entry.requestId));
 assert.match(text(),/Opening brief accepted/);assert.match(text(),/Opening brief acceptance is not work completion/);
 assert.equal(root.root.findAllByProps({'data-handoff-id':'foreign'}).length,0);
 assert.equal(button('session.handoff.read').children.join(''),'Check delivery');
 await act(async()=>root.update(view({...current,revision:100},()=>{calls++})));
 assert.equal(calls,0,'Rendering and fresh stream state never check, create, send or navigate');
});

test('manual read regroups locally without waiting for a parent snapshot',async()=>{
 const unknown={...accepted,status:'unknown',delivery:'unknown',revision:2};
 const calls=[];const dispatch=async(action,args,options)=>{calls.push({action,args,options});return {accepted:true,result:accepted}};
 await act(async()=>{root=create(view(state(unknown),dispatch))});
 assert.deepEqual(activeIds(),['request']);assert.equal(history().length,0);
 await act(async()=>button('session.handoff.read').props.onClick());
 assert.deepEqual(activeIds(),[]);assert.deepEqual(pastIds(),['request']);
 assert.deepEqual(calls,[{action:'session.handoff.read',args:{requestId:'request'},options:{feedback:false}}]);
 await act(async()=>root.update(view(state(unknown),dispatch)));
 assert.deepEqual(pastIds(),['request']);assert.equal(calls.length,1);
});

test('a late older read cannot hide a newer stream failure; equal revisions also favor the stream',async()=>{
 let finish;const dispatch=()=>new Promise(resolve=>{finish=resolve});
 await act(async()=>{root=create(view(state({...accepted,status:'unknown',revision:2}),dispatch))});
 await act(async()=>{button('session.handoff.read').props.onClick()});
 const newer={...accepted,revision:5,status:'failed',delivery:'failed',message:'Newer stream failure'};
 await act(async()=>root.update(view(state(newer),dispatch)));
 await act(async()=>finish({accepted:true,result:accepted}));
 assert.deepEqual(activeIds(),['request']);assert.equal(history().length,0);
 assert.match(text(),/Newer stream failure/);
 await act(async()=>{button('session.handoff.read').props.onClick()});
 await act(async()=>finish({accepted:true,result:{...accepted,revision:5}}));
 assert.equal(history().length,0);assert.match(text(),/Newer stream failure/);
});

test('read errors stay prominent even if the stream settles; Open chat failure is not hidden in history',async()=>{
 let finish;
 const dispatch=()=>new Promise((resolve,reject)=>{finish={resolve,reject}});
 await act(async()=>{root=create(view(state({...accepted,status:'unknown',delivery:'unknown',revision:2}),dispatch))});
 await act(async()=>{button('session.handoff.read').props.onClick()});
 await act(async()=>root.update(view(state(accepted),dispatch)));
 await act(async()=>finish.reject(Error('Delivery read unavailable')));
 assert.deepEqual(activeIds(),['request']);assert.equal(history().length,0);
 assert.equal(root.root.findByProps({role:'alert'}).children.join(''),'Delivery read unavailable');
 await act(async()=>{button('session.handoff.read').props.onClick()});
 assert.match(text(),/Delivery read unavailable/,'Keep the error until a read confirms a result');
 await act(async()=>finish.resolve({accepted:true,result:accepted}));
 assert.deepEqual(pastIds(),['request']);
 await act(async()=>{button('session.select').props.onClick()});
 await act(async()=>finish.reject(Error('Navigation blocked')));
 assert.deepEqual(activeIds(),['request']);assert.equal(history().length,0);
 assert.equal(root.root.findByProps({role:'alert'}).children.join(''),'Navigation blocked');
});

test('unconfirmed or mismatched reads cannot hide uncertainty or re-enable a lost confirmation',async()=>{
 let result;
 const dispatch=async(action)=>{if(action.endsWith('.confirm'))throw Error('Connection lost');return result};
 await act(async()=>{root=create(view(state(),dispatch))});
 await act(async()=>button('session.handoff.confirm').props.onClick());
 for(const invalid of [
  {accepted:false,result:accepted},
  {accepted:true,result:{...accepted,requestId:'other'}},
  {accepted:true,result:{...accepted,sourceSessionId:'other'}},
  {accepted:true,result:{...accepted,revision:undefined}},
 ]){
  result=invalid;
  await act(async()=>button('session.handoff.read').props.onClick());
  assert.equal(history().length,0);assert.equal(button('session.handoff.confirm'),undefined);
  assert.match(text(),/could not be confirmed/);
 }
});

test('a changed source discards local reads and cannot adopt a late result for the former source',async()=>{
 let finish;
 const dispatch=()=>new Promise(resolve=>{finish=resolve});
 const current={...state({...accepted,status:'unknown',delivery:'unknown',revision:2}),client:{id:'client',hostInstanceId:'host',historyScope:'A'}};
 await act(async()=>{root=create(view(current,dispatch))});
 await act(async()=>{button('session.handoff.read').props.onClick()});
 await act(async()=>root.update(view({...current,client:{...current.client,historyScope:'B'}},dispatch)));
 await act(async()=>finish({accepted:true,result:{...accepted,revision:100}}));
 assert.deepEqual(activeIds(),['request']);assert.equal(history().length,0);
 await act(async()=>{button('session.handoff.read').props.onClick()});
 await act(async()=>finish({accepted:true,result:accepted}));
 assert.deepEqual(pastIds(),['request']);
 await act(async()=>root.update(view(current,dispatch)));
 assert.deepEqual(activeIds(),['request']);assert.equal(history().length,0);
});

test('Open chat keeps its scoped navigation fence until acceptance, refusal or unknown settles',async()=>{
 const calls=[];let finish;
 const dispatch=(action,args,meta)=>{calls.push({action,args,meta});return new Promise((resolve,reject)=>{finish={resolve,reject}})};
 await act(async()=>{root=create(view(state(accepted),dispatch))});
 await act(async()=>{button('session.select').props.onClick();button('session.select').props.onClick()});
 assert.equal(calls.length,1);assert.equal(calls[0].action,'session.select');
 assert.deepEqual(calls[0].args,{id:'target'});assert.equal(calls[0].meta.handoffIsCurrent(),true);
 assert.equal(button('session.select').props.disabled,true);
 await act(async()=>finish.resolve({accepted:false,error:'Settings changes were kept.'}));
 assert.deepEqual(activeIds(),['request']);assert.equal(history().length,0);
 assert.equal(root.root.findByProps({role:'alert'}).children.join(''),'Settings changes were kept.');
 for(const outcome of ['unknown','missing-acceptance']){
  await act(async()=>{button('session.select').props.onClick();button('session.select').props.onClick()});
  assert.equal(button('session.select').props.disabled,true);
  if(outcome==='unknown')await act(async()=>finish.reject(Error('Connection lost')));
  else await act(async()=>finish.resolve({}));
  assert.deepEqual(activeIds(),['request']);assert.equal(history().length,0);
  assert.ok(root.root.findByProps({role:'alert'}).children.join(''));
 }
 assert.equal(calls.length,3);
 await act(async()=>{button('session.select').props.onClick()});
 const latestFence=calls.at(-1).meta.handoffIsCurrent;
 await act(async()=>root.update(view(state(accepted,'other'),dispatch)));
 assert.equal(latestFence(),false,'A queued navigation cannot outlive its source view');
 await act(async()=>finish.reject(Error('Late refusal')));
 assert.equal(root.toJSON(),null);
 await act(async()=>root.update(view(state(accepted),dispatch)));
 assert.deepEqual(pastIds(),['request']);assert.doesNotMatch(text(),/Late refusal/);
});

test('rotating bounded projection discards old local reads and errors rather than retaining briefs',async()=>{
 const entries=Array.from({length:24},(_,index)=>({...accepted,requestId:`rotating-${index}`,revision:2,status:'unknown',delivery:'unknown',text:`Projected brief ${index}`}));
 const calls=[];
 const dispatch=async(action,args)=>{
  calls.push(action);
  if(args.requestId==='rotating-0')throw Error('Old local error');
  return {accepted:true,result:{...entries.find(entry=>entry.requestId===args.requestId),revision:100,status:'accepted',delivery:'accepted',text:'Local-only returned brief'}};
 };
 await act(async()=>{root=create(view(state(entries.slice(0,4)),dispatch))});
 for(let offset=0;offset<entries.length;offset+=4){
  await act(async()=>root.update(view(state(entries.slice(offset,offset+4)),dispatch)));
  const reads=root.root.findAllByType('button').filter(node=>node.props['data-action']==='session.handoff.read');
  for(const read of reads)await act(async()=>read.props.onClick());
 }
 assert.equal(calls.length,24);
 // Returning the same IDs with older stream rows must not revive evicted local
 // receipts, retained full briefs, errors or a compact success presentation.
 for(let offset=0;offset<entries.length;offset+=4){
  await act(async()=>root.update(view(state(entries.slice(offset,offset+4)),dispatch)));
  assert.deepEqual(activeIds(),entries.slice(offset,offset+4).map(entry=>entry.requestId));
  assert.equal(history().length,0);assert.doesNotMatch(text(),/Local-only returned brief|Old local error/);
 }
 assert.equal(calls.length,24,'Window rotation never reads or replays effects');
});

test('completion of an evicted in-flight read cannot resurrect its result or error',async()=>{
 let finish;const dispatch=()=>new Promise((resolve,reject)=>{finish={resolve,reject}});
 const unknown={...accepted,revision:2,status:'unknown',delivery:'unknown'};
 const neighbor={...accepted,requestId:'neighbor'};
 await act(async()=>{root=create(view(state([unknown,neighbor]),dispatch))});
 for(const outcome of ['accepted','error']){
  await act(async()=>{button('session.handoff.read').props.onClick()});
  await act(async()=>root.update(view(state(neighbor),dispatch)));
  if(outcome==='accepted')await act(async()=>finish.resolve({accepted:true,result:{...accepted,revision:99,text:'Evicted returned brief'}}));
  else await act(async()=>finish.reject(Error('Evicted read error')));
  await act(async()=>root.update(view(state([unknown,neighbor]),dispatch)));
  assert.deepEqual(activeIds(),['request']);
  assert.deepEqual(pastIds(),['neighbor']);
  assert.doesNotMatch(text(),/Evicted returned brief|Evicted read error/);
  assert.equal(button('session.handoff.read').props.disabled,false);
 }
});

test('a projected-again pending receipt stays fenced until its original call settles',async()=>{
 const unknown={...accepted,revision:2,status:'unknown',delivery:'unknown'};
 const neighbor={...accepted,requestId:'neighbor'};
 let finish,calls=0;const dispatch=()=>{calls++;return new Promise(resolve=>{finish=resolve})};
 await act(async()=>{root=create(view(state([unknown,neighbor]),dispatch))});
 await act(async()=>{button('session.handoff.read').props.onClick()});
 await act(async()=>root.update(view(state(neighbor),dispatch)));
 await act(async()=>root.update(view(state([unknown,neighbor]),dispatch)));
 assert.equal(button('session.handoff.read').props.disabled,true);
 await act(async()=>button('session.handoff.read').props.onClick());
 assert.equal(calls,1);
 await act(async()=>finish({accepted:true,result:accepted}));
 assert.deepEqual(activeIds(),[]);assert.deepEqual(pastIds(),['request','neighbor']);
 assert.equal(button('session.handoff.read').props.disabled,false);
});
