import test from 'node:test';
import assert from 'node:assert/strict';
import {GifPlayer} from '../src/gif-player.js';

test('finite loops end without allocating another frame and never autoplay',async()=>{
 let callback,closed=0,calls=0;
 const decoder={tracks:{ready:Promise.resolve(),selectedTrack:{frameCount:1}},
  decode:async()=>{calls++;return {image:{duration:140_000,close(){closed++}}}},close(){}};
 const reports=[];
 const player=new GifPlayer({decoder,draw:()=>{},loopCount:null,report:(...args)=>reports.push(args),
  schedule:fn=>{callback=fn;return 1},cancel:()=>{}});
 await player.ready();assert.equal(player.playing,false);assert.equal(calls,1);
 player.play();callback();assert.equal(player.playing,false);assert.equal(calls,1);
 assert.deepEqual(reports.at(-1),['ready','ended']);player.dispose();assert.equal(closed,1);
});

test('decoder error is distinct from ready and closes the owned decoder',async()=>{
 let closed=0;
 const decoder={tracks:{ready:Promise.resolve(),selectedTrack:{frameCount:2}},
  decode:async()=>{throw Error('synthetic corruption')},close(){closed++}};
 const reports=[];
 const player=new GifPlayer({decoder,draw:()=>assert.fail('corrupt pixels'),report:(...args)=>reports.push(args)});
 await player.ready();assert.equal(closed,1);
 assert.deepEqual(reports.at(-1),['error','paused','synthetic corruption']);
});

test('multiple inline players bound simultaneous decode work to two',async()=>{
 let live=0,peak=0;
 const releases=[];
 const decoder=()=>({tracks:{ready:Promise.resolve(),selectedTrack:{frameCount:1}},close(){},
  decode:()=>new Promise(resolve=>{
   peak=Math.max(peak,++live);releases.push(()=>{live--;resolve({image:{duration:80_000,close(){}}})});
  })});
 const players=Array.from({length:4},()=>new GifPlayer({decoder:decoder(),draw:()=>{}}));
 const ready=players.map(player=>player.ready());
 await new Promise(resolve=>setImmediate(resolve));assert.equal(live,2);
 while(releases.length){releases.shift()();await new Promise(resolve=>setImmediate(resolve))}
 await Promise.all(ready);assert.equal(peak,2);players.forEach(player=>player.dispose());
});

test('unmounted queued players never call a closed decoder',async()=>{
 const releases=[],calls=[0,0,0],closed=[0,0,0];
 const players=calls.map((_,index)=>new GifPlayer({draw:()=>{},decoder:{
  tracks:{ready:Promise.resolve(),selectedTrack:{frameCount:1}},
  decode:()=>{calls[index]++;return new Promise(resolve=>releases.push(()=>resolve({image:{duration:80_000,close(){}}})))},
  close(){closed[index]++}
 }}));
 const ready=players.map(player=>player.ready());
 await new Promise(resolve=>setImmediate(resolve));
 players[2].dispose();
 while(releases.length){releases.shift()();await new Promise(resolve=>setImmediate(resolve))}
 await Promise.all(ready);
 assert.deepEqual(calls,[1,1,0]);players.forEach(player=>player.dispose());assert.deepEqual(closed,[1,1,1]);
});