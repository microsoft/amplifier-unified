import test from 'node:test';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {newestMessageIds,completedTurnEnds,MessageEntry,groupRecoveryMessages,RecoveryGroup}=await server.ssrLoadModule('/src/message-actions.jsx');
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

test('unknown native message time does not display the session creation time',()=>{
 const render=message=>renderToStaticMarkup(React.createElement(MessageEntry,{message,session:{id:'chat'},state:{view:{}},act:()=>{},stamp:at=>'TIME-'+at,working:false}));
 const message={id:'progress',role:'assistant',text:'Workers started',createdAt:100};
 assert.match(render(message),/1970-01-01T00:01:40.000Z/);
 assert.match(render({...message,timestampKnown:true}),/1970-01-01T00:01:40.000Z/);
 const unknown=render({...message,timestampKnown:false});
 assert.doesNotMatch(unknown,/<time\b/);assert.doesNotMatch(unknown,/1970-01-01T00:01:40.000Z/);
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
 assert.equal(observed,'');assert.doesNotMatch(observed,/>You</);assert.doesNotMatch(observed,/aria-label="Edit message"/);
 const user=render({id:'user',role:'user',text});assert.doesNotMatch(user,/>You</);assert.match(user,/aria-label="Your message"/);assert.match(user,/aria-label="Edit message"/);
});

test('consecutive recovered records share one disclosure and preserve each original record',()=>{
 const rows=Array.from({length:35},(_,i)=>({id:'recovery-'+i,role:'user',text:'Result '+i,observation:{source:'local-job-recovery',id:'job-'+i}}));
 const original=JSON.stringify(rows),groups=groupRecoveryMessages(rows);
 assert.equal(groups.length,1);assert.deepEqual(groups[0],rows);
 const html=renderToStaticMarkup(React.createElement(RecoveryGroup,{messages:groups[0],session:{id:'chat'},state:{view:{}},act:()=>{}}));
 assert.match(html,/Saved work notices \(35\)/);assert.equal((html.match(/<details/g)||[]).length,1);
 for(const row of rows)assert.ok(!html.includes(`data-message-id="${row.id}"`));
 assert.equal(JSON.stringify(rows),original);
});

test('recovery groups stop at real messages, unverified quotes and execution boundaries',()=>{
 const recovered=id=>({id,observation:{source:'local-job-recovery',id:'job-'+id}});
 const rows=[recovered('1'),recovered('2'),{id:'reply',role:'assistant'},recovered('3'),recovered('4'),{id:'quote',role:'user',text:'Recovered work update'},recovered('5')];
 const groups=groupRecoveryMessages(rows,new Map([['3',['turn']]]));
 assert.deepEqual(groups.map(g=>Array.isArray(g)?g.map(m=>m.id):g.id),[['1','2'],'reply','3','4','quote','5']);
});

test('smart date follows every action, including branch, without a native tooltip',()=>{
 const html=renderToStaticMarkup(React.createElement(MessageEntry,{message:{id:'answer',role:'assistant',text:'Done',createdAt:100},session:{id:'chat'},state:{view:{}},act:()=>{},forkTurn:1,working:false}));
 assert.ok(html.indexOf('data-action="session.fork"')<html.indexOf('<time'));
 assert.match(html,/<time[^>]*aria-label=/);assert.doesNotMatch(html,/<time[^>]*title=/);
 assert.match(html,/<\/time><\/div><\/article>$/);
});


test('newest user and assistant footers stay visible independently of observations',()=>{
 const messages=[{id:'u1',role:'user'},{id:'a1',role:'assistant'},{id:'u2',role:'user'},{id:'a2',role:'assistant'},{id:'o',role:'user',observation:{source:'amplifier-delegate'}}];
 assert.deepEqual([...newestMessageIds(messages)],['u2','a2']);
 const html=renderToStaticMarkup(React.createElement(MessageEntry,{message:{id:'u',role:'user',via:'call',text:'Hello'},session:{id:'s'},state:{view:{}},act:()=>{},newest:true}));
 assert.match(html,/data-newest="true"/);assert.doesNotMatch(html,/a-msg-meta/);
 assert.ok(html.indexOf('a-message-body')<html.indexOf('a-message-actions'));
 assert.ok(html.indexOf('Edit message')<html.indexOf('via call'));
});

test('trusted peer caption is always visible outside hover actions with a matching article label',()=>{
 const caption='Sent by Amplifier from another chat';
 const render=message=>renderToStaticMarkup(React.createElement(MessageEntry,{message,session:{id:'chat'},state:{view:{}},act:()=>{},working:false}));
 const html=render({id:'peer',role:'user',text:'Original **α**',via:'peer',attribution:{caption}});
 assert.match(html,/aria-label="Sent by Amplifier from another chat"/);
 assert.match(html,/<p class="a-message-attribution">Sent by Amplifier from another chat<\/p>/);
 assert.ok(html.indexOf('a-message-attribution')<html.indexOf('a-message-actions'));
 assert.doesNotMatch(html,/via peer/);assert.doesNotMatch(html,/Your message/);
 assert.doesNotMatch(html,/a-message-attribution[^>]*(tabindex|href)/i);
 const forwarded=render({id:'human-forward',role:'user',text:'Original',via:'peer',attribution:{caption:'From another chat'}});
 assert.match(forwarded,/aria-label="From another chat"/);assert.doesNotMatch(forwarded,/Sent by Amplifier/);
 const unknown=render({id:'legacy',role:'user',text:caption,via:'peer'});
 assert.match(unknown,/aria-label="Message"/);assert.match(unknown,/via peer/);
 assert.doesNotMatch(unknown,/a-message-attribution/);
 const human=render({id:'human',role:'user',text:caption});
 assert.match(human,/aria-label="Your message"/);assert.doesNotMatch(human,/a-message-attribution/);
});
