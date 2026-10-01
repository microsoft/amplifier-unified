import test from 'node:test';
import assert from 'node:assert/strict';
import {updateOverview} from '../src/update-overview.js';
import {matchingSetupOperation} from '../src/ai-connections-data.js';

test('only a completed whole sequence is described as up to date',()=>{
 assert.notEqual(updateOverview({application:{status:'current'},lastCheck:1}).tone,'ready');
 assert.equal(updateOverview({application:{status:'current'},sequence:{stage:'complete'}}).tone,'ready');
});
test('install intent survives restart and idle wait without asking again',()=>{
 for(const pending of [{pendingApp:{}},{pendingRelease:{}},{pendingRestart:{requestStatus:'uncertain'}}]){
  const result=updateOverview({...pending,sequence:{stage:'application',install:true,nextStage:'included'}});
  assert.equal(result.continuing,true);assert.equal(result.tone,'working');assert.notEqual(result.title,'You’re up to date');
 }
});
test('protected edits and failed required components never claim completion',()=>{
 const result=updateOverview({sequence:{stage:'included',install:true,included:{issues:1,protected:1}}});
 assert.equal(result.blocked,true);assert.equal(result.tone,'error');
 assert.equal(updateOverview({pendingReplacement:{},sequence:{stage:'complete'}}).tone,'error');
 assert.equal(updateOverview({application:{status:'check_failed'},sequence:{stage:'complete'}}).tone,'error');
});
test('an install failure with continuation intent remains visibly actionable',()=>{
 const result=updateOverview({pendingApp:{},phase:'error',error:'Restart failed',sequence:{stage:'application',install:true}});
 assert.equal(result.tone,'error');assert.equal(result.installable,true);assert.equal(result.blocked,false);
});
test('provider setup ignores stale operation acknowledgments',()=>{
 const pending={action:'providers.save',id:'one',commandId:'new'};
 assert.equal(matchingSetupOperation({setup:{operations:{'providers.save:one':{commandId:'old',phase:'ready'}}}},pending),null);
 assert.equal(matchingSetupOperation({setup:{operations:{'providers.save:one':{commandId:'new',phase:'error'}}}},pending).phase,'error');
});

test('new batch shows real successful configuration count and retains installed milestones',()=>{
 const diagnostics={attemptId:'new',batch:{attemptId:'new',kind:'ecosystem',tier:'other',components:['approval'],startedAt:1},
  completedBatches:[{attemptId:'old',tier:'other',completedAt:2}]};
 const result=updateOverview({phase:'validating',sequence:{stage:'other',install:true},diagnostics,
  probeProgress:{attemptId:'new',phase:'prepare',completed:24,total:37}});
 assert.equal(result.title,'Updating other components');
 assert.match(result.detail,/24 of 37 completed/);
 assert.equal(result.completedBatches[0].attemptId,'old');
 assert.equal(result.currentBatch.components[0],'approval');
});
test('stale, invalid and terminal progress never masquerades as current preparation',()=>{
 for(const probeProgress of [{attemptId:'old',completed:37,total:37},{attemptId:'new',completed:38,total:37},{attemptId:'new',completed:-1,total:37}]){
  assert.equal(updateOverview({phase:'validating',diagnostics:{attemptId:'new',batch:{attemptId:'new'}},probeProgress}).currentProgress,null);
 }
 const result=updateOverview({phase:'staged',pendingRelease:'candidate',diagnostics:{attemptId:'new',batch:{attemptId:'new'}},probeProgress:{attemptId:'new',phase:'prepare',completed:37,total:37}});
 assert.equal(result.currentProgress,null);assert.equal(result.title,'Waiting for your work to finish');
});
