import test from 'node:test';
import assert from 'node:assert/strict';
import {GifPlayer} from '../src/gif-player.js';
import {MediaReporter} from '../src/media-reporter.js';

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

function reportClock(){
 let time=0,next=0;
 const timers=new Map();
 return {now:()=>time,schedule:(fn,delay)=>{const id=++next;timers.set(id,{fn,at:time+delay});return id},
  cancel:id=>timers.delete(id),timers,
  advance(to){
   for(;;){
    const due=[...timers].filter(([,row])=>row.at<=to).sort((a,b)=>a[1].at-b[1].at)[0];
    if(!due)break;time=due[1].at;timers.delete(due[0]);due[1].fn();
   }
   time=to;
  }};
}
const settle=async()=>{for(let n=0;n<8;n++)await Promise.resolve()};
const snapshot=(frame,playback='playing',renderState='ready',detail='')=>
 ({renderState,playback,detail,presentedFrames:frame,mediaTime:frame/100});
function reporting(send){
 const clock=reportClock(),posts=[],states=[];
 const reporter=new MediaReporter({...clock,generation:123,
  send:value=>{posts.push({at:clock.now(),...value});return send?.(value)},
  onState:value=>states.push(value)});
 return {clock,reporter,posts,states};
}

test('100 Hz presentation keeps advancing while same-state POSTs stay at most 1 Hz',async()=>{
 const {clock,reporter,posts,states}=reporting();
 reporter.report(snapshot(0,'paused','loading'));await settle();
 reporter.report(snapshot(1,'paused'));await settle();
 reporter.report(snapshot(1));await settle();
 for(let frame=2;frame<=301;frame++){
  clock.advance((frame-1)*10);reporter.report(snapshot(frame));await settle();
 }
 const moving=posts.filter(row=>row.playback==='playing');
 assert.deepEqual(moving.map(row=>row.at),[0,1000,2000,3000]);
 assert.ok(moving.at(-1).presentedFrames>=300);
 assert.equal(states.length,3,'React status is not updated for each presentation');
 // A state transition bypasses the remaining cadence delay, flushing the
 // actual latest frame/time, not the last periodic snapshot.
 reporter.report(snapshot(301,'paused'));
 assert.equal(posts.at(-1).playback,'paused');assert.equal(posts.at(-1).presentedFrames,301);
 assert.equal(posts.at(-1).mediaTime,3.01);assert.equal(posts.at(-1).at,3000);
 await settle();reporter.report(snapshot(301,'paused','error','decode failed'));
 assert.equal(posts.at(-1).renderState,'error');assert.equal(posts.at(-1).at,3000);
 await settle();reporter.report(snapshot(301,'ended'));
 assert.equal(posts.at(-1).playback,'ended');assert.equal(posts.at(-1).presentedFrames,301);
 assert.equal(posts.at(-1).at,3000);
 assert.deepEqual(posts.map(row=>row.sequence),posts.map((_,index)=>index+1));
 reporter.dispose();
});

test('GIF frame scheduling is independent of report cadence',async()=>{
 const {clock,reporter,posts}=reporting();
 let frames=0;
 const decoder={tracks:{ready:Promise.resolve(),selectedTrack:{frameCount:3}},
  decode:async({frameIndex})=>({image:{duration:10_000,timestamp:frameIndex*10_000,close(){}}}),close(){}};
 const player=new GifPlayer({...clock,decoder,loopCount:0,draw:()=>frames++,
  report:(renderState,playback,detail)=>reporter.report({...snapshot(frames,playback,renderState,detail)})});
 await player.ready();await settle();player.play();await settle();
 for(let time=10;time<=1000;time+=10){clock.advance(time);await settle()}
 assert.equal(frames,101,'One draw per 10 ms plus the initial frame; reporting must not pace GIF');
 assert.equal(posts.filter(row=>row.playback==='playing').length,2);
 player.pause();assert.equal(posts.at(-1).presentedFrames,101);
 player.dispose();reporter.dispose();
});

test('slow success/failure retain only latest data and one synchronous in-flight request',async()=>{
 const releases=[];
 const {clock,reporter,posts,states}=reporting(()=>new Promise((resolve,reject)=>releases.push({resolve,reject})));
 reporter.report(snapshot(0,'paused','loading'));
 for(let frame=1;frame<=100;frame++){clock.advance(frame*10);reporter.report(snapshot(frame))}
 assert.equal(posts.length,1);assert.equal(clock.timers.size,0);
 reporter.report(snapshot(100,'paused'));
 assert.equal(states.at(-1).playback,'paused','Local transition does not wait on network');
 releases.shift().resolve();await settle();
 assert.equal(posts.length,2);assert.equal(posts.at(-1).presentedFrames,100);
 assert.equal(posts.at(-1).playback,'paused');
 reporter.report(snapshot(101,'paused','error','late failure'));
 releases.shift().reject(Error('transport failure'));await settle();
 assert.equal(posts.length,3);assert.equal(posts.at(-1).renderState,'error');
 assert.equal(posts.at(-1).presentedFrames,101);
 releases.shift().reject(Error('transport failure'));await settle();clock.advance(5000);await settle();
 assert.equal(posts.length,3,'A failed notification is never retried');
 reporter.dispose();
});

test('same-state slow completion waits for cadence and coalesces a newer snapshot',async()=>{
 const releases=[];
 const {clock,reporter,posts}=reporting(()=>new Promise(done=>releases.push(done)));
 reporter.report(snapshot(1));reporter.report(snapshot(2));clock.advance(900);
 releases.shift()();await settle();assert.equal(posts.length,1);
 reporter.report(snapshot(90));clock.advance(1000);
 assert.equal(posts.length,2);assert.equal(posts.at(-1).presentedFrames,90);
 for(let frame=101;frame<=300;frame++){clock.advance(frame*10);reporter.report(snapshot(frame))}
 assert.equal(posts.length,2);
 releases.shift()();await settle();
 assert.equal(posts.length,3);assert.equal(posts.at(-1).presentedFrames,300);
 releases.shift()();await settle();reporter.dispose();
});

test('dispose cancels delayed/pending reports without reading mutable or unmounted state',async()=>{
 for(const inFlight of [false,true]){
  let release;
  const {clock,reporter,posts,states}=reporting(()=>inFlight?new Promise(done=>{release=done}):undefined);
  reporter.report(snapshot(1));if(!inFlight)await settle();
  const mutable=snapshot(9);reporter.report(mutable);
  mutable.presentedFrames=999;mutable.mediaTime=999;
  const before=states.length;
  reporter.dispose();reporter.dispose();
  reporter.report(snapshot(10,'paused','error'));
  release?.();await settle();clock.advance(5000);await settle();
  assert.equal(posts.length,1);assert.equal(states.length,before);assert.equal(clock.timers.size,0);
 }
 const {clock,reporter,posts}=reporting();
 reporter.report(snapshot(1));await settle();
 const value=snapshot(2);reporter.report(value);value.presentedFrames=999;
 clock.advance(1000);assert.equal(posts.at(-1).presentedFrames,2,'Snapshot captured at presentation, not at delayed dispatch');
 reporter.dispose();
});

test('generation/sequence are isolated and a synchronous reentrant send cannot build a queue',async()=>{
 const clock=reportClock(),posts=[];let first;
 first=new MediaReporter({...clock,generation:1,send:value=>{
  posts.push(value);
  if(value.sequence===1)first.report(snapshot(2));
 }});
 first.report(snapshot(1));assert.equal(posts.length,1);await settle();
 clock.advance(1000);await settle();
 assert.deepEqual(posts.map(row=>[row.generation,row.sequence,row.presentedFrames]),[[1,1,1],[1,2,2]]);
 first.dispose();
 const second=new MediaReporter({...clock,generation:2,send:value=>posts.push(value)});
 first.report(snapshot(99,'paused','error'));second.report(snapshot(1,'paused'));
 assert.deepEqual([posts.at(-1).generation,posts.at(-1).sequence],[2,1]);
 second.dispose();
});

test('synchronous transport throw does not escape, retry, or stop future transitions',async()=>{
 const {clock,reporter,posts}=reporting(()=>{throw Error('offline')});
 assert.doesNotThrow(()=>reporter.report(snapshot(1)));
 clock.advance(2000);assert.equal(posts.length,1);
 reporter.report(snapshot(2,'paused'));assert.equal(posts.length,2);
 reporter.report(snapshot(2,'paused','error'));assert.equal(posts.length,3);
 reporter.dispose();
});

test('late completion of a disposed generation cannot flush into a new owner',async()=>{
 const clock=reportClock(),posts=[];let release;
 const old=new MediaReporter({...clock,generation:10,send:value=>{
  posts.push(value);return new Promise(done=>{release=done});
 }});
 old.report(snapshot(1));old.report(snapshot(100,'paused'));old.dispose();
 const current=new MediaReporter({...clock,generation:11,send:value=>posts.push(value)});
 current.report(snapshot(1,'paused','loading'));await settle();
 release();await settle();clock.advance(2000);await settle();
 assert.deepEqual(posts.map(row=>[row.generation,row.sequence,row.presentedFrames]),[[10,1,1],[11,1,1]]);
 current.report(snapshot(2,'paused'));assert.deepEqual([posts.at(-1).generation,posts.at(-1).sequence],[11,2]);
 current.dispose();
});