import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {InlineMedia}=await server.ssrLoadModule('/src/inline-media.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const originalFetch=globalThis.fetch,originalObserver=globalThis.IntersectionObserver;
let root,requests;
const render=props=>React.createElement(InlineMedia,{sessionId:'chat-a',messageId:'answer',...props});
const metadata=(overflow=false)=>({items:[],overflow,mediaRevision:0});
const complete=async(index,value=metadata())=>act(async()=>requests[index].resolve(new Response(JSON.stringify(value))));
test.beforeEach(()=>{
 requests=[];globalThis.IntersectionObserver=undefined;
 globalThis.fetch=(url,options)=>new Promise((resolve,reject)=>requests.push({url,signal:options.signal,resolve,reject}));
});
test.afterEach(async()=>{await act(async()=>root?.unmount());root=null;globalThis.fetch=originalFetch;globalThis.IntersectionObserver=originalObserver});
test.after(()=>server.close());

test('known-empty and uncovered selected messages issue no metadata requests',async()=>{
 await act(async()=>{root=create(render({linkedMediaAvailable:false}))});
 for(const props of [{mediaRevision:99,linkedMediaAvailable:false},{sessionId:'chat-b'},
  {sessionId:'chat-b',linkedMediaAvailable:null},{sessionId:'chat-b',linkedMediaAvailable:'true'}])
  await act(async()=>root.update(render(props)));
 assert.equal(requests.length,0);assert.equal(root.root.findAllByProps({role:'alert'}).length,0);
});

test('positive coverage requests once, false aborts and fences an unresolved response',async()=>{
 await act(async()=>{root=create(render({linkedMediaAvailable:true}))});
 assert.equal(requests.length,1);assert.match(requests[0].url,/sessionId=chat-a/);
 await act(async()=>root.update(render({linkedMediaAvailable:true})));
 assert.equal(requests.length,1,'unchanged publication does not repeat the request');
 await act(async()=>root.update(render({linkedMediaAvailable:false})));
 assert.equal(requests[0].signal.aborted,true);
 await complete(0,metadata(true));assert.equal(root.root.findAllByType('p').length,0);
 await act(async()=>root.update(render({linkedMediaAvailable:true})));
 assert.equal(requests.length,2,'relink at revision zero recovers discovery');
 await complete(1,metadata(true));assert.equal(root.root.findAllByType('p').length,1);
 await act(async()=>root.update(render({linkedMediaAvailable:false})));
 assert.equal(root.root.findAllByType('p').length,0,'unlink clears completed metadata too');
});

test('selection and message changes cannot display the previous owner while coverage is pending',async()=>{
 await act(async()=>{root=create(render({linkedMediaAvailable:true}))});
 await complete(0,metadata(true));assert.equal(root.root.findAllByType('p').length,1);
 await act(async()=>root.update(render({sessionId:'chat-b'})));
 assert.equal(root.root.findAllByType('p').length,0);assert.equal(requests.length,1);
 await act(async()=>root.update(render({sessionId:'chat-b',linkedMediaAvailable:true})));
 assert.equal(requests.length,2);
 await act(async()=>root.update(render({sessionId:'chat-b',messageId:'other'})));
 assert.equal(requests[1].signal.aborted,true);await complete(1,metadata(true));
 assert.equal(root.root.findAllByType('p').length,0);assert.equal(requests.length,2);
});

test('metadata errors are owned by the message and clear when its registry becomes empty',async()=>{
 await act(async()=>{root=create(render({linkedMediaAvailable:true}))});
 await act(async()=>requests[0].reject(Error('synthetic failure')));
 assert.equal(root.root.findAllByProps({role:'alert'}).length,1);
 await act(async()=>root.update(render({linkedMediaAvailable:false})));
 assert.equal(root.root.findAllByProps({role:'alert'}).length,0);
 await act(async()=>root.update(render({linkedMediaAvailable:true,mediaRevision:2})));
 assert.equal(requests.length,2);await complete(1);
 assert.equal(root.root.findAllByProps({role:'alert'}).length,0);
});

test('positive registry coverage keeps viewport ownership and disposes pending reads',async()=>{
 let visible;
 globalThis.IntersectionObserver=class{constructor(callback){visible=callback}observe(){}disconnect(){}};
 await act(async()=>{root=create(render({linkedMediaAvailable:true}),{createNodeMock:()=>({})})});
 assert.equal(requests.length,0);
 await act(async()=>visible([{isIntersecting:true}]));assert.equal(requests.length,1);
 await act(async()=>visible([{isIntersecting:false}]));assert.equal(requests[0].signal.aborted,true);
 await complete(0,metadata(true));assert.equal(root.root.findAllByType('p').length,0);
 await act(async()=>visible([{isIntersecting:true}]));assert.equal(requests.length,2);
 await act(async()=>root.unmount());root=null;assert.equal(requests[1].signal.aborted,true);
});
