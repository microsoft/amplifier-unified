import test from 'node:test';
import assert from 'node:assert/strict';
import {followEarlierHistory,followLaterHistory} from '../src/history-scroll.js';

test('older pages load on upward scrolling, once per pending request',async()=>{
 const pane=new EventTarget();pane.scrollTop=300;let calls=0,release;
 const cleanup=followEarlierHistory(pane,()=>true,()=>{calls++;return new Promise(resolve=>{release=resolve})});
 assert.equal(calls,0);
 pane.scrollTop=500;pane.dispatchEvent(new Event('scroll'));await Promise.resolve();assert.equal(calls,0);
 pane.scrollTop=90;pane.dispatchEvent(new Event('scroll'));await Promise.resolve();assert.equal(calls,1);
 pane.scrollTop=30;pane.dispatchEvent(new Event('scroll'));await Promise.resolve();assert.equal(calls,1);
 release();await new Promise(resolve=>setTimeout(resolve,0));
 cleanup();pane.scrollTop=0;pane.dispatchEvent(new Event('scroll'));await Promise.resolve();assert.equal(calls,1);
});

test('unavailable earlier history does not start requests',async()=>{
 const pane=new EventTarget();pane.scrollTop=300;let calls=0;
 const cleanup=followEarlierHistory(pane,()=>false,()=>calls++);
 pane.scrollTop=0;pane.dispatchEvent(new Event('scroll'));await Promise.resolve();
 assert.equal(calls,0);cleanup();
});

test('downward Recent follows user input near 120px, never mount, resize or recursive append',async()=>{
 const pane=new EventTarget();Object.assign(pane,{scrollTop:0,scrollHeight:1000,clientHeight:300});
 const window=new EventTarget();pane.ownerDocument={defaultView:window};
 let calls=0,release,available=true;
 const follow=followLaterHistory(pane,()=>available,()=>{calls++;return new Promise(resolve=>{release=resolve})});
 const move=async(top,user=true)=>{
  if(user)pane.dispatchEvent(new Event('wheel'));
  pane.scrollTop=top;pane.dispatchEvent(new Event('scroll'));await Promise.resolve();
 };
 assert.equal(calls,0);
 await move(600,false);assert.equal(calls,0,'programmatic movement is not intent');
 pane.dispatchEvent(new Event('wheel'));window.dispatchEvent(new Event('resize'));
 await move(700,false);assert.equal(calls,0,'resize clears stale intent at the old boundary');
 await move(500);assert.equal(calls,0,'upward movement does not load');
 await move(579);assert.equal(calls,0,'121px is outside the threshold');
 await move(580);assert.equal(calls,1);
 await move(700);assert.equal(calls,1,'pending scrolls share one request');
 pane.scrollHeight=2200;release();await new Promise(resolve=>setTimeout(resolve,0));
 await move(1900,false);assert.equal(calls,1,'append/anchor adjustment cannot refill');
 pane.dispatchEvent(new Event('wheel'));
 follow.suppress(()=>{pane.scrollTop=1850});
 await move(1900,false);assert.equal(calls,1,'explicit restore clears pending user intent');
 available=false;await move(1901);assert.equal(calls,1,'exhausted/hidden/inert/collapsed gate');
 follow.dispose();available=true;await move(1902);assert.equal(calls,1);
});

test('tall Recent has no automatic fill and keyboard scroll is one user gesture',async()=>{
 const pane=new EventTarget();Object.assign(pane,{scrollTop:0,scrollHeight:200,clientHeight:900});
 let calls=0;
 const follow=followLaterHistory(pane,()=>true,()=>calls++);
 pane.dispatchEvent(new Event('scroll'));await Promise.resolve();assert.equal(calls,0);
 pane.scrollHeight=1500;
 const key=new Event('keydown');Object.assign(key,{key:'PageDown'});
 pane.dispatchEvent(key);pane.scrollTop=600;pane.dispatchEvent(new Event('scroll'));
 await new Promise(resolve=>setTimeout(resolve,0));assert.equal(calls,1);
 pane.scrollTop=601;pane.dispatchEvent(new Event('scroll'));await Promise.resolve();assert.equal(calls,1);
 follow.dispose();
});
