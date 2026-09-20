import test from 'node:test';
import assert from 'node:assert/strict';
import {activityFor,relativeActivity,compactParent,parentPath} from '../src/navigation-presentation.js';

test('navigation distinguishes unresolved input, active work, errors, and new replies',()=>{
 assert.equal(activityFor({status:'working',approvals:[{status:'pending'}]}).kind,'attention');
 assert.equal(activityFor({status:'idle',approvals:[{status:'approved'}]}).kind,'idle');
 assert.equal(activityFor({status:'working',error:'previous'}).kind,'working');
 assert.equal(activityFor({status:'error'}).kind,'attention');
 assert.equal(activityFor({id:'new'}, {attention:{sessions:{new:1}}}).kind,'unread');
 assert.equal(activityFor({activity:{kind:'attention',label:'Approval requested'}}).label,'Approval requested');
});
test('recency handles unknown and future timestamps without inventing activity',()=>{
 for(const value of [null,undefined,NaN,Infinity,'1',0,-1])assert.equal(relativeActivity(value,3600).short,'—');
 assert.equal(relativeActivity(4000,3600).short,'now');
 assert.equal(relativeActivity(3540,3600).long,'1 minute ago');
 assert.equal(relativeActivity(1,7201).short,'2h');
 assert.equal(relativeActivity(1,172801).long,'2 days ago');
});
test('workspace parent labels preserve tails on POSIX and Windows paths',()=>{
 assert.equal(parentPath('/project'),'/');
 assert.equal(parentPath('/deep/parent/project/'),'/deep/parent');
 assert.equal(compactParent('/home/person/dev/experiments/september/project'),'…/experiments/september');
 assert.equal(parentPath('C:\\work\\project'),'C:\\work');
});

test('workspace rows retain distinguishing ancestors beyond a common deep parent',async()=>{
 const {workspaceContext}=await import('../src/navigation-presentation.js');
 assert.equal(workspaceContext({name:'app',path:'/one/common/deep/app',pathLabel:'one/common/deep/app'}),'one/common/deep');
 assert.equal(workspaceContext({name:'app',path:'/two/common/deep/app',pathLabel:'two/common/deep/app'}),'two/common/deep');
});
