import {test} from 'node:test';
import assert from 'node:assert/strict';
import {voiceContext} from '../src/voice-context.js';
test('voice reads actual tools without a model call, schemas, paths or credentials',async()=>{
 const snapshot={session:'s',configurationRevision:3,capabilities:{actions:['feedback.list'],nativeTools:'not-inspected'}};
 const context={readSessionContext:async()=>snapshot,inspectSession:async()=>({configurationRevision:3}),nativeControlExisting:async(session,operation)=>{
  assert.equal(session,'s');assert.equal(operation,'catalog.inspect');return {tools:[{name:'bash',description:'Run commands',inputSchema:{private:'secret'}}],private:'secret'};
 }};
 const result=await voiceContext(context,'s');
 assert.equal(result.capabilities.nativeTools,'inspected');
 assert.deepEqual(result.capabilities.tools,[{name:'bash',description:'Run commands'}]);
 assert.ok(!JSON.stringify(result).includes('secret'));
 context.nativeControlExisting=async()=>{throw Error('cold runtime')};
 assert.deepEqual(await voiceContext(context,'s'),snapshot);
 context.nativeControlExisting=async()=>({tools:[]});context.inspectSession=async()=>({configurationRevision:4});
 assert.deepEqual(await voiceContext(context,'s'),snapshot);
});
