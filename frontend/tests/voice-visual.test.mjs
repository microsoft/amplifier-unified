import test from 'node:test';
import assert from 'node:assert/strict';
import {VoiceVisualClient} from '../src/voice-visual.js';
const target={status:'connected',id:'call-one',sessionId:'session-one'};
function client(){const requests=[],states=[];const value=new VoiceVisualClient({request:async(...args)=>{requests.push(args);return {}},onState:s=>states.push({...s}),getVoice:()=>target,media:{}});return {value,requests,states}}

test('missing browser permission API is explicit, no request or capture',async()=>{const {value,requests}=client();await assert.rejects(value.choose(),/unavailable in this browser/);assert.equal(requests.length,0);value.dispose()});
test('reconnect and replaced call revoke, release tracks, never restore a source',()=>{for(const reason of ['disconnect','replace','revoke']){const {value,requests}=client();let stopped=0;value.stream={getTracks:()=>[{stop(){stopped++}}]};value.grant={id:'grant',callId:reason==='replace'?'old-call':target.id};value.sync(target,reason!=='disconnect',reason==='revoke'?{available:false}:{available:true});assert.equal(stopped,1);assert.equal(value.grant,null);assert.match(requests[0][0],/revoke/);value.sync(target,true,{available:true,id:'grant'});assert.equal(value.stream,null);value.dispose()}});
test('stale commands return an explicit error without attempting a video frame',async()=>{const {value,requests}=client();await value.capture({id:'late',grantId:'old',callId:'old',sessionId:'other'});assert.match(requests[0][1].body.error,/no longer authorized/);assert.equal(requests[0][1].body.image,undefined);value.dispose()});

test('native check and grant are separate explicit requests without browser capture',async()=>{
 const requests=[],host={id:'fixture-host',label:'Fixture host'};let voice={...target};
 const value=new VoiceVisualClient({getVoice:()=>voice,media:{getDisplayMedia(){throw Error('must not capture browser')}},request:async(path,options)=>{requests.push([path,options]);if(path.endsWith('/status'))return {available:true,host,hostInstanceId:'instance'};if(path.endsWith('/grant'))return {id:'native',callId:target.id,sessionId:target.sessionId,expiresAt:Date.now()/1000+60,source:{kind:'native-foreground',label:host.label}};return {}}});
 await assert.rejects(value.chooseNative(),/Check the desktop host/);assert.equal(requests.length,0);
 await value.checkNative();assert.equal(value.grant,null);assert.equal(requests.length,1);
 await value.chooseNative();assert.equal(value.stream,null);assert.equal(value.grant.id,'native');assert.deepEqual(requests[1][1].body.source,{kind:'native-foreground',hostId:'fixture-host',hostInstanceId:'instance'});
 value.sync(voice,false,{available:true,id:'native'});assert.equal(value.grant,null);assert.match(requests.at(-1)[0],/revoke/);value.dispose();
});
