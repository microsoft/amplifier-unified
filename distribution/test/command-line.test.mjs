import test from 'node:test';
import assert from 'node:assert/strict';
import {parseCommandLine,manageService,inspectInstallation,runCommandLine} from '../src/command-line.js';
import {serviceFixture} from './service-config-fixture.mjs';

test('ordinary commands keep launch compatibility and validate options before work',()=>{
 assert.deepEqual(parseCommandLine(['--config','/private/config.json']),{command:'serve',configuration:'/private/config.json'});
 assert.deepEqual(parseCommandLine(['serve','--config','/private/config.json']),parseCommandLine(['--config','/private/config.json']));
 assert.equal(parseCommandLine([]).command,'help');
 assert.equal(parseCommandLine(['doctor'],{AMPLIFIER_UNIFIED_INSTALLATION:'/private/installed'}).directory,'/private/installed');
 for(const args of [['doctor'],['doctor','--data-dir','/x','--request','x'],['service','status','--data-dir','/x','--wait','NaN'],['service','stop','--data-dir','/x','--data-dir','/y'],['serve','--config','/x','--json'],['service','receipt','--data-dir','/x'],['service','status','--data-dir','/x','--wait','121']])assert.throws(()=>parseCommandLine(args,{}));
});

const identity={installationId:'installation',ownerId:'owner',dataScope:'data',instanceId:'instance',releaseDigest:'a'.repeat(64)};
function controller({state='running',stopStatus='stopped',loseReply=false}={}){
 const effects=[],announced=[];let closed=false;
 const stopped={commandId:'original-stop',operation:'stop',status:'stopped',expected:identity};
 const receipts=new Map([[stopped.commandId,stopped]]);
 const client={service:{
  inspect:async()=>({state,identity,...(state==='stopped'?{stoppedReceiptId:stopped.commandId}:{ownership:'verified'})}),
  stop:async c=>{effects.push(['stop',c]);const r={...c,status:stopStatus,operation:'stop'};receipts.set(c.commandId,r);if(loseReply)throw Error('reply_lost');return r;},
  resume:async c=>{effects.push(['resume',c]);const r={...c,status:'ready',operation:'resume',admissionSettlement:{state:'settled'}};receipts.set(c.commandId,r);return r;},
  receipt:async id=>receipts.get(id)??null,
 },close:()=>{closed=true;}};
 return {effects,announced,client,connect:async()=>client,onRequest:r=>announced.push(r),isClosed:()=>closed};
}
test('restart resumes only its own acknowledged stop and announces each identity before dispatch',async()=>{
 const f=controller(),result=await manageService({directory:'/owned',action:'restart',wait:0},f);
 assert.equal(result.receipts.length,2);assert.equal(f.effects.length,2);
 assert.equal(f.effects[1][1].stoppedCommandId,f.effects[0][1].commandId);
 assert.deepEqual(f.effects[1][1].expected,identity);
 assert.deepEqual(f.announced.map(r=>r.request),f.effects.map(r=>r[1].commandId));assert.equal(f.isClosed(),true);
});
test('unknown, busy and still-running stop outcomes never start a replacement',async()=>{
 for(const stopStatus of ['refused','unknown','running']){
  const f=controller({stopStatus});await manageService({directory:'/owned',action:'restart',wait:0},f);
  assert.equal(f.effects.length,1);assert.equal(f.isClosed(),true);
 }
 const f=controller({state:'unknown'});await assert.rejects(manageService({directory:'/owned',action:'start',wait:0},f),/requires_inspection/);assert.equal(f.effects.length,0);
});
test('lost stop reply preserves original request for read-only inspection with no repeated effect',async()=>{
 const f=controller({loseReply:true});let request;
 await assert.rejects(manageService({directory:'/owned',action:'restart',wait:0},f),e=>{request=e.request;return e.message==='service_request_unconfirmed';});
 assert.equal(request,f.effects[0][1].commandId);assert.equal(request,f.announced[0].request);
 const result=await manageService({directory:'/owned',action:'receipt',request},f);
 assert.equal(result.receipt.status,'stopped');assert.equal(f.effects.length,1);
});
test('already running start and already stopped stop are observations, not effects',async()=>{
 for(const [state,action] of [['running','start'],['stopped','stop']]){
  const f=controller({state}),r=await manageService({directory:'/owned',action,wait:0},f);
  assert.equal(r.changed,false);assert.equal(f.effects.length,0);
 }
 const f=controller({state:'stopped'});await manageService({directory:'/owned',action:'start',wait:0},f);
 assert.equal(f.effects[0][1].stoppedCommandId,'original-stop');
});
test('doctor reads retained installation and authenticated service without prompts or mutations',async t=>{
 const fixture=await serviceFixture(t),f=controller();
 const result=await inspectInstallation(fixture.root,f);
 assert.equal(result.ok,true);assert.equal(result.installation,'standard');assert.equal(result.workReplayed,false);
 assert.equal(f.effects.length,0);assert.equal(f.isClosed(),true);
 const stopped=await inspectInstallation(fixture.root,controller({state:'stopped'}));assert.equal(stopped.ok,false);
});
test('missing or private invalid input reports safe guidance without leaking configuration',async()=>{
 const messages=[];const code=await runCommandLine({command:'doctor',directory:'/missing/SECRET_TOKEN_123',json:true},{out:s=>messages.push(s),error:s=>messages.push(s)});
 assert.equal(code,1);assert.equal(messages.length,1);assert.doesNotMatch(messages[0],/SECRET_TOKEN/);
 assert.equal(JSON.parse(messages[0]).workReplayed,false);
});
