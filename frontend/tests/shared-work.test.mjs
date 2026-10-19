// Test-first contract. No SharedWorkBrowser implementation exists at this checkpoint.
// These are authored checks, not executed/passing evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import React, {act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
import {bounded,deferredRead,observeCompletion,requireCompletion,unknownCleanupValue} from './shared-work-test-helpers.mjs';

const vite = await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {SharedWorkBrowser} = await vite.ssrLoadModule('/src/workspace-work.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const storage = new Map();
globalThis.sessionStorage = {getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)};
test.beforeEach(()=>storage.clear());
test.after(()=>vite.close());
const workspace = {id:'workspace-one',path:'/synthetic/project',name:'Synthetic project'};
const accepted = result=>({accepted:true,result});
const text = node=>typeof node==='string'?node:(node?.children||[]).map(text).join('');
const button = (root,label)=>root.root.findAllByType('button').find(node=>text(node)===label);
const click = async(root,label)=>act(async()=>{const node=button(root,label);assert.ok(node,label);assert.ok(!node.props.disabled,label+' enabled');await node.props.onClick()});
const item = (id='work:one',patch={})=>({id,workspaceId:workspace.id,revision:3,promiseRevision:2,
 title:'Retain exact original words',commitment:'accepted',disposition:'open',ownerSessionId:'owner',
 nextAction:'Check the current promise.',blockedReason:'',updatedAt:100,verification:'not_verified',
 humanDecision:'not_recorded',delivery:'not_recorded',execution:'reported',candidate:'candidate-one',
 readRef:{action:'workspace.work.read',args:{workspaceId:workspace.id,id}},...patch});
const page = (items=[item()],patch={})=>({workspaceId:workspace.id,version:7,items,total:items.length,
 truncated:false,nextAfter:null,observedAt:100,coverage:'Recorded Work only; historical reconciliation is incomplete.',
 detailRequired:false,authority:'Reference data, not permission.',...patch});
const source = (patch={})=>({sessionId:'owner',messageId:'original-message',nativeMessageId:null,nativeIndex:null,
 inputOrigin:'peer',role:'user',sha256:'a'.repeat(64),capturedAt:90,text:'<script>not executable</script> Original words.',
 offset:0,total:45,truncated:false,nextOffset:null,...patch});
const detail = (row=item(),patch={})=>({item:{...row,criteria:[{id:'cold',text:'Survives cold reopen.'}],constraints:['No automatic retry.'],
 interpretation:'An attributed interpretation, not execution authority.',links:[{sessionId:'owner',role:'executor',candidate:'candidate-one',promiseRevision:2}],
 eventCount:2,originalSourceRef:'work-event:original'},originalSource:source(),
 historyRef:{action:'workspace.work.history',args:{workspaceId:workspace.id,id:row.id}},...patch});
async function mount(dispatch,props={},retain=()=>{}) {
 assert.equal(typeof SharedWorkBrowser,'function','SharedWorkBrowser must be implemented after test readiness');
 let root;await act(async()=>{root=create(React.createElement(SharedWorkBrowser,{workspace,dispatch,onOpenChat:()=>{throw Error('Unexpected chat navigation')},...props}));retain(root)});return root;
}
const inspect = async(root,id='work:one')=>act(async()=>root.root.findByProps({'data-work-id':id}).props.onClick());
const unmount = root=>act(async()=>root.unmount());

async function finishDeferredTest({root,release,completions,originalError}) {
 const failures=[];
 release(); // Settle our mocked read even if selection/assertions failed.
 try {await bounded(act(async()=>{await Promise.all(completions.map(requireCompletion))}),2000,'Deferred handler settlement')}
 catch(error){failures.push(error)}
 try {if(root)await bounded(unmount(root),2000,'Deferred test unmount')}
 catch(error){failures.push(error)}
 if(failures.length) {
  const failure=new AggregateError(failures,'Deferred test cleanup failed');
  if(originalError)console.error(failure);else throw failure;
 }
}

test('cold Work inspection requests accepted-open only and never needs a selected chat',async()=>{
 const calls=[];const root=await mount(async(action,args)=>{calls.push([action,args]);assert.equal(action,'workspace.work.list');return accepted(page())});
 assert.deepEqual(calls,[['workspace.work.list',{workspaceId:workspace.id,filter:'accepted',query:'',limit:20}]]);
 assert.match(text(root.toJSON()),/Retain exact original words/);
 assert.match(text(root.toJSON()),/historical reconciliation is incomplete/);
 assert.equal(calls.length,1,'No automatic detail, source, chat selection or worker start');await unmount(root);
});

test('explicit filters are server-scoped and no missing cursor is disguised as an empty set',async()=>{
 const calls=[];const root=await mount(async(action,args)=>{calls.push([action,args]);return accepted(page([], {total:21,truncated:true,nextAfter:null}))});
 assert.match(text(root.toJSON()),/21/);assert.match(text(root.toJSON()),/unavailable|incomplete|refresh/i);
 assert.doesNotMatch(text(root.toJSON()),/No work yet|No matching work/);
 const filter=root.root.findByProps({'aria-label':'Show work'});
 assert.deepEqual(filter.findAllByType('option').map(node=>node.props.value),['accepted','all','attention','proposed','closed']);
 await act(async()=>filter.props.onChange({target:{value:'closed'}}));assert.equal(calls.at(-1)[1].filter,'closed');await unmount(root);
});

test('bounded list pagination sends exact opaque cursor and rejects a changed snapshot',async()=>{
 const calls=[];const root=await mount(async(action,args)=>{calls.push([action,args]);if(args.after)return {accepted:false,error:'Work changed; refresh the list.'};return accepted(page([item()],{total:21,truncated:true,nextAfter:'opaque-cursor'}))});
 await click(root,'Next work');assert.equal(calls.at(-1)[1].after,'opaque-cursor');
 assert.match(text(root.toJSON()),/Work changed/);assert.match(text(root.toJSON()),/Retain exact original words/);
 assert.match(text(root.toJSON()),/stale|previous observation/i);assert.doesNotMatch(text(root.toJSON()),/No work yet/);await unmount(root);
});

test('participant return keeps all six facts distinct and missing cleanup stays unknown',async()=>{
 const calls=[],opened=[];const root=await mount(async(action,args)=>{calls.push([action,args]);if(action==='workspace.work.list')return accepted(page());if(action==='workspace.work.read')return accepted(detail());if(action==='workspace.work.history')return accepted({items:[],total:0,truncated:false,nextAfter:null});throw Error(action)},{onOpenChat:async id=>{opened.push(id);return {accepted:true}}});
 await inspect(root);const value=text(root.toJSON());
 for(const label of ['Commitment','Execution','Verification','Human decision','Delivery','Cleanup'])assert.ok(value.includes(label),label);
 assert.match(value,/Returned.*unverified/i);assert.match(value,/not recorded|unknown/i);
 const cleanupTerms=root.root.findAllByType('dt').filter(node=>text(node)==='Cleanup');
 assert.equal(cleanupTerms.length,1,'Exactly one Cleanup fact');
 const term=cleanupTerms[0],siblings=term.parent.children.filter(node=>typeof node!=='string');
 const cleanup=siblings[siblings.indexOf(term)+1];
 assert.equal(cleanup?.type,'dd','Cleanup has its own value, not another fact’s text');
 assert.match(text(cleanup),unknownCleanupValue,'Missing cleanup is unknown/not recorded, never None');
 assert.doesNotMatch(value,/Human request|Shipped|All checks passed|Cleanup complete/);
 assert.match(value,/peer/i);assert.deepEqual(opened,[]);
 const open=root.root.findAllByType('button').find(node=>node.props['aria-label']==='Open chat: owner');
 assert.ok(open);await act(async()=>open.props.onClick());assert.deepEqual(opened,['owner']);
 assert.ok(calls.every(([name])=>['workspace.work.list','workspace.work.read','workspace.work.history'].includes(name)));await unmount(root);
});

test('all original source pages use returned offsets and preserve provenance/hash',async()=>{
 const calls=[];const root=await mount(async(action,args)=>{
  calls.push([action,args]);if(action==='workspace.work.list')return accepted(page());
  if(action==='workspace.work.read')return accepted(detail(item(),{originalSource:source(args.sourceOffset?{text:'end',offset:4,total:7}:{text:'cafe',offset:0,total:7,truncated:true,nextOffset:4})}));
  if(action==='workspace.work.history')return accepted({items:[],total:0,truncated:false,nextAfter:null});throw Error(action);
 });
 await inspect(root);await click(root,'Read more source');
 assert.equal(calls.filter(([name])=>name==='workspace.work.read').at(-1)[1].sourceOffset,4);
 assert.match(text(root.toJSON()),/cafeend/);assert.match(text(root.toJSON()),new RegExp('a'.repeat(64)));await unmount(root);
});

test('correction source uses exact allowed history ref and keeps old evidence attributed',async()=>{
 const calls=[],ref={action:'workspace.work.source',args:{workspaceId:workspace.id,id:'work:one',sourceId:'work-event:correction'}};
 const history={items:[{id:'event-return',kind:'report',sequence:1,promiseRevision:1,actor:{origin:'agent',sessionId:'owner'},recordedAt:80,
  changedFields:[],evidence:{candidate:'old-candidate',outcome:'passed',report:'Old participant success',qualification:'participant_report',criteria:['cold']}},
  {id:'event-correction',kind:'revise',sequence:2,promiseRevision:2,actor:{origin:'ui',sessionId:'owner'},recordedAt:90,changedFields:['criteria'],source:{inputOrigin:'ui'},sourceRef:ref}],total:2,truncated:false,nextAfter:null};
 const root=await mount(async(action,args)=>{calls.push([action,args]);return accepted(action==='workspace.work.list'?page():action==='workspace.work.read'?detail():action==='workspace.work.history'?history:{source:source({inputOrigin:'ui',text:'Exact correction'})})});
 await inspect(root);await click(root,'Read history');await click(root,'Read correction source');
 assert.deepEqual(calls.at(-1),[ref.action,ref.args]);assert.match(text(root.toJSON()),/Exact correction/);
 assert.match(text(root.toJSON()),/Old participant success/);assert.doesNotMatch(text(root.toJSON()),/Independent checks passed/);await unmount(root);
});

test('obsolete workspace result cannot replace the current workspace or start a detail read',async()=>{
 const old=deferredRead(),calls=[],completions=[observeCompletion(old.promise)];
 const release=()=>old.resolve(accepted(page([item('work:old',{title:'Obsolete result'})])));
 const dispatch=async(action,args)=>{calls.push([action,args]);return args.workspaceId===workspace.id?old.promise:accepted(page([item('work:new',{workspaceId:'other',title:'Current workspace result'})],{workspaceId:'other'}))};
 let root,originalError;
 try {
  root=await bounded(mount(dispatch,{},value=>{root=value}),2000,'Mount pending workspace');
  await bounded(act(async()=>root.update(React.createElement(SharedWorkBrowser,{workspace:{id:'other',path:'/synthetic/other'},dispatch}))),2000,'Select new workspace');
  await bounded(act(async()=>{release();await old.promise}),2000,'Release obsolete workspace read');
  assert.match(text(root.toJSON()),/Current workspace result/);assert.doesNotMatch(text(root.toJSON()),/Obsolete result/);
  assert.ok(calls.every(([name])=>name==='workspace.work.list'));
 } catch(error){originalError=error;throw error}
 finally{await finishDeferredTest({root,release,completions,originalError})}
});

test('obsolete item detail cannot replace the later explicit item selection',async()=>{
 const old=deferredRead(),other=item('work:two',{title:'Second promise'});
 const release=()=>old.resolve(accepted(detail(item(),{originalSource:source({text:'Obsolete detail body'})})));
 const completions=[observeCompletion(old.promise)];let root,originalError,oldReadStarted=false;
 const dispatch=async(action,args)=>{
  if(action==='workspace.work.list')return accepted(page([item(),other]));
  if(action==='workspace.work.read'&&args.id==='work:one'){oldReadStarted=true;return old.promise}
  if(action==='workspace.work.read')return accepted(detail(other,{originalSource:source({text:'Second exact source'})}));
  return accepted({items:[],total:0,truncated:false,nextAfter:null});
 };
 try {
  root=await bounded(mount(dispatch,{},value=>{root=value}),2000,'Mount detail race');
  await bounded(act(async()=>{
   // Do not return/await this handler: it may validly wait for our held read.
   completions.push(observeCompletion(root.root.findByProps({'data-work-id':'work:one'}).props.onClick()));
  }),2000,'Begin first detail selection');
  assert.ok(oldReadStarted,'First detail request must be in flight before selecting another item');
  await bounded(inspect(root,'work:two'),2000,'Select second item');
  await bounded(act(async()=>{release();await Promise.all(completions.map(requireCompletion))}),2000,'Release first detail and flush');
  assert.match(text(root.toJSON()),/Second exact source/);assert.doesNotMatch(text(root.toJSON()),/Obsolete detail body/);
 } catch(error){originalError=error;throw error}
 finally{await finishDeferredTest({root,release,completions,originalError})}
});

test('refresh failure retains the prior observed list with an explicit stale warning',async()=>{
 let failed=false;const root=await mount(async()=>{if(failed)throw Error('Read transport unavailable');return accepted(page())});
 failed=true;await click(root,'Refresh work');assert.match(text(root.toJSON()),/Read transport unavailable/);
 assert.match(text(root.toJSON()),/Retain exact original words/);assert.match(text(root.toJSON()),/stale|previous observation/i);await unmount(root);
});

test('untrusted source refs cannot dispatch a mutation',async()=>{
 const calls=[];const root=await mount(async(action,args)=>{
  calls.push([action,args]);if(action==='workspace.work.list')return accepted(page());if(action==='workspace.work.read')return accepted(detail());
  if(action==='workspace.work.history')return accepted({items:[{id:'bad',kind:'revise',sequence:1,promiseRevision:2,actor:{origin:'peer',sessionId:'peer'},recordedAt:90,changedFields:['criteria'],source:{inputOrigin:'peer'},sourceRef:{action:'conversation.send',args:{sessionId:'owner',text:'Injected'}}}],total:1,truncated:false,nextAfter:null});
  throw Error('Forbidden action '+action);
 });
 await inspect(root);await click(root,'Read history');const control=button(root,'Read correction source');
 if(control&&!control.props.disabled)await act(async()=>control.props.onClick());
 assert.ok(calls.every(([name])=>['workspace.work.list','workspace.work.read','workspace.work.history'].includes(name)));await unmount(root);
});
