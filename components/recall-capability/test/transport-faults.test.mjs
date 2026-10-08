import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, writeFile, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createRecallCapability} from '../src/index.js';
async function peer(t, body, options={}) {
 const dir=await mkdtemp(join(tmpdir(),'owner-transport-')),path=join(dir,'peer.mjs');
 const pidFile=join(dir,'peer.pid');
 await writeFile(path,`import {writeFileSync} from 'node:fs';writeFileSync(${JSON.stringify(pidFile)},String(process.pid));import {createInterface} from 'node:readline';const reply=(id,result)=>process.stdout.write(JSON.stringify({jsonrpc:'2.0',id,result})+'\\n');createInterface({input:process.stdin}).on('line',line=>{const row=JSON.parse(line);${body}});`);
 const launch={command:process.execPath,args:[path],requestTimeoutMs:150,initializeTimeoutMs:2000,...options};
 const owner=createRecallCapability({owner:launch});const call=()=>owner.actionSchemas();
 t.after(async()=>{await owner.close();await rm(dir,{recursive:true,force:true})});
 return {owner,call,isAlive:async()=>{const pid=Number(await readFile(pidFile,'utf8'));try{process.kill(pid,0);return true}catch(error){if(error.code==='ESRCH')return false;throw error}}};
}
const initialize="if(row.method==='initialize'){reply(row.id,{protocolVersion:1,quiescence:{heldIntake:true,durableRelease:true}});return;}";
test('silent initialize is bounded; closed transport cannot replay work',async t=>{
 const {call}=await peer(t,'',{initializeTimeoutMs:75});
 await assert.rejects(call(),/timed out.*unknown/);
 await assert.rejects(call(),/closed|unavailable/i);
});
test('all 64 silent requests settle as unknown and channel stays closed',async t=>{
 const {owner,call}=await peer(t,initialize+"if(!globalThis.warm){globalThis.warm=true;reply(row.id,{});}");
 await call();
 const settled=Promise.allSettled(Array.from({length:64},()=>call()));
 await assert.rejects(call(),/capacity/);
 for(const result of await settled){assert.equal(result.status,'rejected');assert.match(result.reason.message,/timed out.*unknown/);}
 if(owner.pending)assert.equal(owner.pending.size,0);
 await assert.rejects(call(),/closed|unavailable/i);
});
for(const frame of [null,[],{jsonrpc:'2.0',method:'owner/changed'},{jsonrpc:'2.0',method:'owner/changed',params:null},{jsonrpc:'2.0',id:2,error:{code:-1}},{jsonrpc:'2.0',id:2,result:{},error:{code:-1,message:'both'}}]){
 test('malformed frame is contained: '+JSON.stringify(frame),async t=>{
  const {call}=await peer(t,initialize+`process.stdout.write(${JSON.stringify(JSON.stringify(frame)+'\n')});`);
  await assert.rejects(call(),/invalid|malformed/i);
  await assert.rejects(call(),/closed|unavailable/i);
 });
}
test('already exited owner closes without the fallback delay',async t=>{
 const {owner,call}=await peer(t,initialize+'process.exit(0)');
 await assert.rejects(call(),/exited|closed/i);
 const start=performance.now();await owner.close();assert.ok(performance.now()-start<500);
});
test('settled request clears deadline without killing healthy channel',async t=>{
 const {call}=await peer(t,initialize+'reply(row.id,{});');
 await call();await new Promise(resolve=>setTimeout(resolve,200));await call();
});


for(const result of [null,[],{protocolVersion:99}]){
 test('invalid initialization closes the owned process: '+JSON.stringify(result),async t=>{
  const {call,isAlive}=await peer(t,`reply(row.id,${JSON.stringify(result)});`);
  await assert.rejects(call());
  await assert.rejects(call(),/closed|unavailable/i);
  const deadline=Date.now()+1000;
  while(await isAlive()){assert.ok(Date.now()<deadline,'invalid peer still alive');await new Promise(r=>setTimeout(r,10));}
 });
}
