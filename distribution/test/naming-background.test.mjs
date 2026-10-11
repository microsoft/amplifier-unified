import test from 'node:test';import assert from 'node:assert/strict';
import {createBackgroundNaming} from '../src/naming-background.js';
function fixture(){
 let ready=true,mediaIdle=true,active=0;const calls=[],pages=[],projected=[];
 const rows=Array.from({length:32},(_,i)=>({uri:'ahp-session:/'+i,nativeSessionId:'native-'+i}));
 const owner={inspectBackgroundReadiness:()=>({ready:ready&&active===0}),withExternalMutation:async(name,invoke)=>{assert.equal(name,'composition');active++;try{return await invoke();}finally{active--; }},commitSessionTitle:async(uri,args,commit)=>{assert.equal(active,0);projected.push([uri,args,await commit()]);}};
 const background=createBackgroundNaming({host:()=>owner,idle:()=>mediaIdle,list:async cursor=>{pages.push(cursor);return {items:cursor?[{uri:'ahp-session:/next',nativeSessionId:'next'}]:rows,nextCursor:cursor?null:'page2'};},nativeNaming:async(uri,op,args)=>{assert.equal(active,1);assert.equal(op,'backfill');assert.deepEqual(args,{});calls.push(uri);return {status:'named',applied:true,title:'Named',commandId:'named:'+uri};}});
 return {background,calls,pages,projected,rows,setReady:value=>ready=value,setMediaIdle:value=>mediaIdle=value};
}
test('background discovery waits for connected idle Host and settled media',async()=>{
 const f=fixture();f.setReady(false);await f.background.tick();assert.deepEqual(f.pages,[]);f.setReady(true);f.setMediaIdle(false);await f.background.tick();assert.deepEqual(f.pages,[]);f.setMediaIdle(true);await f.background.tick();assert.equal(f.calls.length,2);assert.equal(f.projected.length,2);await f.background.close();await f.background.tick();assert.equal(f.calls.length,2);
});
test('two attempts per idle pass rotate through the whole bounded page without losing rows',async()=>{
 const f=fixture();for(let i=0;i<16;i++)await f.background.tick();assert.deepEqual(f.calls,f.rows.map(row=>row.uri));assert.deepEqual(f.pages,[undefined]);await f.background.tick();assert.equal(f.calls.at(-1),'ahp-session:/next');assert.deepEqual(f.pages,[undefined,'page2']);await f.background.close();
});
test('unsettled, child, archived and unavailable histories do not reach a provider',async()=>{
 const f=fixture();for(const [index,key]of ['parentUri','isArchived','productHidden','nativeDeleted'].entries())f.rows[index][key]=true;f.rows[4].availability='missing';f.rows[5].workspaceAvailability='missing';await f.background.tick();assert.deepEqual(f.calls,['ahp-session:/6','ahp-session:/7']);await f.background.close();
});
test('in-flight naming is drained once; losing a reply never immediately replays it',async()=>{
 let release,started,calls=0,active=0;const entering=new Promise(r=>started=r),gate=new Promise(r=>release=r);
 const host={inspectBackgroundReadiness:()=>({ready:active===0}),withExternalMutation:async(_,fn)=>{active++;try{return await fn();}finally{active--;}}};
 const n=createBackgroundNaming({host:()=>host,list:async()=>({items:[{uri:'ahp-session:/x',nativeSessionId:'x'}]}),nativeNaming:async()=>{calls++;started();await gate;throw Error('lost response');}});
 const pending=n.tick();await entering;const duplicate=n.tick();const closing=n.close();assert.equal(calls,1);assert.equal(n.diagnostics().pending,1);release();await duplicate;await pending;await closing;assert.equal(calls,1);assert.equal(n.diagnostics().failures,1);
});
