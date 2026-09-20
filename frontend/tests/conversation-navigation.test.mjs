import test from 'node:test';
import assert from 'node:assert/strict';
import {createConversationNavigation} from '../src/conversation-navigation.js';

function state(id,revision=1){return {revision,client:{hostInstanceId:'host'},selectedSessionId:id,selectedWorkspaceId:'space',
 sessions:['a','b','c'].map(key=>({id:key,workspaceId:'space',title:key,draft:key+' draft',messages:key===id?[{id:key+'-message',text:key+' history'}]:[]})),
 view:{draft:id+' draft'},canvas:{id:id+'-canvas',open:true},canvasWorkspace:{views:[]},canvasArtifacts:[]}}

test('cached history and draft display immediately without changing server state',()=>{
 const nav=createConversationNavigation(),a=state('a'),b=state('b',2);
 nav.remember(a);nav.remember(b);const token=nav.begin(b,'a'),shown=nav.apply(b);
 assert.equal(shown.selectedSessionId,'a');assert.equal(b.selectedSessionId,'b');
 assert.equal(shown.sessions.find(row=>row.id==='a').messages[0].text,'a history');
 assert.equal(shown.view.draft,'a draft');assert.equal(shown.canvas.id,'a-canvas');
 nav.settle(token);assert.equal(nav.apply(b),b);
});

test('late responses and a failed older selection cannot replace newer intent',()=>{
 const nav=createConversationNavigation(),a=state('a');nav.remember(a);
 const first=nav.begin(a,'b');const second=nav.begin(nav.apply(a),'c');
 nav.settle(first);nav.remember(state('b',2));
 assert.equal(nav.apply(state('b',2)).selectedSessionId,'c');
 nav.settle(second);assert.equal(nav.apply(state('b',2)).selectedSessionId,'b');
});

test('uncached chat shows its own loading state and does not leak the previous canvas',()=>{
 const nav=createConversationNavigation(),a=state('a');nav.remember(a);nav.begin(a,'b');
 const shown=nav.apply(a),b=shown.sessions.find(row=>row.id==='b');
 assert.equal(b.historyLoading,true);assert.deepEqual(b.messages,[]);assert.equal(shown.canvas.open,false);
 assert.equal(shown.view.draft,'b draft');
});

test('dirty renderers stay mounted until authoritative navigation completes',()=>{
 const nav=createConversationNavigation(),a=state('a');a.canvasWorkspace.views=[{dirty:true}];
 assert.equal(nav.begin(a,'b'),null);assert.equal(nav.apply(a),a);
});

test('host restart clears cached details and pending choices',()=>{
 const nav=createConversationNavigation(),a=state('a');nav.remember(a);nav.begin(a,'b');
 const restarted={...state('c'),client:{hostInstanceId:'new-host'}};nav.remember(restarted);
 assert.equal(nav.apply(restarted),restarted);nav.begin(restarted,'a');
 assert.equal(nav.apply(restarted).sessions.find(row=>row.id==='a').historyLoading,true);
});

test('display cache is bounded independently of the saved library',()=>{
 const nav=createConversationNavigation(1);nav.remember(state('a'));nav.remember(state('b'));
 nav.begin(state('b'),'a');assert.equal(nav.apply(state('b')).sessions.find(row=>row.id==='a').historyLoading,true);
});
