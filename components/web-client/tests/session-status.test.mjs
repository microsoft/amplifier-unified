import test from 'node:test';
import assert from 'node:assert/strict';
import {sessionStatus} from '../src/session-status.js';

test('first session preparation explains setup and preserves the message context',()=>{
 const state={status:'starting',progress:{message:'Loading the foundation bundle'},messages:[{text:'Please review my project'}]};
 const view=sessionStatus(state);
 assert.equal(view.label,'Preparing your Amplifier session…');
 assert.equal(view.detail,'Loading the foundation bundle');
 assert.match(view.explanation,/configured bundle and tools/);
 assert.match(view.explanation,/message stays/);
 assert.equal(view.busy,true);
 assert.equal(state.messages[0].text,'Please review my project');
});
test('working, stopping, ready, and failure are distinct',()=>{
 const statuses=['starting','working','stopping','idle','error'].map(status=>sessionStatus({status}));
 assert.equal(new Set(statuses.map(s=>s.label)).size,5);
 assert.deepEqual(statuses.map(s=>s.busy),[true,true,true,false,false]);
});
test('progress uses reported text without fabricating percentages or completed stages',()=>{
 assert.equal(sessionStatus({status:'starting'}).detail,'');
 assert.equal(sessionStatus({status:'working',progress:'Preparing the requested tool'}).detail,'Preparing the requested tool');
 assert.equal(sessionStatus({status:'error',progress:'Earlier progress'}).detail,'');
});

test('external startup identifies its participant without promising local bundle loading',()=>{
 const externalAgent={id:'remote',label:'Project assistant'};
 const starting=sessionStatus({status:'starting',externalAgent});
 assert.equal(starting.label,'Connecting to Project assistant…');
 assert.match(starting.explanation,/message stays/);
 assert.doesNotMatch(starting.explanation,/bundle|Amplifier/);
 assert.equal(sessionStatus({status:'working',externalAgent}).label,'Project assistant is working…');
 assert.equal(sessionStatus({status:'error',externalAgent}).label,'Project assistant needs your attention');
});
