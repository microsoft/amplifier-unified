import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {HistoryQuiescence} from '../src/quiescence.js';
test('actual owner retention gate retains unknown fence, exact proof and 101-family bounds',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'retention-history-'));let gate=new HistoryQuiescence(directory);
 const c={fenceId:'hold',commandId:'reviewed-hide',purpose:'retention-hide',instanceId:'instance',dataScope:'owned'},sessions=Array.from({length:101},(_,i)=>'ahp-session:/'+i);
 try{const p=gate.participant('actual-history'),lease=await p.acquire(c);assert.equal(p.retentionHide.version,1);assert.equal(lease.ownerId,'actual-history');assert.deepEqual(await lease.inspectRetentionReferences({sessions}),{coverage:'complete',protected:[],omissions:[]});
 await assert.rejects(gate.effect(async()=>{throw Error('Effect must not run')}),/closed/);
 await lease.release('unknown');await assert.rejects(lease.inspectRetentionReferences({sessions}));await assert.rejects(lease.release('unchanged',{kind:'admission-refused'}));
 gate.close();gate=new HistoryQuiescence(directory);await assert.rejects(gate.participant('actual-history').acquire(c));
 const proof={verified:true,...c,outcome:'unchanged',receiptId:'exact-host-child'};
 await gate.participant('actual-history').reconcileRelease({...c,outcome:'unchanged',proof});
 await gate.participant('actual-history').reconcileRelease({...c,outcome:'unchanged',proof});
 assert.equal(gate.inspect().intakeClosed,false);
 }finally{gate.close();await rm(directory,{recursive:true,force:true})}
});

import {createHistoryCapability} from '../src/index.js';import {DatabaseSync} from 'node:sqlite';
test('history factory reports incomplete coverage for an unresolved import without querying native history',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'history-retention-data-'));const fail=()=>{throw Error('Native/host must not start')};const owner=createHistoryCapability({directory,engineId:'native',nativeAdmin:fail,authorizeWorkspace:fail,adoptImportedSession:fail,importAdoptionReceipt:fail});
 try{const db=new DatabaseSync(join(directory,'history-import.sqlite'));db.prepare('INSERT INTO commands VALUES(?,?,?,?,?,?,?)').run('c','workflow','history.import.commit','hash','{}','unknown',null);db.close();
 const context={fenceId:'f',commandId:'hide',purpose:'retention-hide',instanceId:'i',dataScope:'d'},lease=await owner.quiescenceParticipant.acquire(context);
 assert.deepEqual(await lease.inspectRetentionReferences({sessions:['ahp-session:/selected']}),{coverage:'partial',protected:[],omissions:[{reason:'history-import-unsettled',scope:'owner'}]});
 await lease.release('unchanged',{verified:true,...context,outcome:'unchanged',receiptId:'r'});
 }finally{owner.close();await rm(directory,{recursive:true,force:true})}
});
