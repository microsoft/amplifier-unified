import test from 'node:test';
import assert from 'node:assert/strict';
import {createConversationNavigation,createInlineOwnership,inlineOwnership,inlineEditNotice,leavesSettings,normalizedAction} from '../src/conversation-navigation.js';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

function state(id,revision=1){return {revision,client:{hostInstanceId:'host'},selectedSessionId:id,selectedWorkspaceId:'space',
 sessions:['a','b','c'].map(key=>({id:key,workspaceId:'space',title:key,draft:key+' draft',messages:key===id?[{id:key+'-message',text:key+' history'}]:[]})),
 view:{draft:id+' draft'},canvas:{id:id+'-canvas',open:true},canvasWorkspace:{views:[]},canvasArtifacts:[]}}

for(const target of ['a','b'])test('opening '+target+' from a workspace paints the chat before the server responds',()=>{
 const nav=createConversationNavigation(),workspace=state('a');
 workspace.view.workSurface='workspace';workspace.view.workWorkspaceId='space';
 const token=nav.begin(workspace,target),shown=nav.apply(workspace);
 assert.equal(shown.view.workSurface,'chat');assert.equal(shown.selectedSessionId,target);
 assert.equal(shown.navigationPending,true);assert.equal(workspace.view.workSurface,'workspace');
 nav.settle(token);assert.equal(nav.apply(workspace).view.workSurface,'workspace');
});

test('cached chat shows loading until fresh state arrives, while preserving its draft',()=>{
 const nav=createConversationNavigation(),a=state('a'),b=state('b',2);
 nav.remember(a);nav.remember(b);const token=nav.begin(b,'a'),shown=nav.apply(b);
 assert.equal(shown.selectedSessionId,'a');assert.equal(b.selectedSessionId,'b');
 assert.deepEqual(shown.sessions.find(row=>row.id==='a').messages,[]);
 assert.equal(shown.sessions.find(row=>row.id==='a').historyLoading,true);
 assert.equal(shown.view.draft,'a draft');assert.equal(shown.canvas.open,false);
 const fresh=nav.apply(state('a',3));assert.equal(fresh.sessions.find(row=>row.id==='a').messages[0].text,'a history');
 nav.settle(token);assert.equal(nav.apply(b),b);
});

test('late responses and a failed older selection cannot replace newer intent',()=>{
 const nav=createConversationNavigation(),a=state('a');nav.remember(a);
 const first=nav.begin(a,'b');const second=nav.begin(nav.apply(a),'c');
 nav.settle(first);nav.remember(state('b',2));
 assert.equal(nav.apply(state('b',2)).selectedSessionId,'c');
 nav.settle(second);assert.equal(nav.apply(state('b',2)).selectedSessionId,'b');
});

test('uncached chat shows its own loading state and does not leak the previous canvas',()=>{
 const nav=createConversationNavigation(),a=state('a');nav.remember(a);nav.begin(a,'b');
 const shown=nav.apply(a),b=shown.sessions.find(row=>row.id==='b');
 assert.equal(b.historyLoading,true);assert.deepEqual(b.messages,[]);assert.equal(shown.canvas.open,false);
 assert.equal(shown.view.draft,'b draft');
});

test('dirty renderers stay mounted until authoritative navigation completes',()=>{
 const nav=createConversationNavigation(),a=state('a');a.canvasWorkspace.views=[{dirty:true}];
 assert.equal(nav.begin(a,'b'),null);assert.equal(nav.apply(a),a);
});

test('host restart clears cached details and pending choices',()=>{
 const nav=createConversationNavigation(),a=state('a');nav.remember(a);nav.begin(a,'b');
 const restarted={...state('c'),client:{hostInstanceId:'new-host'}};nav.remember(restarted);
 assert.equal(nav.apply(restarted),restarted);nav.begin(restarted,'a');
 assert.equal(nav.apply(restarted).sessions.find(row=>row.id==='a').historyLoading,true);
});

test('display cache is bounded independently of the saved library',()=>{
 const nav=createConversationNavigation(1);nav.remember(state('a'));nav.remember(state('b'));
 nav.begin(state('b'),'a');assert.equal(nav.apply(state('b')).sessions.find(row=>row.id==='a').historyLoading,true);
});

test('a retained hidden dirty viewer blocks speculative navigation too',()=>{
 const navigation=createConversationNavigation(),state={selectedSessionId:'one',sessions:[{id:'one'},{id:'two'}],canvas:{open:false},canvasWorkspace:{views:[{viewId:'primary',dirty:true}]},view:{}};
 assert.equal(navigation.begin(state,'two'),null);
 assert.equal(navigation.apply(state),state);
});

test('a background chat also hides inline execution and transcript details until selected',()=>{
 const nav=createConversationNavigation(),a=state('a');
 Object.assign(a.sessions[1],{messages:[{id:'stale'}],execution:{nodes:[{id:'old'}]},runtimeEvents:[{type:'tool.pre'}],streaming:'Stale partial response'});
 nav.begin(a,'b');const shown=nav.apply(a).sessions.find(row=>row.id==='b');
 assert.deepEqual(shown.messages,[]);assert.equal(shown.execution,undefined);assert.equal(shown.runtimeEvents,undefined);assert.equal(shown.streaming,undefined);
});

const target={viewId:'inline-editor',resourceId:'document',resourceRevision:'revision-one',generation:1};
const owner={sessionId:'a',messageId:'republished-turn',version:2};
test('exact mounted ownership protects pending/rejected intent; only the matching clean receipt clears it',()=>{
 const edits=createInlineOwnership(),scope={client:{id:'tab',hostInstanceId:'host'},canvasWorkspace:{views:[target]}};
 const unregister=edits.register(scope,target,owner);
 assert.equal(edits.blocksWindow('a',[{id:owner.messageId}]),false);
 const dirty=edits.intent(target,true);
 assert.equal(edits.blocksWindow('a',[]),true);
 edits.settle(dirty,{accepted:false});assert.equal(edits.dirty(),true);
 const clean=edits.intent(target,false);
 assert.equal(edits.blocksWindow('a',[]),true,'Pending clean declarations remain protective');
 const newer=edits.intent(target,true);
 edits.settle(clean,{accepted:true});assert.equal(edits.dirty(),true,'Stale success cannot clear newer typing');
 edits.settle(newer,{accepted:true});assert.equal(edits.dirty(),true);
 assert.equal(edits.blocksWindow('a',[{id:'first-row-anchor'}]),true,'Never substitute the original resource anchor');
 assert.equal(edits.blocksWindow('a',[{id:'earlier'},{id:owner.messageId}]),false,'Additive paging retains the dirty node');
 assert.equal(edits.protectsPublication({...owner,resourceId:target.resourceId}),true);
 assert.equal(edits.protectsPublication({...owner,resourceId:target.resourceId,version:1}),false);
 assert.equal(edits.protectsPublication({...owner,resourceId:target.resourceId,messageId:'first-row-anchor'}),false);
 edits.settle(edits.intent(target,false),{accepted:true});
 assert.equal(edits.dirty(),false);unregister();assert.equal(edits.hasMounts(),false);
});

test('generation, client and host revocation remove protection, never authorize stale content retention',()=>{
 for(const change of [
  {canvasWorkspace:{views:[{...target,generation:2}]}},
  {client:{id:'other-tab',hostInstanceId:'host'}},
  {client:{id:'tab',hostInstanceId:'restarted-host'}},
  {canvasWorkspace:{views:[]}}
 ]){
  const edits=createInlineOwnership(),scope={client:{id:'tab',hostInstanceId:'host'},canvasWorkspace:{views:[target]}};
  edits.register(scope,target,owner);const tickets=edits.intent(target,true);
  assert.equal(edits.reconcile(scope),false);
  assert.equal(edits.reconcile({...scope,...change}),true);
  edits.settle(tickets,{accepted:true});
  assert.equal(edits.hasMounts(),false);assert.equal(edits.blocksWindow('a',[]),false);
 }
 const edits=createInlineOwnership();edits.register({},target,owner);
 edits.intent({...target,generation:2},true);assert.equal(edits.dirty(),false,'Another generation cannot dirty this mount');
 edits.intent({viewId:'primary'},true);assert.equal(edits.blocksWindow('a',[]),false,'Primary Canvas does not own history paging');
});

test('even a clean inline mount stays on screen during queued speculative session navigation',()=>{
 const a=state('a'),unregister=inlineOwnership.register(a,target,owner),nav=createConversationNavigation();
 try{assert.equal(nav.begin(a,'b'),null);assert.equal(nav.apply(a),a)}
 finally{unregister()}
});

test('transition holds veto late dirty/busy input and survive SSE before receipt until exact adoption',async()=>{
 const edits=createInlineOwnership(),initial={...target,publishedVersion:2,selectedVersion:2,followLatest:false};
 const scope={client:{id:'tab',hostInstanceId:'host'},selectedSessionId:'a',canvasWorkspace:{views:[initial]}};
 let frozen=false,prepared=0,valid=true;
 edits.register(scope,initial,owner);
 edits.registerRenderer(initial,{prepare:async id=>{prepared++;frozen=true;return {id}},validate:()=>valid,release:()=>{frozen=false}});
 const hold=await edits.prepareTransition(initial,'exact-command',true,scope);
 assert.equal(frozen,true);assert.equal(edits.blocksWindow('a',[]),true);
 edits.validateTransition(hold,scope);
 valid=false;assert.throws(()=>edits.validateTransition(hold,scope),/Finish or cancel/);valid=true;
 edits.intent(initial,true);assert.throws(()=>edits.validateTransition(hold,scope),/Finish or cancel/);
 edits.settle(edits.intent(initial,false),{accepted:true});edits.validateTransition(hold,scope);
 hold.sent=true;
 const next={...initial,resourceRevision:'next',generation:2,followLatest:true,selectedVersion:null};
 const streamed={...scope,canvasWorkspace:{views:[next]}};
 assert.equal(edits.reconcile(streamed),false);
 assert.equal(frozen,true);assert.equal(edits.transition(initial),hold);
 assert.equal(await edits.prepareTransition(initial,'exact-command',true,streamed),hold);
 assert.equal(prepared,1,'Unknown exact retry never prepares another mount');
 await assert.rejects(edits.prepareTransition(initial,'new-command',false,streamed));
 await edits.completeTransition(hold,{accepted:true,result:next},streamed);
 assert.equal(frozen,true,'Receipt and state are both observed; old DOM stays held until caller adopts');
 edits.adoptTransition(initial,'exact-command');assert.equal(frozen,false);
 assert.equal(edits.transition(initial),undefined);
});

test('receipt-before-React-render retains the newer confirmed successor but never ignores revocation',async()=>{
 for(const change of ['old-render','newer-view','revoked','foreign','late-dirty']){
  const edits=createInlineOwnership(),initial={...target,publishedVersion:2,selectedVersion:2,followLatest:false};
  const shown={revision:10,client:{id:'tab',hostInstanceId:'host'},selectedSessionId:'a',canvasWorkspace:{views:[initial]}};
  let releases=0;
  edits.register(shown,initial,owner);
  edits.registerRenderer(initial,{prepare:async()=>({}),validate:()=>true,release:()=>releases++});
  const hold=await edits.prepareTransition(initial,'render-lag',true,shown);hold.sent=true;
  const next={...initial,generation:2,selectedVersion:null,followLatest:true};
  const fresh={...shown,revision:11,canvasWorkspace:{views:[next]}};
  await edits.completeTransition(hold,{accepted:true,result:next},fresh);
  let rendered=shown,expected=next;
  if(change==='newer-view'){
   expected={...next,generation:3,resourceRevision:'newer'};
   edits.reconcile({...fresh,revision:12,canvasWorkspace:{views:[expected]}});
  }
  if(change==='revoked')edits.reconcile({...fresh,revision:12,canvasWorkspace:{views:[]}});
  if(change==='foreign')rendered={...shown,client:{id:'other',hostInstanceId:'host'}};
  if(change==='late-dirty')edits.intent(initial,true);
  if(change==='late-dirty'){
   assert.throws(()=>edits.adoptTransition(initial,'render-lag',rendered),error=>error.unconfirmed===true);
   assert.equal(releases,0);assert.equal(edits.transition(initial),hold);
  }else if(['revoked','foreign'].includes(change)){
   assert.equal(edits.adoptTransition(initial,'render-lag',rendered),undefined);
  }else{
   assert.deepEqual(edits.adoptTransition(initial,'render-lag',rendered),expected);
   assert.equal(releases,1);
  }
 }
});

test('successful follow receipt cannot discard late dirty input or an invalidated preparation proof',async()=>{
 for(const change of ['dirty-intent','proof','host-dirty']){
  const edits=createInlineOwnership(),initial={...target,publishedVersion:2,selectedVersion:2,followLatest:false};
  const scope={client:{id:'tab',hostInstanceId:'host'},selectedSessionId:'a',canvasWorkspace:{views:[initial]}};
  let frozen=false,proofValid=true,releases=0;
  edits.register(scope,initial,owner);
  edits.registerRenderer(initial,{prepare:async()=>{frozen=true;return {token:'exact'}},
   validate:()=>proofValid,release:()=>{frozen=false;releases++}});
  const hold=await edits.prepareTransition(initial,'accepted-exact',true,scope);
  edits.validateTransition(hold,scope);hold.sent=true;
  const next={...initial,generation:2,selectedVersion:null,followLatest:true};
  const receipt={accepted:true,result:next},current={...scope,canvasWorkspace:{views:[next]}};
  await edits.completeTransition(hold,receipt,current);
  if(change==='dirty-intent')edits.intent(initial,true);
  if(change==='proof')proofValid=false;
  if(change==='host-dirty')current.canvasWorkspace.views=[{...next,dirty:true}];
  assert.throws(()=>edits.adoptTransition(initial,'accepted-exact',current),error=>error.unconfirmed===true);
  assert.equal(frozen,true);assert.equal(releases,0);
  assert.equal(edits.transition(initial),hold);assert.equal(hold.receipt,receipt);
  assert.equal(edits.protectsPublication(owner),true);
 }
});

test('explicit discard reconciles only a confirmed private conflict in the same current view',async()=>{
 for(const change of ['valid','unknown','wrong-id','foreign','host-dirty','revoked']){
  const edits=createInlineOwnership(),initial={...target,publishedVersion:2,selectedVersion:2,followLatest:false};
  const scope={revision:10,client:{id:'tab',hostInstanceId:'host'},selectedSessionId:'a',canvasWorkspace:{views:[initial]}};
  let releases=0;
  edits.register(scope,initial,owner);
  edits.registerRenderer(initial,{prepare:async()=>({}),validate:()=>true,release:()=>releases++});
  const hold=await edits.prepareTransition(initial,'deliberate',true,scope);hold.sent=true;
  const next={...initial,generation:2,selectedVersion:null,followLatest:true};
  const current={...scope,revision:11,canvasWorkspace:{views:[next]}};
  if(change!=='unknown')await edits.completeTransition(hold,{accepted:true,result:next},current);
  edits.intent(initial,true);
  if(change!=='unknown')assert.throws(()=>edits.adoptTransition(initial,'deliberate',current));
  if(change==='foreign')current.selectedSessionId='foreign';
  if(change==='host-dirty')current.canvasWorkspace.views=[{...next,dirty:true}];
  if(change==='revoked')current.canvasWorkspace.views=[];
  const adopted=edits.discardTransition(initial,change==='wrong-id'?'wrong':'deliberate',current);
  if(change==='valid'){assert.deepEqual(adopted,next);assert.equal(releases,1);assert.equal(edits.dirty(),false)}
  else{assert.equal(adopted,undefined);assert.equal(releases,0);assert.equal(edits.transition(initial),hold)}
 }
});

test('missing preparation, explicit refusal and actual scope revocation never authorize a POST',async()=>{
 for(const missing of [true,false]){
  const edits=createInlineOwnership(),scope={client:{id:'tab',hostInstanceId:'host'},selectedSessionId:'a',canvasWorkspace:{views:[target]}};
  edits.register(scope,target,owner);
  if(!missing)edits.registerRenderer(target,{prepare:()=>Promise.reject(Error('Pending operation, no flush')),release(){},validate:()=>false});
  await assert.rejects(edits.prepareTransition(target,'refused',true,scope));
  assert.equal(edits.transition(target),undefined);assert.equal(edits.dirty(),false);
 }
 const edits=createInlineOwnership(),scope={client:{id:'tab',hostInstanceId:'host'},selectedSessionId:'a',canvasWorkspace:{views:[target]}};
 let released=0;
 edits.register(scope,target,owner);
 edits.registerRenderer(target,{prepare:async()=>({}),release:()=>released++,validate:()=>true});
 await edits.prepareTransition(target,'unknown',true,scope);
 assert.equal(edits.reconcile({...scope,client:{id:'other',hostInstanceId:'host'},canvasWorkspace:{views:[]}}),true);
 assert.equal(released,1);assert.equal(edits.hasMounts(),false);
});

// Exercise main.execute itself, not a replacement queue implementation.
test('actual execute prepares only at execution, validates immediately before POST and holds unknown delivery',async()=>{
 const source=readFileSync(new URL('../src/main.jsx',import.meta.url),'utf8');
 const start=source.indexOf('  const execute=async()=>{'),end=source.indexOf('  // Reviewing exact item',start);
 assert.ok(start>=0&&end>start);
 for(const outcome of ['dirty','stale','known-refusal','unknown','http408','http500','http503','non-json','accepted']){
  const edits=createInlineOwnership(),initial={...target,publishedVersion:2,selectedVersion:2,followLatest:false};
  const scope={client:{id:'tab',hostInstanceId:'host'},selectedSessionId:'a',canvasWorkspace:{views:[initial]}};
  const latest={current:scope},posts=[];let frozen=false,prepared=0;
  edits.register(scope,initial,owner);
  edits.registerRenderer(initial,{prepare:async id=>{prepared++;frozen=true;return {id}},validate:()=>outcome!=='stale',release:()=>{frozen=false}});
  const meta={id:'central-exact'},args={...target,followLatest:true};
  const execute=runInNewContext(source.slice(start,end)+'execute',{
   meta,command:{action:'canvas.views.follow',args},action:'canvas.views.follow',args,navigation:false,
   dirtyBarrier:null,inlineOwnership:edits,inlineEditNotice,latest,dirtyTickets:[],DOMException,crypto:{randomUUID:()=> 'unused'},
   request:async(_url,options)=>{
    posts.push(options.body);
    if(outcome==='known-refusal')throw Object.assign(Error('Host refused'),{status:409});
    if(outcome==='unknown')throw Object.assign(Error('Disconnected'),{unconfirmed:true});
    if(outcome.startsWith('http'))throw Object.assign(Error('Unknown execution'),{status:Number(outcome.slice(4))});
    if(outcome==='non-json')throw Error('The server returned an unexpected response (200).');
    const next={...initial,generation:2,followLatest:true,selectedVersion:null};
    return {accepted:true,result:next,state:{...scope,canvasWorkspace:{views:[next]}}};
   },
   acceptState:value=>{edits.reconcile(value);latest.current=value},awaitState:async()=>{}
  });
  assert.equal(prepared,0); // Queue waiting interval remains editable.
  if(outcome==='dirty')edits.intent(initial,true);
  if(outcome==='accepted'){
   const result=await execute();assert.equal(result.accepted,true);assert.equal(frozen,true);
   edits.adoptTransition(target,meta.id);assert.equal(frozen,false);
  }else await assert.rejects(execute());
  assert.equal(posts.length,['dirty','stale'].includes(outcome)?0:1);
  if(['dirty','stale','known-refusal'].includes(outcome))assert.equal(frozen,false);
  if(['unknown','http408','http500','http503','non-json'].includes(outcome)){assert.equal(frozen,true);assert.equal(edits.transition(target).id,meta.id)}
 }
});

test('confirmed receipt adopts the current monotonic successor, even when receipt generation was never observed',async()=>{
 for(const sawSecond of [false,true]){
  const edits=createInlineOwnership(),initial={...target,publishedVersion:2,followLatest:false,selectedVersion:2};
  const scope={client:{id:'tab',hostInstanceId:'host'},selectedSessionId:'a',canvasWorkspace:{views:[initial]}};
  let frozen=false;
  edits.register(scope,initial,owner);
  edits.registerRenderer(initial,{prepare:async()=>{frozen=true;return {}},validate:()=>true,release:()=>{frozen=false}});
  const hold=await edits.prepareTransition(initial,'original-id',true,scope);hold.sent=true;
  const second={...initial,generation:2,resourceRevision:'r2',followLatest:true,selectedVersion:null};
  const third={...second,generation:3,resourceRevision:'r3'};
  if(sawSecond)edits.reconcile({...scope,canvasWorkspace:{views:[second]}});
  const current={...scope,canvasWorkspace:{views:[third]}};
  edits.reconcile(current);
  assert.equal(hold.reconciled,false,'State alone is never a receipt');assert.equal(frozen,true);
  const receipt={accepted:true,result:second};
  await edits.completeTransition(hold,receipt,current);
  assert.equal(hold.receipt,receipt);assert.equal(hold.id,'original-id');assert.equal(frozen,true);
  assert.deepEqual(edits.adoptTransition(initial,hold.id,current),third);
  assert.equal(frozen,false);assert.equal(receipt.result,second);
 }
});

test('receipt plus wrong scope, view, publication, intent or older generation cannot authorize adoption',async()=>{
 const initial={...target,publishedVersion:2,followLatest:false,selectedVersion:2};
 const next={...initial,generation:2,resourceRevision:'r2',followLatest:true,selectedVersion:null};
 const scope={client:{id:'tab',hostInstanceId:'host'},selectedSessionId:'a',canvasWorkspace:{views:[initial]}};
 for(const patch of [
  {client:{id:'other',hostInstanceId:'host'}},{client:{id:'tab',hostInstanceId:'other'}},{selectedSessionId:'b'},
  ...[{viewId:'other'},{resourceId:'other'},{publishedVersion:1},{followLatest:false},{selectedVersion:2},{generation:1},{resourceRevision:'wrong-at-same-generation'}]
   .map(change=>({canvasWorkspace:{views:[{...next,...change}]}}))
 ]){
  const edits=createInlineOwnership();let frozen=false;
  edits.register(scope,initial,owner);
  edits.registerRenderer(initial,{prepare:async()=>{frozen=true;return {}},validate:()=>true,release:()=>{frozen=false}});
  const hold=await edits.prepareTransition(initial,'original-id',true,scope);hold.sent=true;
  await edits.completeTransition(hold,{accepted:true,result:next},{...scope,canvasWorkspace:{views:[next]}});
  assert.equal(edits.adoptTransition(initial,hold.id,{...scope,...patch}),undefined);
  assert.equal(frozen,true);assert.equal(hold.id,'original-id');
  edits.refuseTransition(hold);
 }
});

test('JSON loss after host success reconciles only the original command and current newer view',async()=>{
 const source=readFileSync(new URL('../src/main.jsx',import.meta.url),'utf8');
 const start=source.indexOf('  const execute=async()=>{'),end=source.indexOf('  // Reviewing exact item',start);
 const edits=createInlineOwnership(),initial={...target,publishedVersion:2,followLatest:false,selectedVersion:2};
 const next={...initial,resourceRevision:'r2',generation:2,followLatest:true,selectedVersion:null};
 const newer={...next,resourceRevision:'r3',generation:3};
 const scope={client:{id:'tab',hostInstanceId:'host'},selectedSessionId:'a',canvasWorkspace:{views:[initial]}};
 const latest={current:scope},posts=[],meta={id:'original-json-command'},args={...target,followLatest:true};
 let frozen=false,prepared=0;
 edits.register(scope,initial,owner);
 edits.registerRenderer(initial,{prepare:async()=>{prepared++;frozen=true;return {}},validate:()=>true,release:()=>{frozen=false}});
 const receipt={accepted:true,result:next};
 const execute=runInNewContext(source.slice(start,end)+'execute',{
  meta,command:{action:'canvas.views.follow',args},action:'canvas.views.follow',args,navigation:false,
  dirtyBarrier:null,inlineOwnership:edits,inlineEditNotice,latest,dirtyTickets:[],DOMException,crypto:{randomUUID:()=>{throw Error('Must retain original ID')}},
  request:async(_path,options)=>{
   posts.push(options.body);
   if(posts.length===1){
    latest.current={...scope,canvasWorkspace:{views:[newer]}};edits.reconcile(latest.current);
    throw Error('The server returned an unexpected response (200).');
   }
   return receipt;
  },acceptState(){},awaitState:async()=>{}
 });
 await assert.rejects(execute(),error=>error.unconfirmed===true);
 assert.equal(frozen,true);assert.equal(edits.transition(target).id,meta.id);assert.equal(prepared,1);
 await execute();
 assert.deepEqual(posts[0],posts[1]);assert.equal(prepared,1);assert.equal(frozen,true);
 assert.deepEqual(edits.adoptTransition(target,meta.id,latest.current),newer);
 assert.equal(frozen,false);assert.equal(receipt.result,next);
});

test('Settings leave checks normalize wrapped commands and separate publication from one-time opening',()=>{
 for(const action of ['session.select','session.draft','workspace.select','session.create','canvas.select']){
  assert.equal(leavesSettings(action,{}),true);
  assert.equal(leavesSettings('shell.command',{action,args:{}}),true);
 }
 for(const canvas of ['unchanged','open']){
  const args={sessionId:'a',id:'artifact',version:2,presentation:{chat:'inline',canvas}};
  assert.equal(leavesSettings('canvas.present',args),canvas==='open');
  assert.equal(leavesSettings('shell.command',{action:'canvas.present',args}),canvas==='open');
  assert.deepEqual(normalizedAction('shell.command',{action:'canvas.present',args}),{action:'canvas.present',args});
 }
 assert.equal(leavesSettings('canvas.present',{presentation:{chat:'none'}}),false);
});

test('actual central Settings guard cancels without dispatch and accepts exactly one unchanged identity',async()=>{
 const source=readFileSync(new URL('../src/main.jsx',import.meta.url),'utf8');
 const start=source.indexOf(' const dispatch=useCallback('),end=source.indexOf("  if(action==='call.resumeAudio')",start);
 assert.ok(start>=0&&end>start);
 for(const wrapped of [false,true]){
  const sent=[],prompts=[],editor={value:'private settings text'},original=editor;
  let decide,serial=0;
  const dispatch=runInNewContext(source.slice(start,end)+
   'sent.push({action,args,meta});return Promise.resolve({accepted:true});},[]);dispatch',{
    useCallback:fn=>fn,normalizedAction,leavesSettings,inlineOwnership:createInlineOwnership(),
    inlineEditNotice:'Finish preview edit',crypto:{randomUUID:()=>`command-${++serial}`},
    settingsNavigation:{current:{hasUnsaved:()=>true,confirmClose:()=>{prompts.push(editor);return new Promise(resolve=>{decide=resolve})}}},sent
   });
  const inner={id:'artifact',sessionId:'a',version:2,presentation:{chat:'compact',canvas:'open'}};
  const action=wrapped?'shell.command':'canvas.present',args=wrapped?{action:'canvas.present',args:inner}:inner;
  const cancelled=dispatch(action,args,{id:'stable-command'});assert.equal(sent.length,0);
  decide(false);assert.equal((await cancelled).accepted,false);
  assert.equal(editor,original);assert.equal(editor.value,'private settings text');assert.equal(sent.length,0);
  const accepted=dispatch(action,args,{id:'stable-command'});decide(true);await accepted;
  assert.equal(sent.length,1);assert.equal(sent[0].meta.id,'stable-command');
  assert.equal(sent[0].args,args);assert.equal(prompts.length,2);
  const unchanged={...inner,presentation:{chat:'none',canvas:'unchanged'}};
  await dispatch(wrapped?'shell.command':'canvas.present',wrapped?{action:'canvas.present',args:unchanged}:unchanged);
  assert.equal(prompts.length,2,'Chat-only publication never prompts');
 }
});
