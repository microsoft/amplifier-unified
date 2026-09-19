import test from 'node:test';
import assert from 'node:assert/strict';
import {messageTextForCopy} from '../src/message-copy.js';

test('copy uses an available message without another request',async()=>{
 const state={sessions:[{id:'chat',messages:[{id:'answer',text:'**Markdown**'}]}]};
 assert.equal(await messageTextForCopy(state,{sessionId:'chat',messageId:'answer'},()=>assert.fail('unexpected read')),'**Markdown**');
});

test('copy reads an explicitly addressed off-page chat without changing selection',async()=>{
 const state={selectedSessionId:'visible',sessions:[{id:'visible',messages:[]},{id:'off/page',messages:[]}]};
 let url;
 const text=await messageTextForCopy(state,{sessionId:'off/page',messageId:'answer'},async value=>{
  url=value;return {selectedSessionId:'visible',sessions:[{id:'off/page',messages:[{id:'answer',text:'# Saved answer'}]}]};
 });
 assert.equal(url,'/api/state?sessionId=off%2Fpage');assert.equal(text,'# Saved answer');
 assert.equal(state.selectedSessionId,'visible');assert.deepEqual(state.sessions[1].messages,[]);
});

test('missing scoped message reports failure instead of copying unrelated content',async()=>{
 await assert.rejects(messageTextForCopy({sessions:[]},{sessionId:'chat',messageId:'missing'},async()=>({sessions:[{id:'chat',messages:[{id:'other',text:'Not the requested entry'}]}]})),/Message no longer available/);
});
