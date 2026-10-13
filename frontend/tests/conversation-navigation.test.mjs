import test from 'node:test';
import assert from 'node:assert/strict';
import {createConversationNavigation,createInlineOwnership,inlineOwnership,leavesSettings,normalizedAction} from '../src/conversation-navigation.js';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

function state(id,revision=1){return {revision,client:{hostInstanceId:'host'},selectedSessionId:id,selectedWorkspaceId:'space',
 sessions:['a','b','c'].map(key=>({id:key,workspaceId:'space',title:key,draft:key+' draft',messages:key===id?[{id:key+'-message',text:key+' history'}]:[]})),
 view:{draft:id+' draft'},canvas:{id:id+'-canvas',open:true},canvasWorkspace:{views:[]},canvasArtifacts:[]}}

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
