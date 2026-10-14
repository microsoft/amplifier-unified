import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {AssignmentTaskList}=await server.ssrLoadModule('/src/assignment-task-list.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const board=()=>({assignmentId:'assignment',assignmentRevision:3,assignmentStatus:'active',ownerSessionId:'owner',canManage:true,version:'v1',members:[{sessionId:'owner',label:'Coordinator'},{sessionId:'peer',label:'Draft helper'}],tasks:[{id:'first',title:'Inspect source',description:'Check input',revision:1,status:'open',dependsOn:[],blockedBy:[],readiness:'ready',claim:null},{id:'second',title:'Review report',description:'',revision:1,status:'open',dependsOn:['first'],blockedBy:['first'],readiness:'blocked',claim:null}]});
const text=root=>JSON.stringify(root.toJSON());
const button=(root,label)=>root.root.findAllByType('button').find(row=>row.children.join('')===label);
const expand=async root=>{const details=root.root.findAllByType('details')[0],element={open:true};await act(async()=>details.props.onToggle({target:element,currentTarget:element}))};
function storage(){const map=new Map();return {getItem:key=>map.get(key),setItem:(key,value)=>map.set(key,value),map}}
function fixture(value=board(),mutation=()=>({accepted:true,result:{task:board().tasks[0]}})){const calls=[];let resolveWait;const dispatch=(action,args,meta={})=>{calls.push({action,args,meta});if(action.endsWith('.list'))return Promise.resolve({result:structuredClone(value)});if(action.endsWith('.wait'))return new Promise((resolve,reject)=>{resolveWait=resolve;meta.signal?.addEventListener('abort',()=>reject(Error('aborted')),{once:true})});return Promise.resolve().then(()=>mutation(action,args,meta))};return {dispatch,calls,wake:next=>resolveWait({result:next,changed:true})}}

for(const independent of [false,true])test(`passive disclosure and readable ${independent?'independent':'assignment'} scope`,async()=>{
 globalThis.sessionStorage=storage();const value=independent?{assignmentId:null,tasks:[],members:[],canManage:false,version:'unassigned'}:board();const f=fixture(value);let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});assert.equal(f.calls.length,0);await expand(root);assert.equal(f.calls[0].action,'assignment.tasks.list');assert.equal(f.calls[1].action,'assignment.tasks.wait');assert.equal(f.calls[1].meta.presentation,true);
 if(independent){assert.match(text(root),/independent chat has no shared task list/);assert.equal(button(root,'Add task'),undefined)}else{assert.match(text(root),/Waiting for: /);assert.match(text(root),/Inspect source/);assert.equal(root.root.findAllByType('button').filter(row=>row.children.join('')==='Claim for this chat').length,1)}
 }finally{await act(async()=>root.unmount())}
});

test('peer changes arrive passively and completion stays a report',async()=>{
 globalThis.sessionStorage=storage();const f=fixture();let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});await expand(root);const next=board();next.version='v2';next.tasks[0]={...next.tasks[0],status:'completed',completion:{report:'Checked source',reportedBy:'peer',verification:'participant_report'}};await act(async()=>f.wake(next));assert.match(text(root),/Reported complete/);assert.match(text(root),/Draft helper/);assert.match(text(root),/Artifact verification and integration remain separate/);assert.ok(f.calls.every(row=>['assignment.tasks.list','assignment.tasks.wait'].includes(row.action)));
 }finally{await act(async()=>root.unmount())}
});

test('uncertain claim uses one saved request identity on retry and reload',async()=>{
 globalThis.sessionStorage=storage();let writes=0;const f=fixture(board(),()=>{if(++writes===1)throw Error('Connection lost');return {accepted:true,result:{task:board().tasks[0]}}});let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});await expand(root);await act(async()=>button(root,'Claim for this chat').props.onClick());assert.match(text(root),/no confirmed response/);const first=f.calls.find(row=>row.action==='assignment.tasks.claim');await act(async()=>root.unmount());await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});await expand(root);await act(async()=>button(root,'Check the same task change').props.onClick());const claims=f.calls.filter(row=>row.action==='assignment.tasks.claim');assert.equal(claims.length,2);assert.equal(claims[1].meta.id,first.meta.id);assert.deepEqual(claims[1].args,first.args);assert.doesNotMatch(text(root),/no confirmed response/);
 }finally{await act(async()=>root.unmount())}
});

test('session switch ignores delayed previous board and keeps drafts private',async()=>{
 globalThis.sessionStorage=storage();let release;const calls=[];const dispatch=(action,args,meta)=>{calls.push({action,args,meta});if(args.sessionId==='first'&&action.endsWith('.list'))return new Promise(resolve=>{release=()=>resolve({result:{...board(),tasks:[{...board().tasks[0],title:'OLD PRIVATE TASK'}]}})});if(action.endsWith('.list'))return Promise.resolve({result:{...board(),tasks:[]}});return new Promise((resolve,reject)=>meta.signal.addEventListener('abort',()=>reject(Error('aborted')),{once:true}))};let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch,sessionId:'first'}))});await expand(root);await act(async()=>root.update(React.createElement(AssignmentTaskList,{dispatch,sessionId:'second'})));await act(async()=>release());assert.doesNotMatch(text(root),/OLD PRIVATE TASK/);await expand(root);assert.equal(calls.filter(row=>row.args.sessionId==='second'&&row.action.endsWith('.list')).length,1);
 }finally{await act(async()=>root.unmount())}
});

test('stale responsibility cannot complete and reassignment explains running effects',async()=>{
 globalThis.sessionStorage=storage();const value=board();value.tasks[0]={...value.tasks[0],status:'claimed',claim:{id:'claim',sessionId:'owner',status:'stale',detail:'The holder was stopped.'}};const f=fixture(value);let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});await expand(root);assert.match(text(root),/Claim needs review/);assert.match(text(root),/Prior file or tool work may continue/);assert.equal(button(root,'Mark reported complete'),undefined);await act(async()=>button(root,'Release claim').props.onClick());const release=f.calls.find(row=>row.action==='assignment.tasks.release');assert.equal(release.args.claimId,'claim');assert.equal(release.args.expectedRevision,1);
 }finally{await act(async()=>root.unmount())}
});


test('an unconfirmed response keeps the exact pending request',async()=>{
 globalThis.sessionStorage=storage();const f=fixture(board(),()=>({accepted:false}));let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});await expand(root);await act(async()=>button(root,'Claim for this chat').props.onClick());assert.match(text(root),/no confirmed receipt/);assert.ok(button(root,'Check the same task change'));const saved=JSON.parse(sessionStorage.getItem('amplifier-assignment-tasks:owner'));assert.equal(saved.pending.id,f.calls.find(row=>row.action==='assignment.tasks.claim').meta.id);
 }finally{await act(async()=>root.unmount())}
});

test('task revision updates refresh assignment controls without usage reads',async()=>{
 globalThis.sessionStorage=storage();const value=board();value.assignmentControl={id:'assignment',revision:3,status:'active',ownerSessionId:'owner',limits:{maxOpenTasks:8},members:{owner:{status:'bound'}}};const f=fixture(value),observed=[];let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner',onAssignmentChange:row=>observed.push(row)}))});await expand(root);const next=structuredClone(value);next.version='v2';next.assignmentControl.revision=4;await act(async()=>f.wake(next));assert.deepEqual(observed.map(row=>row.revision),[3,4]);assert.ok(f.calls.every(row=>row.action.startsWith('assignment.tasks.')));
 }finally{await act(async()=>root.unmount())}
});
