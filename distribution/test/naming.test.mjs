import test from 'node:test';import assert from 'node:assert/strict';import {composeNaming} from '../src/naming.js';
const marker={version:1,method:'_amplifier/naming',passiveMetadata:true,commandIds:true,replayUnknown:false};
const scope='ahp-session:/owned',binding={session:scope,engineId:'native',nativeSessionId:'named',workingDirectory:'/owned'};
const event=(revision=1,name='advisory')=>({sessionId:'named',event:{type:'session.naming',name,nameRevision:revision}});
function fixture(){let projected;let current={title:'First turn generated',titleRevision:'1',metadata:{naming:{automatic:true,nameRevision:1,policyRevision:0}}},calls=[],release,active=0;const barrier=new Promise(r=>release=r);
 const host={readSessionTitle:async()=>current,commitSessionTitle:async(_s,_e,persist)=>{await barrier;calls.push(_e);projected=await persist({...binding,...current});return projected;},withExternalMutation:(_id,work)=>{active++;return Promise.resolve().then(work).finally(()=>active--);}};
 const admin={namingCapabilities:async()=>marker,performNaming:async request=>{calls.push(request);return {title:current.title,naming:current.metadata.naming,applied:true};}};
 return {admin,host,calls,release,get projected(){return projected;},get active(){return active;},manual:()=>{current={title:'Newer manual',titleRevision:'2',metadata:{naming:{automatic:false,nameRevision:2,policyRevision:1}}};}};
}
test('projection coalesces accepted events, tracks pending work and drains current canonical snapshot',async()=>{
 const f=fixture(),n=await composeNaming({admin:f.admin,engineId:'native',host:()=>f.host,inspectSession:async()=>binding,hostPortsSupported:true});assert.equal(n.event(binding,event()),true);assert.equal(n.event(binding,event(2,'old event text')),true);await new Promise(r=>setImmediate(r));assert.equal(n.diagnostics().pending,1);assert.equal(f.active,1);f.manual();let closed=false;const closing=n.close().then(()=>closed=true);await new Promise(r=>setImmediate(r));assert.equal(closed,false);f.release();await closing;assert.equal(n.diagnostics().failures,0);assert.equal(f.active,0);assert.equal(f.calls.length,1);assert.equal(f.projected.title,'Newer manual');assert.equal(f.projected.metadata.naming.automatic,false);assert.equal(n.event(binding,event()),false);
});
test('malformed/misbound events and missing actual marker/Host ports do not dispatch',async()=>{
 const f=fixture(),n=await composeNaming({admin:f.admin,engineId:'native',host:()=>f.host,inspectSession:async()=>binding,hostPortsSupported:true});
 for(const bad of [{...event(),sessionId:'foreign'},event(-1),event(NaN),event(1,'x'.repeat(201)),{sessionId:'named',event:{type:'session.naming',nameRevision:1}}])assert.equal(n.event(binding,bad),false);assert.equal(f.calls.length,0);await n.close();
 for(const supported of [false,true]){let calls=0;const old=await composeNaming({admin:{namingCapabilities:async()=>{calls++;return {...marker,replayUnknown:true};},performNaming:async()=>{throw Error('must not dispatch');}},engineId:'native',host:()=>f.host,inspectSession:async()=>binding,hostPortsSupported:supported});assert.equal(old.available,false);assert.equal(calls,supported?1:0);}
});
test('projection failure is observed and does not become successful title publication',async()=>{
 const f=fixture();f.release();f.host.commitSessionTitle=async()=>{throw Error('owned projection failure');};let failures=0;const n=await composeNaming({admin:f.admin,engineId:'native',host:()=>f.host,inspectSession:async()=>binding,hostPortsSupported:true,onFailure:()=>failures++});n.event(binding,event());await n.drain();assert.equal(failures,1);assert.equal(n.diagnostics().failures,1);assert.equal(n.diagnostics().pending,0);await n.close();
});

test('projection bound preserves pending work and reports saturation without a hidden queue',async()=>{
 const f=fixture(),n=await composeNaming({admin:f.admin,engineId:'native',host:()=>f.host,inspectSession:async session=>({...binding,session}),hostPortsSupported:true});
 for(let i=0;i<32;i++)assert.equal(n.event({...binding,session:'ahp-session:/'+i},event()),true);
 assert.equal(n.event({...binding,session:'ahp-session:/overflow'},event()),false);assert.equal(n.diagnostics().pending,32);assert.equal(n.diagnostics().dropped,1);f.release();await n.close();assert.equal(n.diagnostics().pending,0);
});

test('event receipt identity stays stable for one selected Native revision across deliveries',async()=>{
 const f=fixture();f.release();const n=await composeNaming({admin:f.admin,engineId:'native',host:()=>f.host,inspectSession:async()=>binding,hostPortsSupported:true});
 n.event(binding,event(7));await n.drain();f.manual();n.event(binding,event(7,'different advisory'));await n.drain();n.event(binding,event(8));await n.drain();assert.equal(f.calls[0].commandId,f.calls[1].commandId);assert.notEqual(f.calls[1].commandId,f.calls[2].commandId);assert.equal(f.projected.title,'Newer manual');await n.close();
});
