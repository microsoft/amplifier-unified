import test from 'node:test';
import assert from 'node:assert/strict';
import {foldInterimRows,interimResponse,interimOpen,groupForMessage,retainInterimIds} from '../src/interim-replies.js';
import {conversationWorkRows,turnPlacements,combinedWork} from '../src/timeline-data.js';
import {withArtifactRows} from '../src/artifact-placement.js';
import {createInlineOwnership} from '../src/conversation-navigation.js';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const response={version:1,generationId:'g',status:'completed',phase:'interim',finalMessageIds:['final','final-2'],inputIds:['input'],anchorMessageId:'user'};
const message=(id,extra={})=>({id,role:'assistant',generationId:'g',text:id,...extra});
function fixture(){
 const messages=[{id:'user',role:'user'},message('progress',{response}),message('update',{response}),message('final'),message('final-2')];
 const data={nodes:[],turns:['user','progress','update','final-2'].map((id,index)=>({
  id:'input@'+id,originalTurnId:'input',anchorMessageId:id,phase:'completed',
  startedAt:10+index*5,endedAt:15+index*5,aggregateUsage:{calls:1,totalTokens:10},
 }))};
 return {messages,data,rows:withArtifactRows(conversationWorkRows(messages,turnPlacements(messages,data)))};
}
test('off/missing/invalid preference returns original rows, without mutation',()=>{
 const {rows,data}=fixture(),before=JSON.stringify(rows);
 for(const value of [undefined,false,'true',1])assert.equal(foldInterimRows(rows,data,value),rows);
 foldInterimRows(rows,data,true);assert.equal(JSON.stringify(rows),before);
});
test('completion moves updates and work into one mixed group; multipart finals and later work stay outside',()=>{
 const {rows,data}=fixture(),folded=foldInterimRows(rows,data,true),group=folded.find(row=>row.interim);
 assert.deepEqual(group.items.map(item=>item.message?.id||item.turnId),['input@user','progress','input@progress','update','input@update']);
 assert.deepEqual(group.outputs.map(row=>row.message.id),['progress','update']);
 assert.deepEqual(folded.filter(row=>row.kind==='message').map(row=>row.id),['user','final','final-2']);
 assert.equal(folded.at(-2).items[0].turnId,'input@final-2');
 assert.equal(combinedWork(group.items.filter(i=>i.turnId).map(i=>data.turns.find(t=>t.id===i.turnId)),100).usage.totalTokens,30);
});
test('without confirmed provenance idle and later work never qualify a message',()=>{
 for(const extra of [{},{partial:true,response},{streaming:true,response},{response:{...response,status:'failed'}},
  {response:{...response,phase:'final'}},{response:{...response,generationId:'foreign'}},{response:{...response,finalMessageIds:[]}}]){
  assert.equal(interimResponse(message('progress',extra)),null);
 }
});
test('human/peer boundaries split groups and unrelated/live work is not swallowed',()=>{
 const {rows,data}=fixture();
 rows.splice(4,0,{kind:'message',id:'steer',message:{id:'steer',role:'user',inputOrigin:'peer'}});
 const folded=foldInterimRows(rows,data,true);
 assert.equal(folded.filter(row=>row.interim).length,2);
 assert.ok(folded.some(row=>row.id==='steer'));
 const live=structuredClone(fixture());live.data.turns[1].phase='running';delete live.data.turns[1].endedAt;
 assert.ok(foldInterimRows(live.rows,live.data,true).some(row=>!row.interim&&row.items?.some(i=>i.turnId==='input@progress')));
 const ids=foldInterimRows(live.rows,live.data,true).filter(row=>row.interim).map(row=>row.id);
 assert.equal(new Set(ids).size,ids.length);
});
test('earlier pages retain the open group identity rather than remounting its messages',()=>{
 const {rows,data}=fixture();
 const earlier=foldInterimRows(rows.slice(4),data,true);
 const all=retainInterimIds(foldInterimRows(rows,data,true),earlier);
 const old=earlier.find(row=>row.interim),current=all.find(row=>row.interim);
 assert.equal(current.id,old.id);
 assert.equal(interimOpen(new Set([old.id]),current),true);
});
test('message-only turns and final off-page use host annotation, not loaded suffix',()=>{
 const rows=withArtifactRows([{kind:'message',id:'progress',message:message('progress',{response})}]);
 const folded=foldInterimRows(rows,{turns:[]},true);
 assert.equal(folded.length,1);assert.equal(folded[0].items[0].message.id,'progress');
 assert.equal(groupForMessage(folded,'progress'),folded[0]);
 assert.equal(groupForMessage(folded,'final'),undefined);
});
test('deferred generations remain unchanged while other completed history can fold',()=>{
 const {rows,data}=fixture();
 assert.deepEqual(foldInterimRows(rows,data,true,{deferred:new Set(['g'])}),rows);
});
test('explicit disclosure choices win; Everything is not a default-open override',()=>{
 const {rows,data}=fixture(),group=foldInterimRows(rows,data,true).find(row=>row.interim);
 assert.equal(interimOpen(new Set(),group),false);
 assert.equal(interimOpen(new Set([group.id]),group),true);
 assert.equal(interimOpen(new Set(['turn:input@progress']),group),true);
 assert.equal(interimOpen(new Set(['turn:input@progress','closed:'+group.id]),group),false);
});

// Exercise the production hook without mounting the app or inventing a second
// folding algorithm. The browser regression checks actual DOM identity.
function foldingHook(edits){
 const source=readFileSync(new URL('../src/interim-replies.jsx',import.meta.url),'utf8');
 const slots=[],effects=[];let index=0;
 const hook=runInNewContext(source.slice(source.indexOf('function reading('),source.indexOf('export function ConversationWork('))
  .replace('export function useInterimReplies','function useInterimReplies')+';useInterimReplies',{
   inlineOwnership:edits,foldInterimRows,groupForMessage,interimResponse,interimOpen,retainInterimIds,
   useRef:value=>{const key=index++;return slots[key]??= {current:value}},
   useState:value=>{const key=index++;if(!(key in slots))slots[key]=value;return [slots[key],next=>{slots[key]=typeof next==='function'?next(slots[key]):next}]},
   useMemo:fn=>fn(),useCallback:fn=>fn,
   useLayoutEffect:(fn,deps)=>{
    const key=index++,old=slots[key];
    if(!old||deps.some((value,i)=>value!==old.deps[i]))effects.push(()=>{old?.cleanup?.();slots[key]={deps,cleanup:fn()}});
   }
  });
 return {render(rows,data,enabled=true){index=0;const value=hook(rows,data,enabled,'chat',{current:null},[],async()=>{});
  while(effects.length)effects.shift()();return value.rows},
  dispose(){for(const slot of slots)slot?.cleanup?.()}};
}

test('real hook defers only protected publication grouping, keeps fresh rows, and allows clean fold/unfold',async()=>{
 const edits=createInlineOwnership(),hook=foldingHook(edits),{rows,data}=fixture();
 const target={viewId:'inline',resourceId:'app',resourceRevision:'r1',generation:1};
 const scope={client:{id:'tab',hostInstanceId:'host'},selectedSessionId:'chat',canvasWorkspace:{views:[target]}};
 const unregister=edits.register(scope,target,{sessionId:'chat',messageId:'progress',version:1});
 const initial=rows.map(row=>row.message?{...row,message:{...row.message,response:undefined}}:row);
 try{
  hook.render(initial,data);edits.intent(target,true);
  const updated=rows.map(row=>row.message?.id==='progress'?{...row,message:{...row.message,text:'Fresh completion metadata'}}:row);
  let rendered=hook.render(updated,data);
  assert.equal(rendered.some(row=>row.interim),false);
  assert.equal(rendered.find(row=>row.id==='progress').message.text,'Fresh completion metadata','Never freeze session/message metadata');
  const other={kind:'message',id:'other',message:message('other',{generationId:'other-generation',
   response:{...response,generationId:'other-generation',finalMessageIds:['other-final']}})};
  hook.render([...updated,other],data);rendered=hook.render([...updated,other],data);
  assert.ok(groupForMessage(rendered,'other'),'Unrelated completed history can fold');
  assert.equal(groupForMessage(rendered,'progress'),undefined);
  edits.settle(edits.intent(target,false),{accepted:true});
  hook.render(updated,data);rendered=hook.render(updated,data);
  const group=rendered.find(row=>row.interim);assert.ok(group);
  edits.intent(target,true);rendered=hook.render(updated,data,false);
  assert.equal(rendered.find(row=>row.interim)?.id,group.id,'Preference OFF cannot ungroup a dirty publication');
  edits.settle(edits.intent(target,false),{accepted:true});
  assert.equal(hook.render(updated,data,false).some(row=>row.interim),false);
  // A transport-unknown hold provides the same grouping protection, with no
  // dirty input and no receipt inferred from the new response metadata.
  hook.render(updated,data);hook.render(updated,data);
  const stopRenderer=edits.registerRenderer(target,{prepare:async()=>({}),validate:()=>true,release(){}});
  const hold=await edits.prepareTransition(target,'unknown-fold',true,scope);hold.sent=true;
  assert.ok(hook.render(updated,data,false).some(row=>row.interim));
  assert.equal(hold.receipt,undefined);
  edits.refuseTransition(hold);stopRenderer();
  assert.equal(hook.render(updated,data,false).some(row=>row.interim),false);
 }finally{unregister();hook.dispose()}
});

test('interim output uses current artifact rows to suppress the legacy duplicate media player',()=>{
 const source=readFileSync(new URL('../src/interim-replies.jsx',import.meta.url),'utf8');
 assert.match(source,/<InlineMedia[^>]+artifactRows=\{state.canvasArtifacts\}/);
});