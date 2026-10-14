const allocation={allocationId:'12345678-1234-1234-1234-123456789abc',executionDirectory:'/owned/managed/files',allocationHash:'a'.repeat(64),treeHash:'b'.repeat(64),entryCount:1,bytes:5};
import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {WorktreeQuiescence} from '../src/quiescence.js';
test('actual managed-files gate retains unknown fence, exact proof and 101-family bounds',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'retention-worktree-'));let gate=new WorktreeQuiescence(directory);
 const c={fenceId:'hold',commandId:'reviewed-hide',purpose:'managed-files-disposal',instanceId:'instance',dataScope:'owned'},sessions=Array.from({length:101},(_,i)=>'ahp-session:/'+i);
 try{const p=(gate.managedReferences=()=>({coverage:'complete',protected:[],omissions:[]}),gate.participant('actual-worktree')),lease=await p.acquire(c);assert.equal(p.managedFiles.version,1);assert.equal(lease.ownerId,'actual-worktree');assert.deepEqual(await lease.inspectManagedFilesReferences({allocation,sessions}),{coverage:'complete',protected:[],omissions:[]});
 await assert.rejects(gate.effect(async()=>{throw Error('Effect must not run')}),/closed/);
 await assert.rejects(lease.inspectManagedFilesReferences({allocation:{...allocation,treeHash:'c'.repeat(64)},sessions:['ahp-session:/one']}));await lease.release('unknown');await assert.rejects(lease.inspectManagedFilesReferences({allocation,sessions}));await assert.rejects(lease.release('unchanged',{kind:'admission-refused'}));
 gate.close();gate=new WorktreeQuiescence(directory);await assert.rejects((gate.managedReferences=()=>({coverage:'complete',protected:[],omissions:[]}),gate.participant('actual-worktree')).acquire(c));
 const proof={verified:true,...c,outcome:'unchanged',receiptId:'exact-host-child'};
 await (gate.managedReferences=()=>({coverage:'complete',protected:[],omissions:[]}),gate.participant('actual-worktree')).reconcileRelease({...c,outcome:'unchanged',proof});
 await (gate.managedReferences=()=>({coverage:'complete',protected:[],omissions:[]}),gate.participant('actual-worktree')).reconcileRelease({...c,outcome:'unchanged',proof});
 assert.equal(gate.inspect().intakeClosed,false);
 }finally{gate.close();await rm(directory,{recursive:true,force:true})}
});

import {WorktreeStore} from '../src/store.js';
test('worktree retained uncertain commands protect only their explicit conversations',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'worktree-retention-data-')),store=new WorktreeStore(directory),session='ahp-session:/selected';
 try{store.begin({id:'c',sessionId:session,operation:'worktree.handoff',phase:'unknown',createdAt:1,revision:1},'hash');
 const context={fenceId:'f',commandId:'hide',purpose:'managed-files-disposal',instanceId:'i',dataScope:'d'},lease=await store.quiescence.participant('worktree').acquire(context);
 assert.deepEqual(await lease.inspectManagedFilesReferences({allocation,sessions:[session,'ahp-session:/other']}),{coverage:'complete',protected:[{session,reasons:['worktree-unsettled']}],omissions:[]});
 await lease.release('unchanged',{verified:true,...context,outcome:'unchanged',receiptId:'r'});
 }finally{store.close();await rm(directory,{recursive:true,force:true})}
});

test('cross-conversation retained worktree source protects only overlapping allocation',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'managed-worktree-ref-')),store=new WorktreeStore(directory);
 try{store.begin({id:'retained',sessionId:'ahp-session:/other',operation:'worktree.create',phase:'completed',createdAt:1,revision:1,source:'/owned/managed/files/repo'},'sig');
 const c={fenceId:'f',commandId:'reviewed',purpose:'managed-files-disposal',instanceId:'i',dataScope:'d'},lease=await store.quiescence.participant('worktree').acquire(c);
 assert.deepEqual(await lease.inspectManagedFilesReferences({allocation,sessions:['ahp-session:/selected']}),{coverage:'complete',protected:[{session:'ahp-session:/selected',reasons:['worktree-file-reference']}],omissions:[]});
 await lease.release('unchanged',{verified:true,...c,outcome:'unchanged',receiptId:'refused'});
 assert.equal(store.db.prepare('SELECT count(*) n FROM commands').get().n,1);
 }finally{store.close();await rm(directory,{recursive:true,force:true})}
});
