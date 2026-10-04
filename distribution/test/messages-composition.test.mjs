import test from 'node:test';
import assert from 'node:assert/strict';
import {composeMessages,messagePrincipalEngines} from '../src/messages.js';
import {composeCapabilities} from '../src/capabilities.js';
import {composeQuiescence} from '../src/quiescence.js';

const account='launcher-account',scope='ahp-session:/original';
const selected={engineId:'native',nativeSessionId:'original-native',workingDirectory:'/history',executionDirectory:'/relocated'};
function fixture({inspect=async()=>selected,negotiate=async()=>true}={}){
 let options,closed=0,reads=0;
 const create=opts=>{options=opts;return {connection:{close:async()=>closed++},capabilities:{manifest:{version:1,topics:{'message-metadata':{uri:'amplifier-capability://native/message-metadata'}},actions:{'messages.get':{topic:'message-metadata',operation:'messages.get'}}},negotiate,action:async(_request,context)=>options.resolveSession(context),read:async(_request,context)=>options.resolveSession(context)}};};
 return {open:()=>composeMessages(create,{id:'native',command:'/passive',account:'engine-spoof'},{account,inspectSession:async uri=>{reads++;assert.equal(uri,scope);return inspect(uri);}}),get options(){return options;},get closed(){return closed;},get reads(){return reads;}};
}
test('root-derived principal is limited to the admitted native engine without modifying config',()=>{
 const engines=[{id:'native',messagePrincipal:'spoof'},{id:'foreign',messagePrincipal:'spoof'}];
 assert.deepEqual(messagePrincipalEngines(engines,'native',account),[{id:'native',messagePrincipal:account},{id:'foreign'}]);
 assert.deepEqual(engines,[{id:'native',messagePrincipal:'spoof'},{id:'foreign',messagePrincipal:'spoof'}]);
 assert.deepEqual(messagePrincipalEngines(engines,undefined,account),[{id:'native'},{id:'foreign'}]);
});
test('compositor supplies account and passive resolver preserves canonical history after relocation',async()=>{
 const f=fixture(),owner=await f.open(),composed=composeCapabilities([owner.capabilities],{account});
 assert.equal(f.options.account,account);
 const context={account:'browser-spoof',clientId:'another-client',session:{uri:scope,workingDirectory:'/body-spoof',nativeSessionId:'body-spoof'}};
 assert.deepEqual(await composed.action({topic:'message-metadata',operation:'messages.get',args:{account:'args-spoof'}},context),selected);
 assert.deepEqual(await composed.read({topic:'message-metadata',scope,clientId:'another-client'},context),selected);
 assert.equal(f.reads,2);
 // A cold composition still resolves original history, independent of default/execution directory.
 const cold=fixture(),next=await cold.open();assert.deepEqual(await next.capabilities.action({}, {...context,account}),selected);
});
test('foreign account is rejected before history inspection and malformed/foreign native identities are refused',async()=>{
 const f=fixture();await f.open();
 await assert.rejects(f.options.resolveSession({account:'foreign',session:{uri:scope}}),/account mismatch/);assert.equal(f.reads,0);
 await assert.rejects(f.options.resolveSession({account,session:{uri:'host'}}),/session required/);assert.equal(f.reads,0);
 for(const row of [{...selected,engineId:'foreign'},{...selected,nativeSessionId:''},{...selected,workingDirectory:'relative'}]){
  const foreign=fixture({inspect:async()=>row});await foreign.open();await assert.rejects(foreign.options.resolveSession({account,session:{uri:scope}}),/admitted native engine/);
 }
});
test('failed passive negotiation closes its connection before composition exits',async()=>{
 const f=fixture({negotiate:async()=>{throw Error('negotiation failed');}});await assert.rejects(f.open(),/negotiation failed/);assert.equal(f.closed,1);assert.equal(f.reads,0);
});
test('unsupported peer closes without registering an empty owner',async()=>{
 const f=fixture({negotiate:async()=>false}),owner=await f.open();assert.equal(owner.ready,false);assert.equal(f.closed,1);
});
test('message intake participant remains distinct from native administration',async()=>{
 const f=fixture(),owner=await f.open(),participant={id:'native-message-metadata',acquire:async()=>null};
 owner.capabilities.quiescenceParticipant=participant;
 const admin={manifest:{version:1,topics:{configuration:{}}}},adminParticipant={id:'native-administration',acquire:async()=>null};
 const coverage=composeQuiescence({instanceId:'i',dataScope:'d'},[admin,owner.capabilities],{bindings:new Map([[admin,adminParticipant]])});
 assert.equal(coverage.coverage.capabilities['message-metadata'],participant.id);assert.equal(coverage.coverage.capabilities.configuration,adminParticipant.id);
 assert.deepEqual(coverage.requiredOwners,[adminParticipant.id,participant.id]);assert.equal(coverage.participants[1],participant);
});
