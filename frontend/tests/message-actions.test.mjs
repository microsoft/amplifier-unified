import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {completedTurnEnds}=await server.ssrLoadModule('/src/message-actions.jsx');
test.after(()=>server.close());
test('fork controls follow the last assistant entry in each completed user turn',()=>{
 const messages=[{id:'u1',role:'user',inputId:'1'},{id:'a1',role:'assistant'},{id:'a2',role:'assistant'},{id:'u2',role:'user',inputId:'2'},{id:'a3',role:'assistant'}];
 const session={status:'working',messages,execution:{turns:[{inputId:'1',phase:'completed'},{inputId:'2',phase:'running'}]}};
 assert.deepEqual([...completedTurnEnds(session)],[['a2',1]]);
 session.status='idle';session.execution.turns[1].phase='completed';assert.deepEqual([...completedTurnEnds(session)],[['a2',1],['a3',2]]);
});
test('legacy completed messages support forks but partial failed responses do not',()=>{
 const session={status:'idle',messages:[{id:'u1',role:'user'},{id:'a1',role:'assistant'}]};
 assert.equal(completedTurnEnds(session).get('a1'),1);session.status='error';assert.equal(completedTurnEnds(session).size,0);
});


test('paged native history uses the original user-turn number for forks',()=>{
 const session={status:'idle',sharedHistoryUserTurnOffset:21,messages:[{id:'older-assistant',role:'assistant'},{id:'u22',role:'user'},{id:'a22',role:'assistant'},{id:'u23',role:'user'},{id:'a23',role:'assistant'}]};
 assert.deepEqual([...completedTurnEnds(session)],[['a22',22],['a23',23]]);
});
