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
test('app preparation is never mislabeled as other components',()=>{
 assert.equal(updateOverview({phase:'staging',sequence:{stage:'application'}}).title,'Updating Amplifier');
});

test('pending activation locks duplicate updates, but a failed candidate can be retried',()=>{
 for(const pending of [{pendingApp:{}},{pendingRelease:'candidate',plan:{activation:'per-worker'}},{pendingSmartTools:[{id:'tool'}]}]){
  const running=updateOverview({...pending,phase:'staged'});
  assert.equal(running.busy,true);
  assert.equal(updateOverview({...pending,phase:'error',error:'Failed'}).busy,false);
 }
});
test('restart waiting wins over a previous component plan and counts distinct chats only',()=>{
 const result=updateOverview({phase:'staged',pendingApp:{},pendingRelease:'candidate',plan:{activation:'per-worker'},blockers:[{kind:'conversation',sessionId:'one'},{kind:'conversation',sessionId:'one'}]});
 assert.equal(result.waiting,true);assert.equal(result.processing,false);assert.match(result.title,/1 chat to finish/);assert.match(result.detail,/restart automatically/);
 const mixed=updateOverview({pendingApp:{},blockers:[{kind:'voice',sessionId:'one'},{kind:'conversation',sessionId:'one'}]});
 assert.equal(mixed.title,'Waiting for your work to finish');
});
test('component adoption is ready only after activation and all update stages finish',()=>{
 const state={adoption:{pendingWorkers:2},sequence:{stage:'complete'}};
 assert.equal(updateOverview(state).title,'Ready for new work');
 assert.equal(updateOverview({...state,pendingRelease:'candidate',plan:{activation:'per-worker'}}).tone,'working');
 assert.equal(updateOverview({...state,error:'Failed'}).tone,'error');
 assert.equal(updateOverview({...state,adoption:{pendingWorkers:0}}).title,'You’re up to date');
});
