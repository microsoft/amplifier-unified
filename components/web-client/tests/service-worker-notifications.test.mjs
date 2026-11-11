import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../src/service-worker.js',import.meta.url),'utf8');
test('notification click permits only a same-origin exact chat link, never arbitrary URLs or actions',async()=>{
 const handlers={},opened=[];
 vm.runInNewContext(source,{self:{addEventListener:(name,handler)=>handlers[name]=handler,clients:{openWindow:async url=>opened.push(url)}}});
 let closed=0;
 for(const id of ['chat /?action=approval.respond',null,'',42,'bad\nid','x'.repeat(201)]){
  const waits=[];handlers.notificationclick({notification:{data:{sessionId:id},close:()=>closed++},waitUntil:value=>waits.push(value)});
  await Promise.all(waits);
 }
 assert.equal(closed,6);
 assert.deepEqual(opened,['/?chat=chat%20%2F%3Faction%3Dapproval.respond']);
});