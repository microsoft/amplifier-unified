import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {composeMessages} from '../src/messages.js';
import {composeCapabilities} from '../src/capabilities.js';
import {composeQuiescence} from '../src/quiescence.js';

test('real passive message bridge uses root account and canonical history; quiescence fences its own pipe',async()=>{
 const {createMessageCapabilities}=await import(process.env.UNIFIED_MESSAGE_BRIDGE_ENTRY??'@amplifier/unified-native-capabilities');
 const root=await mkdtemp(join(tmpdir(),'message-composition-passive-')),audit=join(root,'audit.jsonl'),script=join(root,'peer.mjs'),scope='ahp-session:/owned';let owner,composed;
 try{
  await writeFile(script,`import readline from 'node:readline';import{appendFileSync}from'node:fs';for await(const line of readline.createInterface({input:process.stdin})){const r=JSON.parse(line);appendFileSync(${JSON.stringify(audit)},JSON.stringify(r)+'\\n');let result={};if(r.method==='initialize')result={protocolVersion:1,agentCapabilities:{_meta:{'amplifier.dev/native':{version:1,messageInteractions:{version:1,methods:['reaction','read','receipt']}}}}};else if(r.method==='_amplifier/message/reaction')result={changed:true,messageId:'m',revision:1,sent:false,commandId:r.params.commandId};else if(r.method==='_amplifier/message/read')result={items:[],sent:false};else if(r.method==='_amplifier/message/receipt')result={receipt:null,replayed:false};else if(r.method!=='_amplifier/history')throw Error('Active admission forbidden: '+r.method);console.log(JSON.stringify({jsonrpc:'2.0',id:r.id,result}));}`);
  const account='configured-account',selected={uri:scope,engineId:'native',nativeSessionId:'original-native',workingDirectory:root,executionDirectory:'/relocated'};
  owner=await composeMessages(createMessageCapabilities,{id:'native',command:process.execPath,args:[script]},{account,cwd:root,inspectSession:async uri=>{assert.equal(uri,scope);return selected;}});assert.equal(owner.ready,true);
  composed=composeCapabilities([owner.capabilities],{account});
  const context={account:'browser-account',clientId:'one',session:{uri:scope,nativeSessionId:'forged',workingDirectory:'/forged'}},request=(operation,args,id='original')=>({channel:scope,topic:'message-metadata',operation:'message.'+operation,version:1,args,commandId:id});
  const first=await composed.action(request('reaction',{reference:{sessionId:'original-native',messageId:'m',position:0,historyEpoch:null},emoji:'👍',present:true}),context);assert.equal(first.accepted,true);assert.deepEqual(first.updates,[]);
  await composed.action(request('read',{messageIds:['m']},'read'),{...context,clientId:'two'});await composed.action(request('receipt',{commandId:'original'},'receipt'),context);
  const rows=()=>readFile(audit,'utf8').then(s=>s.trim().split('\n').map(JSON.parse));
  const before=await rows();assert.deepEqual(before.map(r=>r.method),['initialize','_amplifier/history','_amplifier/message/reaction','_amplifier/message/read','_amplifier/message/receipt']);
  assert.deepEqual(before[0].params.clientCapabilities._meta['amplifier.dev/messagePrincipal'],{version:1,account});assert.deepEqual(before[1].params,{sessionId:'original-native',cwd:root,limit:1});assert.equal(before[2].params.sessionId,'original-native');assert.equal(before[2].params.commandId,'original');assert.equal(before[2].params.account,undefined);
  const coverage=composeQuiescence({instanceId:'i',dataScope:'d'},[owner.capabilities]),participant=coverage.participants[0];assert.equal(coverage.coverage.capabilities['message-metadata'],participant.id);
  const lease=await participant.acquire({fenceId:'f',commandId:'c',purpose:'recovery',instanceId:'i',dataScope:'d'});assert.ok(lease);
  assert.equal((await composed.action(request('read',{messageIds:['m']},'fenced'),context)).accepted,false);assert.equal((await rows()).length,before.length);
  await lease.release('unchanged',{kind:'admission-refused'});await composed.action(request('receipt',{commandId:'original'},'after'),context);
  await composed.close();assert.equal(owner.connection.activity().closed,true);composed=null;
 }finally{if(composed)await composed.close();else if(owner&&!owner.connection.activity().closed)await owner.connection.close();await rm(root,{recursive:true,force:true});}
});
