import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../../scripts/browser_dom_recorder.js',import.meta.url),'utf8');
function recorder({groups=0,singles=0,embedded=0}={}){
 const window={},timers=[];let clicked=false,now=0;
 const group={closest:()=>null},groupRows=Array.from({length:groups},()=>({parentElement:{closest:()=>null}}));
 const singleRows=Array.from({length:singles},()=>({closest:()=>null}));
 const embeddedRows=Array.from({length:embedded},()=>({closest:()=>group}));
 const message={getAttribute:()=>clicked?'new-message':'old-message'};
 const pane={closest:()=>null,getClientRects:()=>[{}],getAttribute:()=> 'false',querySelectorAll(selector){
  if(selector==='article.a-message[data-message-id]')return [message];
  if(selector==='[data-part="execution"]')return [...singleRows,...embeddedRows];
  if(selector==='[data-part="execution-group"],.a-work-group')return groupRows;
  throw Error('Unexpected selector '+selector);
 }};
 const button={disabled:false,getClientRects:()=>[{}],closest(selector){return selector==='[data-session-id]'?{getAttribute:()=> 'target'}:null},
  getAttribute(key){return key==='aria-label'?'Target':key==='aria-current'&&clicked?'page':null},click(){clicked=true}};
 const app={getAttribute:()=> 'chat',querySelector(selector){
  if(selector.includes('messages'))return pane;
  if(selector==='.a-work-heading strong')return {getAttribute:()=> 'Target'};
  if(selector.includes('Message Amplifier'))return {value:''};
  return null;
 },querySelectorAll:()=>[]};
 const document={title:'Synthetic',hidden:false,readyState:'complete',getElementById:()=>app,querySelectorAll:()=>[button]};
 const context={window,document,performance:{now:()=>now,timeOrigin:0},getComputedStyle:()=>({visibility:'visible'}),
  setTimeout:callback=>{timers.push(callback);return timers.length},clearTimeout:()=>{}};
 vm.runInNewContext(source,context);
 return {api:window.__unifiedDomTiming,tick(){now+=100;timers.shift()?.()}};
}

test('single-turn Work headers count alongside groups, without embedded double counting',()=>{
 const r=recorder({groups:2,singles:3,embedded:7}),state=r.api.report().current;
 assert.equal(state.workGroups,2);assert.equal(state.workSingleTurns,3);assert.equal(state.workHeaders,5);
 assert.equal(state.work,undefined,'The old groups-only field is not relabeled as completeness');
 r.api.dispose();
});

test('opening a group does not manufacture new top-level Work headers',()=>{
 const closed=recorder({groups:1,singles:1}),open=recorder({groups:1,singles:1,embedded:10});
 assert.equal(closed.api.report().current.workHeaders,open.api.report().current.workHeaders);
 closed.api.dispose();open.api.dispose();
});

test('minimum-header milestone includes single turns and explicitly disclaims completeness',()=>{
 const r=recorder({singles:2});assert.equal(r.api.start({id:'target',minWorkHeaders:2}).started,true);
 r.tick();const result=r.api.report();
 assert.equal(result.schemaVersion,4);assert.equal(result.marks.firstWorkHeaderMs,100);
 assert.equal(result.marks.minimumWorkHeadersMs,100);assert.equal(result.marks.expectedWorkCountMs,undefined);
 assert.ok(result.limitations.some(x=>x.includes('not canonical turns, events or complete history')));
 r.api.dispose();
});

test('no Work headers cannot satisfy a nonzero observation minimum',()=>{
 const r=recorder();r.api.start({id:'target',minWorkHeaders:1});r.tick();
 assert.equal(r.api.report().marks.minimumWorkHeadersMs,undefined);
 assert.equal(r.api.report().marks.readyCandidateMs,undefined);r.api.dispose();
});

test('an old groups-only minimum is rejected rather than silently lowering the gate',()=>{
 const r=recorder({singles:1});assert.match(r.api.start({id:'target',minWork:23}).error,/minWorkHeaders/);
 assert.equal(r.api.report().running,false);r.api.dispose();
});
