import test from 'node:test';
import assert from 'node:assert/strict';
import {followEarlierHistory} from '../src/history-scroll.js';

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
