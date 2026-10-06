import {test} from 'node:test';import assert from 'node:assert/strict';
import {createCoordinationCapabilities} from '../dist/index.js';
const session='ahp-session:/saved',caller='ahp-session:/reader';
const authority={clientId:'browser-one',origin:'agent',session:caller};
function fixture(t,{inspect,read,enabled=true}={}){
 const calls=[];let revision='one';
 const pages={first:[...Array(53)].map((_,i)=>({id:'m'+i,role:i%2?'assistant':'user',text:'😀'.repeat(15)+i,inputOrigin:'voice'})),second:[{id:'last',role:'assistant',text:'final saved text'}]};
 const owner=createCoordinationCapabilities({owner:{command:'/must-not-launch-coordination-owner'},...(enabled?{history:{inspect:inspect??(async id=>{calls.push(['inspect',id]);return {id,revision};}),read:read??(async(id,args)=>{calls.push(['read',id,args]);if(args.expectedRevision!==revision)throw Error('Saved revision changed');return {revision,rows:args.cursor?pages.second:pages.first,...(!args.cursor?{nextCursor:'native-next'}:{}),omittedUpdates:2};})}}:{}),listCoordinationSessions:async()=>{throw Error('No inventory scan')},readCoordinationSession:async()=>{throw Error('No settled-result substitution')},readCoordinationWorkers:async()=>{throw Error('No worker reads')},controlCoordinationWorker:async()=>{throw Error('No effects')},controlCoordinationSession:async()=>{throw Error('No effects')},observeSession:async()=>{throw Error('No watches')}});
 t.after(()=>owner.close());
 return {owner,calls,setRevision:value=>revision=value,action:(args,context=authority)=>owner.action({version:1,topic:'coordination',operation:'coordination.read',channel:'host',args},context).then(r=>r.result)};
}
test('peer history pages every message within and across native turns, keeps exact identities and bounds Unicode text',async t=>{
 const f=fixture(t);assert.ok(f.owner.manifest.actions['coordination.read']);assert.equal(f.owner.quiescenceAccess['coordination.read'],'read');
 const first=await f.action({sessionId:session,limit:50,textLimit:3});assert.equal(first.messages.length,50);assert.equal(first.messages[0].text,'😀😀😀');assert.equal(first.messages[0].textTruncated,true);assert.equal(first.messages[0].authorization,'unverified-native-history');
 const same=await f.action({sessionId:session,cursor:first.nextCursor,limit:2,textLimit:4000});assert.deepEqual(same.messages.map(x=>x.messageId),['m50','m51']);
 const third=await f.action({sessionId:session,cursor:same.nextCursor,limit:50});assert.deepEqual(third.messages.map(x=>x.messageId),['m52']);
 const last=await f.action({sessionId:session,cursor:third.nextCursor});assert.deepEqual(last.messages.map(x=>x.messageId),['last']);assert.equal(last.nextCursor,null);assert.equal(last.executionStarted,false);assert.equal(f.calls.filter(x=>x[0]==='inspect').length,1);assert.ok(f.calls.filter(x=>x[0]==='read').every(x=>x[2].limit===1));
});
test('reader and chat bound cursors reject reuse before another native read; changed history never returns a stale page',async t=>{
 const f=fixture(t),first=await f.action({sessionId:session,limit:1});const count=f.calls.length;
 for(const context of [{...authority,clientId:'other'},{...authority,session:'ahp-session:/other'},{...authority,origin:'ui'}])await assert.rejects(f.action({sessionId:session,cursor:first.nextCursor},context),/cursor/);
 await assert.rejects(f.action({sessionId:'ahp-session:/different',cursor:first.nextCursor}),/cursor/);assert.equal(f.calls.length,count);
 f.setRevision('two');await assert.rejects(f.action({sessionId:session,cursor:first.nextCursor}),/revision changed/);assert.equal((await f.action({sessionId:session})).revision,'two');
});
test('optional capability stays unadvertised; malformed input and unavailable sources never become empty success',async t=>{
 const absent=fixture(t,{enabled:false});assert.equal(absent.owner.manifest.actions['coordination.read'],undefined);await assert.rejects(absent.action({sessionId:session}),/Unadvertised/);
 const f=fixture(t);for(const args of [{sessionId:session,limit:0},{sessionId:session,textLimit:4001},{sessionId:session,origin:'ui'},{sessionId:session+'?other'},{sessionId:session,cursor:4}])await assert.rejects(f.action(args));await assert.rejects(f.action({sessionId:session},{clientId:'x',origin:'agent'}),/Authenticated/);assert.equal(f.calls.length,0);
 const wrong=fixture(t,{inspect:async()=>({id:'ahp-session:/wrong',revision:'one'})});await assert.rejects(wrong.action({sessionId:session}),/Exact/);
 const unavailable=fixture(t,{read:async()=>{throw Error('Native source is unavailable')}});await assert.rejects(unavailable.action({sessionId:session}),/unavailable/);
 const bad=fixture(t,{read:async()=>({revision:'one',rows:[{id:'bad',role:'system',text:'internal'}]})});await assert.rejects(bad.action({sessionId:session}),/bounded contract/);
});
test('history reads stay capacity bounded and shutdown invalidates pending results and cursors',async t=>{
 let release;const gate=new Promise(r=>release=r);const f=fixture(t,{read:async()=>{await gate;return {revision:'one',rows:[]}}});
 const pending=Array.from({length:4},()=>f.action({sessionId:session}));await assert.rejects(f.action({sessionId:session}),/capacity/);const rejected=Promise.all(pending.map(p=>assert.rejects(p,/closed during read/)));await f.owner.close();release();await rejected;await assert.rejects(f.action({sessionId:session}),/closed/);
});
