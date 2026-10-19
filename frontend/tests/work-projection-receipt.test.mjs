import test from 'node:test';
import assert from 'node:assert/strict';
import {workProjectionReceipt} from '../src/work-projection-receipt.js';
const receipt={hostInstanceId:'host',sessionId:'chat',generation:3,sourceCount:4,nodeCount:18000,turnCount:43};
const session=patch=>({id:'chat',workProjection:{status:'ready',current:true,receipt,...patch}});

test('exports only bounded owner and accepted generation facts without traversing Work',()=>{
 const row=session();Object.defineProperty(row,'execution',{get(){throw Error('must not traverse Work')}});
 row.workProjection.receipt={...receipt,paths:['private'],sourceText:'private'};
 const value=workProjectionReceipt(row,'host'),parsed=JSON.parse(value);
 assert.equal(parsed.current,true);assert.deepEqual(parsed.receipt,{generation:3,sourceCount:4,nodeCount:18000,turnCount:43});
 assert.equal(value.includes('private'),false);assert.ok(new TextEncoder().encode(value).length<=1024);
});
test('retained receipt is noncurrent while pending, retrying or unavailable',()=>{
 for(const status of ['loading','retrying','unavailable']){
  const parsed=JSON.parse(workProjectionReceipt(session({status}),'host'));
  assert.equal(parsed.current,false);assert.equal(parsed.receipt.generation,3);
 }
 assert.equal(JSON.parse(workProjectionReceipt(session({current:false}),'host')).current,false);
});
test('missing or malformed and mismatched receipts cannot qualify current Work',()=>{
 for(const bad of [undefined,null,{...receipt,hostInstanceId:'old'},{...receipt,sessionId:'other'},
  {...receipt,generation:0},{...receipt,nodeCount:-1},{...receipt,turnCount:1.5},{...receipt,sourceCount:NaN}]){
  const parsed=JSON.parse(workProjectionReceipt(session({receipt:bad}),'host'));
  assert.equal(parsed.current,false);assert.equal(parsed.receipt,null);
 }
 assert.equal(workProjectionReceipt({id:'chat'},'host'),undefined);
 assert.equal(workProjectionReceipt(session(),null),undefined);
 assert.equal(workProjectionReceipt(session({status:'invented'}),'host'),undefined);
});
test('bound includes UTF-8 size and rejects excessive identity instead of truncating authority',()=>{
 const long='界'.repeat(200),row={id:long,workProjection:{status:'ready',current:true,receipt:{...receipt,hostInstanceId:long,sessionId:long}}};
 assert.equal(workProjectionReceipt(row,long),undefined);
 assert.equal(workProjectionReceipt({...session(),id:'x'.repeat(201)},'host'),undefined);
});


test('bounded Work receipts qualify only the selected window, never complete history',()=>{
 const supplied={hostInstanceId:'host',sessionId:'chat',generation:5,scope:'recent-events',windowKey:'owned',historyComplete:false,sourceCount:1,itemCount:92,scannedBytes:4194304,scannedEvents:123};
 const value=JSON.parse(workProjectionReceipt(session({receipt:supplied}),'host'));
 assert.equal(value.version,2);assert.equal(value.current,true);
 assert.equal(value.receipt.historyComplete,false);assert.equal(value.receipt.itemCount,92);
 assert.equal('nodeCount' in value.receipt,false);assert.equal('turnCount' in value.receipt,false);
 for(const patch of [{historyComplete:true},{windowKey:''},{scannedBytes:-1},{scannedEvents:undefined}]){
  const invalid=JSON.parse(workProjectionReceipt(session({receipt:{...supplied,...patch}}),'host'));
  assert.equal(invalid.current,false);assert.equal(invalid.receipt,null);
 }
});

// Shape emitted by WorkWindows.read_selected; rootProof.generation is a
// separate page identity, not the positive integer display generation.
const rootReceipt=()=>({hostInstanceId:'host',sessionId:'chat',generation:7,
 scope:'root-proof',windowKey:'owned',historyComplete:false,
 proofId:'0123456789abcdef0123456789abcdef',itemCount:100});

test('root-proof receipts describe current bounded raw display, not complete association or history',()=>{
 for(const status of ['complete','incomplete'])for(const itemCount of [0,1,100]){
  const supplied={...rootReceipt(),itemCount};
  const rootProof={id:supplied.proofId,status,steps:2,boundCount:0,generation:'a'.repeat(64)};
  const value=JSON.parse(workProjectionReceipt(session({receipt:supplied,rootProof}),'host'));
  assert.deepEqual(value,{version:2,hostInstanceId:'host',sessionId:'chat',status:'ready',current:true,
   receipt:{generation:7,scope:'root-proof',windowKey:'owned',historyComplete:false,
    proofId:supplied.proofId,itemCount}});
 }
});

test('root-proof receipts retain exact host, session, display generation, window and proof identities',()=>{
 for(const patch of [
  {hostInstanceId:'other-host',sessionId:'other-chat',generation:1,windowKey:'other-window',proofId:'f'.repeat(32)},
  {hostInstanceId:'host-界',sessionId:'chat-界',generation:Number.MAX_SAFE_INTEGER,windowKey:'window-界',proofId:'0'.repeat(32)},
 ]){
  const supplied={...rootReceipt(),...patch},row={...session({receipt:supplied}),id:supplied.sessionId};
  const value=JSON.parse(workProjectionReceipt(row,supplied.hostInstanceId));
  assert.equal(value.current,true);assert.equal(value.version,2);
  assert.equal(value.hostInstanceId,supplied.hostInstanceId);assert.equal(value.sessionId,supplied.sessionId);
  for(const key of ['generation','windowKey','proofId'])assert.equal(value.receipt[key],supplied[key],key);
 }
});

test('root-proof receipts reject missing, malformed or mismatched producer fields',()=>{
 const reject=(supplied,label)=>{
  const value=JSON.parse(workProjectionReceipt(session({receipt:supplied}),'host'));
  assert.equal(value.current,false,label);assert.equal(value.receipt,null,label);
 };
 for(const key of Object.keys(rootReceipt())){
  const supplied=rootReceipt();delete supplied[key];reject(supplied,`missing ${key}`);
 }
 const malformed={
  hostInstanceId:['other-host','',null,0],
  sessionId:['other-chat','',null,0],
  generation:[0,-1,1.5,Number.MAX_SAFE_INTEGER+1,NaN,Infinity,'7',null,true],
  windowKey:['','x'.repeat(201),null,0,{},[]],
  historyComplete:[true,null,0,'false'],
  proofId:['','a'.repeat(31),'a'.repeat(33),'A'.repeat(32),'g'.repeat(32),'a'.repeat(31)+'\n','a'.repeat(32)+'\n',null,0,{},[]],
  itemCount:[-1,101,1.5,Number.MAX_SAFE_INTEGER+1,NaN,Infinity,'100',null,true],
 };
 for(const [key,values] of Object.entries(malformed)){
  for(const value of values)reject({...rootReceipt(),[key]:value},`malformed ${key}: ${String(value)}`);
 }
});

test('unknown or malformed scopes cannot become complete-tree receipts through forged counts',()=>{
 for(const scope of ['invented','full-tree','ROOT-PROOF','',null,0,false,[],{}]){
  const supplied={...receipt,...rootReceipt(),scope};
  const value=JSON.parse(workProjectionReceipt(session({receipt:supplied}),'host'));
  assert.equal(value.current,false,`scope ${String(scope)}`);assert.equal(value.receipt,null);
 }
});

test('invalid root-proof receipts cannot fall back to supplied complete-tree counts',()=>{
 for(const patch of [{proofId:'invalid'},{itemCount:101},{windowKey:''},{historyComplete:true}]){
  const supplied={...receipt,...rootReceipt(),...patch};
  const value=JSON.parse(workProjectionReceipt(session({receipt:supplied}),'host'));
  assert.equal(value.version,2);assert.equal(value.current,false);assert.equal(value.receipt,null);
 }
});

test('root-proof receipts retain display identity without overriding host currentness or readiness',()=>{
 for(const patch of [
  {status:'loading'},{status:'retrying'},{status:'unavailable'},
  {current:false},{current:undefined},{current:'true'},
 ]){
  const value=JSON.parse(workProjectionReceipt(session({receipt:rootReceipt(),...patch}),'host'));
  assert.equal(value.version,2);assert.equal(value.current,false);
  assert.equal(value.receipt.proofId,rootReceipt().proofId);assert.equal(value.receipt.historyComplete,false);
 }
});

test('root-proof receipt byte cap accepts 1024 UTF-8 bytes and refuses 1025 without truncation',()=>{
 const owner='界'.repeat(100),supplied={...rootReceipt(),hostInstanceId:owner,sessionId:owner,windowKey:''};
 const expected={version:2,hostInstanceId:owner,sessionId:owner,status:'ready',current:true,
  receipt:{generation:7,scope:'root-proof',windowKey:'',historyComplete:false,proofId:supplied.proofId,itemCount:100}};
 const remaining=1024-new TextEncoder().encode(JSON.stringify(expected)).byteLength;
 assert.ok(remaining>0&&remaining<=200);
 supplied.windowKey='x'.repeat(remaining);expected.receipt.windowKey=supplied.windowKey;
 const row={...session({receipt:supplied}),id:owner},value=workProjectionReceipt(row,owner);
 assert.equal(value,JSON.stringify(expected));assert.equal(new TextEncoder().encode(value).byteLength,1024);
 supplied.windowKey='é'+supplied.windowKey.slice(1);
 assert.equal(workProjectionReceipt(row,owner),undefined);
});

test('root-proof receipts neither invent counts nor traverse or leak unrelated Work and proof details',()=>{
 const supplied={...rootReceipt(),sourceText:'private-source-marker'};
 for(const key of ['sourceCount','nodeCount','turnCount','scannedBytes','scannedEvents','paths','toJSON']){
  Object.defineProperty(supplied,key,{enumerable:true,get(){throw Error(`must not read ${key}`)}});
 }
 const row=session({receipt:supplied,rootProof:{id:supplied.proofId,status:'incomplete',
  steps:2,boundCount:0,generation:'a'.repeat(64),reason:'private-proof-marker'}});
 for(const key of ['execution','messages','workers']){
  Object.defineProperty(row,key,{get(){throw Error(`must not traverse ${key}`)}});
 }
 const value=workProjectionReceipt(row,'host'),parsed=JSON.parse(value);
 assert.equal(parsed.current,true);assert.equal(value.includes('private-'),false);
 assert.deepEqual(Object.keys(parsed.receipt).sort(),['generation','historyComplete','itemCount','proofId','scope','windowKey']);
 assert.equal('rootProof' in parsed,false);assert.ok(new TextEncoder().encode(value).byteLength<=1024);
});
