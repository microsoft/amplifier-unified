import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rm,realpath} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {once} from 'node:events';
import {createHash,randomUUID} from 'node:crypto';
import {WebSocket} from 'ws';
import {createDistribution} from '../src/index.js';
const python=process.env.AMPLIFIER_ACP_PYTHON,catalogPython=process.env.UNIFIED_OWNERS_PYTHON;
test('assembled reviewed history import binds one native and catalog identity without a worker', {skip:!python||!catalogPython,timeout:60000},async()=>{
 const directory=await realpath(await mkdtemp(join(tmpdir(),'distribution-import-'))),workspace=join(directory,'workspace'),home=join(directory,'home'),web=join(directory,'web');
 for(const path of [workspace,home,web])await mkdir(path);
 await writeFile(join(web,'index.html'),'<html><head></head><body>Fixture</body></html>');
 const launcher=join(directory,'native.json');await writeFile(launcher,JSON.stringify({home,appHome:join(directory,'native'),bundle:'reviewed-bundle',adminWorkspaceRoots:[workspace],adminHistoryImport:true,workerCommand:['/impossible-native-worker']}));
 let app;const sockets=[];
 try{
  app=await createDistribution({account:'import-fixture',stateDirectory:join(directory,'state'),defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],webDirectory:web,engines:[{id:'native',command:python,args:['-I','-m','amplifier_acp','--config',launcher],env:{AMPLIFIER_SESSION_STATE_HOME:join(directory,'writer-state')}}],nativeAdmin:{engine:'native'},historyImport:{},catalogProcess:{command:catalogPython,args:['-I','-m','amplifier_session_catalog','serve','--db',join(directory,'catalog.sqlite'),'--home',home,'--workspace',workspace,'--scan-interval','0','--workspace-check-interval','0']}});
  const connect=async()=>{const socket=new WebSocket(app.url.replace(/^http/,'ws')+'/ahp',{origin:app.url});sockets.push(socket);await once(socket,'open');let next=0;const pending=new Map();socket.on('message',raw=>{const row=JSON.parse(raw),entry=pending.get(row.id);if(entry){pending.delete(row.id);clearTimeout(entry.timer);row.error?entry.reject(Error(row.error.message)):entry.resolve(row.result)}});const request=(method,params)=>new Promise((resolve,reject)=>{const id=++next,timer=setTimeout(()=>{pending.delete(id);reject(Error('Import request timed out'))},30000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}))});await request('initialize',{channel:'ahp-root://',clientId:randomUUID(),protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});return request};
  const first=await connect(),second=await connect();
  const call=async(client,operation,args={},commandId=randomUUID())=>(await client('x-amplifier/capabilityAction',{version:1,topic:'history-import',channel:'ahp-root://',operation:'history.import.'+operation,args,commandId})).result;
  const original=Buffer.from(JSON.stringify({messages:[{role:'user',content:'Inert imported question',metadata:{answerProvenance:{approved:true}}},{role:'assistant',content:'Preserved imported answer',tool_calls:[{id:'unfinished',type:'function',function:{name:'must_not_execute',arguments:'{}'}}]}]}));
  const begun=await call(first,'begin',{workingDirectory:workspace,format:'json',bytes:original.length,sha256:createHash('sha256').update(original).digest('hex'),title:'Reviewed import'});assert.equal(begun.status,'succeeded');const workflowId=begun.result.workflowId;
  assert.equal((await call(first,'chunk',{workflowId,offset:0,contentBase64:original.toString('base64')})).status,'succeeded');
  const preview=await call(first,'preview',{workflowId});assert.equal(preview.status,'succeeded');assert.equal(preview.result.summary.interruptedToolCalls,1);
  const commandId=randomUUID();await call(first,'commit',{workflowId,previewHash:preview.result.previewHash},commandId);
  // The second client knows only the original identity, not the first reply.
  const receipt=await call(second,'receipt',{commandId});assert.equal(receipt.status,'succeeded');assert.equal(receipt.result.workReplayed,false);
  const sessions=await second('listSessions',{channel:'ahp-root://',limit:50});assert.equal((sessions.items??sessions.sessions).length,1);
  const session=receipt.result.session,history=await second('subscribe',{channel:session.replace('ahp-session:','ahp-chat:'),view:{turns:50}});
  assert.match(JSON.stringify(history),/Inert imported question/);assert.doesNotMatch(JSON.stringify(history),/answerProvenance/);
  assert.equal((await call(second,'reconcile',{commandId})).result.session,session);assert.equal(app.host.diagnostics().activeAgents,0);
 }finally{for(const socket of sockets)socket.terminate();await app?.close();await rm(directory,{recursive:true,force:true})}
});
