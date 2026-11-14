import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ModelContent}=await server.ssrLoadModule('/src/execution-content.jsx');
const {WorkNavigationContext}=await server.ssrLoadModule('/src/work-navigation.js');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const locationBefore=globalThis.location;
globalThis.location={origin:'http://fixture.invalid'};
const marker={v:1,sha256:'a'.repeat(64),bytes:123,expired_at:'2026-11-13T12:00:00+02:00',reason:'retention'};
const base={id:'call',status:'completed',provider:'test',model:'model'};
const reference={sessionId:'session',id:'event',digest:'a'.repeat(64)};
const recording={sha256:'a'.repeat(64),bytes:123,truncated:false};
const render=(node,dispatch=()=>{})=>React.createElement(ModelContent,{node,error:{value:null},now:0,act:dispatch});
const text=root=>JSON.stringify(root.toJSON());
const buttons=(root,label)=>root.root.findAllByType('button').filter(button=>button.children.includes(label));
test.after(async()=>{await server.close();if(locationBefore===undefined)delete globalThis.location;else globalThis.location=locationBefore});

for(const field of ['request','response'])test(`${field} expiry is explicit UTC text with no download, disclosure or settings controls`,async()=>{
 let root;const calls=[],fetchBefore=globalThis.fetch;
 globalThis.fetch=(...args)=>{calls.push(args);throw Error('Expired payload must not be read')};
 try{
  await act(async()=>{root=create(render({...base,requestRecordingExpired:marker,responseRecordingExpired:marker,[field+'Detail']:reference,[field+'Recording']:recording,[field]:'STALE DIAGNOSTIC'}))});
  const expired=root.root.findByProps({'aria-label':`Expired ${field} recording`});
  assert.match(expired.findByType('p').children.filter(value=>typeof value==='string').join(''),/intentionally expired/);
  assert.equal(expired.findByType('time').props.dateTime,'2026-11-13T10:00:00.000Z');
  assert.equal(expired.findAllByType('a').length,0);
  assert.equal(expired.findAllByType('button').length,0);
  assert.equal(root.root.findAllByProps({className:'a-link a-execution-request-toggle'}).length,0);
  assert.doesNotMatch(text(root),/STALE DIAGNOSTIC|Open recording settings|Waiting for|Download (?:full|partial)? ?recorded/);
  assert.equal(buttons(root,'Download selected metrics').length,1,'Expiry does not remove independent usage exports');
  assert.equal(calls.length,0);
 }finally{await act(async()=>root?.unmount());globalThis.fetch=fetchBefore}
});

test('expiry does not suppress the other direction or alter genuine missing-recording guidance',async()=>{
 let root;
 try{
  await act(async()=>{root=create(render({...base,responseRecordingExpired:marker}))});
  assert.equal(root.root.findAllByProps({'aria-label':'Expired response recording'}).length,1);
  assert.equal(buttons(root,'Open recording settings').length,1,'Only the genuinely missing request retains its normal guidance');
  await act(async()=>root.update(render({...base,requestRecordingExpired:marker,responseDetail:reference,responseRecording:recording})));
  assert.equal(root.root.findAllByProps({'aria-label':'Expired request recording'}).length,1);
  const response=root.root.findByProps({'aria-label':'Recorded response'});
  assert.match(text(root),/Extracted images remain references, not embedded files/);
  assert.match(text(root),/not a self-contained media archive/);
  assert.equal(response.findAllByType('a').length,1);
  assert.equal(buttons(root,'Open recording settings').length,0);
  assert.ok(response.findAllByType('button').some(button=>button.props['aria-expanded']===false));
 }finally{await act(async()=>root?.unmount())}
});

test('unknown or malformed expiry metadata is not presented as intentional expiry',async()=>{
 let root;
 try{
  for(const value of [undefined,{...marker,v:2},{...marker,reason:'unknown'},{...marker,bytes:'123'},{...marker,extra:true},JSON.stringify(marker)]){
   await act(async()=>{root=create(render({...base,requestRecordingExpired:value}))});
   assert.doesNotMatch(text(root),/intentionally expired/);
   assert.equal(buttons(root,'Open recording settings').length,1);
   await act(async()=>root.unmount());root=null;
  }
 }finally{await act(async()=>root?.unmount())}
});

test('expiry arriving while detail is open aborts the read and never shows its late payload',async()=>{
 let root,resolve;const calls=[],fetchBefore=globalThis.fetch;
 globalThis.fetch=(path,options)=>{calls.push({path,options});return new Promise(done=>{resolve=done})};
 try{
  await act(async()=>{root=create(render({...base,requestDetail:reference,requestRecording:recording,responseRecordingExpired:marker}))});
  assert.equal(calls.length,0,'Ordinary details still require explicit disclosure');
  await act(async()=>root.root.findAllByType('button').find(button=>button.props['aria-expanded']===false).props.onClick());
  assert.equal(calls.length,1);
  await act(async()=>root.update(render({...base,requestDetail:reference,requestRecording:recording,requestRecordingExpired:marker,responseRecordingExpired:marker})));
  assert.equal(calls[0].options.signal.aborted,true);
  await act(async()=>resolve(new Response(JSON.stringify({value:'STALE EXPIRED PAYLOAD',nextOffset:null}),{headers:{'Content-Type':'application/json'}})));
  assert.doesNotMatch(text(root),/STALE EXPIRED PAYLOAD|Download (?:full|partial)? ?recorded|Open recording settings|Loading saved/);
  assert.equal(calls.length,1);
 }finally{await act(async()=>root?.unmount());globalThis.fetch=fetchBefore}
});

test('both expired directions do not offer a related-recordings route implying recoverable payloads',async()=>{
 let root;const calls=[];
 try{
  const node={...base,requestRecordingExpired:marker,responseRecordingExpired:marker,recordingsGroup:{id:'group'}};
  await act(async()=>{root=create(React.createElement(WorkNavigationContext.Provider,{value:{openRecordings:()=>calls.push('open')}},render(node)))});
  assert.equal(buttons(root,'Show saved recordings').length,0);
  assert.equal(calls.length,0);
 }finally{await act(async()=>root?.unmount())}
});