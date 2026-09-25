import test from 'node:test';
import assert from 'node:assert/strict';
import {VoiceClient} from '../src/voice.js';

for(const boundary of ['configuration','microphone'])test(`navigation cancels setup awaiting ${boundary} without restarting audio`,async t=>{
  const previous=Object.fromEntries(['window','document','navigator'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
  const window=new EventTarget(),document=new EventTarget();document.visibilityState='visible';window.RTCPeerConnection=class {};
  let ready,resolve;const pending=new Promise(r=>resolve=r),started=new Promise(r=>ready=r);
  const track={stopped:false,stop(){this.stopped=true;}},requests=[];
  const audioSession=new EventTarget();audioSession.type='auto';audioSession.state='active';
  const navigator={audioSession,mediaDevices:{getUserMedia:async()=>{ready();return pending;}}};
  for(const [key,value] of Object.entries({window,document,navigator}))Object.defineProperty(globalThis,key,{value,configurable:true});
  t.after(()=>{for(const [key,value] of Object.entries(previous)){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}});
  const client=new VoiceClient({request:async path=>{requests.push(path);if(boundary==='configuration'){ready();return pending;}return {available:true};}});
  const call=client.start();await started;window.dispatchEvent(new Event('pagehide'));
  resolve(boundary==='configuration'?{available:true}:{getTracks:()=>[track]});await call;
  assert.equal(client.state.status,'ended');assert.equal(client.state.id,null);assert.equal(audioSession.type,'auto');
  assert.deepEqual(requests,['/api/voice/config']);assert.equal(track.stopped,boundary==='microphone');client.dispose();
});
