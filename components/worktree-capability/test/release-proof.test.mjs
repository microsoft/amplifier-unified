import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';import {spawn} from 'node:child_process';import {once} from 'node:events';import {createInterface} from 'node:readline';
import {WorktreeQuiescence} from '../src/quiescence.js';
const context={fenceId:'exact-release',commandId:'maintenance',purpose:'recovery',instanceId:'original',dataScope:'owned'},proof={verified:true,...context,outcome:'unchanged',receiptId:'durable-receipt'};
test('lost release acknowledgement survives process replacement and accepts only exact outcome and proof',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'release-proof-')),module=new URL('../src/quiescence.js',import.meta.url).href;
 const script=`import {WorktreeQuiescence} from ${JSON.stringify(module)};const directory=process.argv[1],context=JSON.parse(process.argv[2]),proof=JSON.parse(process.argv[3]);const gate=new WorktreeQuiescence(directory);const lease=await gate.participant().acquire(context);await lease.release('unchanged',proof);console.log('release-persisted');setInterval(()=>{},1000)`;
 const child=spawn(process.execPath,['--input-type=module','-e',script,directory,JSON.stringify(context),JSON.stringify(proof)],{stdio:['ignore','pipe','pipe']});child.stderr.resume();const lines=createInterface({input:child.stdout});let gate;
 try{assert.equal((await once(lines,'line'))[0],'release-persisted');child.kill('SIGKILL');await once(child,'exit');lines.close();gate=new WorktreeQuiescence(directory);const participant=gate.participant();
  await participant.reconcileRelease({...context,outcome:'unchanged',proof:Object.fromEntries(Object.entries(proof).reverse())});
  for(const input of [{...context,outcome:'ready',proof:{...proof,outcome:'ready',instanceId:'replacement'}},{...context,outcome:'unchanged',proof:{...proof,receiptId:'different'}},{...context,dataScope:'foreign',outcome:'unchanged',proof},{...context,outcome:'unknown',proof}])await assert.rejects(participant.reconcileRelease(input),/Exact|exact/);
  assert.equal(gate.inspect().intakeClosed,false);
 }finally{if(child.exitCode===null&&child.signalCode===null){child.kill('SIGKILL');await once(child,'exit')}lines.close();gate?.close();await rm(directory,{recursive:true,force:true});}
});
