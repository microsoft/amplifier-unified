import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const vite=await createServer({cacheDir:'.vite-pin-settlement-tests',server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {PinnedItems}=await vite.ssrLoadModule('/src/shell/navigation-components.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>vite.close());
const ids=['session:a','session:b'],expected=['session:b','session:a'];
const item=id=>({id,kind:'session',row:{id:id.slice(8),title:id,pinned:true}});
async function fixture(dispatch=async()=>({accepted:true})){
 let current={generation:1,selectedSessionId:'a',selectedWorkspaceId:'w',navigationResources:['pinnedNavigation'],view:{},pinnedItemIds:ids,pinnedNavigation:{items:ids.map(item),total:2,index:0,pages:1}};
 const listeners=new Set(),calls=[];
 const host={getSnapshot:()=>current,subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn)}};
 const model={host,state:current,view:{},act:(...args)=>{calls.push(args);return dispatch(...args)},setDraft(){},choose(){}};
 let root;const element=()=>React.createElement(PinnedItems,{page:current.pinnedNavigation,model,now:0});
 await act(async()=>{root=create(element())});
 const update=async patch=>{await act(async()=>{current={...current,...patch};model.state=current;for(const fn of [...listeners])fn();root.update(element())})};
 const key=async(id,key)=>{await act(async()=>root.root.findByProps({'aria-label':'Reorder '+id}).props.onKeyDown({key,preventDefault(){}}))};
 return {root,calls,listeners,update,key,get current(){return current},order:()=>root.root.findAll(node=>node.type==='div'&&node.props['data-order-id']).map(node=>node.props['data-order-id']),busy:id=>root.root.findByProps({'aria-label':'Reorder '+id}).props['aria-disabled'],close:async()=>act(async()=>root.unmount())};
}
test('accepted command retains preview and blocks a second reorder until its exact query page arrives',async()=>{
 const f=await fixture();try{
  await f.key('session:b','ArrowUp');assert.deepEqual(f.order(),expected);assert.equal(f.busy('session:b'),true);
  await f.update({pinnedItemIds:expected,navigationResourcePending:{pinnedNavigation:{status:'pending'}}});
  assert.deepEqual(f.order(),expected);await f.key('session:b','ArrowDown');assert.equal(f.calls.length,1);
  await f.update({pinnedNavigation:{...f.current.pinnedNavigation,items:expected.map(item)},navigationResourcePending:{}});
  assert.equal(f.busy('session:b'),undefined);assert.deepEqual(f.order(),expected);assert.equal(f.listeners.size,0);
  await f.key('session:b','ArrowDown');assert.deepEqual(f.calls[1],['navigation.pinOrder',{ids}]);
  await f.update({pinnedItemIds:ids,pinnedNavigation:{...f.current.pinnedNavigation,items:ids.map(item)}});
  assert.deepEqual(f.order(),ids);assert.equal(f.busy('session:b'),undefined);
 }finally{await f.close()}
});
test('a concurrent authoritative order overrides intent without replay or a stuck busy control',async()=>{
 const f=await fixture();try{
  await f.key('session:b','ArrowUp');
  await f.update({pinnedItemIds:['session:a'],pinnedNavigation:{items:[item('session:a')],total:1,index:0,pages:1}});
  assert.equal(f.calls.length,1);assert.equal(f.listeners.size,0);assert.deepEqual(f.order(),['session:a']);
  assert.match(f.root.toJSON().map?JSON.stringify(f.root.toJSON()):'',/Pins changed elsewhere/);
 }finally{await f.close()}
});
test('query failure releases busy state with a read error and never replays the saved command',async()=>{
 const f=await fixture();try{
  await f.key('session:b','ArrowUp');await f.update({pinnedItemIds:expected,navigationResourcePending:{pinnedNavigation:{status:'retry'}}});
  assert.equal(f.listeners.size,0);assert.equal(f.busy('session:b'),undefined);assert.equal(f.calls.length,1);assert.match(JSON.stringify(f.root.toJSON()),/display could not be refreshed/);
 }finally{await f.close()}
});
test('changing the page scope cancels the old order wait',async()=>{
 const f=await fixture();try{await f.key('session:b','ArrowUp');await f.update({view:{navPinnedPage:1}});assert.equal(f.listeners.size,0);assert.equal(f.busy('session:b'),undefined);assert.match(JSON.stringify(f.root.toJSON()),/Pinned view changed/)}finally{await f.close()}
});
test('unmount cancels a held stream wait and a late receipt cannot subscribe again',async()=>{
 let release;const f=await fixture(()=>new Promise(resolve=>{release=resolve}));await f.key('session:b','ArrowUp');await f.close();await act(async()=>release({accepted:true}));assert.equal(f.listeners.size,0);assert.equal(f.calls.length,1);
 const g=await fixture();await g.key('session:b','ArrowUp');assert.equal(g.listeners.size,1);await g.close();assert.equal(g.listeners.size,0);
});
test('offline stream timeout is bounded and does not replay intent',async t=>{
 const f=await fixture();t.mock.timers.enable({apis:['setTimeout']});try{
  await f.key('session:b','ArrowUp');await act(async()=>t.mock.timers.tick(10001));assert.equal(f.listeners.size,0);assert.equal(f.busy('session:b'),undefined);assert.equal(f.calls.length,1);assert.match(JSON.stringify(f.root.toJSON()),/display has not arrived/);
 }finally{t.mock.timers.reset();await f.close()}
});
