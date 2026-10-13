import test from 'node:test';
import assert from 'node:assert/strict';
import {createStateStream} from '../src/state-stream.js';
class Socket {static last;constructor(url){this.url=url;Socket.last=this}close(code){this.code=code;this.onclose?.()}}
test('state socket preserves identity, events and close/reconnect ownership',()=>{
 const stream=createStateStream('/api/events?transport=delta-v1&clientId=private',['websocket'],{Socket,origin:'https://example.test'});
 const socket=Socket.last;assert.equal(socket.url,'wss://example.test/api/events/ws?transport=delta-v1&clientId=private');
 let opened=0,errors=0,frame;stream.onopen=()=>opened++;stream.onerror=()=>errors++;
 stream.addEventListener('state',e=>frame=JSON.parse(e.data));socket.onopen();assert.equal(stream.readyState,1);assert.equal(opened,1);
 socket.onmessage({data:JSON.stringify({event:'state',data:{revision:1}})});assert.deepEqual(frame,{revision:1});
 socket.onclose();assert.equal(stream.readyState,2);assert.equal(errors,1);
 stream.close();assert.equal(errors,1,'intentional close must not restart');
});
test('malformed frames close the socket; older hosts keep SSE',()=>{
 let selected;
 const legacy=createStateStream('/api/events', ['http','sse'],{Source:class{constructor(path){selected=path}}});assert.ok(legacy);assert.equal(selected,'/api/events');
 const stream=createStateStream('/api/events',['websocket'],{Socket,origin:'http://localhost'});let errors=0;stream.onerror=()=>errors++;
 Socket.last.onmessage({data:'not json'});assert.equal(Socket.last.code,1002);assert.equal(errors,1);
});
