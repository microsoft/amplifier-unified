import test from 'node:test';
import assert from 'node:assert/strict';
import {peerRuns} from '../src/peer-groups.js';
const peer=(id,source='a',extra={})=>({kind:'message',id,message:{id,role:'user',inputOrigin:'peer',text:'same',attribution:{caption:'Sent by Amplifier from another chat',peer:{sourceId:source,available:true}},...extra}});
const ordinary=(kind,id)=>({kind,id,message:{id,role:'assistant'}});
test('peer runs retain exact originals and A-B-A order',()=>{
 const input=[peer('1'),peer('2','b'),peer('3')],out=peerRuns(input);
 assert.equal(out.length,1);assert.equal(out[0].kind,'peers');assert.deepEqual(out[0].rows,input);
 assert.equal(new Set(out[0].rows.map(x=>x.id)).size,3);
});
test('all non-peer rows, unknown provenance and system notifications split',()=>{
 for(const kind of ['message','work','artifacts','gap','approval','question']){
  const out=peerRuns([peer('1'),ordinary(kind,'boundary'),peer('2')]);assert.equal(out.length,3,kind);
 }
 const system=peer('system','a',{attribution:{caption:'Amplifier update'}});
 assert.equal(peerRuns([peer('1'),system,peer('2')]).length,3);
 assert.equal(peerRuns([peer('1'),peer('unknown','a',{attribution:null}),peer('2')]).length,3);
});
test('folded interim Work stays a boundary and absent coverage never joins',()=>{
 const interim={kind:'work',id:'interim',items:[{message:{id:'hidden',role:'assistant'}}]};
 assert.equal(peerRuns([peer('1'),interim,peer('2')]).length,3);
});
test('attention facts stay in originals; no prose inference',()=>{
 const input=[peer('1','a',{text:'approved success'}),peer('2','b',{delivery:{status:'failed'}})];
 assert.deepEqual(peerRuns(input)[0].rows,input);
});
