import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

// Synthetic component props and action responses, not native handoff proof.
const vite=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {WorktreeControls}=await vite.ssrLoadModule('/src/worktrees.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>vite.close());
let storage;
test.beforeEach(()=>{storage=new Map();globalThis.sessionStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}});
const receipt=(patch={})=>({id:'original-move',revision:1,phase:'pending',source:'/fixture/home',target:'/fixture/copy',displayState:'waiting',waitingFor:['generation'],detail:'The current turn will finish first.',...patch});
const session=(row=receipt(),patch={})=>({id:'chat',workspace:'/fixture/home',workingDirectory:'/fixture/home',executionRevision:2,configurationBusy:true,worktrees:[{id:'copy',path:'/fixture/copy',source:'/fixture/home',status:'ready',owned:true,revision:3}],worktreeHandoffs:[row],...patch});
const content=root=>JSON.stringify(root.toJSON());
const button=root=>root.root.findByProps({'data-action':'worktree.check'});
async function mount(current=session(),handler){
 const calls=[],dispatch=async(...args)=>{calls.push(args);return handler?handler(...args):{accepted:true,result:current.worktreeHandoffs[0]}};
 let root;await act(()=>{root=create(React.createElement(WorktreeControls,{session:current,act:dispatch}))});
 return {root,calls,update:next=>act(()=>root.update(React.createElement(WorktreeControls,{session:next,act:dispatch})))};
}

test('plain states and actual From/To folders replace phase, raw identity and manual placement',async()=>{
 for(const [displayState,label] of Object.entries({waiting:'Waiting for this turn',moving:'Moving',moved:'Moved',not_moved:'Not moved',needs_recovery:'Needs recovery'})){
  const {root,calls}=await mount(session(receipt({displayState,phase:'unknown'})));
  const card=root.root.findByProps({'data-handoff-id':'original-move'});
  assert.equal(card.findByProps({'data-part':'move-status'}).children[0],label);assert.match(content(root),/From folder/);assert.match(content(root),/To folder/);
  assert.match(content(root),/\/fixture\/home/);assert.match(content(root),/\/fixture\/copy/);
  assert.equal(card.findAllByType('button').length,1);
  assert.equal(card.findAllByType('textarea').length,0);
  assert.equal(card.findAllByProps({'data-action':'worktree.reconcile'}).length,0);
  assert.doesNotMatch(content(root),/What did you verify|Use source folder|Use target folder/);
  assert.equal(calls.length,0,'rendering performs no check or move');
  await act(()=>root.unmount());
 }
});

test('waiting reasons and optional continuation labels never infer task resumption',async()=>{
 for(const [status,label] of Object.entries({waiting:'Waiting to continue',backpressured:'Waiting to continue',enqueued:'Continuation queued',suppressed:'Continuation not sent'})){
  const {root}=await mount(session(receipt({waitingFor:['tool-replies'],continuation:{status,detail:'Synthetic continuation detail'}})));
  assert.equal(root.root.findByProps({'data-part':'move-status'}).children[0],'Waiting to move');
  assert.match(content(root),new RegExp(label));assert.match(content(root),/Synthetic continuation detail/);
  assert.doesNotMatch(content(root),/Task continued|Task resumed/);await act(()=>root.unmount());
 }
 const {root}=await mount();assert.equal(root.root.findAllByProps({'data-part':'move-continuation'}).length,0);await act(()=>root.unmount());
});

test('Check observes the original id once while busy, without changing canonical props',async()=>{
 let finish;const current=session(receipt({displayState:'needs_recovery',phase:'unknown'}));
 const {root,calls}=await mount(current,()=>new Promise(resolve=>{finish=resolve}));
 const click=button(root).props.onClick;let pending;
 await act(()=>{pending=click();void click()});
 assert.equal(calls.length,1);assert.deepEqual(calls[0],['worktree.check',{sessionId:'chat',id:'original-move'},{propagateError:true}]);
 assert.equal(button(root).props['aria-disabled'],true);assert.match(content(root),/Checking move…/);
 await act(async()=>{finish({accepted:true,result:receipt({displayState:'moved',target:'/unpublished/target'})});await pending});
 assert.match(content(root),/Needs recovery/);assert.doesNotMatch(content(root),/\/unpublished\/target/);
 assert.equal(current.workingDirectory,'/fixture/home');assert.equal(current.worktreeHandoffs[0].displayState,'needs_recovery');
 assert.equal(button(root).props['aria-disabled'],false);await act(()=>root.unmount());
});

test('delayed same-row projection owns placement, with continuation pending separately',async()=>{
 const {root,calls,update}=await mount();
 await update(session(receipt({revision:2,displayState:'moving',waitingFor:[]})));
 assert.match(content(root),/Moving/);
 assert.equal(root.root.findByProps({'data-part':'execution-directory'}).children[0],'/fixture/home');
 await update(session(receipt({revision:3,phase:'applied',displayState:'moved',waitingFor:[],continuation:{status:'waiting',detail:'Waiting for the task owner.'}}),{workingDirectory:'/fixture/copy',configurationBusy:false}));
 assert.equal(root.root.findAllByProps({'data-handoff-id':'original-move'}).length,1);
 assert.match(content(root),/Moved/);assert.match(content(root),/Waiting to continue/);
 assert.doesNotMatch(content(root),/Task continued|Task resumed/);assert.equal(calls.length,0);
 await act(()=>root.unmount());
});

test('canonical update during a check retains the guard and late receipt never regresses placement',async()=>{
 let finish,pending;
 const original=session(),before=JSON.stringify(original);
 Object.freeze(original.worktreeHandoffs[0]);Object.freeze(original.worktreeHandoffs);Object.freeze(original);
 const {root,calls,update}=await mount(original,()=>new Promise(resolve=>{finish=resolve}));
 const originalButton=button(root);
 await act(()=>{pending=originalButton.props.onClick()});
 await update(session(receipt({revision:3,displayState:'moved',continuation:{status:'enqueued'}}),{workingDirectory:'/fixture/copy'}));
 assert.equal(button(root),originalButton);assert.equal(button(root).props['aria-disabled'],true);
 await act(()=>{void button(root).props.onClick()});assert.equal(calls.length,1);
  await act(async()=>{finish({accepted:true,result:receipt({revision:1,displayState:'needs_recovery',detail:'Obsolete owner warning.'})});await pending});
 assert.equal(root.root.findByProps({'data-part':'move-status'}).children[0],'Moved');
 assert.equal(root.root.findByProps({'data-part':'execution-directory'}).children[0],'/fixture/copy');
 assert.match(content(root),/Continuation queued/);assert.doesNotMatch(content(root),/Needs recovery|Obsolete owner warning/);
 assert.equal(JSON.stringify(original),before);await act(()=>root.unmount());
});

test('a newer canonical row supersedes earlier check feedback without remounting the button',async()=>{
 const {root,update,calls}=await mount(session(receipt()),()=>({accepted:true,result:receipt({revision:2,displayState:'needs_recovery',detail:'Owner evidence is unavailable.'})}));
 const originalButton=button(root);
 await act(()=>button(root).props.onClick());
 assert.match(content(root),/Move check: Needs recovery/);
 await update(session(receipt({revision:3,displayState:'moved'}),{workingDirectory:'/fixture/copy'}));
 assert.equal(button(root),originalButton);assert.equal(button(root).props['aria-disabled'],false);
 assert.doesNotMatch(content(root),/Needs recovery|Owner evidence is unavailable/);
 assert.equal(root.root.findByProps({'data-part':'move-status'}).children[0],'Moved');
 assert.equal(calls.length,1);await act(()=>root.unmount());
});

test('not moved keeps source and retry uses only existing backend-unfenced Move',async()=>{
 const row=receipt({phase:'rejected',displayState:'not_moved'});
 const {root,calls,update}=await mount(session(row));
 const move=()=>root.root.findAllByProps({'data-action':'worktree.handoff'}).find(node=>node.children.includes('Move execution here'));
 assert.equal(move().props.disabled,true);
 await update(session(row,{configurationBusy:false}));
 assert.equal(move().props.disabled,false);
 assert.equal(root.root.findByProps({'data-part':'execution-directory'}).children[0],row.source);
 await act(()=>move().props.onClick());
 assert.deepEqual(calls,[['worktree.handoff',{sessionId:'chat',id:'copy',expectedExecutionRevision:2}]]);
 await act(()=>root.unmount());
});

test('failed/rejected/mismatched checks show alerts without follow-up actions or placement claims',async()=>{
 for(const response of [new Error('Synthetic connection lost'),undefined,{accepted:true},{accepted:false,error:'Synthetic check rejected'},{accepted:true,result:receipt({id:'other'})}]){
  const {root,calls}=await mount(session(),()=>{if(response instanceof Error)throw response;return response});
  await act(()=>button(root).props.onClick());
  assert.equal(root.root.findAllByProps({role:'alert'}).length,1);
  assert.match(content(root),/Waiting for this turn/);assert.equal(button(root).props['aria-disabled'],false);
  assert.equal(calls.length,1);assert.equal(calls[0][0],'worktree.check');await act(()=>root.unmount());
 }
});

test('needs recovery check anomaly is visible without overwriting a confirmed projection',async()=>{
 const {root,calls}=await mount(session(receipt({displayState:'moved'}),{workingDirectory:'/fixture/copy'}),()=>({accepted:true,result:receipt({displayState:'needs_recovery',detail:'Owner evidence is unavailable.'})}));
 await act(()=>button(root).props.onClick());
 assert.match(content(root),/Move check: Needs recovery\. Owner evidence is unavailable/);
 assert.equal(root.root.findByProps({'data-part':'move-status'}).children[0],'Moved');
 assert.equal(root.root.findByProps({'data-part':'execution-directory'}).children[0],'/fixture/copy');
 assert.deepEqual(calls.map(call=>call[0]),['worktree.check']);await act(()=>root.unmount());
});

test('reload reads canonical rows with no check, new move or manual journal',async()=>{
 const {root,calls}=await mount();await act(()=>button(root).props.onClick());await act(()=>root.unmount());
 const reloaded=await mount(session(receipt({displayState:'needs_recovery',phase:'unknown'})));
 assert.match(content(reloaded.root),/Needs recovery/);assert.equal(reloaded.calls.length,0);
 assert.equal(calls.length,1);assert.equal(storage.size,0);await act(()=>reloaded.root.unmount());
});

test('existing archive pending journal and create/attach/home controls remain intact',async()=>{
 const saved={action:'worktree.archive',args:{sessionId:'chat',id:'copy',expectedRevision:3},id:'original-archive'};
 storage.set('amplifier-checkout-change:chat',JSON.stringify(saved));
 const {root,calls}=await mount(session(receipt(),{workingDirectory:'/fixture/other',configurationBusy:false,worktreeInspection:{repositorySelection:'',repository:{root:'/fixture/home',sourceRevision:'synthetic-source'}}}));
 for(const label of ['Create managed checkout','Attach checkout','Return execution to home','Archive and preserve files'])assert.match(content(root),new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
 await act(()=>button(root).props.onClick());
 assert.equal(storage.get('amplifier-checkout-change:chat'),JSON.stringify(saved));
 assert.equal(calls.length,1);assert.equal(calls[0][0],'worktree.check');
 await act(()=>root.root.findAllByType('button').find(node=>node.children.includes('Check the same checkout change')).props.onClick());
 assert.deepEqual(calls[1],[saved.action,saved.args,{id:saved.id}]);await act(()=>root.unmount());
});