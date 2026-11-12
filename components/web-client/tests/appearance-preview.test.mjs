import test from 'node:test';
import assert from 'node:assert/strict';
import {clearAppearancePreview,previewAppearance} from '../src/appearance-preview.js';
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('distinct Settings mounts serialize old cleanup before the reopened preview using the App owner',async()=>{
 const clientDocument={current:{}},response=deferred(),calls=[];
 let oldMounted=true;
 const act=async(action,args)=>{calls.push([action,args?.name]);if(action==='appearance.preview'&&args.name==='old')return response.promise;return {accepted:true}};
 const mount=()=>({preview:(name,current)=>previewAppearance(clientDocument.current,act,{name},current),close:()=>clearAppearancePreview(clientDocument.current,act)});
 const oldMount=mount(),old=oldMount.preview('old',()=>oldMounted);
 await tick();oldMounted=false;
 const cleanup=oldMount.close(),newMount=mount(),next=newMount.preview('new',()=>true);
 await tick();assert.deepEqual(calls,[['appearance.preview','old']]);
 response.resolve({accepted:true});await Promise.all([old,cleanup,next]);
 assert.deepEqual(calls,[['appearance.preview','old'],['appearance.preview.cancel',undefined],['appearance.preview','new']]);
 assert.equal(clientDocument.current.active,true);
});
test('retired pending preview is cleared before a reopened owner can preview',async()=>{
 const response=deferred(),calls=[],owner={};let current=true;
 const act=async(action,args)=>{calls.push([action,args]);if(action==='appearance.preview'&&args.name==='old')return response.promise;return {accepted:true}};
 const old=previewAppearance(owner,act,{name:'old'},()=>current);await tick();current=false;
 const next=previewAppearance(owner,act,{name:'new'},()=>true);await tick();assert.equal(calls.length,1);
 response.resolve({accepted:true});await Promise.all([old,next]);
 assert.deepEqual(calls.map(([action,args])=>[action,args.name]),[['appearance.preview','old'],['appearance.preview.cancel',undefined],['appearance.preview','new']]);
 assert.equal(owner.active,true);
});
test('failure and failed cleanup do not poison a later preview',async()=>{
 const response=deferred(),owner=()=>{};let current=true;
 const old=previewAppearance(owner,async action=>action==='appearance.preview'?response.promise:Promise.reject(Error('cleanup failed')),{},()=>current);
 const rejected=assert.rejects(old,/cleanup failed/);await tick();current=false;response.reject(Error('response lost'));await rejected;
 let ran=false;await previewAppearance(owner,async()=>{ran=true;return {accepted:true}},{},()=>true);assert.equal(ran,true);
});
test('retired queued preview never dispatches and another owner remains independent',async()=>{
 const response=deferred(),owner=()=>{};let ran=0;
 const first=previewAppearance(owner,()=>response.promise,{},()=>true);await tick();
 const stale=previewAppearance(owner,()=>{ran++;return {accepted:true}},{},()=>false);
 await previewAppearance(()=>{},()=>{ran++;return {accepted:true}},{},()=>true);assert.equal(ran,1);
 response.resolve({accepted:true});await Promise.all([first,stale]);assert.equal(ran,1);
});
test('settled cleanup is ordered before the next preview and rejection does not activate',async()=>{
 const response=deferred(),owner={active:true},calls=[];
 const clear=clearAppearancePreview(owner,()=>{calls.push('clear');return response.promise});
 const next=previewAppearance(owner,()=>{calls.push('preview');return {accepted:false,error:'not accepted'}},{},()=>true);
 const rejected=assert.rejects(next,/not accepted/);await tick();assert.deepEqual(calls,['clear']);response.resolve({accepted:true});await clear;await rejected;
 assert.deepEqual(calls,['clear','preview']);assert.equal(owner.active,false);
});
for(const retired of [false,true])test(`queued cleanup waits for ${retired?'retired':'current'} preview settlement, cancels once, then admits the next preview`,async()=>{
 const previewResponse=deferred(),cancelResponse=deferred(),owner={},calls=[];let current=true;
 const act=async(action,args)=>{
  calls.push([action,args]);
  if(action==='appearance.preview'&&args.name==='old')return previewResponse.promise;
  if(action==='appearance.preview.cancel')return cancelResponse.promise;
  return {accepted:true};
 };
 const old=previewAppearance(owner,act,{name:'old'},()=>current);await tick();
 if(retired)current=false;
 const clear=clearAppearancePreview(owner,act),duplicateClear=clearAppearancePreview(owner,act);
 const next=previewAppearance(owner,act,{name:'new'},()=>true);
 await tick();assert.deepEqual(calls,[['appearance.preview',{name:'old'}]]);
 previewResponse.resolve({accepted:true});await tick();
 assert.deepEqual(calls,[['appearance.preview',{name:'old'}],['appearance.preview.cancel',{}]],'Cleanup must finish before a later preview starts.');
 cancelResponse.resolve({accepted:true});await Promise.all([old,clear,duplicateClear,next]);
 assert.deepEqual(calls,[['appearance.preview',{name:'old'}],['appearance.preview.cancel',{}],['appearance.preview',{name:'new'}]]);
 assert.equal(owner.active,true);
});
test('clearing an inactive preview does not dispatch',async()=>{
 const calls=[],result=await clearAppearancePreview({},async action=>{calls.push(action);return {accepted:true}});
 assert.deepEqual(calls,[]);assert.equal(result.skipped,true);
});
test('fresh-owner cleanup is a no-op even when a rejected preview is queued next',async()=>{
 const owner={},calls=[];
 const clear=clearAppearancePreview(owner,()=>{calls.push('clear');return {accepted:true}});
 const next=previewAppearance(owner,()=>{calls.push('preview');return {accepted:false,error:'not accepted'}},{},()=>true);
 const rejected=assert.rejects(next,/not accepted/);
 assert.equal((await clear).skipped,true);await rejected;
 assert.deepEqual(calls,['preview'],'Only an active or still-settling preview needs cancellation.');
 assert.notEqual(owner.active,true);
});
