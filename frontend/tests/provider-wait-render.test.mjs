// Rendered component semantics only: not installed browser/Worker acceptance.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,rm} from 'node:fs/promises';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {join} from 'node:path';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {build} from 'vite';

test('model details separate observations, limits, capture, uncertainty and local settlement',async()=>{
 const root=fileURLToPath(new URL('../../',import.meta.url));
 await mkdir(join(root,'frontend/.ci'),{recursive:true});
 const output=await mkdtemp(join(root,'frontend/.ci/model-render-'));
 try{
  await build({configFile:false,root:join(root,'frontend'),logLevel:'silent',
   build:{ssr:join(root,'frontend/src/execution-content.jsx'),outDir:output,emptyOutDir:true,
    rollupOptions:{output:{entryFileNames:'model.mjs'}}}});
  const {ModelContent}=await import(pathToFileURL(join(output,'model.mjs')));
  const field={value:null,incomplete:false,loading:false,error:null};
  const render=node=>renderToStaticMarkup(React.createElement(ModelContent,{
   node,request:field,error:field,requestOpen:false,requestInline:false,toggleRequest:()=>{},now:200}));
  const base={kind:'llm',phase:'running',provider:'fixture',model:'fixture',startedAt:100};
  const unavailable=render(base);
  assert.match(unavailable,/Awaiting provider result/);
  assert.match(unavailable,/Unavailable for this call/);
  assert.match(unavailable,/Raw request capture is unavailable/);
  assert.match(unavailable,/not required to observe response activity/);
  assert.doesNotMatch(unavailable,/Waiting for the recorded request/);
  assert.doesNotMatch(unavailable,/Last response activity observed/);
  const limits={mode:'none',elapsed_seconds:null,connect_seconds:5,pool_seconds:5,read_seconds:null,write_seconds:null};
  const providerWait={version:1,attempt:2,limits,observedAt:150};
  const observed=render({...base,providerWait});
  assert.match(observed,/Observed attempt<\/dt><dd>2/);
  assert.match(observed,/No response activity observed/);
  assert.match(observed,/No elapsed limit/);
  assert.match(observed,/Read limit<\/dt><dd>None/);
  assert.match(observed,/Elapsed locally<\/dt><dd>100.0 seconds/);
  const activity=render({...base,providerWait:{...providerWait,lastResponseActivityAt:160}});
  assert.doesNotMatch(activity,/No response activity observed/);
  assert.match(render({...base,providerWait:{...providerWait,limits:{...limits,mode:'elapsed',elapsed_seconds:45}}}),/Elapsed per attempt \(not whole turn\)/);
  assert.match(render({...base,providerWait:{...providerWait,limits:{...limits,mode:'phase',read_seconds:30}}}),/SDK phase limits \(no elapsed limit\)/);
  const failure=render({...base,phase:'error',endedAt:180,providerWait,
   failure:{summary:'Provider wait ended without a confirmed result.',guidance:'The request may have been accepted; no automatic replacement request was sent.'}});
  assert.match(failure,/role="alert"/);
  assert.match(failure,/no automatic replacement request was sent/);
  assert.doesNotMatch(failure,/Awaiting provider result/);
  const cancelled=render({...base,phase:'cancelled',endedAt:180,providerWait});
  assert.match(cancelled,/does not confirm remote provider cancellation/);
  assert.match(cancelled,/Elapsed locally<\/dt><dd>80.0 seconds/);
  assert.doesNotMatch(cancelled,/Awaiting provider result/);
 }finally{await rm(output,{recursive:true,force:true})}
});