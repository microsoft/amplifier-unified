import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

// Synthetic component props and action responses, not native handoff proof.
const vite=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {WorktreeControls,nextFolderChoice}=await vite.ssrLoadModule('/src/worktrees.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>vite.close());
let storage;
test.beforeEach(()=>{storage=new Map();globalThis.sessionStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}});
const receipt=(patch={})=>({id:'original-move',revision:1,phase:'pending',source:'/fixture/home',target:'/fixture/copy',displayState:'waiting',waitingFor:['generation'],detail:'The current turn will finish first.',...patch});
const intent=(patch={})=>receipt({placementMode:'deferred-retirement',step:'desired',createdAt:10,detail:'Existing workers may finish in the current folder.',...patch});
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
 const move=()=>root.root.findAllByProps({'data-action':'worktree.handoff'}).find(node=>node.children.includes('Use this folder next'));
 assert.equal(move().props.disabled,true);
 await update(session(row,{configurationBusy:false}));
 assert.equal(move().props.disabled,false);
 assert.equal(root.root.findByProps({'data-part':'execution-directory'}).children[0],row.source);
 await act(()=>move().props.onClick());
 assert.deepEqual(calls,[['worktree.handoff',{sessionId:'chat',id:'copy',expectedExecutionRevision:2},{propagateError:true}]]);
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
 for(const label of ['Create managed checkout','Attach checkout','Use home folder next','Archive and preserve files'])assert.match(content(root),new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
 await act(()=>button(root).props.onClick());
 assert.equal(storage.get('amplifier-checkout-change:chat'),JSON.stringify(saved));
 assert.equal(calls.length,1);assert.equal(calls[0][0],'worktree.check');
 await act(()=>root.root.findAllByType('button').find(node=>node.children.includes('Check the same checkout change')).props.onClick());
 assert.deepEqual(calls[1],[saved.action,saved.args,{id:saved.id}]);await act(()=>root.unmount());
});

test('new ordinary intent has current/next folders without Check, UUID text or timing promises',async()=>{
 const row=intent(),before=JSON.stringify(row);
 const {root,calls}=await mount(session(row,{configurationBusy:false,status:'working',workers:[{id:'worker',status:'idle'}]}));
 assert.match(content(root),/Current folder/);assert.match(content(root),/Next folder/);
 assert.equal(root.root.findByProps({'data-part':'execution-directory'}).children[0],row.source);
 assert.equal(root.root.findByProps({'data-part':'next-execution-directory'}).children[0],row.target);
 assert.match(content(root),/existing work to settle/);assert.match(content(root),/Work may still run in the current folder/);
 const card=root.root.findByProps({'data-handoff-id':row.id});
 assert.equal(card.findAllByType('button').length,0);assert.equal(root.root.findAllByProps({'data-action':'worktree.check'}).length,0);
 const text=node=>typeof node==='string'?node:node.children.map(text).join('');
 assert.doesNotMatch(text(root.root),/original-move|next input|next message|Task resumed|Task continued|Check move/);
 assert.equal(calls.length,0);assert.equal(JSON.stringify(row),before);await act(()=>root.unmount());
});

test('next choice uses only newest pending deferred receipt, ignoring history and array order',()=>{
 const rows=[intent({id:'a',createdAt:20,target:'/a'}),receipt({createdAt:100,target:'/legacy'}),intent({id:'z',createdAt:20,target:'/z'}),intent({id:'terminal',createdAt:200,phase:'applied',target:'/old'}),intent({id:'superseded',createdAt:300,phase:'rejected',supersededBy:'z'})];
 Object.freeze(rows);
 assert.equal(nextFolderChoice(rows).target,'/z');assert.equal(rows[0].id,'a');
 assert.equal(nextFolderChoice([rows[1],rows[3],rows[4]]),undefined);
});

test('retiring and committing keep actual source until applied; continuation remains separate',async()=>{
 const {root,update,calls}=await mount(session(intent(),{configurationBusy:false}));
 for(const step of ['retiring','committing']){
  await update(session(intent({revision:2,step,displayState:'moving'}),{configurationBusy:true}));
  assert.equal(root.root.findByProps({'data-part':'execution-directory'}).children[0],'/fixture/home');
  assert.equal(root.root.findByProps({'data-part':'next-execution-directory'}).children[0],'/fixture/copy');
  assert.match(content(root),/Changing folder/);
 }
 await update(session(intent({revision:3,phase:'applied',step:'applied',displayState:'moved',continuation:{status:'backpressured',detail:'No continuation accepted yet.'}}),{workingDirectory:'/fixture/copy',configurationBusy:false}));
 assert.equal(root.root.findByProps({'data-part':'execution-directory'}).children[0],'/fixture/copy');
 assert.equal(root.root.findAllByProps({'data-part':'next-execution-directory'}).length,0);
 assert.match(content(root),/Folder changed/);assert.match(content(root),/Waiting to continue/);
 assert.doesNotMatch(content(root),/Task resumed|Task continued/);assert.equal(calls.length,0);await act(()=>root.unmount());
});

test('superseded, stale CAS, Stop and unknown deferred receipts never turn into current or next',async()=>{
 for(const row of [
  intent({phase:'rejected',displayState:'not_moved',supersededBy:'new-choice',detail:'A newer folder was chosen.'}),
  intent({phase:'rejected',displayState:'not_moved',detail:'Execution revision changed. The choice was not applied.'}),
  intent({phase:'rejected',displayState:'not_moved',detail:'Stopped before placement. No continuation was sent.'}),
  intent({phase:'unknown',displayState:'needs_recovery',detail:'Saved placement could not be confirmed. Review host recovery details.'})
 ]){
  const {root,calls}=await mount(session(row));
  assert.equal(root.root.findAllByProps({'data-part':'next-execution-directory'}).length,0);
  assert.equal(root.root.findByProps({'data-part':'execution-directory'}).children[0],row.source);
  assert.equal(root.root.findAllByProps({'data-action':'worktree.check'}).length,0);
  assert.match(content(root),new RegExp(row.detail.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
  if(row.phase==='unknown')assert.equal(root.root.findAllByProps({role:'alert'}).length,1);
  else assert.match(content(root),/not applied|Not applied/);
  assert.equal(calls.length,0);await act(()=>root.unmount());
 }
});

test('new choice supersedes displayed intent through projection only; late result cannot restore old Next',async()=>{
 let finish,pending;
 const {root,calls,update}=await mount(session(intent({target:'/fixture/other'}),{configurationBusy:false}),()=>new Promise(resolve=>{finish=resolve}));
 const choose=root.root.findByProps({'data-worktree-id':'copy'}).findByProps({'data-action':'worktree.handoff'}).props.onClick;
 await act(()=>{pending=choose();void choose()});
 assert.equal(calls.length,1);assert.match(content(root),/Saving folder choice/);
 await update(session(intent({id:'new-choice',createdAt:30,target:'/fixture/new',revision:1}),{configurationBusy:false}));
 await act(async()=>{finish({accepted:true,result:intent({target:'/stale-http-result'})});await pending});
 assert.equal(root.root.findByProps({'data-part':'next-execution-directory'}).children[0],'/fixture/new');
 assert.equal(root.root.findByProps({'data-part':'execution-directory'}).children[0],'/fixture/home');
 assert.doesNotMatch(content(root),/stale-http-result/);
 assert.deepEqual(calls,[['worktree.handoff',{sessionId:'chat',id:'copy',expectedExecutionRevision:2},{propagateError:true}]]);
 await act(()=>root.unmount());
});

test('deferred intent does not add a control fence, but actual closing configuration does',async()=>{
 const {root,update,calls}=await mount(session(intent({target:'/fixture/other'}),{configurationBusy:false,status:'working'}));
 assert.equal(root.root.findByProps({'data-worktree-id':'copy'}).findByProps({'data-action':'worktree.handoff'}).props.disabled,false);
 await update(session(intent({step:'committing',displayState:'moving'}),{configurationBusy:true}));
 const button=root.root.findByProps({'data-worktree-id':'copy'}).findByProps({'data-action':'worktree.handoff'});
 assert.equal(button.props.disabled,true);await act(()=>button.props.onClick());assert.equal(calls.length,0);
 await act(()=>root.unmount());
});

test('folder submission error announces refusal and preserves intent without hidden check or retry',async()=>{
 const {root,calls}=await mount(session(intent({target:'/fixture/other'}),{configurationBusy:false}),()=>({accepted:false,error:'The execution revision changed. Refresh before choosing again.'}));
 await act(()=>root.root.findByProps({'data-worktree-id':'copy'}).findByProps({'data-action':'worktree.handoff'}).props.onClick());
 assert.match(content(root),/execution revision changed/);
 assert.equal(root.root.findAllByProps({role:'alert'}).length,1);
 assert.equal(root.root.findByProps({'data-part':'execution-directory'}).children[0],'/fixture/home');
 assert.equal(root.root.findByProps({'data-part':'next-execution-directory'}).children[0],'/fixture/other');
 assert.deepEqual(calls.map(call=>call[0]),['worktree.handoff']);await act(()=>root.unmount());
});

test('pending choice can be superseded by current or home folder without a separate cancellation flow',async()=>{
 const current=intent({target:'/fixture/other'});
 const {root,calls}=await mount(session(current,{workingDirectory:'/fixture/copy',configurationBusy:false}),()=>({accepted:true,result:intent({id:'replacement'})}));
 const choose=root.root.findByProps({'data-worktree-id':'copy'}).findByProps({'data-action':'worktree.handoff'});
 assert.equal(choose.props.disabled,false);await act(()=>choose.props.onClick());
 const home=root.root.findAllByProps({'data-action':'worktree.handoff'}).find(node=>node.children.includes('Use home folder next'));
 await act(()=>home.props.onClick());
 assert.deepEqual(calls.map(call=>call[1]),[{sessionId:'chat',id:'copy',expectedExecutionRevision:2},{sessionId:'chat',id:null,expectedExecutionRevision:2}]);
 assert.equal(root.root.findByProps({'data-part':'execution-directory'}).children[0],'/fixture/copy');
 assert.equal(root.root.findByProps({'data-part':'next-execution-directory'}).children[0],current.target);
 await act(()=>root.unmount());
});

test('reload uses canonical desired choice and mixed historical unknown retains legacy Check only',async()=>{
 const old=receipt({id:'legacy-unknown',phase:'unknown',displayState:'needs_recovery',detail:'Original release remains unconfirmed.'});
 const current=intent({id:'current-choice'});
 const {root,calls}=await mount(session(current,{configurationBusy:false,worktreeHandoffs:[old,current]}));
 assert.equal(root.root.findAllByProps({'data-action':'worktree.check'}).length,1);
 assert.equal(root.root.findByProps({'data-handoff-id':'current-choice'}).findAllByType('button').length,0);
 assert.equal(root.root.findByProps({'data-handoff-id':'legacy-unknown'}).findAllByType('button').length,1);
 assert.equal(calls.length,0);await act(()=>root.unmount());
 const reloaded=await mount(session(current,{configurationBusy:false}));
 assert.equal(reloaded.root.root.findByProps({'data-part':'next-execution-directory'}).children[0],current.target);
 assert.equal(reloaded.calls.length,0);assert.equal(storage.size,0);await act(()=>reloaded.root.unmount());
});

test('same Next checkout or home is disabled while a different current folder stays selectable',async()=>{
 const {root,calls,update}=await mount(session(intent(),{configurationBusy:false}));
 const choose=()=>root.root.findByProps({'data-worktree-id':'copy'}).findByProps({'data-action':'worktree.handoff'});
 assert.equal(choose().props.disabled,true);await act(()=>choose().props.onClick());assert.equal(calls.length,0);
 await update(session(intent({target:'/fixture/home'}),{workingDirectory:'/fixture/copy',configurationBusy:false}));
 const home=root.root.findAllByProps({'data-action':'worktree.handoff'}).find(node=>node.children.includes('Use home folder next'));
 assert.equal(home.props.disabled,true);await act(()=>home.props.onClick());assert.equal(calls.length,0);
 assert.equal(choose().props.disabled,false);await act(()=>root.unmount());
});

test('Next target cannot be archived or removed while preserving the archive journal',async()=>{
 const saved=JSON.stringify({action:'worktree.archive',args:{sessionId:'chat',id:'other',expectedRevision:1},id:'original-archive'});
 storage.set('amplifier-checkout-change:chat',saved);
 const {root}=await mount(session(intent(),{configurationBusy:false}));
 const card=root.root.findByProps({'data-worktree-id':'copy'});
 assert.equal(card.findByProps({'data-action':'worktree.archive'}).props.disabled,true);
 assert.equal(card.findByProps({'data-action':'worktree.remove'}).props.disabled,true);
 assert.equal(storage.get('amplifier-checkout-change:chat'),saved);await act(()=>root.unmount());
});

test('late choice failure after canonical progress never overrides newer folder status',async()=>{
 for(const error of [new Error('Obsolete transport warning'),null]){
  let fail,pending;
  const {root,update,calls}=await mount(session(intent({target:'/fixture/other'}),{configurationBusy:false}),()=>new Promise((resolve,reject)=>{fail=()=>reject(error)}));
  await act(()=>{pending=root.root.findByProps({'data-worktree-id':'copy'}).findByProps({'data-action':'worktree.handoff'}).props.onClick()});
  await update(session(intent({id:'new-choice',revision:2,createdAt:30,target:'/fixture/new'}),{configurationBusy:false}));
  await act(async()=>{fail();await pending});
  assert.equal(root.root.findAllByProps({role:'alert'}).length,0);
  assert.doesNotMatch(content(root),/Obsolete transport warning|No confirmed response/);
  assert.equal(root.root.findByProps({'data-part':'next-execution-directory'}).children[0],'/fixture/new');
  assert.equal(calls.length,1);await act(()=>root.unmount());
 }
});

test('canonical receipt or actual folder progress clears prior choice error and idless notice',async()=>{
 for(const response of [new Error('Earlier choice error'),{accepted:true,result:{}}]){
  const {root,update}=await mount(session(intent({target:'/fixture/other'}),{configurationBusy:false}),()=>{if(response instanceof Error)throw response;return response});
  await act(()=>root.root.findByProps({'data-worktree-id':'copy'}).findByProps({'data-action':'worktree.handoff'}).props.onClick());
  if(response instanceof Error)assert.match(content(root),/Earlier choice error/);else assert.match(content(root),/Folder choice received/);
  await update(session(intent({revision:2}),{configurationBusy:false}));
  assert.doesNotMatch(content(root),/Earlier choice error|Folder choice received/);
  await act(()=>root.unmount());
 }
});

test('SSE before idless HTTP and actual placement before error suppress obsolete local feedback',async()=>{
 for(const result of [{accepted:true,result:{}},new Error('Late failure')]){
  let finish,pending;
  const {root,update}=await mount(session(intent({target:'/fixture/other'}),{configurationBusy:false}),()=>new Promise((resolve,reject)=>{finish=()=>result instanceof Error?reject(result):resolve(result)}));
  await act(()=>{pending=root.root.findByProps({'data-worktree-id':'copy'}).findByProps({'data-action':'worktree.handoff'}).props.onClick()});
  await update(session(intent({revision:2,phase:'applied',step:'applied',displayState:'moved'}),{workingDirectory:'/fixture/copy',executionRevision:3,configurationBusy:false}));
  await act(async()=>{finish();await pending});
  assert.doesNotMatch(content(root),/Folder choice received|Late failure/);
  assert.equal(root.root.findAllByProps({role:'alert'}).length,0);
  await act(()=>root.unmount());
 }
});

test('unconfirmed catch uses uncertainty wording, never claims saved state unchanged',async()=>{
 const {root,calls}=await mount(session(intent({target:'/fixture/other'}),{configurationBusy:false}),()=>{throw null});
 await act(()=>root.root.findByProps({'data-worktree-id':'copy'}).findByProps({'data-action':'worktree.handoff'}).props.onClick());
 assert.match(content(root),/No confirmed response/);assert.doesNotMatch(content(root),/saved folder status is unchanged|Could not save/);
 assert.equal(calls.length,1);await act(()=>root.unmount());
});

test('only explicit retiring or committing steps claim Changing folder',async()=>{
 for(const step of [undefined,'unrecognized']){
  const {root}=await mount(session(intent({step}),{configurationBusy:false}));
  assert.doesNotMatch(content(root),/Changing folder/);await act(()=>root.unmount());
 }
});

test('Inspect busy state does not render Saving folder choice',async()=>{
 let finish,pending;
 const {root}=await mount(session(intent(),{configurationBusy:false}),()=>new Promise(resolve=>{finish=resolve}));
 await act(()=>{pending=root.root.findAllByProps({'data-action':'worktree.inspect'})[0].props.onClick()});
 assert.doesNotMatch(content(root),/Saving folder choice/);
 await act(async()=>{finish({accepted:true});await pending});await act(()=>root.unmount());
});