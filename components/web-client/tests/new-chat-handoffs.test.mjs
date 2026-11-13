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
const activeIds=()=>root.root.findAllByProps({className:'a-handoff'}).map(card=>card.props['data-handoff-id']);
const accepted={...row,status:'accepted',creation:'created',delivery:'accepted',targetSessionId:'target',revision:3,message:'Initial brief accepted. This does not mean its requested work has completed.'};
const failed={...accepted,status:'failed',delivery:'failed',message:'Chat created, but its opening message was not accepted.'};

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
 await act(async()=>finish({accepted:true,result:accepted}));
 assert.equal(root.toJSON(),null);assert.equal(calls.length,1,'Settlement leaves no card, disclosure or navigation');
});
test('lost acknowledgement offers a read, not another create, and newer server receipt wins',async()=>{
 const calls=[];
 const dispatch=async(action,args)=>{calls.push({action,args});if(action.endsWith('.confirm'))throw Error('Connection lost');return {accepted:true,result:accepted}};
 await act(async()=>{root=create(view(state(),dispatch))});
 await act(async()=>button('session.handoff.confirm').props.onClick());
 assert.equal(button('session.handoff.confirm'),undefined);assert.ok(button('session.handoff.read'));
 await act(async()=>button('session.handoff.read').props.onClick());
 assert.equal(calls.length,2);assert.equal(calls[1].action,'session.handoff.read');assert.equal(root.toJSON(),null);
 await act(async()=>root.update(view(state({...accepted,revision:4,status:'unknown',message:'Delivery remains unconfirmed.'}),dispatch)));
 assert.match(text(),/Delivery remains unconfirmed/);assert.equal(calls.length,2);
});
test('only a definitive pending read re-enables confirmation after lost response',async()=>{
 const calls=[];const dispatch=async(action)=>{calls.push(action);if(action.endsWith('.confirm'))throw Error('Connection lost');return {accepted:true,result:row}};
 await act(async()=>{root=create(view(state(),dispatch))});
 await act(async()=>button('session.handoff.confirm').props.onClick());assert.equal(button('session.handoff.confirm'),undefined);
 await act(async()=>button('session.handoff.read').props.onClick());assert.ok(button('session.handoff.confirm'));assert.equal(calls.length,2);
});
test('cancellation sends no creation or navigation and removes the whole request',async()=>{
 const calls=[];const dispatch=async(action,args)=>{calls.push({action,args});return {accepted:true,result:{...row,revision:2,status:'cancelled',creation:'not_started',delivery:'not_started',message:'Nothing was created.'}}};
 await act(async()=>{root=create(view(state(),dispatch))});
 await act(async()=>button('session.handoff.cancel').props.onClick());
 assert.deepEqual(calls,[{action:'session.handoff.cancel',args:{requestId:'request'}}]);assert.equal(root.toJSON(),null);
});

test('lost cancellation response requires a definitive read before either mutation is offered again',async()=>{
 let calls=0;
 const dispatch=async action=>{calls++;if(action.endsWith('.cancel'))throw Error('Cancellation timed out');return {accepted:true,result:row}};
 await act(async()=>{root=create(view(state(),dispatch))});
 await act(async()=>button('session.handoff.cancel').props.onClick());
 assert.equal(button('session.handoff.confirm'),undefined);assert.equal(button('session.handoff.cancel'),undefined);
 assert.match(text(),/Cancellation timed out/);assert.ok(button('session.handoff.read'));
 await act(async()=>root.update(view(state({...row,revision:2}),dispatch)));
 assert.equal(button('session.handoff.confirm'),undefined,'A stream update alone cannot clear a lost mutation response');
 await act(async()=>button('session.handoff.read').props.onClick());
 assert.ok(button('session.handoff.confirm'));assert.ok(button('session.handoff.cancel'));assert.equal(calls,2);
});

test('accepted action envelope does not settle failed, unknown or contradictory delivery',async()=>{
 let result;
 const dispatch=async()=>({accepted:true,result});
 await act(async()=>{root=create(view(state(),dispatch))});
 for(const receipt of [failed,{...accepted,status:'unknown',delivery:'unknown'},{...accepted,delivery:'failed'},
  {...accepted,status:'cancelled',creation:'unknown',delivery:'not_started',targetSessionId:null}]){
  result=receipt;
  await act(async()=>button(result===failed?'session.handoff.confirm':'session.handoff.read').props.onClick());
  assert.deepEqual(activeIds(),['request']);assert.ok(button('session.handoff.read'));
 }
});

test('known settled receipts leave no history; unfamiliar and contradictory receipts stay actionable',async()=>{
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
 assert.deepEqual(activeIds(),[...attention,...contradictions].map(entry=>entry.requestId));
 assert.doesNotMatch(text(),/Past handoffs|Opening brief accepted|Past chat|"Details"|a-handoff--settled/);
 assert.equal(root.root.findAllByProps({'data-handoff-id':'foreign'}).length,0);
 assert.equal(button('session.handoff.read').children.join(''),'Check delivery');
 await act(async()=>root.update(view({...current,revision:100},()=>{calls++})));
 assert.equal(calls,0,'Rendering and fresh stream state never check, create, send or navigate');
 await act(async()=>root.update(view(state([...settled,cancelled]),()=>{calls++})));
 assert.equal(root.toJSON(),null,'No empty history section or shell when every row is settled');
 await act(async()=>root.update(view(state([]),()=>{calls++})));
 assert.equal(root.toJSON(),null);
});

test('accepted and cancelled receipts with missing or empty phases stay actionable in streams and exact reads',async()=>{
 const incomplete=[];
 for(const status of ['accepted','cancelled']){
  const complete={...accepted,status,...(status==='cancelled'?{creation:'not_started',delivery:'not_started',targetSessionId:null}:{})};
  for(const phase of ['creation','delivery'])for(const value of [undefined,null,'']){
   const receipt={...complete,requestId:`${status}-${phase}-${String(value)}`};
   if(value===undefined)delete receipt[phase];else receipt[phase]=value;
   incomplete.push(receipt);
  }
  const {creation,delivery,...missingBoth}=complete;
  incomplete.push({...missingBoth,requestId:`${status}-missing-both`});
 }
 const calls=[];
 const dispatch=async(action,args)=>{calls.push(action);return {accepted:true,result:{...incomplete.find(receipt=>receipt.requestId===args.requestId),revision:4}}};
 await act(async()=>{root=create(view(state(incomplete),dispatch))});
 assert.deepEqual(activeIds(),incomplete.map(receipt=>receipt.requestId));
 const olderCompletePhases=incomplete.map(receipt=>({...receipt,status:'unknown',
  creation:receipt.status==='accepted'?'created':'not_started',delivery:receipt.status==='accepted'?'accepted':'not_started'}));
 await act(async()=>root.update(view(state(olderCompletePhases),dispatch)));
 for(const receipt of incomplete){
  const card=root.root.findByProps({'data-handoff-id':receipt.requestId});
  const check=card.findByProps({'data-action':'session.handoff.read'});
  await act(async()=>check.props.onClick());
 }
 assert.deepEqual(activeIds(),incomplete.map(receipt=>receipt.requestId),'Neither an accepted envelope nor older stream phases can complete an incomplete exact read');
 assert.ok(calls.every(action=>action==='session.handoff.read'));
 assert.equal(calls.length,incomplete.length);
});

test('manual read removes locally without waiting for a parent snapshot; older stream cannot resurrect it',async()=>{
 const unknown={...accepted,status:'unknown',delivery:'unknown',revision:2};
 const calls=[];const dispatch=async(action,args,options)=>{calls.push({action,args,options});return {accepted:true,result:accepted}};
 await act(async()=>{root=create(view(state(unknown),dispatch))});
 assert.deepEqual(activeIds(),['request']);
 await act(async()=>button('session.handoff.read').props.onClick());
 assert.equal(root.toJSON(),null);
 assert.deepEqual(calls,[{action:'session.handoff.read',args:{requestId:'request'},options:{feedback:false}}]);
 await act(async()=>root.update(view(state(unknown),dispatch)));
 assert.equal(root.toJSON(),null);assert.equal(calls.length,1);
});

for(const status of ['accepted','cancelled'])test(`exact ${status} settlement survives omission before stale pending returns`,async()=>{
 const pending={...row,revision:8},calls=[];let globalRevision=100;
 // These are increasing whole-state revisions carrying older per-receipt rows,
 // not globally old frames (which App.acceptState already rejects).
 const snapshot=rows=>({...state(rows),revision:globalRevision++});
 const settled={...accepted,revision:9,status,text:'Evicted exact brief',message:'Evicted exact feedback',
  ...(status==='cancelled'?{creation:'not_started',delivery:'not_started',targetSessionId:null}:{})};
 const dispatch=async action=>{
  calls.push(action);
  if(action.endsWith('.confirm'))throw Error('Lost confirmation');
  return {accepted:true,result:settled};
 };
 await act(async()=>{root=create(view(snapshot(pending),dispatch))});
 await act(async()=>button('session.handoff.confirm').props.onClick());
 await act(async()=>button('session.handoff.read').props.onClick());
 assert.equal(root.toJSON(),null,'Exact revision 9 settles ahead of stream revision 8');
 for(let cycle=0;cycle<2;cycle++){
  await act(async()=>root.update(view(snapshot([]),dispatch)));
  assert.equal(root.toJSON(),null,'Omission renders no shell');
  await act(async()=>root.update(view(snapshot(pending),dispatch)));
  assert.equal(root.toJSON(),null,'Older pending receipt cannot resurrect after omission');
 }
 for(const revision of [9,10]){
  await act(async()=>root.update(view(snapshot({...pending,revision,status:'unknown',message:'Stream uncertainty'}),dispatch)));
  assert.deepEqual(activeIds(),['request'],'Equal or newer stream receipt wins over the compact witness');
  assert.ok(button('session.handoff.read'));
  assert.doesNotMatch(text(),/Evicted exact brief|Evicted exact feedback|Lost confirmation/);
 }
 assert.deepEqual(calls,['session.handoff.confirm','session.handoff.read'],'Omission and restoration never replay or poll');
});

test('a newer incomplete exact read stays actionable over an older settlement watermark',async()=>{
 let finish,calls=0;
 const dispatch=()=>{calls++;return calls===1?Promise.resolve({accepted:true,result:{...accepted,revision:9}}):new Promise(resolve=>{finish=resolve})};
 const unknown={...row,status:'unknown',revision:8};
 await act(async()=>{root=create(view(state(unknown),dispatch))});
 await act(async()=>button('session.handoff.read').props.onClick());
 await act(async()=>root.update(view(state([]),dispatch)));
 await act(async()=>root.update(view(state({...unknown,revision:10}),dispatch)));
 await act(async()=>{button('session.handoff.read').props.onClick()});
 await act(async()=>root.update(view(state(unknown),dispatch)));
 await act(async()=>finish({accepted:true,result:{...row,revision:11,status:'accepted'}}));
 assert.deepEqual(activeIds(),['request'],'A newer exact receipt with missing phases cannot be suppressed by an older watermark');
 assert.ok(button('session.handoff.read'));assert.equal(calls,2);
});

for(const outcome of ['accepted','unconfirmed'])test(`repeated ${outcome} omission renders reuse compact interaction identity`,async()=>{
 let commits=0,calls=0;const phases=[];
 const dispatch=async()=>{calls++;if(outcome==='unconfirmed')throw Error('Lost confirmation');return {accepted:true,result:accepted}};
 const profiled=current=>React.createElement(React.Profiler,{id:'handoffs',onRender:(_id,phase)=>{commits++;phases.push(phase)}},view(current,dispatch));
 await act(async()=>{root=create(profiled(state()))});
 await act(async()=>button('session.handoff.confirm').props.onClick());
 await act(async()=>root.update(profiled(state([]))));
 for(let cycle=0;cycle<4;cycle++){
  const before=commits;
  await act(async()=>root.update(profiled(state([]))));
  assert.equal(commits-before,1,`Unchanged omission must not schedule a nested interaction-state commit; phases: ${phases.slice(before).join(', ')}`);
  assert.equal(root.toJSON(),null);
 }
 await act(async()=>root.update(profiled(state())));
 if(outcome==='accepted')assert.equal(root.toJSON(),null,'Restored older pending cannot undo the witness');
 else{
  assert.ok(button('session.handoff.read'));assert.equal(button('session.handoff.confirm'),undefined);
  assert.doesNotMatch(text(),/Lost confirmation/);
 }
 assert.equal(calls,1,'Repeated renders and restoration do not read or replay');
});

for(const outcome of ['accepted','error'])test(`late ${outcome} read while omitted preserves only the prior settlement witness`,async()=>{
 let finish,calls=0;
 const dispatch=()=>{calls++;return calls===1?Promise.resolve({accepted:true,result:{...accepted,revision:9}}):new Promise((resolve,reject)=>{finish={resolve,reject}})};
 const unknown={...accepted,revision:8,status:'unknown',delivery:'unknown'};
 await act(async()=>{root=create(view(state(unknown),dispatch))});
 await act(async()=>button('session.handoff.read').props.onClick());
 await act(async()=>root.update(view(state({...unknown,revision:10}),dispatch)));
 await act(async()=>{button('session.handoff.read').props.onClick()});
 await act(async()=>root.update(view(state([]),dispatch)));
 if(outcome==='accepted')await act(async()=>finish.resolve({accepted:true,result:{...accepted,revision:100,text:'Late exact body',message:'Late exact feedback'}}));
 else await act(async()=>finish.reject(Error('Late exact error')));
 await act(async()=>root.update(view(state({...row,revision:8}),dispatch)));
 assert.equal(root.toJSON(),null,'The already-observed revision 9 witness survives');
 await act(async()=>root.update(view(state({...unknown,revision:10}),dispatch)));
 assert.deepEqual(activeIds(),['request'],'The omitted late revision 100 was not adopted');
 assert.ok(button('session.handoff.read'));assert.equal(button('session.handoff.read').props.disabled,false);
 assert.doesNotMatch(text(),/Late exact body|Late exact feedback|Late exact error/);
 assert.equal(calls,2);
});

test('a late older read cannot hide a newer stream failure; equal revisions also favor the stream',async()=>{
 let finish;const dispatch=()=>new Promise(resolve=>{finish=resolve});
 await act(async()=>{root=create(view(state({...accepted,status:'unknown',revision:2}),dispatch))});
 await act(async()=>{button('session.handoff.read').props.onClick()});
 const newer={...accepted,revision:5,status:'failed',delivery:'failed',message:'Newer stream failure'};
 await act(async()=>root.update(view(state(newer),dispatch)));
 await act(async()=>finish({accepted:true,result:accepted}));
 assert.deepEqual(activeIds(),['request']);
 assert.match(text(),/Newer stream failure/);
 await act(async()=>{button('session.handoff.read').props.onClick()});
 await act(async()=>finish({accepted:true,result:{...accepted,revision:5}}));
 assert.deepEqual(activeIds(),['request']);assert.match(text(),/Newer stream failure/);
});

test('in-flight reads and errors stay prominent even if the stream settles',async()=>{
 let finish;
 const dispatch=()=>new Promise((resolve,reject)=>{finish={resolve,reject}});
 await act(async()=>{root=create(view(state({...accepted,status:'unknown',delivery:'unknown',revision:2}),dispatch))});
 await act(async()=>{button('session.handoff.read').props.onClick()});
 await act(async()=>root.update(view(state(accepted),dispatch)));
 assert.equal(button('session.handoff.read').props.disabled,true,'Do not hide a pending check before its outcome');
 await act(async()=>finish.reject(Error('Delivery read unavailable')));
 assert.deepEqual(activeIds(),['request']);
 assert.equal(root.root.findByProps({role:'alert'}).children.join(''),'Delivery read unavailable');
 await act(async()=>{button('session.handoff.read').props.onClick()});
 assert.match(text(),/Delivery read unavailable/,'Keep the error until a read confirms a result');
 await act(async()=>finish.resolve({accepted:true,result:accepted}));
 assert.equal(root.toJSON(),null);
});

test('unconfirmed or mismatched reads cannot hide uncertainty or re-enable a lost confirmation',async()=>{
 let result;
 const dispatch=async(action)=>{if(action.endsWith('.confirm'))throw Error('Connection lost');return result};
 await act(async()=>{root=create(view(state(),dispatch))});
 await act(async()=>button('session.handoff.confirm').props.onClick());
 for(const invalid of [
  {accepted:true},
  {accepted:false,result:accepted},
  {accepted:true,result:{...accepted,requestId:'other'}},
  {accepted:true,result:{...accepted,sourceSessionId:'other'}},
  {accepted:true,result:{...accepted,revision:undefined}},
 ]){
  result=invalid;
  await act(async()=>button('session.handoff.read').props.onClick());
  assert.deepEqual(activeIds(),['request']);assert.equal(button('session.handoff.confirm'),undefined);
  assert.match(text(),/could not be confirmed/);
 }
});

for(const key of ['historyScope','hostInstanceId','id'])test(`a changed ${key} discards local reads and fences late results`,async()=>{
 let finish;
 const dispatch=()=>new Promise(resolve=>{finish=resolve});
 const current={...state({...accepted,status:'unknown',delivery:'unknown',revision:2}),client:{id:'client',hostInstanceId:'host',historyScope:'A'}};
 await act(async()=>{root=create(view(current,dispatch))});
 await act(async()=>{button('session.handoff.read').props.onClick()});
 await act(async()=>root.update(view({...current,client:{...current.client,[key]:'B'}},dispatch)));
 await act(async()=>finish({accepted:true,result:{...accepted,revision:100}}));
 assert.deepEqual(activeIds(),['request']);
 await act(async()=>{button('session.handoff.read').props.onClick()});
 await act(async()=>finish({accepted:true,result:accepted}));
 assert.equal(root.toJSON(),null);
 await act(async()=>root.update(view(current,dispatch)));
 assert.deepEqual(activeIds(),['request']);
});

test('Open chat keeps its scoped navigation fence until acceptance, refusal or unknown settles',async()=>{
 const calls=[];let finish;
 const dispatch=(action,args,meta)=>{calls.push({action,args,meta});return new Promise((resolve,reject)=>{finish={resolve,reject}})};
 await act(async()=>{root=create(view(state(failed),dispatch))});
 await act(async()=>{button('session.select').props.onClick();button('session.select').props.onClick()});
 assert.equal(calls.length,1);assert.equal(calls[0].action,'session.select');
 assert.deepEqual(calls[0].args,{id:'target'});assert.equal(calls[0].meta.handoffIsCurrent(),true);
 assert.equal(button('session.select').props.disabled,true);
 await act(async()=>finish.resolve({accepted:false,error:'Settings changes were kept.'}));
 assert.deepEqual(activeIds(),['request']);
 assert.equal(root.root.findByProps({role:'alert'}).children.join(''),'Settings changes were kept.');
 for(const outcome of ['unknown','missing-acceptance']){
  await act(async()=>{button('session.select').props.onClick();button('session.select').props.onClick()});
  assert.equal(button('session.select').props.disabled,true);
  if(outcome==='unknown')await act(async()=>finish.reject(Error('Connection lost')));
  else await act(async()=>finish.resolve({}));
  assert.deepEqual(activeIds(),['request']);
  assert.ok(root.root.findByProps({role:'alert'}).children.join(''));
 }
 assert.equal(calls.length,3);
 await act(async()=>{button('session.select').props.onClick()});
 const latestFence=calls.at(-1).meta.handoffIsCurrent;
 await act(async()=>root.update(view(state(failed,'other'),dispatch)));
 assert.equal(latestFence(),false,'A queued navigation cannot outlive its source view');
 await act(async()=>finish.reject(Error('Late refusal')));
 assert.equal(root.toJSON(),null);
 await act(async()=>root.update(view(state(failed),dispatch)));
 assert.deepEqual(activeIds(),['request']);assert.doesNotMatch(text(),/Late refusal/);
});

test('rotating projection retains scalar settlement ordering without old briefs or errors',async()=>{
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
 // Older rows cannot undo exact settlement, but the omitted full brief and error
 // must not reappear when an equal/newer stream receipt becomes actionable.
 for(let offset=0;offset<entries.length;offset+=4){
  await act(async()=>root.update(view(state(entries.slice(offset,offset+4)),dispatch)));
  assert.deepEqual(activeIds(),offset===0?['rotating-0']:[]);
  await act(async()=>root.update(view(state(entries.slice(offset,offset+4).map(entry=>({...entry,revision:101}))),dispatch)));
  assert.deepEqual(activeIds(),entries.slice(offset,offset+4).map(entry=>entry.requestId));
  assert.doesNotMatch(text(),/Local-only returned brief|Old local error/);
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
  assert.equal(root.root.findAllByProps({'data-handoff-id':'neighbor'}).length,0);
  assert.doesNotMatch(text(),/Evicted returned brief|Evicted read error/);
  assert.equal(button('session.handoff.read').props.disabled,false);
 }
});

test('a projected-again pending receipt stays fenced until its original call settles',async()=>{
 const unknown={...accepted,revision:2,status:'unknown',delivery:'unknown'};
 let finish,calls=0;const dispatch=()=>{calls++;return new Promise(resolve=>{finish=resolve})};
 await act(async()=>{root=create(view(state(unknown),dispatch))});
 await act(async()=>{button('session.handoff.read').props.onClick()});
 await act(async()=>root.update(view(state([]),dispatch)));
 assert.equal(root.toJSON(),null,'Omission is not a settlement receipt');
 await act(async()=>root.update(view(state(unknown),dispatch)));
 assert.equal(button('session.handoff.read').props.disabled,true);
 await act(async()=>button('session.handoff.read').props.onClick());
 assert.equal(calls,1);
 await act(async()=>finish({accepted:true,result:accepted}));
 assert.equal(root.toJSON(),null);
});

for(const action of ['confirm','cancel'])for(const omittedBeforeFailure of [false,true])test(`${action} uncertainty survives stream omission ${omittedBeforeFailure?'before':'after'} a lost response`,async()=>{
 let finish,calls=0;
 const dispatch=()=>{calls++;return new Promise((resolve,reject)=>{finish={resolve,reject}})};
 await act(async()=>{root=create(view(state(),dispatch))});
 await act(async()=>{button(`session.handoff.${action}`).props.onClick()});
 if(omittedBeforeFailure)await act(async()=>root.update(view(state([]),dispatch)));
 await act(async()=>finish.reject(Error('Lost mutation response')));
 if(!omittedBeforeFailure)await act(async()=>root.update(view(state([]),dispatch)));
 assert.equal(root.toJSON(),null,'An omitted row is not retained as a history card');
 await act(async()=>root.update(view(state(),dispatch)));
 assert.equal(button('session.handoff.confirm'),undefined);assert.equal(button('session.handoff.cancel'),undefined);
 assert.ok(button('session.handoff.read'));assert.doesNotMatch(text(),/Lost mutation response/,'Eviction retains the safety flag, not old feedback');
 assert.equal(calls,1,'Omission must not replay or read automatically');
 await act(async()=>{button('session.handoff.read').props.onClick()});
 await act(async()=>finish.resolve({accepted:true,result:row}));
 assert.ok(button('session.handoff.confirm'));assert.ok(button('session.handoff.cancel'));
 assert.equal(calls,2,'Only an explicit exact read clears uncertainty');
});

test('an unmounted source ignores late success and failure without restoring a request',async()=>{
 for(const outcome of ['accepted','error']){
  let finish;
  const dispatch=()=>new Promise((resolve,reject)=>{finish={resolve,reject}});
  await act(async()=>{root=create(view(state(),dispatch))});
  await act(async()=>{button('session.handoff.confirm').props.onClick()});
  await act(async()=>root.unmount());
  if(outcome==='accepted')await act(async()=>finish.resolve({accepted:true,result:accepted}));
  else await act(async()=>finish.reject(Error('Late failure')));
  assert.equal(root.toJSON(),null);
 }
});

for(const moved of [false,true])test(`removing the final card ${moved?'does not steal moved-away focus':'continues at the existing composer'}`,async()=>{
 let finish;
 const dispatch=()=>new Promise(resolve=>{finish=resolve});
 const body={},focusCalls=[],doc={body,activeElement:body,querySelector:selector=>{
  assert.equal(selector,'#amp-one [role="textbox"][aria-label="Message Amplifier"]');
  return {focus:options=>focusCalls.push(options)};
 }};
 const node={isConnected:true,ownerDocument:doc};
 await act(async()=>{root=create(view(state(),dispatch))});
 root.root.findByType('section').props.onFocusCapture({target:node});
 await act(async()=>{button('session.handoff.confirm').props.onClick()});
 node.isConnected=false;
 if(moved)doc.activeElement={};
 await act(async()=>finish({accepted:true,result:accepted}));
 assert.equal(root.toJSON(),null);
 assert.deepEqual(focusCalls,moved?[]:[{preventScroll:true}]);
});
