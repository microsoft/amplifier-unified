// Execute the maintained composer callbacks with controlled peers, without
// mounting the entire app or inventing a second send implementation.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {nativeDeliverySource,freshRetrySource,deliveryScope,deliveryTargetMatches} from '../src/pending-delivery-checks.js';
const source=readFileSync(new URL('../src/main.jsx',import.meta.url),'utf8');
function callback(name,end,bindings){
 bindings={nativeDeliverySource,freshRetrySource,deliveryTargetMatches:(state,target,id)=>deliveryTargetMatches({...state,client:{hostInstanceId:'host'}},target,id),...bindings};
 const start=source.indexOf(` async function ${name}(`),stop=source.indexOf(end,start);
 assert.ok(start>=0&&stop>start,`Find the maintained ${name} callback`);
 return new Function(...Object.keys(bindings),`${source.slice(start,stop)};return ${name};`)(...Object.values(bindings));
}
function ordinary(value){
 const wire=JSON.parse(JSON.stringify(value));
 assert.equal(Object.hasOwn(wire,'expectedGenerationId'),false);
 assert.equal(Object.hasOwn(wire,'allowNextTurn'),false);
}

test('known failed historical retry clears old routing intent; a lost new receipt can retry the same unanchored payload',async()=>{
 const entry={id:'local',commandId:'old-input',sessionId:session.id,scope:scope(null,session),text:'Original text',attachmentIds:['file'],status:'failed',expectedGenerationId:'old-run',allowNextTurn:true};
 const box=outbox(entry),calls=[],errors=[];
 const retry=callback('retryMessage','\n async function send(',{outbox:box,outboxMatches:matches,deliveryScope:scope,latest:{current:{sessions:[session]}},crypto:{randomUUID:()=> 'new-input'},deliver:async value=>calls.push(structuredClone(value)),setError:error=>errors.push(error)});
 await retry({localDelivery:{id:'local'}});
 assert.equal(calls.length,1);assert.equal(calls[0].commandId,'new-input');ordinary(calls[0]);
 assert.equal(calls[0].expectedGenerationId,undefined);assert.equal(calls[0].allowNextTurn,undefined);
 box.update('local',{status:'unknown'});
 await retry({localDelivery:{id:'local'}});
 assert.equal(calls.length,2);assert.equal(calls[1].commandId,'new-input');
 assert.deepEqual(calls[1],{...calls[0],status:'unknown'});assert.deepEqual(errors,[]);
});
function outbox(entry){
 const current={current:entry?[entry]:[]};
 return {current,update(id,patch){const previous=current.current.find(row=>row.id===id);const next=patch===null?null:{...previous,id,...patch};current.current=patch===null?current.current.filter(row=>row.id!==id):[...current.current.filter(row=>row.id!==id),next];return next}};
}
const scope=(state,session)=>deliveryScope({...state,client:{hostInstanceId:'host'}},session);
const matches=(entry,value)=>entry.scope===value;
const session={id:'captured-chat',nextMessageRevision:7,interruptionRevision:3,collaborationGeneration:{id:'stale-projection',terminal:false}};
const document={version:2,segments:[{type:'text',text:'Commentary'},{type:'quote',text:'Captured excerpt',source:{sessionId:'source-chat',messageId:'source-message'}}]};

for(const quoted of [false,true])test(`composer captures destination/config/text/${quoted?'document':'reply'}/attachments without routing flags`,async()=>{
 const box=outbox(),delivered=[],attachments=[{id:'file',name:'reference.txt'}],state={view:{messageReply:{id:'reply-message'}},sessions:[session],selectedSessionId:session.id};
 const latest={current:state};
 const send=callback('send','\n async function submitWorker(',{
  dictationControl:{current:null},referenceDraftLock:{current:false},draft:'  Exact text\n',availableAttachments:attachments,uploadCount:{current:0},busy:false,newChatPending:false,historyPending:false,executionUnavailable:false,
  preserveOtherDraft(){},session,quoteApi:{current:{document:()=>quoted?document:null}},crypto:{randomUUID:()=> 'stable-input'},externalConversation:false,detail:{focused:false},chatScroll:{current:null},setError(){},setQuoteNotice(){},notice:undefined,quoteNoticeAfterSend(){},editDraft(){},clearTimeout(){},draftTimer:{current:null},stagedDraft:{current:'draft-token'},outbox:box,deliveryScope:scope,latest,state,mode:'text',Date,stagedDraftPayload:{current:{}},
  saveDraft:async()=>{latest.current={...state,selectedSessionId:'other-chat',sessions:[{...session,nextMessageRevision:99,interruptionRevision:4,collaborationGeneration:{id:'newer-projection',terminal:false}},{id:'other-chat'}]};},
  reportError(error){throw error},outboxMatches:matches,ensureSession(){throw Error('Existing target must not be recreated')},deliver:async entry=>delivered.push(entry),
 });
 await send({preventDefault(){}});
 assert.equal(delivered.length,1);const entry=delivered[0];ordinary(entry);
 assert.equal(entry.commandId,'stable-input');assert.equal(entry.sessionId,'captured-chat');assert.equal(entry.scope,scope(null,session));assert.equal(entry.expectedConfigurationRevision,7);
 assert.deepEqual(entry.expectedSource,nativeDeliverySource(session),'Source witness and Stop epoch are both frozen at submit');
 assert.equal(entry.expectedInterruptionRevision,3,'Stop epoch is captured before any draft/checkpoint/network await, not rebound on delivery');
 assert.equal(entry.text,'  Exact text\n');assert.deepEqual(entry.attachmentIds,['file']);assert.deepEqual(entry.attachments,attachments);assert.equal(entry.via,'text');
 if(quoted){assert.deepEqual(entry.document,document);assert.deepEqual(entry.replyTo,{version:2,segments:document.segments})}
 else assert.deepEqual(entry.replyTo,{id:'reply-message'});
 assert.equal(entry.status,'sending');
});

test('delivery sends the exact captured payload and command once without choosing an execution route',async()=>{
 const entry={id:'local',commandId:'stable-input',sessionId:session.id,scope:scope(null,session),expectedSource:nativeDeliverySource(session),expectedConfigurationRevision:7,expectedInterruptionRevision:3,text:'Exact text',document,replyTo:{id:'reply-message'},attachmentIds:['file'],via:'chat',status:'unknown'};
 const box=outbox(entry),calls=[],deliveries={current:new Set()},latest={current:{sessions:[{...session,nextMessageRevision:99,interruptionRevision:4}],selectedSessionId:'other-chat'}};
 let finish;const pending=new Promise(resolve=>{finish=resolve});
 const deliver=callback('deliver','\n function discardMessage(',{outboxMatches:matches,deliveryScope:scope,latest,deliveries,outbox:box,dispatch:async(action,args,options)=>{calls.push({action,args,options});return pending},actionErrorMessage:error=>error.message});
 const running=deliver(entry);await deliver(entry);
 assert.equal(calls.length,1);ordinary(calls[0].args);
 assert.deepEqual(calls[0],{action:'conversation.send',args:{sessionId:session.id,text:entry.text,expectedSource:entry.expectedSource,document,expectedConfigurationRevision:7,expectedInterruptionRevision:3,replyId:'reply-message',attachmentIds:['file'],via:'chat',preserveDraft:true},options:{id:'stable-input',feedback:false,outboxScope:entry.scope,preservePayload:true}});
 finish({delivery:'unknown'});await running;
 assert.equal(box.current.current[0].status,'unknown');assert.equal(box.current.current[0].commandId,'stable-input');assert.equal(deliveries.current.size,0);assert.equal(calls.length,1,'Uncertain receipt never automatically resubmits');
});

for(const status of ['unknown','failed'])test(`${status} retry preserves payload/target; only known rejection creates a new input/config expectation`,async()=>{
 const entry={id:'local',commandId:'original-input',sessionId:session.id,scope:scope(null,session),expectedConfigurationRevision:7,expectedInterruptionRevision:3,text:'Captured text',document,replyTo:{version:2,segments:document.segments},attachmentIds:['file'],status};
 const box=outbox(entry),delivered=[],latest={current:{sessions:[{...session,nextMessageRevision:8,interruptionRevision:4}],selectedSessionId:'other-chat'}};
 const retry=callback('retryMessage','\n async function send(',{outbox:box,outboxMatches:matches,deliveryScope:scope,latest,crypto:{randomUUID:()=> 'new-input'},act(){throw Error('No draft/editor mutation for unchanged retry')},deliver:async value=>delivered.push(value),setError(error){throw Error(error)}});
 await retry({localDelivery:{id:'local'}});
 assert.equal(delivered.length,1);const value=delivered[0];ordinary(value);
 assert.equal(value.commandId,status==='unknown'?'original-input':'new-input');assert.equal(value.expectedConfigurationRevision,status==='unknown'?7:8);
 assert.equal(value.expectedInterruptionRevision,status==='unknown'?3:4,'Unknown preserves original Stop epoch; definite rejection refreshes it with a new input ID');
 if(status==='failed')assert.deepEqual(value.expectedSource,nativeDeliverySource(session));
 else assert.equal(Object.hasOwn(value,'expectedSource'),false,'Unknown preserves witness omission as well as Stop epoch');
 assert.equal(value.sessionId,entry.sessionId);assert.equal(value.scope,entry.scope);assert.equal(value.text,entry.text);assert.deepEqual(value.document,document);assert.deepEqual(value.replyTo,entry.replyTo);assert.deepEqual(value.attachmentIds,['file']);
});

test('ordinary callbacks contain no generation capture or execution-route plumbing',()=>{
 const callbacks=source.slice(source.indexOf(' async function deliver('),source.indexOf(' function discardMessage('))+source.slice(source.indexOf(' async function send('),source.indexOf(' async function submitWorker('));
 assert.doesNotMatch(callbacks,/expectedGenerationId|allowNextTurn|collaborationGeneration/);
});

test('an earlier unknown entry without a Stop epoch is never rebound to the current epoch at retry or delivery',async()=>{
 const entry={id:'local',commandId:'earlier-input',sessionId:session.id,scope:scope(null,session),text:'Keep original arguments',attachmentIds:[],status:'unknown'};
 const box=outbox(entry),calls=[],latest={current:{sessions:[{...session,interruptionRevision:99}]}};
 const deliver=callback('deliver','\n function discardMessage(',{outboxMatches:matches,deliveryScope:scope,latest,deliveries:{current:new Set()},outbox:box,dispatch:async(action,args,options)=>{calls.push({action,args,options});return {delivery:'unknown'}},actionErrorMessage:error=>error.message});
 const retry=callback('retryMessage','\n async function send(',{outbox:box,outboxMatches:matches,deliveryScope:scope,latest,deliver,setError(error){throw Error(error)}});
 await retry({localDelivery:{id:'local'}});
 assert.equal(calls.length,1);assert.equal(calls[0].options.id,'earlier-input');
 assert.equal(Object.hasOwn(calls[0].args,'expectedInterruptionRevision'),false,'No current-epoch default in dispatch/delivery');
 assert.equal(Object.hasOwn(calls[0].args,'expectedConfigurationRevision'),false,'No enrichment of a literal original omission');
 assert.equal(Object.hasOwn(box.current.current[0],'expectedInterruptionRevision'),false);
});

test('a known-failed retained handler cannot form fresh intent while its captured chat row is omitted',async()=>{
 const entry={id:'local',commandId:'rejected-input',sessionId:session.id,scope:scope(null,session),expectedSource:nativeDeliverySource(session),expectedConfigurationRevision:7,expectedInterruptionRevision:3,text:'Keep rejected intent',attachmentIds:[],status:'failed'};
 const box=outbox(entry),before=structuredClone(box.current.current),errors=[];
 const retry=callback('retryMessage','\n async function send(',{outbox:box,latest:{current:{sessions:[{id:'other'}]}},setError:error=>errors.push(error)});
 await retry({localDelivery:{id:'local'}});
 assert.deepEqual(box.current.current,before,'No new ID, witness removal or zero-revision refresh');
 assert.match(errors[0],/Open the original chat.*retry/i);
});

for(const external of [false,true])test(`fresh ${external?'ACP':'native'} new-chat send binds confirmed nonzero config without enriching old unknowns`,async()=>{
 const box=outbox(),delivered=[],state={view:{},sessions:[],selectedSessionId:null};
 const created={...session,id:'new-chat',nextMessageRevision:17,...(external?{externalAgent:{id:'acp'}}:{})},latest={current:state};
 const send=callback('send','\n async function submitWorker(',{
  dictationControl:{current:null},referenceDraftLock:{current:false},draft:'First input',availableAttachments:[],uploadCount:{current:0},busy:false,newChatPending:false,historyPending:false,executionUnavailable:false,
  preserveOtherDraft(){},session:null,quoteApi:{current:null},crypto:{randomUUID:()=> 'new-input'},externalConversation:external,creationSetup:()=>({workspace:'/fixture'}),detail:{focused:false},chatScroll:{current:null},setError(){},setQuoteNotice(){},quoteNoticeAfterSend(){},editDraft(){},clearTimeout(){},draftTimer:{current:null},stagedDraft:{current:'token'},outbox:box,deliveryScope:scope,latest,state,mode:'text',Date,stagedDraftPayload:{current:{}},
  saveDraft:async()=>{},reportError(error){throw error},outboxMatches:matches,ensureSession:async()=>created,deliver:async entry=>delivered.push(entry),
 });
 await send();
 assert.equal(delivered.length,1);assert.equal(delivered[0].expectedConfigurationRevision,17);
 assert.equal(delivered[0].expectedInterruptionRevision,0,'New-chat Stop intent remains captured at submit');
 if(external)assert.equal(Object.hasOwn(delivered[0],'expectedSource'),false);
 else assert.deepEqual(delivered[0].expectedSource,nativeDeliverySource(created));
});

for(const fields of [{expectedGenerationId:'old-run',allowNextTurn:true},{allowNextTurn:true},{expectedGenerationId:null}]){
 test(`historical unknown ${JSON.stringify(fields)} stays intact and cannot become a new unanchored intent`,async()=>{
  const entry={id:'local',commandId:'historical-input',sessionId:session.id,scope:scope(null,session),text:'Keep this original',attachmentIds:['file'],status:'unknown',...fields};
  const box=outbox(entry),before=structuredClone(box.current.current),errors=[];
  const retry=callback('retryMessage','\n async function send(',{outbox:box,outboxMatches:matches,deliveryScope:scope,latest:{current:{sessions:[session]}},setError:value=>errors.push(value)});
  await retry({localDelivery:{id:'local'}});
  assert.deepEqual(box.current.current,before,'No deletion, rebind, new identity or status downgrade');
  assert.equal(errors.length,1);assert.match(errors[0],/Use Check delivery.*saved message was kept/);
 });
}
