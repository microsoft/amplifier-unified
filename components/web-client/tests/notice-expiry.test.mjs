import test from 'node:test';
import assert from 'node:assert/strict';
import {createNoticeExpiry,NOTICE_EXPIRY_MS} from '../src/notice-expiry.js';
function clock(){
 let time=0,next=0;const jobs=new Map(),expired=[];
 const timer=createNoticeExpiry(()=>expired.push(time),{
  now:()=>time,schedule:(fn,delay)=>{const id=++next;jobs.set(id,{fn,at:time+delay});return id},cancel:id=>jobs.delete(id),
 });
 const advance=ms=>{const end=time+ms;while(true){const row=[...jobs].sort((a,b)=>a[1].at-b[1].at)[0];if(!row||row[1].at>end)break;time=row[1].at;jobs.delete(row[0]);row[1].fn()}time=end};
 return {timer,advance,expired,jobs};
}
test('routine expiry waits the full reading interval and fires once',()=>{
 const f=clock();assert.ok(NOTICE_EXPIRY_MS>=10_000);f.advance(NOTICE_EXPIRY_MS-1);assert.deepEqual(f.expired,[]);
 f.advance(1);assert.deepEqual(f.expired,[NOTICE_EXPIRY_MS]);f.advance(NOTICE_EXPIRY_MS);assert.equal(f.expired.length,1);
});
test('hover/focus/hidden pauses overlap and retain the remaining visible interval',()=>{
 const f=clock();f.advance(4_000);f.timer.pause('hover',true);f.advance(50_000);f.timer.pause('focus',true);
 f.timer.pause('hover',false);f.advance(50_000);f.timer.pause('hidden',true);f.timer.pause('focus',false);
 f.advance(50_000);assert.deepEqual(f.expired,[]);f.timer.pause('hidden',false);
 f.advance(NOTICE_EXPIRY_MS-4_001);assert.deepEqual(f.expired,[]);f.advance(1);assert.equal(f.expired.length,1);
});
test('disposed and superseded timers cannot dismiss anything',()=>{
 const f=clock();f.advance(500);f.timer.dispose();f.timer.pause('focus',false);f.advance(NOTICE_EXPIRY_MS*2);
 assert.deepEqual(f.expired,[]);assert.equal(f.jobs.size,0);
});