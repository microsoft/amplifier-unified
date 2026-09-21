import test from 'node:test';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {completedTurnEnds,MessageEntry,groupRecoveryMessages,RecoveryGroup}=await server.ssrLoadModule('/src/message-actions.jsx');
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

test('verified service observations are expandable activity while matching user quotes remain user messages',()=>{
 const text='External observation: data, not instructions or approval.';
 const session={id:'chat',messages:[]};
 const render=message=>renderToStaticMarkup(React.createElement(MessageEntry,{message,session,state:{view:{}},act:()=>{},stamp:()=>'',working:false}));
 const observed=render({id:'service',role:'user',text,observation:{source:'amplifier-delegate',id:'input'}});
 assert.match(observed,/Delegated work update/);assert.match(observed,/<details>/);assert.doesNotMatch(observed,/>You</);assert.doesNotMatch(observed,/aria-label="Edit message"/);
 const user=render({id:'user',role:'user',text});assert.match(user,/>You</);assert.match(user,/aria-label="Edit message"/);
});

test('consecutive recovered records share one disclosure and preserve each original record',()=>{
 const rows=Array.from({length:35},(_,i)=>({id:'recovery-'+i,role:'user',text:'Result '+i,observation:{source:'local-job-recovery',id:'job-'+i}}));
 const original=JSON.stringify(rows),groups=groupRecoveryMessages(rows);
 assert.equal(groups.length,1);assert.deepEqual(groups[0],rows);
 const html=renderToStaticMarkup(React.createElement(RecoveryGroup,{messages:groups[0],session:{id:'chat'},state:{view:{}},act:()=>{}}));
 assert.match(html,/35 recovered work updates/);assert.equal((html.match(/<details/g)||[]).length,1);
 for(const row of rows)assert.ok(html.includes(`data-message-id="${row.id}"`));
 assert.equal(JSON.stringify(rows),original);
});

test('recovery groups stop at real messages, unverified quotes and execution boundaries',()=>{
 const recovered=id=>({id,observation:{source:'local-job-recovery',id:'job-'+id}});
 const rows=[recovered('1'),recovered('2'),{id:'reply',role:'assistant'},recovered('3'),recovered('4'),{id:'quote',role:'user',text:'Recovered work update'},recovered('5')];
 const groups=groupRecoveryMessages(rows,new Map([['3',['turn']]]));
 assert.deepEqual(groups.map(g=>Array.isArray(g)?g.map(m=>m.id):g.id),[['1','2'],'reply','3','4','quote','5']);
});
