import test from 'node:test';
import assert from 'node:assert/strict';
import {inputStatus,statusLabel,workingDuration} from '../src/input-status.js';
const message={role:'user',inputId:'one',delivery:{status:'accepted'}};
test('host receipt never claims read, working, or completion',()=>{
 assert.deepEqual(inputStatus(message),{inputId:'one',phase:'received',basis:'delivery'});
 assert.equal(statusLabel(inputStatus(message)),'Received');
 assert.equal(inputStatus({...message,delivery:undefined}).phase,'unknown');
 assert.equal(inputStatus({...message,inputStatus:{version:1,inputId:'one',phase:'unknown',basis:'no_evidence'}}).phase,'received');
 assert.equal(inputStatus({...message,nativeInputId:'other'}),null);
 assert.equal(inputStatus({...message,role:'assistant'}),null);
 assert.equal(inputStatus({...message,displayOnly:true}),null);
 assert.equal(inputStatus({role:'user',text:'legacy'}),null);
});
test('only versioned exact input evidence controls status; unrelated or malformed data cannot',()=>{
 for(const status of [{version:1,inputId:'other',phase:'done',basis:'generation'}, {version:2,inputId:'one',phase:'done',basis:'generation'}, {version:1,inputId:'one',phase:'read',basis:'guess'}, {version:1,inputId:'one',phase:'working'}]){
  assert.equal(inputStatus({...message,inputStatus:status}).phase,'received');
 }
 for(const phase of ['working','waiting','queued','included','done','failed','unknown']){
  const inputStatusRecord={version:1,inputId:'one',phase,basis:'observed'};
  assert.equal(inputStatus({...message,inputStatus:inputStatusRecord}).phase,phase);
 }
});
test('saved native rows expose only exact nonambiguous versioned host evidence',()=>{
 const saved={role:'user',displayOnly:true,nativeInputId:'native',delivery:{status:'accepted'}};
 for(const phase of ['working','waiting','queued','included','done','failed','unknown']){
  const evidence={version:1,inputId:'native',phase,basis:'observed',startedAt:100,updatedAt:142};
  assert.deepEqual(inputStatus({...saved,inputStatus:evidence}),evidence);
  assert.deepEqual(inputStatus({...saved,inputId:'native',inputStatus:evidence}),evidence);
 }
 const inputStatusRecord={version:1,inputId:'native',phase:'working',basis:'execution_observed'};
 for(const [name,patch] of [
  ['missing evidence',{}],
  ['unbound evidence',{nativeInputId:undefined,inputStatus:inputStatusRecord}],
  ['canonical id alone',{nativeInputId:undefined,inputId:'native',inputStatus:inputStatusRecord}],
  ['blank native id',{nativeInputId:'',inputStatus:inputStatusRecord}],
  ['whitespace native id',{nativeInputId:' ',inputStatus:{...inputStatusRecord,inputId:' '}}],
  ['non-string native id',{nativeInputId:42,inputStatus:{...inputStatusRecord,inputId:42}}],
  ['ambiguous binding',{nativeInputAmbiguous:true,inputStatus:inputStatusRecord}],
  ['conflicting binding',{inputId:'other',inputStatus:inputStatusRecord}],
  ['observation',{observation:{source:'amplifier-delegate'},inputStatus:inputStatusRecord}],
  ['peer via',{via:'peer',inputStatus:inputStatusRecord}],
  ['peer origin',{inputOrigin:'peer',inputStatus:inputStatusRecord}],
  ['assistant',{role:'assistant',inputStatus:inputStatusRecord}],
  ...[
   {inputId:'other'}, {version:undefined}, {version:2}, {phase:'read'}, {basis:undefined}, {basis:42}, {basis:''}, {basis:' '}, {basis:'no_evidence'},
  ].map(correction=>[JSON.stringify(correction),{inputStatus:{...inputStatusRecord,...correction}}]),
 ])assert.equal(inputStatus({...saved,...patch}),null,name);
 // Without a positive host record neither a receipt nor its absence fabricates status.
 assert.equal(inputStatus({...saved,delivery:undefined}),null);
});
test('delivery recovery and steering retain their existing UI independently',()=>{
 for(const status of ['sending','failed','held','queued_for_update','unknown'])assert.equal(inputStatus({...message,delivery:{status}}),null);
 assert.equal(inputStatus({...message,delivery:undefined,steering:{disposition:'queued'}}),null);
 assert.equal(inputStatus({role:'user',nativeInputId:'native',inputStatus:{version:1,inputId:'native',phase:'working',basis:'execution'}}).phase,'working');
});
test('timer starts only at observed execution and stops on terminal or disconnected evidence',()=>{
 const status={phase:'working',startedAt:100,updatedAt:142};
 assert.equal(workingDuration(status,165),'1:05');
 assert.equal(workingDuration(status,165,false),'0:42');
 assert.equal(workingDuration({...status,phase:'done',endedAt:150},999),'0:50');
 assert.equal(workingDuration({...status,startedAt:null}),null);
 assert.equal(workingDuration({...status,endedAt:90}),null);
 assert.match(statusLabel(status,false),/last observed/);
 assert.equal(statusLabel({phase:'done'}),'Turn finished');
});