import test from 'node:test';
import assert from 'node:assert/strict';
import {resizeComposer,composerPrimaryAction} from '../src/composer.js';
test('composer starts compact, expands with content, and scrolls only at its bound',()=>{
 const element={style:{},scrollHeight:28};resizeComposer(element);assert.equal(element.style.height,'44px');assert.equal(element.style.overflowY,'hidden');
 element.scrollHeight=112;resizeComposer(element);assert.equal(element.style.height,'112px');
 element.scrollHeight=260;resizeComposer(element);assert.equal(element.style.height,'180px');assert.equal(element.style.overflowY,'auto');
 element.scrollHeight=24;resizeComposer(element);assert.equal(element.style.height,'44px');assert.equal(element.style.overflowY,'hidden');
});

test('content keeps Send during voice, work, and pending attachments',()=>{
 for(const input of [{draft:'A message'},{attachments:[{id:'file'}]}]){
  for(const voiceStatus of ['idle','connecting','connected','ending']){
   const action=composerPrimaryAction({...input,voiceStatus,working:true,responseActive:true});
   assert.equal(action.action,'conversation.send');assert.equal(action.label,'Send a correction');assert.equal(action.disabled,false);
  }
 }
 const upload=composerPrimaryAction({uploading:true,voiceStatus:'connected',responseActive:true});
 assert.equal(upload.action,'conversation.send');assert.equal(upload.disabled,true);
});

test('an empty composer ends voice before stopping background work, otherwise offers voice',()=>{
 for(const voiceStatus of ['connecting','connected','ending']){
  const action=composerPrimaryAction({draft:' \n',voiceStatus,responseActive:true,executionUnavailable:true,historyPending:true});
  assert.equal(action.action,'call.end');assert.equal(action.disabled,voiceStatus==='ending');assert.equal(action.icon,'cancel');
 }
 const stop=composerPrimaryAction({responseActive:true});
 assert.equal(stop.action,'conversation.stop');assert.equal(stop.disabled,false);assert.equal(stop.icon,'cancel');
 assert.equal(composerPrimaryAction({responseActive:true,stopping:true}).disabled,true);
 for(const voiceStatus of ['idle','ended','error'])assert.equal(composerPrimaryAction({voiceStatus}).action,'call.start');
});

test('pending sends never expose an accidental voice launch and readiness still gates sending',()=>{
 for(const flag of ['newChatPending','sending']){
  const pending=composerPrimaryAction({[flag]:true});assert.equal(pending.action,'conversation.send');assert.equal(pending.disabled,true);
 }
 for(const flag of ['busy','historyPending','executionUnavailable']){
  assert.equal(composerPrimaryAction({[flag]:true}).disabled,true);
  const send=composerPrimaryAction({draft:'Retain this draft',voiceStatus:'connected',[flag]:true});
  assert.equal(send.action,'conversation.send');assert.equal(send.disabled,true);
 }
 assert.equal(composerPrimaryAction({voiceStarting:true}).disabled,true);
});
