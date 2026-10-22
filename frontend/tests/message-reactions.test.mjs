import test from 'node:test';
import assert from 'node:assert/strict';
import {createReactionController} from '../src/message-reactions.js';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const receipt=(reactions,revision=1)=>({accepted:true,result:{sessionId:'s',messageId:'m',reactionActor:'user',reactions,annotationRevision:revision}});
function harness(options={}){const writes=[],reads=[],saved=[];const controller=createReactionController({sessionId:'s',messageId:'m',clientId:'client',send:row=>new Promise((resolve,reject)=>writes.push({row:{...row},resolve,reject})),read:async row=>{reads.push(row);return {sessionId:'s',messageId:'m',status:'unknown'}},save:rows=>saved.push(structuredClone(rows)),...options});controller.observe({reactions:[],annotationRevision:0});return {controller,writes,reads,saved}}
test('intent is immediate and rapid toggles serialize only the same emoji',async()=>{
 const {controller:c,writes}=harness();c.set('heart',true);assert.deepEqual(c.view().reactions,['heart']);c.set('heart',false);assert.deepEqual(c.view().reactions,[]);c.set('check',true);assert.equal(writes.length,2);
 writes[0].resolve(receipt(['heart']));await tick();assert.equal(writes.length,3);assert.equal(writes[2].row.present,false);assert.notEqual(writes[2].row.id,writes[0].row.id);assert.deepEqual(c.view().reactions,['check']);
 writes[2].resolve(receipt([],3));writes[1].resolve(receipt(['heart','check'],2));await tick();assert.deepEqual(c.view().reactions,[],'Late full-state receipt cannot overwrite a newer revision');
});
test('older rejection rolls back only its change, preserving unrelated/newer intent',async()=>{
 const {controller:c,writes}=harness();c.observe({reactions:['kept'],annotationRevision:1});c.set('heart',true);c.set('check',true);writes[0].reject(Object.assign(Error('Message unavailable'),{status:404}));await tick();assert.deepEqual(c.view().reactions,['kept','check']);assert.match(c.view().error,/unavailable/);assert.equal(c.view().unknown,false);
 writes[1].resolve(receipt(['kept','check'],2));await tick();assert.deepEqual(c.view().reactions,['kept','check']);
});
test('newer same-emoji intent survives old acknowledgement and external snapshot',async()=>{
 const {controller:c,writes}=harness();c.set('heart',true);c.set('heart',false);c.observe({reactions:['heart','peer'],annotationRevision:4});writes[0].resolve(receipt(['heart'],1));await tick();assert.deepEqual(c.view().reactions,['peer']);writes[1].resolve(receipt(['peer'],5));await tick();assert.deepEqual(c.view().reactions,['peer']);
});
test('unknown transport outcome is never replayed; exact receipt read settles current state',async()=>{
 let result={sessionId:'s',messageId:'m',status:'unknown'};const {controller:c,writes}=harness({read:async()=>result});c.set('heart',true);writes[0].reject(Object.assign(Error('Offline'),{unconfirmed:true}));await tick();assert.equal(c.view().unknown,true);await c.reconcile();assert.equal(writes.length,1);assert.equal(c.view().unknown,true);result={sessionId:'s',messageId:'m',status:'accepted',receipt:receipt(['heart']),revision:2,current:{reactions:[]}};await c.reconcile();assert.equal(c.view().unknown,false);assert.deepEqual(c.view().reactions,[]);assert.equal(writes.length,1);
});
test('reload retains uncertainty and reads without writing a saved intent',async()=>{
 const first=harness();first.controller.set('heart',true);first.controller.set('heart',false);
 const recovered=harness({restored:first.saved.at(-1),read:async()=>({sessionId:'s',messageId:'m',status:'accepted',receipt:receipt(['heart']),revision:1,current:{reactions:['heart']}})});
 assert.equal(recovered.controller.view().unknown,true);await recovered.controller.reconcile();assert.equal(recovered.writes.length,0);assert.match(recovered.controller.view().error,/not sent/);assert.deepEqual(recovered.controller.view().reactions,['heart']);
});
test('different messages have independent flights; forged actor receipt stays unknown',async()=>{
 const a=harness(),b=harness();a.controller.set('heart',true);b.controller.set('check',true);assert.equal(a.writes.length,1);assert.equal(b.writes.length,1);a.writes[0].resolve({accepted:true,result:{...receipt(['heart']).result,reactionActor:'assistant'}});await tick();assert.equal(a.controller.view().unknown,true);assert.deepEqual(b.controller.view().reactions,['check']);
});
